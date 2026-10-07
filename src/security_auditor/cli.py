import argparse


def main():
    parser = argparse.ArgumentParser(description="安全设计与静态分析工作台")
    parser.add_argument("command", choices=["serve", "gateway", "doctor"], default="serve", nargs="?")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
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
