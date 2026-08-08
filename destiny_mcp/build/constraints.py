"""Constraint Parser — normalizes BuildRequest → BuildConstraints.

Resolves exotic names to item hashes via the manifest and converts
character class names to classType integers.
"""

from __future__ import annotations

from ..exceptions import BuildValidationError
from ..logging_config import get_logger
from .constants import STAT_NAMES
from .models import BuildConstraints, BuildRequest

logger = get_logger(__name__)

# Map user-friendly stat names (including Chinese) to STAT_NAMES indices
_PRIORITY_STAT_MAP: dict[str, int] = {
    "weapons": 0, "weapon": 0, "武器": 0,
    "health": 1, "hp": 1, "生命": 1,
    "class_stat": 2, "class": 2, "职业": 2,
    "grenade": 3, "手雷": 3,
    "melee": 4, "近战": 4, "力量": 4, "strength": 4,
    "super_stat": 5, "super": 5, "超能": 5,
}


def parse(request: BuildRequest, manifest) -> BuildConstraints:
    """Convert a user-facing BuildRequest into solver-ready BuildConstraints.

    Resolves:
    - exotic_name → exotic_hash (via manifest search)
    - character_class → class_type (via manifest resolution)

    Args:
        request: The user's build request (from LLM or preset).
        manifest: ManifestManager for item name lookups.

    Returns:
        Normalized BuildConstraints for the solver.
    """
    exotic_hash: int | None = None
    exotic_hashes: set[int] = set()

    # Resolve the target class before filtering class-specific exotic armor.
    class_type: int | None = None
    if request.character_class:
        from ..manifest import CHARACTER_CLASS_MAP

        name = request.character_class.strip().lower()
        class_type = CHARACTER_CLASS_MAP.get(name)

    # Resolve exotic name — collect ALL hashes for the same item name.
    # Different versions of the same item (e.g. from different seasons)
    # have different hashes but the same name.
    if request.exotic_name:
        query = request.exotic_name.strip().casefold()
        results = manifest.search(request.exotic_name, limit=50)
        exotics = [
            result
            for result in results
            if result.get("itemType") == 2
            and result.get("tier") == 6
            and (
                class_type is None
                or result.get("classType", -1) in {-1, class_type}
            )
        ]
        exact_matches = [
            result
            for result in exotics
            if query
            in {
                str(result.get("name", "")).strip().casefold(),
                str(result.get("nameEn", "")).strip().casefold(),
            }
        ]
        if not exact_matches:
            raise BuildValidationError(
                f"无法精确确认指定金装“{request.exotic_name}”。"
                "请先进行模糊搜索，并让玩家确认正式名称。"
            )

        chosen = exact_matches[0]
        exotic_hash = chosen["itemHash"]
        chosen_name = str(chosen.get("name", "")).strip().casefold()
        chosen_name_en = str(chosen.get("nameEn", "")).strip().casefold()
        for result in exotics:
            result_name = str(result.get("name", "")).strip().casefold()
            result_name_en = str(result.get("nameEn", "")).strip().casefold()
            if (chosen_name and result_name == chosen_name) or (
                chosen_name_en and result_name_en == chosen_name_en
            ):
                exotic_hashes.add(result["itemHash"])

    # Resolve set bonus
    set_bonus_hash: int | None = None
    set_bonus_count: int = 0
    if request.set_bonus_name:
        set_bonus_info = manifest.search_set_bonus(request.set_bonus_name)
        if set_bonus_info:
            set_bonus_hash = set_bonus_info["set_hash"]
            set_bonus_count = request.set_bonus_count or 2
            logger.info(
                "Set bonus resolved: %s → hash=%d, count=%d",
                request.set_bonus_name, set_bonus_hash, set_bonus_count,
            )
        else:
            logger.warning("Set bonus not found: %s", request.set_bonus_name)
            raise BuildValidationError(
                f"无法确认指定套装加成“{request.set_bonus_name}”。"
                "请先查询可用套装并使用正式名称。"
            )

    requested_priorities = (
        request.priority_stats
        if request.priority_stats
        else ([request.priority_stat] if request.priority_stat else [])
    )
    priority_stat_indices: list[int] = []
    for priority in requested_priorities:
        key = priority.strip().lower()
        index = _PRIORITY_STAT_MAP.get(key)
        if index is None:
            logger.warning("Unknown priority stat: '%s'", priority)
        elif index not in priority_stat_indices:
            priority_stat_indices.append(index)
    priority_stat_index = (
        priority_stat_indices[0] if priority_stat_indices else None
    )

    constraints = BuildConstraints(
        weapons_min=request.weapons_target or 0,
        health_min=request.health_target or 0,
        class_stat_min=request.class_target or 0,
        grenade_min=request.grenade_target or 0,
        melee_min=request.melee_target or 0,
        super_stat_min=request.super_target or 0,
        exotic_hash=exotic_hash,
        exotic_hashes=exotic_hashes,
        class_type=class_type,
        top_n=request.top_n,
        set_bonus_hash=set_bonus_hash,
        set_bonus_count=set_bonus_count,
        priority_stat_indices=priority_stat_indices,
        priority_stat_index=priority_stat_index,
    )

    logger.debug(
        "Parsed constraints: exotic=%s class=%s set_bonus=%s(%d) stats=%s priority=%s",
        exotic_hash,
        class_type,
        set_bonus_hash,
        set_bonus_count,
        constraints.as_vector(),
        [STAT_NAMES[index] for index in priority_stat_indices],
    )
    return constraints
