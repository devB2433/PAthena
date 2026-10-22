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
    records: list[Record] = Field(description="Explicit stage results; an empty list must be submitted explicitly")
    gaps: list[str] = Field(description="Explicit unresolved facts; submit an empty list if there are none")


@lru_cache
def stage_schema(role: str):
    from . import domain
    models = {kind: getattr(domain, kind.capitalize()) for kind in
              ('fact', 'requirement', 'applicability', 'threat', 'assessment', 'finding', 'review', 'risk')}
    selected = tuple(models[kind] for kind in sorted(ALLOWED_KINDS[role]))
    record_type = selected[0] if len(selected) == 1 else Annotated[Union[selected], Field(discriminator='kind')]
    return create_model(role + 'StageOutput', __base__=ModelStageOutput,
                        records=(list[record_type], Field(description='Submit every scoped result explicitly')))


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
                                    records=(list[record],Field(description='Every criterion paired with its own source constraints')))
            base = {'design_analyst': Fact, 'requirement_generator': Requirement, 'requirement_reviewer': Review}[role]
            key, item = ('claim_reviews', ClaimReview) if role == 'requirement_reviewer' else ('source_obligations', SourceObligation)
            if role == 'requirement_reviewer':
                item = create_model('SourceClaimReview', __base__=ClaimReview,
                                    logic_comparisons=(list[LogicComparison], Field(min_length=1)))
            record = create_model(role + 'SourceRecord', __base__=base, **{key: (list[item], Field(min_length=1))})
            return create_model(role + 'SourceSubmission', __base__=ModelStageOutput,
                                records=(list[record], Field(description='Complete source-grounded results')))
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
                                records=(list[legacy], Field(description='Explicit scoped results')))
        return stage_schema(role)
    from . import domain
    groups = create_model(role + 'RecordGroups', __base__=StrictModel, **{
        kind: (list[getattr(domain, kind.capitalize())], Field(description='Explicit typed results; [] if none'))
        for kind in sorted(ALLOWED_KINDS[role])})
    return create_model(role + 'Submission', __base__=StrictModel,
        records=(groups, Field(description='Separate homogeneous lists; never mix record fields')),
        gaps=(list[str], Field(description='Explicit unresolved facts; [] if none')),
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
                        records=(list[record], Field(description='Complete analytical results; task owns origin and clause_ids')),
                        gaps=(list[str], Field(description='Explicit unresolved facts; [] if none')),
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
                          assessment=(list[assessment],Field(description='Every exact scoped criterion')),
                          finding=(list[finding],Field(description='Explicit candidates; [] if none')))
    return create_model('ScopedCheckSubmission', __base__=StrictModel,
                        records=(groups,Field(description='Separate analytical result lists; task owns requirement links')),
                        gaps=(list[str],Field(description='Explicit unresolved facts; [] if none')),
                        summary=(str,Field(min_length=1)))


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
        schema = (scoped_checker_submission_model(owned_check) if owned_check else
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
                            'description':'0 = statement, 1..N = acceptance criteria in exact order; cover each index once'}
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
                        'description': 'Exact Qxxxxx paragraph ID from the current read_evidence result',
                        **({'enum': list(references.by_quote)} if references.by_quote else {'pattern': '^Q[0-9]{5}$'})}}
                    definition['required'] = ['quote_id']
                    continue
                if bundle.skill_id=='requirement_checker' and 'id' in fields:
                    fields['id']['pattern'] = '^'+task['id']+'_[a-zA-Z0-9_-]+$'
                    fields['id']['description'] = 'New result ID in the current task namespace; never reuse an upstream requirement ID'
                if owned_check:
                    fields.pop('requirement_id',None)
                    fields.pop('requirement_ids',None)
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
                    description='Submit the complete final StageOutput: records, gaps, summary. This ends analysis.',
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
            """List snapshot evidence IDs with bounded pagination; does not imply analysis completion."""
            rows = [
                row
                for row in self.store.evidence(run_id, source_type or None)
                if row["source_type"] in allowed_sources
            ]
            start = max(0, page - 1) * min(max(page_size, 1), 50)
            source_ids()
            return references.encode({"total": len(rows), "items": rows[start : start + min(max(page_size, 1), 50)]})

        def read_evidence(evidence_id: str) -> str:
            """Read one original evidence block belonging to this snapshot."""
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
            """Literal source search with total/has_more/next_offset; read hits separately. Not semantic coverage."""
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
            """Read document blocks together; return explicit remaining IDs at the byte limit."""
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
            """Query precomputed standard/control vectors; only this new query is embedded. Read matched controls before selecting."""
            from .standard_library import search
            from .embeddings import encoder
            prepared = (self.store.run(run_id)['snapshot'].get('standard') or {}).get('prepared')
            embedder = encoder(self.settings.embedding_model_dir) if prepared and query.strip() else None
            import asyncio
            from .retrieval_models import reranker
            ranker = reranker(self.settings.reranker_model_dir) if prepared and prepared.get('retrieval', {}).get('reranker_profile') else None
            return await asyncio.to_thread(search, self.store, run_id, query, section, limit, offset, embedder=embedder, reranker_model=ranker)

        def read_standard_control(control_id: str) -> dict:
            """Read an immutable bilingual control template and its stored original normative source."""
            from .control_catalog import read_control
            data, eid = read_control(self.store, run_id, task['id'], control_id)
            source_ids()
            return {'standard_control': data, 'source': read_evidence(eid)}

        def read_standard_clause(clause_id: str) -> dict:
            """Read original structured normative requirement, applicability notes, testing procedures, guidance and PDF source."""
            from .standard_library import read
            data, eid = read(self.store, run_id, clause_id)
            source_ids()
            return {'structured_clause': data, 'source': read_evidence(eid)}

        def read_standard_context(context_id: str) -> str:
            """Read an original standard scope or parent context page by its exact catalog ID."""
            from .standard_library import read
            _, eid = read(self.store, run_id, context_id, context=True)
            source_ids()
            return read_evidence(eid)

        def read_upstream_records(record_ids: list[str]) -> dict:
            """Read exact full upstream records from this task's inventory. These outputs are leads, not original source citations."""
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
            """Find symbols using the native Mantis structural index; absence is not proof of safety."""
            return await navigate("find_symbol", {"name": name, "offset": offset})

        async def find_callers(symbol: str, filepath: str = "", offset: int = 0) -> dict:
            """Use Mantis to locate indexed callers; dynamic calls can be missing."""
            return await navigate("find_callers", {"symbol": symbol, "filepath": filepath, "offset": offset})

        async def find_callees(symbol: str, filepath: str = "", offset: int = 0) -> dict:
            """Use Mantis to locate indexed callees; confirm relevant paths in source."""
            return await navigate("find_callees", {"symbol": symbol, "filepath": filepath, "offset": offset})

        async def get_function_boundary(filepath: str, line: int) -> dict:
            """Read a complete function through Mantis, with a usable source evidence ID."""
            return await navigate("get_function_boundary", {"filepath": filepath, "line": line})

        async def get_mantis_model() -> dict:
            """Read the native code summary and threat model for this run."""
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
        instruction += ('\nSource-reference transport: use the exact short source handles (S0001, S0002, etc.) '
                        'provided in this invocation for read tools and evidence_ids. These handles name original '
                        'sources through a fixed lookup table. Never invent, shorten, approximate or repair a handle. '
                        'The program maps valid handles to original source IDs without changing analysis content.')
        instruction += ('\nShort source handles are transport identifiers for tool arguments and citation fields only. '
                        'In titles, rationale, summaries and gaps, identify sources by their readable filename '
                        'and line/page location. Do not use short handles as narrative source names.')
        if owned_provenance:
            instruction += ('\nThis invocation uses task_owned_requirement_provenance_v1. '
                'The task owns origin and clause_ids, fixed to '+json.dumps(owned_provenance)+'. '
                'The model is not required to return these metadata fields; prefer omitting them. '
                'An exact echo is compatible, but a different origin or clause list is rejected and never overwritten. '
                'This transport rule overrides generic descriptions of the complete stored Requirement. '
                'Return every analytical field: title, module, statement, acceptance_criteria, rationale and actual '
                'source references, together with the declared record ID/kind. The program adds only the '
                'announced task provenance after validation; it never fills or rewrites analytical fields. '
                'Every requirement must cite the exact original target clause source actually read in this task.')
            context = {**context, 'task_owned_requirement_provenance':owned_provenance}
        if bundle.skill_id=='requirement_checker':
            instruction += ('\nThis invocation uses task_owned_check_provenance_v1. The task owns the '
                'requirement link fixed to '+owned_check+'. Omit assessment.requirement_id and '
                'finding.requirement_ids; the program supplies only this declared task association. '
                'Exact echoes are compatible, but any conflicting association is rejected, never replaced. '
                'This transport rule overrides generic descriptions of the stored records. All analytical '
                'fields, exact acceptance criteria, verdicts and source references must still be returned.')
            instruction += ('\nNew result IDs must start with '+task['id']+'_ followed by a unique '
                'alphanumeric suffix. Use different IDs for every assessment and finding. '
                'requirement_id is the existing requirement reference, never the new record id. '
                'The program rejects duplicate IDs and does not rename returned records.')
            instruction += ('\nImplementation source gate: until original code has actually been read in this task, '
                'the submission schema permits only UNKNOWN or NOT_CODE_VERIFIABLE. Use source navigation '
                'and reading tools to inspect implementation before claiming STATIC_SUPPORTED, PARTIAL or VIOLATED. '
                'Every such assessment must include an actual original code source handle in evidence_ids. '
                'Design documents, standards, upstream records and the Mantis model are not code evidence. '
                'The allowed status enum is rebound after actual code reads; a search hit alone does not unlock it.')
        if len(ALLOWED_KINDS[bundle.skill_id]) > 1:
            instruction += ('\nFinal transport: records is an object with separate lists named '
                            + ', '.join(sorted(ALLOWED_KINDS[bundle.skill_id]))
                            + '. Include every list explicitly, using [] when empty. '
                              'Each list contains only that record type and its declared fields. '
                              'Do not put fields from another record type in a record. '
                              'The program flattens these lists without changing their values.')
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
            context['upstream_record_mode'] = ('Compact indexes and exact applicability conditions. '
                'Use read_upstream_records for full original statements, rationale and missing scope facts. '
                'Only the original sources delivered in design_materials/target_materials or explicitly '
                'read through source tools can be cited; upstream records are analysis leads.')
            if task['scope'].get('control_id'):
                context['selected_control'] = read_standard_control(task['scope']['control_id'])
        context = {**context, 'required_document_reads': sorted(required_reads)}
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
