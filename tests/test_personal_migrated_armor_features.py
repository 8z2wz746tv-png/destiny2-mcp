"""Regression tests for Armor 3.0 rules ported to the personal edition."""

from __future__ import annotations

from dataclasses import replace

from destiny_mcp.build.armor_rules import (
    ARMOR_ARCHETYPES,
    all_tier5_templates,
    is_valid_tier5_template,
    masterworked_stats,
    tier5_templates,
)
from destiny_mcp.build.constants import STAT_NAMES
from destiny_mcp.build.farm_target import find_farm_targets
from destiny_mcp.build.models import Armor, BuildConstraints, InventorySnapshot
from destiny_mcp.models import ArmorStats


def test_armor3_defines_exactly_forty_eight_legal_tier_five_templates() -> None:
    templates = all_tier5_templates()

    assert len(ARMOR_ARCHETYPES) == 12
    assert len(templates) == 48
    assert len(set(templates)) == 48
    for archetype in ARMOR_ARCHETYPES:
        generated = tier5_templates(archetype)
        assert len(generated) == 4
        for template in generated:
            assert template.gear_tier == 5
            assert tuple(sorted(template.base_stats)) == (0, 0, 0, 20, 25, 30)
            assert template.base_stats[STAT_NAMES.index(archetype.primary_stat)] == 30
            assert template.base_stats[STAT_NAMES.index(archetype.secondary_stat)] == 25
            assert template.base_stats[STAT_NAMES.index(template.tertiary_stat)] == 20
            assert is_valid_tier5_template(template)


def test_armor3_rejects_tampered_or_impossible_templates() -> None:
    valid = all_tier5_templates()[0]
    tampered_stats = list(valid.base_stats)
    tampered_stats[tampered_stats.index(0)] = 1
    tampered = replace(valid, base_stats=tuple(tampered_stats))
    impossible_tertiary = replace(valid, tertiary_stat=valid.archetype.primary_stat)

    assert not is_valid_tier5_template(tampered)
    assert not is_valid_tier5_template(impossible_tertiary)
    assert masterworked_stats(tampered) is None
    assert tier5_templates("not-a-manifest-hash") == ()


def _equipped_armor(slot: str) -> Armor:
    return Armor(
        item_instance_id=f"equipped-{slot}",
        item_hash=1,
        name=f"Equipped {slot}",
        slot=slot,
        stats=ArmorStats(class_stat=30),
        is_equipped=True,
        tier=5,
    )


def test_farm_target_returns_only_canonical_tier_five_rolls() -> None:
    snapshot = InventorySnapshot(
        gauntlets=[_equipped_armor("gauntlets")],
        chests=[_equipped_armor("chests")],
        legs=[_equipped_armor("legs")],
        class_items=[_equipped_armor("class_items")],
    )
    legal = {
        (template.archetype.plug_hash, template.tertiary_stat, template.base_stats)
        for template in all_tier5_templates()
    }

    analysis = find_farm_targets(
        snapshot,
        BuildConstraints(class_stat_min=150),
        replacement_slot="helmet",
        top_n=48,
    )

    assert analysis.reason == "farm_target_ready"
    assert analysis.farm_options
    for option in analysis.farm_options:
        base_stats = tuple(option.base_stats.get(name) for name in STAT_NAMES)
        assert (
            option.archetype_hash,
            option.tertiary_stat,
            base_stats,
        ) in legal
        assert tuple(sorted(base_stats)) == (0, 0, 0, 20, 25, 30)
        assert option.replacement_slot == "helmets"
