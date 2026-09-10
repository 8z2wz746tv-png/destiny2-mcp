"""Regression tests for complete farm-roll enumeration and exact inventory search."""

from __future__ import annotations

from collections import defaultdict

import destiny_mcp.build.farm_target as farm_target_module
from destiny_mcp.build.farm_target import _virtual_rolls, find_farm_targets
from destiny_mcp.build.models import Armor, ArmorStats, BuildConstraints, InventorySnapshot
from destiny_mcp.tools._farm_target_response import serialize_farm_target_analysis


_LOCKED_SLOTS = ("gauntlets", "chests", "legs", "class_items")


def _armor(
    slot: str,
    name: str,
    *,
    class_stat: int = 0,
    weapons: int = 0,
    is_equipped: bool = False,
    is_exotic: bool = False,
    item_hash: int = 1,
) -> Armor:
    return Armor(
        item_instance_id=f"{slot}:{name}",
        item_hash=item_hash,
        name=name,
        slot=slot,
        stats=ArmorStats(class_stat=class_stat, weapons=weapons),
        is_equipped=is_equipped,
        is_exotic=is_exotic,
        tier=5,
    )


def test_virtual_rolls_include_every_untuned_balanced_and_directional_result() -> None:
    rolls = _virtual_rolls()
    by_template: dict[tuple[int, str], list[object]] = defaultdict(list)
    for roll in rolls:
        key = (roll.template.archetype.plug_hash, roll.template.tertiary_stat)
        by_template[key].append(roll.tuning)

    assert len(by_template) == 48
    assert len(rolls) == 48 * 32
    for tunings in by_template.values():
        assert sum(tuning is None for tuning in tunings) == 1
        assert sum(
            tuning is not None and tuning.kind == "balanced" for tuning in tunings
        ) == 1
        assert sum(
            tuning is not None and tuning.kind == "directional"
            for tuning in tunings
        ) == 30


def test_inventory_search_finds_best_roll_beyond_old_per_slot_cap() -> None:
    snapshot = InventorySnapshot()
    for slot in _LOCKED_SLOTS:
        # These 32 higher-total decoys filled the old per-slot cap, even though
        # none can meet the requested class-stat target.
        for index in range(32):
            getattr(snapshot, slot).append(
                _armor(slot, f"decoy-{index:02d}", weapons=100)
            )
        getattr(snapshot, slot).append(
            _armor(slot, "required-class-roll", class_stat=30)
        )

    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=150, priority_stat_indices=[2]),
        baseline="inventory",
        replacement_slot="helmet",
        top_n=1,
    )

    assert analysis.reason == "farm_target_ready"
    assert len(analysis.farm_options) == 1
    assert analysis.farm_options[0].projected_total.class_stat == 155
    assert {item["name"] for item in analysis.farm_options[0].locked_items} == {
        "required-class-roll"
    }


def test_inventory_search_node_limit_is_an_explicit_public_error(
    monkeypatch,
) -> None:
    snapshot = InventorySnapshot()
    for slot in _LOCKED_SLOTS:
        getattr(snapshot, slot).append(_armor(slot, f"only-{slot}"))
    monkeypatch.setattr(farm_target_module, "_MAX_INVENTORY_SEARCH_NODES", 0)

    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(),
        baseline="inventory",
        replacement_slot="helmet",
    )
    serialized = serialize_farm_target_analysis(analysis)

    assert analysis.farm_options == []
    assert analysis.reason == "inventory_search_too_large"
    assert serialized["reason"] == "inventory_search_too_large"


def test_two_piece_fallback_returns_minimum_legal_farm_plan() -> None:
    snapshot = InventorySnapshot(
        helmets=[_armor("helmets", "weak-helmet", is_equipped=True)],
        gauntlets=[_armor("gauntlets", "weak-gauntlets", is_equipped=True)],
        chests=[_armor("chests", "strong-chest", class_stat=45, is_equipped=True)],
        legs=[_armor("legs", "strong-legs", class_stat=45, is_equipped=True)],
        class_items=[
            _armor("class_items", "strong-class", class_stat=45, is_equipped=True)
        ],
    )

    single = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=200),
        max_replacements=1,
    )
    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=200),
        max_replacements=2,
    )

    assert single.reason == "single_replacement_insufficient"
    assert analysis.reason == "farm_target_ready"
    assert analysis.farm_options == []
    plan = analysis.farm_plans[0]
    assert plan.replacement_count == 2
    assert {piece.replacement_slot for piece in plan.pieces} == {
        "helmets",
        "gauntlets",
    }
    assert plan.projected_total.class_stat >= 200
    assert {item["name"] for item in plan.locked_items} == {
        "strong-chest",
        "strong-legs",
        "strong-class",
    }
    for piece in plan.pieces:
        assert sorted(piece.base_stats.model_dump().values()) == [0, 0, 0, 20, 25, 30]


