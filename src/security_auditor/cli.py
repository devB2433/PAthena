import argparse


def main():
    parser = argparse.ArgumentParser(description="安全设计与静态分析工作台")
    parser.add_argument("command", choices=["serve", "gateway", "doctor", "prepare-standard"], default="serve", nargs="?")
    parser.add_argument("--port", type=int)
    parser.add_argument('--standard-pack')
    parser.add_argument('--model-dir')
    parser.add_argument('--reranker-dir')
    args = parser.parse_args()
    if args.command == 'prepare-standard':
        import json
        from pathlib import Path
        from .config import Settings
        from .embeddings import encoder
        from .standard_controls import preparation
        from .retrieval_models import reranker
        settings = Settings()
        ranker_dir = args.reranker_dir or settings.reranker_model_dir
        if args.reranker_dir and not (Path(ranker_dir) / 'model-identity.json').is_file():
            raise ValueError('指定的重排模型尚未完整预置')
        ranker = reranker(ranker_dir) if (Path(ranker_dir) / 'model-identity.json').is_file() else None
        result = preparation(Path(args.standard_pack or settings.standard_pack),
                             encoder(args.model_dir or settings.embedding_model_dir), ranker)
        print(json.dumps({k: result[k] for k in ('catalog_id', 'control_count', 'vector_count')}))
        return
    if args.command == "doctor":
        from .config import Settings
        from .skills import SkillLoader

        settings = Settings()
        print(f"技能包校验通过：{len(SkillLoader(settings.skills_dir).catalog())} 个")
        print(f"标准包：{'已配置' if settings.standard_pack else '尚未配置，不生成合规结论'}")
        print(f"本地解析模型：{'已配置' if settings.docling_models else '尚未配置 PDF / OCR 模型'}")
        return
    import uvicorn

    target = "security_auditor.gateway:app" if args.command == "gateway" else "security_auditor.api:app"
    uvicorn.run(target, host="127.0.0.1", port=args.port or (8081 if args.command == "gateway" else 8080))


if __name__ == "__main__":
    main()
