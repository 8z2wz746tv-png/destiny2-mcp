"""审计里的"哪个宿主在调"（C 件事）—— 纯函数守门 + 落盘断言。

为什么要有它：2026-10-09 排查"这批反复是谁发的"时只能按时间段猜、猜错两次。审计原先只记
工具/参数/耗时/结果，**分不出宿主** → 修复效果没法按宿主验收。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from mcp.types import Implementation

from destiny_mcp.audit import AuditLogger, client_label


def _session(name: str = "", version: str = "") -> SimpleNamespace:
    """真形状：会话上的 `client_params.clientInfo` 就是 SDK 的 `Implementation`。"""
    info = Implementation(name=name, version=version) if name else None
    return SimpleNamespace(client_params=SimpleNamespace(clientInfo=info))


def test_client_label_reads_the_handshake_identity() -> None:
    assert client_label(_session("doubao-connector", "1.2")) == "doubao-connector/1.2"
    # 只报了名字也能用（版本是可选的）
    assert client_label(_session("codex-mcp", "")) == "codex-mcp"


def test_client_label_is_empty_when_there_is_nothing_to_read() -> None:
    """读不到就给空串 —— **不编 "unknown"**（那会让人以为观测到了宿主）。"""
    assert client_label(None) == ""
    assert client_label(SimpleNamespace()) == ""          # 没有 client_params（握手未完成）
    assert client_label(_session()) == ""                 # 有 client_params 但没有 clientInfo
    assert client_label(SimpleNamespace(client_params=None)) == ""
    # 名字是空白串也算"没读到"
    assert client_label(SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name="  ", version="1")))) == ""


def test_the_audit_record_carries_the_client(tmp_path) -> None:
    """落盘字段名就是 `client`；不传时写空串（旧条目没有该字段也合法，读取侧按缺失处理）。"""
    audit = AuditLogger(base_dir=tmp_path)
    audit.log("inventory_assistant", {"intent": "summary"}, {"ok": True}, 12.0,
              client="doubao-connector/1.2")
    audit.log("build_assistant", {"intent": "find"}, {"ok": True}, 3.0)

    files = sorted(tmp_path.rglob("*.json"))
    assert len(files) == 2, files
    by_tool = {f.name.split("-", 1)[1]: json.loads(f.read_text()) for f in files}
    with_client = by_tool["inventory_assistant.json"]
    without = by_tool["build_assistant.json"]
    assert with_client["client"] == "doubao-connector/1.2"
    assert without["client"] == ""
