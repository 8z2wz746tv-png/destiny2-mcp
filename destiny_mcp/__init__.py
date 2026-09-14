"""Destiny MCP Server — Destiny 2 item management via MCP protocol."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path


def _source_tree_version() -> str | None:
    """源码树里的版本：直接读 pyproject.toml（安装后没有这个文件，返回 None）。

    以前这里是写死的 `__version__ = "0.1.0"`：发到 0.1.8 了包里还写着 0.1.0
    （干净安装冒烟时才被发现）。改成"源码树读 pyproject、安装后读发行版元数据"，
    两边都不会漂，`tests/test_package_version.py` 把这条钉住。
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


def _resolve_version() -> str:
    return _source_tree_version() or _installed_version()


def _installed_version() -> str:
    try:
        return _dist_version("destiny-mcp")
    except PackageNotFoundError:  # pragma: no cover - 源码树且未安装
        return "0.0.0+unknown"


__version__ = _resolve_version()
