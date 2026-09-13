"""依赖必须带上下界：装出来要能启动。

真实事故（从 GitHub 干净克隆装出来的）：`pip install -e .` 把
`mcp[cli]>=1.27.2` 解析成了 **mcp 2.2.0**，而 2.x 把 `mcp.server.fastmcp`
改名成 `MCPServer`（`from mcp.server.mcpserver import MCPServer`），于是
`destiny_mcp/server.py` 在 import 阶段就炸，自检 `VERIFY_FAILED=MCPError:
Connection closed` —— 陌生人第一次装就是这个结果。

开发机上装着 1.x，所以本地怎么跑都是绿的：这类问题只有"干净环境重新解析依赖"
才会暴露。约束自己带好上界，比事后靠 lock 文件兜底可靠。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_dependency_excludes_the_2_x_rename() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"(mcp\[cli\][^"]*)"', text)
    assert match, "pyproject.toml 里找不到 mcp 依赖声明"
    specifier = match.group(1)
    assert "<2" in specifier, (
        f"mcp 依赖缺少上界：{specifier!r}。mcp 2.x 移除了 mcp.server.fastmcp，"
        "干净环境会装到 2.x 并直接无法启动。"
    )


def test_lockfile_pins_a_1_x_mcp() -> None:
    """lock 文件是"已知能跑"的那一组版本，别跟着 2.x 走。"""
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^mcp==([0-9][^\s]*)", lock, re.MULTILINE)
    assert pinned, "requirements.lock.txt 里找不到 mcp 的钉版"
    assert pinned.group(1).startswith("1."), f"lock 里的 mcp 应该是 1.x，实际 {pinned.group(1)}"


def test_server_imports_the_v1_fastmcp_api() -> None:
    """跑起来的这套代码确实用 v1 的 FastMCP；配合上面的上界才是完整约束。"""
    source = (ROOT / "destiny_mcp" / "server.py").read_text(encoding="utf-8")
    assert "from mcp.server.fastmcp import FastMCP" in source
