from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Annotated, Union, Literal

from .config import Settings
from .domain import ALLOWED_SOURCES, ALLOWED_KINDS, StageOutput, Record, StrictModel
from pydantic import Field, create_model
from .skills import SkillBundle, compile_instruction, digest
from .store import Store
from .source_references import SourceReferences
from .source_contract import uses_source_contract
from .model_contract import accounted_response
from .provider import OutputContractError, ProviderUnavailable, ProviderBlocked, BudgetExceeded
from .vendor.mantis_llm_gateway import SecretScrubber, wrap_untrusted_content


class ModelStageOutput(StageOutput):
    records: list[Record] = Field(description="明确提交本阶段结果；没有结果也必须显式提交空列表")
    gaps: list[str] = Field(description="明确列出尚未解决的事实；没有则提交空列表")


@lru_cache
def stage_schema(role: str):
    from . import domain
    models = {kind: getattr(domain, kind.capitalize()) for kind in
              ('fact', 'requirement', 'applicability', 'threat', 'assessment', 'finding', 'review', 'risk')}
    selected = tuple(models[kind] for kind in sorted(ALLOWED_KINDS[role]))
    record_type = selected[0] if len(selected) == 1 else Annotated[Union[selected], Field(discriminator='kind')]
    return create_model(role + 'StageOutput', __base__=ModelStageOutput,
                        records=(list[record_type], Field(description='显式提交范围内的每项结果')))


@lru_cache
def submission_model(role: str, structured_sources: bool = False):
    """Homogeneous record lists avoid provider union-branch field mixing."""
    if len(ALLOWED_KINDS[role]) == 1:
        if structured_sources and role in {'design_analyst', 'requirement_generator', 'requirement_reviewer'}:
            from .domain import Fact, Requirement, Review, SourceObligation, ClaimReview, LogicComparison
            if role == 'requirement_generator':
                criterion = create_model('SourceCriterion', __base__=StrictModel,
                    criterion=(str,Field(min_length=1)), source=(SourceObligation,Field()))
                record = create_model(role + 'SourceRecord', __base__=StrictModel, **{
                    **{key:(field.annotation,field) for key,field in Requirement.model_fields.items()
                       if key not in {'acceptance_criteria','source_obligations'}},
                    'statement_source':(SourceObligation,Field()),
                    'acceptance_criteria':(list[criterion],Field(min_length=1))})
                return create_model(role + 'SourceSubmission', __base__=ModelStageOutput,
                                    records=(list[record],Field(description='每个验收项必须配对其自身的原文约束')))
            base = {'design_analyst': Fact, 'requirement_generator': Requirement, 'requirement_reviewer': Review}[role]
            key, item = ('claim_reviews', ClaimReview) if role == 'requirement_reviewer' else ('source_obligations', SourceObligation)
            if role == 'requirement_reviewer':
                item = create_model('SourceClaimReview', __base__=ClaimReview,
                                    logic_comparisons=(list[LogicComparison], Field(min_length=1)))
            record = create_model(role + 'SourceRecord', __base__=base, **{key: (list[item], Field(min_length=1))})
            return create_model(role + 'SourceSubmission', __base__=ModelStageOutput,
                                records=(list[record], Field(description='完整且基于原始来源的结果')))
        if role in {'design_analyst', 'requirement_generator', 'requirement_reviewer'}:
            # Archived skill revisions keep their original output protocol.
            # Do not expose v4 fields to a baseline that predates those methods.
            from . import domain
            kind = next(iter(ALLOWED_KINDS[role]))
            base = getattr(domain, kind.capitalize())
            legacy = create_model(kind.capitalize(), __base__=StrictModel, **{
                key: (field.annotation, field) for key, field in base.model_fields.items()
                if key not in {'source_obligations', 'claim_reviews'}})
            return create_model(role + 'LegacySubmission', __base__=ModelStageOutput,
                                records=(list[legacy], Field(description='明确提交范围内的结果')))
        return stage_schema(role)
    from . import domain
    groups = create_model(role + 'RecordGroups', __base__=StrictModel, **{
        kind: (list[getattr(domain, kind.capitalize())], Field(description='按类型明确提交结果；没有则提交 []'))
        for kind in sorted(ALLOWED_KINDS[role])})
    return create_model(role + 'Submission', __base__=StrictModel,
        records=(groups, Field(description='不同类型使用独立列表；禁止混用记录字段')),
        gaps=(list[str], Field(description='明确列出尚未解决的事实；没有则提交 []')),
        summary=(str, Field(min_length=1)))


@lru_cache
def scoped_pci_submission_model(clause_id: str):
    """Task-owned provenance is deliberately outside the model's data protocol."""
    from .domain import Requirement
    fields = {
        key: (value.annotation, value) for key,value in Requirement.model_fields.items()
        if key not in {'origin','clause_ids'}}
    # Exact echo is compatible; conflicting metadata is rejected, never replaced.
    fields.update(origin=(Literal['PCI_DSS'] | None, None),
                  clause_ids=(list[Literal[clause_id]] | None, Field(default=None,min_length=1,max_length=1)))
    record = create_model('ScopedPCIRequirement', __base__=StrictModel, **fields)
    return create_model('ScopedPCISubmission', __base__=StrictModel,
                        records=(list[record], Field(description='完整分析结果；origin 和 clause_ids 由任务固定')),
                        gaps=(list[str], Field(description='明确列出尚未解决的事实；没有则提交 []')),
                        summary=(str, Field(min_length=1)))


