"""Offline retrieval adapters validated by the independent bilingual benchmark."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from functools import lru_cache
from pathlib import Path

from .config import ROOT, contained_file
from .embeddings import validate_vector
from .skills import digest

TASK = 'Given a security requirement, retrieve normative security controls that directly specify the required behavior.'
SUPPORTED = {'BAAI/bge-m3': ('embedding', 'cls', 1024),
             'Qwen/Qwen3-Embedding-0.6B': ('embedding', 'last_token', 1024),
             'BAAI/bge-reranker-v2-m3': ('reranker', 'classification', 1),
             'Qwen/Qwen3-Reranker-0.6B': ('reranker', 'yes_no_logit', 1)}


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


class LocalRetrievalModel:
    def __init__(self, root: Path, purpose: str):
        self.root = root
        identity = json.loads(contained_file(root, 'model-identity.json').read_text())
        self.name = identity['model']
        if self.name not in SUPPORTED or SUPPORTED[self.name][0] != purpose:
            raise ValueError('本地检索模型类型不匹配')
        revision = contained_file(root, 'revision.txt').read_text().strip()
        if revision != identity['revision']:
            raise ValueError('本地模型版本声明不匹配')
        names = ('config.json', 'model.safetensors', 'pytorch_model.bin', 'tokenizer.json',
                 'tokenizer_config.json', 'special_tokens_map.json', 'sentencepiece.bpe.model',
                 'vocab.json', 'merges.txt', 'revision.txt', 'model-identity.json')
        files = {n: file_digest(contained_file(root, n)) for n in names if (root / n).is_file()}
        if not {'config.json', 'tokenizer.json', 'revision.txt'} <= files.keys() or not (
            {'model.safetensors', 'pytorch_model.bin'} & files.keys()):
            raise ValueError('本地检索模型未预置完整')
        self.profile = {'model': self.name, 'revision': revision, 'purpose': purpose,
            'dimensions': SUPPORTED[self.name][2], 'pooling': SUPPORTED[self.name][1],
            'dtype': 'float32', 'device': 'cpu', 'threads': 4, 'batch_size': 8,
            'max_tokens': 1024, 'window_tokens': 960,
            'instruction': TASK if self.name.startswith('Qwen/') else '',
            'long_text_policy': 'token_windows_960_mean_normalized_v1',
            'rerank_batching': 'length_sorted_v1', 'files': files}
        self.profile['id'] = digest(json.dumps(self.profile, sort_keys=True).encode())
        self.lock = threading.Lock()
        self.model = self.tokenizer = None

    def _load(self):
        import torch
        from transformers import AutoModel, AutoTokenizer, AutoModelForSequenceClassification, AutoModelForCausalLM
        if self.model is None:
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            self.tokenizer = AutoTokenizer.from_pretrained(self.root, local_files_only=True, trust_remote_code=False)
            if self.name.startswith('Qwen/'):
                self.tokenizer.padding_side = 'left'
            loader = AutoModel if self.profile['purpose'] == 'embedding' else (
                AutoModelForCausalLM if self.name.startswith('Qwen/') else AutoModelForSequenceClassification)
            self.model = loader.from_pretrained(self.root, local_files_only=True, trust_remote_code=False,
                dtype=torch.float32, attn_implementation='sdpa').eval()

    def encode(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        if self.profile['purpose'] != 'embedding' or purpose not in {'query', 'passage'} or not texts or any(not t.strip() for t in texts):
            raise ValueError('向量输入或用途无效')
        import torch
        with self.lock:
            self._load()
            windows, owners = [], []
            for owner, text in enumerate(texts):
                ids = self.tokenizer.encode(text, add_special_tokens=False)
                for start in range(0, len(ids), 960):
                    value = self.tokenizer.decode(ids[start:start + 960])
                    if self.name.startswith('Qwen/') and purpose == 'query':
                        value = f'Instruct: {TASK}\nQuery:{value}'
                    windows.append(value)
                    owners.append(owner)
            sums = torch.zeros((len(texts), self.profile['dimensions']))
            with torch.inference_mode():
                for start in range(0, len(windows), 8):
                    batch = self.tokenizer(windows[start:start + 8], padding=True, truncation=False, return_tensors='pt')
                    if batch['input_ids'].shape[1] > 1024:
                        raise ValueError('向量窗口超过固定长度，拒绝截断')
                    hidden = self.model(**batch).last_hidden_state
                    pooled = hidden[:, -1] if self.name.startswith('Qwen/') else hidden[:, 0]
                    pooled = torch.nn.functional.normalize(pooled, dim=1)
                    for offset, vector in enumerate(pooled):
                        sums[owners[start + offset]] += vector
            return [validate_vector(v.tolist(), self.profile['dimensions']) for v in sums]

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if self.profile['purpose'] != 'reranker' or not query.strip() or not documents:
            raise ValueError('重排输入或用途无效')
        import torch
        with self.lock:
            self._load()
            if self.name.startswith('Qwen/'):
                prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
                suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
                texts = [prefix + f'<Instruct>: {TASK}\n<Query>: {query}\n<Document>: {doc}' + suffix for doc in documents]
            else:
                texts = [(query, doc) for doc in documents]
            lengths = [len(self.tokenizer(*t if isinstance(t, tuple) else [t], truncation=False)['input_ids']) for t in texts]
            if max(lengths) > 1024:
                raise ValueError('重排输入超过固定长度，拒绝截断')
            order = sorted(range(len(texts)), key=lambda i: lengths[i])
            scores = [0.] * len(texts)
            with torch.inference_mode():
                for start in range(0, len(texts), 8):
                    indices = order[start:start + 8]
                    batch = self.tokenizer([texts[i] for i in indices], padding=True, truncation=False, return_tensors='pt')
                    if self.name.startswith('Qwen/'):
                        output = self.model(**batch, logits_to_keep=1).logits
                        yes, no = [self.tokenizer.convert_tokens_to_ids(t) for t in ('yes', 'no')]
                        values = output[:, -1, yes] - output[:, -1, no]
                    else:
                        values = self.model(**batch).logits.reshape(-1)
                    for index, score in zip(indices, values.tolist()):
                        if not math.isfinite(score):
                            raise ValueError('本地重排结果无效')
                        scores[index] = float(score)
            return scores


@lru_cache(maxsize=1)
def reranker(root: str = '') -> LocalRetrievalModel:
    root = root or os.getenv('AUDITOR_RERANKER_MODEL_DIR', str(ROOT / 'models/rerankers/bge-reranker-v2-m3'))
    return LocalRetrievalModel(Path(root), 'reranker')
