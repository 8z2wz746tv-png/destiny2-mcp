"""Build Engine solver — armor combination optimizer with mod assignment.

Translated from DIM's src/app/loadout-builder/process-worker/process.ts.
Uses 5-level nested loop to enumerate armor combinations, then assigns
stat mods (general + artifice) to maximize stats while respecting constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..logging_config import get_logger
from .constants import STAT_HASHES as _STAT_HASHES
from .models import (
    BuildConstraints,
    InventorySnapshot,
)
from .process_types import (
    ARTIFICE_STAT_BOOST,
    MAJOR_STAT_BOOST,
    MAX_STAT,
    MINOR_STAT_BOOST,
    AutoModData,
    ModDef,
    ProcessItem,
    ProcessResult,
    ProcessArmorSet,
    StatMods,
    armor_to_process_item,
)
from .process_utils import (
    LoSessionInfo,
    pick_and_assign_slot_independent_mods,
    pick_optimal_stat_mods,
    precalculate_structures,
    update_max_stats,
)
from .set_tracker import HeapEntry, HeapSetTracker, encode_stat_mix

logger = get_logger(__name__)

# Number of top sets to return
RETURNED_ARMOR_SETS = 200
_STAT_RANK_BASE = MAX_STAT + 1
_TOTAL_STAT_RANK_BASE = MAX_STAT * 6 + 1


def _ranking_metric(
    stats: list[int],
    priority_indices: list[int],
    optimistic_bonus: int = 0,
) -> int:
    """Encode strict priority order, then total stats, into one heap key."""
    if not priority_indices:
        return sum(stats) + optimistic_bonus

    metric = 0
    for index in priority_indices:
        value = min(MAX_STAT, stats[index] + optimistic_bonus)
        metric = metric * _STAT_RANK_BASE + value
    total = min(MAX_STAT * 6, sum(stats) + optimistic_bonus)
    return metric * _TOTAL_STAT_RANK_BASE + total


def _snapshot_auto_mod_data(snapshot: InventorySnapshot) -> AutoModData:
    """Build solver mod options exclusively from manifest-backed definitions."""
    general: dict[int, StatMods] = {}
    artifice: dict[int, int] = {}
    for stat_hash in _STAT_HASHES:
        definitions = [
            mod
            for mod in snapshot.stat_mod_definitions
            if mod.kind == "general" and mod.stat_hash == stat_hash
        ]
        major = min(
            (mod for mod in definitions if mod.value == MAJOR_STAT_BOOST),
            key=lambda mod: (mod.energy_cost, mod.hash),
            default=None,
        )
        minor = min(
            (mod for mod in definitions if mod.value == MINOR_STAT_BOOST),
            key=lambda mod: (mod.energy_cost, mod.hash),
            default=None,
        )
        if major or minor:
            general[stat_hash] = StatMods(
                major_mod=(
                    ModDef(hash=major.hash, cost=major.energy_cost)
                    if major
                    else None
                ),
                minor_mod=(
                    ModDef(hash=minor.hash, cost=minor.energy_cost)
                    if minor
                    else None
                ),
            )

        artifice_mod = min(
            (
                mod
                for mod in snapshot.stat_mod_definitions
                if mod.kind == "artifice"
                and mod.stat_hash == stat_hash
                and mod.value == ARTIFICE_STAT_BOOST
            ),
            key=lambda mod: (mod.energy_cost, mod.hash),
            default=None,
        )
        if artifice_mod:
            artifice[stat_hash] = artifice_mod.hash

    return AutoModData(general_mods=general, artifice_mods=artifice)


def _validated_auto_mod_data(options: AutoModData) -> AutoModData:
    """Drop non-executable definitions so the solver can never emit hash 0."""
    general: dict[int, StatMods] = {}
    for stat_hash, mods in options.general_mods.items():
        major = mods.major_mod
        minor = mods.minor_mod
        valid_major = major if major and major.hash > 0 and major.cost >= 0 else None
        valid_minor = minor if minor and minor.hash > 0 and minor.cost >= 0 else None
        if valid_major or valid_minor:
            general[stat_hash] = StatMods(
                major_mod=valid_major,
                minor_mod=valid_minor,
            )
    artifice = {
        stat_hash: mod_hash
        for stat_hash, mod_hash in options.artifice_mods.items()
        if mod_hash > 0
    }
    return AutoModData(general_mods=general, artifice_mods=artifice)


def _assign_stat_mods(
    mod_hashes: list[int],
    items: list[ProcessItem],
    options: AutoModData,
) -> dict[str, list[int]] | None:
    """Assign each executable mod to a concrete compatible armor instance."""
    if not mod_hashes:
        return {}

    general_costs: dict[int, int] = {}
    for mods in options.general_mods.values():
        for definition in (mods.major_mod, mods.minor_mod):
            if definition and definition.hash > 0:
                general_costs[definition.hash] = definition.cost
    artifice_hashes = {
        mod_hash for mod_hash in options.artifice_mods.values() if mod_hash > 0
    }
    if any(
        mod_hash <= 0
        or (mod_hash not in general_costs and mod_hash not in artifice_hashes)
        for mod_hash in mod_hashes
    ):
        return None

    assignments: dict[str, list[int]] = {}
    general_mods = sorted(
        (mod_hash for mod_hash in mod_hashes if mod_hash in general_costs),
        key=lambda mod_hash: (-general_costs[mod_hash], mod_hash),
    )
    general_items = sorted(
        items,
        key=lambda item: (-item.remaining_energy_capacity, item.id),
    )
    if len(general_mods) > len(general_items):
        return None
    for mod_hash, item in zip(general_mods, general_items):
        if general_costs[mod_hash] > item.remaining_energy_capacity:
            return None
        if item.id:
            assignments.setdefault(item.id, []).append(mod_hash)

    artifice_items = sorted(
        (item for item in items if item.is_artifice),
        key=lambda item: item.id,
    )
    artifice_mods = sorted(
        mod_hash for mod_hash in mod_hashes if mod_hash in artifice_hashes
    )
    if len(artifice_mods) > len(artifice_items):
        return None
    for mod_hash, item in zip(artifice_mods, artifice_items):
        if item.id:
            assignments.setdefault(item.id, []).append(mod_hash)

    return assignments


@dataclass(frozen=True)
class FixedSetEvaluationContext:
    """Precomputed values shared by normal and farm-target set validation."""

    constraints: BuildConstraints
    auto_mod_data: AutoModData
    info: LoSessionInfo
    desired_min: list[int]
    desired_max: list[int]
    bonus_vector: list[int]
    priority_indices: list[int]
    mod_stat_totals: list[int]


def prepare_fixed_set_context(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    auto_mod_data: AutoModData | None = None,
    mod_stat_totals: list[int] | None = None,
) -> FixedSetEvaluationContext:
    """Prepare reusable, manifest-backed rules for one fixed five-piece set."""
    if auto_mod_data is None:
        auto_mod_data = _snapshot_auto_mod_data(snapshot)
    auto_mod_data = _validated_auto_mod_data(auto_mod_data)
    totals = list(mod_stat_totals or [0] * 6)
    if len(totals) != 6:
        totals = [0] * 6

    raw_min = constraints.as_vector()
    bonus_vector = constraints.subclass_and_fragment_vector()
    desired_min = [max(0, raw_min[i] - bonus_vector[i]) for i in range(6)]
    priority_indices = constraints.ordered_priority_indices
    if priority_indices:
        desired_max = [
            MAX_STAT if index in priority_indices else desired_min[index]
            for index in range(6)
        ]
    else:
        desired_max = [MAX_STAT] * 6

    info = precalculate_structures(
        auto_mod_data,
        [],
        [],
        True,
        _STAT_HASHES,
    )
    return FixedSetEvaluationContext(
        constraints=constraints,
        auto_mod_data=auto_mod_data,
        info=info,
        desired_min=desired_min,
        desired_max=desired_max,
        bonus_vector=bonus_vector,
        priority_indices=priority_indices,
        mod_stat_totals=totals,
    )


def _allowed_exotic_hashes(constraints: BuildConstraints) -> set[int]:
    """Return signed and unsigned variants accepted for the requested exotic."""
    from ..utils.hash_utils import to_signed, to_unsigned

    hashes: set[int] = set()
    for item_hash in {*constraints.exotic_hashes, constraints.exotic_hash}:
        if item_hash is None:
            continue
        hashes.update({item_hash, to_signed(item_hash), to_unsigned(item_hash)})
    return hashes


def _fixed_items_meet_non_stat_constraints(
    items: Sequence[ProcessItem],
    constraints: BuildConstraints,
) -> bool:
    """Validate exotic and set constraints for a concrete five-item selection."""
    if sum(1 for item in items if item.is_exotic) > 1:
        return False

    if constraints.exotic_hash is not None:
        allowed_hashes = _allowed_exotic_hashes(constraints)
        if not any(item.hash in allowed_hashes for item in items):
            return False

    if constraints.set_bonus_count <= 0:
        return True

    set_count = sum(
        1 for item in items if item.set_bonus == constraints.set_bonus_hash
    )
    wildcard_count = sum(1 for item in items if item.has_set_bonus_mod_socket)
    return set_count + wildcard_count >= constraints.set_bonus_count


def validate_fixed_process_items(
    items: Sequence[ProcessItem],
    context: FixedSetEvaluationContext,
) -> ProcessArmorSet | None:
    """Evaluate exactly five items with the same rules used by ``solve``."""
    if len(items) != 5 or not _fixed_items_meet_non_stat_constraints(
        items, context.constraints
    ):
        return None

    stats = [
        context.mod_stat_totals[index]
        + sum(item.stats.get(stat_hash, 0) for item in items)
        for index, stat_hash in enumerate(_STAT_HASHES)
    ]
    needed_stats = [
        max(0, context.desired_min[index] - stats[index])
        for index in range(6)
    ]
    total_needed = sum(needed_stats)
    num_artifice = sum(1 for item in items if item.is_artifice)
    max_mod_bonus = (
        num_artifice * ARTIFICE_STAT_BOOST
        + context.info.num_available_general_mods * MAJOR_STAT_BOOST
    )
    if total_needed > max_mod_bonus:
        return None

    has_mods = bool(context.info.total_mod_energy_cost > 0)
    if has_mods or total_needed > 0:
        if (
            pick_and_assign_slot_independent_mods(
                context.info,
                list(items),
                needed_stats if total_needed > 0 else None,
                num_artifice,
            )
            is None
        ):
            return None

    optimal_result = pick_optimal_stat_mods(
        context.info,
        list(items),
        stats,
        context.desired_min,
        context.desired_max,
        priority_stat_indices=context.priority_indices,
    )
    if optimal_result is None:
        return None
    bonus_stats, mod_hashes = optimal_result
    assignments = _assign_stat_mods(
        mod_hashes,
        list(items),
        context.auto_mod_data,
    )
    if assignments is None:
        return None

    final_stats = [
        stats[index] + bonus_stats[index] + context.bonus_vector[index]
        for index in range(6)
    ]
    return ProcessArmorSet(
        process_items=list(items),
        stats=stats,
        bonus_stats=bonus_stats,
        stat_mods=mod_hashes,
        stat_mod_assignments=assignments,
        enabled_stats_total=_ranking_metric(final_stats, context.priority_indices),
        stats_total=sum(stats),
    )


def solve(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    auto_mod_data: AutoModData | None = None,
    mod_stat_totals: list[int] | None = None,
) -> ProcessResult:
    """Find optimal armor combinations with mod assignment.

    Translated from DIM's process() function.

    Algorithm:
      1. Convert armor to ProcessItem list per slot.
      2. Filter by exotic constraint.
      3. 5-level nested loop: for each combination:
         a. Check exotic constraint (max 1 exotic).
         b. Sum base stats.
         c. Check if mods can satisfy stat targets.
         d. Pick optimal mods.
         e. Insert into HeapSetTracker (top-N).
      4. Return top-N results.

    Args:
        snapshot: All armor pieces grouped by slot.
        constraints: Normalized build constraints.
        auto_mod_data: Executable mod definitions. If None, uses the current
            manifest-backed definitions stored on the inventory snapshot.
        mod_stat_totals: Stat bonuses from user-picked mods, in STAT_HASHES order.
                        Added to base stats before solving. None = all zeros.

    Returns:
        ProcessResult with top-N armor sets.
    """
    context = prepare_fixed_set_context(
        snapshot,
        constraints,
        auto_mod_data=auto_mod_data,
        mod_stat_totals=mod_stat_totals,
    )
    auto_mod_data = context.auto_mod_data
    mod_stat_totals = context.mod_stat_totals

    # ── Prepare items per slot ─────────────────────────────────────────
    # Keep both Armor objects (for output) and ProcessItem (for computation)
    helmets_armor = list(snapshot.helmets)
    gauntlets_armor = list(snapshot.gauntlets)
    chests_armor = list(snapshot.chests)
    legs_armor = list(snapshot.legs)
    class_items_armor = list(snapshot.class_items)

    helmets = [armor_to_process_item(a) for a in helmets_armor]
    gauntlets = [armor_to_process_item(a) for a in gauntlets_armor]
    chests = [armor_to_process_item(a) for a in chests_armor]
    legs = [armor_to_process_item(a) for a in legs_armor]
    class_items = [armor_to_process_item(a) for a in class_items_armor]

    # Filter by exotic constraint
    if constraints.exotic_hash is not None:
        from ..utils.hash_utils import to_signed, to_unsigned

        # Build set of all valid exotic hashes (including unsigned variants
        # and all versions of the same item name from manifest search)
        exotic_hashes: set[int] = set()
        for h in constraints.exotic_hashes:
            exotic_hashes.add(h)
            exotic_hashes.add(to_signed(h))
            exotic_hashes.add(to_unsigned(h))
        if constraints.exotic_hash not in exotic_hashes:
            exotic_hashes.add(constraints.exotic_hash)
            exotic_hashes.add(to_signed(constraints.exotic_hash))
            exotic_hashes.add(to_unsigned(constraints.exotic_hash))

        # Find which slot the exotic belongs to
        exotic_slot = None
        for slot_name, slot_armor in [
            ("helmets", helmets_armor), ("gauntlets", gauntlets_armor),
            ("chests", chests_armor), ("legs", legs_armor),
            ("class_items", class_items_armor),
        ]:
            if any(a.item_hash in exotic_hashes for a in slot_armor):
                exotic_slot = slot_name
                break

        if exotic_slot is None:
            logger.info(
                "Requested exotic is not present in the armor snapshot: %s",
                constraints.exotic_hash,
            )
            return ProcessResult(sets=[], combos=0)

        def _filter_exotics(
            armor_list: list, proc_list: list[ProcessItem], is_exotic_slot: bool
        ) -> tuple[list, list[ProcessItem]]:
            filtered_armor = []
            filtered_proc = []
            for armor, proc in zip(armor_list, proc_list):
                if is_exotic_slot:
                    # In the exotic's slot: keep ONLY the exotic
                    if proc.hash in exotic_hashes:
                        filtered_armor.append(armor)
                        filtered_proc.append(proc)
                else:
                    # In other slots: keep non-exotic only
                    if not proc.is_exotic:
                        filtered_armor.append(armor)
                        filtered_proc.append(proc)
            return filtered_armor, filtered_proc

        helmets_armor, helmets = _filter_exotics(helmets_armor, helmets, exotic_slot == "helmets")
        gauntlets_armor, gauntlets = _filter_exotics(gauntlets_armor, gauntlets, exotic_slot == "gauntlets")
        chests_armor, chests = _filter_exotics(chests_armor, chests, exotic_slot == "chests")
        legs_armor, legs = _filter_exotics(legs_armor, legs, exotic_slot == "legs")
        class_items_armor, class_items = _filter_exotics(class_items_armor, class_items, exotic_slot == "class_items")

    combos = len(helmets) * len(gauntlets) * len(chests) * len(legs) * len(class_items)
    logger.debug(
        "Solver: %d combos from %d items (helms=%d, gauntlets=%d, chests=%d, legs=%d, class=%d)",
        combos,
        len(helmets) + len(gauntlets) + len(chests) + len(legs) + len(class_items),
        len(helmets), len(gauntlets), len(chests), len(legs), len(class_items),
    )

    if combos == 0:
        return ProcessResult(sets=[], combos=0)

    # ── Precompute session data ────────────────────────────────────────
    stat_order = _STAT_HASHES
    bonus_vector = context.bonus_vector
    desired_min = context.desired_min
    desired_max = context.desired_max
    priority_indices = context.priority_indices
    info = context.info

    # ── Build tracker ──────────────────────────────────────────────────
    tracker = HeapSetTracker(RETURNED_ARMOR_SETS)

    # Track stat ranges for reporting
    stat_ranges: list[list[int]] = [[MAX_STAT, 0] for _ in range(6)]

    combo_count = 0
    # ── 5-level nested loop ────────────────────────────────────────────
    for h_idx, helm in enumerate(helmets):
        helm_exotic = 1 if helm.is_exotic else 0
        helm_artifice = 1 if helm.is_artifice else 0
        helm_stats = [helm.stats.get(h, 0) for h in stat_order]

        for g_idx, gaunt in enumerate(gauntlets):
            gaunt_exotic = 1 if gaunt.is_exotic else 0
            gaunt_artifice = 1 if gaunt.is_artifice else 0
            gaunt_stats = [gaunt.stats.get(h, 0) for h in stat_order]

            for c_idx, chest in enumerate(chests):
                chest_exotic = 1 if chest.is_exotic else 0
                chest_artifice = 1 if chest.is_artifice else 0
                chest_stats = [chest.stats.get(h, 0) for h in stat_order]

                for l_idx, leg in enumerate(legs):
                    leg_exotic = 1 if leg.is_exotic else 0
                    leg_artifice = 1 if leg.is_artifice else 0
                    leg_stats = [leg.stats.get(h, 0) for h in stat_order]

                    for ci_idx, class_item in enumerate(class_items):
                        combo_count += 1

                        ci_exotic = 1 if class_item.is_exotic else 0
                        ci_artifice = 1 if class_item.is_artifice else 0
                        ci_stats = [class_item.stats.get(h, 0) for h in stat_order]

                        # ── Exotic constraint ──────────────────────────
                        exotic_sum = helm_exotic + gaunt_exotic + chest_exotic + leg_exotic + ci_exotic
                        if exotic_sum > 1:
                            continue

                        # ── Set bonus constraint (with wildcard support) ──
                        if constraints.set_bonus_count > 0:
                            set_hash = constraints.set_bonus_hash
                            set_count = (
                                (1 if helm.set_bonus == set_hash else 0) +
                                (1 if gaunt.set_bonus == set_hash else 0) +
                                (1 if chest.set_bonus == set_hash else 0) +
                                (1 if leg.set_bonus == set_hash else 0) +
                                (1 if class_item.set_bonus == set_hash else 0)
                            )
                            if set_count < constraints.set_bonus_count:
                                # Wildcards: items with has_set_bonus_mod_socket
                                # can count as any set
                                wildcards_remaining = (
                                    (1 if helm.has_set_bonus_mod_socket else 0) +
                                    (1 if gaunt.has_set_bonus_mod_socket else 0) +
                                    (1 if chest.has_set_bonus_mod_socket else 0) +
                                    (1 if leg.has_set_bonus_mod_socket else 0) +
                                    (1 if class_item.has_set_bonus_mod_socket else 0)
                                )
                                wildcards_needed = constraints.set_bonus_count - set_count
                                if wildcards_remaining < wildcards_needed:
                                    continue  # Not enough set pieces even with wildcards

                        # ── Sum base stats (including mod stat totals) ──
                        stats = [
                            mod_stat_totals[i] + helm_stats[i] + gaunt_stats[i] + chest_stats[i] + leg_stats[i] + ci_stats[i]
                            for i in range(6)
                        ]

                        # ── Calculate needed stats ─────────────────────
                        needed_stats = [0] * 6
                        total_needed = 0
                        for i in range(6):
                            if desired_min[i] > 0:
                                needed = desired_min[i] - stats[i]
                                if needed > 0:
                                    needed_stats[i] = needed
                                    total_needed += needed

                        num_artifice = helm_artifice + gaunt_artifice + chest_artifice + leg_artifice + ci_artifice

                        # ── Early prune: can mods possibly satisfy? ────
                        max_mod_bonus = num_artifice * ARTIFICE_STAT_BOOST + info.num_available_general_mods * MAJOR_STAT_BOOST
                        if total_needed > max_mod_bonus:
                            continue

                        armor = [helm, gaunt, chest, leg, class_item]

                        # This deliberately overestimates every priority so the
                        # top-N prune cannot discard a lexicographically better set.
                        opt_total = _ranking_metric(
                            stats,
                            priority_indices,
                            optimistic_bonus=max_mod_bonus,
                        )
                        if not tracker.could_insert(opt_total):
                            continue

                        fixed_result = validate_fixed_process_items(armor, context)
                        if fixed_result is None:
                            continue

                        # ── Update stat ranges ─────────────────────────
                        update_max_stats(
                            info,
                            armor,
                            stats,
                            num_artifice,
                            desired_min,
                            desired_max,
                            stat_ranges,
                        )

                        bonus_stats = fixed_result.bonus_stats
                        mod_hashes = fixed_result.stat_mods
                        stat_mod_assignments = fixed_result.stat_mod_assignments
                        final_stats = [
                            stats[i] + bonus_stats[i] + bonus_vector[i] for i in range(6)
                        ]

                        # ── Final prune and insertion ────────────────────
                        priority_weighted_total = _ranking_metric(
                            final_stats,
                            priority_indices,
                        )
                        if not tracker.could_insert(priority_weighted_total):
                            continue

                        # ── Encode stat mix and insert ─────────────────
                        stat_mix = encode_stat_mix(final_stats, desired_max)

                        # Store both Armor objects (for output) and ProcessItems (for reference)
                        original_armor = [
                            helmets_armor[h_idx],
                            gauntlets_armor[g_idx],
                            chests_armor[c_idx],
                            legs_armor[l_idx],
                            class_items_armor[ci_idx],
                        ]

                        tracker.insert(HeapEntry(
                            enabled_stats_total=priority_weighted_total,
                            stat_mix=stat_mix,
                            stats_total=sum(stats),
                            power=sum(a.power for a in armor if a.power > 0) // 5,
                            armor=original_armor,
                            stats=stats,
                            bonus_stats=bonus_stats,
                            stat_mods=mod_hashes,
                            stat_mod_assignments=stat_mod_assignments,
                        ))

    logger.debug(
        "Solver: processed %d combos, found %d valid sets",
        combo_count, tracker.total_sets,
    )

    # Convert HeapEntry back to ProcessArmorSet for output
    result_sets = [
        ProcessArmorSet(
            armor=entry.armor,
            stats=entry.stats,
            bonus_stats=entry.bonus_stats,
            stat_mods=entry.stat_mods,
            stat_mod_assignments=entry.stat_mod_assignments,
            enabled_stats_total=entry.enabled_stats_total,
            stats_total=entry.stats_total,
            stat_mix=entry.stat_mix,
            power=entry.power,
        )
        for entry in tracker.get_armor_sets()
    ]

    return ProcessResult(
        sets=result_sets,
        combos=combo_count,
    )
