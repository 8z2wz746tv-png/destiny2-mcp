#!/usr/bin/env python3
"""录基线：改动前把"现在返回什么"完整存下来（武器面 / 护甲面）。

重构会改 JSON 键名，因此需要一份"改动前快照"作为对照：
- 每个用例一次真实 MCP 调用，落盘完整响应 + 耗时 + 体积；
- 后续用 `scripts/diff_weapon_baseline.py` 生成差异报告，
  硬门槛是"任何消失的字段都要有理由"。

用例可以是一步，也可以是**多步**（护甲的 `farm_target` / `equip_build` 回显要先走
金装确认握手），多步用两种取值指令：
- `{"$replay_candidate": 0}`：把上一步 `candidates[0].arguments` 原样回传（金装确认）；
- `{"$canonical_from": "data.builds[0].canonical_build"}`：从上一步取一段塞进参数。

用法：
    .venv/bin/python scripts/capture_weapon_baseline.py                  # 武器面，存到默认目录
    .venv/bin/python scripts/capture_weapon_baseline.py --surface armor  # 护甲面
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

# 护甲面：P0 先录现状，P1–P5 用它当"字段不许无理由消失"的闸门。
# 覆盖：库存列表（部位/稀有度/仓库）、按名检索、概况、异域护甲列表与详情、
# 套装效果（列表/详情）、护甲模组目录、社区配装的库存核对、反推待刷件、装备确认回显。
ARMOR_CASES: list[tuple[str, object]] = [
    ("armor_get_legs", "inventory_assistant", {"intent": "get", "armor_slot": "legs", "limit": 3}),
    ("armor_get_exotic", "inventory_assistant", {"intent": "get", "armor_slot": "legs", "rarity": "异域", "limit": 2}),
    ("armor_get_vault", "inventory_assistant", {"intent": "get", "location": "vault", "armor_slot": "class_item", "limit": 2}),
    ("armor_search", "inventory_assistant", {"intent": "search", "item_name": "至高狂徒腿铠"}),
    # 单件详情：T5（有词条/调谐/能量）与老护甲（15 槽、无词条原型）各一件
    ("armor_item_t5", "inventory_assistant", {
        "intent": "item", "item_instance_id": "6917530198796768597",
    }),
    ("armor_item_legacy", "inventory_assistant", {
        "intent": "item", "item_instance_id": "6917529740500777594",
    }),
    # 换模组的确认路径（confirmed=false，基线里**不写账号**）
    ("equip_mod_confirm", "inventory_assistant", {
        "intent": "equip_mod", "item_instance_id": "6917530198796768597",
        "mod_name": "手雷模组", "character": "hunter", "confirmed": False,
    }),
    # 无解时的六维阶梯：社区模板那套硬约束（近战70+手雷70）实机就是无解
    ("ladder_no_solution", [
        ("build_assistant", {
            "intent": "find", "character": "hunter", "exotic_name": "快速装弹松身裤",
            "weapons_target": 150, "class_target": 100, "super_target": 80,
            "melee_target": 70, "grenade_target": 70,
            "priority_stats": ["weapons", "class_stat", "super_stat", "melee", "grenade"],
        }),
        ("build_assistant", {"$replay_candidate": 0}),
    ]),
    # 0.1.6：原来那条 `ladder_no_solution`（150/100/80/70/70 + 金装）现在**有解了** ——
    # 调谐补齐只用改 1 件调谐就能达标，所以阶梯不再出现。这条用例专门锁"真的配不出来"
    # 的那条路（`verdict.satisfiable=false` + 逐项 ceiling 的口径说明）。
    ("ladder_infeasible_verdict", "build_assistant", {
        "intent": "find", "character": "hunter",
        "weapons_target": 150, "class_target": 100, "super_target": 80,
        "melee_target": 70, "health_target": 200,
        "priority_stats": ["weapons", "class_stat", "super_stat", "melee"],
    }),
    ("equip_mod_wrong_slot", "inventory_assistant", {
        "intent": "equip_mod", "item_instance_id": "6917530198796768597",
        "mod_name": "手雷快速启动", "character": "hunter", "confirmed": False,
    }),
    ("inventory_summary", "inventory_assistant", {"intent": "summary"}),
    ("exotic_armor_list", "build_assistant", {"intent": "exotic_armor", "character": "hunter"}),
    ("exotic_armor_detail", "build_assistant", {"intent": "exotic_armor", "character": "hunter", "exotic_name": "快速装弹松身裤"}),
    ("set_bonus_list", "build_assistant", {"intent": "set_bonus"}),
    ("set_bonus_detail", "build_assistant", {"intent": "set_bonus", "set_bonus_name": "埃希恩记忆"}),
    ("armor_mods_all", "build_assistant", {"intent": "armor_mods"}),
    ("armor_mods_stat", "build_assistant", {"intent": "armor_mods", "priority_stat": "武器"}),
    ("community_build_inventory", "build_assistant", {
        "intent": "community_build",
        "community_build_id": "builds/s29/00vivy2a-hunter/index.html#build-1",
        "include_inventory": True,
        "character": "hunter",
    }),
    # 两步：金装确认握手 → 反推待刷件
    ("farm_target_armor", [
        ("build_assistant", {
            "intent": "farm_target", "character": "hunter", "exotic_name": "快速装弹松身裤",
            "weapons_target": 150, "class_target": 100, "super_target": 80,
            "melee_target": 70, "grenade_target": 70, "max_replacements": 2,
        }),
        ("build_assistant", {"$replay_candidate": 0}),
    ]),
    # 三步：金装确认握手 → 求解 → 装备确认回显（confirmed=false，不写账号）
    ("equip_confirm_echo", [
        ("build_assistant", {
            "intent": "find", "character": "hunter", "exotic_name": "快速装弹松身裤",
            "weapons_target": 150, "class_target": 100, "super_target": 80,
        }),
        ("build_assistant", {"$replay_candidate": 0}),
        ("build_assistant", {
            "intent": "equip_build", "confirmed": False,
            "$canonical_from": "data.builds[0].canonical_build",
        }),
    ]),
]

SURFACES: dict[str, tuple[Path, list]] = {
    "weapon": (DEFAULT_OUT, CASES),
    "armor": (ROOT / "tests" / "baselines" / "armor_responses", ARMOR_CASES),
}


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


def _as_cases(raw_cases: list) -> list[tuple[str, list[tuple[str, dict]]]]:
    """把两种写法统一成 `(用例 id, 步骤列表)`。

    单步：`(case_id, tool, args)`；多步：`(case_id, [(tool, args), ...])`。
    """
    normalized: list[tuple[str, list[tuple[str, dict]]]] = []
    for case in raw_cases:
        if len(case) == 2 and isinstance(case[1], list):
            normalized.append((case[0], list(case[1])))
        else:
            case_id, tool, args = case
            normalized.append((case_id, [(tool, args)]))
    return normalized


def _resolve(args: dict, previous: dict | None) -> dict:
    """把两种取值指令换成真实参数。"""
    if "$replay_candidate" not in args and "$canonical_from" not in args:
        return args
    if not isinstance(previous, dict):
        raise RuntimeError("取值指令需要上一步的响应，但上一步不存在或不是 JSON")
    resolved = {k: v for k, v in args.items() if not k.startswith("$")}
    if "$replay_candidate" in args:
        index = int(args["$replay_candidate"])
        candidates = previous.get("candidates") or []
        if index >= len(candidates):
            raise RuntimeError(f"上一步没有 candidates[{index}]（实际 {len(candidates)} 个）")
        resolved.update(candidates[index].get("arguments") or {})
    if "$canonical_from" in args:
        node: object = previous
        for part in str(args["$canonical_from"]).split("."):
            name, _, index = part.partition("[")
            node = node.get(name) if isinstance(node, dict) else None  # type: ignore[union-attr]
            if index:
                position = int(index.rstrip("]"))
                node = node[position] if isinstance(node, list) and position < len(node) else None
            if node is None:
                raise RuntimeError(f"上一步里找不到 {args['$canonical_from']}")
        resolved["canonical_build"] = node
    return resolved


async def capture(out_dir: Path, cases: list) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(command=str(ROOT / ".venv" / "bin" / "destiny-mcp"), args=[])
    index: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for case_id, steps in cases:
                payload: dict = {}
                elapsed_total = 0
                chars_total = 0
                resolved_steps: list[dict] = []
                try:
                    for tool, raw_args in steps:
                        args = _resolve(raw_args, payload)
                        resolved_steps.append({"tool": tool, "arguments": args})
                        payload = await _call(session, tool, args)
                        meta = payload.get("_case") or {}
                        elapsed_total += meta.get("elapsed_ms") or 0
                        chars_total += meta.get("payload_chars") or 0
                    payload["_case"] = {
                        "tool": steps[-1][0],
                        "steps": resolved_steps,
                        "elapsed_ms": elapsed_total,
                        "payload_chars": chars_total,
                        "is_error": bool(payload.get("_case", {}).get("is_error")),
                    }
                except Exception as exc:  # noqa: BLE001 - 基线要记下任何失败形态
                    payload = {
                        "_case": {
                            "tool": steps[-1][0],
                            "steps": resolved_steps
                            or [{"tool": tool, "arguments": args} for tool, args in steps],
                            "raised": type(exc).__name__,
                        },
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
                        "tool": steps[-1][0],
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
    parser.add_argument("--out", type=Path, default=None, help="不填就用该面的默认目录")
    parser.add_argument("--surface", choices=sorted(SURFACES), default="weapon")
    args = parser.parse_args()
    default_out, cases = SURFACES[args.surface]
    return asyncio.run(capture(args.out or default_out, _as_cases(cases)))


if __name__ == "__main__":
    raise SystemExit(main())