@lru_cache
def scoped_checker_submission_model(requirement_id: str):
    """The task owns requirement links; all analytical fields remain model output."""
    from .domain import Assessment, Finding
    assessment = create_model('ScopedAssessment', __base__=StrictModel, **{
        **{key:(field.annotation,field) for key,field in Assessment.model_fields.items() if key!='requirement_id'},
        'requirement_id':(Literal[requirement_id] | None,None)})
    finding = create_model('ScopedFinding', __base__=StrictModel, **{
        **{key:(field.annotation,field) for key,field in Finding.model_fields.items() if key!='requirement_ids'},
        'requirement_ids':(list[Literal[requirement_id]] | None,Field(default=None,min_length=1,max_length=1))})
    groups = create_model('ScopedCheckGroups', __base__=StrictModel,
                          assessment=(list[assessment],Field(description='逐项覆盖范围内的准确验收项')),
                          finding=(list[finding],Field(description='明确列出候选项；没有则提交 []')))
    return create_model('ScopedCheckSubmission', __base__=StrictModel,
                        records=(groups,Field(description='分析结果按类型分列；需求关联由任务固定')),
                        gaps=(list[str],Field(description='明确列出尚未解决的事实；没有则提交 []')),
                        summary=(str,Field(min_length=1)))


@lru_cache
def scoped_mapping_submission_model(requirement_id: str):
    """The evaluated requirement is task identity, including unrelated decisions."""
    from .domain import Applicability
    record = create_model('ScopedApplicability', __base__=StrictModel, **{
        **{key: (field.annotation, field) for key, field in Applicability.model_fields.items()
           if key != 'requirement_ids'},
        'requirement_ids': (list[Literal[requirement_id]] | None, Field(default=None, min_length=1, max_length=1))})
    return create_model('ScopedMappingSubmission', __base__=ModelStageOutput,
                        records=(list[record], Field(description='每项判断均评估本任务固定的需求')))


@lru_cache
def scoped_control_submission_model(control_id: str, clause_id: str):
    """The explicit single-control task owns its subject, not the verdict."""
    from .domain import Applicability
    record = create_model('ScopedControlApplicability', __base__=StrictModel, **{
        **{key: (field.annotation, field) for key, field in Applicability.model_fields.items()
           if key not in {'control_ids', 'clause_id'}},
        'control_ids': (list[Literal[control_id]] | None, Field(default=None, min_length=1, max_length=1)),
        'clause_id': (Literal[clause_id] | None, None)})
    return create_model('ScopedControlSubmission', __base__=ModelStageOutput,
                        records=(list[record], Field(min_length=1, max_length=1)))


@lru_cache
def scoped_review_submission_model(subject_id: str, criterion_count: int):
    from .domain import Review, ClaimReview, LogicComparison
    entry = create_model('ScopedClaimReview', __base__=StrictModel, **{
        **{key:(field.annotation,field) for key,field in ClaimReview.model_fields.items()
           if key not in {'claim','claim_index','logic_comparisons'}},
        'logic_comparisons':(list[LogicComparison],Field(min_length=1))})
    slots = create_model('ScopedClaimSlots', __base__=StrictModel, **{
        key:(entry,Field()) for key in ['statement', *[f'criterion_{i+1}' for i in range(criterion_count)]]})
    record = create_model('ScopedSourceReview', __base__=StrictModel, **{
        **{key:(field.annotation,field) for key,field in Review.model_fields.items()
           if key not in {'subject_id','claim_reviews'}},
        'subject_id':(Literal[subject_id],Field()), 'claim_reviews':(slots,Field())})
    return create_model('ScopedReviewSubmission', __base__=ModelStageOutput,
                        records=(list[record],Field(min_length=1,max_length=1)))


def parse_submission(role: str, value, *, structured_sources: bool = False) -> StageOutput:
    schema = submission_model(role, structured_sources)
    parsed = schema.model_validate_json(value) if isinstance(value, str) else schema.model_validate(value)
    if len(ALLOWED_KINDS[role]) == 1:
        return parsed
    records = [record for kind in sorted(ALLOWED_KINDS[role]) for record in getattr(parsed.records, kind)]
    # A declared transport projection only. No values, verdicts or IDs are inferred.
    return stage_schema(role).model_validate(dict(records=records, gaps=parsed.gaps, summary=parsed.summary))


