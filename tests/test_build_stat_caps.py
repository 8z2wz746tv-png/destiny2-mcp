"""P2：属性上限（`stat_caps`）的语义 + 可达区间。

口径（`docs/plans/SOLVER_OPTIMALITY_PLAN.md` 决定 1）：

- 上限**不是**硬约束：超了照样出解，只是①不再往那一项上堆模组、②排到没超上限的方案后面、
  ③在结果里逐项标注（`max_violations`）；
- 不传 = 不限（**不许**悄悄按 100 截断 —— 200 才是游戏上限）；
- 上限低于下限是自相矛盾，直接报错，不偷偷按某一头截断；
- 有解时也要给"每项单独能顶到多少"（`reachable`），并且必须带上
  "逐项可达 ≠ 同时可达"这句话。
"""

from __future__ import annotations

import pytest

from destiny_mcp.build.constants import STAT_NAMES
from destiny_mcp.build.models import NAME_TO_STAT_HASH
from destiny_mcp.build.models import (
    Armor,
    ArmorStats,
    BuildConstraints,
    BuildRequest,
    InventorySnapshot,
    StatModDefinition,
)
from destiny_mcp.build.solver import prepare_fixed_set_context, solve
from destiny_mcp.exceptions import BuildValidationError


def _armor(slot: str, stats: dict[str, int], *, energy: int = 10) -> Armor:
    """`energy` 默认给满：不给能量的话求解器连模组都装不了，测不出"上限管住模组"。"""
    return Armor(
        item_instance_id=f"probe-{slot}-{'-'.join(map(str, stats.values()))}",
        item_hash=hash(slot) % 1000,
        name=slot,
        slot=slot,
        stats=ArmorStats(**stats),
        energy_capacity=energy,
        armor_system="armor_3",
        gear_tier=5,
    )


def _snapshot(armors: list[Armor], *, grenade_mod: bool = False) -> InventorySnapshot:
    buckets: dict[str, list[Armor]] = {
        "helmets": [], "gauntlets": [], "chests": [], "legs": [], "class_items": [],
    }
    for armor in armors:
        buckets[armor.slot].append(armor)
    # `stat_hash` 走单一出处 NAME_TO_STAT_HASH（手写 hash 很容易写成别的属性 ——
    # 这条用例的第一版就把 2996146975（武器）当成了手雷，于是"没超上限能堆更高"验不出来）。
    mods = (
        [StatModDefinition(
            hash=7001,
            stat_hash=NAME_TO_STAT_HASH["grenade"],
            value=10, energy_cost=3, kind="general",
        )] if grenade_mod else []
    )
    return InventorySnapshot(**buckets, stat_mod_definitions=mods)


# ── 上限进 desired_max：模组不再往已经到上限的属性上加 ───────────────────


def test_caps_become_the_desired_max_for_mods() -> None:
    """`desired_max` 是模组分配的预算上限：给了 caps 就按 caps，没给就退回老口径。"""
    snapshot = _snapshot([_armor(slot, {"grenade": 100}) for slot in
                          ("helmets", "gauntlets", "chests", "legs", "class_items")])

    capped = prepare_fixed_set_context(
        snapshot, BuildConstraints(grenade_min=50, grenade_max=100)
    )
    uncapped = prepare_fixed_set_context(
        snapshot, BuildConstraints(grenade_min=50, priority_stat_indices=[3])
    )
    no_priority = prepare_fixed_set_context(snapshot, BuildConstraints(grenade_min=50))

    grenade = STAT_NAMES.index("grenade")
    weapons = STAT_NAMES.index("weapons")
    assert capped.desired_max[grenade] == 100, "上限就是模组能加到的地方"
    assert capped.desired_max[weapons] == capped.desired_min[weapons], "没给上限的非优先项照旧压在底线上"
    assert uncapped.desired_max[grenade] == 200, "优先属性没给上限时照旧可以吃满"
    assert no_priority.desired_max[grenade] == 200, "没有优先级也没有上限 → 不限"


def test_caps_never_drop_below_the_minimum() -> None:
    """从别的入口直接调 `solve()` 时，`desired_max` 也不许低于 `desired_min`。"""
    snapshot = _snapshot([_armor(slot, {"grenade": 100}) for slot in
                          ("helmets", "gauntlets", "chests", "legs", "class_items")])

    context = prepare_fixed_set_context(
        snapshot, BuildConstraints(grenade_min=120, grenade_max=100)
    )

    grenade = STAT_NAMES.index("grenade")
    assert context.desired_max[grenade] >= context.desired_min[grenade]


def test_the_solver_does_not_stack_mods_past_a_given_cap() -> None:
    """手雷 95 + 一颗 +10 的手雷模组：不给上限会到 105，给了 100 就停在 100 以内。"""
    snapshot = _snapshot(
        [_armor(slot, {"grenade": 19}) for slot in
         ("helmets", "gauntlets", "chests", "legs", "class_items")],
        grenade_mod=True,
    )

    capped = solve(snapshot, BuildConstraints(grenade_min=95, grenade_max=100))
    uncapped = solve(snapshot, BuildConstraints(grenade_min=95))

    grenade = STAT_NAMES.index("grenade")
    capped_best = max(entry.stats[grenade] + entry.bonus_stats[grenade] for entry in capped.sets)
    uncapped_best = max(entry.stats[grenade] + entry.bonus_stats[grenade] for entry in uncapped.sets)
    assert capped_best <= 100, f"给了上限 100 还堆到 {capped_best}"
    assert uncapped_best > capped_best, "不给上限时应该能堆更高（否则这条用例没在验东西）"


