#!/usr/bin/env python
"""把「一次装备链路」的墙钟时间拆成三笔：服务端 / 模型回合 / 用户确认。

数据源是 `~/.destiny_mcp/audit/<YYYYMMDD>/*.json`（每次 MCP 调用一条，含 `timestamp`(UTC)
与 `duration_ms`）。它只覆盖**走 MCP 传输**的调用（DSH/GUI/别的 agent 都算），
豆包那台机器有自己的审计目录，不在本机。

口径（别读歪）：

- **服务端** = 每条 `duration_ms` 之和；
- **间隔** = 上一条结束到下一条开始（墙钟 − 服务端）。这里面**混着**模型思考/生成、
  宿主的往返、以及"用户看了一眼说确认"的时间 —— 审计分不开它们，
  所以只能说"链路里有多少时间不在服务端手里"，不能说"模型想了多久"。

用法：

    .venv/bin/python scripts/audit_chain_timing.py            # 最近 40 次装备所在链路
    .venv/bin/python scripts/audit_chain_timing.py --gap 900  # 换"同一条链"的间隔阈值（秒）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys
from typing import Any

AUDIT_ROOT = pathlib.Path.home() / ".destiny_mcp" / "audit"


def _load(days: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in sorted(AUDIT_ROOT.iterdir())[-days:]:
        if not day.is_dir():
            continue
        for path in sorted(day.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            stamp = data.get("timestamp")
            if not stamp:
                continue
            arguments = data.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = eval(arguments)  # 审计里是 repr：只读本地文件
                except (ValueError, SyntaxError):
                    arguments = {}
            rows.append({
                "start": dt.datetime.fromisoformat(stamp),
                "ms": float(data.get("duration_ms") or 0),
                "tool": str(data.get("tool") or ""),
                "intent": (arguments or {}).get("intent"),
                "ok": data.get("error") in (None, "None"),
            })
    return sorted(rows, key=lambda row: row["start"])


def _chains(rows: list[dict[str, Any]], gap_seconds: int) -> list[list[dict[str, Any]]]:
    chains: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if current:
            previous_end = current[-1]["start"] + dt.timedelta(milliseconds=current[-1]["ms"])
            if (row["start"] - previous_end).total_seconds() > gap_seconds:
                chains.append(current)
                current = []
        current.append(row)
    if current:
        chains.append(current)
    return chains


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap", type=int, default=600, help="同一条链的最大调用间隔（秒）")
    parser.add_argument("--show", type=int, default=6, help="打印几条最近的装备链路")
    args = parser.parse_args()

    rows = _load()
    if not rows:
        print("审计目录里没有记录")
        return 1

    equip_chains = [
        chain for chain in _chains(rows, args.gap)
        if any(row["tool"] == "build_assistant" and row["intent"] == "equip_build" for row in chain)
    ]
    if not equip_chains:
        print("没找到含 equip_build 的链路")
        return 1

    print(f"审计记录 {len(rows)} 条；含 equip_build 的链路 {len(equip_chains)} 条")
    print(f"{'时间':16s} {'调用':>4s} {'墙钟':>8s} {'服务端':>8s} {'间隔':>8s}  明细")
    for chain in equip_chains[-args.show:]:
        server = sum(row["ms"] for row in chain) / 1000
        start = chain[0]["start"]
        end = chain[-1]["start"] + dt.timedelta(milliseconds=chain[-1]["ms"])
        wall = (end - start).total_seconds()
        gap = wall - server
        detail = "、".join(
            f"{row['tool'].replace('_assistant', '')}"
            f"{'/' + str(row['intent']) if row['intent'] else ''}:{row['ms'] / 1000:.0f}s"
            for row in chain
        )
        print(f"{start.strftime('%m-%d %H:%M:%S'):16s} {len(chain):4d} {wall:7.1f}s {server:7.1f}s {gap:7.1f}s  {detail}")

    walls, servers, gaps, counts = [], [], [], []
    for chain in equip_chains:
        server = sum(row["ms"] for row in chain) / 1000
        end = chain[-1]["start"] + dt.timedelta(milliseconds=chain[-1]["ms"])
        wall = (end - chain[0]["start"]).total_seconds()
        walls.append(wall)
        servers.append(server)
        gaps.append(wall - server)
        counts.append(len(chain))

    print("\n=== 全部装备链路（中位 / 最大）===")
    for label, values in (("墙钟", walls), ("服务端", servers), ("间隔(模型+用户)", gaps)):
        print(f"  {label:16s} p50={statistics.median(values):7.1f}s  max={max(values):7.1f}s")
    print(f"  {'调用次数':16s} p50={statistics.median(counts):7.0f}    max={max(counts):7.0f}")
    share = statistics.median(gaps) / max(1.0, statistics.median(walls))
    print(f"\n中位链路里 **{share:.0%}** 的墙钟时间不在服务端手里"
          "（模型回合 + 宿主往返 + 用户确认，审计分不开这三者）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
