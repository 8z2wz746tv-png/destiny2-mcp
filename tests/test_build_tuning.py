"""调谐补齐（services/build_tuning.py）的行为测试。

场景都是"求解器给的方案差一点点"：
- 差 5 点、有件护甲能挪 → 应该给出"把 X 的调谐改成 +手雷 / -武器"并复核通过；
- 差得超出调谐额度 → 不许假装救回来，候选池保持原样；
- 本来就达标 → 一次都不能多解（基线里的绿方案必须原样返回）。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from destiny_mcp.build.constants import NAME_TO_STAT_HASH, STAT_NAMES
from destiny_mcp.build.models import (
    Armor,
    ArmorStats,
    BuildConstraints,
    StatModDefinition,
)
from destiny_mcp.build.process_types import ProcessArmorSet
from destiny_mcp.build.tuning import EMPTY_TUNING_PLUG_HASH, TUNING_PLUG_SET_HASH
from destiny_mcp.build.models import InventorySnapshot
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.services.build_tuning import (
    armor_with_tuning,
    headroom_payload,
    relax_targets,
    rescue_sets,
    sets_meet_targets,
    solve_with_tuning,
    tuning_allowance,
)

T5_ARMOR_HASH = 3600000100
LEGACY_ARMOR_HASH = 3600000101
STAT_INDEX = {name: index for index, name in enumerate(STAT_NAMES)}


def _table(rows: dict[int, dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE DestinyInventoryItemDefinition (id INTEGER PRIMARY KEY, json TEXT)")
    for item_hash, payload in rows.items():
        conn.execute(
            "INSERT INTO DestinyInventoryItemDefinition (id, json) VALUES (?, ?)",
            (item_hash, json.dumps(payload, ensure_ascii=False)),
        )
    return conn


def _manifest() -> ManifestManager:
    rows = {
        T5_ARMOR_HASH: {
            "displayProperties": {"name": "T5 测试件"},
            "itemType": 2,
            "sockets": {
                "socketEntries": [
                    {
                        "reusablePlugSetHash": TUNING_PLUG_SET_HASH,
                        "singleInitialItemHash": EMPTY_TUNING_PLUG_HASH,
                    }
                ]
            },
        },
        LEGACY_ARMOR_HASH: {
            "displayProperties": {"name": "旧测试件"},
            "itemType": 2,
            "sockets": {"socketEntries": [{"reusablePlugSetHash": 0}]},
        },
    }
    manager = ManifestManager()
    manager._conn = _table(rows)
    manager._build_name_index(manager._conn, language="en")
    return manager


def _armor(
    slot: str,
    values: dict[str, int],
    *,
    item_hash: int = T5_ARMOR_HASH,
    tuning: int | None = None,
) -> Armor:
    rounded = {name: values.get(name, 0) for name in STAT_NAMES}
    return Armor(
        item_instance_id=f"inst-{slot}",
        item_hash=item_hash,
        name=f"{slot} 件",
        slot=slot,
        stats=ArmorStats(**rounded),
        armor_system="armor_3",
        gear_tier=5,
        tuning_mod_hash=tuning,
    )


def _snapshot(
    armors: list[Armor],
    *,
    with_stat_mods: bool = False,
) -> InventorySnapshot:
    buckets: dict[str, list[Armor]] = {
        "helmets": [],
        "gauntlets": [],
        "chests": [],
        "legs": [],
        "class_items": [],
    }
    for armor in armors:
        buckets[armor.slot].append(armor)
    mods = (
        [
            StatModDefinition(
                hash=7001 + index,
                stat_hash=NAME_TO_STAT_HASH["grenade"],
                value=10,
                energy_cost=3,
                kind="general",
            )
            for index in range(1)
        ]
        if with_stat_mods
        else []
    )
    return InventorySnapshot(**buckets, stat_mod_definitions=mods)


def _five(*, grenade: int, weapons: int, health: int = 10) -> list[Armor]:
    """五件：手雷/武器按参数给，其余低位；头盔那件留出可挪的武器余量。"""
    slots = ["helmets", "gauntlets", "chests", "legs", "class_items"]
    armors = [
        _armor(slot, {"grenade": grenade // 5, "weapons": weapons // 5, "health": health // 5})
        for slot in slots
    ]
    return armors


def _set_from(armors: list[Armor]) -> ProcessArmorSet:
    stats = [
        sum(int(getattr(armor.stats, name, 0) or 0) for armor in armors)
        for name in STAT_NAMES
    ]
    return ProcessArmorSet(armor=list(armors), stats=stats, bonus_stats=[0] * 6)


class _FakeCompute:
    """替身：直接在当前进程里跑求解器，并记下被调用的次数。"""

    def __init__(self) -> None:
        self.calls: list[BuildConstraints] = []

    async def run(self, func, snapshot, constraints):  # noqa: ANN001 - 与 BuildCompute 同形
        self.calls.append(constraints)
        return func(snapshot, constraints)


# ── 额度与放宽 ───────────────────────────────────────────────────────


def test_tuning_allowance_counts_tunable_slots() -> None:
    manifest = _manifest()
    five = _snapshot(_five(grenade=50, weapons=40))
    legacy = _snapshot(
        [_armor(slot, {"grenade": 10}, item_hash=LEGACY_ARMOR_HASH) for slot in
         ("helmets", "gauntlets", "chests", "legs", "class_items")]
    )

    assert tuning_allowance(five, manifest)["grenade"] == 25
    assert tuning_allowance(legacy, manifest)["grenade"] == 0


def test_relax_targets_only_touches_non_zero_targets() -> None:
    constraints = BuildConstraints(grenade_min=70, weapons_min=0)

    relaxed = relax_targets(constraints, {name: 25 for name in STAT_NAMES})

    assert relaxed is not None
    assert relaxed.grenade_min == 45
    assert relaxed.weapons_min == 0
    # 没有可放宽的项 → None（省掉一次求解）
    assert relax_targets(BuildConstraints(), {name: 25 for name in STAT_NAMES}) is None


def test_sets_meet_targets_uses_armor_plus_mods_plus_subclass() -> None:
    armors = _five(grenade=50, weapons=40)
    snapshot_set = _set_from(armors)
    snapshot_set.bonus_stats = [0, 0, 0, 5, 0, 0]

    # 护甲 50 + 模组 5 = 55
    assert sets_meet_targets([snapshot_set], BuildConstraints(grenade_min=56)) is False
    assert sets_meet_targets([snapshot_set], BuildConstraints(grenade_min=55)) is True
    # 子职业/碎片也算进去（和响应里的口径一致）
    constraints = BuildConstraints(grenade_min=60, fragment_stats=[0, 0, 0, 5, 0, 0])
    assert sets_meet_targets([snapshot_set], constraints) is True
    # 一个目标都没设时不算"达标"，免得白白跳过调谐
    assert sets_meet_targets([snapshot_set], BuildConstraints()) is False


# ── 救回来 / 救不回来 ────────────────────────────────────────────────


def test_rescue_closes_a_five_point_gap_and_reports_the_change() -> None:
    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)

    rescues = rescue_sets(
        snapshot,
        BuildConstraints(grenade_min=55),
        manifest,
        [_set_from(armors)],
    )

    assert len(rescues) == 1
    rescue = rescues[0]
    assert rescue.plan.feasible is True
    assert rescue.plan.change_count == 1
    change = rescue.plan.changes[0]
    assert change.increased == "grenade"
    # 这件护甲的近战本来就是 0，−5 打上去白给：不用牺牲任何非零项
    assert change.decreased == "melee"
    # 复核后的六维必须真的到 55
    stats = list(rescue.armor_set.stats)
    assert stats[STAT_INDEX["grenade"]] == 55
    # 护甲对象也要跟着改（对外展示的是它）
    tuned = next(
        armor
        for armor in rescue.armor_set.armor
        if armor.item_instance_id == change.item_instance_id
    )
    assert tuned.tuning_mod_hash == change.to_plug


def test_rescue_refuses_a_gap_beyond_the_tuning_budget() -> None:
    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)

    rescues = rescue_sets(
        snapshot,
        BuildConstraints(grenade_min=100),
        manifest,
        [_set_from(armors)],
    )

    assert rescues == []


def test_rescue_needs_tunable_armor() -> None:
    manifest = _manifest()
    armors = [
        _armor(slot, {"grenade": 10, "weapons": 8}, item_hash=LEGACY_ARMOR_HASH)
        for slot in ("helmets", "gauntlets", "chests", "legs", "class_items")
    ]
    snapshot = _snapshot(armors)

    assert rescue_sets(
        snapshot, BuildConstraints(grenade_min=55), manifest, [_set_from(armors)]
    ) == []


def test_rescue_does_not_break_another_target() -> None:
    """每一项都卡在目标上、没有让步空间时，就不能硬凑。"""
    manifest = _manifest()
    armors = [
        _armor(
            slot,
            {"grenade": 10, "weapons": 8, "health": 1, "class_stat": 1, "melee": 1, "super_stat": 1},
        )
        for slot in ("helmets", "gauntlets", "chests", "legs", "class_items")
    ]
    snapshot = _snapshot(armors)
    # 手雷差 5，而每一件能当"代价"的项都恰好卡在目标上（5 件 × 1 点，再减就见底）
    constraints = BuildConstraints(
        grenade_min=55, weapons_min=40, health_min=5,
        class_target=5 and 0, class_stat_min=5, melee_min=5, super_stat_min=5,
    )

    rescues = rescue_sets(snapshot, constraints, manifest, [_set_from(armors)])

    assert rescues == []


# ── 两趟求解 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_solve_with_tuning_returns_untouched_when_targets_are_met() -> None:
    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)
    compute = _FakeCompute()

    pool, plans = await solve_with_tuning(
        compute, snapshot, BuildConstraints(grenade_min=40), manifest
    )

    assert len(compute.calls) == 1, "达标时不该再解第二遍"
    assert plans == {}
    assert pool and pool[0].stats[STAT_INDEX["grenade"]] == 50


@pytest.mark.asyncio
async def test_solve_with_tuning_rescues_a_five_point_gap() -> None:
    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)
    compute = _FakeCompute()
    constraints = BuildConstraints(grenade_min=55)

    pool, plans = await solve_with_tuning(compute, snapshot, constraints, manifest)

    assert len(compute.calls) == 2, "没达标时要用放宽后的目标再解一遍"
    assert compute.calls[1].grenade_min == 30  # 55 − 25
    assert len(plans) == 1
    assert pool and list(pool[0].stats)[STAT_INDEX["grenade"]] == 55
    assert sets_meet_targets(pool, constraints) is True


@pytest.mark.asyncio
async def test_solve_with_tuning_keeps_the_original_pool_when_it_cannot_help() -> None:
    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)
    compute = _FakeCompute()
    constraints = BuildConstraints(grenade_min=100)

    pool, plans = await solve_with_tuning(compute, snapshot, constraints, manifest)

    assert plans == {}
    # 这份替身 Manifest 里没有属性模组，所以严格解本身就会被剪掉（pool 为空也正常）；
    # 关键是：没救回来时不能凭空造方案，也不能报"达标"。
    assert sets_meet_targets(pool, constraints) is False


# ── 供"无解"阶梯引用的额度证据 ───────────────────────────────────────


def test_headroom_payload_says_what_tuning_can_and_cannot_cover() -> None:
    manifest = _manifest()
    snapshot = _snapshot(_five(grenade=50, weapons=40))

    payload = headroom_payload(snapshot, manifest)

    assert payload["allowance"]["grenade"] == 25
    assert payload["per_stat_max_gain"]["grenade"] == 25
    assert "零和" in payload["note"]


def test_armor_with_tuning_updates_stats_and_plug_together() -> None:
    manifest = _manifest()
    armor = _armor("helmets", {"grenade": 10, "weapons": 8})
    snapshot = _snapshot([
        armor,
        *_five(grenade=40, weapons=32)[1:],
    ])
    from destiny_mcp.build.tuning import piece_tuning

    piece = piece_tuning(armor, manifest)
    assert piece is not None
    choice = piece.option(EMPTY_TUNING_PLUG_HASH)
    assert choice is not None
    tuned = armor_with_tuning(armor, piece, choice)

    assert tuned.stats.grenade == armor.stats.grenade
    assert tuned.tuning_mod_hash == EMPTY_TUNING_PLUG_HASH
    assert snapshot.total_pieces == 5


def test_allowance_counts_one_piece_per_slot_not_the_whole_vault() -> None:
    """实机踩过：118 件的仓库被整仓相加，算出"每项能补 563 点"。

    一套护甲一个部位只出一件，所以额度必须是「每个部位取最强的一件」再相加：
    同一槽里塞 3 件，每项额度还是 5（不是 15）。
    """
    manifest = _manifest()
    armors = [
        _armor(
            "helmets",
            {"grenade": 10, "weapons": 8},
        )
        for _ in range(3)
    ]
    for index, piece in enumerate(armors):
        piece.item_instance_id = f"inst-helmets-{index}"
    snapshot = _snapshot([armors[0], *[a for a in armors[1:]]])

    allowance = tuning_allowance(snapshot, manifest)

    assert allowance["grenade"] == 5
    assert allowance["weapons"] == 5


def test_allowance_includes_undoing_a_tuning_that_hurts() -> None:
    """这件现在是 +武器/−生命值：换成 +生命值/−武器就是 +10 生命。"""
    manifest = _manifest()
    from destiny_mcp.build.tuning import directional_tuning_hash

    opposing = directional_tuning_hash("weapons", "health")
    armor = _armor("helmets", {"weapons": 15, "health": 5}, tuning=opposing)
    snapshot = _snapshot([armor])

    allowance = tuning_allowance(snapshot, manifest)

    assert allowance["health"] == 10
    # 武器已经吃满了（方向型最多 +5），换任何调谐都加不了更多
    assert allowance["weapons"] == 0


def test_within_tuning_reach_gates_the_second_solve() -> None:
    """差得比额度多就别再解一遍（这是省时间的那道闸）。"""
    from destiny_mcp.services.build_tuning import within_tuning_reach

    armors = _five(grenade=50, weapons=40)
    armor_set = _set_from(armors)
    allowance = {name: 5 for name in STAT_NAMES}

    assert within_tuning_reach([armor_set], BuildConstraints(grenade_min=55), allowance) is True
    assert within_tuning_reach([armor_set], BuildConstraints(grenade_min=56), allowance) is False
    assert within_tuning_reach([], BuildConstraints(grenade_min=55), allowance) is False
    assert within_tuning_reach([armor_set], BuildConstraints(), allowance) is False


# ── 复核驱动的兜底搜索 ───────────────────────────────────────────────


def test_verification_search_finds_a_plan_the_heuristic_would_also_find() -> None:
    """兜底搜索的契约：有解就给"过了复核"的方案，没解就如实返回 None。"""
    from destiny_mcp.services.build_tuning import verification_search
    from destiny_mcp.build.solver import prepare_fixed_set_context
    from destiny_mcp.build.tuning import piece_tuning

    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)
    parsed = BuildConstraints(grenade_min=55)
    context = prepare_fixed_set_context(snapshot, parsed)
    tunable = [p for p in (piece_tuning(a, manifest) for a in armors) if p]

    found = verification_search(armors, tunable, context, {"grenade": 55}, ["grenade"])

    assert found is not None
    tuned, plan = found
    assert plan.change_count >= 1
    assert sum(a.stats.grenade for a in tuned) >= 55


def test_verification_search_gives_up_honestly_when_nothing_can_help() -> None:
    from destiny_mcp.services.build_tuning import verification_search
    from destiny_mcp.build.solver import prepare_fixed_set_context
    from destiny_mcp.build.tuning import piece_tuning

    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    snapshot = _snapshot(armors)
    parsed = BuildConstraints(grenade_min=200)
    context = prepare_fixed_set_context(snapshot, parsed)
    tunable = [p for p in (piece_tuning(a, manifest) for a in armors) if p]

    assert verification_search(armors, tunable, context, {"grenade": 200}, ["grenade"]) is None


def test_worth_search_is_a_cheap_necessary_condition() -> None:
    """缺口比"模组预算 + 调谐上限"还大就别搜了；能补上才值得进兜底。"""
    from destiny_mcp.services.build_tuning import _worth_search
    from destiny_mcp.build.tuning import piece_tuning

    manifest = _manifest()
    armors = _five(grenade=50, weapons=40)
    tunable = [p for p in (piece_tuning(a, manifest) for a in armors) if p]
    armor_only = [40, 10, 0, 50, 0, 0]

    assert _worth_search(armors, armor_only, {"grenade": 55}, 0, tunable) is True
    assert _worth_search(armors, armor_only, {"grenade": 95}, 0, tunable) is False
