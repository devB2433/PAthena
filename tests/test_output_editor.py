from dataclasses import replace
import json
import shutil

import httpx
import pytest

from security_auditor.domain import Requirement, Fact, StageOutput, SourceObligation, SourceQuote
from security_auditor.output_editor import OutputEditor, editor_skill, slots_for, apply_edits
from security_auditor.provider import OutputContractError, ProviderUnavailable
from security_auditor.runtime import Scheduler


def output():
    return StageOutput(summary='以下是需求分析结果。', records=[Requirement(id='req-1',
        title='租户校验要求', module='orders', statement='订单查询必须校验租户。',
        acceptance_criteria=['跨租户查询必须拒绝。'], origin='EXPLICIT_DESIGN',
        rationale='以下是你需要知道的：接口可能读取其他租户的 10 条记录，代码位于 `orders.py:12`。',
        evidence_ids=['source-1'])])


def test_only_explanatory_chinese_fields_can_change():
    original = output()
    slots = slots_for(original)
    assert {s['field'] for s in slots.values()} == {'title', 'summary', 'rationale'}
    texts = {k:s['text'].replace('以下是你需要知道的：', '') for k,s in slots.items()}
    edited = apply_edits(original, slots, texts)
    assert edited.records[0].rationale == '接口可能读取其他租户的 10 条记录，代码位于 `orders.py:12`。'
    before, after = original.model_dump(), edited.model_dump()
    for r in (before, after):
        r['summary'] = ''
        for item in r['records']:
            for key in ('title','rationale'):
                item[key] = ''
    assert before == after
    assert original.records[0].rationale.startswith('以下')


@pytest.mark.parametrize('change', ['number', 'certainty', 'code', 'citation', 'missing', 'extra'])
def test_editor_rejects_changed_protected_values_or_slot_inventory(change):
    original = output()
    slots = slots_for(original)
    texts = {k:s['text'] for k,s in slots.items()}
    key = next(k for k,s in slots.items() if s['field']=='rationale')
    if change == 'missing':
        texts.pop(key)
    elif change == 'extra':
        texts['implementation_status'] = 'STATIC_SUPPORTED'
    else:
        texts[key] = texts[key].replace({'number':'10','certainty':'可能','code':'orders.py:12','citation':'记录'}[change],
                                      {'number':'100','certainty':'确认','code':'orders.py:13','citation':'记录“原文”'}[change])
    with pytest.raises(OutputContractError):
        apply_edits(original, slots, texts)


def test_full_upstream_skill_is_required_and_byte_verified(settings, tmp_path):
    bundle = editor_skill(settings.skills_dir)
    assert '31' in bundle.instruction and '保留信息和确定程度' in bundle.instruction
    target = tmp_path/'skills'
    shutil.copytree(settings.skills_dir/'chinese_output_editor', target/'chinese_output_editor')
    (target/'chinese_output_editor/vendor/humanizer-zh/SKILL.md').write_text('简略规则')
    with pytest.raises(ValueError, match='摘要'):
        editor_skill(target)


def response(group):
    texts = {k: v['text'].replace('以下是你需要知道的：', '') for k,v in group.items()}
    return httpx.Response(200, json={'usage':{'total_tokens':100}, 'choices':[{'finish_reason':'tool_calls',
        'message':{'tool_calls':[{'function':{'name':'submit_chinese_edits',
                                          'arguments':json.dumps({'texts':texts},ensure_ascii=False)}}]}}]})


async def test_english_skips_editor_and_chinese_results_are_idempotent(settings, store):
    settings=replace(settings,humanize_chinese=True)
    calls=[]
    def upstream(request):
        body=json.loads(request.content)
        calls.append(body)
        assert '保留信息和确定程度' in body['messages'][0]['content']
        assert request.headers['x-auditor-request']
        return response(json.loads(body['messages'][1]['content']))
    editor=OutputEditor(settings,store,httpx.MockTransport(upstream))
    project=store.create_project('editor')
    for language in ['en','zh-CN']:
        run=store.create_run(project['id'],'requirements_only',{'language':language},False,100,200000)
        task=store.add_task(run['id'],'requirements',{})
        result=await editor.edit(task,'requirement_generator',output())
        if language=='en':
            assert result==output() and not calls
        else:
            assert result.records[0].rationale!=output().records[0].rationale
            assert await editor.edit(task,'requirement_generator',output())==result
    assert len(calls)==1
    assert store.rows("SELECT status FROM tasks WHERE stage='chinese_output_polish'")[0]['status']=='SUCCEEDED'


