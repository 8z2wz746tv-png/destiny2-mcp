#!/usr/bin/env python
"""失败率报表：**一条命令看清"模型在哪儿反复"**（D 件事，只读）。

为什么要有它：2026-10-09 全量审计 5407 次调用、**1405 次失败（25%）**，这些失败烧掉约 21.5 小时，
其中 87% 是"失败之后模型那一段"。以前是"疼了才去查"，修完一次过几天又反弹 —— 因为没有基线可比。

看什么：
- **失败率**（总 + 按宿主）—— 宿主名来自 MCP 握手（2026-10-09 起记录；更早的条目算"未记录"）；
- **前十错误码** + **环比上一周期** —— 反弹当天就能看见；
- 每个错误码一条**真实样例**（工具、intent、参数、原文前 120 字）—— 不用再翻审计文件。

用法：
    python scripts/audit_failure_report.py                # 最近 7 天，与上一个 7 天比
    python scripts/audit_failure_report.py --days 30 --top 15
    python scripts/audit_failure_report.py --json          # 给 CI / 别的脚本吃

只读：不写账号、不改审计。
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import pathlib
import sys
from collections import Counter
from typing import Any

AUDIT_ROOT = pathlib.Path.home() / ".destiny_mcp" / "audit"


def _payload(summary: Any) -> dict[str, Any] | None:
    """审计里的 `result_summary` 是 `TextContent(...)` 的 repr，从里面取出那段 JSON。

    为什么不用 `literal_eval` 整段：`TextContent` 不是字面量，会直接抛。所以先按 Python 字符串
    字面量的规则（尊重转义）切出 `text=` 那一段，再当 JSON 读。
    """
    if not isinstance(summary, str):
        return None
    i = summary.find("text=")
    if i < 0:
        return None
    i += len("text=")
    if i >= len(summary) or summary[i] not in "'\"":
        return None
    quote, j, escaped = summary[i], i + 1, False
    while j < len(summary):
        char = summary[j]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            break
        j += 1
    try:
        return json.loads(ast.literal_eval(summary[i : j + 1]))
    except (ValueError, SyntaxError):
        return None


def load_records(days: int, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """读最近 `days` 天的审计条目（按时间升序）。缺字段的老条目按缺失处理，不回填。"""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    rows: list[dict[str, Any]] = []
    if not AUDIT_ROOT.is_dir():
        return rows
    for day in sorted(AUDIT_ROOT.iterdir()):
        if not day.is_dir():
            continue
        for path in sorted(day.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            try:
                timestamp = dt.datetime.fromisoformat(record.get("timestamp", ""))
            except ValueError:
                continue
            if timestamp < cutoff:
                continue
            rows.append({
                "timestamp": timestamp,
                "tool": record.get("tool", ""),
                "arguments": record.get("arguments") or {},
                "client": record.get("client", ""),
                "payload": _payload(record.get("result_summary")),
            })
    rows.sort(key=lambda row: row["timestamp"])
    return rows


def summarize(rows: list[dict[str, Any]], top: int = 10) -> dict[str, Any]:
    """把条目压成一张表：失败率（总/按宿主）+ 前十错误码 + 每个码一条样例。"""
    total = len(rows)
    failures = [row for row in rows if (row["payload"] or {}).get("ok") is False]
    codes: Counter[str] = Counter()
    samples: dict[str, dict[str, Any]] = {}
    by_client: dict[str, list[int]] = {}
    for row in rows:
        client = row["client"] or "（未记录）"
        slot = by_client.setdefault(client, [0, 0])
        slot[0] += 1
        payload = row["payload"]
        if not payload or payload.get("ok") is not False:
            continue
        slot[1] += 1
        error = payload.get("error") or {}
        code = str(error.get("code") or "（无码）")
        codes[code] += 1
        samples.setdefault(code, {
            "tool": row["tool"],
            "intent": (row["arguments"] or {}).get("intent", ""),
            "arguments": {
                k: v for k, v in (row["arguments"] or {}).items() if k != "intent"
            },
            "message": str(error.get("message") or "")[:120],
            "next_actions": len(payload.get("next_actions") or []),
        })
    return {
        "total": total,
        "failed": len(failures),
        "failure_rate": round(len(failures) / total * 100, 1) if total else 0.0,
        "by_client": {
            client: {"calls": calls, "failed": failed,
                     "rate": round(failed / calls * 100, 1) if calls else 0.0}
            for client, (calls, failed) in sorted(by_client.items(), key=lambda kv: -kv[1][0])
        },
        "top_codes": [
            {"code": code, "count": count, "sample": samples[code]}
            for code, count in codes.most_common(top)
        ],
    }


def _render(report: dict[str, Any], *, days: int, previous: dict[str, Any]) -> str:
    lines = [
        f"审计最近 {days} 天：{report['total']} 次调用，失败 {report['failed']} "
        f"（{report['failure_rate']}%）",
    ]
    if previous["total"]:
        delta = report["failure_rate"] - previous["failure_rate"]
        lines.append(
            f"上一个 {days} 天：{previous['total']} 次、失败 {previous['failed']} "
            f"（{previous['failure_rate']}%）→ 环比 {delta:+.1f} 个百分点"
        )
    else:
        lines.append(f"上一个 {days} 天：没有可比数据（这是第一份基线）")
    lines.append("")
    lines.append("按宿主（client 来自 MCP 握手；老条目没有这个字段）:")
    for client, stat in report["by_client"].items():
        lines.append(f"  {stat['calls']:>5} 次  失败 {stat['failed']:>4}（{stat['rate']:>5}%）  {client}")
    lines.append("")
    lines.append(f"前十错误码（{len(report['top_codes'])} 条）:")
    previous_codes = {row["code"]: row["count"] for row in previous["top_codes"]}
    for row in report["top_codes"]:
        before = previous_codes.get(row["code"], 0)
        arrow = f"（环比 {row['count'] - before:+d}）" if previous["total"] else ""
        sample = row["sample"]
        lines.append(f"  {row['count']:>5}  {row['code']:<28}{arrow}")
        lines.append(
            f"         样例：{sample['tool']}({sample['intent']}) "
            f"{json.dumps(sample['arguments'], ensure_ascii=False)[:80]}"
        )
        if sample["message"]:
            lines.append(f"         {sample['message']}")
        lines.append(f"         next_actions 条数：{sample['next_actions']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="失败率报表（只读）")
    parser.add_argument("--days", type=int, default=7, help="统计窗口（默认 7 天）")
    parser.add_argument("--top", type=int, default=10, help="错误码取前几名（默认 10）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    current = load_records(args.days, now=now)
    previous_rows = load_records(args.days * 2, now=now - dt.timedelta(days=args.days))
    report = summarize(current, top=args.top)
    previous = summarize(previous_rows, top=args.top)
    if args.json:
        print(json.dumps({"current": report, "previous": previous}, ensure_ascii=False, indent=2))
    else:
        print(_render(report, days=args.days, previous=previous))
    return 0


if __name__ == "__main__":
    sys.exit(main())
