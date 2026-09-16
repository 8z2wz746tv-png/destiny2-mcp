"""调谐（Tuning）纯计算层的特征测试。

口径来自实机 + Manifest（docs/plans/ARMOR_FORMAT_PLAN.md P7）：
30 个方向型（+5/−5，零和）+ 平衡调整（六维 +1）+ 空插件；有调谐槽的护甲
件件都能装这 31 个非空插件。这里钉住四件事：

1. 目录本身（数量、增量、hash）；
2. 单件视图的加减法（现状含调谐 → 先减掉才能算"换成什么"）；
3. 分配器会**同时**检查"被减的那一项会不会掉破目标"；
4. 补不上时给的是结构化原因，不是空手而归。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from destiny_mcp.build.constants import STAT_NAMES
from destiny_mcp.build.models import Armor, ArmorStats
from destiny_mcp.build.tuning import (
    EMPTY_TUNING_PLUG_HASH,
    TUNING_PLUG_SET_HASH,
    directional_tuning_hash,
    has_tuning_socket,
    piece_tuning,
    plan_tuning,
    tuning_catalog,
    tuning_headroom,
)
from destiny_mcp.manifest import ManifestManager

T5_ARMOR_HASH = 3600000001
LEGACY_ARMOR_HASH = 3600000002
BALANCED_HASH = 3122197216

# 真机 Manifest 里几个方向型调谐的 hash（六维改名前后的实测值都没变）
WEAPONS_UP_HEALTH_DOWN = directional_tuning_hash("weapons", "health")
GRENADE_UP_CLASS_DOWN = directional_tuning_hash("grenade", "class_stat")


def _stats(
    weapons: int = 0,
    health: int = 0,
    class_stat: int = 0,
    grenade: int = 0,
    melee: int = 0,
    super_stat: int = 0,
) -> ArmorStats:
    return ArmorStats(
        weapons=weapons,
        health=health,
        class_stat=class_stat,
        grenade=grenade,
        melee=melee,
        super_stat=super_stat,
    )


def _armor(
    name: str,
    stats: ArmorStats,
    *,
    tuning: int | None = None,
    item_hash: int = T5_ARMOR_HASH,
) -> Armor:
    return Armor(
        item_instance_id=f"inst-{name}",
        item_hash=item_hash,
        name=name,
        slot="legs",
        stats=stats,
        armor_system="armor_3",
        gear_tier=5,
        tuning_mod_hash=tuning,
    )


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


def _manager(with_tuning_socket: bool = True) -> ManifestManager:
    """内存 Manifest：一件带调谐槽的 T5、一件没有槽的 legacy，外加几个插件名。"""
    socket_entry = (
        {"reusablePlugSetHash": TUNING_PLUG_SET_HASH, "singleInitialItemHash": EMPTY_TUNING_PLUG_HASH}
        if with_tuning_socket
        else {"reusablePlugSetHash": 0}
    )
    rows = {
        T5_ARMOR_HASH: {
            "displayProperties": {"name": "测试腿甲"},
            "itemType": 2,
            "sockets": {"socketEntries": [socket_entry]},
        },
        LEGACY_ARMOR_HASH: {
            "displayProperties": {"name": "旧腿甲"},
            "itemType": 2,
            "sockets": {"socketEntries": [{"reusablePlugSetHash": 0}]},
        },
        WEAPONS_UP_HEALTH_DOWN: {
            "displayProperties": {"name": "+武器 / -生命值"},
            "itemType": 19,
        },
        GRENADE_UP_CLASS_DOWN: {
            "displayProperties": {"name": "+手雷 / -职业"},
            "itemType": 19,
        },
        BALANCED_HASH: {"displayProperties": {"name": "平衡调整"}, "itemType": 19},
        EMPTY_TUNING_PLUG_HASH: {
            "displayProperties": {"name": "空调整模组插槽"},
            "itemType": 19,
        },
    }
    manager = ManifestManager()
    manager._conn = _table(rows)
    manager._build_name_index(manager._conn, language="en")
    return manager


# ── 目录 ─────────────────────────────────────────────────────────────


def test_catalog_is_thirty_directional_plus_balanced_and_empty() -> None:
    catalog = tuning_catalog(_manager())

    kinds = [choice.kind for choice in catalog]
    assert kinds.count("directional") == 30
    assert kinds.count("balanced") == 1
    assert kinds.count("empty") == 1


def test_directional_choices_move_exactly_five_points_between_two_stats() -> None:
    catalog = tuning_catalog(_manager())

    for choice in catalog:
        if choice.kind != "directional":
            continue
        assert sum(choice.delta) == 0
        assert sorted(choice.delta) == [-5, 0, 0, 0, 0, 5]
        assert choice.increased is not None and choice.decreased is not None


def test_balanced_choice_adds_one_to_every_stat() -> None:
    catalog = tuning_catalog(_manager())

    balanced = next(choice for choice in catalog if choice.kind == "balanced")
    assert balanced.delta == (1, 1, 1, 1, 1, 1)


def test_catalog_uses_manifest_names_and_falls_back_for_unknown_plugs() -> None:
    catalog = tuning_catalog(_manager())

    names = {choice.plug_hash: choice.name for choice in catalog}
    assert names[WEAPONS_UP_HEALTH_DOWN] == "+武器 / -生命值"
    # 没在 Manifest 里的插件用中文拼兜底名，绝不能显示成 "#123456"
    assert not any(name.startswith("#") for name in names.values())


def test_tuning_catalog_hash_matches_the_live_manifest_value() -> None:
    """方向型 hash 是硬编码常量表，用实机抓到的一对钉住它。"""
    assert GRENADE_UP_CLASS_DOWN == 1922571986


# ── 有没有调谐槽 ─────────────────────────────────────────────────────


def test_has_tuning_socket_reads_the_socket_entry_not_the_gear_tier() -> None:
    manager = _manager()

    assert has_tuning_socket(manager.get_item_definition(T5_ARMOR_HASH)) is True
    assert has_tuning_socket(manager.get_item_definition(LEGACY_ARMOR_HASH)) is False
    assert has_tuning_socket(None) is False


def test_piece_tuning_is_none_for_armor_without_the_socket() -> None:
    manager = _manager()
    armor = _armor("旧腿甲", _stats(weapons=10), item_hash=LEGACY_ARMOR_HASH)

    assert piece_tuning(armor, manager) is None


# ── 单件加减法 ───────────────────────────────────────────────────────


def test_piece_tuning_round_trips_the_installed_choice() -> None:
    """304 组件给的是含调谐的值：减掉当前调谐再加回同一个，必须回到原值。"""
    manager = _manager()
    armor = _armor(
        "已调谐",
        _stats(weapons=25, health=20, class_stat=10, grenade=15, melee=5, super_stat=2),
        tuning=WEAPONS_UP_HEALTH_DOWN,
    )

    piece = piece_tuning(armor, manager)
    assert piece is not None
    assert piece.current.plug_hash == WEAPONS_UP_HEALTH_DOWN
    assert piece.base == (20, 25, 10, 15, 5, 2)  # 减掉 +5 武器 / −5 生命值
    assert piece.stats_with(None) == (25, 20, 10, 15, 5, 2)


def test_piece_tuning_treats_a_missing_plug_as_empty() -> None:
    manager = _manager()
    armor = _armor("没调谐", _stats(weapons=30, health=25, class_stat=20))

    piece = piece_tuning(armor, manager)
    assert piece is not None
    assert piece.current.kind == "empty"
    assert piece.base == (30, 25, 20, 0, 0, 0)


def test_delta_of_is_relative_to_what_is_installed_now() -> None:
    """这件现在减手雷，所以"撤掉调谐"就是 +5 手雷 / −5 武器。"""
    manager = _manager()
    armor = _armor(
        "已调谐",
        _stats(weapons=25, health=20, class_stat=10, grenade=15),
        tuning=directional_tuning_hash("weapons", "grenade"),
    )
    piece = piece_tuning(armor, manager)
    assert piece is not None

    empty = piece.option(EMPTY_TUNING_PLUG_HASH)
    assert empty is not None
    delta = piece.delta_of(empty)
    assert delta[STAT_NAMES.index("grenade")] == 5
    assert delta[STAT_NAMES.index("weapons")] == -5


def test_tuning_headroom_counts_every_piece_that_can_help() -> None:
    manager = _manager()
    pieces = [
        piece_tuning(_armor("甲", _stats(weapons=0, grenade=0)), manager),
        piece_tuning(_armor("乙", _stats(weapons=0, grenade=0)), manager),
    ]
    headroom = tuning_headroom([piece for piece in pieces if piece])

    assert headroom["grenade"] == 10  # 每件最多 +5 手雷
    assert headroom["weapons"] == 10


# ── 分配器 ───────────────────────────────────────────────────────────


def _piece(name: str, stats: ArmorStats, tuning: int | None = None):
    manager = _manager()
    piece = piece_tuning(_armor(name, stats, tuning=tuning), manager)
    assert piece is not None
    return piece


def test_plan_closes_a_five_point_gap_with_one_change() -> None:
    piece = _piece("腿甲", _stats(weapons=40, health=30, grenade=15))

    plan = plan_tuning(
        [piece],
        stats_now=(40, 30, 0, 15, 0, 0),
        targets={"weapons": 20, "grenade": 20},
    )

    assert plan.feasible is True
    assert plan.change_count == 1
    change = plan.changes[0]
    assert change.increased == "grenade"
    assert plan.final_delta[STAT_NAMES.index("grenade")] == 5
    # 这件护甲的近战本来就是 0，−5 打上去是白给的：不用动任何非零项
    assert change.decreased == "melee"


def test_plan_refuses_when_the_decreased_side_would_break_a_target() -> None:
    """六维都非零、且都卡在目标上：想补手雷就必须从别项拿 5 点，一拿就掉破。"""
    piece = _piece(
        "腿甲",
        _stats(weapons=20, health=30, class_stat=1, grenade=15, melee=1, super_stat=1),
    )

    plan = plan_tuning(
        [piece],
        stats_now=(20, 30, 1, 15, 1, 1),
        targets={
            "weapons": 20, "health": 30, "class_stat": 1,
            "grenade": 20, "melee": 1, "super_stat": 1,
        },
    )

    assert plan.feasible is False
    assert "掉破目标" in plan.reason


def test_plan_spends_two_pieces_on_a_ten_point_gap() -> None:
    pieces = [
        _piece("甲", _stats(weapons=40, health=30, grenade=10)),
        _piece("乙", _stats(weapons=40, health=30, grenade=10)),
    ]

    plan = plan_tuning(
        [*pieces],
        stats_now=(80, 60, 0, 20, 0, 0),
        targets={"grenade": 30, "weapons": 40, "health": 40},
    )

    assert plan.feasible is True
    assert plan.change_count == 2
    assert plan.final_delta[STAT_NAMES.index("grenade")] == 10


def test_plan_can_undo_a_tuning_that_hurts_the_target() -> None:
    """现在是 +武器/−手雷，而手雷差 5 点：撤掉这次调谐就能补上。

    生命值也卡在目标上（−5 会掉破），所以这一件只剩"撤回调谐"这一条路。
    """
    piece = _piece(
        "腿甲",
        _stats(weapons=35, health=5, grenade=15),
        tuning=directional_tuning_hash("weapons", "grenade"),
    )

    plan = plan_tuning(
        [piece],
        stats_now=(35, 5, 0, 15, 0, 0),
        targets={"grenade": 20, "health": 5},
    )

    assert plan.feasible is True
    assert plan.change_count == 1
    # 换个"不再减手雷"的调谐即可：手雷 +5，被减的那一项本来就是 0（夹在 0 不动），
    # 比直接撤掉调谐更好 —— 撤掉会把武器 +5 也一起丢掉。
    change = plan.changes[0]
    assert change.delta[STAT_NAMES.index("grenade")] == 5
    assert all(
        value >= 0 for index, value in enumerate(change.delta)
        if index != STAT_NAMES.index("grenade")
    )


def test_plan_rejects_a_gap_larger_than_all_five_pieces_can_cover() -> None:
    pieces = [_piece(f"件{index}", _stats(weapons=50, grenade=0)) for index in range(5)]

    plan = plan_tuning(
        pieces,
        stats_now=(250, 0, 0, 0, 0, 0),
        targets={"grenade": 26},
    )

    assert plan.feasible is False
    assert "调谐能力" in plan.reason


def test_plan_prefers_fewer_changes_when_both_work() -> None:
    pieces = [
        _piece("甲", _stats(weapons=50, grenade=10)),
        _piece("乙", _stats(weapons=50, grenade=10)),
    ]

    plan = plan_tuning(
        pieces,
        stats_now=(100, 0, 0, 20, 0, 0),
        targets={"grenade": 25},
    )

    assert plan.feasible is True
    assert plan.change_count == 1


def test_plan_without_any_tunable_piece_says_so() -> None:
    plan = plan_tuning([], stats_now=(0, 0, 0, 0, 0, 0), targets={"grenade": 5})

    assert plan.feasible is False
    assert "没有带调谐槽" in plan.reason


def test_plan_reports_a_cap_instead_of_claiming_infeasible() -> None:
    """搜到上限就如实说"没搜完"，不能把"没搜到"说成"不可能"。"""
    pieces = [
        _piece(f"件{index}", _stats(weapons=60, health=60, grenade=0)) for index in range(5)
    ]

    plan = plan_tuning(
        pieces,
        stats_now=(300, 300, 0, 0, 0, 0),
        targets={"grenade": 24},
        max_nodes=1,
    )

    assert plan.feasible is False
    assert "上限" in plan.reason


def test_plan_uses_balanced_tuning_for_a_one_point_gap() -> None:
    """六维都非零、又都卡在目标上：只有平衡调整（六维 +1）不伤任何一项。"""
    piece = _piece(
        "腿甲",
        _stats(weapons=20, health=20, class_stat=1, grenade=20, melee=1, super_stat=1),
    )

    plan = plan_tuning(
        [piece],
        stats_now=(20, 20, 1, 20, 1, 1),
        targets={
            "weapons": 20, "health": 20, "class_stat": 1,
            "grenade": 21, "melee": 1, "super_stat": 1,
        },
    )

    assert plan.feasible is True
    assert plan.change_count == 1
    assert plan.changes[0].to_plug == BALANCED_HASH


def test_plan_is_deterministic() -> None:
    pieces = [
        _piece("甲", _stats(weapons=50, health=50, grenade=10)),
        _piece("乙", _stats(weapons=50, health=50, grenade=10)),
    ]
    args = {
        "stats_now": (100, 100, 0, 20, 0, 0),
        "targets": {"grenade": 25, "weapons": 60},
    }

    first = plan_tuning(pieces, **args)
    second = plan_tuning(pieces, **args)

    assert [change.as_dict() for change in first.changes] == [
        change.as_dict() for change in second.changes
    ]


def test_plan_delta_payload_only_lists_changed_stats() -> None:
    piece = _piece("腿甲", _stats(weapons=40, health=30, grenade=15))

    plan = plan_tuning([piece], stats_now=(40, 30, 0, 15, 0, 0), targets={"grenade": 20})
    payload = plan.as_dict()

    assert payload["change_count"] == 1
    # 代价那一项本来就见底（职业 = 0），所以净变化只有手雷 +5
    assert payload["changes"][0]["delta"] == {"grenade": 5}
    assert payload["final_delta"] == {"grenade": 5}


@pytest.mark.parametrize("kind", ["directional", "balanced", "empty"])
def test_choice_as_dict_keeps_hash_and_kind(kind: str) -> None:
    catalog = tuning_catalog(_manager())
    choice = next(choice for choice in catalog if choice.kind == kind)

    payload = choice.as_dict()
    assert payload["hash"] == choice.plug_hash
    assert payload["kind"] == kind


def test_dumping_a_stat_that_is_already_zero_is_free() -> None:
    """T5 词条本来就只有三项非零：把 −5 打在本就为 0 的项上，净代价是 0。"""
    piece = _piece("腿甲", _stats(weapons=30, health=25, grenade=20))

    plan = plan_tuning(
        [piece],
        stats_now=(30, 25, 0, 20, 0, 0),
        targets={"weapons": 30, "health": 25, "grenade": 25},
    )

    assert plan.feasible is True
    assert plan.change_count == 1
    change = plan.changes[0]
    assert change.increased == "grenade"
    assert change.delta[STAT_NAMES.index("grenade")] == 5
    assert change.delta[STAT_NAMES.index("weapons")] == 0, "武器不能被扣"
    assert min(change.delta) == 0, "整条改动不应该有任何负数（减的那项已经见底）"