async def test_failed_edit_resumes_draft_without_rerunning_analysis(settings, store):
    settings=replace(settings,humanize_chinese=True)
    project=store.create_project('draft-resume')
    run=store.create_run(project['id'],'requirements_only',{'language':'zh-CN'},False,100,200000)
    eid=store.add_evidence(run['id'],'document','design:1','接口需要验证租户。',{})
    class Executor:
        calls=0
        async def execute(self,task,bundle,context):
            self.calls+=1
            store.note_read(task['id'],eid)
            return StageOutput(summary='设计分析已完成。',records=[Fact(id='fact-1',title='租户校验',
                module='orders',fact_type='CONTROL',basis='DECLARED',evidence_ids=[eid],
                rationale='以下是你需要知道的：接口需要验证租户。',source_obligations=[SourceObligation(
                    claim='接口需要验证租户。',quotes=[SourceQuote(evidence_id=eid,text='接口需要验证租户。')],
                    subject='接口',action='验证租户',modality='REQUIRED',conditions=[],quantifier='UNSPECIFIED',
                    trigger='',time_anchor='',exceptions=[])])])
    calls=[]
    def upstream(request):
        calls.append(request)
        if len(calls)==1:
            raise httpx.ConnectError('fixture offline')
        return response(json.loads(json.loads(request.content)['messages'][1]['content']))
    editor=OutputEditor(settings,store,httpx.MockTransport(upstream))
    executor=Executor()
    scheduler=Scheduler(settings,store,executor=executor,output_editor=editor)
    parent=store.add_task(run['id'],'design_model',{},wave='stage')
    node={'id':'design_model','role':'design_analyst','skill_id':'design_analyst','skill_version':'0.6.0'}
    scope={'evidence_ids':[eid]}
    with pytest.raises(ProviderUnavailable):
        await scheduler.child(run['id'],node,parent,scope)
    assert executor.calls==1 and not store.records(run['id'])
    assert store.rows('SELECT task_id FROM analysis_drafts')
    await scheduler.child(run['id'],node,parent,scope)
    assert executor.calls==1 and len(calls)==2
    record=store.records(run['id'])[0]
    assert record['rationale']=='接口需要验证租户。'
    assert record['source_obligations'][0]['quotes'][0]['text']=='接口需要验证租户。'
    assert store.rows("SELECT status FROM tasks WHERE stage='chinese_output_polish'")[0]['status']=='SUCCEEDED'


async def test_native_phase_reuses_complete_draft_after_edit_failure(settings, store, tmp_path, monkeypatch):
    from security_auditor.mantis_engine import MantisEngine
    settings=replace(settings,humanize_chinese=True)
    engine=MantisEngine(settings,store)
    project=store.create_project('native-editor')
    run=store.create_run(project['id'],'full',{'language':'zh-CN','mantis_hash':engine.fingerprint,
        'repository':{'root':str(tmp_path),'files':[]}},False,100,200000)
    eid=store.add_evidence(run['id'],'code','orders.py:1','def orders(): pass',{'path':'orders.py','line_start':1,'line_end':1})
    invokes=[]
    async def invoke(*args):
        invokes.append(args)
        return {'phase':'model'}
    def mapping(run_id,task_id,result):
        store.note_read(task_id,eid)
        return StageOutput(summary='代码建模已完成。',records=[Fact(id='native-fact',title='订单入口',module='orders',
            fact_type='ENTRYPOINT',basis='OBSERVED',engine='mantis',evidence_ids=[eid],
            rationale='以下是你需要知道的：代码包含订单入口。')])
    monkeypatch.setattr(engine,'invoke',invoke)
    monkeypatch.setattr(engine,'map_output',mapping)
    calls=[]
    def upstream(request):
        calls.append(request)
        if len(calls)==1:
            raise httpx.ConnectError('fixture transport')
        return response(json.loads(json.loads(request.content)['messages'][1]['content']))
    engine.output_editor=OutputEditor(settings,store,httpx.MockTransport(upstream))
    parent=store.add_task(run['id'],'threat_model',{},wave='stage')
    with pytest.raises(ProviderUnavailable):
        await engine.execute(run['id'],'model',parent)
    assert len(invokes)==1 and not store.records(run['id'])
    await engine.execute(run['id'],'model',parent)
    assert len(invokes)==1 and len(calls)==2
    assert store.records(run['id'])[0]['rationale']=='代码包含订单入口。'


def test_literal_line_breaks_are_formatting_but_code_and_quotes_stay_exact():
    from security_auditor.output_editor import protected_fragments
    before='说明\\n1. 可能读取 10 条记录。\\n2. 路径 `src/api.py:12`，引文“未实现\\n检查”。'
    after='说明\n1. 可能读取 10 条记录。\n2. 路径 `src/api.py:12`，引文“未实现\\n检查”。'
    assert protected_fragments(before)==protected_fragments(after)
    assert protected_fragments(after)!=protected_fragments(after.replace('未实现\\n检查','未实现\n检查'))
