"""Precomputed controls: import-time derivation, immutable project-time reuse."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .domain import StrictModel
from .embeddings import encoder, validate_vector
from .skills import digest


class ControlText(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    applicability_conditions: list[str]
    rationale: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_criteria(self):
        if any(not c.strip() for c in self.acceptance_criteria) or len(set(self.acceptance_criteria)) != len(self.acceptance_criteria):
            raise ValueError('预生成控制验收项为空或重复')
        return self


class StandardControl(StrictModel):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    clause_id: str
    control_type: Literal['TECHNICAL', 'DEPLOYMENT', 'ORGANIZATIONAL']
    verification_method: Literal['CODE', 'DEPLOYMENT', 'NON_CODE']
    source_excerpt: str = Field(min_length=1)
    source_pages: list[int] = Field(min_length=1)
    localizations: dict[str, ControlText]

    @model_validator(mode='after')
    def complete(self):
        if set(self.localizations) != {'en', 'zh-CN'}:
            raise ValueError('控制需求必须预生成中英文版本')
        if len(self.localizations['en'].acceptance_criteria) != len(self.localizations['zh-CN'].acceptance_criteria):
            raise ValueError('控制需求中英文验收库存不一致')
        methods = {'TECHNICAL': 'CODE', 'DEPLOYMENT': 'DEPLOYMENT', 'ORGANIZATIONAL': 'NON_CODE'}
        if self.verification_method != methods[self.control_type]:
            raise ValueError('控制类型与验证方式不一致')
        return self


def load_controls(root: Path, clauses: list[dict]) -> list[dict]:
    from .config import contained_file
    data = [StandardControl.model_validate_json(line).model_dump() for line in
            contained_file(root, 'controls.jsonl').read_text().splitlines() if line.strip()]
    source = {c['id']: c for c in clauses}
    if len({c['id'] for c in data}) != len(data) or {c['clause_id'] for c in data} != set(source):
        raise ValueError('预生成控制目录重复或未覆盖完整标准库存')
    for control in data:
        clause = source[control['clause_id']]
        if control['source_excerpt'] not in clause['defined_approach']:
            raise ValueError('控制来源必须是规范性条款的确切原文')
        if not set(control['source_pages']) <= set(clause['source']['pdf_pages']):
            raise ValueError('控制引用页码不属于原始条款')
    return data


def preparation(root: Path, embedder=None, reranker_model=None) -> dict:
    """Explicit preparation command only; no project path invokes this function."""
    from .ingestion import standard_manifest
    manifest = standard_manifest(root, include_prepared=False)
    clauses = [json.loads(line) for line in (root / 'clauses.jsonl').read_text().splitlines() if line.strip()]
    controls = load_controls(root, clauses)
    embedder = embedder or encoder()
    controls_hash = digest((root / 'controls.jsonl').read_bytes())
    retrieval = {'algorithm': 'bilingual_bm25_rrf60_rerank40_v1' if reranker_model else 'bilingual_bm25_rrf60_v1', 'rrf_k': 60, 'rerank_limit': 40,
                 'reranker_profile': reranker_model.profile if reranker_model else None}
    identity = {'schema_version': 2, 'retrieval': retrieval, 'manifest_hash': manifest['manifest_hash'],
                'contexts_hash': manifest.get('contexts_hash'), 'clauses_hash': manifest['clauses_hash'],
                'controls_hash': controls_hash, 'profile': embedder.profile}
    target = root / 'prepared.json'
    if target.exists():
        old = json.loads(target.read_text())
        if all(old.get(k) == v for k, v in identity.items()):
            if digest((root / old['vectors_file']).read_bytes()) != old['vectors_hash']:
                raise ValueError('预计算向量被修改')
            return old
    # Versioned vector files keep previous catalog revisions intact.
    items = catalog_items(clauses, controls)
    vectors = []
    for start in range(0, len(items), 16):
        batch = items[start:start + 16]
        encoded = embedder.encode([c['text'] for c in batch], purpose='passage')
        if len(encoded) != len(batch):
            raise ValueError('向量模型返回库存不完整')
        for item, vector in zip(batch, encoded):
            vectors.append({'item_type': item['item_type'], 'item_id': item['item_id'],
                            'text_hash': digest(item['text'].encode()),
                            'vector': validate_vector(vector, embedder.profile['dimensions'])})
    raw = ''.join(json.dumps(v, sort_keys=True) + '\n' for v in vectors).encode()
    name = 'vectors-' + digest(raw)[:16] + '.jsonl'
    (root / name).write_bytes(raw)
    result = {**identity, 'vectors_file': name, 'vectors_hash': digest(raw),
              'control_count': len(controls), 'vector_count': len(vectors)}
    result['catalog_id'] = digest(json.dumps(result, sort_keys=True).encode())
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(target)
    return result


def control_text(control: dict, language: str) -> str:
    text = control['localizations'][language]
    return '\n'.join([text['title'], text['statement'], *text['acceptance_criteria'], *text['applicability_conditions']])


def catalog_items(clauses: list[dict], controls: list[dict], schema_version: int = 2) -> list[dict]:
    items = [{'item_type': 'clause', 'item_id': c['id'],
              'text': c['defined_approach'] + '\n' + c.get('applicability_notes', '')} for c in clauses]
    items += [{'item_type': 'control_' + language if schema_version >= 2 else 'control',
               'item_id': c['id'], 'text': control_text(c, language)}
              for c in controls for language in (['en', 'zh-CN'] if schema_version >= 2 else ['en'])]
    return items


def main():
    parser = argparse.ArgumentParser(description='Prepare immutable standard controls and local embeddings once')
    parser.add_argument('standard_pack', type=Path)
    parser.add_argument('--model-dir', default='')
    args = parser.parse_args()
    result = preparation(args.standard_pack, encoder(args.model_dir))
    print(json.dumps({k: result[k] for k in ('catalog_id', 'control_count', 'vector_count')}))


if __name__ == '__main__':
    main()
