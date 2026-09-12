#!/usr/bin/env python3
"""录武器基线：改动前把"现在返回什么"完整存下来。

重构会改 JSON 键名，因此需要一份"改动前快照"作为对照：
- 每个用例一次真实 MCP 调用，落盘完整响应 + 耗时 + 体积；
- 后续用 `scripts/diff_weapon_baseline.py` 生成差异报告，
  硬门槛是"任何消失的字段都要有理由"。

用法：
    .venv/bin/python scripts/capture_weapon_baseline.py                  # 存到默认目录
    .venv/bin/python scripts/capture_weapon_baseline.py --out /tmp/before
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "baselines" / "weapon_responses"

# 覆盖：武器全部只读 intent、两个内部消费者（社区配装匹配 / 商人愿单标记）、
# 三种稀有度形态、固定与随机两类武器、以及 inventory.type 的边界。
CASES: list[tuple[str, str, dict]] = [
    # 目录与列表
    ("catalog_perk", "weapon_assistant", {"intent": "catalog", "perk_name": "萤火虫"}),
    ("catalog_type", "weapon_assistant", {"intent": "catalog", "weapon_type": "手炮", "limit": 5}),
    ("type_list", "weapon_assistant", {"intent": "type", "weapon_type": "手炮", "limit": 5}),
    ("filter_rolls_owned", "weapon_assistant", {"intent": "filter_rolls", "weapon_name": "刚玉战锤"}),
    ("filter_rolls_catalog", "weapon_assistant", {"intent": "filter_rolls", "weapon_type": "微型冲锋枪", "include_inventory": False, "limit": 5}),
    ("inventory_type", "inventory_assistant", {"intent": "type", "type_name": "手炮"}),
    # 单把武器：随机 roll（传说）/ 固定 perk（异域）/ 可锻造异域 / 蓝 / 白
    ("info_legendary", "weapon_assistant", {"intent": "info", "weapon_name": "遗产"}),
    ("info_exotic", "weapon_assistant", {"intent": "info", "weapon_name": "泰拉巴"}),
    ("info_rare", "weapon_assistant", {"intent": "info", "weapon_name": "Ψ卷云II"}),
    ("info_common", "weapon_assistant", {"intent": "info", "weapon_name": "刚愎自用"}),
    ("stats_legendary", "weapon_assistant", {"intent": "stats", "weapon_name": "遗产"}),
    ("perk_pool_legendary", "weapon_assistant", {"intent": "perk_pool", "weapon_name": "遗产"}),
    ("perk_pool_exotic", "weapon_assistant", {"intent": "perk_pool", "weapon_name": "泰拉巴"}),
    ("perk_pool_craftable", "weapon_assistant", {"intent": "perk_pool", "weapon_name": "枯骨鳞片"}),
    ("analyze_legendary", "weapon_assistant", {"intent": "analyze", "weapon_name": "遗产"}),
    ("analyze_exotic", "weapon_assistant", {"intent": "analyze", "weapon_name": "泰拉巴"}),
    ("god_roll_legendary", "weapon_assistant", {"intent": "god_roll", "weapon_name": "遗产"}),
    ("god_roll_exotic", "weapon_assistant", {"intent": "god_roll", "weapon_name": "泰拉巴"}),
    ("catalyst_legendary", "weapon_assistant", {"intent": "catalyst", "weapon_name": "遗产"}),
    ("catalyst_exotic", "weapon_assistant", {"intent": "catalyst", "weapon_name": "牵引器火炮"}),
    ("popularity", "weapon_assistant", {"intent": "popularity", "weapon_name": "遗产"}),
    ("compare", "weapon_assistant", {"intent": "compare", "weapon_name": "挽歌"}),
    ("perk_description", "weapon_assistant", {"intent": "perk_description", "perk_name": "狂暴"}),
    ("weapon_community", "weapon_assistant", {"intent": "community", "weapon_name": "辉耀炽热"}),
    # 内部消费者：社区配装的库存匹配、商人商品愿单标记
    ("community_builds", "build_assistant", {"intent": "community", "character": "hunter", "top_n": 2}),
    ("vendor_banshee", "world_assistant", {"intent": "vendor", "vendor_name": "班西-44", "limit": 5}),
]


async def _call(session: ClientSession, tool: str, args: dict) -> dict:
    started = time.perf_counter()
    result = await session.call_tool(tool, args)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    text = ""
    for item in result.content:
        candidate = getattr(item, "text", None)
        if candidate:
            text = candidate
            break
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {"_protocol_error": True, "raw": text[:2000]}
    payload["_case"] = {
        "tool": tool,
        "arguments": args,
        "elapsed_ms": elapsed_ms,
        "payload_chars": len(text),
        "is_error": bool(result.isError),
    }
    return payload


async def capture(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(command=str(ROOT / ".venv" / "bin" / "destiny-mcp"), args=[])
    index: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for case_id, tool, args in CASES:
                try:
                    payload = await _call(session, tool, args)
                except Exception as exc:  # noqa: BLE001 - 基线要记下任何失败形态
                    payload = {
                        "_case": {"tool": tool, "arguments": args, "raised": type(exc).__name__},
                        "_exception": str(exc)[:500],
                    }
                (out_dir / f"{case_id}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                meta = payload.get("_case", {})
                index.append(
                    {
                        "case": case_id,
                        "tool": tool,
                        "ok": payload.get("ok"),
                        "error_code": (payload.get("error") or {}).get("code"),
                        "elapsed_ms": meta.get("elapsed_ms"),
                        "payload_chars": meta.get("payload_chars"),
                    }
                )
                print(
                    f"{case_id:24s} ok={payload.get('ok')!s:5s} "
                    f"{meta.get('elapsed_ms')}ms {meta.get('payload_chars')}B "
                    f"code={(payload.get('error') or {}).get('code')}"
                )
    (out_dir / "index.json").write_text(
        json.dumps({"cases": index}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total_ms = sum(item["elapsed_ms"] or 0 for item in index)
    total_chars = sum(item["payload_chars"] or 0 for item in index)
    print(f"\n共 {len(index)} 例；合计 {total_ms / 1000:.1f}s，{total_chars // 1024} KB；落盘 {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    return asyncio.run(capture(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
