"""Import prepared catalogs, query persisted vectors and bind frozen controls."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import contained_file
from .domain import Requirement, compliance_candidate
from .embeddings import encoder, validate_vector
from .skills import digest


def import_catalog(store, library_id: str, manifest: dict):
    prepared = manifest.get('prepared')
    if not prepared:
        return
    identity = {k: v for k, v in prepared.items() if k != 'catalog_id'}
    if digest(json.dumps(identity, sort_keys=True).encode()) != prepared['catalog_id']:
        raise ValueError('预计算目录身份摘要不一致')
    schema = prepared.get('schema_version', 1)
    recipe = prepared.get('retrieval')
    if schema not in (1, 2):
        raise ValueError('预计算目录版本不支持')
    if schema == 2:
        if not isinstance(recipe, dict) or set(recipe) != {'algorithm', 'rrf_k', 'rerank_limit', 'reranker_profile'}:
            raise ValueError('预计算检索策略不完整')
        expected = 'bilingual_bm25_rrf60_rerank40_v1' if recipe['reranker_profile'] else 'bilingual_bm25_rrf60_v1'
        if recipe['algorithm'] != expected or recipe['rrf_k'] != 60 or recipe['rerank_limit'] != 40:
            raise ValueError('预计算检索策略不支持')
        if recipe['reranker_profile']:
            profile = {k: v for k, v in recipe['reranker_profile'].items() if k != 'id'}
            if digest(json.dumps(profile, sort_keys=True).encode()) != recipe['reranker_profile'].get('id'):
                raise ValueError('重排模型身份摘要不一致')
    elif recipe:
        raise ValueError('旧目录不能附加新检索策略')
    cid = prepared['catalog_id']
    if store.rows('SELECT catalog_id FROM standard_catalogs WHERE catalog_id=?', (cid,)):
        return
    root = Path(manifest['root'])
    from .standard_controls import load_controls, catalog_items
    clauses = [json.loads(line) for line in contained_file(root, 'clauses.jsonl').read_text().splitlines() if line.strip()]
    if digest(contained_file(root, 'controls.jsonl').read_bytes()) != prepared['controls_hash']:
        raise ValueError('预生成控制目录摘要不一致')
    controls = load_controls(root, clauses)
    raw = contained_file(root, prepared['vectors_file']).read_bytes()
    if digest(raw) != prepared['vectors_hash']:
        raise ValueError('预计算标准向量摘要不一致')
    vectors = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    inventory = catalog_items(clauses, controls, prepared.get('schema_version', 1))
    items = {(i['item_type'], i['item_id']): digest(i['text'].encode()) for i in inventory}
    if len(controls) != prepared['control_count'] or len(vectors) != prepared['vector_count'] or len(vectors) != len(items):
        raise ValueError('预计算目录库存不完整')
    seen = set()
    dimensions = prepared['profile']['dimensions']
    profile = {k: v for k, v in prepared['profile'].items() if k != 'id'}
    if digest(json.dumps(profile, sort_keys=True).encode()) != prepared['profile']['id']:
        raise ValueError('向量模型身份摘要不一致')
    for v in vectors:
        key = (v['item_type'], v['item_id'])
        if key in seen or items.get(key) != v['text_hash']:
            raise ValueError('标准向量来源不匹配或重复')
        validate_vector(v['vector'], dimensions)
        seen.add(key)
    if seen != items.keys():
        raise ValueError('标准向量库存不匹配')
    payload = json.dumps(prepared, sort_keys=True)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM standard_catalogs WHERE catalog_id=?', (cid,)).fetchone():
            return
        db.execute('INSERT INTO standard_catalogs VALUES(?,?,?,?,?,?)',
                   (cid, library_id, payload, digest(payload.encode()), len(controls), len(vectors)))
        for c in controls:
            payload = json.dumps(c, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO standard_controls VALUES(?,?,?,?,?)',
                       (cid, c['id'], c['clause_id'], payload, digest(payload.encode())))
        for v in vectors:
            payload = json.dumps(v['vector'])
            db.execute('INSERT INTO standard_vectors VALUES(?,?,?,?,?,?)',
                       (cid, v['item_type'], v['item_id'], payload, digest(payload.encode()), v['text_hash']))
        if prepared.get('schema_version', 1) >= 2:
            from .retrieval import import_lexical
            import_lexical(db, cid, inventory)


def catalog(store, run_id: str) -> dict:
    snapshot = store.run(run_id)['snapshot'].get('standard') or {}
    prepared = snapshot.get('prepared') or {}
    rows = store.rows('SELECT * FROM standard_catalogs WHERE catalog_id=? AND library_id=?',
                      (prepared.get('catalog_id', ''), snapshot.get('library_id', '')))
    if not rows or digest(rows[0]['payload'].encode()) != rows[0]['sha256']:
        raise ValueError('本次分析没有完整预计算的标准控制与向量目录')
    if json.loads(rows[0]['payload']) != prepared:
        raise ValueError('标准控制目录与运行固定版本不一致')
    return prepared


def control(store, run_id: str, control_id: str) -> dict:
    prepared = catalog(store, run_id)
    rows = store.rows('SELECT * FROM standard_controls WHERE catalog_id=? AND control_id=?',
                      (prepared['catalog_id'], control_id))
    if not rows or digest(rows[0]['payload'].encode()) != rows[0]['sha256']:
        raise ValueError('控制需求不属于当前目录或内容摘要不一致')
    return json.loads(rows[0]['payload'])


def search(store, run_id: str, query: str, section: str = '', limit: int = 8, offset: int = 0, embedder=None, reranker_model=None):
    prepared = catalog(store, run_id)
    if not query.strip() or len(query) > 4000 or (section and not re.fullmatch(r'(?:\d+|A[123])(?:\.\d+)*', section)):
        raise ValueError('语义查询或章节无效')
    embedder = embedder or encoder()
    profile = prepared['profile']
    if embedder.profile != profile:
        raise ValueError('查询向量模型与标准预计算模型不一致，必须显式重建目录')
    recipe = prepared.get('retrieval') or {}
    if recipe.get('reranker_profile'):
        from .retrieval_models import reranker
        reranker_model = reranker_model or reranker()
        if reranker_model.profile != recipe['reranker_profile']:
            raise ValueError('重排模型与固定标准目录不一致')
    query_key = digest(query.encode())
    cache = store.rows('SELECT * FROM standard_query_vectors WHERE profile_id=? AND query_hash=?', (profile['id'], query_key))
    if cache:
        if digest(cache[0]['vector'].encode()) != cache[0]['sha256']:
            raise ValueError('查询向量缓存摘要不一致')
        q = validate_vector(json.loads(cache[0]['vector']), profile['dimensions'])
    else:
        encoded = embedder.encode([query], purpose='query')
        if len(encoded) != 1:
            raise ValueError('查询向量返回库存无效')
        q = validate_vector(encoded[0], profile['dimensions'])
        payload = json.dumps(q)
        with store.connect() as db:
            db.execute('INSERT OR IGNORE INTO standard_query_vectors VALUES(?,?,?,?)',
                       (profile['id'], query_key, payload, digest(payload.encode())))
    controls = {r['control_id']: r for r in store.rows('SELECT * FROM standard_controls WHERE catalog_id=?', (prepared['catalog_id'],))}
    decoded, by_clause = {}, {}
    for control_id, row in controls.items():
        if digest(row['payload'].encode()) != row['sha256']:
            raise ValueError('预生成控制内容摘要不一致')
        decoded[control_id] = json.loads(row['payload'])
        by_clause.setdefault(row['clause_id'], []).append(control_id)
    vectors = store.rows('SELECT * FROM standard_vectors WHERE catalog_id=?', (prepared['catalog_id'],))
    scored = {}
    for row in vectors:
        if digest(row['vector'].encode()) != row['sha256']:
            raise ValueError('已入库标准向量摘要不一致')
        vector = validate_vector(json.loads(row['vector']), profile['dimensions'])
        score = sum(a * b for a, b in zip(q, vector))
        targets = [row['item_id']] if row['item_type'].startswith('control') else by_clause[row['item_id']]
        for target in targets:
            scored[target] = max(score, scored.get(target, -1.0))
    selected = store.run(run_id)['language']
    dense = sorted(scored, key=lambda c: (-scored[c], c))
    ranked = dense
    rerank_scores = {}
    if recipe:
        from .retrieval import lexical_ranking, fuse
        lexical = lexical_ranking(store, prepared['catalog_id'], query, by_clause)
        ranked = fuse(dense, lexical, k=recipe['rrf_k'])
    ranked = [cid for cid in ranked if not section or decoded[cid]['clause_id'] == section or decoded[cid]['clause_id'].startswith(section + '.')]
    if recipe:
        cache = store.rows('SELECT * FROM standard_search_cache WHERE catalog_id=? AND query_hash=? AND language=? AND section=?',
                           (prepared['catalog_id'], query_key, selected, section))
        if cache:
            row = cache[0]
            if digest(row['payload'].encode()) != row['sha256']:
                raise ValueError('标准检索缓存摘要不一致')
            result = json.loads(row['payload'])
            if len(result['ranking']) != len(set(result['ranking'])) or set(result['ranking']) != set(ranked):
                raise ValueError('标准检索缓存库存不一致')
            ranked, rerank_scores = result['ranking'], result['rerank_scores']
        else:
            if reranker_model and recipe.get('reranker_profile') and ranked:
                from .standard_controls import control_text
                ids = ranked[:recipe['rerank_limit']]
                docs = [control_text(decoded[c], selected) + '\nNormative source:\n' + decoded[c]['source_excerpt'] for c in ids]
                values = reranker_model.rerank(query, docs)
                import math
                if len(values) != len(ids) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise ValueError('本地重排结果库存或数值无效')
                rerank_scores = dict(zip(ids, values))
                ranked = sorted(ids, key=lambda c: (-rerank_scores[c], c)) + ranked[len(ids):]
            payload = json.dumps({'ranking': ranked, 'rerank_scores': rerank_scores}, sort_keys=True)
            with store.connect() as db:
                db.execute('INSERT OR IGNORE INTO standard_search_cache VALUES(?,?,?,?,?,?)',
                           (prepared['catalog_id'], query_key, selected, section, payload, digest(payload.encode())))
    candidates = []
    for control_id in ranked:
        c = decoded[control_id]
        text = c['localizations'][selected]
        candidates.append({'control_id': control_id, 'clause_id': c['clause_id'],
                           'control_type': c['control_type'], 'verification_method': c['verification_method'],
                           'title': text['title'], 'statement': text['statement'],
                           'similarity': scored[control_id], 'rerank_score': rerank_scores.get(control_id), 'read_required': True})
    limit, offset = min(max(limit, 1), 12), max(0, offset)
    return {'items': candidates[offset:offset + limit], 'has_more': len(candidates) > offset + limit,
            'next_offset': offset + len(candidates[offset:offset + limit]), 'catalog_id': prepared['catalog_id'],
            'retrieval': ('PERSISTED_BILINGUAL_HYBRID_WITH_LOCAL_RERANK' if recipe.get('reranker_profile') else 'PERSISTED_BILINGUAL_HYBRID') if recipe else 'PERSISTED_STANDARD_AND_CONTROL_VECTORS',
            'candidate_only': True, 'scores_are_applicability_probabilities': False}


def read_control(store, run_id: str, task_id: str, control_id: str) -> tuple[dict, str]:
    c = control(store, run_id, control_id)
    from .standard_library import read
    _, eid = read(store, run_id, c['clause_id'])
    prepared = catalog(store, run_id)
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO task_control_reads VALUES(?,?,?)',
                   (task_id, prepared['catalog_id'], control_id))
    return {k: v for k, v in c.items() if k != 'localizations'} | {
        'content': c['localizations'][store.run(run_id)['language']], 'catalog_id': prepared['catalog_id']}, eid


def bind_controls(store, run_id: str, task_id: str) -> dict:
    """A declared association projection, not a model or text generation step."""
    records = store.records(run_id)
    mappings = [r for r in records if r['kind'] == 'applicability' and compliance_candidate(r)]
    if not (store.run(run_id)['snapshot'].get('standard') or {}).get('prepared'):
        if mappings:
            raise ValueError('标准必须先独立生成控制和向量，不能在项目中现场生成')
        store.finish_service(task_id, {'requirements': 0, 'gaps': []}, status='SKIPPED')
        return {'requirements': 0}
    prepared = catalog(store, run_id)
    byid = {r['id']: r for r in records}
    groups = {}
    for m in mappings:
        if not m.get('control_ids'):
            raise ValueError('相关匹配未指定预生成控制')
        for control_id in m['control_ids']:
            c = control(store, run_id, control_id)
            if c['clause_id'] != m['clause_id'] or c['control_type'] == 'ORGANIZATIONAL':
                raise ValueError('匹配控制来源或范围无效')
            for req_id in m['requirement_ids']:
                req = byid[req_id]
                key = (control_id, req['module'])
                group = groups.setdefault(key, {'control': c, 'requirements': set(), 'mappings': set()})
                group['requirements'].add(req_id)
                group['mappings'].add(m['id'])
    bound = []
    language = store.run(run_id)['language']
    from .standard_library import read
    for (control_id, module), group in sorted(groups.items()):
        c = group['control']
        text = c['localizations'][language]
        _, eid = read(store, run_id, c['clause_id'])
        rid = 'control_' + digest(json.dumps([run_id, prepared['catalog_id'], control_id, module]).encode())[:40]
        requirement = Requirement(id=rid, title=text['title'], statement=text['statement'],
            acceptance_criteria=text['acceptance_criteria'], rationale=text['rationale'],
            module=module, origin='PCI_DSS', clause_ids=[c['clause_id']], evidence_ids=[eid],
            standard_control_id=control_id, standard_catalog_id=prepared['catalog_id'],
            verification_method=c['verification_method'], matched_requirement_ids=sorted(group['requirements']))
        requirement.applicability_conditions = text['applicability_conditions']
        bound.append((requirement, group))
    from .store import now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()[0] == 'SUCCEEDED':
            return {'requirements': len(bound)}
        for requirement, group in bound:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                       (requirement.id, run_id, task_id, 'requirement', requirement.model_dump_json(), now()))
            db.execute('INSERT INTO project_control_bindings VALUES(?,?,?,?,?,?,?)',
                       (run_id, prepared['catalog_id'], requirement.standard_control_id, requirement.module,
                        requirement.id, json.dumps(sorted(group['requirements'])), json.dumps(sorted(group['mappings']))))
        receipt = {'requirements': len(bound), 'catalog_id': prepared['catalog_id'],
                   'model_calls': 0, 'standard_embedding_calls': 0, 'gaps': []}
        db.execute("UPDATE tasks SET status='SUCCEEDED',result=?,error=NULL WHERE id=?", (json.dumps(receipt), task_id))
    store.event(run_id, 'standard_controls_bound', receipt)
    return receipt
