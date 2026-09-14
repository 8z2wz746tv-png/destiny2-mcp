"""版本号只有一处真相：pyproject.toml。

实机教训（0.1.8 干净安装冒烟）：包内 `__version__` 写死在 0.1.0，发到 0.1.8 了还写着旧值。
现在源码树读 pyproject、安装后读发行版元数据，这里把"两边一致"钉住。
"""

from __future__ import annotations

import re
from pathlib import Path

import destiny_mcp

ROOT = Path(__file__).parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert match, "pyproject.toml 里找不到 version"
    return match.group(1)


def test_package_version_matches_pyproject() -> None:
    assert destiny_mcp.__version__ == _pyproject_version()


def test_source_tree_version_reads_pyproject_not_a_hardcoded_string() -> None:
    assert destiny_mcp._source_tree_version() == _pyproject_version()


def test_installed_version_falls_back_when_metadata_is_missing() -> None:
    """安装后的分支要能兜住"没装成发行版"的情况，而不是抛异常。"""
    from importlib.metadata import PackageNotFoundError

    import pytest

    original = destiny_mcp._dist_version

    def missing(_name: str) -> str:
        raise PackageNotFoundError(_name)

    destiny_mcp._dist_version = missing  # type: ignore[assignment]
    try:
        assert destiny_mcp._installed_version() == "0.0.0+unknown"
    finally:
        destiny_mcp._dist_version = original  # type: ignore[assignment]
    assert pytest is not None
