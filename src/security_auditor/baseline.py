"""Freeze and import committed requirements without another model interpretation."""
from __future__ import annotations

import json

from .domain import StageOutput
from .skills import digest
from .store import Store, now


def baseline_snapshot(store: Store, project_id: str, source_run_id: str) -> dict:
    source = store.run(source_run_id)
    if source['project_id'] != project_id:
        raise ValueError('需求来源不属于此项目')
    if source['status'] not in {'COMPLETED', 'PAUSED', 'FAILED'}:
        raise ValueError('请先暂停需求来源运行')
    rows = store.rows("SELECT r.* FROM records r JOIN tasks t ON t.id=r.task_id "
                      "WHERE r.run_id=? AND r.kind IN ('fact','requirement') AND t.status='SUCCEEDED' "
                      "ORDER BY r.created,r.id", (source_run_id,))
    records = [json.loads(row['payload']) for row in rows]
    # Only document design facts accompany the requirements, never old code observations.
    records = [r for r in records if r['kind'] == 'requirement' or r.get('basis') != 'OBSERVED']
    if not any(r['kind'] == 'requirement' for r in records):
        raise ValueError('来源运行没有已提交的安全需求')
    StageOutput(records=records, summary='Frozen source requirements')
    evidence_ids = sorted({eid for r in records for eid in r['evidence_ids']})
    evidence = [store.read_evidence(source_run_id, eid) for eid in evidence_ids]
    if any(e['source_type'] not in {'document', 'standard'} for e in evidence):
        raise ValueError('需求基线引用了非设计材料')
    data = {'source_run_id': source_run_id, 'source_snapshot_hash': digest(
        json.dumps(source['snapshot'], sort_keys=True).encode()),
        'language': source['language'], 'records': records, 'evidence': evidence,
        'requirement_review': 'NOT_IMPORTED', 'compliance_mapping': 'NOT_IMPORTED'}
    return {**data, 'sha256': digest(json.dumps(data, sort_keys=True, ensure_ascii=False).encode())}


def import_baseline(store: Store, run_id: str, task_id: str) -> dict:
    baseline = dict(store.run(run_id)['snapshot']['baseline'])
    expected = baseline.pop('sha256')
    if digest(json.dumps(baseline, sort_keys=True, ensure_ascii=False).encode()) != expected:
        raise ValueError('需求基线摘要不匹配')
    source_map = {}
    for source in baseline['evidence']:
        if digest(source['content'].encode()) != source['sha256']:
            raise ValueError('需求来源摘要不匹配')
        source_map[source['id']] = store.add_evidence(
            run_id, source['source_type'], source['locator'], source['content'],
            {**source['metadata'], 'baseline_run_id': baseline['source_run_id'],
             'baseline_evidence_id': source['id']})
    record_map, records = {}, []
    for index, original in enumerate(baseline['records']):
        rid = run_id.replace('-', '') + f'_baseline_{index:03d}'
        record_map[original['id']] = rid
        records.append({**original, 'id': rid,
                        'evidence_ids': [source_map[eid] for eid in original['evidence_ids']]})
    output = StageOutput(records=records, summary='Imported immutable requirement baseline')
    receipt = {'source_run_id': baseline['source_run_id'], 'baseline_hash': expected,
               'record_id_map': record_map, 'evidence_id_map': source_map,
               'requirements': sum(r['kind'] == 'requirement' for r in records), 'gaps': []}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()[0] == 'SUCCEEDED':
            return receipt
        for record in output.records:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (record.id, run_id, task_id, record.kind, record.model_dump_json(), now()))
        db.execute("UPDATE tasks SET status='SUCCEEDED',result=?,error=NULL WHERE id=?",
                   (json.dumps(receipt), task_id))
    store.event(run_id, 'requirement_baseline_imported', receipt)
    return receipt
