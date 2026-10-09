"""Offline, versioned embeddings. Only preparation embeds standard passages."""
from __future__ import annotations

import json
import math
import os
import threading
from functools import lru_cache
from pathlib import Path

from .skills import digest


def validate_vector(vector, dimensions: int) -> list[float]:
    if not isinstance(vector, list) or len(vector) != dimensions or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector
    ):
        raise ValueError('向量维度或数值无效')
    norm = math.sqrt(sum(v * v for v in vector))
    if norm < 1e-12:
        raise ValueError('零向量不能用于检索')
    return [float(v / norm) for v in vector]


class LocalE5:
    """Pinned local E5 weights; mean pooling and query/passage prefixes."""

    def __init__(self, root: Path):
        from .config import contained_file
        self.root = root
        names = ('config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
                 'special_tokens_map.json', 'sentencepiece.bpe.model', 'revision.txt')
        files = {name: digest(contained_file(root, name).read_bytes()) for name in names if (root / name).is_file()}
        if not {'config.json', 'model.safetensors', 'tokenizer.json', 'revision.txt'} <= files.keys():
            raise ValueError('本地向量模型未预置完整')
        self.profile = {'model': 'intfloat/multilingual-e5-small', 'revision': (root / 'revision.txt').read_text().strip(),
                        'dimensions': 384, 'max_tokens': 512, 'pooling': 'mean_normalized',
                        'passage_prefix': 'passage: ', 'query_prefix': 'query: ', 'files': files,
                        'long_text_policy': 'token_windows_480_mean_normalized_v1'}
        self.profile['id'] = digest(json.dumps(self.profile, sort_keys=True).encode())
        self.lock = threading.Lock()
        self.model = self.tokenizer = None

    def encode(self, texts: list[str], *, purpose: str) -> list[list[float]]:
        if purpose not in {'query', 'passage'} or any(not t.strip() for t in texts):
            raise ValueError('向量输入或用途无效')
        import torch
        from transformers import AutoModel, AutoTokenizer
        with self.lock:
            if self.model is None:
                self.tokenizer = AutoTokenizer.from_pretrained(self.root, local_files_only=True, trust_remote_code=False)
                self.model = AutoModel.from_pretrained(self.root, local_files_only=True, trust_remote_code=False)
                self.model.eval()
                torch.set_num_threads(min(4, os.cpu_count() or 1))
            windows, owners = [], []
            for i, text in enumerate(texts):
                ids = self.tokenizer.encode(text, add_special_tokens=False)
                for start in range(0, len(ids), 480):
                    windows.append(purpose + ': ' + self.tokenizer.decode(ids[start:start + 480]))
                    owners.append(i)
            sums = torch.zeros((len(texts), self.profile['dimensions']))
            counts = torch.zeros(len(texts))
            with torch.inference_mode():
                for start in range(0, len(windows), 16):
                    batch = self.tokenizer(windows[start:start + 16], max_length=512, padding=True,
                                           truncation=True, return_tensors='pt')
                    hidden = self.model(**batch).last_hidden_state
                    mask = batch['attention_mask'].unsqueeze(-1).bool()
                    pooled = hidden.masked_fill(~mask, 0).sum(1) / mask.sum(1)
                    pooled = torch.nn.functional.normalize(pooled, dim=1)
                    for offset, vector in enumerate(pooled):
                        owner = owners[start + offset]
                        sums[owner] += vector
                        counts[owner] += 1
            return [validate_vector(v.tolist(), self.profile['dimensions']) for v in sums / counts.unsqueeze(1)]


@lru_cache(maxsize=2)
def encoder(root: str = ''):
    default = Path(__file__).resolve().parents[2] / 'models/embeddings/qwen3-embedding-0.6b'
    location = Path(root or os.getenv('AUDITOR_EMBEDDING_MODEL_DIR', str(default)))
    if (location / 'model-identity.json').is_file():
        from .retrieval_models import LocalRetrievalModel
        return LocalRetrievalModel(location, 'embedding')
    return LocalE5(location)
