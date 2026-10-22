"""Deterministic requirement-check selection. Never a code-audit boundary."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, model_validator

from .domain import StrictModel
from .skills import digest

POLICY = 'requirement_check_scope_v1'
NODE = re.compile(r'(?:[1-9][0-9]*|A[123])(?:\.[0-9]+)*\Z')


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


class ScopeSelector(StrictModel):
    kind: Literal['facets', 'controls']
    standard_node_ids: list[str] = Field(default_factory=list, max_length=256)
    control_ids: list[str] = Field(default_factory=list, max_length=2048)
    topic_ids: list[str] = Field(default_factory=list, max_length=0)
    responsibility_ids: list[str] = Field(default_factory=list, max_length=0)

    @model_validator(mode='after')
    def selection(self):
        values = self.standard_node_ids if self.kind == 'facets' else self.control_ids
        other = self.control_ids if self.kind == 'facets' else self.standard_node_ids
        if not values or other or any(not v.strip() for v in values):
            raise ValueError('请选择非空的章节或控制，不能混用两种选择方式')
        if len(values) != len(set(values)):
            raise ValueError('范围选择 ID 不能重复')
        return self


class ScopeRequest(StrictModel):
    catalog_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    selector: ScopeSelector
    preview_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def descendants(clause_id: str, node_id: str) -> bool:
    return clause_id == node_id or clause_id.startswith(node_id + '.')


def controls(store, catalog_id: str) -> tuple[dict, list[dict]]:
    rows = store.rows('SELECT * FROM standard_catalogs WHERE catalog_id=?', (catalog_id,))
    if not rows:
        raise ValueError('标准控制目录不存在')
    row = rows[0]
    if digest(row['payload'].encode()) != row['sha256']:
        raise ValueError('标准控制目录摘要不一致')
    prepared = json.loads(row['payload'])
    if prepared.get('catalog_id') != catalog_id or digest(json.dumps({k: v for k, v in prepared.items() if k != 'catalog_id'}, sort_keys=True).encode()) != catalog_id:
        raise ValueError('标准控制目录身份不一致')
    items = store.rows('SELECT * FROM standard_controls WHERE catalog_id=? ORDER BY clause_id,control_id', (catalog_id,))
    if len(items) != row['control_count']:
        raise ValueError('标准控制库存不完整')
    result = []
    for item in items:
        if digest(item['payload'].encode()) != item['sha256']:
            raise ValueError('标准控制内容摘要不一致')
        control = json.loads(item['payload'])
        if control['id'] != item['control_id'] or control['clause_id'] != item['clause_id']:
            raise ValueError('标准控制身份不一致')
        result.append(control)
    return row, result


def scope_options(store, catalog_id: str) -> dict:
    row, items = controls(store, catalog_id)
    nodes = {}
    for c in items:
        clause = c['clause_id']
        if not NODE.fullmatch(clause):
            raise ValueError('标准章节编号不支持范围选择')
        parts = clause.split('.')
        for index in range(1, len(parts) + 1):
            node = '.'.join(parts[:index])
            nodes.setdefault(node, {'id': node, 'parent_id': '.'.join(parts[:index-1]), 'control_count': 0})['control_count'] += 1
    data = {'policy': POLICY, 'catalog_id': catalog_id, 'library_id': row['library_id'],
            'nodes': sorted(nodes.values(), key=lambda n: tuple((0, int(p)) if p.isdigit() else (1, p) for p in n['id'].split('.'))),
            'controls': [{'id': c['id'], 'clause_id': c['clause_id'], 'verification_method': c['verification_method'],
                          'titles': {lang: text['title'] for lang, text in c['localizations'].items()}} for c in items],
            'supported_dimensions': ['standard_node_ids', 'control_ids'],
            'metadata_source': 'fixed_catalog_clause_identity'}
    data['metadata_hash'] = fingerprint(data)
    # Derived identity metadata, persisted once. Existing v1/v2 vectors remain untouched.
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO standard_scope_metadata VALUES(?,?,?)',
                   (catalog_id, json.dumps(data, ensure_ascii=False, sort_keys=True), fingerprint(data)))
        saved = db.execute('SELECT payload,sha256 FROM standard_scope_metadata WHERE catalog_id=?', (catalog_id,)).fetchone()
        if fingerprint(json.loads(saved['payload'])) != saved['sha256'] or json.loads(saved['payload']) != data:
            raise ValueError('标准范围元数据与固定目录不一致')
    return data


def preview(store, request: ScopeRequest | dict) -> dict:
    request = request if isinstance(request, ScopeRequest) else ScopeRequest.model_validate(request)
    options = scope_options(store, request.catalog_id)
    _, items = controls(store, request.catalog_id)
    selector = request.selector
    nodes, ids = set(selector.standard_node_ids), set(selector.control_ids)
    if nodes - {n['id'] for n in options['nodes']} or ids - {c['id'] for c in items}:
        raise ValueError('范围包含不存在或不属于该目录的章节/控制')
    chosen = [c for c in items if c['id'] in ids or any(descendants(c['clause_id'], n) for n in nodes)]
    if not chosen:
        raise ValueError('所选范围没有控制需求，不能回退全量')
    selector_data = selector.model_dump()
    for key in selector_data:
        if isinstance(selector_data[key], list):
            selector_data[key] = sorted(selector_data[key])
    clauses = sorted({c['clause_id'] for c in chosen})
    library = options['library_id']
    contexts, gaps = set(), []
    for clause in clauses:
        rows = store.rows('SELECT payload,sha256 FROM standard_clauses WHERE pack_id=? AND clause_id=?', (library, clause))
        if not rows or digest(rows[0]['payload'].encode()) != rows[0]['sha256']:
            raise ValueError('范围条款原文缺失或摘要不一致')
        data = json.loads(rows[0]['payload'])
        contexts.update(data.get('source', {}).get('context_ids', []))
    for cid in sorted(contexts):
        rows = store.rows('SELECT payload,sha256 FROM standard_contexts WHERE pack_id=? AND context_id=?', (library, cid))
        if not rows or digest(rows[0]['payload'].encode()) != rows[0]['sha256']:
            gaps.append({'context_id': cid, 'reason': 'MISSING_OR_INVALID_CONTEXT'})
    result = {'policy': POLICY, 'catalog_id': request.catalog_id, 'library_id': library,
              'metadata_hash': options['metadata_hash'], 'selector': selector_data,
              'control_ids': sorted(c['id'] for c in chosen), 'clause_ids': clauses,
              'control_hashes': {c['id']: fingerprint(c) for c in chosen},
              'context_ids': sorted(contexts), 'dependency_gaps': gaps,
              'dependency_policy': 'declared_source_contexts_v1',
              'dependency_limitations': ['CROSS_CLAUSE_SEMANTIC_DEPENDENCIES_NOT_PREPARED'],
              'counts': {'controls': len(chosen), 'clauses': len(clauses), 'contexts': len(contexts),
                         'code_controls': sum(c['verification_method'] == 'CODE' for c in chosen),
                         'external_controls': sum(c['verification_method'] != 'CODE' for c in chosen)}}
    result['preview_hash'] = fingerprint(result)
    if request.preview_hash and request.preview_hash != result['preview_hash']:
        raise ValueError('范围预览已变化，请重新预览后开始')
    return result


def validate_frozen(store, frozen: dict) -> dict:
    expected = preview(store, {key: frozen[key] for key in ('catalog_id', 'selector', 'preview_hash')})
    if frozen != expected:
        raise ValueError('运行固定需求检查范围已变化')
    return frozen
