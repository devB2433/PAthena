from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from .config import Settings, contained_file
from .skills import digest
from .store import Store


SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__", "target", ".idea"}
CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".scala", ".vue",
    ".svelte", ".html", ".css", ".scss", ".sql", ".sh", ".conf",
}
CODE_SKIP_DIRS = {"docs", "documents", "samples", "data", "models", "uploads", "reports", "test-results"}


def repository_inventory(root: Path, max_files: int, *, code_only: bool = False) -> list[dict]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("仓库挂载不可读或为符号链接")
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".") and not (Path(directory) / d).is_symlink()
            and (not code_only or d not in CODE_SKIP_DIRS)
        )
        for name in sorted(names):
            path = Path(directory) / name
            if name.startswith("."):
                continue
            if code_only and path.suffix.lower() not in CODE_SUFFIXES and name != "Dockerfile":
                continue
            if path.is_symlink() or path.stat().st_nlink > 1:
                continue
            if path.stat().st_size > 1024 * 1024:
                files.append({"path": str(path.relative_to(root)), "status": "OVERSIZED"})
                if len(files) > max_files:
                    raise ValueError("仓库文件库存超出当前限额")
                continue
            raw = path.read_bytes()
            if b"\x00" in raw[:1024]:
                continue
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": digest(raw),
                    "status": "READABLE",
                }
            )
            if len(files) > max_files:
                raise ValueError("仓库文件库存超出当前限额，请缩小已登记分析范围")
    return files


