"""Public formatting for read-only Armor 3.0 farm targets."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


_STAT_ALIASES = {
    "weapons": ("weapons",),
    "health": ("health",),
    "class": ("class", "class_stat"),
    "grenade": ("grenade",),
    "melee": ("melee",),
    "super": ("super", "super_stat"),
}
_DIRECT_REASONS = frozenset({
    "farm_target_ready",
    "single_replacement_insufficient",
    "two_replacements_insufficient",
    "unsupported_exotic_replacement",
    "equipped_armor_incomplete",
    "equipped_exotic_conflict",
    "inventory_search_too_large",
    "farm_plan_search_too_large",
    "inventory_multi_replacement_unavailable",
    "invalid_replacement_slot",
    "invalid_baseline",
    "invalid_max_replacements",
    "inventory_slot_empty",
    "unverified_armor3_baseline",
})
_PREFIX_REASONS = (
    "invalid_replacement_slot",
    "invalid_baseline",
    "invalid_max_replacements",
    "inventory_slot_empty",
    "unverified_armor3_baseline",
)


def serialize_farm_target_analysis(analysis: Any) -> dict[str, Any]:
    """Expose farm calculations without virtual IDs or equip metadata."""
    source = _mapping(analysis)
    reason = public_farm_reason(source.get("reason"))
    if reason == "farm_target_unavailable":
        return {
            "reason": reason,
            "max_possible": {},
            "suggested_farm": [],
            "farm_options": [],
            "farm_plans": [],
            "assumptions": [],
        }
    return {
        "reason": reason,
        "max_possible": _stats(source.get("max_possible")),
        "suggested_farm": _texts(source.get("suggested_farm"), limit=8),
        "farm_options": [
            _farm_option(option)
            for option in _values(source.get("farm_options"), limit=20)
            if _mapping(option)
        ],
        "farm_plans": [
            _farm_plan(plan)
            for plan in _values(source.get("farm_plans"), limit=10)
            if _mapping(plan)
        ],
        "assumptions": _texts(source.get("assumptions"), limit=8),
    }


def public_farm_reason(value: Any) -> str:
    reason = _text(value, 200)
    if reason in _DIRECT_REASONS:
        return reason
    for prefix in _PREFIX_REASONS:
        if reason.startswith(f"{prefix}:"):
            return prefix
    return "farm_target_unavailable"


def _farm_option(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        "replacement_slot": _text(source.get("replacement_slot"), 100),
        "baseline": _text(source.get("baseline"), 80),
        "archetype_name": _text(source.get("archetype_name"), 160),
        "primary_stat": _text(source.get("primary_stat"), 80),
        "secondary_stat": _text(source.get("secondary_stat"), 80),
        "tertiary_stat": _text(source.get("tertiary_stat"), 80),
        "base_stats": _stats(source.get("base_stats")),
        "masterworked_stats": _stats(source.get("masterworked_stats")),
        "tuning_name": _text(source.get("tuning_name"), 120),
        "tuning_delta": _stats(source.get("tuning_delta")),
        "projected_stats": _stats(source.get("projected_stats")),
        "projected_total": _stats(source.get("projected_total")),
        "requires_set_piece": source.get("requires_set_piece") is True,
        "locked_items": _locked_items(source.get("locked_items")),
    }


def _farm_plan(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        "replacement_count": _integer(
            source.get("replacement_count"), minimum=2, maximum=2
        ),
        "baseline": _text(source.get("baseline"), 80),
        "pieces": [
            _farm_piece(piece)
            for piece in _values(source.get("pieces"), limit=2)
            if _mapping(piece)
        ],
        "projected_total": _stats(source.get("projected_total")),
        "locked_items": _locked_items(source.get("locked_items")),
    }


def _farm_piece(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    return {
        "replacement_slot": _text(source.get("replacement_slot"), 100),
        "archetype_name": _text(source.get("archetype_name"), 160),
        "primary_stat": _text(source.get("primary_stat"), 80),
        "secondary_stat": _text(source.get("secondary_stat"), 80),
        "tertiary_stat": _text(source.get("tertiary_stat"), 80),
        "base_stats": _stats(source.get("base_stats")),
        "masterworked_stats": _stats(source.get("masterworked_stats")),
        "tuning_name": _text(source.get("tuning_name"), 120),
        "tuning_delta": _stats(source.get("tuning_delta")),
        "projected_stats": _stats(source.get("projected_stats")),
        "requires_set_piece": source.get("requires_set_piece") is True,
    }


def _locked_items(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw_item in _values(value, limit=4):
        item = _mapping(raw_item)
        name = _text(item.get("name"), 160)
        if name:
            items.append({
                "slot": _text(item.get("slot"), 100),
                "name": name,
                "icon_url": _text(item.get("icon_url") or item.get("icon"), 1000),
            })
    return items


def _stats(value: Any) -> dict[str, int | float]:
    source = _mapping(value)
    result: dict[str, int | float] = {}
    for output_key, aliases in _STAT_ALIASES.items():
        for alias in aliases:
            number = _number(source.get(alias))
            if number is not None:
                result[output_key] = number
                break
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _values(value: Any, *, limit: int) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _texts(value: Any, *, limit: int) -> list[str]:
    return [
        text
        for text in (_text(item, 500) for item in _values(value, limit=limit))
        if text
    ]


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _number(value: Any) -> int | float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        return None
    return value


def _integer(value: Any, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None