# ── 超上限不阻止出解，但要排到后面 ───────────────────────────────────────


def test_over_cap_solutions_rank_after_clean_ones() -> None:
    """一套"手雷很高但超上限"、一套"手雷低但没超" —— 没超的必须排前面。"""
    clean = _armor("helmets", {"grenade": 30})
    dirty = _armor("helmets", {"grenade": 200})
    others = [_armor(slot, {"grenade": 0}) for slot in
              ("gauntlets", "chests", "legs", "class_items")]

    result = solve(
        _snapshot([clean, dirty, *others]),
        BuildConstraints(grenade_max=100, priority_stat_indices=[3]),
    )

    grenade = STAT_NAMES.index("grenade")
    ranked = [entry for entry in result.sets]
    assert ranked, "上限是软约束：照样要出解"
    assert ranked[0].stats[grenade] == 30, "没超上限的那套排第一"
    assert any(entry.stats[grenade] == 200 for entry in ranked), "超上限的那套也要在（只是靠后）"


def test_cap_violations_are_annotated_per_build() -> None:
    """对外字段 `max_violations`：哪一项、实际多少、上限多少。"""
    from destiny_mcp.services.build_results import _max_violations
    from destiny_mcp.build.models import BuildCandidate

    candidate = BuildCandidate(
        items=[], grenade=120, super_stat=110, weapons=100,
    )
    constraints = BuildConstraints(grenade_max=100, super_stat_max=100)

    violations = {row["stat"]: row for row in _max_violations(candidate, constraints)}

    assert set(violations) == {"grenade", "super_stat"}
    assert violations["grenade"]["actual"] == 120
    assert violations["grenade"]["max"] == 100
    assert violations["grenade"]["label"] == "手雷", "对外要带中文标签"


def test_no_caps_means_no_violations_reported() -> None:
    from destiny_mcp.build.models import BuildCandidate
    from destiny_mcp.services.build_results import _max_violations

    candidate = BuildCandidate(items=[], grenade=200, super_stat=200)

    assert _max_violations(candidate, BuildConstraints()) == []


# ── 输入合法性：不许静默忽略 ─────────────────────────────────────────────


def _parse(**kwargs):
    from destiny_mcp.build.constraints import parse

    class _Manifest:
        def search(self, *args, **kwargs):
            return []

    return parse(BuildRequest(character_class="titan", **kwargs), _Manifest())


def test_unknown_cap_key_is_an_error_not_a_silent_drop() -> None:
    """上限被悄悄忽略比报错更糟：用户会拿到自己声明过不要超过的配装。"""
    with pytest.raises(BuildValidationError, match="不认识的上限属性"):
        _parse(stat_caps={"速度": 100})


def test_cap_accepts_chinese_keys() -> None:
    constraints = _parse(grenade_target=100, stat_caps={"手雷": 150, "super": 100})

    assert constraints.grenade_max == 150
    assert constraints.super_stat_max == 100


def test_cap_below_target_is_rejected() -> None:
    with pytest.raises(BuildValidationError, match="上限.*下限"):
        _parse(weapons_target=100, stat_caps={"weapons": 50})


def test_cap_out_of_range_is_rejected() -> None:
    with pytest.raises(BuildValidationError, match="0-200"):
        _parse(stat_caps={"grenade": 999})


# ── 可达区间：有解时也要给 ───────────────────────────────────────────────


def test_reachable_ceilings_are_returned_by_the_solver() -> None:
    snapshot = _snapshot([_armor(slot, {"grenade": 20}) for slot in
                          ("helmets", "gauntlets", "chests", "legs", "class_items")])

    result = solve(snapshot, BuildConstraints(grenade_min=50))

    grenade = STAT_NAMES.index("grenade")
    assert len(result.reachable_ceilings) == 6
    assert result.reachable_ceilings[grenade] >= 100, "五件 ×20 = 100 是这套的下限"


def test_reachable_payload_carries_the_marginal_warning() -> None:
    """逐项可达 ≠ 同时可达：这句话必须跟数字一起出去（阶梯那边已经钉过同一条）。"""
    from destiny_mcp.build.process_types import ProcessResult

    payload = ProcessResult(
        combos=3, reachable_ceilings=[100, 20, 30, 40, 50, 60]
    ).diagnostics.to_dict()

    assert payload["reachable"]["weapons"] == 100
    # 两句话缺一不可：① 保守下界（不是上限）② 逐项可达 ≠ 同时达到
    assert "保守下界" in payload["reachable_note"]
    assert "≠" in payload["reachable_note"] and "同时达到" in payload["reachable_note"]


def test_reachable_is_absent_when_not_computed() -> None:
    """没算就不给 —— 空字典而不是一排 0（0 会被读成"顶不上去"）。"""
    from destiny_mcp.build.process_types import ProcessResult

    payload = ProcessResult().diagnostics.to_dict()

    assert payload["reachable"] == {}
    assert payload["reachable_note"] is None