class AdkExecutor:
    def __init__(self, settings: Settings, store: Store, model_factory=None):
        self.settings = settings
        self.store = store
        self.model_factory = model_factory

    async def execute(self, task: dict, bundle: SkillBundle, context: dict) -> StageOutput:
        # Prevent LiteLLM from fetching its remote pricing catalog at import time.
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        from google.adk.agents import Agent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.runners import Runner, RunConfig
        from google.adk.sessions.sqlite_session_service import SqliteSessionService
        from google.adk.tools.set_model_response_tool import SetModelResponseTool
        from google.genai import types

        run_id = task["run_id"]
        allowed_sources = ALLOWED_SOURCES[bundle.skill_id]
        structured_sources = uses_source_contract(bundle.skill_id, bundle.version)
        pending_requests: list[str] = []
        failures: list[Exception] = []
        owned_provenance = ({'origin':'PCI_DSS','clause_ids':task['scope']['clause_ids']}
                            if bundle.skill_id=='pci_requirement_generator'
                            and len(task['scope'].get('clause_ids', []))==1
                            and (self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id') else None)
        owned_check = task['scope']['requirement_id'] if bundle.skill_id=='requirement_checker' else None
        owned_mapping = task['scope'].get('requirement_id') if bundle.skill_id == 'pci_mapper' else None
        owned_control = task['scope'].get('control_id') if bundle.skill_id == 'pci_mapper' else None
        owned_clause = (task['scope'].get('clause_ids') or [None])[0] if owned_control else None
        if owned_control and (not owned_clause or len(task['scope']['clause_ids']) != 1):
            raise OutputContractError('固定控制任务必须具有唯一原始条款')
        schema = (scoped_checker_submission_model(owned_check) if owned_check else
                  scoped_control_submission_model(owned_control, owned_clause) if owned_control else
                  scoped_mapping_submission_model(owned_mapping) if owned_mapping else
                  scoped_pci_submission_model(owned_provenance['clause_ids'][0]) if owned_provenance else
                  submission_model(bundle.skill_id, structured_sources=structured_sources))
        if bundle.skill_id == 'requirement_reviewer' and structured_sources:
            subjects = task['scope'].get('subject_ids', [])
            if len(subjects) == 1:
                target = next(r for r in self.store.records(run_id,'requirement') if r['id'] == subjects[0])
                schema = scoped_review_submission_model(subjects[0], len(target['acceptance_criteria']))
        references = SourceReferences()
        storage_schema = stage_schema(bundle.skill_id) if structured_sources else schema
        submitted_via_tool = False
        invocation_reads: set[str] = set()
        instruction_observed = False

        def source_ids():
            identifiers = [e['id'] for e in self.store.evidence(run_id) if e['source_type'] in allowed_sources]
            references.register(identifiers)
            return identifiers

        source_ids()
        required_reads = (set(task['scope'].get('evidence_ids', [])) if bundle.skill_id == 'design_analyst'
                          else {e['id'] for e in self.store.evidence(run_id, 'document')}
                          if bundle.skill_id == 'requirement_generator' else set())

        def verify_material_completion():
            if required_reads - invocation_reads:
                raise OutputContractError('本阶段指定的设计材料尚未全部读取，不能提交已完成结果')

        def project_submission(value, *, receipt=False):
            if not structured_sources:
                return value
            import copy
            from .output_projection import project_empty_extras, resolve_claim_indices
            value, removed = copy.deepcopy(value), []
            targets = {r['id']: r for r in self.store.records(run_id, 'requirement')
                       if r['id'] in task['scope'].get('subject_ids', [])}
            if bundle.skill_id == 'requirement_reviewer':
                for index, record in enumerate(value.get('records', [])):
                    target = targets.get(record.get('subject_id'), {})
                    for key in ('origin', 'clause_ids'):
                        if key in record and key in target:
                            if record[key] != target[key]:
                                raise OutputContractError('复核返回的来源注释与原需求冲突')
                            removed.append({'path':['records',index,key], 'value':record.pop(key),
                                            'rule':'exact_target_annotation_echo'})
            value, empty_removed = project_empty_extras(value, schema.model_json_schema())
            removed.extend(empty_removed)
            schema.model_validate(value)
            value = resolve_claim_indices(value, bundle.skill_id, targets)
            if receipt and removed:
                self.store.save_model_output(task['id'], json.dumps({
                    'transport':'stage_output_projection_v1', 'removed':removed,
                    'rule':'Undeclared empty decoration or exact target-owned annotation only; no analytical values changed'},
                    ensure_ascii=False))
            return value

        def submission_schema():
            result = schema.model_json_schema()
            read_ids = sorted(invocation_reads)
            allowed = read_ids or source_ids()
            code_ready = bundle.skill_id!='requirement_checker' or any(
                self.store.read_evidence(run_id,eid)['source_type']=='code' for eid in read_ids)
            for definition in result.get('$defs', {}).values():
                fields = definition.get('properties', {})
                if structured_sources and ('source_obligations' in fields or 'claim_reviews' in fields or 'statement_source' in fields):
                    for key in ('evidence_ids', 'engine', 'native_id', 'native_status', 'native_data'):
                        fields.pop(key, None)
                    definition['required'] = [key for key in definition.get('required', []) if key in fields]
                if structured_sources and 'claim_index' in fields:
                    if bundle.skill_id == 'requirement_reviewer':
                        fields.pop('claim', None)
                        fields['claim_index'] = {'type':'integer', 'minimum':0,
                            'description':'0 对应 statement，1..N 按原顺序对应验收项；每个索引必须且只能覆盖一次'}
                        if bundle.skill_id == 'requirement_reviewer':
                            counts = [len(r['acceptance_criteria']) for r in self.store.records(run_id,'requirement')
                                      if r['id'] in task['scope'].get('subject_ids', [])]
                            if counts:
                                fields['claim_index']['enum'] = list(range(max(counts) + 1))
                    elif bundle.skill_id == 'requirement_generator':
                        fields.pop('claim', None)
                        fields.pop('claim_index', None)
                    else:
                        fields.pop('claim_index', None)
                    definition['required'] = [key for key in definition.get('required', []) if key in fields]
                if structured_sources and 'quote_id' in fields and 'text' in fields:
                    definition['properties'] = {'quote_id': {'type': 'string',
                        'description': '当前 read_evidence 返回的准确 Qxxxxx 段落编号',
                        **({'enum': list(references.by_quote)} if references.by_quote else {'pattern': '^Q[0-9]{5}$'})}}
                    definition['required'] = ['quote_id']
                    continue
                if bundle.skill_id=='requirement_checker' and 'id' in fields:
                    fields['id']['pattern'] = '^'+task['id']+'_[a-zA-Z0-9_-]+$'
                    fields['id']['description'] = '使用当前任务命名空间内的新结果编号；禁止复用上游需求编号'
                if owned_check:
                    fields.pop('requirement_id',None)
                    fields.pop('requirement_ids',None)
                if owned_mapping:
                    fields.pop('requirement_ids', None)
                    definition['required'] = [key for key in definition.get('required', []) if key in fields]
                if owned_control:
                    fields.pop('control_ids', None)
                    fields.pop('clause_id', None)
                    definition['required'] = [key for key in definition.get('required', []) if key in fields]
                if 'implementation_status' in fields and not code_ready:
                    fields['implementation_status']['enum'] = ['UNKNOWN','NOT_CODE_VERIFIABLE']
                elif 'implementation_status' in fields:
                    fields['implementation_status']['enum'] = [s for s in fields['implementation_status']['enum']
                                                              if s != 'EXTERNAL_EVIDENCE_REQUIRED']
                if bundle.skill_id == 'pci_mapper' and task['scope'].get('compliance_scope_policy') in {'code_related_v2', 'precomputed_controls_v3', 'selected_controls_v1'}:
                    if 'control_scope' in fields:
                        fields['control_scope'] = {'type': 'string', 'enum': ['CODE_RELATED', 'NON_CODE', 'UNKNOWN']}
                        required = definition.setdefault('required', [])
                        if 'control_scope' not in required:
                            required.append('control_scope')
                if owned_provenance:
                    for key in owned_provenance:
                        fields.pop(key,None)
                if 'origin' in fields and bundle.skill_id == 'pci_requirement_generator':
                    fields['origin']['enum'] = ['PCI_DSS']
                elif 'origin' in fields and bundle.skill_id == 'requirement_generator':
                    fields['origin']['enum'] = ['EXPLICIT_DESIGN', 'INFERRED_SECURITY']
                for name in ('evidence_ids', 'counter_evidence_ids'):
                    if name in fields and allowed:
                        fields[name]['items']['enum'] = references.encode(allowed)
                if 'evidence_id' in fields and allowed:
                    fields['evidence_id']['enum'] = references.encode(allowed)
                if 'subject_id' in fields and task['scope'].get('subject_ids'):
                    fields['subject_id']['enum'] = task['scope']['subject_ids']
                if 'clause_id' in fields and task['scope'].get('clause_ids'):
                    fields['clause_id']['enum'] = task['scope']['clause_ids']
                if 'control_ids' in fields and task['scope'].get('compliance_scope_policy') == 'precomputed_controls_v3':
                    read_controls = [r['control_id'] for r in self.store.rows(
                        'SELECT control_id FROM task_control_reads WHERE task_id=?', (task['id'],))]
                    if read_controls:
                        fields['control_ids']['items']['enum'] = sorted(read_controls)
                if 'requirement_id' in fields and task['scope'].get('requirement_id'):
                    fields['requirement_id']['enum'] = [task['scope']['requirement_id']]
                if 'acceptance_criterion' in fields and task['scope'].get('acceptance_criteria'):
                    fields['acceptance_criterion']['enum'] = task['scope']['acceptance_criteria']
                if 'requirement_ids' in fields and task['scope'].get('requirement_id'):
                    fields['requirement_ids']['items']['enum'] = [task['scope']['requirement_id']]
                if 'clause_ids' in fields and task['scope'].get('clause_ids'):
                    fields['clause_ids']['items']['enum'] = task['scope']['clause_ids']
            return result

        class FinalStageTool(SetModelResponseTool):
            def _get_declaration(self):
                return types.FunctionDeclaration(name='set_model_response',
                    description='提交完整的最终 StageOutput：records、gaps、summary；提交后本次分析结束。',
                    parameters_json_schema=submission_schema())

            async def run_async(self, *, args, tool_context):
                nonlocal submitted_via_tool
                # Validation errors stop the run, never return repair feedback to a model.
                try:
                    verify_material_completion()
                    result = storage_schema.model_validate(references.decode_submission(project_submission(args, receipt=True), quote_handles_required=structured_sources))
                except (ValueError, OutputContractError) as exc:
                    failure = exc if isinstance(exc, OutputContractError) else OutputContractError('模型提交的阶段结果未通过结构化校验')
                    failures.append(failure)
                    raise failure from None
                tool_context.actions.set_model_response = result.model_dump()
                submitted_via_tool = True
                return result.model_dump()

        def list_evidence(source_type: str = "", page: int = 1, page_size: int = 30) -> dict:
            """分页列出快照来源编号；列出编号不代表已完成分析。"""
            rows = [
                row
                for row in self.store.evidence(run_id, source_type or None)
                if row["source_type"] in allowed_sources
            ]
            start = max(0, page - 1) * min(max(page_size, 1), 50)
            source_ids()
            return references.encode({"total": len(rows), "items": rows[start : start + min(max(page_size, 1), 50)]})

        def read_evidence(evidence_id: str) -> str:
            """读取属于本快照的一块原始来源材料。"""
            source_ids()
            row = self.store.read_evidence(run_id, references.resolve(evidence_id))
            if row["source_type"] not in allowed_sources:
                raise ValueError("此角色不能读取该类材料")
            self.store.note_read(task["id"], row['id'])
            invocation_reads.add(row['id'])
            if structured_sources and row['source_type'] in {'document', 'standard'}:
                row['passages'] = references.register_quotes(row)
                row.pop('content')  # All original paragraphs are present once, not duplicated.
                self.store.save_model_output(task['id'], json.dumps({'transport': 'quote_reference_catalog_v1',
                    'references': references.by_quote}, ensure_ascii=False))
            text = json.dumps(references.encode(row), ensure_ascii=False)
            return wrap_untrusted_content(SecretScrubber.scrub(text), filename=row["locator"])

        def search_evidence(query: str, source_type: str = "", limit: int = 20, offset: int = 0) -> dict:
            """对来源做字面搜索，返回 total/has_more/next_offset；命中材料需另行读取。搜索命中不代表语义覆盖。"""
            # Literal bounded search handles Chinese without a remote embedding service.
            if not query.strip() or len(query) > 200 or offset < 0:
                raise ValueError("查询为空、过长或分页位置无效")
            page_limit = min(max(limit, 1), 50)
            filters = (run_id, query, query, source_type, source_type, *sorted(allowed_sources))
            condition = ("run_id=? AND (instr(lower(content),lower(?))>0 OR instr(lower(locator),lower(?))>0) "
                         "AND (?='' OR source_type=?) AND source_type IN (" + ','.join('?' for _ in allowed_sources) + ")")
            total = self.store.rows('SELECT count(*) AS n FROM evidence WHERE ' + condition, filters)[0]['n']
            rows = self.store.rows(
                'SELECT id,source_type,locator FROM evidence WHERE ' + condition + ' ORDER BY locator,id LIMIT ? OFFSET ?',
                (*filters, page_limit, offset),
            )
            source_ids()
            return references.encode({
                "items": [
                    row
                    for row in rows
                    if row["source_type"] in allowed_sources
                    and (not source_type or row["source_type"] == source_type)
                ], 'total': total, 'has_more': offset + len(rows) < total,
                'next_offset': offset + len(rows), 'read_required': True,
                'coverage': 'Literal search of registered source blocks; not semantic reachability or proof of absence.'
            })

        def read_evidence_batch(evidence_ids: list[str]) -> dict:
            """批量读取文档块；达到字节上限时明确返回尚未读取的编号。"""
            source_ids()
            result, size = {}, 0
            for eid in evidence_ids:
                row = self.store.read_evidence(run_id, references.resolve(eid))
                if row["source_type"] not in allowed_sources:
                    raise ValueError("此角色不能读取该类材料")
                length = len(json.dumps(row, ensure_ascii=False).encode())
                if result and size + length > 45000:
                    break
                result[eid] = read_evidence(eid)
                size += length
            return {"items": result, "remaining_ids": [eid for eid in evidence_ids if eid not in result]}

        async def search_standard_library(query: str = '', section: str = '', limit: int = 8, offset: int = 0) -> dict:
            """检索预先入库的标准和控制向量，仅向量化本次查询；选择前必须读取命中的控制。"""
            from .standard_library import search
            from .embeddings import encoder
            prepared = (self.store.run(run_id)['snapshot'].get('standard') or {}).get('prepared')
            embedder = encoder(self.settings.embedding_model_dir) if prepared and query.strip() else None
            import asyncio
            from .retrieval_models import reranker
            ranker = reranker(self.settings.reranker_model_dir) if prepared and prepared.get('retrieval', {}).get('reranker_profile') else None
            return await asyncio.to_thread(search, self.store, run_id, query, section, limit, offset, embedder=embedder, reranker_model=ranker)

        def read_standard_control(control_id: str) -> dict:
            """读取不可变的双语控制模板及其已入库的标准原文。"""
            from .control_catalog import read_control
            data, eid = read_control(self.store, run_id, task['id'], control_id)
            source_ids()
            return {'standard_control': data, 'source': read_evidence(eid)}

        def read_standard_clause(clause_id: str) -> dict:
            """读取标准条款及其固定控制索引；选择前必须读取控制模板，索引不等于已读控制内容。"""
            from .standard_library import read
            data, eid = read(self.store, run_id, clause_id)
            source_ids()
            result = {'structured_clause': data, 'source': read_evidence(eid)}
            if (self.store.run(run_id)['snapshot'].get('standard') or {}).get('prepared'):
                from .control_catalog import clause_controls
                result['fixed_controls'] = clause_controls(self.store, run_id, clause_id)
            return result

        def read_standard_context(context_id: str) -> str:
            """按准确的目录编号读取标准原始范围说明或父级上下文页。"""
            from .standard_library import read
            _, eid = read(self.store, run_id, context_id, context=True)
            source_ids()
            return read_evidence(eid)

        def read_upstream_records(record_ids: list[str]) -> dict:
            """读取本任务库存中完整的上游记录。上游输出仅作为调查线索，不能代替原始来源引用。"""
            permitted = {r['id'] for r in context.get('upstream_records', [])}
            if not record_ids or any(rid not in permitted for rid in record_ids):
                raise ValueError('上游记录不属于本任务库存')
            originals = {r['id']: r for r in self.store.records(run_id)}
            items, size = {}, 0
            for rid in record_ids[:3]:
                item = originals[rid]
                length = len(json.dumps(item, ensure_ascii=False).encode())
                if items and size + length > 45000:
                    break
                items[rid] = wrap_untrusted_content(json.dumps(references.encode(item), ensure_ascii=False), filename='upstream-record-'+rid)
                size += length
            return {'items': items, 'remaining_ids': [rid for rid in record_ids if rid not in items]}

        from .mantis_engine import MantisEngine

        async def navigate(tool: str, arguments: dict) -> dict:
            result = await MantisEngine(self.settings, self.store).navigate(run_id, tool, arguments, task['id'])
            if result.get('read') and result.get('evidence_id'):
                invocation_reads.add(result['evidence_id'])
            source_ids()
            return references.encode(result)

        async def find_symbol(name: str, offset: int = 0) -> dict:
            """通过原生结构索引查找符号；没有命中不代表安全。"""
            return await navigate("find_symbol", {"name": name, "offset": offset})

        async def find_callers(symbol: str, filepath: str = "", offset: int = 0) -> dict:
            """定位索引内的调用方；动态调用可能缺失。"""
            return await navigate("find_callers", {"symbol": symbol, "filepath": filepath, "offset": offset})

        async def find_callees(symbol: str, filepath: str = "", offset: int = 0) -> dict:
            """定位索引内的被调用方；相关调用路径必须回到源码确认。"""
            return await navigate("find_callees", {"symbol": symbol, "filepath": filepath, "offset": offset})

        async def get_function_boundary(filepath: str, line: int) -> dict:
            """读取完整函数，同时返回可引用的源码来源编号。"""
            return await navigate("get_function_boundary", {"filepath": filepath, "line": line})

        async def get_mantis_model() -> dict:
            """读取本次分析的代码摘要和威胁模型。"""
            result = await MantisEngine(self.settings, self.store).navigate(run_id, "get_model", {}, task["id"])
            return {"result": wrap_untrusted_content(SecretScrubber.scrub(result["result"]), filename="mantis-model")}

        def before_model(callback_context, llm_request):
            nonlocal instruction_observed
            if not instruction_observed:
                with self.store.connect() as db:
                    db.execute('UPDATE tasks SET instruction_hash=? WHERE id=?',
                               (digest(str(llm_request.config.system_instruction).encode()), task['id']))
                instruction_observed = True
            # Source references are a closed set, not text a model may approximate.
            # Bind read-tool arguments too, preserving the original IDs unchanged.
            allowed = references.encode(source_ids())
            for tool in llm_request.config.tools or []:
                for declaration in tool.function_declarations or []:
                    if declaration.name == 'set_model_response':
                        # ADK caches declarations. Rebind the final contract after
                        # every read/navigation, so unread search hits cannot be cited.
                        declaration.parameters = None
                        declaration.parameters_json_schema = submission_schema()
                        continue
                    if declaration.name not in {'read_evidence', 'read_evidence_batch'} or not allowed:
                        continue
                    name = 'evidence_id' if declaration.name == 'read_evidence' else 'evidence_ids'
                    if declaration.parameters_json_schema is not None:
                        field = declaration.parameters_json_schema['properties'][name]
                        (field if name == 'evidence_id' else field['items'])['enum'] = allowed
                    elif declaration.parameters and declaration.parameters.properties:
                        field = declaration.parameters.properties[name]
                        (field if name == 'evidence_id' else field.items).enum = allowed
            if not self.model_factory:
                return
            serialized = str(llm_request.config.system_instruction) + "".join(
                content.model_dump_json(exclude_none=True) for content in llm_request.contents
            )
            serialized += json.dumps(schema.model_json_schema(), ensure_ascii=False)
            reserved = len(serialized.encode()) + 4096
            pending_requests.append(self.store.reserve(run_id, task["id"], reserved))

        def after_model(callback_context, llm_response):
            usage = llm_response.usage_metadata
            if pending_requests:
                self.store.settle(pending_requests.pop(0), getattr(usage, "total_token_count", None))
            from .model_contract import validate_native_response
            parts = llm_response.content.parts if llm_response.content else []
            calls = [part.function_call for part in parts if part.function_call]
            if calls:
                for call in calls:
                    if call.name == 'set_model_response':
                        self.store.save_model_output(task['id'], json.dumps(dict(call.args or {}), ensure_ascii=False))
            else:
                self.store.save_model_output(task['id'], ''.join(part.text or '' for part in parts))
            try:
                validate_native_response(llm_response, storage_schema, projector=project_submission)
            except OutputContractError as exc:
                failures.append(exc)
                raise

        instruction, _ = compile_instruction(bundle, language=self.store.run(run_id)["language"])
        instruction += ('\n来源引用协议：读取工具和 evidence_ids 必须使用本次调用提供的准确短句柄'
                        '（S0001、S0002 等）。短句柄通过固定映射表指向原始来源。不得虚构、缩短、'
                        '近似拼写或修补句柄。程序只将有效句柄还原为原始来源 ID，不改写分析内容。')
        instruction += ('\n短句柄仅用于工具参数和引用字段。标题、rationale、summary、gaps 中应使用'
                        '可读的文件名与行号或页码说明来源，不得把短句柄当成自然语言中的来源名称。')
        if owned_provenance:
            instruction += ('\n本次使用 task_owned_requirement_provenance_v1。origin 和 clause_ids 由任务固定为 '
                +json.dumps(owned_provenance)+'。请省略这些元数据；回传完全相同的值也可接受，冲突值会被拒绝，'
                '程序绝不覆盖冲突。该协议优先于存储型 Requirement 的通用字段说明。必须提交完整分析字段：'
                'title、module、statement、acceptance_criteria、rationale、实际来源引用，以及记录 ID/kind。'
                '程序在校验后只补入上述已声明的任务归属，不补写或改写分析内容。每条需求必须引用本任务'
                '实际读取的、准确对应目标条款的原文来源。')
            context = {**context, 'task_owned_requirement_provenance':owned_provenance}
        if bundle.skill_id=='requirement_checker':
            instruction += ('\n本次使用 task_owned_check_provenance_v1。关联需求固定为 '+owned_check+'。'
                '省略 assessment.requirement_id 和 finding.requirement_ids，程序只补入这个已声明关联；'
                '完全相同的回传可接受，冲突关联直接拒绝，绝不替换。该规则优先于存储字段通用说明。'
                '分析字段、准确的验收项文本、判断和来源引用仍须完整提交。')
            instruction += ('\n新结果 ID 必须以 '+task['id']+'_ 开头并带唯一的字母数字后缀。每条 assessment '
                '和 finding 使用不同 ID。requirement_id 指向既有需求，不能当作新记录 ID；程序会拒绝重名，'
                '不会给返回的记录改名。')
            instruction += ('\n实现判断的源码门槛：本任务实际读取原始代码前，只允许 UNKNOWN 或 '
                'NOT_CODE_VERIFIABLE。必须使用源码导航和读取工具调查实现后，才能提交 STATIC_SUPPORTED、'
                'PARTIAL 或 VIOLATED，且这些判断的 evidence_ids 必须包含实际读取的原始代码句柄。'
                '设计文档、标准、上游记录和代码模型不能代替源码依据。状态枚举只在实际读取代码后开放，'
                '搜索命中本身不会开放实现判断。')
        if owned_mapping:
            instruction += ('\n本次使用 task_owned_mapping_provenance_v1。每条 applicability 都评估任务需求 '
                +owned_mapping+'。省略 requirement_ids，程序只补入该已知任务关联，包括 UNRELATED、UNKNOWN '
                '或排除结论。这只是评估对象，不表示已经相关或适用。完全相同的回传可接受，冲突关联直接'
                '拒绝，绝不替换。本协议优先于存储型 Applicability 的通用说明。分析字段、控制 ID、条件、'
                '判断和原始来源引用仍全部由模型准确提交。')
        if owned_control:
            instruction += ('\n本次使用 task_owned_control_provenance_v1，仅评估条款 '+owned_clause+' 的固定控制 '
                +owned_control+'。省略 control_ids 和 clause_id；程序只补入上述已声明的任务对象标识，'
                '包括 UNRELATED、NON_CODE、UNKNOWN 和 NOT_APPLICABLE 结论。相同回传可接受，冲突值拒绝，'
                '绝不覆盖。准确提交一条 Applicability；本协议优先于通用字段说明。该对象关联不表示相关、'
                '适用或已实现。所有分析字段、真实设计需求关联、原始来源、条件、缺失事实及理由仍由你判断。'
                '绝不能生成 Requirement。')
        if len(ALLOWED_KINDS[bundle.skill_id]) > 1:
            instruction += ('\n最终提交协议：records 是对象，包含以下独立列表：'
                            + ', '.join(sorted(ALLOWED_KINDS[bundle.skill_id]))
                            + '。所有列表都必须显式提交，没有记录时使用 []。各列表只能包含对应类型及其'
                              '声明字段，不得混入其他类型字段。程序只展开列表，不改变字段值。')
        store = self.store
        class AccountedModel(LiteLlm):
            async def generate_content_async(model, llm_request, stream=False):
                class Delegate:
                    def generate_content_async(_, request, stream=False):
                        return LiteLlm.generate_content_async(model, request, stream=False)
                try:
                    async for response in accounted_response(Delegate(), llm_request, store, run_id, task["id"]):
                        yield response
                except (OutputContractError, ProviderUnavailable, ProviderBlocked, BudgetExceeded) as exc:
                    failures.append(exc)
                    raise
        model = (
            self.model_factory()
            if self.model_factory
            else AccountedModel(
                model=f"openai/{self.settings.model_id}",
                api_base=self.settings.gateway + "/v1",
                api_key="local-gateway",
                max_retries=0,
                max_tokens=self.settings.agent_output_tokens,
                temperature=0.0,
            )
        )
        agent = Agent(
            name=task["stage"],
            model=model,
            instruction=instruction,
            tools=[list_evidence, read_evidence, read_evidence_batch, search_evidence, read_upstream_records, FinalStageTool(schema)] + (
                [search_standard_library, read_standard_clause, read_standard_context] + (
                    [read_standard_control] if (self.store.run(run_id)['snapshot'].get('standard') or {}).get('prepared') else [])
                if 'standard' in allowed_sources and (self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id') else []
            ) + (
                [find_symbol, find_callers, find_callees, get_function_boundary, get_mantis_model]
                if bundle.skill_id == "requirement_checker" else []
            ),
            before_model_callback=before_model,
            after_model_callback=after_model,
        )
        sessions = SqliteSessionService(db_path=str(self.settings.state_dir / "adk-sessions.sqlite3"))
        # New isolated invocation after a crash; committed stage boundaries are the resume contract.
        session = await sessions.create_session(app_name="security_auditor", user_id=run_id)
        if 'standard' in allowed_sources and (self.store.run(run_id)['snapshot'].get('standard') or {}).get('library_id'):
            from .standard_library import overview
            context = {**context, 'standard_library': overview(self.store, run_id)}
        if bundle.skill_id=='requirement_checker':
            requirement = next(r for r in self.store.records(run_id, 'requirement')
                               if r['id']==task['scope']['requirement_id'])
            materials, remaining, size = {}, [], 0
            for eid in requirement['evidence_ids']:
                source = self.store.read_evidence(run_id, eid)
                if source['source_type'] not in {'document','standard'}:
                    continue
                length = len(json.dumps(source, ensure_ascii=False).encode())
                if size+length>40000:
                    remaining.append(eid)
                    continue
                materials[eid] = read_evidence(eid)
                size += length
            context = {**context, 'requirement_materials':materials,
                       'remaining_requirement_source_ids':remaining}
        if bundle.skill_id in {'pci_mapper', 'pci_requirement_generator'}:
            # Deliver every normative target verbatim, rather than trusting a
            # model to request clauses it must decide. Context pages remain tools.
            context = {**context, 'target_materials': {
                eid: read_evidence(eid) for eid in task['scope'].get('evidence_ids', [])}}
            context['target_references'] = {
                self.store.read_evidence(run_id, eid)['metadata']['clause_id']: eid
                for eid in task['scope'].get('evidence_ids', [])}
            # Upstream facts are leads, not substitutes for original design.
            # Deliver their document dependencies before allowing citations.
            design_references = {eid for record in context.get('upstream_records', [])
                                 for eid in record.get('evidence_ids', [])}
            document_ids = {e['id'] for e in self.store.evidence(run_id, 'document')}
            context['design_materials'] = {eid: read_evidence(eid) for eid in sorted(design_references & document_ids)}
            context['upstream_record_mode'] = ('此处仅含紧凑索引和准确适用条件。使用 read_upstream_records '
                '读取完整原始陈述、理由和尚缺的范围事实。仅可引用 design_materials/target_materials '
                '已提供或通过来源工具实际读取的原始材料；上游记录仅作为分析线索。')
            if task['scope'].get('control_id'):
                context['selected_control'] = read_standard_control(task['scope']['control_id'])
        context = {**context, 'required_document_reads': sorted(required_reads)}
        if bundle.skill_id == 'requirement_generator':
            context['upstream_record_mode'] = (
                '完整的事实导航库存。较长原始引文、来源约束和理由仍原样保存在数据库，使用 '
                'read_upstream_records 读取完整事实。索引条目既不是原始依据，也不证明已读完文档。'
                '提交需求前必须读取每个必读原始设计块，并保留原文的业务条件和跨模块关系。')
        message = json.dumps(references.encode(context), ensure_ascii=False)
        limit = 160000 if bundle.skill_id in {'pci_mapper', 'pci_requirement_generator'} else 80000
        if len(message.encode()) > limit:
            raise OutputContractError("任务输入上下文超出容量，需要精简上游对象或细分任务；尚未调用模型", code='input_context')
        runner = Runner(app_name="security_auditor", agent=agent, session_service=sessions)
        final = ""
        try:
            async for event in runner.run_async(
                user_id=run_id,
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=SecretScrubber.scrub(message))]
                ),
                run_config=RunConfig(max_llm_calls=self.settings.agent_max_calls if self.settings.enforce_budgets else 0),
            ):
                if event.error_code:
                    # ADK can convert a raised exception to an error event. Preserve
                    # the typed failure so recovery does not mistake it for bad output.
                    if failures:
                        raise failures[-1]
                    raise OutputContractError("模型调用或结构化输出失败")
                if event.is_final_response() and event.content:
                    final = "".join(part.text or "" for part in event.content.parts)
            self.store.save_model_output(task["id"], final)
            try:
                # Use the same role-bound schema advertised to and validated for
                # this invocation, including its explicitly declared defaults.
                if owned_check:
                    payload = schema.model_validate_json(final).model_dump()
                    for record in payload['records']['assessment']:
                        record['requirement_id'] = owned_check
                    for record in payload['records']['finding']:
                        record['requirement_ids'] = [owned_check]
                    parsed = parse_submission(bundle.skill_id,payload)
                elif owned_mapping:
                    payload = schema.model_validate_json(final).model_dump()
                    for record in payload['records']:
                        record['requirement_ids'] = [owned_mapping]
                    parsed = stage_schema(bundle.skill_id).model_validate(payload)
                elif owned_control:
                    payload = schema.model_validate_json(final).model_dump()
                    for record in payload['records']:
                        record['control_ids'] = [owned_control]
                        record['clause_id'] = owned_clause
                    parsed = stage_schema(bundle.skill_id).model_validate(payload)
                elif owned_provenance:
                    parsed = schema.model_validate_json(final)
                    payload = parsed.model_dump()
                    payload['records'] = [{**record, **owned_provenance} for record in payload['records']]
                    parsed = stage_schema(bundle.skill_id).model_validate(payload)
                else:
                    if structured_sources:
                        payload = json.loads(final)
                        if not submitted_via_tool:
                            payload = project_submission(payload, receipt=True)
                        parsed = storage_schema.model_validate(payload)
                    else:
                        parsed = parse_submission(bundle.skill_id, final)
                output = stage_schema(bundle.skill_id).model_validate(
                    references.decode_submission(parsed.model_dump(), quote_handles_required=structured_sources))
                if any(eid not in invocation_reads for record in output.records
                       for eid in record.evidence_ids+getattr(record,'counter_evidence_ids',[])):
                    raise OutputContractError('引用的原始来源尚未在本次调用中读取，阶段未完成')
                verify_material_completion()
                return output
            except OutputContractError:
                raise
            except ValueError:
                raise OutputContractError("模型最终结果未通过结构化校验，原始返回已保存") from None
        finally:
            if owned_control:
                self.store.save_model_output(task['id'], json.dumps({
                    'transport': 'task_owned_control_provenance_v1', 'task_id': task['id'],
                    'control_ids': [owned_control], 'clause_id': owned_clause}, ensure_ascii=False))
            if owned_mapping:
                self.store.save_model_output(task['id'], json.dumps(
                    {'transport': 'task_owned_mapping_provenance_v1', 'task_id': task['id'],
                     'requirement_ids': [owned_mapping]}, ensure_ascii=False))
            if owned_check:
                self.store.save_model_output(task['id'],json.dumps(
                    {'transport':'task_owned_check_provenance_v1','task_id':task['id'],
                     'requirement_id':owned_check},ensure_ascii=False))
            if owned_provenance:
                self.store.save_model_output(task['id'], json.dumps(
                    {'transport':'task_owned_requirement_provenance_v1','task_id':task['id'],
                     'fixed_fields':owned_provenance},ensure_ascii=False))
            self.store.save_model_output(task['id'], json.dumps(
                {'transport': 'source_reference_catalog_v1', 'references': references.by_handle,
                 'invocation_read_ids':sorted(invocation_reads)}, ensure_ascii=False))
            for request_id in pending_requests:
                self.store.settle(request_id, None)
            await runner.close()
