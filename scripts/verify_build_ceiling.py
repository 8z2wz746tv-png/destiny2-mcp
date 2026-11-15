"""真机验证：求解器的"能顶到多少"与"上限"两条口径，在一套真配装上跑一遍（只读）。

存在的理由：P2（上限 + 逐项可达）与 P4（吃干净调谐额度）的验收证据此前都来自合成夹具与
手工探测。这个脚本把同一批口径放到**真账号**上跑，回答三件事：

1. **那套真配装原样重解**：六维是多少、手雷能不能到 130+、比现在穿着的强在哪；
2. **社区模板的六项原样传进去**：超能上限 100 会不会被如实报成违规（`max_violations`）；
3. **每项单独能到多少**（`data.reachable`）：是不是保守下界（真机实测会小于"把下限写成那个数"的解）。

用法：

    # 全部场景（默认；每个场景一次真机求解，慢的那条约 5 分钟）
    .venv/bin/python scripts/verify_build_ceiling.py

    # 只跑一个场景
    .venv/bin/python scripts/verify_build_ceiling.py --scene installed
    .venv/bin/python scripts/verify_build_ceiling.py --scene caps
    .venv/bin/python scripts/verify_build_ceiling.py --scene grenade130

**只读**：只读账号（背包/已装备/组件 304）与跑求解，不写任何东西。

读的是 `.env` 里的默认玩家（与 MCP 工具同一处口径）。
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from destiny_mcp.build.models import BuildRequest
from destiny_mcp.build.process_types import SearchDiagnostics
from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.player_resolver import PlayerResolver
from destiny_mcp.services import profile_components
from destiny_mcp.services.build_service import BuildService
from destiny_mcp.services.inventory_service import InventoryService
from destiny_mcp.tools._helpers import resolve_player_name

STAT_ORDER = ("weapons", "health", "class_stat", "grenade", "super_stat", "melee")
STAT_LABELS = {
    "weapons": "武器", "health": "生命", "class_stat": "职业",
    "grenade": "手雷", "super_stat": "超能", "melee": "近战",
}

#: 场景 = 名字 → （说明, 请求参数）
SCENES: dict[str, tuple[str, dict]] = {
    "installed": (
        "现在穿着的那套（术士 + 星火协议 + 埃希恩记忆 4 件）：手雷下限 100、超能上限 100",
        dict(
            character_class="warlock",
            exotic_name="星火协议",
            grenade_target=100,
            stat_caps={"super_stat": 100},
            top_n=3,
        ),
    ),
    "grenade130": (
        "同一套，把手雷下限顶到 130（P4 的验收线）",
        dict(
            character_class="warlock",
            exotic_name="星火协议",
            grenade_target=130,
            stat_caps={"super_stat": 100},
            top_n=3,
        ),
    ),
    "caps": (
        "社区模板六项**按区间原样**传入（生命0 近战70 手雷100~200 超能80~100 职业70~100 "
        "武器100~200），看上限有没有被如实报出来",
        dict(
            character_class="warlock",
            exotic_name="星火协议",
            # 下限就是模板区间的下端
            weapons_target=100,
            class_target=70,
            grenade_target=100,
            super_target=80,
            melee_target=70,
            # 上限就是模板区间的上端（软约束：超了照样出解，但要被标出来）
            stat_caps={
                "weapons": 200,
                "class_stat": 100,
                "grenade": 200,
                "super_stat": 100,
            },
            top_n=3,
        ),
    ),
}


def _fmt(values: dict[str, int] | list[int]) -> str:
    if isinstance(values, list):
        values = dict(zip(STAT_ORDER, values))
    return " ".join(f"{STAT_LABELS[k]}{values.get(k, 0)}" for k in STAT_ORDER)


async def _equipped_stats(
    inventory: InventoryService, resolver: Any, player: str, character: str
) -> dict:
    """**身上那五件**的六维（含调谐、不含子职业加成）—— 当"改之前"的对照。

    `InventorySnapshot` 是**背包全景**（这个账号两百多件护甲），不能直接求和 ——
    第一版就是这么写的，打出来"武器 2727"这种一眼假的数。要按组件 200
    （`characterEquipment`）挑出真正穿着的那五件。
    """
    snapshot = await inventory.get_armor_snapshot(player, character)
    resolved = await resolver.resolve_player(player)
    character_id = await resolver.resolve_character_id(
        resolved["membership_id"], resolved["membership_type"], character
    )
    profile = await resolver.get_profile(
        resolved["membership_id"], resolved["membership_type"],
        profile_components.ARMOR_SNAPSHOT,
    )
    equipped = {
        str(item.get("itemInstanceId") or "")
        for item in (
            ((profile.get("characterEquipment") or {}).get("data") or {})
            .get(character_id, {})
            .get("items")
            or []
        )
    }
    pieces: list[tuple[str, dict, str]] = []
    for group in (snapshot.helmets, snapshot.gauntlets, snapshot.chests,
                  snapshot.legs, snapshot.class_items):
        for armor in group:
            if armor.item_instance_id not in equipped:
                continue
            row = {key: int(getattr(armor.stats, key, 0) or 0) for key in STAT_ORDER}
            pieces.append((
                f"{armor.name}（{getattr(armor, 'slot', '?')}）",
                row,
                getattr(armor, "tuning_name", "") or "",
            ))
    return {"pieces": pieces}


async def run_scene(build_svc: BuildService, inventory: InventoryService,
                    resolver: Any, player: str, scene: str) -> int:
    note, kwargs = SCENES[scene]
    character = kwargs.get("character_class", "warlock")
    print("=" * 100)
    print(f"场景 {scene}：{note}")

    try:
        baseline = await _equipped_stats(inventory, resolver, player, character)
        totals = {key: 0 for key in STAT_ORDER}
        for _, row, _ in baseline["pieces"]:
            for key in STAT_ORDER:
                totals[key] += row[key]
        print(f"  现在穿着（护甲+调谐，**不含**属性模组与子职业）：{_fmt(totals)}")
        for name, row, tuning in baseline["pieces"]:
            print(f"    - {name:28s} {_fmt(row)}" + (f"  调谐: {tuning}" if tuning else ""))
    except Exception as exc:  # noqa: BLE001 - 对照读不到不该挡住主结论
        print(f"  （现装对照读不到：{type(exc).__name__}: {exc}）")

    request = BuildRequest(**kwargs)
    diagnostics: list[SearchDiagnostics] = []
    started = time.monotonic()
    try:
        results = await build_svc.find_build(player, request, diagnostics)
    except Exception as exc:  # noqa: BLE001 - 真机脚本：如实报出来，别吞
        print(f"  **求解失败**：{type(exc).__name__}: {exc}")
        return 1
    elapsed = time.monotonic() - started

    print(f"  求解 {elapsed:.1f}s；候选 {len(results)} 套")
    for index, result in enumerate(results[:3]):
        candidate = result.build
        stats = {key: int(getattr(candidate, key, 0) or 0) for key in STAT_ORDER}
        print(f"    [{index}] {_fmt(stats)}  score={result.score} "
              f"（含属性模组 {_fmt(candidate.bonus_stats)}）")
        if getattr(result, "max_violations", None):
            for violation in result.max_violations:
                print(f"        超上限: {violation}")
        for item in candidate.items:
            stats = {key: int(getattr(item.stats, key, 0) or 0) for key in STAT_ORDER}
            tuning = getattr(item, "tuning_name", "") or ""
            extras = " ".join(
                part for part in (
                    f"T{getattr(item, 'gear_tier', 0) or '?'}",
                    f"原型 {getattr(item, 'archetype_name', '') or '?'}",
                    f"套装 {getattr(item, 'set_bonus_name', '') or '—'}",
                    "大师" if getattr(item, "is_masterworked", False) else "未大师",
                    f"能量 {getattr(item, 'energy_capacity', 0) or '?'}",
                    "已装备" if getattr(item, "is_equipped", False) else "",
                ) if part
            )
            print(f"        {getattr(item, 'slot', '?'):12s} "
                  f"{getattr(item, 'name', '?'):16s} {_fmt(stats)}"
                  + (f"  调谐: {tuning}" if tuning else ""))
            print(f"            id={getattr(item, 'item_instance_id', '?')}  {extras}")
        for change in getattr(result, "tuning_changes", None) or []:
            print(f"        调谐改动: {change}")

    if diagnostics:
        diag = diagnostics[0]
        coverage = diag.coverage
        print(f"  枚举: exhaustive={coverage.exhaustive} combos={coverage.combos} "
              f"truncated_by={coverage.truncated_by!r}")
        ceilings = diag.reachable_ceilings
        if ceilings:
            print(f"  每项单独特到（reachable，保守下界）：{_fmt(list(ceilings))}")
        note_text = diag.to_dict().get("reachable_note")
        if note_text:
            print(f"  口径: {note_text}")
    return 0


async def main() -> int:
    scenes = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--scene" in sys.argv:
        scenes = [sys.argv[sys.argv.index("--scene") + 1]]
    if not scenes:
        scenes = list(SCENES)
    unknown = [s for s in scenes if s not in SCENES]
    if unknown:
        print(f"不认识的场景 {unknown}；可用：{list(SCENES)}")
        return 2

    player = resolve_player_name("")
    manifest = ManifestManager()
    bungie = BungieClient()
    await bungie.start()
    exit_code = 0
    try:
        await manifest.ensure_loaded(bungie)
        resolver = PlayerResolver(bungie, manifest)
        for scene in scenes:
            exit_code |= await run_scene(
                BuildService(bungie, manifest, resolver),
                InventoryService(bungie, manifest, resolver),
                resolver,
                player,
                scene,
            )
    finally:
        await bungie.close()
        manifest.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