def test_public_serializer_hides_two_piece_internal_metadata() -> None:
    snapshot = InventorySnapshot(
        helmets=[_armor("helmets", "weak-helmet", is_equipped=True)],
        gauntlets=[_armor("gauntlets", "weak-gauntlets", is_equipped=True)],
        chests=[_armor("chests", "strong-chest", class_stat=45, is_equipped=True)],
        legs=[_armor("legs", "strong-legs", class_stat=45, is_equipped=True)],
        class_items=[
            _armor("class_items", "strong-class", class_stat=45, is_equipped=True)
        ],
    )

    serialized = serialize_farm_target_analysis(
        find_farm_targets(
            snapshot,
            BuildConstraints(class_stat_min=200),
            max_replacements=2,
        )
    )

    plan = serialized["farm_plans"][0]
    assert plan["replacement_count"] == 2
    assert plan["projected_total"]["class"] >= 200
    for field in (
        "archetype_hash",
        "tuning_hash",
        "stat_mods",
        "item_instance_id",
        "canonical_build",
        "execution_id",
    ):
        assert field not in str(plan)


def test_two_piece_fallback_keeps_requested_slot_and_exotic_locked() -> None:
    snapshot = InventorySnapshot(
        helmets=[
            _armor(
                "helmets",
                "requested-exotic",
                is_equipped=True,
                is_exotic=True,
                item_hash=7,
            )
        ],
        gauntlets=[_armor("gauntlets", "weak-gauntlets", is_equipped=True)],
        chests=[_armor("chests", "weak-chest", is_equipped=True)],
        legs=[_armor("legs", "strong-legs", class_stat=45, is_equipped=True)],
        class_items=[
            _armor("class_items", "strong-class", class_stat=45, is_equipped=True)
        ],
    )

    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(
            class_stat_min=155,
            exotic_hash=7,
            exotic_hashes={7},
        ),
        replacement_slot="gauntlets",
        max_replacements=2,
    )

    plan = analysis.farm_plans[0]
    assert "gauntlets" in {piece.replacement_slot for piece in plan.pieces}
    assert any(item["name"] == "requested-exotic" for item in plan.locked_items)


def test_two_piece_request_fails_closed_for_inventory_and_invalid_limit() -> None:
    snapshot = InventorySnapshot(
        **{
            slot: [_armor(slot, slot)]
            for slot in ("helmets", "gauntlets", "chests", "legs", "class_items")
        }
    )

    inventory = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=200),
        baseline="inventory",
        max_replacements=2,
    )
    invalid = find_farm_targets(
        snapshot,
        BuildConstraints(),
        max_replacements=3,
    )

    assert inventory.reason == "inventory_multi_replacement_unavailable"
    assert invalid.reason.startswith("invalid_max_replacements")


def test_two_piece_search_budget_is_not_reported_as_no_solution(monkeypatch) -> None:
    snapshot = InventorySnapshot(
        helmets=[_armor("helmets", "weak-helmet", is_equipped=True)],
        gauntlets=[_armor("gauntlets", "weak-gauntlets", is_equipped=True)],
        chests=[_armor("chests", "strong-chest", class_stat=45, is_equipped=True)],
        legs=[_armor("legs", "strong-legs", class_stat=45, is_equipped=True)],
        class_items=[
            _armor("class_items", "strong-class", class_stat=45, is_equipped=True)
        ],
    )
    monkeypatch.setattr(farm_target_module, "_MAX_TWO_PIECE_SEARCH_NODES", 0)

    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=200),
        max_replacements=2,
    )

    assert analysis.reason == "farm_plan_search_too_large"
