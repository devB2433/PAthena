"""Canonical source paths for native tools, without widening the source jail."""
from pathlib import Path

from .config import contained_file


def native_source_path(root: Path, value: str) -> str:
    path = Path(value.replace("\\", "/"))
    if ".." in path.parts:
        raise ValueError("路径超出已登记范围")
    if path.is_absolute():
        path = path.relative_to(root.resolve())
    relative = path.as_posix()
    contained_file(root, relative)
    return relative
