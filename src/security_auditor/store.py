from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .domain import ALLOWED_KINDS, StageOutput, new_id
from .skills import digest
from .provider import BudgetExceeded, ProviderLedger
from .migrations import migrate, insert_artifact


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,name TEXT NOT NULL,
                sha256 TEXT NOT NULL,path TEXT NOT NULL,media_type TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,mode TEXT NOT NULL,
                status TEXT NOT NULL,snapshot TEXT NOT NULL,demo INTEGER NOT NULL DEFAULT 0,
                max_requests INTEGER NOT NULL,max_tokens INTEGER NOT NULL,pause_requested INTEGER DEFAULT 0,
                created TEXT NOT NULL,error TEXT, idempotency_key TEXT,
                UNIQUE(project_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,run_id TEXT NOT NULL,
                source_type TEXT NOT NULL,locator TEXT NOT NULL,content TEXT NOT NULL,sha256 TEXT NOT NULL,
                metadata TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED,content);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,parent_task_id TEXT,
                stage TEXT NOT NULL,wave TEXT NOT NULL,scope TEXT NOT NULL,status TEXT NOT NULL,
                skill_id TEXT,skill_version TEXT,skill_hash TEXT,instruction_hash TEXT,rules TEXT,
                result TEXT,error TEXT,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,task_id TEXT NOT NULL,
                kind TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,task_id TEXT NOT NULL,
                reserved_tokens INTEGER NOT NULL,charged_tokens INTEGER,status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS run_budget_policy(run_id TEXT PRIMARY KEY,
                unlimited INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS task_reads(task_id TEXT NOT NULL,evidence_id TEXT NOT NULL,
                PRIMARY KEY(task_id,evidence_id));
            CREATE TABLE IF NOT EXISTS model_outputs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,
                content TEXT NOT NULL,sha256 TEXT NOT NULL,created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id,status);
            CREATE INDEX IF NOT EXISTS idx_records_run ON records(run_id,kind);
            CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id,id);
            """)
        self.provider = ProviderLedger(path)
        migrate(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def project(self, project_id: str) -> dict:
        rows = self.rows("SELECT * FROM projects WHERE id=?", (project_id,))
        if not rows:
            raise KeyError("项目不存在")
        return rows[0]

    def create_project(self, name: str) -> dict:
        project = {"id": new_id(), "name": name, "created": now()}
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES(:id,:name,:created)", project)
        return project

    def add_asset(self, project_id: str, name: str, path: Path, media_type: str) -> dict:
        self.project(project_id)
        asset = {
            "id": new_id(),
            "project_id": project_id,
            "name": name,
            "path": str(path),
            "sha256": digest(path.read_bytes()),
            "media_type": media_type,
            "created": now(),
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO assets VALUES(:id,:project_id,:name,:sha256,:path,:media_type,:created)", asset
            )
        return asset

    def create_run(
        self,
        project_id: str,
        mode: str,
        snapshot: dict,
        demo: bool,
        max_requests: int,
        max_tokens: int,
        key: str | None = None,
        *, physical: bool = False,
    ) -> dict:
        if demo:
            raise ValueError("不再支持演示运行")
        self.project(project_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = (
                db.execute(
                    "SELECT * FROM runs WHERE project_id=? AND idempotency_key=?", (project_id, key)
                ).fetchone()
                if key
                else None
            )
            if existing:
                if existing["snapshot"] != json.dumps(snapshot, sort_keys=True) or existing["mode"] != mode:
                    raise ValueError("幂等键已用于另一组分析输入")
                return self.run(existing['id'])
            rid = new_id()
            db.execute(
                "INSERT INTO runs(id,project_id,mode,status,snapshot,demo,max_requests,max_tokens,created,"
                "idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    project_id,
                    mode,
                    "PENDING",
                    json.dumps(snapshot, sort_keys=True),
                    int(demo),
                    max_requests,
                    max_tokens,
                    now(),
                    key,
                ),
            )
            if physical:
                db.execute("INSERT INTO run_controls VALUES(?,?,?,1)",
                           (rid, max_requests or None, max_tokens or None))
        self.event(rid, "run_created", {"status": "PENDING"})
        return self.run(rid)

    def language(self, default: str = 'en') -> str:
        from .localization import language
        rows = self.rows("SELECT value FROM system_settings WHERE key='language'")
        return language(rows[0]['value'] if rows else default)

    def set_language(self, selected: str):
        from .localization import language
        with self.connect() as db:
            db.execute("INSERT INTO system_settings VALUES('language',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (language(selected),))

    def run(self, run_id: str) -> dict:
        rows = self.rows("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise KeyError("运行不存在")
        run = rows[0]
        run["snapshot"] = json.loads(run["snapshot"])
        run["demo"] = bool(run["demo"])
        run["language"] = run["snapshot"].get("language", "en")
        policy = self.rows("SELECT unlimited FROM run_budget_policy WHERE run_id=?", (run_id,))
        run["unlimited_budget"] = bool(policy and policy[0]["unlimited"])
        run["usage"] = self.rows(
            "SELECT count(*) requests,COALESCE(SUM(COALESCE(charged_tokens,"
            "reserved_tokens)),0) tokens FROM usage WHERE run_id=?",
            (run_id,),
        )[0]
        policy = self.provider.policy(run_id)
        run["budget"] = policy
        if policy:
            run["usage"] = self.provider.usage(run_id)
        issue = self.rows('SELECT code,message,updated FROM run_issues WHERE run_id=?', (run_id,))
        run['issue'] = issue[0] if issue else None
        recovery = self.rows('SELECT attempts,wake_at FROM run_recovery WHERE run_id=?', (run_id,))
        run['recovery'] = recovery[0] if recovery else None
        run['pause_requested'] = bool(run['pause_requested'])
        return run

    def set_run_status(self, run_id: str, status: str, error: str | None = None, *, issue_code: str | None = None):
        from .localization import tr
        if error:
            error = tr(error, self.run(run_id)["language"])
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,error=? WHERE id=?", (status, error, run_id))
            if status in {'PENDING', 'RUNNING', 'COMPLETED'}:
                db.execute('DELETE FROM run_issues WHERE run_id=?', (run_id,))
            elif error:
                db.execute('INSERT INTO run_issues VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                           'code=excluded.code,message=excluded.message,updated=excluded.updated',
                           (run_id, issue_code or 'analysis_incomplete', error, now()))
            if status == 'COMPLETED':
                db.execute('DELETE FROM run_recovery WHERE run_id=?', (run_id,))
        self.event(run_id, "run_status", {"status": status, "reason": error})

    def save_model_output(self, task_id: str, content: str):
        with self.connect() as db:
            output_id, created = new_id(), now()
            db.execute("INSERT INTO model_outputs VALUES(?,?,?,?,?)",
                       (output_id, task_id, content, digest(content.encode()), created))
        return output_id

    def pause_run(self, project_id: str, run_id: str):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT status FROM runs WHERE id=? AND project_id=?', (run_id, project_id)).fetchone()
            if not run:
                raise KeyError('资源不属于此项目')
            if run['status'] not in {'RUNNING', 'PENDING', 'WAITING'}:
                raise ValueError('此运行当前不能暂停')
            immediate = run['status'] in {'PENDING', 'WAITING'}
            message = '已按用户要求暂停' if immediate else '正在暂停，当前请求结束后停止'
            db.execute("UPDATE runs SET pause_requested=1,status=CASE WHEN status IN ('PENDING','WAITING') "
                       "THEN 'PAUSED' ELSE status END,error=? WHERE id=?", (message, run_id))
            db.execute('INSERT INTO run_issues VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                       'code=excluded.code,message=excluded.message,updated=excluded.updated',
                       (run_id, 'user_paused', message, now()))
            db.execute('INSERT INTO events(run_id,event_type,payload,created) VALUES(?,?,?,?)',
                       (run_id, 'pause_requested', '{}', now()))
        return self.run(run_id)

    def update_budget(self, project_id: str, run_id: str, requests: int | None, tokens: int | None):
        if any(v is not None and v < 1 for v in (requests, tokens)):
            raise ValueError('预算必须为正数或明确不限额')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT * FROM runs WHERE id=? AND project_id=?', (run_id, project_id)).fetchone()
            if not run:
                raise KeyError('资源不属于此项目')
            if run['status'] not in {'PAUSED', 'FAILED', 'PENDING'} or not db.execute(
                    'SELECT run_id FROM run_controls WHERE run_id=?', (run_id,)).fetchone():
                raise ValueError('请先暂停运行；历史运行保留原记账方式')
            db.execute('UPDATE run_controls SET max_requests=?,max_tokens=? WHERE run_id=?', (requests, tokens, run_id))
            db.execute('UPDATE runs SET max_requests=?,max_tokens=? WHERE id=?', (requests or 0, tokens or 0, run_id))
            db.execute('INSERT INTO events(run_id,event_type,payload,created) VALUES(?,?,?,?)',
                       (run_id, 'budget_updated', json.dumps({'max_requests': requests, 'max_tokens': tokens}), now()))
        return self.run(run_id)

    def resume_run(self, project_id: str, run_id: str, expected: dict):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT * FROM runs WHERE id=? AND project_id=?', (run_id, project_id)).fetchone()
            if not run:
                raise KeyError('资源不属于此项目')
            if run['status'] not in {'PAUSED', 'FAILED'}:
                raise ValueError('此运行当前不能恢复')
            snapshot = json.loads(run['snapshot'])
            if any(key in snapshot and snapshot[key] != value for key, value in expected.items()
                   if key != 'mantis_hash' or snapshot.get('repository')):
                raise ValueError('分析流程或技能版本已更新，请开始新分析')
            policy = db.execute('SELECT * FROM run_controls WHERE run_id=?', (run_id,)).fetchone()
            if policy:
                used = db.execute('SELECT count(*),COALESCE(SUM(COALESCE(actual_tokens,CASE WHEN '
                                  "status IN ('UNKNOWN','IN_FLIGHT') THEN reserved_tokens ELSE 0 END)),0) "
                                  'FROM provider_requests WHERE run_id=?', (run_id,)).fetchone()
                exhausted = ((policy['max_requests'] is not None and used[0] >= policy['max_requests']) or
                             (policy['max_tokens'] is not None and used[1] >= policy['max_tokens']))
            else:
                unlimited = db.execute('SELECT unlimited FROM run_budget_policy WHERE run_id=?', (run_id,)).fetchone()
                used = db.execute('SELECT count(*),COALESCE(SUM(COALESCE(charged_tokens,reserved_tokens)),0) '
                                  'FROM usage WHERE run_id=?', (run_id,)).fetchone()
                exhausted = not (unlimited and unlimited[0]) and (used[0] >= run['max_requests'] or used[1] >= run['max_tokens'])
            if exhausted:
                raise ValueError('累计预算已用尽；可修改预算后继续，已有用量保留')
            db.execute("UPDATE runs SET status='PENDING',pause_requested=0,error=NULL WHERE id=?", (run_id,))
            db.execute('DELETE FROM run_recovery WHERE run_id=?', (run_id,))
            db.execute('DELETE FROM run_issues WHERE run_id=?', (run_id,))
            db.execute('INSERT INTO events(run_id,event_type,payload,created) VALUES(?,?,?,?)',
                       (run_id, 'run_resumed', '{}', now()))
        return self.run(run_id)

    def save_model_submission(self, task_id: str, stage: str, tool: str, campaign: str, parameters: dict):
        task = self.task(task_id)
        content = json.dumps({'stage': stage, 'tool': tool, 'campaign': campaign, 'parameters': parameters}, ensure_ascii=False)
        from .migrations import submission
        if not submission(content, task['stage']):
            raise ValueError('阶段产物与提交角色不一致')
        output_id, created = new_id(), now()
        with self.connect() as db:
            db.execute('INSERT INTO model_outputs VALUES(?,?,?,?,?)',
                       (output_id, task_id, content, digest(content.encode()), created))
            insert_artifact(db, output_id, task_id, task['run_id'], task['stage'], content, created)
        return output_id

    def event(self, run_id: str, event_type: str, payload: dict):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run_id,event_type,payload,created) VALUES(?,?,?,?)",
                (run_id, event_type, json.dumps(payload, ensure_ascii=False), now()),
            )

    def add_evidence(self, run_id: str, source_type: str, locator: str, content: str, metadata: dict) -> str:
        run = self.run(run_id)
        eid = digest(f"{run_id}:{source_type}:{locator}:{content}".encode())[:32]
        with self.connect() as db:
            exists = db.execute("SELECT id FROM evidence WHERE id=?", (eid,)).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)",
                    (
                        eid,
                        run["project_id"],
                        run_id,
                        source_type,
                        locator,
                        content,
                        digest(content.encode()),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                db.execute("INSERT INTO evidence_fts VALUES(?,?)", (eid, content))
        return eid

    def read_evidence(self, run_id: str, evidence_id: str) -> dict:
        rows = self.rows("SELECT * FROM evidence WHERE id=? AND run_id=?", (evidence_id, run_id))
        if not rows:
            raise ValueError("证据不属于本次快照")
        evidence = rows[0]
        if digest(evidence["content"].encode()) != evidence["sha256"]:
            raise ValueError("证据内容摘要不匹配")
        evidence["metadata"] = json.loads(evidence["metadata"])
        return evidence

    def note_read(self, task_id: str, evidence_id: str):
        task = self.task(task_id)
        self.read_evidence(task["run_id"], evidence_id)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO task_reads VALUES(?,?)", (task_id, evidence_id))

    def evidence(self, run_id: str, source_type: str | None = None) -> list[dict]:
        sql = "SELECT id,source_type,locator,sha256,metadata FROM evidence WHERE run_id=?"
        args = (run_id,)
        if source_type:
            sql += " AND source_type=?"
            args += (source_type,)
        return self.rows(sql + " ORDER BY locator", args)

    def add_task(
        self, run_id: str, stage: str, scope: dict, parent: str | None = None, wave: str = "analysis"
    ) -> str:
        tid = digest(f"{run_id}:{stage}:{parent}:{wave}:{json.dumps(scope, sort_keys=True)}".encode())[:32]
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO tasks(id,run_id,parent_task_id,stage,wave,scope,status,created) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (tid, run_id, parent, stage, wave, json.dumps(scope), "PENDING", now()),
            )
        return tid

    def task(self, task_id: str) -> dict:
        rows = self.rows("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError("任务不存在")
        task = rows[0]
        task["scope"] = json.loads(task["scope"])
        task["result"] = json.loads(task["result"]) if task["result"] else None
        return task

    def start_task(self, task_id: str, bundle=None, instruction_hash=None):
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status='RUNNING',skill_id=?,skill_version=?,skill_hash=?,"
                "instruction_hash=?,rules=? WHERE id=?",
                (
                    bundle.skill_id if bundle else None,
                    bundle.version if bundle else None,
                    bundle.content_hash if bundle else None,
                    instruction_hash,
                    json.dumps(bundle.rule_ids) if bundle else None,
                    task_id,
                ),
            )
        task = self.task(task_id)
        self.event(task["run_id"], "task_started", {"task_id": task_id, "stage": task["stage"]})

    def fail_task(self, task_id: str, error: str, status: str = "FAILED"):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status=?,error=? WHERE id=?", (status, error, task_id))
        self.event(self.task(task_id)["run_id"], "task_failed", {"task_id": task_id, "reason": error})

    def finish_service(self, task_id: str, result: dict, status: str = "SUCCEEDED"):
        if status not in {"SUCCEEDED", "SKIPPED"}:
            raise ValueError("服务任务终态无效")
        with self.connect() as db:
            db.execute("UPDATE tasks SET status=?,result=?,error=NULL WHERE id=?", (status, json.dumps(result), task_id))

    def records(self, run_id: str, kind: str | None = None) -> list[dict]:
        sql = "SELECT payload FROM records WHERE run_id=?"
        args = (run_id,)
        if kind:
            sql += " AND kind=?"
            args += (kind,)
        return [json.loads(row["payload"]) for row in self.rows(sql + " ORDER BY created,id", args)]

    def model_artifacts(self, run_id: str) -> list[dict]:
        """Read committed structured artifacts; raw output is not a read path."""
        if not self.rows("SELECT id FROM tasks WHERE run_id=? AND stage='mantis_model' AND status='SUCCEEDED'", (run_id,)):
            return []
        artifacts = {}
        for row in self.rows("SELECT a.* FROM stage_artifacts a JOIN tasks t ON t.id=a.task_id "
                             "WHERE t.run_id=? AND t.status='SUCCEEDED' "
                             "AND a.artifact_type IN ('summary','threat_model') ORDER BY a.created,a.id", (run_id,)):
            if digest(row['payload'].encode()) != row['sha256']:
                raise ValueError("模型产物归档摘要不一致")
            artifacts[(row['artifact_type'], row['campaign'])] = {'artifact_type': row['artifact_type'], 'data': json.loads(row['payload'])}
        return list(artifacts.values())

    def commit_output(self, task_id: str, role: str, output: StageOutput):
        task = self.task(task_id)
        run_id = task["run_id"]
        if task["status"] == "SUCCEEDED":
            return
        known = {r["id"]: r for r in self.records(run_id)}
        read_ids = {
            row["evidence_id"]
            for row in self.rows("SELECT evidence_id FROM task_reads WHERE task_id=?", (task_id,))
        }
        ids = [record.id for record in output.records]
        if len(ids) != len(set(ids)) or set(ids) & set(known):
            raise ValueError("输出记录 ID 重复")
        if role == "mantis_engine":
            known.update({r.id: r.model_dump() for r in output.records})
        for record in output.records:
            if record.engine is not None and role != "mantis_engine":
                raise ValueError("模型不能声明 Mantis 引擎来源")
            if record.kind not in ALLOWED_KINDS[role]:
                raise ValueError("角色输出了未允许的记录类型")
            if role == 'design_analyst' and record.basis == 'OBSERVED':
                raise ValueError('设计材料不能声明代码观察事实')
            if role == 'requirement_generator' and record.origin == 'PCI_DSS':
                raise ValueError('设计需求角色不能声明 PCI DSS 来源')
            for eid in record.evidence_ids + getattr(record, "counter_evidence_ids", []):
                self.read_evidence(run_id, eid)
                if eid not in read_ids:
                    raise ValueError("引用的证据尚未在本任务中实际读取")
            if not record.evidence_ids:
                raise ValueError("记录缺少可定位的原始证据")
            subject_id = getattr(record, "subject_id", None)
            if subject_id and subject_id not in known:
                raise ValueError("复核或评级对象不存在")
            if subject_id and role != "mantis_engine" and subject_id not in task["scope"].get("subject_ids", []):
                raise ValueError("复核或评级对象不属于当前任务")
            if record.kind == "assessment":
                requirement = known.get(record.requirement_id, {})
                if requirement.get("kind") != "requirement":
                    raise ValueError("检查引用的需求不存在")
                if record.requirement_id != task["scope"].get("requirement_id"):
                    raise ValueError("检查结果不属于当前需求")
                if record.acceptance_criterion not in requirement["acceptance_criteria"]:
                    raise ValueError("检查项不属于该需求")
                if record.implementation_status in {'STATIC_SUPPORTED', 'PARTIAL', 'VIOLATED'} and not any(
                    self.read_evidence(run_id, eid)['source_type'] == 'code' for eid in record.evidence_ids
                ):
                    raise ValueError('实现判断必须引用本任务实际读取的代码')
            for req_id in getattr(record, "requirement_ids", []):
                if known.get(req_id, {}).get("kind") != "requirement":
                    raise ValueError("发现引用了不存在的需求")
        if role == "pci_mapper":
            expected = task["scope"].get("clause_ids", [])
            actual = [r.clause_id for r in output.records if r.kind == "applicability"]
            requirement_id = task['scope'].get('requirement_id')
            if requirement_id:
                if len(actual) != len(set(actual)) or (not actual and not output.gaps):
                    raise ValueError('需求匹配结果重复或缺少匹配缺口')
                for decision in output.records:
                    if decision.requirement_ids != [requirement_id] or decision.relevance == 'UNKNOWN':
                        raise ValueError('条款匹配必须对应当前需求并明确相关性')
                    if decision.clause_id not in self.run(run_id)['snapshot']['standard']['requirement_ids']:
                        raise ValueError('匹配条款不属于固定标准库')
                    if not any(self.read_evidence(run_id, eid)['source_type']=='document' for eid in decision.evidence_ids):
                        raise ValueError('条款匹配必须引用原始设计材料')
                    if decision.status=='APPLICABLE' and any(f.strip() for f in decision.missing_facts):
                        raise ValueError('缺少适用范围事实时不能确认适用')
            elif sorted(expected) != sorted(actual):
                raise ValueError("条款适用性库存不完整或重复")
            decisions = {r.clause_id: r for r in output.records if r.kind == 'applicability'}
            for clause_id, decision in decisions.items():
                cited_clauses = {self.read_evidence(run_id, eid)['metadata'].get('clause_id')
                                for eid in decision.evidence_ids}
                if clause_id not in cited_clauses:
                    raise ValueError('适用性结果必须引用对应条款原文')
                if decision.status == 'UNDETERMINED' and not any(f.strip() for f in decision.missing_facts):
                    raise ValueError('未确定适用性必须明确缺失事实')
        if role == 'pci_requirement_generator':
            from .domain import compliance_candidate
            expected = set(task['scope'].get('clause_ids', []))
            candidates = {r['clause_id'] for r in known.values() if r['kind']=='applicability' and compliance_candidate(r)}
            if not expected or not expected <= candidates:
                raise ValueError('合规需求任务必须来自相关或可能相关的条款')
            mapped = set()
            for requirement in (r for r in output.records if r.kind == 'requirement'):
                if requirement.origin != 'PCI_DSS' or not requirement.clause_ids:
                    raise ValueError('合规需求必须具有正式条款来源')
                if any(cid not in expected for cid in requirement.clause_ids):
                    raise ValueError('未确定或不适用的条款不能形成已适用合规需求')
                cited = {self.read_evidence(run_id, eid)['metadata'].get('clause_id') for eid in requirement.evidence_ids}
                if not set(requirement.clause_ids) <= cited:
                    raise ValueError('合规需求必须引用对应条款原文')
                mapped.update(requirement.clause_ids)
            if expected - mapped:
                raise ValueError('适用条款缺少可验收需求')
        if role in {"requirement_reviewer", "finding_reviewer", "finding_critic", "risk_calibrator"}:
            expected = task["scope"].get("subject_ids", [])
            actual = [r.subject_id for r in output.records]
            if sorted(expected) != sorted(actual):
                raise ValueError("复核或评级对象库存不完整或重复")
        if role == "requirement_checker":
            requirement_id = task["scope"].get("requirement_id")
            requirement = known.get(requirement_id, {})
            expected = requirement.get("acceptance_criteria", [])
            if requirement.get("kind") != "requirement" or expected != task["scope"].get("acceptance_criteria"):
                raise ValueError("检查任务与需求验收项库存不一致")
            actual = [r.acceptance_criterion for r in output.records if r.kind == "assessment"]
            if sorted(expected) != sorted(actual):
                raise ValueError("验收项检查库存不完整或重复")
            if any(r.kind == "finding" and r.requirement_ids != [requirement_id] for r in output.records):
                raise ValueError("需求检查发现必须关联当前需求")
            existing = [r for r in known.values() if r["kind"] == "assessment"
                        and r["requirement_id"] == requirement_id]
            if existing:
                raise ValueError("该需求已经提交检查结果")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0] == "SUCCEEDED":
                return
            if role == "requirement_checker":
                # Recheck under the write lock so concurrent tasks cannot submit twice.
                assessments = db.execute("SELECT payload FROM records WHERE run_id=? AND kind='assessment'",
                                         (run_id,)).fetchall()
                if any(json.loads(row[0])["requirement_id"] == requirement_id for row in assessments):
                    raise ValueError("该需求已经提交检查结果")
            for record in output.records:
                payload = record.model_dump()
                if record.kind == "risk":
                    payload["score"] = (
                        record.native_score if record.engine == "mantis"
                        else record.impact_score * record.likelihood_score
                    )
                db.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?)",
                    (record.id, run_id, task_id, record.kind, json.dumps(payload, ensure_ascii=False), now()),
                )
            db.execute(
                "UPDATE tasks SET status='SUCCEEDED',result=?,error=NULL WHERE id=?",
                (output.model_dump_json(), task_id),
            )
        self.event(run_id, "task_completed", {"task_id": task_id, "records": len(output.records)})

    def reserve(self, run_id: str, task_id: str, tokens: int) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            policy = db.execute("SELECT unlimited FROM run_budget_policy WHERE run_id=?", (run_id,)).fetchone()
            unlimited = bool(policy and policy[0])
            physical = db.execute("SELECT physical FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
            used = db.execute(
                "SELECT count(*),COALESCE(SUM(COALESCE(charged_tokens,reserved_tokens)),0) "
                "FROM usage WHERE run_id=?",
                (run_id,),
            ).fetchone()
            task_calls = db.execute("SELECT count(*) FROM usage WHERE task_id=?", (task_id,)).fetchone()[0]
            wave = db.execute("SELECT wave FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            cumulative_exceeded = used[0] >= run["max_requests"] or used[1] + tokens > run["max_tokens"]
            task_exceeded = task_calls >= (run["max_requests"] if wave == "native" else 12)
            if (not physical and not unlimited and cumulative_exceeded) or (
                task_exceeded and wave != "native"
            ) or (not physical and task_exceeded and not unlimited):
                raise BudgetExceeded("累计模型预算达到上限")
            request_id = new_id()
            db.execute(
                "INSERT INTO usage VALUES(?,?,?,?,?,?)",
                (request_id, run_id, task_id, tokens, None, "RESERVED"),
            )
        return request_id

    def settle(self, request_id: str, actual_tokens: int | None):
        with self.connect() as db:
            db.execute(
                "UPDATE usage SET charged_tokens=COALESCE(?,reserved_tokens),status='CHARGED' "
                "WHERE id=? AND status='RESERVED'",
                (actual_tokens, request_id),
            )

    def recover(self):
        self.provider.recover()
        with self.connect() as db:
            db.execute(
                "UPDATE usage SET charged_tokens=reserved_tokens,status='CHARGED' WHERE status='RESERVED'"
            )
            db.execute("UPDATE tasks SET status='PENDING' WHERE status='RUNNING'")
            db.execute(
                "UPDATE runs SET status=CASE WHEN pause_requested=1 THEN 'PAUSED' ELSE 'PENDING' END,"
                "error='服务重启，从已提交检查点恢复' WHERE status='RUNNING'"
            )
