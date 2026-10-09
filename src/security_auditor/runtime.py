from __future__ import annotations

import asyncio
import json
import time

from .agents import AdkExecutor
from .config import Settings
from .domain import ALLOWED_KINDS, compliance_candidate
from .ingestion import ingest_run
from .mantis_engine import MantisEngine
from .native_progress import reconcile_native_progress
from .skills import SkillLoader, compile_instruction, digest
from .store import BudgetExceeded, Store
from .provider import ProviderUnavailable, ProviderBlocked, OutputContractError


class Scheduler:
    """Durable stage boundaries, bounded child tasks, and a programmatic supervisor."""

    def __init__(self, settings: Settings, store: Store, executor=None, mantis_executor=None):
        self.settings, self.store = settings, store
        self.loader = SkillLoader(settings.skills_dir)
        self.spec = json.loads(settings.workflow.read_text())
        self.loader.catalog()
        self.executor = executor or AdkExecutor(settings, store)
        self.mantis = mantis_executor or MantisEngine(settings, store)
        self.semaphore = asyncio.Semaphore(settings.concurrency)
        self.active: set[str] = set()
        self.stop = asyncio.Event()

    async def serve(self):
        self.store.recover()
        jobs = set()
        try:
            while not self.stop.is_set():
                with self.store.connect() as db:
                    db.execute("UPDATE runs SET status='PENDING' WHERE status='WAITING' AND pause_requested=0 "
                               "AND id IN (SELECT run_id FROM run_recovery WHERE wake_at<=?)", (time.time(),))
                for row in self.store.rows("SELECT id FROM runs WHERE status='PENDING' ORDER BY created"):
                    if row["id"] not in self.active:
                        self.active.add(row["id"])
                        job = asyncio.create_task(self.execute_run(row["id"]))
                        jobs.add(job)
                        job.add_done_callback(jobs.discard)
                await asyncio.sleep(0.2)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    def scopes(self, run_id: str, role: str) -> list[dict]:
        records = self.store.records(run_id)
        if role in {"pci_mapper", "pci_requirement_generator"}:
            if role == 'pci_mapper' and (self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id'):
                policy = ('precomputed_controls_v3' if (self.store.run(run_id)['snapshot'].get('standard') or {}).get('prepared')
                          else 'code_related_v2')
                return [{'requirement_id': r['id'], 'compliance_scope_policy': policy} for r in records
                        if r['kind']=='requirement' and r['origin']!='PCI_DSS']
            evidence = self.store.evidence(run_id, "standard")
            applicable = {r['clause_id'] for r in records if r['kind'] == 'applicability' and compliance_candidate(r)}
            contexts = {json.loads(e["metadata"]).get("context_id"): e["id"] for e in evidence}
            scopes, batch, size = [], [], 0
            atomic = role=='pci_requirement_generator' and bool((self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id'))
            covered, existing = set(), {}
            if atomic:
                for task in self.store.rows("SELECT scope,status FROM tasks WHERE run_id=? AND stage='pci_requirements' AND wave='analysis' ORDER BY created,id", (run_id,)):
                    saved = json.loads(task['scope'])
                    if task['status']=='SUCCEEDED':
                        scopes.append(saved)
                        covered.update(saved['clause_ids'])
                    elif task['status']!='SKIPPED' and len(saved.get('clause_ids', []))==1:
                        existing[saved['clause_ids'][0]] = saved
            def append_batch():
                context_ids = list(dict.fromkeys(
                    contexts[cid] for entry in batch
                    for cid in json.loads(entry["metadata"]).get("context_ids", []) if cid in contexts))
                generated = {"clause_ids": [json.loads(e["metadata"])["clause_id"] for e in batch],
                             "evidence_ids": [e["id"] for e in batch], "context_evidence_ids": context_ids}
                if role == 'pci_mapper':
                    generated['compliance_scope_policy'] = 'code_related_v2'
                scopes.append(existing.get(generated['clause_ids'][0], generated) if atomic else generated)
            for entry in evidence:
                if not json.loads(entry["metadata"]).get("clause_id"):
                    continue
                if role == 'pci_requirement_generator' and json.loads(entry['metadata'])['clause_id'] not in applicable:
                    continue
                if json.loads(entry['metadata'])['clause_id'] in covered:
                    continue
                length = len(self.store.read_evidence(run_id, entry["id"])["content"].encode())
                if batch and (len(batch) >= (1 if atomic else 8) or size + length > 20000):
                    append_batch()
                    batch, size = [], 0
                batch.append(entry)
                size += length
            if batch:
                append_batch()
            return scopes
        if role == "requirement_checker":
            return [
                {
                    "requirement_id": r["id"],
                    "module": r["module"],
                    "acceptance_criteria": r["acceptance_criteria"],
                }
                for r in records
                if r["kind"] == "requirement"
            ]
        if role == "requirement_reviewer":
            return [{"subject_ids": [r["id"]]} for r in records if r["kind"] == "requirement"]
        if role in {"finding_reviewer", "finding_critic", "risk_calibrator"}:
            return [{"subject_ids": [r["id"]]} for r in records if r["kind"] == "finding"]
        if role == "design_analyst":
            evidence = self.store.evidence(run_id, "document")
            # Parsing blocks are citation units, not mandatory task slices.
            scopes, batch, size = [], [], 0
            for e in evidence:
                length = len(self.store.read_evidence(run_id, e["id"])["content"].encode())
                if batch and size + length > 90000:
                    scopes.append({"evidence_ids": batch, "scope": "project_document",
                                   "split_reason": "context_budget"})
                    batch, size = [], 0
                batch.append(e["id"])
                size += length
            if batch:
                scopes.append({"evidence_ids": batch, "scope": "project_document"})
            return scopes
        return [{"scope": "system_and_cross_module_relationships"}]

    def upstream_context(self, run_id: str, role: str, scope: dict) -> list[dict]:
        records = self.store.records(run_id)
        if role == 'design_analyst':
            records = []  # Concurrent document tasks must not depend on completion order.
        elif role == 'requirement_generator':
            records = [r for r in records if r['kind'] == 'fact']
        elif role == 'requirement_checker':
            # Each check is independent. Other requirements, previous verdicts and
            # whole native models can exceed the input bound and bias this task.
            # Original design/code and the shared Mantis model remain available as tools.
            records = [r for r in records if r['kind'] == 'requirement' and r['id'] == scope['requirement_id']]
            targets = {cid for r in records for cid in r['clause_ids']}
            records += [r for r in self.store.records(run_id) if r['kind']=='applicability'
                        and (scope['requirement_id'] in r.get('requirement_ids', []) or r['clause_id'] in targets)]
        elif role == 'pci_mapper':
            # Prior applicability results are not inputs to an independent clause decision.
            records = [r for r in records if r['kind'] == 'fact' or
                       (r['kind']=='requirement' and r['id']==scope.get('requirement_id'))]
        elif role == 'pci_requirement_generator':
            targets = set(scope['clause_ids'])
            records = [r for r in records if r['kind'] == 'fact' or
                       (r['kind'] == 'applicability' and r['clause_id'] in targets) or
                       (r['kind'] == 'requirement' and r['origin'] != 'PCI_DSS')]
            # Existing requirements are duplicate-detection leads, not inputs
            # to rewrite. Avoid repeating their criteria and long rationales in
            # every compliance batch; original document facts remain intact.
            records = [{key: r[key] for key in
                       ('id', 'kind', 'title', 'module', 'origin', 'clause_ids')}
                       if r['kind'] == 'requirement' else
                       {key: r[key] for key in ('id','kind','title','module','fact_type','basis','evidence_ids')}
                       if r['kind']=='fact' else r for r in records]
        elif role == 'requirement_reviewer':
            targets = set(scope['subject_ids'])
            records = [r if r['id'] in targets else {key: r[key] for key in
                       ('id', 'kind', 'title', 'module', 'origin', 'clause_ids')}
                       for r in records if r['kind'] == 'requirement']
        if role in {'pci_requirement_generator', 'requirement_checker'}:
            # Association inventories can contain many repetitions of a long
            # rationale. Keep IDs and all declared conditions in the initial
            # input; exact full upstream outputs remain available through a tool.
            fields = ('id', 'kind', 'clause_id', 'status', 'relevance', 'control_scope', 'control_ids', 'requirement_ids',
                      'applicability_conditions', 'missing_facts', 'evidence_ids')
            if role == 'pci_requirement_generator':
                fields = tuple(key for key in fields if key != 'missing_facts')
            records = [{key: r[key] for key in fields if key in r}
                       if r['kind']=='applicability' else r for r in records]
        return [{k: v for k, v in record.items() if k != 'native_data'} for record in records]

    def retire_replaced_clause_batches(self, run_id: str, parent_id: str, scopes: list[dict]):
        """Preserve old failures while refining only uncommitted control batches."""
        if not (self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id'):
            return
        planned = {json.dumps(scope, sort_keys=True) for scope in scopes}
        tasks = self.store.rows('SELECT * FROM tasks WHERE parent_task_id=?', (parent_id,))
        for task in tasks:
            scope = json.loads(task['scope'])
            if task['status'] in {'SUCCEEDED','SKIPPED'} or json.dumps(scope,sort_keys=True) in planned:
                continue
            target = set(scope.get('clause_ids', []))
            replacements = [t for t in tasks if json.dumps(json.loads(t['scope']),sort_keys=True) in planned and
                            set(json.loads(t['scope']).get('clause_ids', [])) <= target]
            replacement_clauses = {cid for t in replacements for cid in json.loads(t['scope']).get('clause_ids', [])}
            if not target or replacement_clauses != target or self.store.rows('SELECT id FROM records WHERE task_id=?',(task['id'],)):
                raise ValueError('条款任务细化必须保留完整库存且不能替换已提交结果')
            receipt = {'superseded': True, 'replaced_by': [t['id'] for t in replacements],
                       'original_error': task['error'], 'gaps': ['未提交的合规批次已细化为逐条款任务，原始返回和用量保留']}
            self.store.finish_service(task['id'],receipt,status='SKIPPED')
            self.store.event(run_id,'inventory_refined',{'task_id':task['id'],**receipt})

    async def child(self, run_id: str, node: dict, parent_id: str, scope: dict, wave="analysis"):
        tid = self.store.add_task(run_id, node["id"], scope, parent_id, wave)
        task = self.store.task(tid)
        if task["status"] == "SUCCEEDED":
            return
        bundle = self.loader.resolve(node["role"], node["skill_id"], node["skill_version"])
        _, instruction_hash = compile_instruction(bundle, language=self.store.run(run_id)["language"])
        async with self.semaphore:
            if self.store.run(run_id)["pause_requested"]:
                raise BudgetExceeded("用户请求暂停，将从已提交任务恢复")
            self.store.start_task(tid, bundle, instruction_hash)
            task = self.store.task(tid)
            context = {
                "scope": scope,
                "wave": wave,
                "allowed_kinds": sorted(ALLOWED_KINDS[node["role"]]),
                "upstream_records": self.upstream_context(run_id, node['role'], scope),
                "record_id_prefix": tid + '_',
                "snapshot_id": digest(
                    json.dumps(self.store.run(run_id)["snapshot"], sort_keys=True).encode()
                ),
                "required_output": "StageOutput",
                "output_language": self.store.run(run_id)["language"],
                "static_only": True,
                "mantis_workspace": str(self.mantis.workspace(run_id)),
            }
            if node['role'] == 'requirement_checker':
                evidence = self.store.evidence(run_id)
                context['source_inventory'] = {
                    kind: sum(e['source_type'] == kind for e in evidence)
                    for kind in ('document', 'standard', 'code')}
                context['repository_id'] = (self.store.run(run_id)['snapshot'].get('repository') or {}).get('id')
            if node['role'] == 'pci_mapper':
                code_sources = self.store.evidence(run_id, 'code')
                paths = sorted({json.loads(e['metadata']).get('path', e['locator'].rsplit(':', 1)[0])
                                for e in code_sources})
                context['code_scope'] = {
                    'repository_id': (self.store.run(run_id)['snapshot'].get('repository') or {}).get('id'),
                    'file_count': len(paths), 'sample_paths': paths[:64], 'paths_truncated': len(paths) > 64,
                    'design_modules': sorted({r['module'] for r in context['upstream_records'] if r.get('module')}),
                    'policy': 'Classify the assessed subsystem, not the whole organization. '
                              'No repository or missing sample path is not proof of irrelevance; read sources when needed.'}
            try:
                requirement = next((r for r in context['upstream_records'] if r['kind'] == 'requirement'
                                    and r['id'] == scope.get('requirement_id')), {})
                if node['role'] == 'requirement_checker' and requirement.get('standard_control_id') and requirement.get('verification_method') == 'DEPLOYMENT':
                    # This is an explicit prepared verification policy, not a
                    # second inference. Preserve every requirement and criterion.
                    from .domain import Assessment, StageOutput
                    from .localization import tr
                    refs = requirement['evidence_ids']
                    for eid in refs:
                        self.store.note_read(tid, eid)
                    output = StageOutput(records=[Assessment(
                        id=tid + f'_external_{i}', title=requirement['title'],
                        requirement_id=requirement['id'], acceptance_criterion=criterion,
                        module=requirement['module'], entrypoint='', design_status='UNKNOWN',
                        implementation_status='NOT_CODE_VERIFIABLE', evidence_ids=refs,
                        rationale=tr('该安全要求依赖实际部署或运营状态，无法通过代码验证。', self.store.run(run_id)['language']))
                        for i, criterion in enumerate(requirement['acceptance_criteria'])],
                        summary=tr('该安全要求依赖实际部署或运营状态，无法通过代码验证。', self.store.run(run_id)['language']))
                else:
                    output = await self.executor.execute(task, bundle, context)
                self.store.save_model_output(tid, output.model_dump_json())
                read_ids = {
                    row["evidence_id"]
                    for row in self.store.rows(
                        "SELECT evidence_id FROM task_reads WHERE task_id=?", (tid,)
                    )
                }
                unread = set(scope.get("evidence_ids", [])) - read_ids
                if unread:
                    raise OutputContractError(f"本任务有 {len(unread)} 个目标材料块未读取，阶段未完成")
                if wave == "triage":
                    # First-wave findings remain leads, not domain conclusions.
                    self.store.finish_service(tid, output.model_dump())
                else:
                    self.store.commit_output(tid, node["role"], output)
            except BudgetExceeded:
                self.store.fail_task(tid, "累计预算不足或用户暂停", "PENDING")
                raise
            except (ProviderUnavailable, ProviderBlocked):
                self.store.fail_task(tid, "等待模型服务恢复", "PENDING")
                raise
            except OutputContractError as exc:
                self.store.fail_task(tid, str(exc))
                raise
            except ValueError:
                reason = '模型产物未通过结构、来源引用或需求对应校验，阶段未完成'
                self.store.fail_task(tid, reason)
                raise OutputContractError(reason) from None
            except Exception:
                self.store.fail_task(tid, "阶段输出、证据或模型调用未通过校验")
                raise

    async def execute_run(self, run_id: str):
        try:
            if self.store.run(run_id)['pause_requested']:
                self.store.set_run_status(run_id, 'PAUSED', '已按用户要求暂停', issue_code='user_paused')
                return
            self.store.set_run_status(run_id, "RUNNING")
            run = self.store.run(run_id)
            from .localization import policy_hashes
            if run["snapshot"].get("language_policy_hashes", policy_hashes()) != policy_hashes():
                raise ValueError("输出语言技能版本已变化，需要新运行")
            expected = run["snapshot"]["skill_hashes"]
            actual = {row["id"]: row["sha256"] for row in self.loader.catalog()}
            if expected != actual:
                raise ValueError("技能版本已变化，需要新运行")
            if run["snapshot"]["workflow_hash"] != digest(self.settings.workflow.read_bytes()):
                raise ValueError("流程版本已变化，需要新运行")
            if run['mode'] == 'implementation_only':
                from .baseline import import_baseline
                baseline_task = self.store.add_task(run_id, 'requirement_baseline',
                    {'source_run_id': run['snapshot']['baseline']['source_run_id']}, wave='service')
                if self.store.task(baseline_task)['status'] != 'SUCCEEDED':
                    self.store.start_task(baseline_task)
                    try:
                        import_baseline(self.store, run_id, baseline_task)
                    except Exception:
                        self.store.fail_task(baseline_task, '需求基线导入未通过校验')
                        raise
            ingest = self.store.add_task(run_id, "ingestion", {"snapshot": "all"}, wave="service")
            if self.store.task(ingest)["status"] != "SUCCEEDED":
                self.store.start_task(ingest)
                try:
                    gaps = await asyncio.to_thread(ingest_run, self.store, run_id, self.settings)
                    self.store.finish_service(ingest, {"gaps": gaps})
                except Exception:
                    self.store.fail_task(ingest, "输入快照或材料提取未通过校验")
                    raise
            mode = self.spec["modes"][run["mode"]]
            selected = mode["include_nodes"] if isinstance(mode["include_nodes"], list) else None
            for node in self.spec["nodes"]:
                if node.get('handler') == 'bind_standard_controls' and (not selected or node['id'] in selected):
                    tid = self.store.add_task(run_id, node['id'], {'stage': node['id']}, wave='service')
                    if self.store.task(tid)['status'] in {'SUCCEEDED', 'SKIPPED'}:
                        continue
                    self.store.start_task(tid)
                    try:
                        from .control_catalog import bind_controls
                        bind_controls(self.store, run_id, tid)
                    except Exception:
                        self.store.fail_task(tid, '预生成控制需求绑定未通过校验')
                        raise
                    continue
                if node["kind"] not in {"agent", "engine"} or (selected and node["id"] not in selected):
                    continue
                parent = self.store.add_task(run_id, node["id"], {"stage": node["id"]}, wave="stage")
                if self.store.task(parent)["status"] in {"SUCCEEDED", "SKIPPED"}:
                    continue
                self.store.start_task(parent)
                if node["kind"] == "engine":
                    try:
                        async with self.semaphore:
                            await self.mantis.execute(run_id, node["phase"], parent)
                        if isinstance(self.mantis, MantisEngine):
                            reconcile_native_progress(self.store, run_id, self.mantis.workspace(run_id), node["phase"])
                        self.store.finish_service(parent, {"engine": "mantis", "gaps": []})
                    except BaseException:
                        self.store.fail_task(parent, "Mantis 阶段尚未完成", "PENDING")
                        raise
                    continue
                scopes = self.scopes(run_id, node["role"])
                if not scopes:
                    reason = (
                        "没有正式标准包，跳过 PCI DSS 映射"
                        if node["role"] == "pci_mapper"
                        else "没有相关或可能相关的条款，未生成 PCI DSS 需求；匹配结果继续保留"
                        if node['role'] == 'pci_requirement_generator'
                        else f"阶段 {node['id']} 没有可处理对象，未执行专项分析"
                    )
                    self.store.finish_service(parent, {"gaps": [reason]}, status="SKIPPED")
                    continue
                # Persist the full inventory before dispatch; stop queued requests on failure.
                for scope in scopes:
                    self.store.add_task(run_id, node['id'], scope, parent)
                if node['role']=='pci_requirement_generator':
                    self.retire_replaced_clause_batches(run_id,parent,scopes)
                for start in range(0, len(scopes), self.settings.concurrency):
                    results = await asyncio.gather(
                        *(self.child(run_id, node, parent, scope)
                          for scope in scopes[start:start + self.settings.concurrency]), return_exceptions=True)
                    errors = [result for result in results if isinstance(result, BaseException)]
                    if errors:
                        resumable = isinstance(errors[0], (BudgetExceeded, ProviderUnavailable, ProviderBlocked))
                        self.store.fail_task(parent, '阶段子任务尚未完成', 'PENDING' if resumable else 'FAILED')
                        raise errors[0]
                child_tasks = self.store.rows("SELECT id,status FROM tasks WHERE parent_task_id=?", (parent,))
                self.store.finish_service(parent, {"children": len(child_tasks), "gaps": []})
            self.store.set_run_status(run_id, "COMPLETED")
        except ProviderUnavailable as exc:
            with self.store.connect() as db:
                db.execute("INSERT INTO run_recovery(run_id,attempts,wake_at) VALUES(?,1,0) "
                           "ON CONFLICT(run_id) DO UPDATE SET attempts=attempts+1", (run_id,))
                attempts = db.execute("SELECT attempts FROM run_recovery WHERE run_id=?", (run_id,)).fetchone()[0]
                db.execute("UPDATE run_recovery SET wake_at=? WHERE run_id=?",
                           (time.time() + min(3600, 30 * 2 ** min(attempts - 1, 7)), run_id))
            status = "WAITING" if attempts <= 5 else "PAUSED"
            self.store.set_run_status(run_id, status, str(exc) if status == "WAITING" else "模型服务持续不可用，已保存进度，可稍后继续",
                                      issue_code=exc.code if status == 'WAITING' else 'recovery_exhausted')
        except ProviderBlocked as exc:
            self.store.set_run_status(run_id, "PAUSED", str(exc), issue_code=exc.code)
        except OutputContractError as exc:
            self.store.set_run_status(run_id, "FAILED", str(exc), issue_code=exc.code)
        except BudgetExceeded:
            paused = self.store.run(run_id)['pause_requested']
            self.store.set_run_status(run_id, "PAUSED", '已按用户要求暂停' if paused else '累计模型预算已用尽，进度已保存',
                                      issue_code='user_paused' if paused else 'budget')
        except asyncio.CancelledError:
            paused = self.store.run(run_id)["pause_requested"]
            self.store.set_run_status(run_id, "PAUSED" if paused else "PENDING",
                                      "已按用户要求暂停" if paused else "服务重启后自动继续，已保存阶段结果",
                                      issue_code='user_paused' if paused else None)
            raise
        except Exception as exc:
            safe = (
                str(exc)
                if isinstance(exc, ValueError) and "版本" in str(exc)
                else "部分阶段未完成，请检查材料、模型配置和任务详情"
            )
            self.store.set_run_status(run_id, "FAILED", safe, issue_code='version' if '版本' in safe else 'analysis_incomplete')
        finally:
            self.active.discard(run_id)


def report(store: Store, run_id: str) -> dict:
    run = store.run(run_id)
    tasks = store.rows("SELECT * FROM tasks WHERE run_id=? ORDER BY created,id", (run_id,))
    records = store.records(run_id)
    gaps = []
    for task in tasks:
        if task["result"]:
            gaps.extend(json.loads(task["result"]).get("gaps", []))
    inventory = [task for task in tasks if task["parent_task_id"]]
    return {
        "schema_version": "0.1.0",
        "run": run,
        "records": records,
        "model_artifacts": store.model_artifacts(run_id),
        "requirement_matrix": result_matrix(store, run_id)["requirements"],
        "tasks": tasks,
        "evidence_gaps": sorted(set(gaps)),
        "incomplete_tasks": [t["id"] for t in tasks if t["status"] not in {"SUCCEEDED", "SKIPPED"}],
        "skipped_tasks": [t["id"] for t in tasks if t["status"] == "SKIPPED"],
        "coverage": {
            "planned_tasks": len(inventory),
            "completed_tasks": sum(t["status"] == "SUCCEEDED" for t in inventory),
        },
        "assurance": "STATIC_EVIDENCE_ONLY",
        "compliance": "NOT_ASSESSED" if not run["snapshot"].get("standard") else "REQUIREMENT_GAP_ANALYSIS",
    }


def result_matrix(store: Store, run_id: str) -> dict:
    """The server owns safety aggregation; the browser only displays these states."""
    from .localization import tr
    selected = store.run(run_id)["language"]
    records = store.records(run_id)
    tasks = store.rows("SELECT scope,status FROM tasks WHERE run_id=? AND stage='requirement_check' "
                       "AND parent_task_id IS NOT NULL", (run_id,))
    check_tasks = {json.loads(t["scope"]).get("requirement_id"): t["status"] for t in tasks}
    requirements = []
    findings = []
    for record in records:
        if record["kind"] == "requirement":
            reviews = [r for r in records if r['kind'] == 'review' and r['subject_id'] == record['id']]
            verdicts = {r['verdict'] for r in reviews}
            review_status = next((s for s in ('REJECTED', 'NEEDS_EVIDENCE', 'SUPPORTED') if s in verdicts), 'NOT_REVIEWED')
            checks = [{**r, 'implementation_status': 'NOT_CODE_VERIFIABLE'
                       if r['implementation_status'] == 'EXTERNAL_EVIDENCE_REQUIRED' else r['implementation_status']}
                      for r in records if r["kind"] == "assessment" and r["requirement_id"] == record["id"]]
            criteria = [
                next((c for c in checks if c["acceptance_criterion"] == criterion),
                     {"acceptance_criterion": criterion, "implementation_status": "NOT_CHECKED",
                      "evidence_ids": [], "rationale": tr("尚未检查", selected)})
                for criterion in record["acceptance_criteria"]
            ]
            checked = sum(c["implementation_status"] != "NOT_CHECKED" for c in criteria)
            states = {c["implementation_status"] for c in criteria}
            task_state = check_tasks.get(record["id"])
            # A complete static conclusion requires every acceptance criterion.
            if "VIOLATED" in states:
                status = "VIOLATED"
            elif checked != len(criteria):
                status = ("CHECKING" if task_state == "RUNNING" else "INCOMPLETE"
                          if checked or task_state == "FAILED" else "NOT_CHECKED")
            else:
                status = next((s for s in ("UNKNOWN", "PARTIAL", "NOT_CODE_VERIFIABLE")
                               if s in states), "STATIC_SUPPORTED")
            number = f"SR-{len(requirements) + 1:03d}"
            linked = [r["id"] for r in records if r["kind"] == "finding"
                      and record["id"] in r.get("requirement_ids", [])]
            requirements.append({**record, "requirement_number": number, "checks": checks,
                                 "compliance_matches": [r for r in records if r['kind']=='applicability' and compliance_candidate(r)
                                                        and (record['id'] in r.get('requirement_ids', [])
                                                             or r['clause_id'] in record['clause_ids'])],
                                 "requirement_review_status": review_status, "requirement_reviews": reviews,
                                 "criterion_checks": criteria, "implementation_status": status,
                                 "checked_criteria": checked, "total_criteria": len(criteria),
                                 "finding_ids": linked})
        if record["kind"] == "finding":
            reviews = [r for r in records if r["kind"] == "review" and r["subject_id"] == record["id"]]
            status = "NEEDS_EVIDENCE"
            if record.get("engine") == "mantis":
                status = record["status"]
            if any(review["verdict"] == "REJECTED" for review in reviews):
                status = "FALSE_POSITIVE"
            elif len(reviews) >= 2 and all(review["verdict"] == "SUPPORTED" for review in reviews):
                status = "STATIC_SUPPORTED"
            risk = next(
                (r for r in reversed(records) if r["kind"] == "risk" and r["subject_id"] == record["id"]),
                None,
            )
            findings.append(
                {**record, "review_status": status, "severity": risk["severity"] if risk else
                 (record.get("native_data") or {}).get("severity", "UNKNOWN")}
            )
    numbers = {r["id"]: r["requirement_number"] for r in requirements}
    for finding in findings:
        finding["requirement_numbers"] = [numbers[rid] for rid in finding.get("requirement_ids", [])
                                          if rid in numbers]
    return {"requirements": requirements, "findings": findings,
            "model_artifacts": store.model_artifacts(run_id)}
