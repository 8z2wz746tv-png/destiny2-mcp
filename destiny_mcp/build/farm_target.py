"""Legal Armor 3.0 farm-target diagnostics.

This module answers which one not-yet-owned Tier-5 legendary armor roll could
complete a build. It never creates a virtual ``Armor`` or execution contract:
the prospective item exists only as an ID-less ``ProcessItem`` while the
shared solver validator checks the five-piece set.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import heapq
from itertools import combinations
from typing import Literal, cast

from ..utils.hash_utils import to_signed, to_unsigned
from .armor_rules import (
    ArmorRollTemplate,
    ArmorTuningOption,
    StatsVector,
    all_tier5_templates,
    apply_tuning,
    masterworked_stats,
    tuning_options,
)
from .constants import STAT_HASHES, STAT_NAMES
from .models import (
    Armor,
    ArmorFarmOption,
    ArmorFarmPiece,
    ArmorFarmPlan,
    ArmorStats,
    BuildAnalysis,
    BuildConstraints,
    InventorySnapshot,
)
from .process_types import (
    ARTIFICE_STAT_BOOST,
    MAJOR_STAT_BOOST,
    MINOR_STAT_BOOST,
    ProcessItem,
    armor_to_process_item,
)
from .solver import (
    FixedSetEvaluationContext,
    prepare_fixed_set_context,
    validate_fixed_process_items,
)


_SLOTS = ("helmets", "gauntlets", "chests", "legs", "class_items")
_SLOT_ALIASES = {
    "helmet": "helmets",
    "helmets": "helmets",
    "head": "helmets",
    "gauntlet": "gauntlets",
    "gauntlets": "gauntlets",
    "gloves": "gauntlets",
    "chest": "chests",
    "chests": "chests",
    "leg": "legs",
    "legs": "legs",
    "class_item": "class_items",
    "class_items": "class_items",
    "class": "class_items",
}
_MAX_INVENTORY_SEARCH_NODES = 200_000
_MAX_TWO_PIECE_SEARCH_NODES = 2_000_000


@dataclass(frozen=True)
class _BaselineState:
    items: tuple[Armor, ...]
    stats: tuple[int, int, int, int, int, int]
    artifice_count: int
    exotic_count: int
    has_requested_exotic: bool
    set_count: int
    wildcard_count: int


@dataclass(frozen=True)
class _VirtualRoll:
    template: ArmorRollTemplate
    masterworked: StatsVector
    projected: StatsVector
    tuning: ArmorTuningOption | None


@dataclass(frozen=True)
class _InventorySearchNode:
    state: _BaselineState
    depth: int


@dataclass(frozen=True)
class _InventorySearchBounds:
    max_stats: tuple[StatsVector, ...]
    max_artifice_counts: tuple[int, ...]
    can_supply_requested_exotic: tuple[bool, ...]
    max_set_or_wildcard_counts: tuple[int, ...]
    minimum_locked_items: tuple[tuple[tuple[str, str], ...], ...]


class _FarmOptionCollector:
    """Keep the exact best N options without retaining every valid result."""

    def __init__(self, limit: int, context: FixedSetEvaluationContext) -> None:
        self._limit = limit
        self._context = context
        self._options: list[ArmorFarmOption] = []

    def add(self, option: ArmorFarmOption) -> None:
        self._options.append(option)
        self._options.sort(key=lambda item: _option_sort_key(item, self._context))
        del self._options[self._limit :]

    def can_improve(self, lower_bound: tuple) -> bool:
        if len(self._options) < self._limit:
            return True
        # An equal key is an interchangeable tie. There is no later sort field
        # beyond locked slot/name pairs, so it cannot produce a better result.
        return lower_bound < _option_sort_key(self._options[-1], self._context)

    @property
    def options(self) -> list[ArmorFarmOption]:
        return list(self._options)


def find_farm_targets(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    *,
    baseline: Literal["equipped", "inventory"] = "equipped",
    replacement_slot: str | None = None,
    max_replacements: int = 1,
    top_n: int = 5,
) -> BuildAnalysis:
    """Find legal Tier-5 rolls that can complete one or two replacement slots.

    ``equipped`` fixes four currently equipped pieces. ``inventory`` searches
    real inventory combinations with an exact best-first search, then leaves
    exactly one slot as the prospective legendary template. When explicitly
    enabled, an equipped baseline can fall back to two prospective pieces
    after the complete single-piece search proves that one is insufficient.
    """
    if isinstance(max_replacements, bool) or max_replacements not in {1, 2}:
        return BuildAnalysis(
            reason="invalid_max_replacements: max_replacements 只能是 1 或 2。",
            assumptions=_assumptions(baseline, max_replacements),
        )
    normalized_slot = _normalize_slot(replacement_slot)
    if replacement_slot and normalized_slot is None:
        return BuildAnalysis(
            reason="invalid_replacement_slot: 请使用 helmet/gauntlets/chest/legs/class_item。",
            assumptions=_assumptions(baseline, max_replacements),
        )
    if baseline not in {"equipped", "inventory"}:
        return BuildAnalysis(
            reason="invalid_baseline: baseline 只能是 equipped 或 inventory。",
            assumptions=_assumptions(baseline, max_replacements),
        )

    context = prepare_fixed_set_context(snapshot, constraints)
    slots = (normalized_slot,) if normalized_slot else _SLOTS
    all_options: list[ArmorFarmOption] = []
    errors: list[str] = []
    evaluated_slots = 0
    for slot in slots:
        if baseline == "inventory":
            inventory_options, error = _inventory_options_for_slot(
                snapshot,
                constraints,
                slot,
                context,
                max(1, top_n),
            )
            if error:
                errors.append(error)
                continue
            evaluated_slots += 1
            all_options.extend(inventory_options)
            continue

        states, error = _equipped_baseline_states(snapshot, constraints, slot)
        if error:
            errors.append(error)
            continue
        evaluated_slots += 1
        all_options.extend(_options_for_slot(slot, states, context, baseline))

    all_options.sort(key=lambda option: _option_sort_key(option, context))
    options = all_options[: max(1, top_n)]
    if options:
        return BuildAnalysis(
            reason="farm_target_ready",
            farm_options=options,
            assumptions=_assumptions(baseline, max_replacements),
        )
    single_reason = (
        "single_replacement_insufficient"
        if evaluated_slots
        else (errors[0] if errors else "single_replacement_insufficient")
    )
    if max_replacements == 1 or single_reason != "single_replacement_insufficient":
        return BuildAnalysis(
            reason=single_reason,
            assumptions=_assumptions(baseline, max_replacements),
        )
    if baseline == "inventory":
        return BuildAnalysis(
            reason="inventory_multi_replacement_unavailable",
            assumptions=_assumptions(baseline, max_replacements),
        )

    plans, plan_error = _two_piece_plans(
        snapshot,
        constraints,
        context,
        required_slot=normalized_slot,
        top_n=max(1, top_n),
    )
    if plans:
        return BuildAnalysis(
            reason="farm_target_ready",
            farm_plans=plans,
            assumptions=_assumptions(baseline, max_replacements),
        )
    return BuildAnalysis(
        reason=plan_error or "two_replacements_insufficient",
        assumptions=_assumptions(baseline, max_replacements),
    )


def _two_piece_plans(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    context: FixedSetEvaluationContext,
    *,
    required_slot: str | None,
    top_n: int,
) -> tuple[list[ArmorFarmPlan], str | None]:
    """Search legal two-piece plans against the currently equipped baseline."""
    pairs = [
        pair
        for pair in combinations(_SLOTS, 2)
        if required_slot is None or required_slot in pair
    ]
    if not pairs:
        return [], "two_replacements_insufficient"

    ordered_pairs: list[tuple[tuple[str, str], _BaselineState]] = []
    errors: list[str] = []
    for pair in pairs:
        state, error = _equipped_baseline_state_for_slots(
            snapshot,
            constraints,
            pair,
        )
        if error:
            errors.append(error)
            continue
        ordered_pairs.append((pair, state))

    ordered_pairs.sort(
        key=lambda entry: _baseline_priority_key(entry[1], context),
        reverse=True,
    )
    if not ordered_pairs:
        return [], errors[0] if errors else "two_replacements_insufficient"

    plans: list[ArmorFarmPlan] = []
    seen: set[tuple] = set()
    search_nodes = 0
    for pair, state in ordered_pairs:
        pair_plans = _plans_for_pair(
            pair,
            state,
            context,
            top_n=max(1, top_n),
            search_budget=_MAX_TWO_PIECE_SEARCH_NODES - search_nodes,
        )
        search_nodes += pair_plans[1]
        if pair_plans[0] is None:
            return [], "farm_plan_search_too_large"
        for plan in pair_plans[0]:
            signature = _farm_plan_signature(plan)
            if signature in seen:
                continue
            seen.add(signature)
            plans.append(plan)
        if len(plans) >= top_n:
            break
        if search_nodes >= _MAX_TWO_PIECE_SEARCH_NODES:
            return [], "farm_plan_search_too_large"

    plans.sort(key=lambda plan: _plan_sort_key(plan, context))
    return plans[:top_n], None


def _equipped_baseline_state_for_slots(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    replacement_slots: tuple[str, str],
) -> tuple[_BaselineState | None, str | None]:
    selected: list[Armor] = []
    for slot in _SLOTS:
        if slot in replacement_slots:
            continue
        equipped = [armor for armor in snapshot.get_slot(slot) if armor.is_equipped]
        if len(equipped) != 1:
            return None, "equipped_armor_incomplete"
        if not _is_verified_baseline_armor(equipped[0]):
            return None, f"unverified_armor3_baseline:{slot}"
        selected.append(equipped[0])
    state = _state_from_items(tuple(selected), constraints)
    if state.exotic_count > 1:
        return None, "equipped_exotic_conflict"
    if constraints.exotic_hash is not None and not state.has_requested_exotic:
        return None, "unsupported_exotic_replacement"
    return state, None


def _plans_for_pair(
    pair: tuple[str, str],
    state: _BaselineState,
    context: FixedSetEvaluationContext,
    *,
    top_n: int,
    search_budget: int,
) -> tuple[list[ArmorFarmPlan] | None, int]:
    if search_budget <= 0:
        return None, 0
    rolls = _ordered_relevant_virtual_rolls(state, context)
    if not rolls:
        return [], 0

    max_mod_bonus = _max_mod_bonus_by_stat(context, state.artifice_count)
    plans: list[ArmorFarmPlan] = []
    searched = 0
    for first_index, first in enumerate(rolls):
        for second in rolls[first_index:]:
            searched += 1
            if searched > search_budget:
                return None, searched
            if not _pair_can_meet_targets(
                state,
                first,
                second,
                context,
                max_mod_bonus,
            ):
                continue
            plan = _plan_for_pair(
                pair,
                state,
                first,
                second,
                context,
            )
            if plan is not None:
                plans.append(plan)
                if len(plans) >= max(1, top_n):
                    return plans, searched
    return plans, searched


def _ordered_relevant_virtual_rolls(
    state: _BaselineState,
    context: FixedSetEvaluationContext,
) -> list[_VirtualRoll]:
    relevant_indices = tuple(
        sorted(
            {
                index
                for index, target in enumerate(context.desired_min)
                if target > state.stats[index] + context.bonus_vector[index]
            }
            | set(context.priority_indices)
        )
    )
    if not relevant_indices:
        relevant_indices = tuple(range(len(STAT_NAMES)))

    representatives: dict[tuple[int, ...], _VirtualRoll] = {}
    for roll in _virtual_rolls():
        signature = tuple(roll.projected[index] for index in relevant_indices)
        previous = representatives.get(signature)
        if previous is None or _roll_choice_key(roll) < _roll_choice_key(previous):
            representatives[signature] = roll

    deficits = tuple(
        max(
            0,
            context.desired_min[index]
            - state.stats[index]
            - context.bonus_vector[index],
        )
        for index in range(len(STAT_NAMES))
    )
    return sorted(
        representatives.values(),
        key=lambda roll: (
            -sum(
                min(deficits[index], roll.projected[index])
                for index in relevant_indices
            ),
            *(-roll.projected[index] for index in context.priority_indices),
            _roll_choice_key(roll),
        ),
    )


def _roll_choice_key(roll: _VirtualRoll) -> tuple:
    tuning = roll.tuning
    tuning_hash = None if tuning is None else tuning.plug_hash
    tuning_name = "" if tuning is None else _tuning_label(tuning)
    return (
        _tuning_rank(tuning_hash, tuning_name),
        -sum(roll.projected),
        roll.template.archetype.name,
        roll.template.tertiary_stat,
        tuning_hash or 0,
    )


def _baseline_priority_key(
    state: _BaselineState,
    context: FixedSetEvaluationContext,
) -> tuple:
    return (
        *(state.stats[index] for index in context.priority_indices),
        *(state.stats[index] for index in range(len(STAT_NAMES))),
    )


def _pair_can_meet_targets(
    state: _BaselineState,
    first: _VirtualRoll,
    second: _VirtualRoll,
    context: FixedSetEvaluationContext,
    max_mod_bonus: StatsVector,
) -> bool:
    return all(
        state.stats[index]
        + first.projected[index]
        + second.projected[index]
        + context.mod_stat_totals[index]
        + max_mod_bonus[index]
        >= context.desired_min[index]
        for index in range(len(STAT_NAMES))
    )


def _plan_for_pair(
    pair: tuple[str, str],
    state: _BaselineState,
    first: _VirtualRoll,
    second: _VirtualRoll,
    context: FixedSetEvaluationContext,
) -> ArmorFarmPlan | None:
    required_set_pieces = max(
        0,
        context.constraints.set_bonus_count
        - state.set_count
        - state.wildcard_count,
    )
    if required_set_pieces > len(pair):
        return None
    virtual_items = {
        slot: _template_process_item(
            roll.template,
            roll.projected,
            set_bonus_hash=(
                context.constraints.set_bonus_hash
                if index < required_set_pieces
                else None
            ),
        )
        for index, (slot, roll) in enumerate(
            zip(pair, (first, second), strict=True)
        )
    }
    items = _build_process_items_for_slots(state.items, virtual_items)
    evaluation = validate_fixed_process_items(items, context)
    if evaluation is None:
        return None
    total = [
        min(
            200,
            evaluation.stats[index]
            + evaluation.bonus_stats[index]
            + context.bonus_vector[index],
        )
        for index in range(len(STAT_NAMES))
    ]
    rolls = (first, second)
    pieces = [
        _farm_piece(
            slot,
            roll,
            requires_set_piece=index < required_set_pieces,
        )
        for index, (slot, roll) in enumerate(zip(pair, rolls, strict=True))
    ]
    return ArmorFarmPlan(
        replacement_count=2,
        baseline="equipped",
        pieces=pieces,
        projected_total=_stats_model(total),
        stat_mod_bonus=_stats_model(evaluation.bonus_stats),
        stat_mods=list(evaluation.stat_mods),
        locked_items=[
            {
                "slot": armor.slot,
                "name": armor.name,
                "icon_url": armor.icon_url,
            }
            for armor in sorted(state.items, key=lambda armor: armor.slot)
        ],
    )


def _build_process_items_for_slots(
    locked_items: tuple[Armor, ...],
    virtual_items: dict[str, ProcessItem],
) -> list[ProcessItem]:
    by_slot = {armor.slot: armor for armor in locked_items}
    result: list[ProcessItem] = []
    for slot in _SLOTS:
        if slot in virtual_items:
            result.append(virtual_items[slot])
        else:
            result.append(armor_to_process_item(by_slot[slot]))
    return result


def _farm_piece(
    replacement_slot: str,
    roll: _VirtualRoll,
    *,
    requires_set_piece: bool,
) -> ArmorFarmPiece:
    masterworked = roll.masterworked
    projected = roll.projected
    tuning = roll.tuning
    tuning_hash = None if tuning is None else tuning.plug_hash
    tuning_name = "" if tuning is None else _tuning_label(tuning)
    return ArmorFarmPiece(
        replacement_slot=replacement_slot,
        archetype_hash=roll.template.archetype.plug_hash,
        archetype_name=roll.template.archetype.name,
        primary_stat=roll.template.archetype.primary_stat,
        secondary_stat=roll.template.archetype.secondary_stat,
        tertiary_stat=roll.template.tertiary_stat,
        base_stats=_stats_model(roll.template.base_stats),
        masterworked_stats=_stats_model(masterworked),
        tuning_name=tuning_name,
        tuning_hash=tuning_hash,
        tuning_delta=_stats_model(
            [
                projected[index] - masterworked[index]
                for index in range(len(STAT_NAMES))
            ]
        ),
        projected_stats=_stats_model(projected),
        requires_set_piece=requires_set_piece,
    )


def _farm_plan_signature(plan: ArmorFarmPlan) -> tuple:
    return (
        tuple(
            (
                piece.replacement_slot,
                piece.archetype_name,
                piece.tertiary_stat,
                piece.tuning_hash,
            )
            for piece in plan.pieces
        ),
        tuple((item["slot"], item["name"]) for item in plan.locked_items),
    )


def _plan_sort_key(
    plan: ArmorFarmPlan,
    context: FixedSetEvaluationContext,
) -> tuple:
    final = tuple(
        plan.projected_total.model_dump()[stat_name] for stat_name in STAT_NAMES
    )
    target_excess = sum(
        max(0, final[index] - context.constraints.as_vector()[index])
        for index in range(len(STAT_NAMES))
    )
    return (
        *(-final[index] for index in context.priority_indices),
        sum(
            _tuning_rank(piece.tuning_hash, piece.tuning_name)
            for piece in plan.pieces
        ),
        -target_excess,
        -sum(final),
        tuple(
            (piece.replacement_slot, piece.archetype_name, piece.tertiary_stat)
            for piece in plan.pieces
        ),
        tuple((item["slot"], item["name"]) for item in plan.locked_items),
    )


def _equipped_baseline_states(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    replacement_slot: str,
) -> tuple[list[_BaselineState], str | None]:
    source_slots = [slot for slot in _SLOTS if slot != replacement_slot]
    selected: list[Armor] = []
    for slot in source_slots:
        equipped = [armor for armor in snapshot.get_slot(slot) if armor.is_equipped]
        if len(equipped) != 1:
            return [], "equipped_armor_incomplete"
        if not _is_verified_baseline_armor(equipped[0]):
            return [], f"unverified_armor3_baseline:{slot}"
        selected.append(equipped[0])
    state = _state_from_items(tuple(selected), constraints)
    if state.exotic_count > 1:
        return [], "equipped_exotic_conflict"
    if constraints.exotic_hash is not None and not state.has_requested_exotic:
        return [], "unsupported_exotic_replacement"
    return [state], None


def _inventory_options_for_slot(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    replacement_slot: str,
    context: FixedSetEvaluationContext,
    top_n: int,
) -> tuple[list[ArmorFarmOption], str | None]:
    """Search inventory baselines exactly, stopping once top N is proven.

    The old cross-product/Pareto pass could materialize hundreds of thousands
    of states before it knew which rolls mattered. Here each node carries an
    optimistic sort key. Once the collector is full, any branch whose best
    possible key cannot beat the current last result is safe to skip.
    """
    candidates_by_slot, error = _inventory_candidates(
        snapshot,
        constraints,
        replacement_slot,
        context,
        top_n,
    )
    if error:
        return [], error

    bounds = _inventory_search_bounds(candidates_by_slot, constraints)
    collector = _FarmOptionCollector(top_n, context)
    root = _InventorySearchNode(state=_empty_state(), depth=0)
    roots = sorted(
        (
            (
                _inventory_option_bound(root, roll, bounds, context),
                roll,
            )
            for roll in _virtual_rolls()
            if _inventory_state_can_complete(root, bounds, constraints)
            and _inventory_roll_can_meet_targets(root, roll, bounds, context)
        ),
        key=lambda entry: entry[0],
    )
    scheduled_nodes = 0

    for root_bound, roll in roots:
        if not collector.can_improve(root_bound):
            break
        scheduled_nodes += 1
        if scheduled_nodes > _MAX_INVENTORY_SEARCH_NODES:
            return [], "inventory_search_too_large"

        queue: list[tuple[tuple, int, _InventorySearchNode]] = [
            (root_bound, 0, root),
        ]
        sequence = 1
        while queue:
            bound, _, node = heapq.heappop(queue)
            if not collector.can_improve(bound):
                break

            if node.depth == len(candidates_by_slot):
                option = _option_for_state(
                    replacement_slot,
                    "inventory",
                    roll,
                    node.state,
                    context,
                )
                if option is not None:
                    collector.add(option)
                continue

            for armor in candidates_by_slot[node.depth]:
                if node.state.exotic_count + int(armor.is_exotic) > 1:
                    continue
                child = _InventorySearchNode(
                    state=_extend_state(node.state, armor, constraints),
                    depth=node.depth + 1,
                )
                if not _inventory_state_can_complete(child, bounds, constraints):
                    continue
                if not _inventory_roll_can_meet_targets(child, roll, bounds, context):
                    continue
                child_bound = _inventory_option_bound(child, roll, bounds, context)
                if not collector.can_improve(child_bound):
                    continue
                scheduled_nodes += 1
                if scheduled_nodes > _MAX_INVENTORY_SEARCH_NODES:
                    return [], "inventory_search_too_large"
                heapq.heappush(queue, (child_bound, sequence, child))
                sequence += 1

    return collector.options, None


def _inventory_candidates(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
    replacement_slot: str,
    context: FixedSetEvaluationContext,
    top_n: int,
) -> tuple[tuple[tuple[Armor, ...], ...], str | None]:
    source_slots = [slot for slot in _SLOTS if slot != replacement_slot]
    allowed_exotics = _requested_exotic_hashes(constraints)
    candidates_by_slot: list[tuple[Armor, ...]] = []
    for slot in source_slots:
        raw_candidates = list(snapshot.get_slot(slot))
        candidates = [
            armor for armor in raw_candidates if _is_verified_baseline_armor(armor)
        ]
        if not candidates:
            if any(armor.armor_system == "armor_3" for armor in raw_candidates):
                return (), f"unverified_armor3_baseline:{slot}"
            return (), f"inventory_slot_empty:{slot}"

        equivalent: dict[tuple, list[Armor]] = {}
        for armor in candidates:
            signature = _inventory_candidate_signature(
                armor,
                constraints,
                allowed_exotics,
            )
            equivalent.setdefault(signature, []).append(armor)
        # Later equivalent copies can be replaced by one of these earlier
        # names without changing validation, so more than top_n per signature
        # can never enter a top_n result.
        retained = [
            armor
            for matching in equivalent.values()
            for armor in sorted(matching, key=_armor_identity_key)[:top_n]
        ]
        candidates_by_slot.append(
            tuple(
                sorted(
                    retained,
                    key=lambda armor: _inventory_candidate_order_key(armor, context),
                )
            )
        )
    return tuple(candidates_by_slot), None


def _inventory_candidate_signature(
    armor: Armor,
    constraints: BuildConstraints,
    allowed_exotics: set[int],
) -> tuple:
    return (
        tuple(armor.stats.get(stat_name) for stat_name in STAT_NAMES),
        max(0, int(armor.energy_capacity or 0)),
        armor.is_artifice,
        armor.is_exotic,
        armor.item_hash in allowed_exotics,
        armor.set_bonus_hash == constraints.set_bonus_hash,
        armor.has_set_bonus_mod_socket,
    )


def _armor_identity_key(armor: Armor) -> tuple[str, str]:
    return armor.name, armor.item_instance_id


def _inventory_candidate_order_key(
    armor: Armor,
    context: FixedSetEvaluationContext,
) -> tuple:
    stats = tuple(armor.stats.get(stat_name) for stat_name in STAT_NAMES)
    priority = tuple(-stats[index] for index in context.priority_indices)
    return (
        *priority,
        -sum(stats),
        -max(0, int(armor.energy_capacity or 0)),
        -int(armor.is_artifice),
        armor.name,
        armor.item_instance_id,
    )


def _inventory_search_bounds(
    candidates_by_slot: tuple[tuple[Armor, ...], ...],
    constraints: BuildConstraints,
) -> _InventorySearchBounds:
    count = len(candidates_by_slot)
    zero_stats: StatsVector = (0, 0, 0, 0, 0, 0)
    max_stats: list[StatsVector] = [zero_stats for _ in range(count + 1)]
    max_artifice_counts = [0] * (count + 1)
    can_supply_requested_exotic = [False] * (count + 1)
    max_set_or_wildcard_counts = [0] * (count + 1)
    minimum_locked_items: list[tuple[tuple[str, str], ...]] = [
        () for _ in range(count + 1)
    ]
    allowed_exotics = _requested_exotic_hashes(constraints)

    for depth in range(count - 1, -1, -1):
        candidates = candidates_by_slot[depth]
        slot_max_stats = cast(
            StatsVector,
            tuple(
                max(armor.stats.get(stat_name) for armor in candidates)
                for stat_name in STAT_NAMES
            ),
        )
        max_stats[depth] = cast(
            StatsVector,
            tuple(
                slot_max_stats[index] + max_stats[depth + 1][index]
                for index in range(len(STAT_NAMES))
            ),
        )
        max_artifice_counts[depth] = (
            int(any(armor.is_artifice for armor in candidates))
            + max_artifice_counts[depth + 1]
        )
        can_supply_requested_exotic[depth] = (
            any(armor.item_hash in allowed_exotics for armor in candidates)
            or can_supply_requested_exotic[depth + 1]
        )
        max_set_or_wildcard_counts[depth] = (
            int(
                any(
                    armor.set_bonus_hash == constraints.set_bonus_hash
                    or armor.has_set_bonus_mod_socket
                    for armor in candidates
                )
            )
            + max_set_or_wildcard_counts[depth + 1]
        )
        minimum_locked_items[depth] = tuple(
            sorted(
                (
                    min((armor.slot, armor.name) for armor in candidates),
                    *minimum_locked_items[depth + 1],
                )
            )
        )

    return _InventorySearchBounds(
        max_stats=tuple(max_stats),
        max_artifice_counts=tuple(max_artifice_counts),
        can_supply_requested_exotic=tuple(can_supply_requested_exotic),
        max_set_or_wildcard_counts=tuple(max_set_or_wildcard_counts),
        minimum_locked_items=tuple(minimum_locked_items),
    )


def _inventory_state_can_complete(
    node: _InventorySearchNode,
    bounds: _InventorySearchBounds,
    constraints: BuildConstraints,
) -> bool:
    state = node.state
    if constraints.exotic_hash is not None and not state.has_requested_exotic:
        if not bounds.can_supply_requested_exotic[node.depth]:
            return False
    if constraints.set_bonus_count > 0:
        possible_set_pieces = (
            state.set_count
            + state.wildcard_count
            + bounds.max_set_or_wildcard_counts[node.depth]
            + 1  # The prospective legendary piece may belong to the target set.
        )
        if possible_set_pieces < constraints.set_bonus_count:
            return False
    return True


def _inventory_option_bound(
    node: _InventorySearchNode,
    roll: _VirtualRoll,
    bounds: _InventorySearchBounds,
    context: FixedSetEvaluationContext,
) -> tuple:
    state = node.state
    max_artifice_count = state.artifice_count + bounds.max_artifice_counts[node.depth]
    max_mod_bonus = _max_mod_bonus_by_stat(context, max_artifice_count)
    final = cast(
        StatsVector,
        tuple(
            min(
                200,
                state.stats[index]
                + bounds.max_stats[node.depth][index]
                + roll.projected[index]
                + context.mod_stat_totals[index]
                + max_mod_bonus[index]
                + context.bonus_vector[index],
            )
            for index in range(len(STAT_NAMES))
        ),
    )
    locked_items = tuple(
        sorted(
            (
                *((armor.slot, armor.name) for armor in state.items),
                *bounds.minimum_locked_items[node.depth],
            )
        )
    )
    return _farm_sort_key(
        tuning_hash=None if roll.tuning is None else roll.tuning.plug_hash,
        tuning_name="" if roll.tuning is None else _tuning_label(roll.tuning),
        final=final,
        archetype_name=roll.template.archetype.name,
        tertiary_stat=roll.template.tertiary_stat,
        locked_items=locked_items,
        context=context,
    )


def _inventory_roll_can_meet_targets(
    node: _InventorySearchNode,
    roll: _VirtualRoll,
    bounds: _InventorySearchBounds,
    context: FixedSetEvaluationContext,
) -> bool:
    state = node.state
    max_artifice_count = state.artifice_count + bounds.max_artifice_counts[node.depth]
    max_mod_bonus = _max_mod_bonus_by_stat(context, max_artifice_count)
    return all(
        state.stats[index]
        + bounds.max_stats[node.depth][index]
        + roll.projected[index]
        + context.mod_stat_totals[index]
        + max_mod_bonus[index]
        >= context.desired_min[index]
        for index in range(len(STAT_NAMES))
    )


def _max_mod_bonus_by_stat(
    context: FixedSetEvaluationContext,
    max_artifice_count: int,
) -> StatsVector:
    """Return a safe per-stat ceiling using only executable manifest mods."""
    bonuses: list[int] = []
    for stat_hash in STAT_HASHES:
        definitions = context.auto_mod_data.general_mods.get(stat_hash)
        max_general_bonus = 0
        if definitions is not None:
            max_general_bonus = max(
                MAJOR_STAT_BOOST if definitions.major_mod is not None else 0,
                MINOR_STAT_BOOST if definitions.minor_mod is not None else 0,
            )
        bonuses.append(
            context.info.num_available_general_mods * max_general_bonus
            + (
                max_artifice_count * ARTIFICE_STAT_BOOST
                if stat_hash in context.auto_mod_data.artifice_mods
                else 0
            )
        )
    return cast(StatsVector, tuple(bonuses))


@lru_cache(maxsize=1)
def _virtual_rolls() -> tuple[_VirtualRoll, ...]:
    rolls: list[_VirtualRoll] = []
    for template in all_tier5_templates():
        masterworked = masterworked_stats(template)
        if masterworked is None:
            continue
        for tuning in (None, *tuning_options(template)):
            projected = (
                masterworked if tuning is None else apply_tuning(template, tuning)
            )
            if projected is not None:
                rolls.append(_VirtualRoll(template, masterworked, projected, tuning))
    return tuple(rolls)


def _is_verified_baseline_armor(armor: Armor) -> bool:
    """Reject Armor 3.0 pieces whose parsed roll cannot be trusted."""
    return armor.armor_system != "armor_3" or armor.armor3_roll_verified


def _options_for_slot(
    replacement_slot: str,
    states: list[_BaselineState],
    context: FixedSetEvaluationContext,
    baseline: Literal["equipped", "inventory"],
) -> list[ArmorFarmOption]:
    results: list[ArmorFarmOption] = []
    for roll in _virtual_rolls():
        for state in states:
            option = _option_for_state(
                replacement_slot,
                baseline,
                roll,
                state,
                context,
            )
            if option is not None:
                results.append(option)
    return results


def _option_for_state(
    replacement_slot: str,
    baseline: Literal["equipped", "inventory"],
    roll: _VirtualRoll,
    state: _BaselineState,
    context: FixedSetEvaluationContext,
) -> ArmorFarmOption | None:
    requires_set_piece = (
        context.constraints.set_bonus_count > state.set_count + state.wildcard_count
    )
    virtual_item = _template_process_item(
        roll.template,
        roll.projected,
        set_bonus_hash=(
            context.constraints.set_bonus_hash if requires_set_piece else None
        ),
    )
    items = _build_process_items(state.items, replacement_slot, virtual_item)
    evaluation = validate_fixed_process_items(items, context)
    if evaluation is None:
        return None
    return _farm_option(
        replacement_slot,
        baseline,
        roll.template,
        roll.masterworked,
        roll.projected,
        roll.tuning,
        state.items,
        evaluation.stats,
        evaluation.bonus_stats,
        evaluation.stat_mods,
        context,
        requires_set_piece,
    )


def _template_process_item(
    template: ArmorRollTemplate,
    stats: StatsVector,
    *,
    set_bonus_hash: int | None,
) -> ProcessItem:
    """Create an internal, ID-less prospective legendary armor item.

    Armor 3.0 no longer exposes an Armor 2.0 energy capacity. Leaving this
    prospective piece at zero prevents the diagnostic from assuming unknown
    mod capacity while still allowing the four real pieces to contribute their
    verified mod resources.
    """
    return ProcessItem(
        id="",
        name=f"Tier-5 {template.archetype.name}",
        is_exotic=False,
        is_artifice=False,
        remaining_energy_capacity=0,
        stats=dict(zip(STAT_HASHES, stats, strict=True)),
        set_bonus=set_bonus_hash,
    )


def _build_process_items(
    locked_items: tuple[Armor, ...],
    replacement_slot: str,
    virtual_item: ProcessItem,
) -> list[ProcessItem]:
    by_slot = {armor.slot: armor for armor in locked_items}
    result: list[ProcessItem] = []
    for slot in _SLOTS:
        if slot == replacement_slot:
            result.append(virtual_item)
        else:
            result.append(armor_to_process_item(by_slot[slot]))
    return result


def _farm_option(
    replacement_slot: str,
    baseline: Literal["equipped", "inventory"],
    template: ArmorRollTemplate,
    masterworked: StatsVector,
    projected: StatsVector,
    tuning: ArmorTuningOption | None,
    locked_items: tuple[Armor, ...],
    base_stats: list[int],
    mod_bonus: list[int],
    mod_hashes: list[int],
    context: FixedSetEvaluationContext,
    requires_set_piece: bool,
) -> ArmorFarmOption:
    total = [
        min(200, base_stats[index] + mod_bonus[index] + context.bonus_vector[index])
        for index in range(6)
    ]
    tuning_delta = [projected[index] - masterworked[index] for index in range(6)]
    return ArmorFarmOption(
        replacement_slot=replacement_slot,
        baseline=baseline,
        archetype_hash=template.archetype.plug_hash,
        archetype_name=template.archetype.name,
        primary_stat=template.archetype.primary_stat,
        secondary_stat=template.archetype.secondary_stat,
        tertiary_stat=template.tertiary_stat,
        base_stats=_stats_model(template.base_stats),
        masterworked_stats=_stats_model(masterworked),
        tuning_name="" if tuning is None else _tuning_label(tuning),
        tuning_hash=None if tuning is None else tuning.plug_hash,
        tuning_delta=_stats_model(tuning_delta),
        projected_stats=_stats_model(projected),
        projected_total=_stats_model(total),
        stat_mod_bonus=_stats_model(mod_bonus),
        stat_mods=list(mod_hashes),
        requires_set_piece=requires_set_piece,
        locked_items=[
            {
                "slot": armor.slot,
                "name": armor.name,
                "icon_url": armor.icon_url,
            }
            for armor in sorted(locked_items, key=lambda armor: armor.slot)
        ],
    )


def _stats_model(values: list[int] | tuple[int, ...]) -> ArmorStats:
    return ArmorStats(
        **{stat_name: int(values[index]) for index, stat_name in enumerate(STAT_NAMES)}
    )


def _tuning_label(tuning: ArmorTuningOption) -> str:
    if tuning.kind == "balanced":
        return "Balanced"
    return f"+{tuning.increased_stat} / -{tuning.decreased_stat}"


def _option_sort_key(
    option: ArmorFarmOption,
    context: FixedSetEvaluationContext,
) -> tuple:
    final = tuple(
        option.projected_total.model_dump()[stat_name] for stat_name in STAT_NAMES
    )
    locked_items = tuple(
        (str(item["slot"]), str(item["name"])) for item in option.locked_items
    )
    return _farm_sort_key(
        tuning_hash=option.tuning_hash,
        tuning_name=option.tuning_name,
        final=final,
        archetype_name=option.archetype_name,
        tertiary_stat=option.tertiary_stat,
        locked_items=locked_items,
        context=context,
    )


def _farm_sort_key(
    *,
    tuning_hash: int | None,
    tuning_name: str,
    final: tuple[int, ...],
    archetype_name: str,
    tertiary_stat: str,
    locked_items: tuple[tuple[str, str], ...],
    context: FixedSetEvaluationContext,
) -> tuple:
    priority = tuple(-final[index] for index in context.priority_indices)
    target_excess = sum(
        max(0, final[index] - context.constraints.as_vector()[index])
        for index in range(len(STAT_NAMES))
    )
    return (
        *priority,
        _tuning_rank(tuning_hash, tuning_name),
        -target_excess,
        -sum(final),
        archetype_name,
        tertiary_stat,
        tuning_hash or 0,
        locked_items,
    )


def _tuning_rank(tuning_hash: int | None, tuning_name: str) -> int:
    if tuning_hash is None:
        return 0
    return 1 if tuning_name == "Balanced" else 2


def _empty_state() -> _BaselineState:
    return _BaselineState(
        items=(),
        stats=(0, 0, 0, 0, 0, 0),
        artifice_count=0,
        exotic_count=0,
        has_requested_exotic=False,
        set_count=0,
        wildcard_count=0,
    )


def _state_from_items(
    items: tuple[Armor, ...], constraints: BuildConstraints
) -> _BaselineState:
    state = _empty_state()
    for armor in items:
        state = _extend_state(state, armor, constraints)
    return state


def _extend_state(
    state: _BaselineState,
    armor: Armor,
    constraints: BuildConstraints,
) -> _BaselineState:
    stats = cast(
        StatsVector,
        tuple(
            state.stats[index] + armor.stats.get(stat_name)
            for index, stat_name in enumerate(STAT_NAMES)
        ),
    )
    allowed_exotics = _requested_exotic_hashes(constraints)
    return _BaselineState(
        items=(*state.items, armor),
        stats=stats,
        artifice_count=state.artifice_count + int(armor.is_artifice),
        exotic_count=state.exotic_count + int(armor.is_exotic),
        has_requested_exotic=(
            state.has_requested_exotic
            or (bool(allowed_exotics) and armor.item_hash in allowed_exotics)
        ),
        set_count=state.set_count
        + int(armor.set_bonus_hash == constraints.set_bonus_hash),
        wildcard_count=state.wildcard_count + int(armor.has_set_bonus_mod_socket),
    )


def _requested_exotic_hashes(constraints: BuildConstraints) -> set[int]:
    if constraints.exotic_hash is None:
        return set()
    result: set[int] = set()
    for item_hash in {*constraints.exotic_hashes, constraints.exotic_hash}:
        if item_hash is None:
            continue
        result.update({item_hash, to_signed(item_hash), to_unsigned(item_hash)})
    return result


def _normalize_slot(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _SLOT_ALIASES.get(value.strip().lower())


def _assumptions(baseline: str, max_replacements: int = 1) -> list[str]:
    assumptions = [
        "只生成五阶 Armor 3.0 传奇护甲的合法 30/25/20 模板。",
        "未拥有的护甲不携带虚拟实例、装备操作或 CanonicalBuild。",
        "Armor 3.0 未公开 Armor 2.0 能量容量；待刷件不假设可承载属性模组，已拥有的锁定件仍按当前资源验证。",
        f"最多检查 {max_replacements} 件待刷护甲，baseline={baseline}。",
    ]
    if max_replacements == 2:
        assumptions.append(
            "两件方案只在完整单件搜索无解后返回；它证明当前基线最少需要替换两件，不代表已列出所有两件组合。"
        )
    return assumptions
