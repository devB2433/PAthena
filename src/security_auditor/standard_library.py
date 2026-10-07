"""Immutable structured standards, shared across projects without model processing."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import contained_file
from .skills import digest


POLICY = 'structured_library_requirement_matching_v1'


def import_library(store, manifest: dict) -> str:
    key = digest(json.dumps({k: manifest.get(k) for k in
                            ('manifest_hash', 'clauses_hash', 'contexts_hash', 'source_hash')}, sort_keys=True).encode())
    existing = store.rows('SELECT * FROM standard_versions WHERE id=?', (key,))
    if existing:
        if existing[0]['clause_count'] != len(manifest['requirement_ids']):
            raise ValueError('标准库库存与固定版本不一致')
        return key
    root = Path(manifest['root'])
    raw = contained_file(root, 'clauses.jsonl').read_bytes()
    if digest(raw) != manifest['clauses_hash']:
        raise ValueError('标准条款与固定版本不一致')
    clauses = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    contexts = []
    if manifest.get('contexts_hash'):
        raw = contained_file(root, 'contexts.jsonl').read_bytes()
        if digest(raw) != manifest['contexts_hash']:
            raise ValueError('标准上下文与固定版本不一致')
        contexts = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM standard_versions WHERE id=?', (key,)).fetchone():
            return key
        db.execute('INSERT INTO standard_versions VALUES(?,?,?,?,?,?)',
                   (key, 'PCI_DSS', manifest['version'], json.dumps(manifest, sort_keys=True), len(clauses), len(contexts)))
        for clause in clauses:
            payload = json.dumps(clause, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO standard_clauses VALUES(?,?,?,?,?,?,?,?,?)',
                       (key, clause['id'], clause.get('parent_id', ''), clause.get('defined_approach', ''),
                        clause.get('applicability_notes', ''), clause.get('testing_procedures', ''),
                        clause.get('guidance', ''), payload, digest(payload.encode())))
            db.execute('INSERT INTO standard_clause_search VALUES(?,?,?)',
                       (key, clause['id'], clause['text']))
        for context in contexts:
            payload = json.dumps(context, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO standard_contexts VALUES(?,?,?,?)',
                       (key, context['id'], payload, digest(payload.encode())))
    return key


def version(store, run_id: str) -> dict:
    snapshot = store.run(run_id)['snapshot'].get('standard') or {}
    rows = store.rows('SELECT * FROM standard_versions WHERE id=?', (snapshot.get('library_id', ''),))
    if not rows:
        raise ValueError('本次分析没有固定的结构化标准库')
    item = rows[0]
    if json.loads(item['manifest']) != {k: v for k, v in snapshot.items() if k != 'library_id'}:
        raise ValueError('标准库版本与运行快照不一致')
    return item


def overview(store, run_id: str) -> dict:
    item = version(store, run_id)
    return {'standard_id': item['standard_id'], 'version': item['version'],
            'clause_count': item['clause_count'], 'context_count': item['context_count'],
            'sections': store.rows('SELECT substr(clause_id,1,instr(clause_id,\'.\')-1) section,count(*) clause_count '
                                  'FROM standard_clauses WHERE pack_id=? GROUP BY section ORDER BY CAST(section AS INTEGER)',
                                  (item['id'],)),
            'coverage': 'Search candidates are not proof of exhaustive semantic coverage'}


def search(store, run_id: str, query: str, section: str = '', limit: int = 8, offset: int = 0) -> dict:
    item = version(store, run_id)
    if len(query) > 200 or (section and not re.fullmatch(r'\d+(?:\.\d+)*', section)):
        raise ValueError('标准库查询或章节无效')
    tokens = re.findall(r'\w+', query, flags=re.UNICODE)
    limit = min(max(limit, 1), 12)
    if not tokens and not section:
        return overview(store, run_id)
    prefix = section + '.%' if section else '%'
    if tokens:
        expression = ' OR '.join('"' + token + '"' for token in tokens)
        rows = store.rows('SELECT c.payload,c.sha256 FROM standard_clause_search f JOIN standard_clauses c '
                          'ON c.pack_id=f.pack_id AND c.clause_id=f.clause_id WHERE standard_clause_search MATCH ? '
                          'AND c.pack_id=? AND (c.clause_id=? OR c.clause_id LIKE ?) ORDER BY bm25(standard_clause_search),c.clause_id '
                          'LIMIT ? OFFSET ?', (expression, item['id'], section, prefix, limit+1, max(0, offset)))
    else:
        rows = store.rows('SELECT payload,sha256 FROM standard_clauses WHERE pack_id=? AND '
                          '(clause_id=? OR clause_id LIKE ?) ORDER BY clause_id LIMIT ? OFFSET ?',
                          (item['id'], section, prefix, limit+1, max(0, offset)))
    items = []
    for row in rows[:limit]:
        if digest(row['payload'].encode()) != row['sha256']:
            raise ValueError('标准条款摘要不一致')
        clause = json.loads(row['payload'])
        items.append({'clause_id': clause['id'], 'parent_id': clause.get('parent_id', ''),
                      'parent_context': clause.get('parent_context', ''),
                      'defined_approach_excerpt': clause.get('defined_approach', clause['text'])[:1000],
                      'pdf_pages': clause['source'].get('pdf_pages', []), 'read_required': True})
    return {'items': items, 'has_more': len(rows)>limit, 'next_offset': max(0, offset)+len(items)}


def read(store, run_id: str, identifier: str, *, context: bool = False) -> tuple[dict, str]:
    item = version(store, run_id)
    table, field = ('standard_contexts', 'context_id') if context else ('standard_clauses', 'clause_id')
    rows = store.rows(f'SELECT payload,sha256 FROM {table} WHERE pack_id=? AND {field}=?', (item['id'], identifier))
    if not rows:
        raise ValueError('条款或上下文不属于本次固定标准库')
    row = rows[0]
    if digest(row['payload'].encode()) != row['sha256']:
        raise ValueError('标准条款摘要不一致')
    data = json.loads(row['payload'])
    evidence_id = store.add_evidence(run_id, 'standard', identifier, data['text'],
                                    {field: identifier, 'library_id': item['id'], 'source': data['source'],
                                     'version': item['version'], 'record_type': 'context' if context else 'requirement',
                                     'context_ids': data['source'].get('context_ids', [])})
    return data, evidence_id
