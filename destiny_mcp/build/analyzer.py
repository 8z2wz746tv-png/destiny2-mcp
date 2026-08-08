"""Build Analyzer — explains why a build request has no solution.

When the solver returns zero candidates, the Analyzer computes the maximum
achievable stats from the current inventory and identifies which targets
cannot be met. It also suggests farming activities for each stat.
"""

from __future__ import annotations

from ..logging_config import get_logger
from .models import (
    STAT_NAMES,
    BuildAnalysis,
    BuildConstraints,
    InventorySnapshot,
)

logger = get_logger(__name__)

# Which stats are hard to farm (needs specific activities)
_STAT_ADVICE: dict[str, str] = {
    "weapons": "Focus armor engrams at the HELM with Weapons Ghost mod",
    "health": "Focus armor engrams at the HELM with Health Ghost mod",
    "class_stat": "Focus armor engrams at the HELM with Class Ghost mod",
    "grenade": "Focus armor engrams at the HELM with Grenade Ghost mod",
    "melee": "Focus armor engrams at the HELM with Melee Ghost mod",
    "super_stat": "Focus armor engrams at the HELM with Super Ghost mod",
}


def analyze(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> BuildAnalysis:
    """Analyze why no build satisfies the given constraints.

    Args:
        snapshot: The player's armor inventory.
        constraints: The target constraints.

    Returns:
        BuildAnalysis with failure reason and farming suggestions.
    """
    max_possible = _max_possible_stats(snapshot, constraints)

    # Check each target
    failures: list[str] = []
    suggestions: list[str] = []
    for stat_name in STAT_NAMES:
        target = getattr(constraints, f"{stat_name}_min")
        if target > 0 and target > max_possible[stat_name]:
            gap = target - max_possible[stat_name]
            failures.append(
                f"{stat_name}: need {target}, max possible is {max_possible[stat_name]} "
                f"(gap: {gap})"
            )
            if stat_name in _STAT_ADVICE:
                suggestions.append(_STAT_ADVICE[stat_name])

    # Deduplicate suggestions
    suggestions = list(dict.fromkeys(suggestions))

    if failures:
        reason = (
            f"Cannot meet {len(failures)} stat target(s) with current inventory. "
            + "; ".join(failures)
        )
    else:
        reason = "No valid armor combination found (constraints may conflict with exotic + stat requirements)"

    logger.info("Analyzer: %d failures, %d suggestions", len(failures), len(suggestions))
    return BuildAnalysis(
        reason=reason,
        max_possible=max_possible,
        suggested_farm=suggestions,
    )


def _max_possible_stats(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> dict[str, int]:
    """Compute max per-stat values with the same mod rules as the solver.

    The previous analyzer only summed raw armor stats, so it understated
    achievable values whenever +10 general mods or +3 artifice mods could
    close a gap. Probe the solver once per stat with that stat as the dump
    stat so diagnostics match the optimizer's real rules.
    """
    from .solver import solve

    bonus_vector = constraints.subclass_and_fragment_vector()
    max_possible: dict[str, int] = {}

    for stat_index, stat_name in enumerate(STAT_NAMES):
        probe = _probe_constraints_for_stat(constraints, stat_index)
        result = solve(snapshot, probe)
        if not result.sets:
            max_possible[stat_name] = bonus_vector[stat_index]
            continue

        max_possible[stat_name] = max(
            armor_set.stats[stat_index]
            + (
                armor_set.bonus_stats[stat_index]
                if stat_index < len(armor_set.bonus_stats)
                else 0
            )
            + bonus_vector[stat_index]
            for armor_set in result.sets
        )

    return max_possible


def _probe_constraints_for_stat(
    constraints: BuildConstraints,
    stat_index: int,
) -> BuildConstraints:
    """Build a solver probe that keeps non-stat constraints intact."""
    return BuildConstraints(
        exotic_hash=constraints.exotic_hash,
        exotic_hashes=set(constraints.exotic_hashes),
        class_type=constraints.class_type,
        top_n=200,
        subclass_stats=list(constraints.subclass_stats),
        fragment_stats=list(constraints.fragment_stats),
        set_bonus_hash=constraints.set_bonus_hash,
        set_bonus_count=constraints.set_bonus_count,
        priority_stat_index=stat_index,
    )
