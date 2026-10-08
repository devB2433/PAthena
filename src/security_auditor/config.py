from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from .provider import budgets_enabled


ROOT = Path(__file__).resolve().parents[2]


def local_standard_pack() -> str:
    pack = ROOT / "standards/pci-dss-4.0.1"
    return str(pack) if (pack / "manifest.json").is_file() else ""


@dataclass(frozen=True)
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("AUDITOR_STATE_DIR", ROOT / "state")))
    skills_dir: Path = field(default_factory=lambda: Path(os.getenv("AUDITOR_SKILLS_DIR", ROOT / "skills")))
    workflow: Path = field(
        default_factory=lambda: Path(os.getenv("AUDITOR_WORKFLOW", ROOT / "design/workflow.json"))
    )
    web_dist: Path = field(
        default_factory=lambda: Path(os.getenv("AUDITOR_WEB_DIST", ROOT / "apps/web/dist"))
    )
    gateway: str = field(default_factory=lambda: os.getenv("AUDITOR_MODEL_GATEWAY", "http://127.0.0.1:8081"))
    model_id: str = field(default_factory=lambda: os.getenv("AUDITOR_MODEL_ID", "deepseek-flash"))
    repositories: dict[str, str] = field(
        default_factory=lambda: json.loads(os.getenv("AUDITOR_REPOSITORIES", "{}"))
    )
    standard_pack: str = field(default_factory=lambda: os.getenv("AUDITOR_STANDARD_PACK", local_standard_pack()))
    docling_models: str = field(default_factory=lambda: os.getenv("AUDITOR_DOCLING_MODELS", ""))
    language: str = field(default_factory=lambda: os.getenv("AUDITOR_LANGUAGE", "en"))
    enforce_budgets: bool = field(default_factory=budgets_enabled)
    max_requests: int = field(default_factory=lambda: int(os.getenv("AUDITOR_MAX_REQUESTS", "0")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("AUDITOR_MAX_TOKENS", "0")))
    agent_output_tokens: int = field(default_factory=lambda: int(os.getenv("AUDITOR_AGENT_OUTPUT_TOKENS", "16384")))
    agent_max_calls: int = field(default_factory=lambda: int(os.getenv("AUDITOR_AGENT_MAX_CALLS", "0")))
    concurrency: int = field(default_factory=lambda: int(os.getenv("AUDITOR_CONCURRENCY", "2")))
    upload_limit: int = 25 * 1024 * 1024
    code_limit: int = 5000

    def __post_init__(self):
        if self.language not in {"en", "zh-CN"}:
            raise ValueError("语言仅支持 en 或 zh-CN")
        if self.concurrency < 1 or self.upload_limit < 1 or self.code_limit < 1:
            raise ValueError("并发与材料限额必须为正数")
        if self.max_requests < 0 or self.max_tokens < 0:
            raise ValueError("模型预算不能为负数")
        if not 4096 <= self.agent_output_tokens <= 32768:
            raise ValueError("分析角色输出额度必须为 4096 至 32768 tokens")
        if not 0 <= self.agent_max_calls <= 64:
            raise ValueError('单任务模型轮数必须为 0 至 64；0 表示不限轮数')

    @property
    def database(self) -> Path:
        return self.state_dir / "auditor.sqlite3"


def contained_file(root: Path, relative: str) -> Path:
    """Resolve a regular, single-link file within a registered root."""
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("路径超出已登记范围")
    base = root.resolve()
    cursor = base
    candidate = base / relative
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("不读取符号链接")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("路径超出已登记范围")
    if not resolved.is_file() or resolved.stat().st_nlink > 1:
        raise ValueError("材料不是可读取的独立文件")
    return resolved
