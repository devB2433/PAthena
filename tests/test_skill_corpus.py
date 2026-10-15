"""Regression gates for the user's canonical-skill and literal-port requirements."""
import json
import shutil

import pytest

from security_auditor.config import ROOT
from security_auditor.native_skills import NativeSkillLoader, STAGE_SKILLS, SUPPLEMENTAL_FILES
from security_auditor.mantis_worker import ENGINE, native_path, phase_spec
from security_auditor.skills import PROJECT_ROLES, SkillLoader, compile_instruction, digest
from security_auditor.source_search import SnapshotSourceSearch


def test_complete_upstream_corpus_is_in_repository_and_not_shortened():
    loader = NativeSkillLoader(ROOT / 'skills')
    assert len([n for n in loader.manifest['files'] if n.endswith('/SKILL.md')]) == 21
    upstream = json.loads((ENGINE / 'SOURCE.json').read_text())
    for name, declared in loader.manifest['files'].items():
        content = (ROOT / 'skills/code' / name).read_bytes()
        assert digest(content) == declared['sha256']
        if name in SUPPLEMENTAL_FILES:
            assert declared == SUPPLEMENTAL_FILES[name]
        else:
            assert content == (ENGINE / 'skills' / name).read_bytes()
            assert not upstream['files']['skills/' + name]['modified']


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_runtime_and_evaluation_use_exact_same_canonical_skill_packets(language, tmp_path):
    native_path()
    loader = NativeSkillLoader(ROOT / 'skills')
    stages = {}
    for phase in ['model', 'audit']:
        spec = phase_spec(phase, 'http://gateway:8081', 'deepseek-flash', tmp_path / 'audit.db',
                          language, ROOT / 'skills')
        stages.update({node['id']: node for node in spec['nodes'] if node['type'] == 'agent'})
    assert stages.keys() == STAGE_SKILLS.keys()
    for stage, node in stages.items():
        bundle, prompt, _ = loader.compile(stage, language)
        assert node['system_prompt'] == prompt
        assert (ROOT / 'skills/code' / bundle.skill_id / 'SKILL.md').read_text() in prompt
        assert 'assign objective CVSS' not in prompt
        assert 'environment lacks prerequisites' not in prompt
    calibration = stages['calibrator']['system_prompt']
    assert (ROOT / 'skills/code/mantis-calibrate/references/calibration_rules.md').read_text() in calibration


def test_missing_or_corrupted_native_skill_never_falls_back_to_vendor_or_summary(tmp_path):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    folder = tmp_path / 'skills/code/mantis-researcher'
    (folder / 'SKILL.md').write_text('Short replacement')
    with pytest.raises(ValueError, match='摘要'):
        NativeSkillLoader(tmp_path / 'skills').resolve('researcher')
    (folder / 'SKILL.md').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        NativeSkillLoader(tmp_path / 'skills').resolve('researcher')


def test_unregistered_graph_stage_cannot_use_default_compressed_prompt(tmp_path, monkeypatch):
    from security_auditor import mantis_worker
    graph = json.loads((ENGINE / 'workflow.json').read_text())
    graph['nodes'].append({'id': 'new_auditor', 'type': 'agent', 'tools': []})
    (tmp_path / 'workflow.json').write_text(json.dumps(graph))
    monkeypatch.setattr(mantis_worker, 'ENGINE', tmp_path)
    with pytest.raises(ValueError, match='缺少仓库技能绑定'):
        mantis_worker.phase_spec('audit', 'http://gateway', 'deepseek-flash', tmp_path / 'audit.db')


def test_application_skills_are_versioned_complete_methods_and_legacy_audit_roles_are_rejected():
    loader = SkillLoader(ROOT / 'skills')
    assert {r['id'] for r in loader.catalog()} == set(PROJECT_ROLES)
    legacy = json.loads((ROOT / 'skills/pci_requirement_generator/manifest.json').read_text())
    for row in [*loader.catalog(), legacy]:
        assert row['version'] == '0.6.0'
        bundle = loader.resolve(row['id'], row['id'], row['version'])
        prompt, _ = compile_instruction(bundle)
        assert bundle.instruction in prompt and '[METHOD]' in bundle.instruction
        manifest = json.loads((ROOT / 'skills' / row['id'] / 'manifest.json').read_text())
        for reference in manifest['references']:
            assert (ROOT / 'skills' / row['id'] / reference).read_text() in prompt
        for reference in manifest.get('shared_references', {}):
            assert (ROOT / 'skills/_shared' / reference).read_text() in prompt
    for role in ['code_architect', 'threat_modeler', 'vulnerability_planner', 'vulnerability_researcher',
                 'finding_reviewer', 'finding_critic', 'risk_calibrator']:
        assert not (ROOT / 'skills' / role).exists()
        with pytest.raises(ValueError):
            loader.resolve(role, role, '0.6.0')


@pytest.mark.parametrize('missing', [False, True])
def test_application_reference_is_mandatory_and_cannot_be_shortened(tmp_path, missing):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    cases = tmp_path / 'skills/requirement_checker/references/cases.md'
    if missing:
        cases.unlink()
    else:
        cases.write_text('Summary instead of the original role cases')
    with pytest.raises((ValueError, FileNotFoundError)):
        SkillLoader(tmp_path / 'skills').resolve('requirement_checker', 'requirement_checker', '0.6.0')


@pytest.mark.parametrize('missing', [False, True])
def test_shared_security_method_integrity_is_enforced_for_every_application_role(tmp_path, missing):
    shutil.copytree(ROOT / 'skills', tmp_path / 'skills')
    shared = tmp_path / 'skills/_shared/security-requirements.md'
    if missing:
        shared.unlink()
    else:
        shared.write_text('Shortened test-only knowledge is forbidden')
    for manifest_path in (tmp_path / 'skills').glob('*/manifest.json'):
        row = json.loads(manifest_path.read_text())
        with pytest.raises((ValueError, FileNotFoundError)):
            SkillLoader(tmp_path / 'skills').resolve(row['id'], row['id'], row['version'])


def test_static_grep_bridge_preserves_full_call_site_inventory_and_snapshot_bounds(tmp_path):
    root = tmp_path / 'snapshot'
    root.mkdir()
    for name, body in [('a.py', 'sink(user)\nsink(other)\n'), ('b.py', 'sink(tenant)\n')]:
        (root / name).write_text(body)
    inventory = [{'path': p.name, 'status': 'READABLE', 'sha256': digest(p.read_bytes())}
                 for p in sorted(root.iterdir())]
    inventory.append({'path': 'large.py', 'status': 'OVERSIZED'})
    search = SnapshotSourceSearch(root, inventory)
    first = search.search('sink', limit=2)
    second = search.search('sink', offset=first['next_offset'], limit=2)
    assert first['has_more'] and not second['has_more']
    assert [(r['path'], r['line']) for r in first['items'] + second['items']] == [('a.py', 1), ('a.py', 2), ('b.py', 1)]
    assert first['read_required'] and first['gaps'] == [{'path': 'large.py', 'status': 'OVERSIZED'}]
    assert search.search('sink', directory='./')['total_matches'] == 3
    assert search.search('sink', directory='.')['total_matches'] == 3
    with pytest.raises(ValueError):
        search.search('sink', directory='../')
    (root / 'a.py').write_text('modified')
    with pytest.raises(ValueError, match='摘要'):
        search.search('sink')
