import json

import pytest

from security_auditor.api import create_app, LanguageRequest, RunRequest
from security_auditor.config import ROOT
from security_auditor.localization import output_policy, policy_hashes
from security_auditor.mantis_worker import native_path, phase_spec
from security_auditor.product_report import product_report
from security_auditor.skills import SkillLoader, compile_instruction
from security_auditor.store import Store


def route(app, path, method):
    return next(r.endpoint for r in app.routes if r.path.endswith(path) and method in r.methods)


def test_setting_persists_and_runs_pin_language_across_changes(fixture_settings):
    app = create_app(fixture_settings)
    store = app.state.store
    project = store.create_project('language fixture')
    settings = route(app, '/settings', 'GET')
    change = route(app, '/settings', 'PUT')
    start = route(app, '/{project_id}/runs', 'POST')
    assert settings()['language'] == 'en'
    first = start(project['id'], RunRequest(mode='code_only', repository_id='fixture'), None)
    assert first['language'] == first['snapshot']['language'] == 'en'
    change(LanguageRequest(language='zh-CN'))
    assert Store(store.path).language() == 'zh-CN'
    second = start(project['id'], RunRequest(mode='code_only', repository_id='fixture'), None)
    assert second['language'] == 'zh-CN'
    assert second['snapshot']['language_policy_hashes'] == policy_hashes()
    store.set_run_status(first['id'], 'PAUSED')
    resumed = route(app, '/resume', 'POST')(project['id'], first['id'])
    assert resumed['language'] == 'en' and resumed['usage']['requests'] == 0
    assert second['usage']['requests'] == 0
    with pytest.raises(ValueError):
        change(LanguageRequest(language='de'))


@pytest.mark.parametrize('selected', ['en', 'zh-CN'])
def test_all_product_roles_receive_complete_language_skill(selected):
    loader = SkillLoader(ROOT / 'skills')
    for row in loader.catalog():
        bundle = loader.resolve(row['id'], row['id'], row['version'])
        prompt, fingerprint = compile_instruction(bundle, language=selected)
        other = compile_instruction(bundle, language='zh-CN' if selected == 'en' else 'en')[1]
        assert bundle.instruction in prompt and output_policy(selected) in prompt
        assert fingerprint != other
        assert all(f'[{rule}]' in prompt for rule in bundle.rule_ids)
        assert 'StageOutput' in prompt and 'evidence_ids' in prompt


@pytest.mark.parametrize('selected', ['en', 'zh-CN'])
def test_every_native_agent_in_both_phases_receives_language_policy(selected, tmp_path):
    native_path()
    stages = set()
    for phase in ['model', 'audit']:
        spec = phase_spec(phase, 'http://gateway', 'deepseek-flash', tmp_path / 'native.db', selected)
        for node in spec['nodes']:
            assert node['id'] not in {'reproducer', 'patcher'}
            if node['type'] == 'agent':
                stages.add(node['id'])
                assert output_policy(selected) in node['system_prompt']
                if node['id'] != 'structural_index':
                    assert f"MANTIS_STAGE:{node['id']}" in node['system_prompt']
    assert {'architect', 'threat_modeler', 'researcher', 'reviewer', 'critic', 'calibrator', 'reporter'} <= stages


@pytest.mark.asyncio
async def test_adk_generates_chinese_directly_without_translation_call(settings, store):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from security_auditor.agents import AdkExecutor
    from security_auditor.domain import StageOutput
    from pydantic import PrivateAttr
    class Model(BaseLlm):
        model: str = 'language-fixture'
        _calls: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            assert output_policy('zh-CN') in str(llm_request.config.system_instruction)
            output = StageOutput(records=[], summary='中文分析结论', gaps=['缺少输入材料'])
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text=output.model_dump_json())]))
    p = store.create_project('fixture')
    run = store.create_run(p['id'], 'full', {'language': 'zh-CN'}, False, 10, 100000)
    tid = store.add_task(run['id'], 'requirements', {})
    bundle = SkillLoader(settings.skills_dir).resolve('requirement_generator', 'requirement_generator', '0.1.0')
    model = Model()
    output = await AdkExecutor(settings, store, model_factory=lambda: model).execute(store.task(tid), bundle, {})
    assert output.summary == '中文分析结论' and model._calls == 1
    assert json.loads(store.rows('SELECT content FROM model_outputs')[0]['content'])['summary'] == '中文分析结论'


@pytest.mark.parametrize('selected,title', [('en', 'Security requirements'), ('zh-CN', '安全需求')])
def test_report_localizes_labels_without_rewriting_submitted_content(selected, title):
    data = {'run': {'language': selected}, 'records': [], 'model_artifacts': [
        {'artifact_type': 'threat_model', 'data': {'key_risks': ['submitted原文']}}]}
    html = product_report('project', data, {'requirements': [], 'findings': []}, [])
    assert f'lang="{selected}"' in html and title in html
    assert 'submitted原文' in html


def test_v1_upgrade_only_adds_settings_and_preserves_existing_artifacts(store, sample_run):
    _, run, _ = sample_run
    tid = store.add_task(run['id'], 'mantis_threat_modeler', {})
    store.save_model_submission(tid, 'threat_modeler', 'record_threat_model', 'fixture', {'key_risks': ['unchanged']})
    before = store.rows('SELECT * FROM stage_artifacts')
    with store.connect() as db:
        db.execute('DROP TABLE system_settings')
        db.execute('DELETE FROM schema_migrations WHERE version>=2')
    upgraded = Store(store.path)
    assert upgraded.language() == 'en'
    assert upgraded.rows('SELECT * FROM stage_artifacts') == before
    assert upgraded.rows('SELECT max(version) v FROM schema_migrations')[0]['v'] == 5


def test_ui_catalog_covers_all_fixed_localized_strings():
    import re
    catalog = json.loads((ROOT / 'src/security_auditor/resources/messages.json').read_text())
    source = (ROOT / 'apps/web/src/App.tsx').read_text()
    for label in re.findall(r"\bt\('([^']+)'\)", source):
        assert label in catalog, label
    # The browser receives model-produced strings directly.
    assert 'translate(row.' not in source and 'translate(detail.row.' not in source
