"""Vendor shelf shaping — what the tabs are, what a name query matches, and how much is returned.

Separated from VendorService so these rules are readable and unit-testable without
any network access: the service fetches, this module decides what the shelf means.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from ..models import VendorCategory, VendorInfo, VendorRank

MENU_LIMIT_DEFAULT = 15
DETAIL_LIMIT_DEFAULT = 40
LIMIT_MAX = 250
WARNING_CAP = 8

# Decorative tabs that only show a blurb or open an unrelated screen.
# Live identifiers mix separators (gunsmith.help.name / xur_help_name /
# hawthorne_help.name), so match on the bare words.
_HELP_MARKERS = ("help.name", "help_name", "vendor_button", "_button.name")
# The rank-up rewards shelf. It is a normal tab, not a separate module.
_REWARDS_MARKERS = ("rank_rewards", "rank_reward")


@dataclass(frozen=True)
class VendorIdentity:
    """Who a vendor hash is, as far as a caller can name it."""

    vendor_hash: int
    name: str
    identifier: str

    @property
    def label(self) -> str:
        """Best available human label; falls back to the hash so nothing is nameless."""
        return self.name or self.identifier or f"#{self.vendor_hash}"


@dataclass(frozen=True)
class VendorMatch:
    """Result of resolving a caller's vendor_name.

    how is one of: hash | alias | exact | contains | absent | none.
    "absent" means the name/hash is a real, known vendor that this character's
    payload simply did not include; "none" means nothing here matches at all.
    """

    identities: tuple[VendorIdentity, ...]
    how: str
    hash_hint: int | None = None


def clamp_limit(limit: int | None, mode: str) -> int:
    """Turn a caller limit into a usable count for this mode."""
    default = MENU_LIMIT_DEFAULT if mode == "menu" else DETAIL_LIMIT_DEFAULT
    if limit is None:
        return default
    if limit <= 0:
        return default
    return min(int(limit), LIMIT_MAX)


def _norm(text: object) -> str:
    return " ".join(str(text or "").split()).casefold()


def vendor_identities(
    vendor_hashes: Iterable[int],
    definition_lookup: Callable[[int], Mapping | None],
    label_overrides: Mapping[int, str] | None = None,
) -> list[VendorIdentity]:
    """Name every vendor in the payload, including the ones the manifest leaves unnamed."""
    overrides = label_overrides or {}
    identities: list[VendorIdentity] = []
    seen: set[int] = set()
    for raw_hash in vendor_hashes:
        try:
            vendor_hash = int(raw_hash)
        except (TypeError, ValueError):
            continue
        if vendor_hash in seen:
            continue
        seen.add(vendor_hash)
        definition = definition_lookup(vendor_hash) or {}
        display_properties = definition.get("displayProperties") or {}
        identities.append(
            VendorIdentity(
                vendor_hash=vendor_hash,
                name=str(overrides.get(vendor_hash) or display_properties.get("name") or ""),
                identifier=str(definition.get("vendorIdentifier") or ""),
            )
        )
    return identities


def match_vendors(
    query: str,
    identities: Sequence[VendorIdentity],
    aliases: Mapping[str, int] | None = None,
) -> VendorMatch:
    """Resolve a name, alias, identifier fragment or hash to vendor identities."""
    text = str(query or "").strip()
    if not text:
        return VendorMatch((), "none")

    lowered = _norm(text)
    by_hash = {identity.vendor_hash: identity for identity in identities}

    if lowered.isdigit():
        wanted = int(lowered)
        hit = by_hash.get(wanted)
        if hit is not None:
            return VendorMatch((hit,), "hash")
        return VendorMatch((), "absent", wanted)

    for key, vendor_hash in (aliases or {}).items():
        if _norm(key) == lowered:
            hit = by_hash.get(int(vendor_hash))
            if hit is not None:
                return VendorMatch((hit,), "alias")
            return VendorMatch((), "absent", int(vendor_hash))

    exact = tuple(
        identity
        for identity in identities
        if lowered in {_norm(identity.name), _norm(identity.identifier)}
    )
    if exact:
        return VendorMatch(exact, "exact")

    partial = tuple(
        identity
        for identity in identities
        if lowered in _norm(identity.name) or lowered in _norm(identity.identifier)
    )
    if partial:
        return VendorMatch(partial, "contains")

    return VendorMatch((), "none")


def close_vendor_names(
    query: str,
    identities: Sequence[VendorIdentity],
    limit: int = 5,
) -> list[str]:
    """Nearest labels for a query that matched nothing, so the answer can suggest instead of shrug."""
    pool: dict[str, str] = {}
    for identity in identities:
        pool.setdefault(_norm(identity.name or identity.identifier), identity.label)
    matches = difflib.get_close_matches(_norm(query), list(pool), n=limit, cutoff=0.4)
    if matches:
        return [pool[key] for key in matches]
    return [identity.label for identity in identities[:limit]]


def classify_category(
    identifier: str,
    item_count: int,
    submenu_target: int | None,
) -> str | None:
    """Kind of a shelf tab; None means it is a decorative tile and should not be listed."""
    lowered = _norm(identifier)
    if any(marker in lowered for marker in _HELP_MARKERS):
        return None
    if any(marker in lowered for marker in _REWARDS_MARKERS):
        return "rewards"
    if submenu_target:
        return "submenu"
    if item_count <= 0:
        return None
    return "sale"


def category_entries(
    api_categories: Sequence[Mapping] | None,
    display_categories: Sequence[Mapping] | None,
    submenu_targets: Mapping[int, int] | None,
    available_vendors: Iterable[int] | None = None,
    vendor_label: Callable[[int], str] | None = None,
) -> list[VendorCategory]:
    """Turn the live categories component plus manifest tab metadata into listed tabs."""
    display_by_index: dict[int, Mapping] = {}
    for entry in display_categories or []:
        index = entry.get("index")
        if isinstance(index, int):
            display_by_index[index] = entry

    targets = submenu_targets or {}
    available = {int(v) for v in (available_vendors or ())}
    label = vendor_label or (lambda _hash: "")

    categories: list[VendorCategory] = []
    for entry in api_categories or []:
        index = entry.get("displayCategoryIndex")
        item_indexes = entry.get("itemIndexes") or []
        if not isinstance(index, int) or not item_indexes:
            continue
        meta = display_by_index.get(index) or {}
        identifier = str(meta.get("identifier") or "")
        name = str((meta.get("displayProperties") or {}).get("name") or "")
        target = targets.get(index)
        kind = classify_category(identifier, len(item_indexes), target)
        if kind is None:
            continue
        category = VendorCategory(
            index=index,
            name=name or identifier or f"分类{index}",
            identifier=identifier,
            kind=kind,
            item_count=len(item_indexes),
        )
        if kind == "submenu" and target:
            category.target_vendor_hash = int(target)
            category.target_vendor_name = label(int(target))
            category.target_available = int(target) in available
        categories.append(category)

    categories.sort(key=lambda category: category.index)
    return categories


def build_rank(
    progression: Mapping | None,
    progression_name: Callable[[int], str] | None = None,
) -> VendorRank | None:
    """Reputation progress, honestly labelled with its reset cycle."""
    if not isinstance(progression, Mapping) or not progression:
        return None
    progression_hash = int(progression.get("progressionHash") or 0)
    daily_limit = int(progression.get("dailyLimit") or 0)
    weekly_limit = int(progression.get("weeklyLimit") or 0)
    if weekly_limit:
        reset_hint = "每周重置"
    elif daily_limit:
        reset_hint = "每日重置"
    else:
        reset_hint = ""
    return VendorRank(
        progression_hash=progression_hash,
        name=(progression_name(progression_hash) if progression_name else "") or "",
        level=int(progression.get("level") or 0),
        level_cap=int(progression.get("levelCap") or 0),
        progress=int(progression.get("currentProgress") or 0),
        progress_to_next_level=int(progression.get("progressToNextLevel") or 0),
        next_level_at=int(progression.get("nextLevelAt") or 0),
        daily_progress=int(progression.get("dailyProgress") or 0),
        daily_limit=daily_limit,
        weekly_progress=int(progression.get("weeklyProgress") or 0),
        weekly_limit=weekly_limit,
        reset_hint=reset_hint,
    )


def menu_sort_key(vendor: VendorInfo) -> tuple:
    """Rank vendors first, then by how much of the shelf is actually buyable."""
    return (
        vendor.rank is None,
        -vendor.purchasable_items,
        -vendor.total_items,
        vendor.name or vendor.identifier,
    )


def cap_warnings(warnings: Sequence[str], cap: int = WARNING_CAP) -> list[str]:
    """Deduplicate warnings in order and admit when the tail was dropped."""
    unique: list[str] = []
    for warning in warnings:
        text = str(warning or "").strip()
        if text and text not in unique:
            unique.append(text)
    if len(unique) <= cap:
        return unique
    dropped = len(unique) - cap
    return unique[:cap] + [f"另有 {dropped} 条同类提示已省略。"]
