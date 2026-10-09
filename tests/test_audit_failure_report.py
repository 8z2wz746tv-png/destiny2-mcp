"""失败率报表的口径守门（D 件事）—— 夹具是**真审计条目的形状**。

最容易坏的是 `_payload`：审计里的 `result_summary` 是 `TextContent(type='text', text='{…}')`
的 **Python repr**，里面还有转义与中文。今天在临时脚本里为它写错过三次解析，所以单独钉住。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from audit_failure_report import _payload, load_records, main, summarize  # noqa: E402


def _text_content(payload: dict) -> str:
    """造出真形状：`TextContent` 的 repr（json 带缩进、中文不转义）。"""
    return (
        "[TextContent(type='text', text="
        + repr(json.dumps(payload, ensure_ascii=False, indent=2))
        + ", annotations=None, meta=None)]"
    )


def _write(day_dir: pathlib.Path, name: str, record: dict) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _entry(ts: str, tool: str, arguments: dict, payload: dict | None, *, client: str = "") -> dict:
    record = {
        "timestamp": ts,
        "tool": tool,
        "arguments": arguments,
        "duration_ms": 12.0,
        "error": None,
    }
    if client:
        record["client"] = client
    record["result_summary"] = _text_content(payload) if payload is not None else ""
    return record


def test_payload_reads_the_real_repr_with_escapes_and_chinese() -> None:
    """真条目长这样：`text='{\\n  "ok": false, …}'`，里面有转义与中文。"""
    body = {"ok": False, "error": {"code": "move_failed", "message": "目标位置空间不足：先清出位置。"}}
    assert _payload(_text_content(body)) == body
    # 老条目/空摘要 → None（读取侧按缺失处理，不回填）
    assert _payload("") is None
    assert _payload(None) is None
    assert _payload("[TextContent(type='text', text='不是 JSON', annotations=None)]") is None


def test_summarize_counts_rate_clients_and_samples(tmp_path, monkeypatch) -> None:
    import audit_failure_report as report

    monkeypatch.setattr(report, "AUDIT_ROOT", tmp_path)
    day = tmp_path / "20261009"
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat()
    _write(day, "000001-inventory_assistant.json",
           _entry(stamp, "inventory_assistant", {"intent": "move", "item_name": "X"},
                  {"ok": False, "error": {"code": "move_failed", "message": "空间不足"}, "next_actions": []},
                  client="doubao-connector/1.2"))
    _write(day, "000002-build_assistant.json",
           _entry(stamp, "build_assistant", {"intent": "find"},
                  {"ok": False, "error": {"code": "move_failed", "message": "又一条"}}, client="doubao-connector/1.2"))
    _write(day, "000003-inventory_assistant.json",
           _entry(stamp, "inventory_assistant", {"intent": "summary"}, {"ok": True}, client="dsh-mcp-client/0.0.1"))
    # 老条目：没有 client 字段
    _write(day, "000004-inventory_assistant.json",
           _entry(stamp, "inventory_assistant", {"intent": "summary"}, {"ok": True}))

    rows = load_records(1)
    assert len(rows) == 4
    report_data = summarize(rows)
    assert report_data["total"] == 4 and report_data["failed"] == 2
    assert report_data["failure_rate"] == 50.0
    assert report_data["by_client"]["doubao-connector/1.2"] == {"calls": 2, "failed": 2, "rate": 100.0}
    assert report_data["by_client"]["dsh-mcp-client/0.0.1"]["failed"] == 0
    # 没记客户端的旧条目单独一档：不许算进任何真实宿主，也不许被丢掉
    assert report_data["by_client"]["（未记录）"]["calls"] == 1
    top = report_data["top_codes"][0]
    assert top["code"] == "move_failed" and top["count"] == 2
    assert top["sample"]["tool"] == "inventory_assistant"
    assert top["sample"]["intent"] == "move"
    assert top["sample"]["arguments"] == {"item_name": "X"}
    assert top["sample"]["message"] == "空间不足"


def test_json_mode_is_machine_readable(tmp_path, monkeypatch, capsys) -> None:
    import audit_failure_report as report

    monkeypatch.setattr(report, "AUDIT_ROOT", tmp_path)
    assert main(["--days", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"current", "previous"}
    assert out["current"]["total"] == 0 and out["current"]["failure_rate"] == 0.0