def extract_document(path: Path, settings: Settings) -> list[dict]:
    if path.suffix.lower() in {".md", ".txt"}:
        return [
            {
                "locator": f"{path.name}:1",
                "text": path.read_text(encoding="utf-8"),
                "metadata": {"parser": "utf8", "diagram_coverage": "NOT_ANALYZED"},
            }
        ]
    if path.suffix.lower() in {".docx", ".pptx"}:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                raise ValueError("Office 文件解压量超出限制")
    if path.suffix.lower() not in {".docx", ".pdf", ".pptx"}:
        raise ValueError("当前解析器支持 DOCX、PDF、PPTX、Markdown 和文本")
    if path.suffix.lower() == ".pdf" and not settings.docling_models:
        raise ValueError("PDF 解析需要预置 Docling 本地模型目录")
    env = {key: value for key, value in os.environ.items() if "KEY" not in key and "TOKEN" not in key}
    env.update({"HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "DO_NOT_TRACK": "1"})
    output_path = settings.state_dir / "parser-results" / f"{digest(path.read_bytes())}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "security_auditor.document_worker",
        str(path),
        str(output_path),
        settings.docling_models,
    ]
    result = subprocess.run(command, env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("Docling 提取失败，请检查解析依赖、本地模型和文件格式")
    return json.loads(output_path.read_text())["blocks"]


def ingest_run(store: Store, run_id: str, settings: Settings) -> list[str]:
    run = store.run(run_id)
    gaps = []
    code_only = run["mode"] == "code_only"
    if code_only and (run["snapshot"].get("assets") or run["snapshot"].get("standard")):
        raise ValueError("仅代码模式不得包含文档或标准")
    for asset in run["snapshot"]["assets"]:
        path = Path(asset["path"])
        if digest(path.read_bytes()) != asset["sha256"]:
            raise ValueError("原始材料与输入快照不一致")
        try:
            for block in extract_document(path, settings):
                text = block["text"]
                # Bounded blocks retain explicit line ranges for large plain text inputs.
                lines = text.splitlines()
                for start in range(0, len(lines), 100):
                    chunk = "\n".join(lines[start : start + 100])
                    store.add_evidence(
                        run_id,
                        "document",
                        f"{asset['name']} / {block['locator']} / {start + 1}",
                        chunk,
                        {
                            **block["metadata"],
                            "asset_id": asset["id"],
                            "source_hash": asset["sha256"],
                            "line_start": start + 1,
                        },
                    )
        except (ValueError, subprocess.TimeoutExpired, UnicodeError, zipfile.BadZipFile):
            gaps.append(f"{asset['name']} 未能完整解析；需要检查解析依赖、模型或文件质量")
    continuation = run['mode'] == 'implementation_only'
    if not code_only and not continuation and not store.evidence(run_id, "document"):
        raise ValueError("没有可分析的设计文本")
    repository = run["snapshot"].get("repository")
    if repository:
        root = Path(repository["root"])
        for entry in repository["files"]:
            if entry["status"] != "READABLE":
                gaps.append(f"{entry['path']} 超过单文件读取限额")
                continue
            path = contained_file(root, entry["path"])
            raw = path.read_bytes()
            if digest(raw) != entry["sha256"]:
                raise ValueError("仓库已改变，请创建新的输入快照")
            try:
                lines = raw.decode().splitlines()
            except UnicodeError:
                gaps.append(f"{entry['path']} 不是可分析的 UTF-8 文本")
                continue
            for start in range(0, len(lines), 100):
                store.add_evidence(
                    run_id,
                    "code",
                    f"{entry['path']}:{start + 1}-{min(start + 100, len(lines))}",
                    "\n".join(lines[start : start + 100]),
                    {
                        "path": entry["path"],
                        "line_start": start + 1,
                        "source_hash": entry["sha256"],
                        "index_precision": "LEXICAL_ONLY",
                    },
                )
        gaps.append("当前代码索引为词法证据块；动态调用、完整调用图与语义索引尚未验证")
    standard = run["snapshot"].get("standard")
    if standard:
        if standard.get('library_id'):
            from .standard_library import version
            version(store, run_id)
            return gaps  # Shared structured data is read on demand, not copied in full per project.
        pack = Path(standard["root"])
        raw = contained_file(pack, "clauses.jsonl").read_bytes()
        if digest(raw) != standard["clauses_hash"]:
            raise ValueError("标准包与输入快照不一致")
        if standard.get("source_file"):
            if digest(contained_file(pack, standard["source_file"]).read_bytes()) != standard["source_hash"]:
                raise ValueError("标准原始 PDF 与输入快照不一致")
        if standard.get("contexts_hash"):
            contexts_raw = contained_file(pack, "contexts.jsonl").read_bytes()
            if digest(contexts_raw) != standard["contexts_hash"]:
                raise ValueError("标准上下文与输入快照不一致")
            for line in contexts_raw.decode().splitlines():
                context = json.loads(line)
                store.add_evidence(run_id, "standard", context["id"], context["text"],
                                   {"context_id": context["id"], "source": context["source"],
                                    "record_type": "context", "version": "4.0.1"})
        for line in raw.decode().splitlines():
            clause = json.loads(line)
            store.add_evidence(
                run_id,
                "standard",
                clause["id"],
                clause["text"],
                {"clause_id": clause["id"], "source": clause["source"], "version": "4.0.1",
                 "record_type": "requirement", "context_ids": clause["source"].get("context_ids", [])
                 if isinstance(clause["source"], dict) else []},
            )
    elif not code_only and not continuation:
        gaps.append("未导入 PCI DSS 标准包；本次不提供条款覆盖或合规结论")
    return gaps


def standard_manifest(root: Path, *, include_prepared: bool = True) -> dict:
    manifest_raw = contained_file(root, "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get("id") != "PCI_DSS" or manifest.get("version") != "4.0.1" or manifest.get("fixture"):
        raise ValueError("标准包必须是正式 PCI DSS v4.0.1 包")
    raw = contained_file(root, "clauses.jsonl").read_bytes()
    if digest(raw) != manifest.get("clauses_sha256"):
        raise ValueError("标准包内容摘要不匹配")
    clauses = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    ids = [clause["id"] for clause in clauses]
    if not ids or len(ids) != len(set(ids)) or sorted(ids) != sorted(manifest["requirement_ids"]):
        raise ValueError("标准条款库存不匹配")
    if any(not clause.get("source") or not clause.get("text") for clause in clauses):
        raise ValueError("标准条款缺少原文或来源")
    result = {
        "root": str(root.resolve()),
        "manifest_hash": digest(manifest_raw),
        "clauses_hash": digest(raw),
        "requirement_ids": ids,
        "version": "4.0.1",
    }
    if manifest.get("source"):
        source = manifest["source"]
        if digest(contained_file(root, source["file"]).read_bytes()) != source["sha256"]:
            raise ValueError("标准原始 PDF 摘要不匹配")
        result.update({"source_file": source["file"], "source_hash": source["sha256"]})
    if manifest.get("contexts_sha256"):
        context_raw = contained_file(root, "contexts.jsonl").read_bytes()
        if digest(context_raw) != manifest["contexts_sha256"]:
            raise ValueError("标准上下文摘要不匹配")
        contexts = [json.loads(line) for line in context_raw.decode().splitlines() if line.strip()]
        context_ids = [c["id"] for c in contexts]
        if len(context_ids) != len(set(context_ids)) or sorted(context_ids) != sorted(manifest["context_ids"]):
            raise ValueError("标准上下文库存不匹配")
        if any(not c.get("text") or not c.get("source") for c in contexts):
            raise ValueError("标准上下文缺少原文或来源")
        if set(context_ids) & set(ids) or any(
            set(c["source"].get("context_ids", [])) - set(context_ids) for c in clauses
        ):
            raise ValueError("标准上下文引用不匹配")
        result.update({"contexts_hash": digest(context_raw), "context_ids": context_ids})
    if include_prepared and (root / 'prepared.json').is_file():
        prepared = json.loads(contained_file(root, 'prepared.json').read_text())
        if any(prepared.get(k) != result.get(k) for k in ('manifest_hash', 'clauses_hash', 'contexts_hash')):
            raise ValueError('预生成控制目录与标准版本不一致')
        result['prepared'] = prepared
    return result
