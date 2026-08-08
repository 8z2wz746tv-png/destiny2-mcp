"""Auto stat mod utilities for the Build Engine.

Translated from DIM's src/app/loadout-builder/process-worker/auto-stat-mod-utils.ts.
Handles precomputing all possible mod combinations per stat and recursively
finding mod assignments that satisfy stat targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .process_types import (
    ARTIFICE_STAT_BOOST,
    MAJOR_STAT_BOOST,
    MINOR_STAT_BOOST,
    AutoModData,
    ModDef,
    ModsPick,
    StatMods,
)

# ═══════════════════════════════════════════════════════════════════════════
# Cache types
# ═══════════════════════════════════════════════════════════════════════════

# CacheForStat: maps target_stat_value -> list of ModsPick
# AutoModsCache: maps stat_index -> CacheForStat
CacheForStat = dict[int, list[ModsPick]]
AutoModsCache = dict[int, CacheForStat]


# ═══════════════════════════════════════════════════════════════════════════
# build_cache_for_stat — precompute all mod combos for one stat
# ═══════════════════════════════════════════════════════════════════════════


def build_cache_for_stat(
    auto_mod_options: AutoModData,
    stat_hash: int,
    stat_index: int,
    available_general_stat_mods: int,
) -> CacheForStat:
    """Precompute all possible mod combinations for a single stat.

    Translated from DIM's buildCacheForStat function.

    For each combination of (artifice_mods, minor_mods, major_mods), compute
    the total stat value and store a ModsPick that achieves it.

    Args:
        auto_mod_options: Available mod definitions.
        stat_hash: The stat hash to build cache for.
        stat_index: Index of this stat in the priority order.
        available_general_stat_mods: Number of general mod slots available.

    Returns:
        Cache mapping target_stat_value -> list of ModsPick.
    """
    cache: CacheForStat = {}

    artifice_mod = auto_mod_options.artifice_mods.get(stat_hash)
    stat_mods = auto_mod_options.general_mods.get(stat_hash)
    minor_mod = stat_mods.minor_mod if stat_mods else None
    major_mod = stat_mods.major_mod if stat_mods else None

    max_artifice = 5 if artifice_mod else 0
    max_minor = available_general_stat_mods if minor_mod else 0
    max_major = available_general_stat_mods if major_mod else 0

    for num_artifice in range(max_artifice + 1):
        for num_minor in range(max_minor + 1):
            for num_major in range(min(available_general_stat_mods - num_minor, max_major) + 1):
                stat_value = (
                    num_artifice * ARTIFICE_STAT_BOOST
                    + num_minor * MINOR_STAT_BOOST
                    + num_major * MAJOR_STAT_BOOST
                )
                if stat_value == 0:
                    continue

                # We allow overshooting within reason:
                # - With artifice mods: overshoot by (ARTIFICE_STAT_BOOST - 1)
                # - Without artifice: overshoot by (MINOR_STAT_BOOST - 1)
                lower_range = stat_value - (
                    ARTIFICE_STAT_BOOST - 1 if num_artifice > 0 else MINOR_STAT_BOOST - 1
                )

                general_costs: list[int] = []
                mod_hashes: list[int] = []
                if major_mod:
                    general_costs.extend([major_mod.cost] * num_major)
                    mod_hashes.extend([major_mod.hash] * num_major)
                if minor_mod:
                    general_costs.extend([minor_mod.cost] * num_minor)
                    mod_hashes.extend([minor_mod.hash] * num_minor)
                if artifice_mod:
                    mod_hashes.extend([artifice_mod] * num_artifice)

                energy_cost = 0
                if minor_mod:
                    energy_cost += num_minor * minor_mod.cost
                if major_mod:
                    energy_cost += num_major * major_mod.cost

                pick = ModsPick(
                    num_artifice_mods=num_artifice,
                    num_general_mods=num_minor + num_major,
                    general_mods_costs=sorted(general_costs, reverse=True),
                    mod_hashes=mod_hashes,
                    mod_energy_cost=energy_cost,
                    target_stat_index=stat_index,
                    exact_stat_points=stat_value,
                )

                for achievable_value in range(lower_range, stat_value + 1):
                    if achievable_value not in cache:
                        cache[achievable_value] = []
                    cache[achievable_value].append(pick)

    # Sort picks: prefer artifice mods (free), then fewer general mods
    # (preserves slots for other stats), then less energy cost
    for pick_array in cache.values():
        pick_array.sort(key=lambda p: (-p.num_artifice_mods, p.num_general_mods, p.mod_energy_cost))

    return cache


# ═══════════════════════════════════════════════════════════════════════════
# build_auto_mods_map — build the full cache for all stats
# ═══════════════════════════════════════════════════════════════════════════


def build_auto_mods_map(
    auto_mod_options: AutoModData,
    available_general_stat_mods: int,
    stat_order: list[int],
) -> AutoModsCache:
    """Build the full auto mods cache for all stats.

    Translated from DIM's buildAutoModsMap function.

    Args:
        auto_mod_options: Available mod definitions.
        available_general_stat_mods: Number of general mod slots available.
        stat_order: Stat hashes in priority order.

    Returns:
        Cache mapping stat_index -> CacheForStat.
    """
    result: AutoModsCache = {}
    for stat_index, stat_hash in enumerate(stat_order):
        result[stat_index] = build_cache_for_stat(
            auto_mod_options, stat_hash, stat_index, available_general_stat_mods
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# choose_auto_mods — find mod assignment satisfying stat targets
# ═══════════════════════════════════════════════════════════════════════════


def choose_auto_mods(
    auto_mod_cache: AutoModsCache,
    general_mod_costs: list[int],
    needed_stats: list[int],
    num_artifice_mods: int,
    remaining_energy_capacities: list[list[int]],
    remaining_total_energy: int,
) -> list[ModsPick] | None:
    """Pick auto mods (general + artifice) that provide at least neededStats.

    Translated from DIM's chooseAutoMods function.

    Args:
        auto_mod_cache: Precomputed mod combinations per stat.
        general_mod_costs: Energy costs of user-picked general mods, sorted desc.
        needed_stats: Additional stat points needed per stat.
        num_artifice_mods: Number of artifice mod slots available.
        remaining_energy_capacities: Remaining energy per item for each valid
            activity mod assignment.
        remaining_total_energy: Total remaining energy across all items.

    Returns:
        List of ModsPick if a valid assignment found, None otherwise.
    """
    return _recursively_choose_mods(
        auto_mod_cache,
        general_mod_costs,
        needed_stats,
        0,  # stat_index
        5 - len(general_mod_costs),  # remaining_general_slots
        num_artifice_mods,
        remaining_energy_capacities,
        remaining_total_energy,
        None,  # picked_mods
    )


def _do_general_mods_fit(
    general_mod_costs: list[int],
    remaining_energy_capacities: list[list[int]],
    picked_mods: list[ModsPick] | None,
) -> bool:
    """Check if general mods fit into any of the remaining energy possibilities.

    Translated from DIM's doGeneralModsFit function.
    """
    costs = list(general_mod_costs)
    if picked_mods:
        for pick in picked_mods:
            costs.extend(pick.general_mods_costs)
        costs.sort(reverse=True)

    for capacities in remaining_energy_capacities:
        sorted_caps = sorted(capacities, reverse=True)
        if all(cost <= cap for cost, cap in zip(costs, sorted_caps)):
            return True
    return False


def _recursively_choose_mods(
    auto_mod_cache: AutoModsCache,
    general_mod_costs: list[int],
    needed_stats: list[int],
    stat_index: int,
    remaining_general_slots: int,
    remaining_artifice_slots: int,
    remaining_energy_capacities: list[list[int]],
    remaining_total_energy: int,
    picked_mods: list[ModsPick] | None,
) -> list[ModsPick] | None:
    """Recursively find mod combinations that satisfy all stat targets.

    Translated from DIM's recursivelyChooseMods function.
    """
    # Skip stats that don't need increase
    while stat_index < len(needed_stats) and needed_stats[stat_index] == 0:
        stat_index += 1

    if stat_index == len(needed_stats):
        # All stats satisfied, check if general mods fit
        if _do_general_mods_fit(general_mod_costs, remaining_energy_capacities, picked_mods):
            return picked_mods if picked_mods else []
        return None

    # Get possible mod picks for this stat's needed value
    possible_picks = auto_mod_cache.get(stat_index, {}).get(needed_stats[stat_index])
    if not possible_picks:
        return None

    sub_array = list(picked_mods) if picked_mods else []
    sub_array.append(None)  # placeholder

    for pick in possible_picks:
        if (
            pick.num_artifice_mods > remaining_artifice_slots
            or pick.num_general_mods > remaining_general_slots
            or pick.mod_energy_cost > remaining_total_energy
        ):
            continue

        sub_array[-1] = pick
        solution = _recursively_choose_mods(
            auto_mod_cache,
            general_mod_costs,
            needed_stats,
            stat_index + 1,
            remaining_general_slots - pick.num_general_mods,
            remaining_artifice_slots - pick.num_artifice_mods,
            remaining_energy_capacities,
            remaining_total_energy - pick.mod_energy_cost,
            sub_array,
        )
        if solution is not None:
            return solution

    return None
