"""Freeze only the implementation target inventory; other analyses stay independent."""
from __future__ import annotations

import json

from .domain import compliance_candidate
from .scope_policy import POLICY, fingerprint, validate_frozen


def saved_resolution(store, run_id: str) -> dict | None:
    rows = store.rows('SELECT payload,sha256 FROM scope_resolutions WHERE run_id=?', (run_id,))
    if not rows:
        return None
    payload = json.loads(rows[0]['payload'])
    if fingerprint(payload) != rows[0]['sha256']:
        raise ValueError('需求检查范围解析摘要不一致')
    return payload


def resolve(store, run_id: str) -> dict | None:
    run = store.run(run_id)
    frozen = run['snapshot'].get('requirement_check_scope')
    if not frozen:
        return None
    validate_frozen(store, frozen)
    if run['mode'] not in {'full', 'implementation_only'}:
        raise ValueError('当前运行模式不支持需求实现检查范围')
    records = store.records(run_id)
    requirements = {r['id']: r for r in records if r['kind'] == 'requirement'}
    mappings = [r for r in records if r['kind'] == 'applicability']
    inventory, selected = [], set()
    for cid in frozen['control_ids']:
        bound = [r for r in requirements.values() if r.get('standard_control_id') == cid
                 and r.get('standard_catalog_id') == frozen['catalog_id']]
        related = set(r['id'] for r in bound)
        related.update(rid for r in bound for rid in r.get('matched_requirement_ids', []) if rid in requirements)
        decisions = [m for m in mappings if cid in m.get('control_ids', [])]
        related.update(rid for m in decisions if compliance_candidate(m)
                       for rid in m.get('requirement_ids', []) if rid in requirements)
        if related:
            state = 'RELATED'
        elif decisions and all(m['relevance'] == 'UNRELATED' or m['status'] == 'NOT_APPLICABLE'
                               or m.get('control_scope') == 'NON_CODE' for m in decisions):
            state = 'EXCLUDED'
        else:
            state = 'BASELINE_MISSING' if run['mode'] == 'implementation_only' and not decisions else 'UNDETERMINED'
        selected.update(related)
        inventory.append({'control_id': cid, 'state': state, 'requirement_ids': sorted(related),
                          'mapping_ids': sorted(m['id'] for m in decisions),
                          'reasons': [m['rationale'] for m in decisions],
                          'missing_facts': sorted({f for m in decisions for f in m.get('missing_facts', [])})})
    targets = {rid: {'acceptance_criteria': requirements[rid]['acceptance_criteria'],
                     'requirement_hash': fingerprint(requirements[rid])} for rid in sorted(selected)}
    data = {'policy': POLICY, 'preview_hash': frozen['preview_hash'], 'catalog_id': frozen['catalog_id'],
            'inventory': inventory, 'target_requirements': targets,
            'not_selected_requirement_ids': sorted(set(requirements) - selected),
            'dependency_gaps': frozen['dependency_gaps'], 'dependency_limitations': frozen['dependency_limitations'],
            'counts': {'candidate_controls': len(inventory), 'selected_requirements': len(targets),
                       'not_selected_requirements': len(requirements) - len(targets),
                       'acceptance_criteria': sum(len(t['acceptance_criteria']) for t in targets.values()),
                       **{state.lower(): sum(i['state'] == state for i in inventory)
                          for state in ('RELATED', 'EXCLUDED', 'UNDETERMINED', 'BASELINE_MISSING')}}}
    data['resolution_hash'] = fingerprint(data)
    old = saved_resolution(store, run_id)
    if old and old != data:
        raise ValueError('需求检查库存与已冻结解析不一致')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO scope_resolutions VALUES(?,?,?)',
                   (run_id, json.dumps(data, sort_keys=True, ensure_ascii=False), fingerprint(data)))
        for item in inventory:
            payload = json.dumps(item, sort_keys=True, ensure_ascii=False)
            db.execute('INSERT OR IGNORE INTO run_control_inventory VALUES(?,?,?,?,?)',
                       (run_id, frozen['catalog_id'], item['control_id'], payload, fingerprint(item)))
    if saved_resolution(store, run_id) != data:
        raise ValueError('需求检查范围并发提交冲突')
    return data


def check_target(store, run_id: str, scope: dict):
    frozen = store.run(run_id)['snapshot'].get('requirement_check_scope')
    if not frozen:
        return
    result = saved_resolution(store, run_id)
    if not result or result['preview_hash'] != frozen['preview_hash'] or result['policy'] != POLICY:
        raise ValueError('实现任务不属于冻结需求检查范围')
    target = result['target_requirements'].get(scope.get('requirement_id'))
    if not target or scope.get('scope_resolution_hash') != result['resolution_hash'] or scope.get('acceptance_criteria') != target['acceptance_criteria']:
        raise ValueError('实现任务不属于冻结需求检查范围')
    records = store.rows("SELECT payload FROM records WHERE run_id=? AND id=? AND kind='requirement'",
                         (run_id, scope['requirement_id']))
    if not records or fingerprint(json.loads(records[0]['payload'])) != target['requirement_hash']:
        raise ValueError('实现目标需求与冻结版本不一致')


def summary(store, run_id: str) -> dict:
    run = store.run(run_id)
    frozen = run['snapshot'].get('requirement_check_scope')
    if not frozen:
        return {'selection': 'ALL', 'resolution': None, 'code_analysis_scope': 'UNCHANGED_BY_REQUIREMENT_SELECTION'}
    result = saved_resolution(store, run_id)
    return {'selection': 'SELECTED', 'request': frozen, 'resolution': result,
            'code_analysis_scope': 'UNCHANGED_BY_REQUIREMENT_SELECTION'}
