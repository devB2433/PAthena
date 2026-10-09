"""Explicit online installation step; analysis never downloads model resources."""
import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

MODELS = [
    ('Qwen/Qwen3-Embedding-0.6B', '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3',
     'embeddings/qwen3-embedding-0.6b'),
    ('BAAI/bge-reranker-v2-m3', '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e',
     'rerankers/bge-reranker-v2-m3'),
]


def main():
    parser = argparse.ArgumentParser(description='Download pinned local retrieval models before deployment')
    parser.add_argument('--output-dir', type=Path, default=Path('models'))
    args = parser.parse_args()
    for model, revision, directory in MODELS:
        target = args.output_dir / directory
        snapshot_download(repo_id=model, revision=revision, local_dir=target,
            allow_patterns=['config.json', 'model.safetensors', 'tokenizer.json',
                            'tokenizer_config.json', 'special_tokens_map.json',
                            'sentencepiece.bpe.model', 'vocab.json', 'merges.txt', 'README.md', 'LICENSE*'])
        (target / 'revision.txt').write_text(revision + '\n')
        (target / 'model-identity.json').write_text(json.dumps({'model': model, 'revision': revision}) + '\n')
        print(f'Prepared {model} at {target}')


if __name__ == '__main__':
    main()
