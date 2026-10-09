"""Frozen lexical retrieval and reciprocal-rank fusion; no analytical inference."""
import re

from .skills import digest


def terms(text: str) -> list[str]:
    result = []
    for word in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', text.lower()):
        if re.search(r'[\u4e00-\u9fff]', word):
            result += list(word) + [word[i:i + 2] for i in range(len(word) - 1)]
        else:
            result.append(word)
    return result


def lexical_table(catalog_id: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{64}', catalog_id):
        raise ValueError('标准目录 ID 无效')
    return 'standard_lexical_' + catalog_id


def import_lexical(db, catalog_id: str, items: list[dict]):
    table = lexical_table(catalog_id)
    db.execute(f'CREATE VIRTUAL TABLE {table} USING fts5(item_type UNINDEXED,item_id UNINDEXED,content,sha256 UNINDEXED)')
    for item in items:
        content = ' '.join(terms(item['text']))
        db.execute(f'INSERT INTO {table}(item_type,item_id,content,sha256) VALUES(?,?,?,?)',
                   (item['item_type'], item['item_id'], content, digest(content.encode())))


def lexical_ranking(store, catalog_id: str, query: str, by_clause: dict) -> list[str]:
    table = lexical_table(catalog_id)
    for row in store.rows(f'SELECT content,sha256 FROM {table}'):
        if digest(row['content'].encode()) != row['sha256']:
            raise ValueError('标准关键词索引摘要不一致')
    tokens = sorted(set(terms(query)))
    if not tokens:
        return []
    expression = ' OR '.join('"' + t + '"' for t in tokens)
    rows = store.rows(f'SELECT item_type,item_id,bm25({table}) AS score FROM {table} WHERE {table} MATCH ? ORDER BY score,rowid', (expression,))
    scores = {}
    for row in rows:
        ids = [row['item_id']] if row['item_type'].startswith('control') else by_clause[row['item_id']]
        for control_id in ids:
            scores[control_id] = max(-row['score'], scores.get(control_id, -float('inf')))
    return sorted(scores, key=lambda c: (-scores[c], c))


def fuse(*rankings: list[str], k: int = 60) -> list[str]:
    scores = {}
    for ranking in rankings:
        for index, control_id in enumerate(ranking):
            scores[control_id] = scores.get(control_id, 0.) + 1. / (k + index + 1)
    return sorted(scores, key=lambda c: (-scores[c], c))
