"""照抄社区配装功能模组（0.7.9）：解析、能量预留、执行时的跳过口径。

口径（用户 2026-09-25 拍板）：功能模组**不进求解器** —— 它是照抄作者的流派取向；
求解器只需要知道"这些槽被占了、这些能量别动"。所以这里钉三件事：

1. 解析只做确定的（名字 → 部位/版本/能量），认不出来的如实记 `unresolved`，不猜；
2. 能量预留取同名版本里最贵的那颗（执行器挑的一定不比它贵 → 只会多留，不会少留）；
3. 插不进 / 能量不够 → 跳过 + 点名，**不许**让整条配装失败或回退。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from destiny_mcp.build.functional_mods import resolve_functional_mods


class _Manifest:
    """按 `get_armor_mods(slot=…)` 的真形状给：`{name, hash, slot, energy_cost}`。"""

    def __init__(self) -> None:
        self.rows = [
            {"name": "充沛", "hash": 4048902980, "slot": "helmet", "energy_cost": 3},
            {"name": "充沛", "hash": 2414626352, "slot": "helmet", "energy_cost": 1},
            {"name": "特殊武器弹药搜寻者", "hash": 2620835322, "slot": "helmet", "energy_cost": 3},
            {"name": "火力无限", "hash": 2485657760, "slot": "gauntlets", "energy_cost": 2},
            {"name": "手雷模组", "hash": 1435557120, "slot": "general", "energy_cost": 3},
            {"name": "跨部位模组", "hash": 1, "slot": "helmet", "energy_cost": 1},
            {"name": "跨部位模组", "hash": 2, "slot": "chest", "energy_cost": 1},
        ]

    def get_armor_mods(self, slot: str = "", category: str = "all") -> list[dict]:
        if slot and slot not in {"helmet", "gauntlets"}:
            raise ValueError(f"不认识的部位 {slot!r}")
        return [row for row in self.rows if not slot or row["slot"] == slot]


def test_resolve_keeps_every_variant_and_reserves_the_priciest() -> None:
    """同名多版本全留着（执行时按"这一位能不能插"挑），预留能量取最贵那版。"""
    plan = resolve_functional_mods(_Manifest(), ["充沛", "特殊武器弹药搜寻者"])

    assert [mod.slot for mod in plan.mods] == ["helmet", "helmet"]
    assert plan.mods[0].variants == (2414626352, 4048902980)
    assert plan.mods[0].energy_cost == 3, "预留取最贵的那版：少留会让属性模组装不下"
    assert plan.energy_by_slot() == {"helmet": 6}
    # 一组 = 同名版本；执行器按现场数据挑一版
    assert plan.groups_by_slot() == {"helmet": [[2414626352, 4048902980], [2620835322]]}
    assert plan.unresolved == ()


def test_resolve_reports_what_it_cannot_copy() -> None:
    """认不出来的一律如实报：不猜、也不拿属性模组凑数。"""
    plan = resolve_functional_mods(
        _Manifest(),
        ["回收利用", "手雷模组", "跨部位模组", "helmet:充沛", "helmet:不存在"],
    )

    by_entry = {row["entry"]: row["reason"] for row in plan.unresolved}
    assert "找不到这个名字" in by_entry["回收利用"]
    assert "属性模组不照抄" in by_entry["手雷模组"]
    assert "跨多个部位" in by_entry["跨部位模组"]
    assert "找不到这个名字" in by_entry["helmet:不存在"]
    # `部位:名字` 前缀照常认
    assert [mod.name for mod in plan.mods] == ["充沛"]


def test_resolve_keeps_duplicates() -> None:
    """作者写了两颗「火力无限」就是两颗：去重会让能量少算一半。"""
    plan = resolve_functional_mods(_Manifest(), ["火力无限", "火力无限"])

    assert len(plan.mods) == 2
    assert plan.energy_by_slot() == {"gauntlets": 4}


def test_plan_payload_says_where_they_come_from() -> None:
    payload = resolve_functional_mods(_Manifest(), ["充沛"]).as_payload()

    assert payload["source"] == "copied_from_template"
    assert payload["energy_by_slot"] == {"helmet": 3}
    assert "照抄" in payload["note"]


# ── 能量预留：求解器只能用剩下的 ────────────────────────────────────────


def test_snapshot_reserves_energy_for_copied_mods() -> None:
    """照抄模组的能量要从"能给属性模组的能量"里扣掉 —— 取 `max(已装, 预留)`。

    少算的后果是属性模组装不下（预检失败并回退），多算只是少用一点能量，所以取 max。
    """
    from destiny_mcp.build.models import InventorySnapshot

    profile = {
        "profileInventory": {"data": {"items": []}},
        "characters": {"data": {"char-1": {"classType": 1}}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "characterEquipment": {"data": {"char-1": {"items": [
            {"itemInstanceId": "helmet-1", "itemHash": 100, "bucketHash": 3448274439},
        ]}}},
        "itemComponents": {
            "instances": {"data": {"helmet-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            "stats": {"data": {"helmet-1": {"stats": {}}}},
            # 已装的部位模组只占 1 点（“充沛”那版的便宜款）
            "sockets": {"data": {"helmet-1": {"sockets": [
                {"plugHash": 2414626352},
            ]}}},
        },
    }
    class _SnapshotManifest:
        def get_item_info(self, item_hash: int) -> dict:
            return {
                "classType": 1,
                "tier": 5,
                "bucketTypeHash": 3448274439,
                "itemType": 2,
                "icon": "",
            }

        def get_item_name(self, item_hash: int) -> str:
            return "测试头盔"

        def get_item_definition(self, item_hash: int) -> dict:
            return {
                "plug": {
                    "plugCategoryIdentifier": "enhancements.v2_head",
                    "energyCost": {"energyCost": 1},
                },
                "displayProperties": {"name": "#"},
                "sockets": {"socketEntries": [{"singleInitialItemHash": 0}]},
            }

        def get_set_bonus_info(self, item_hash: int) -> None:
            return None

    manifest = _SnapshotManifest()

    without = InventorySnapshot.from_profile(profile, manifest, "hunter")
    with_reserve = InventorySnapshot.from_profile(
        profile, manifest, "hunter", reserved_mod_energy={"helmet": 9}
    )

    assert without.helmets[0].energy_used_by_other_mods == 1
    assert with_reserve.helmets[0].energy_used_by_other_mods == 9, (
        "预留 9 > 已装 1：求解器只能拿剩下的 2 点装属性模组"
    )


# ── 执行：挑版本、装不下/插不进就跳过 ──────────────────────────────────


def _service(sockets: list[dict]) -> SimpleNamespace:
    """把 Mixin 的宿主替身搭出来：只实现 `_plan_functional_mods` 依赖的那几样。"""
    from destiny_mcp.services.loadout_functional_mods import FunctionalModMixin

    class _Host(FunctionalModMixin):
        def __init__(self) -> None:
            self._manifest = SimpleNamespace(
                get_item_definition=lambda item_hash: {"sockets": {"socketEntries": []}}
            )
            self.calls: list[int] = []

        async def _find_mod_socket(self, *args, **kwargs):  # type: ignore[override]
            variant = int(args[2])
            self.calls.append(variant)
            return 0 if variant == 4048902980 else None

        def _plug_energy_cost(self, plug_hash: int) -> int:
            return {4048902980: 3, 2414626352: 1, 1078080765: 0}.get(int(plug_hash), 0)

        def plug_is_insertable(self, *args, **kwargs) -> bool:
            return True

    return _Host()


def _item(groups: list[list[int]]) -> SimpleNamespace:
    return SimpleNamespace(
        name="至高狂徒面具",
        item_hash=656307180,
        item_instance_id="helmet-1",
        functional_mod_groups=groups,
        mod_sockets={},
    )


@pytest.mark.asyncio
async def test_functional_mod_picks_the_insertable_variant_and_records_the_socket() -> None:
    """同名版本里挑"这一位能插"的那版；挑中后记进 `mod_sockets`，回读核对才盖得住它。"""
    service = _service([{"plugHash": 1078080765}])
    item = _item([[2414626352, 4048902980]])

    operations = await service._plan_functional_mods(
        item, [{"plugHash": 1078080765}], {}, {"helmet-1": [{"plugHash": 1078080765}]},
        {"helmet-1"}, "mid", 3, used_energy=8, capacity=11,
    )

    assert [op.as_tuple() for op in operations] == [("mod", 4048902980, 0)]
    assert item.mod_sockets == {0: 4048902980}, "写过的槽要进 mod_sockets 供回读核对"


@pytest.mark.asyncio
async def test_functional_mod_skips_when_it_cannot_fit() -> None:
    """能量不够 → 跳过 + 点名，不拆别人、也不让整条配装失败。"""
    service = _service([{"plugHash": 1078080765}])
    item = _item([[4048902980]])

    operations = await service._plan_functional_mods(
        item, [{"plugHash": 1078080765}], {}, None, set(), "mid", 3,
        used_energy=10, capacity=11,
    )

    assert [op.action for op in operations] == ["blocked"]
    assert "能量不够" in operations[0].reason
    assert item.mod_sockets == {}, "没写进去的槽不能进 mod_sockets（否则回读会把成功判成失败）"


@pytest.mark.asyncio
async def test_functional_mod_keeps_when_already_installed() -> None:
    service = _service([{"plugHash": 4048902980}])
    item = _item([[4048902980]])

    operations = await service._plan_functional_mods(
        item, [{"plugHash": 4048902980}], {}, None, set(), "mid", 3,
        used_energy=8, capacity=11,
    )

    assert [op.action for op in operations] == ["keep"], "已经装着就不写（上游会给 1679）"
    assert item.mod_sockets == {0: 4048902980}


@pytest.mark.asyncio
async def test_functional_mod_skips_when_not_insertable() -> None:
    """一个版本都插不进这一位角色：跳过 + 点名（与 equip_mod 的 writable=false 同一口径）。"""
    service = _service([{"plugHash": 1078080765}])
    item = _item([[999999]])

    operations = await service._plan_functional_mods(
        item, [{"plugHash": 1078080765}], {}, None, set(), "mid", 3,
        used_energy=8, capacity=11,
    )

    assert [op.action for op in operations] == ["blocked"]
    assert "可插入清单" in operations[0].reason


# ── 武器分析的投影（0.7.11）：池子只给值得看的、副本去掉可换项 ──────────────


def test_analysis_projection_keeps_the_recommended_options() -> None:
    """定义级池子默认只给本地愿单有结论的项，并把总数与推荐数一起给出去。"""
    from destiny_mcp.services.weapon_analysis_projection import project_analysis

    socket = {
        "kind": "trait", "slot": "特性1", "option_count": 20,
        "options": [
            {"plug_hash": 1, "name": "普通 A", "can_roll": True, "enhanced_plug_hash": 0,
             "stat_effects": [{"stat": "射程", "value": 5}]},
            {"plug_hash": 2, "name": "推荐 B", "can_roll": True, "enhanced_plug_hash": 77,
             "stat_effects": [{"stat": "射程", "value": 10}],
             "recommended": {"wishlist": {"pve": True, "pvp": False}}},
        ],
    }
    projected = project_analysis({
        "weapon": {"name": "测试枪"}, "sockets": [socket], "stats": {}, "god_roll": {},
        "inventory": {"instances": [{"weapon": {"name": "测试枪"}, "options": [{"options": ["x"]}],
                                     "sockets": [{"equipped": {"name": "推荐 B"}, "options": ["x"]}],
                                     "stats": {}}]},
        "inventory_status": "complete", "starside": {},
    })

    row = projected["sockets"][0]
    assert [o["name"] for o in row["options"]] == ["推荐 B"]
    assert row["option_count"] == 20 and row["recommended_count"] == 1
    # 0/空值不再逐项发；强化版只留 `enhanced: true`
    assert "plug_hash" not in row["options"][0]
    assert row["options"][0]["enhanced"] is True
    assert row["options"][0]["recommended"] == {"wishlist": {"pve": True, "pvp": False}}
    # 副本：可换项整块去掉（归 compare），现在装着什么留着
    instance = projected["inventory"]["instances"][0]
    assert "options" not in instance
    assert "options" not in instance["sockets"][0]
    assert instance["sockets"][0]["equipped"]["name"] == "推荐 B"
    assert "options_hint" in projected["inventory"]


def test_analysis_projection_does_not_silently_empty_a_socket() -> None:
    """一栏里一个愿单结论都没有时，退回前 N 项并说明原因 —— 不许变成"这栏没得选"。"""
    from destiny_mcp.services.weapon_analysis_projection import OPTION_SAMPLE, project_analysis

    socket = {
        "kind": "barrel", "slot": "枪管",
        "options": [{"name": f"选项{i}", "can_roll": True} for i in range(10)],
    }
    row = project_analysis({"sockets": [socket], "inventory": {}})["sockets"][0]

    assert len(row["options"]) == OPTION_SAMPLE
    assert row["recommended_count"] == 0
    assert "本地愿单没有结论" in row["options_note"]
    assert "perk_pool" in row["options_note"]
