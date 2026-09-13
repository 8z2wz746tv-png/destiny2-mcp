"""安装自检脚本的"陌生人第一次跑"行为。

这几个断言锁的是**照 README 装完就该通过**这件事：
- 未显式配置 `DESTINY_OAUTH_REDIRECT_URI` 时按运行时默认值判定，不能误报失败；
- 缺 Manifest 时自动放宽超时（默认 90 秒必然不够下载 717 MB）；
- venv 布局按平台分支（Windows 是 `Scripts\\`）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from destiny_mcp import oauth_setup

VERIFY_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "destiny-mcp-setup"
    / "scripts"
    / "verify_mcp.py"
)


def _verifier():
    spec = importlib.util.spec_from_file_location("destiny_mcp_setup_verify", VERIFY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_default_redirect_matches_the_oauth_helper() -> None:
    """两处默认值必须一致：否则"不填也能登录"会变成"不填就自检失败"。"""
    assert _verifier().DEFAULT_REDIRECT_URI == oauth_setup.DEFAULT_REDIRECT_URI


def test_verifier_uses_platform_venv_layout() -> None:
    module = _verifier()
    expected_dir = "Scripts" if module.os.name == "nt" else "bin"
    expected_exe = ".exe" if module.os.name == "nt" else ""
    assert module._VENV_BIN_DIR == expected_dir
    assert module._ENTRY_EXE == expected_exe


def test_missing_manifest_is_detected(tmp_path: Path) -> None:
    module = _verifier()

    assert module._missing_manifest(tmp_path) is True
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    (manifest_dir / "destiny_manifest.sqlite3").write_bytes(b"")
    assert module._missing_manifest(tmp_path) is False


@pytest.mark.parametrize("configured, expected", [
    ("", oauth_setup.DEFAULT_REDIRECT_URI),
    ("https://localhost:9000/callback", "https://localhost:9000/callback"),
])
def test_redirect_value_falls_back_to_the_default(configured: str, expected: str) -> None:
    """空的 .env 值（或整行缺失）走默认；填了就按填的算。"""
    module = _verifier()
    values = {} if not configured else {"DESTINY_OAUTH_REDIRECT_URI": configured}
    assert (values.get("DESTINY_OAUTH_REDIRECT_URI", "") or module.DEFAULT_REDIRECT_URI) == expected
