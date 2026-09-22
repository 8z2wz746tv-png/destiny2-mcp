"""调谐（Tuning）纯计算层的特征测试。

口径来自实机 + Manifest（docs/plans/ARMOR_FORMAT_PLAN.md P7）：
30 个方向型（+5/−5，零和）+ 平衡调整（**恰好三项并列最低时各 +1**）+ 空插件；有调谐槽的护甲
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


def _MANIFEST_FOR_FIXTURES():
    return _manager()


def _all_tuning_hashes(manifest=None) -> tuple[int, ...]:
    """夹具里的件**允许装全部调谐**（真实清单来自组件 310，每件只有 6 颗）。"""
    from destiny_mcp.build.tuning import tuning_catalog

    return tuple(choice.plug_hash for choice in tuning_catalog(manifest or _manager()))


def _armor(
    name: str,
    stats: ArmorStats,
    *,
    tuning: int | None = None,
    item_hash: int = T5_ARMOR_HASH,
    legal: tuple[int, ...] | None = None,
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
        # `legal=None` = 夹具不模拟组件 310，显式声明"全部允许"；
        # 真机那条清单用 `legal=(…)` 传进来（下面的两条金标准就是）。
        tuning_option_hashes=_all_tuning_hashes() if legal is None else legal,
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


def test_balanced_tuning_bonus_lands_on_the_three_lowest_stats() -> None:
    """平衡调整给谁 +1：**恰好三项并列最低**的那三项 —— 真机实测（2026-09-22）。

    光芒领主面具未调谐基值 武器30/生命5/职业5/手雷30/超能25/近战5，装上平衡调整后
    武器30/生命6/职业6/手雷30/超能25/近战6：只有最低的三项各 +1，30 的那三项一动不动。
    Manifest 的 `investmentStats` 写的是"六维各 +1"，照它建模每项都会多算 1。
    证据脚本：`scripts/verify_tuning_write.py --apply --to 3122197216`（写进去 → 回读
    插槽 → 回读组件 304 的六维 → 换回原样）。
    """
    catalog = tuning_catalog(_manager())
    balanced = next(choice for choice in catalog if choice.kind == "balanced")

    # 目录里那份是**占位**（真实增量得看这件的基础六维才定得下来），所以它必须是全 0，
    # 而不是"六维各 +1"；同时它不能被当成空操作，否则求解器会直接跳过它。
    assert balanced.delta == (0, 0, 0, 0, 0, 0)
    assert balanced.is_noop is False

    # 真机那一件的现装调谐是 +超能/−生命值，观测六维是含它的值。
    piece = _piece(
        "面具",
        _stats(weapons=30, health=0, class_stat=5, grenade=30, super_stat=30, melee=5),
        tuning=directional_tuning_hash("super_stat", "health"),
    )

    # STAT_NAMES 顺序：武器/生命/职业/手雷/近战/超能
    assert piece.base == (30, 5, 5, 30, 5, 25)  # 生命 0 − (−5) = 5，超能 30 − 5 = 25
    assert piece.choice_delta(balanced) == (0, 1, 1, 0, 1, 0)  # 最低的三项：生命/职业/近战
    assert piece.stats_with(balanced) == (30, 6, 6, 30, 6, 25)  # 真机读到的就是这六个


def test_balanced_tuning_does_nothing_without_exactly_three_lowest() -> None:
    """并列最低不是恰好三项时平衡调整一项都不加（真机只验了"三项"这一档）。"""
    catalog = tuning_catalog(_manager())
    balanced = next(choice for choice in catalog if choice.kind == "balanced")

    piece = _piece("腿甲", _stats(weapons=30, grenade=30, health=0, super_stat=0))

    assert piece.choice_delta(balanced) == (0, 0, 0, 0, 0, 0)
    assert piece.stats_with(balanced) == piece.base


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


def _piece(
    name: str,
    stats: ArmorStats,
    tuning: int | None = None,
    legal: tuple[int, ...] | None = None,
):
    manager = _manager()
    piece = piece_tuning(_armor(name, stats, tuning=tuning, legal=legal), manager)
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
    # 被牺牲的那一项必须**没有目标**（这件上就是 生命/职业/超能/近战 里的一件）。
    # 具体挑哪一件由捐赠代价排序决定 —— 新算术下每种牺牲都一样贵，别把某一件写死。
    assert change.decreased not in {"weapons", "grenade"}


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
    # 换个"不再减手雷"的调谐即可：手雷 +5，代价是丢掉现在这条调谐给的武器 +5
    # （新算术下这笔代价如实出现在 delta 里，不再被夹 0 抹掉）。
    change = plan.changes[0]
    assert change.delta[STAT_NAMES.index("grenade")] == 5
    assert change.delta[STAT_NAMES.index("grenade")] > 0
    # 要害是**没有目标被掉破**：生命卡在 5，换完还得 ≥ 5
    after = tuple(
        now + delta for now, delta in zip((35, 5, 0, 15, 0, 0), change.delta)
    )
    assert after[STAT_NAMES.index("health")] >= 5
    assert after[STAT_NAMES.index("grenade")] >= 20


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
    """缺口正好落在"最低三项"之一、别的项又都卡在目标上：只有平衡调整不伤任何一项。

    （缺口必须在最低三项上：平衡调整只给那三项 +1。缺口在 20 那一档上是补不了的 ——
    这条就是被真机数据纠正过的旧夹具。）
    """
    piece = _piece(
        "腿甲",
        _stats(weapons=20, health=20, class_stat=1, grenade=20, melee=1, super_stat=1),
    )

    plan = plan_tuning(
        [piece],
        stats_now=(20, 20, 1, 20, 1, 1),
        targets={
            "weapons": 20, "health": 20, "class_stat": 2,
            "grenade": 20, "melee": 1, "super_stat": 1,
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


def change_decreased_of(payload: dict) -> str:
    """载荷里那项负收益的键名（代价项）。"""
    return next(
        key for key, value in payload["changes"][0]["delta"].items() if value < 0
    )


def test_plan_delta_payload_only_lists_changed_stats() -> None:
    piece = _piece("腿甲", _stats(weapons=40, health=30, grenade=15))

    plan = plan_tuning([piece], stats_now=(40, 30, 0, 15, 0, 0), targets={"grenade": 20})
    payload = plan.as_dict()

    assert payload["change_count"] == 1
    delta = payload["changes"][0]["delta"]
    assert delta["grenade"] == 5
    # 只列**变过**的项：手雷 +5 与代价那一项 −5（新算术下代价如实出现），
    # 没动的项（武器 40、生命 30）一个都不许出现
    assert set(delta) == {"grenade", change_decreased_of(payload)}
    assert min(delta.values()) == -5
    assert payload["final_delta"]["grenade"] == 5


@pytest.mark.parametrize("kind", ["directional", "balanced", "empty"])
def test_choice_as_dict_keeps_hash_and_kind(kind: str) -> None:
    catalog = tuning_catalog(_manager())
    choice = next(choice for choice in catalog if choice.kind == kind)

    payload = choice.as_dict()
    assert payload["hash"] == choice.plug_hash
    assert payload["kind"] == kind


def test_dumping_a_stat_that_is_already_zero_is_not_free() -> None:
    """把 −5 打在已经见底的项上**不是白给** —— 组件 304 如实记负数。

    真机证据（2026-09-22）：光芒领主手套 `6917530188462631525` 装着「+职业 / -生命值」，
    它的生命基础值是 0，而 304 里写的就是 **生命 −5** —— 游戏自己没夹 0。
    旧口径（"夹到 0，所以牺牲见底的项是白给"）会让反推（`observed − delta`）把被夹掉的
    5 点当成本来就有：贪心每走一步凭空长 5 点，真机同一件几步之后从
    武器25/生命−5/手雷0/超能0 变成 武器30/生命5/手雷5/超能5（用户发现的那个 bug）。

    这条测试钉的是"代价要如实出现在 delta 里"，不是"不许牺牲见底的项"：
    那一项**没有目标**，所以扣它仍然合法（游戏里也有这种装法，304 就是证据）。
    """
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
    assert change.delta[STAT_NAMES.index("weapons")] == 0, "武器不能被扣（它卡在目标上）"
    assert min(change.delta) == -5, "代价那一项必须如实记成 −5，不许被夹成 0"
    assert change.decreased in {"class_stat", "super_stat", "melee"}, (
        "被牺牲的必须是**没有目标**的那几项之一"
    )


# ── "不许凭空长点"：用户发现的那个 bug（2026-09-22） ─────────────────────


def test_piece_stats_change_by_exactly_the_tuning_delta() -> None:
    """换调谐，六维只能按那一对 ±5 变 —— 一项都不许多出来。

    旧实现：`base_with()` 把 `base + delta` **夹在 0**（生命 0 − 5 → 0），而反推
    `_invert_tuning` 又做 `observed − delta`（0 − (−5) = **+5**）—— 每"牺牲一项已经见底的
    属性"就凭空长 5 点。贪心对同一件走几步就累加：真机同一件从
    武器25/生命−5/手雷0/超能0 变成 武器30/生命5/手雷5/超能5（**多 15 点**）。

    真机案例（2026-09-22，`inventory_assistant(intent="item")`）：
    光芒领主手套 `6917530188462631525` 的组件 304 = 武器25 生命−5 职业35 手雷0 超能0 近战30
    （职业那 35 里有 10 点是模组，护甲面是 25），装着「+职业 / -生命值」。
    它的基础值 = 武器25 生命0 职业20 手雷0 超能0 近战30；换成「+武器 / -近战」之后
    必须是 武器30 生命0 职业20 手雷0 超能0 近战25。
    """
    piece = _piece(
        "光芒领主手套",
        _stats(weapons=25, health=-5, class_stat=25, grenade=0, super_stat=0, melee=30),
        tuning=directional_tuning_hash("class_stat", "health"),
    )

    assert piece.movable is True
    # STAT_NAMES 顺序：武器 生命 职业 手雷 近战 超能
    assert piece.base == (25, 0, 20, 0, 30, 0)
    assert piece.stats_with(None) == (25, -5, 25, 0, 30, 0)

    choice = piece.option(directional_tuning_hash("weapons", "melee"))
    assert choice is not None
    assert piece.stats_with(choice) == (30, 0, 20, 0, 25, 0), (
        "只有 +武器/−近战 生效；生命/手雷/超能 一个点都不许变"
    )
    before, after = piece.stats_with(None), piece.stats_with(choice)
    assert sum(after) - sum(before) == sum(piece.delta_of(choice))


def test_uninvertible_piece_is_not_movable_and_never_invents_stats(monkeypatch) -> None:
    """反推不出基础六维的件：`movable=False`、`note` 有话说，且**任何改法都返回观测值**。

    这一对（机器可读的 `movable` 与人话 `note`）必须同进同出；而 `stats_with` 在
    反推不出来时返回观测值 —— 宁可"不动它"，也不许编一个改法出来（以前那条兜底
    会把当前调谐再加一遍）。
    """
    from destiny_mcp.build import tuning as tuning_module

    monkeypatch.setattr(tuning_module, "_invert_tuning", lambda observed, current: None)
    piece = _piece("腿甲", _stats(weapons=30, health=25, grenade=20))

    assert piece.movable is False
    assert piece.note, "反推不出来必须有一句人话说明"
    observed = tuple(getattr(piece, "observed"))
    for choice in piece.options:
        assert piece.stats_with(choice) == observed, "不许编改法"


# ── 每件的可调谐属性来自组件 310（用户指出、真机核实） ───────────────────────


def _legal_for(increased: str, *others: str) -> tuple[int, ...]:
    """真机 310 里那种"某一个属性 +5 的 5 颗 + 平衡调整"清单。"""
    return (
        *(directional_tuning_hash(increased, other) for other in others),
        BALANCED_HASH,
    )


def test_tuning_options_are_per_piece_and_come_from_component_310() -> None:
    """每件允许装的调谐**不是** Manifest 那份全局 32 颗，而是组件 310 给的那 6 颗。

    真机（2026-09-22 读组件 310）：两件同名的「光芒领主手套」可调谐属性**不一样** ——
      - `6917530188462631525` = **职业**：只有 `+职业/−{手雷,武器,生命,超能,近战}` + 平衡调整；
      - `6917530191138019736` = **近战**：只有 `+近战/−{手雷,武器,生命,职业,超能}` + 平衡调整。
    所以"给这两件都换 +武器/−近战"是**装不上**的 —— 用户就是这样发现求解器在乱开选项的。
    """
    five = ("weapons", "health", "class_stat", "grenade", "super_stat", "melee")

    a = _piece(
        "光芒领主手套A",
        _stats(weapons=25, health=-5, class_stat=25, melee=30),
        tuning=directional_tuning_hash("class_stat", "health"),
        legal=_legal_for("class_stat", *(s for s in five if s != "class_stat")),
    )
    a_directional = {c.name for c in a.options if c.kind == "directional"}
    assert a_directional == {
        "+职业 / -手雷", "+职业 / -武器", "+职业 / -生命值", "+职业 / -超能", "+职业 / -近战",
    }, "这件只能加职业"
    assert "+武器 / -近战" not in {c.name for c in a.options}, "装不上的不许出现在选项里"
    assert any(c.kind == "balanced" for c in a.options), "平衡调整在 310 清单里，要保留"
    assert any(c.kind == "empty" for c in a.options), "撤掉调谐永远可以做"

    b = _piece(
        "光芒领主手套B",
        _stats(weapons=20, health=6, class_stat=6, grenade=30, super_stat=25, melee=6),
        tuning=BALANCED_HASH,
        legal=_legal_for("melee", *(s for s in five if s != "melee")),
    )
    b_directional = {c.name for c in b.options if c.kind == "directional"}
    assert b_directional == {
        "+近战 / -手雷", "+近战 / -武器", "+近战 / -生命值", "+近战 / -职业", "+近战 / -超能",
    }, "这件的可调谐属性是近战"
    assert not any(c.name.startswith("+武器") for c in b.options)


def test_piece_without_component_310_is_not_movable() -> None:
    """读不到 310 的清单：**缺数据 ≠ 允许** —— 不许规划任何改法，只留"撤掉"。

    以前求解器拿 Manifest 的全局 32 颗当选项，于是给"只能加职业"的件开了 `+武器/−近战`。
    """
    piece = _piece("腿甲", _stats(weapons=30, health=25, grenade=20), legal=())

    assert piece.movable is False
    assert "组件 310" in piece.note
    assert [choice.kind for choice in piece.options] == ["empty"]
