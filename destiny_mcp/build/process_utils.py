"""Process utilities for the Build Engine.

Translated from DIM's src/app/loadout-builder/process-worker/process-utils.ts.
Contains precalculation, mod fitting, and optimal mod assignment logic.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..logging_config import get_logger
from .auto_mod_utils import (
    AutoModsCache,
    build_auto_mods_map,
    choose_auto_mods,
)
from .process_types import (
    ARTIFICE_STAT_BOOST,
    MAJOR_STAT_BOOST,
    MAX_STAT,
    MINOR_STAT_BOOST,
    AutoModData,
    ModsPick,
    ProcessItem,
    ProcessMod,
)

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# LoSessionInfo — precomputed session data
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class LoSessionInfo:
    """Data that stays the same in a given LO run.

    Translated from DIM's LoSessionInfo interface.
    """

    auto_mod_options: AutoModsCache = field(default_factory=dict)
    has_activity_mods: bool = False
    total_mod_energy_cost: int = 0
    general_mod_costs: list[int] = field(default_factory=list)
    num_available_general_mods: int = 0
    activity_mod_permutations: list[list[ProcessMod | None]] = field(default_factory=list)
    activity_tag_counts: dict[str, int] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# precalculate_structures
# ═══════════════════════════════════════════════════════════════════════════


def _generate_mod_permutations(
    mods: list[ProcessMod],
) -> list[list[ProcessMod | None]]:
    """Generate all permutations of activity mod assignments to 5 slots.

    Translated from DIM's generateProcessModPermutations.
    Each permutation is a list of 5 elements (one per armor slot),
    where each element is either a ProcessMod or None.
    """
    if not mods:
        return [[None, None, None, None, None]]

    # Group mods by tag
    mods_by_tag: dict[str, list[ProcessMod]] = {}
    for mod in mods:
        tag = mod.tag or ""
        if tag not in mods_by_tag:
            mods_by_tag[tag] = []
        mods_by_tag[tag].append(mod)

    # Generate all possible slot assignments
    # For simplicity, we use itertools.permutations
    result: list[list[ProcessMod | None]] = []

    # Try all permutations of assigning mods to slots
    slots = list(range(5))
    for perm in itertools.permutations(slots, len(mods)):
        assignment: list[ProcessMod | None] = [None] * 5
        for i, slot_idx in enumerate(perm):
            assignment[slot_idx] = mods[i]
        result.append(assignment)

    # Always include the "no mods" assignment
    if not result:
        result.append([None, None, None, None, None])

    return result


def precalculate_structures(
    auto_mod_options: AutoModData,
    general_mods: list[ProcessMod],
    activity_mods: list[ProcessMod],
    auto_stat_mods: bool,
    stat_order: list[int],
) -> LoSessionInfo:
    """Precompute session data for the optimizer.

    Translated from DIM's precalculateStructures function.

    Args:
        auto_mod_options: Available mod definitions.
        general_mods: User-picked general mods.
        activity_mods: User-picked activity mods.
        auto_stat_mods: Whether to auto-assign stat mods.
        stat_order: Stat hashes in priority order.

    Returns:
        LoSessionInfo with precomputed data.
    """
    general_mod_costs = sorted(
        [m.energy_cost for m in general_mods], reverse=True
    )
    num_available = 5 - len(general_mod_costs) if auto_stat_mods else 0

    total_energy_cost = sum(m.energy_cost for m in general_mods) + sum(
        m.energy_cost for m in activity_mods
    )

    activity_tag_counts: dict[str, int] = {}
    for mod in activity_mods:
        tag = mod.tag or ""
        activity_tag_counts[tag] = activity_tag_counts.get(tag, 0) + 1

    return LoSessionInfo(
        auto_mod_options=build_auto_mods_map(auto_mod_options, num_available, stat_order),
        has_activity_mods=len(activity_mods) > 0,
        total_mod_energy_cost=total_energy_cost,
        general_mod_costs=general_mod_costs,
        num_available_general_mods=num_available,
        activity_mod_permutations=_generate_mod_permutations(activity_mods),
        activity_tag_counts=activity_tag_counts,
    )


# ═══════════════════════════════════════════════════════════════════════════
# get_remaining_energies_per_assignment
# ═══════════════════════════════════════════════════════════════════════════


def _get_remaining_energies_per_assignment(
    activity_mod_permutations: list[list[ProcessMod | None]],
    items: list[ProcessItem],
) -> tuple[int, list[list[int]]]:
    """For each valid activity mod permutation, compute remaining energy per item.

    Translated from DIM's getRemainingEnergiesPerAssignment.

    Returns:
        Tuple of (set_energy, remaining_energies_per_assignment).
    """
    set_energy = sum(item.remaining_energy_capacity for item in items)

    remaining_per_assignment: list[list[int]] = []

    for perm in activity_mod_permutations:
        remaining = [0] * 5
        valid = True

        for i in range(5):
            activity_mod = perm[i]
            item = items[i]
            remaining[i] = item.remaining_energy_capacity

            if activity_mod is not None:
                tag = activity_mod.tag or ""
                energy_cost = activity_mod.energy_cost

                if (
                    energy_cost > item.remaining_energy_capacity
                    or item.compatible_activity_mod != tag
                ):
                    valid = False
                    break
                remaining[i] -= activity_mod.energy_cost

        if valid:
            remaining_per_assignment.append(remaining)

    return set_energy, remaining_per_assignment


# ═══════════════════════════════════════════════════════════════════════════
# pick_and_assign_slot_independent_mods
# ═══════════════════════════════════════════════════════════════════════════


def pick_and_assign_slot_independent_mods(
    info: LoSessionInfo,
    items: list[ProcessItem],
    needed_stats: list[int] | None,
    num_artifice: int,
) -> list[ModsPick] | None:
    """Check if all user-chosen mods can be assigned and auto stat mods can hit targets.

    Translated from DIM's pickAndAssignSlotIndependentMods.

    Args:
        info: Precomputed session data.
        items: The 5 armor pieces.
        needed_stats: Additional stat points needed per stat (None if not needed).
        num_artifice: Number of artifice mod slots available.

    Returns:
        List of ModsPick if valid assignment found, None otherwise.
    """
    # Early energy check
    set_energy = sum(item.remaining_energy_capacity for item in items)
    if set_energy < info.total_mod_energy_cost:
        return None

    # Early activity mod tag check
    if info.has_activity_mods:
        for tag, tag_count in info.activity_tag_counts.items():
            sockets_count = sum(
                1 for item in items if item.compatible_activity_mod == tag
            )
            if sockets_count < tag_count:
                return None

    assigned_at_least_once = False

    for perm in info.activity_mod_permutations:
        # Check if activity mods fit
        remaining = [0] * 5
        valid = True

        for i in range(5):
            activity_mod = perm[i]
            item = items[i]
            remaining[i] = item.remaining_energy_capacity

            if activity_mod is not None:
                tag = activity_mod.tag or ""
                energy_cost = activity_mod.energy_cost

                if (
                    energy_cost > item.remaining_energy_capacity
                    or item.compatible_activity_mod != tag
                ):
                    valid = False
                    break
                remaining[i] -= activity_mod.energy_cost

        if not valid:
            continue

        assigned_at_least_once = True

        if needed_stats:
            result = choose_auto_mods(
                info.auto_mod_options,
                info.general_mod_costs,
                needed_stats,
                num_artifice,
                [remaining],
                set_energy - info.total_mod_energy_cost,
            )
            if result is not None:
                return result
        else:
            # Check if general mods fit without stat mods
            if all(
                cost <= cap
                for cost, cap in zip(
                    sorted(info.general_mod_costs, reverse=True),
                    sorted(remaining, reverse=True),
                )
            ):
                return []

    return None


# ═══════════════════════════════════════════════════════════════════════════
# pick_optimal_stat_mods
# ═══════════════════════════════════════════════════════════════════════════


def greedy_pick_stat_mods(
    info: LoSessionInfo,
    exploration_stats: list[int],
    max_added_stats: list[int],
    num_artifice_mods: int,
    remaining_energy_capacities: list[list[int]],
    remaining_total_energy: int,
    priority_stat_indices: list[int] | None = None,
) -> list[ModsPick]:
    """Pick stat mods to maximize total stats using binary search.

    Translated from DIM's greedyPickStatMods.
    Post-Edge of Fate: no more stat tiers, every point is linear.
    Uses binary search per stat to find the maximum achievable boost
    while keeping all other stats at their minimums.

    Args:
        info: Precomputed session data.
        exploration_stats: Minimum stat points needed per stat.
        max_added_stats: Maximum additional stat points allowed per stat.
        num_artifice_mods: Number of artifice mod slots available.
        remaining_energy_capacities: Remaining energy per item for each valid permutation.
        remaining_total_energy: Total remaining energy across all items.

    Returns:
        List of ModsPick for the optimal assignment.
    """
    # Fast path: nothing to do
    if all(e == 0 for e in remaining_energy_capacities[0]) and num_artifice_mods == 0:
        return []

    # Phase 1: Ensure base exploration_stats can be met
    picks: list[ModsPick] | None = choose_auto_mods(
        info.auto_mod_options,
        info.general_mod_costs,
        exploration_stats,
        num_artifice_mods,
        remaining_energy_capacities,
        remaining_total_energy,
    )

    if picks is None:
        # Can't even hit the base minimums
        return []

    # Minimum targets are already held in exploration_stats. Consume remaining
    # capacity in the requested strict priority order.
    priorities = [
        index
        for index in (priority_stat_indices or [])
        if 0 <= index < 6
    ]
    stat_order = [index for index in range(6) if index not in priorities]
    stat_order.extend(priorities)

    for i in stat_order:
        if max_added_stats[i] <= 0:
            continue  # No need to boost this stat

        original_exploration_stat = exploration_stats[i]

        # Binary search bounds
        min_boost = max(ARTIFICE_STAT_BOOST, original_exploration_stat)
        max_boost = max_added_stats[i] - 1

        last_good_picks: list[ModsPick] | None = None
        last_good_exploration_stat = 0
        candidate_picks: list[ModsPick] | None = None

        while min_boost < max_boost:
            exploration_stats[i] = (min_boost + max_boost) // 2
            if exploration_stats[i] <= 0:
                break

            candidate_picks = choose_auto_mods(
                info.auto_mod_options,
                info.general_mod_costs,
                exploration_stats,
                num_artifice_mods,
                remaining_energy_capacities,
                remaining_total_energy,
            )
            if candidate_picks is not None:
                # Can hit this boost — try higher
                min_boost = exploration_stats[i] + 1
                last_good_picks = candidate_picks
                last_good_exploration_stat = exploration_stats[i]
            else:
                # Can't hit this — try lower
                max_boost = exploration_stats[i]

        if candidate_picks is not None:
            picks = candidate_picks
        elif last_good_picks is not None:
            picks = last_good_picks
            exploration_stats[i] = last_good_exploration_stat
        else:
            # Reset to original
            exploration_stats[i] = original_exploration_stat

    return picks


def pick_optimal_stat_mods(
    info: LoSessionInfo,
    items: list[ProcessItem],
    set_stats: list[int],
    desired_min_stats: list[int],
    desired_max_stats: list[int],
    priority_stat_indices: list[int] | None = None,
) -> tuple[list[int], list[int]] | None:
    """Find the optimal stat mod assignment to maximize total stats.

    Translated from DIM's pickOptimalStatMods.

    Args:
        info: Precomputed session data.
        items: The 5 armor pieces.
        set_stats: Base stat values (before mods).
        desired_min_stats: Minimum stat targets.
        desired_max_stats: Maximum stat targets (0 = ignored).
        priority_stat_indices: Stats to maximize in strict order (optional).

    Returns:
        Tuple of (bonus_stats, mod_hashes) if valid, None otherwise.
    """
    set_energy, remaining_energies = _get_remaining_energies_per_assignment(
        info.activity_mod_permutations, items
    )
    if not remaining_energies:
        return None

    max_added_stats = [0] * 6
    exploration_stats = [0] * 6

    for stat_index in range(5, -1, -1):
        if desired_max_stats[stat_index] > 0:
            value = set_stats[stat_index]
            if desired_min_stats[stat_index] > 0:
                needed = desired_min_stats[stat_index] - value
                if needed > 0:
                    exploration_stats[stat_index] = needed
            max_added_stats[stat_index] = desired_max_stats[stat_index] - value

    num_artifice = sum(1 for item in items if item.is_artifice)

    picks = greedy_pick_stat_mods(
        info,
        exploration_stats,
        max_added_stats,
        num_artifice,
        remaining_energies,
        set_energy - info.total_mod_energy_cost,
        priority_stat_indices=priority_stat_indices,
    )

    # Convert PicksPick list to (bonus_stats, mod_hashes) format
    bonus_stats = [0] * 6
    mod_hashes: list[int] = []
    for pick in picks:
        bonus_stats[pick.target_stat_index] += pick.exact_stat_points
        mod_hashes.extend(pick.mod_hashes)

    return bonus_stats, mod_hashes


# ═══════════════════════════════════════════════════════════════════════════
# update_max_stats
# ═══════════════════════════════════════════════════════════════════════════


def update_max_stats(
    info: LoSessionInfo,
    items: list[ProcessItem],
    set_stats: list[int],
    num_artifice_mods: int,
    desired_min_stats: list[int],
    desired_max_stats: list[int],
    stat_ranges: list[list[int]],
) -> bool:
    """Update max stat ranges by per-point iteration with choose_auto_mods validation.

    Translated from DIM's updateMaxStats.
    For each stat, iterates one point at a time, calling choose_auto_mods at
    each step to verify the stat can be reached while all other stats meet
    their minimums.

    Args:
        info: Precomputed session data.
        items: The 5 armor pieces.
        set_stats: Base stat values.
        num_artifice_mods: Number of artifice mod slots.
        desired_min_stats: Minimum stat targets.
        desired_max_stats: Maximum stat targets.
        stat_ranges: Current min/max ranges per stat (mutated).

    Returns:
        True if any stat can be improved beyond its current max.
    """
    found_any_improvement = False

    if info.num_available_general_mods == 0 and num_artifice_mods == 0:
        # No mods available — just track raw stat values
        for stat_index in range(6):
            value = set_stats[stat_index]
            filter_min = desired_min_stats[stat_index]
            filter_max = desired_max_stats[stat_index]
            if value > stat_ranges[stat_index][1]:
                stat_ranges[stat_index][1] = value
                if filter_min < filter_max and value > filter_min:
                    found_any_improvement = True
        return found_any_improvement

    set_energy, remaining_energies = _get_remaining_energies_per_assignment(
        info.activity_mod_permutations, items
    )
    if not remaining_energies:
        return found_any_improvement

    # Track required minimum extra stats for all stats (shared across iterations)
    required_minimum_extra_stats = [0] * 6

    # First pass: track raw stat values and compute required minimums
    for stat_index in range(6):
        value = set_stats[stat_index]
        filter_min = desired_min_stats[stat_index]
        filter_max = desired_max_stats[stat_index]
        stat_range = stat_ranges[stat_index]

        # Bump range to at least the filter minimum
        if stat_range[1] < filter_min:
            stat_range[1] = filter_min

        if value > stat_range[1]:
            stat_range[1] = value
            found_any_improvement = filter_min < filter_max

        needed = filter_min - value
        required_minimum_extra_stats[stat_index] = max(0, needed)

    # For each stat where we haven't hit MAX_STAT, try to push higher
    for stat_index in range(6):
        value = set_stats[stat_index]
        filter_min = desired_min_stats[stat_index]
        filter_max = desired_max_stats[stat_index]
        stat_range = stat_ranges[stat_index]

        if stat_range[1] >= MAX_STAT:
            continue

        # Save and modify: temporarily set this stat's requirement to its current max
        previous_required = required_minimum_extra_stats[stat_index]
        required_minimum_extra_stats[stat_index] = stat_range[1] - value

        # Iterate one point at a time
        while stat_range[1] < MAX_STAT:
            required_minimum_extra_stats[stat_index] += 1

            if not choose_auto_mods(
                info.auto_mod_options,
                info.general_mod_costs,
                required_minimum_extra_stats,
                num_artifice_mods,
                remaining_energies,
                set_energy - info.total_mod_energy_cost,
            ):
                break  # Can't go higher

            new_value = value + required_minimum_extra_stats[stat_index]
            found_any_improvement = found_any_improvement or (
                filter_min < filter_max and new_value > filter_min
            )
            stat_range[1] = new_value

        # Restore
        required_minimum_extra_stats[stat_index] = previous_required

    return found_any_improvement
