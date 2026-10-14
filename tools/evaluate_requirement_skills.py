"""Real-model, isolated comparison of repository skill revisions.

This runner uses the production executor and never rewrites model conclusions.
Source, gold criteria and scoring belong in the experiment directory, not prompts.
The gateway alone reads the credential; no experiment uses the production DB.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import replace
from pathlib import Path

from security_auditor.config import ROOT, Settings
from security_auditor.domain import Fact, Requirement, StageOutput
from security_auditor.ingestion import ingest_run
from security_auditor.runtime import Scheduler
from security_auditor.skills import SkillLoader, digest
from security_auditor.store import Store


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def git(*args):
    binary = os.getenv('PATHENA_GIT') or shutil.which('git')
    return subprocess.check_output([binary, *args], cwd=ROOT)


def prepare(directory: Path):
    plan = json.loads((directory / 'plan.json').read_text())
    assert digest((directory / 'plan.json').read_bytes()) == (directory / 'plan.sha256').read_text().strip()
    source = json.loads((directory / 'source.json').read_text())
    assert digest((directory / 'design.md').read_bytes()) == source['sha256']
    identity = {}
    for name, ref in zip(plan['versions'], [plan['baseline_commit'], plan['candidate_commit']], strict=True):
        packet = directory / ('skills-v' + name)
        folder = packet / 'skills'
        if not packet.exists():
            packet.mkdir()
            if ref == 'WORKTREE':
                shutil.copytree(ROOT / 'skills', folder)
            else:
                with tarfile.open(fileobj=io.BytesIO(git('archive', ref, 'skills'))) as files:
                    files.extractall(packet, filter='data')
        loader = SkillLoader(folder)
        items = []
        for role in ['design_analyst', 'requirement_generator', 'requirement_reviewer']:
            bundle = loader.resolve(role, role, name)
            expected = plan.get('expected_packet_hashes', {}).get(name, {}).get(role)
            if expected and bundle.content_hash != expected:
                raise ValueError('Candidate packet differs from the pinned reproduction fixture: ' + role)
            manifest = json.loads((folder / role / 'manifest.json').read_text())
            for relative in ['SKILL.md', 'manifest.json', *manifest.get('references', {})]:
                original = ((ROOT / 'skills' / role / relative).read_bytes() if ref == 'WORKTREE'
                            else git('show', ref + ':skills/' + role + '/' + relative))
                assert original == (folder / role / relative).read_bytes(), relative
            items.append({'role': role, 'packet_hash': bundle.content_hash})
        identity[name] = {'commit': git('rev-parse', 'HEAD' if ref == 'WORKTREE' else ref).decode().strip(),
                          'worktree_candidate': ref == 'WORKTREE', 'skills': items}
        spec = json.loads((ROOT / 'design/workflow.json').read_text())
        for node in spec['nodes']:
            if node['kind'] == 'agent':
                if node.get('role') in {'design_analyst', 'requirement_generator', 'requirement_reviewer'}:
                    node['skill_version'] = name
        write(directory / ('workflow-' + name + '.json'), spec)
    write(directory / 'skill-identities.json', identity)
    Store(directory / 'state/auditor.sqlite3', enforce_budgets=False)


def gateway(directory: Path, port: int):
    # The key never enters command arguments, the analysis runner or reports.
    from dotenv import dotenv_values
    import uvicorn
    values = dotenv_values(ROOT / '.env')
    if not values.get('DEEPSEEK_API_KEY'):
        raise RuntimeError('The configured gateway credential is missing')
    os.environ['DEEPSEEK_API_KEY'] = values['DEEPSEEK_API_KEY']
    os.environ['AUDITOR_MODEL_ID'] = json.loads((directory / 'plan.json').read_text())['model']
    os.environ['AUDITOR_GATEWAY_DATABASE'] = str(directory / 'state/auditor.sqlite3')
    os.environ['AUDITOR_ENFORCE_BUDGETS'] = 'false'
    os.environ['AUDITOR_PROVIDER_CONCURRENCY'] = '1'
    os.environ['AUDITOR_PROVIDER_ATTEMPTS'] = '3'
    os.environ.pop('AUDITOR_ANALYSIS_PROXY', None)
    uvicorn.run('security_auditor.gateway:app', host='127.0.0.1', port=port, log_level='warning')


def make_run(directory: Path, name: str, version: str, port: int):
    root = directory / ('skills-v' + version) / 'skills'
    settings = replace(Settings(), state_dir=directory / 'state', skills_dir=root,
                       workflow=directory / ('workflow-' + version + '.json'),
                       standard_pack='', repositories={}, language='zh-CN', model_id='deepseek-flash',
                       gateway=f'http://127.0.0.1:{port}', enforce_budgets=False,
                       agent_max_calls=0, concurrency=1)
    settings = replace(settings, agent_output_tokens=json.loads((directory / 'plan.json').read_text())['max_output_tokens'])
    store = Store(settings.database, enforce_budgets=False)
    prior = store.rows('SELECT id FROM projects WHERE name=?', (name,))
    if prior:
        run = store.rows('SELECT id FROM runs WHERE project_id=? ORDER BY created', (prior[0]['id'],))[0]
        return settings, store, store.run(run['id'])
    project = store.create_project(name)
    source = directory / 'design.md'
    snapshot = {'language': 'zh-CN', 'assets': [{'id': 'design', 'path': str(source),
                 'name': 'design.md', 'sha256': digest(source.read_bytes())}],
                'repository': None, 'standard': None, 'experiment': name,
                'skill_hashes': {r['id']: r['sha256'] for r in SkillLoader(root).catalog()}}
    run = store.create_run(project['id'], 'requirements_only', snapshot, False, 0, 0, physical=True)
    ingest_run(store, run['id'], settings)
    store.set_run_status(run['id'], 'RUNNING')
    return settings, store, store.run(run['id'])


def refs(store: Store, rid: str, lines: list[int]):
    return [e['id'] for e in store.evidence(rid, 'document')
            if any(json.loads(e['metadata'])['line_start'] <= number
                   < json.loads(e['metadata'])['line_start'] + 100 for number in lines)]


def seed_facts(store: Store, rid: str, directory: Path):
    if store.records(rid, 'fact'):
        return
    plan = json.loads((directory / 'plan.json').read_text())
    task = store.add_task(rid, 'fixed_design_input', {}, wave='experiment_input')
    store.start_task(task)
    items = []
    for i, row in enumerate(plan['positives']):
        evidence = refs(store, rid, row['source_lines'])
        for eid in evidence:
            store.note_read(task, eid)
        items.append(Fact(id=rid + f'_fixed_{i}', title=row['behavior'], module='ServiceAccount token lifecycle',
                          fact_type='CONTROL', basis='DECLARED', evidence_ids=evidence,
                          rationale='固定的原文设计事实：' + '；'.join(row['full_credit'])))
    store.commit_output(task, 'design_analyst', StageOutput(records=items, summary='Shared fixed design input'))


REVIEW_CASES = [
    ('keep_manual', '保留显式请求的令牌', '自动清理只能处理自动生成的令牌；显式请求的 Secret 令牌不纳入清理。',
     ['显式请求的 Secret 令牌不被自动清理删除。'], [85, 87, 159, 160], 'SUPPORTED'),
    ('clean_manual', '统一清理所有 Secret 令牌', '自动清理必须同时清理手动创建和自动生成的 Secret 令牌。',
     ['即使是显式请求的 Secret 令牌，也应在闲置周期后自动删除。'], [85, 87, 159, 160], 'REJECTED'),
    ('force_year', '清理周期固定且不可修改', '清理周期必须固定为一年，集群管理员不得配置其他值。',
     ['所有集群强制使用一年周期，拒绝任何周期配置修改。'], [125, 129, 162, 164], 'REJECTED'),
    ('instant_delete', '失效即删除', '令牌标记失效后必须立即删除，不保留重新激活窗口。',
     ['出现 invalid-since 标签即立即删除 Secret。'], [125, 129, 173, 183], 'REJECTED'),
    ('tracking_gate', '跟踪条件保护清理', '跟踪 ConfigMap 不可用时不得删除 Secret。',
     ['跟踪 ConfigMap 不存在或不可用时，不进行 Secret 删除。'], [171], 'SUPPORTED'),
    ('coverage', '固定单测比例作为安全控制', '所有模块必须达到 67.5% 单元测试覆盖率才能允许令牌验证。',
     ['覆盖率低于 67.5% 时禁止所有令牌认证。'], [194, 195], 'REJECTED'),
]


def seed_reviews(store: Store, rid: str, cases=REVIEW_CASES):
    if store.records(rid, 'requirement'):
        return
    task = store.add_task(rid, 'fixed_review_input', {}, wave='experiment_input')
    store.start_task(task)
    records = []
    for case in cases:
        slug, title, statement, criteria, lines, _ = case[:6]
        evidence = refs(store, rid, lines)
        for eid in evidence:
            store.note_read(task, eid)
        records.append(Requirement(id=rid + '_' + slug, title=title, module='ServiceAccount token lifecycle',
            statement=statement, acceptance_criteria=criteria, origin=case[6] if len(case) > 6 else 'EXPLICIT_DESIGN', evidence_ids=evidence,
            rationale=case[7] if len(case) > 7 else '待独立复核的设计要求，依据为所附设计原文。'))
    store.commit_output(task, 'requirement_generator', StageOutput(records=records, summary='Shared review input'))


async def stage(scheduler: Scheduler, rid: str, role: str, scope: dict):
    node = next(n for n in scheduler.spec['nodes'] if n.get('role') == role)
    await scheduler.child(rid, node, None, scope)


async def experiment(directory: Path, port: int):
    prepare(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    tasks = plan.get('tasks') or [('pipeline', 1, '0.2.0'), ('pipeline', 1, '0.3.0'),
             ('pipeline', 2, '0.3.0'), ('pipeline', 2, '0.2.0'),
             ('isolated_generator', 1, '0.2.0'), ('isolated_generator', 1, '0.3.0'),
             ('review', 1, '0.3.0'), ('review', 1, '0.2.0')]
    for kind, repeat, version in tasks:
        name = f'{kind}-r{repeat}-v{version}'
        settings, store, run = make_run(directory, name, version, port)
        output_path = directory / 'outputs' / (name + '.json')
        if output_path.exists():
            continue  # Never selectively retry a judged/failed response.
        scheduler = Scheduler(settings, store)
        started = time.monotonic()
        print(json.dumps({'event': 'start', 'case': name, 'run_id': run['id']}), flush=True)
        failures = []
        try:
            if kind == 'pipeline':
                for scope in scheduler.scopes(run['id'], 'design_analyst'):
                    await stage(scheduler, run['id'], 'design_analyst', scope)
                await stage(scheduler, run['id'], 'requirement_generator', {'scope': 'system_and_cross_module_relationships'})
            elif kind == 'isolated_generator':
                seed_facts(store, run['id'], directory)
                await stage(scheduler, run['id'], 'requirement_generator', {'scope': 'system_and_cross_module_relationships'})
            else:
                cases = plan.get('review_cases') or REVIEW_CASES
                seed_reviews(store, run['id'], cases)
                for slug, *_ in cases:
                    try:
                        await stage(scheduler, run['id'], 'requirement_reviewer', {'subject_ids': [run['id'] + '_' + slug]})
                    except Exception as exc:
                        failures.append({'subject': slug, 'type': type(exc).__name__, 'message': str(exc)})
            store.set_run_status(run['id'], 'FAILED' if failures else 'COMPLETED')
        except Exception as exc:
            failures.append({'type': type(exc).__name__, 'message': str(exc)})
            store.set_run_status(run['id'], 'FAILED', str(exc))
        tasks_output = store.rows('SELECT id,stage,status,skill_id,skill_version,skill_hash,instruction_hash,error '
                                 'FROM tasks WHERE run_id=? ORDER BY created,id', (run['id'],))
        provider = store.rows('SELECT id,status,category,actual_tokens,response FROM provider_requests WHERE run_id=? '
                              'ORDER BY created', (run['id'],))
        for row in provider:
            value = json.loads(row.pop('response') or '{}')
            row['usage'] = value.get('usage')
            row['provider_model'] = value.get('model')
        result = {'case': name, 'kind': kind, 'repeat': repeat, 'version': version, 'run_id': run['id'],
                  'elapsed_seconds': round(time.monotonic() - started, 2), 'status': store.run(run['id'])['status'],
                  'failures': failures, 'records': store.records(run['id']), 'tasks': tasks_output,
                  'usage': store.run(run['id'])['usage'], 'provider_requests': provider,
                  'material_blocks': len(store.evidence(run['id'], 'document')),
                  'source_reads': store.rows('SELECT t.stage,r.evidence_id FROM task_reads r JOIN tasks t ON t.id=r.task_id '
                                            'WHERE t.run_id=?', (run['id'],))}
        write(output_path, result)
        print(json.dumps({'event': 'finish', 'case': name, 'status': result['status'], 'failures': failures,
                          'records': len(result['records']), 'usage': result['usage']}, ensure_ascii=False), flush=True)


async def generated_reviews(directory: Path, port: int):
    """Secondary exploratory extension, recorded separately from frozen primary outputs."""
    plan = json.loads((directory / 'plan.json').read_text())
    for version in plan.get('generated_review_versions', plan['versions']):
        name = 'pipeline-r1-v' + version
        target = directory / 'outputs' / ('generated-reviews-v' + version + '.json')
        if target.exists():
            continue
        original_path = directory / 'outputs' / (name + '.json')
        original = json.loads(original_path.read_text())
        assert original['status'] == 'COMPLETED'
        settings, store, run = make_run(directory, name, version, port)
        prior_provider_ids = {r['id'] for r in store.rows('SELECT id FROM provider_requests WHERE run_id=?', (run['id'],))}
        store.event(run['id'], 'experimental_generated_review_extension', {
            'primary_output_sha256': digest(original_path.read_bytes()),
            'policy': 'First chronological pair, every generated requirement, no modified or regenerated drafts'})
        store.set_run_status(run['id'], 'RUNNING')
        scheduler = Scheduler(settings, store)
        failures = []
        started = time.monotonic()
        requirements = [r for r in original['records'] if r['kind'] == 'requirement']
        for index, requirement in enumerate(requirements):
            try:
                await stage(scheduler, run['id'], 'requirement_reviewer', {'subject_ids': [requirement['id']]})
            except Exception as exc:
                failures.append({'subject': requirement['id'], 'type': type(exc).__name__, 'message': str(exc)})
            print(json.dumps({'event': 'generated_review', 'version': version,
                              'done': index + 1, 'total': len(requirements)}, ensure_ascii=False), flush=True)
        store.set_run_status(run['id'], 'FAILED' if failures else 'COMPLETED')
        provider = store.rows('SELECT id,status,actual_tokens,response FROM provider_requests WHERE run_id=?', (run['id'],))
        provider = [r for r in provider if r['id'] not in prior_provider_ids]
        for row in provider:
            value = json.loads(row.pop('response') or '{}')
            row['usage'] = value.get('usage')
            row['provider_model'] = value.get('model')
        write(target, {'version': version, 'run_id': run['id'], 'type': 'exploratory_secondary',
                       'original_output_sha256': digest(original_path.read_bytes()),
                       'requirements': requirements, 'reviews': store.records(run['id'], 'review'),
                       'failures': failures, 'provider_requests': provider,
                       'requests': len(provider), 'tokens': sum(r['actual_tokens'] or 0 for r in provider),
                       'elapsed_seconds': round(time.monotonic() - started, 2)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'gateway', 'run', 'generated-reviews'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18881)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == 'prepare':
        prepare(directory)
    elif args.action == 'gateway':
        gateway(directory, args.port)
    elif args.action == 'run':
        asyncio.run(experiment(directory, args.port))
    else:
        asyncio.run(generated_reviews(directory, args.port))


if __name__ == '__main__':
    main()
