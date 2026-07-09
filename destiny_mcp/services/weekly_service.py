"""Weekly service — weekly reset milestone queries.

References DIM's milestone classification logic for handling edge cases
like Garden of Salvation not being auto-identified as a raid.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from ..bungie_client import BungieClient
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import (
    WeeklyActivity,
    WeeklyMilestone,
    WeeklyResetResponse,
)

logger = get_logger(__name__)

# DIM's manually maintained raid milestone hashes
# (Garden of Salvation doesn't auto-identify via friendlyName)
RAID_MILESTONE_HASHES: set[int] = {2712317338}

# Raid activity type hash
RAID_ACTIVITY_TYPE_HASH = 2043403989

# Category keywords for classification
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "nightfall": ["nightfall"],
    "trials": ["trials"],
    "raid": ["raid"],
    "crucible": ["crucible", "iron banner", "mayhem", "clash", "control"],
    "dungeon": ["dungeon"],
    "gambit": ["gambit"],
}


def _classify_milestone(
    milestone_hash: int,
    friendly_name: str,
    activity_type_hashes: list[int],
) -> str:
    """Classify a milestone into a category.

    Strategy (from DIM):
    1. Check friendlyName for keywords
    2. Check activity type hashes for raid
    3. Check manually maintained hash list
    """
    name_lower = friendly_name.lower()

    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return category

    # Check activity type
    if RAID_ACTIVITY_TYPE_HASH in activity_type_hashes:
        return "raid"

    # Manual hash fallback
    if milestone_hash in RAID_MILESTONE_HASHES:
        return "raid"

    return "other"


class WeeklyService:
    """Operations for querying weekly reset activities."""

    def __init__(self, bungie: BungieClient, manifest: ManifestManager) -> None:
        self._bungie = bungie
        self._manifest = manifest

    async def get_weekly_reset(self) -> WeeklyResetResponse:
        """Get current weekly reset milestones.

        Returns:
            WeeklyResetResponse with classified milestones.
        """
        logger.info("get_weekly_reset")

        raw = await self._bungie.fetch_milestones()

        milestones: list[WeeklyMilestone] = []

        for ms_hash_str, ms_data in raw.items():
            try:
                ms_hash = int(ms_hash_str)
            except ValueError:
                logger.debug("Skipping non-numeric milestone key: %s", ms_hash_str)
                continue

            ms_def = self._manifest.get_milestone_definition(ms_hash)
            if not ms_def:
                continue

            display = ms_def.get("displayProperties") or {}
            name = display.get("name", "")
            description = display.get("description", "")
            friendly_name = ms_def.get("friendlyName", "")

            # Collect activity type hashes for classification
            activity_type_hashes: list[int] = []
            activities: list[WeeklyActivity] = []

            # Parse activities from milestone data
            ms_activities = ms_data.get("activities", [])
            for act in ms_activities:
                act_hash = act.get("activityHash", 0)
                act_def = self._manifest.get_definition(
                    "DestinyActivityDefinition", act_hash
                )
                act_name = ""
                act_type = ""
                if act_def:
                    act_display = act_def.get("displayProperties") or {}
                    act_name = act_display.get("name", "")
                    at_hash = act_def.get("activityTypeHash", 0)
                    if at_hash:
                        activity_type_hashes.append(at_hash)
                        act_type = self._manifest.get_activity_type_name(at_hash)

                activities.append(WeeklyActivity(
                    name=act_name or f"Activity({act_hash})",
                    activity_type=act_type,
                ))

            # If no activities from API, try manifest
            if not activities and ms_def:
                manifest_acts = ms_def.get("activities", [])
                for ma in manifest_acts:
                    act_hash = ma.get("activityHash", 0)
                    if act_hash:
                        act_name = self._manifest.get_activity_name(act_hash)
                        activities.append(WeeklyActivity(name=act_name))

            # Classify
            category = _classify_milestone(
                ms_hash, friendly_name or name, activity_type_hashes
            )

            # End date
            end_date = ms_data.get("endDate", "")

            # Skip milestones with no name
            if not name and not friendly_name:
                continue

            milestones.append(WeeklyMilestone(
                milestone_hash=ms_hash,
                name=name or friendly_name,
                description=description,
                category=category,
                activities=activities,
                end_date=end_date,
            ))

        # Sort by category
        category_order = {"nightfall": 0, "trials": 1, "raid": 2, "dungeon": 3, "crucible": 4, "gambit": 5, "other": 9}
        milestones.sort(key=lambda m: (category_order.get(m.category, 9), m.name))

        # Next weekly reset (Tuesday 17:00 UTC)
        now = datetime.now(timezone.utc)
        days_until_tuesday = (1 - now.weekday()) % 7
        if days_until_tuesday == 0 and now.hour >= 17:
            days_until_tuesday = 7
        next_reset = now.replace(hour=17, minute=0, second=0, microsecond=0)
        next_reset = next_reset + timedelta(days=days_until_tuesday)

        logger.info("get_weekly_reset: %d milestones", len(milestones))
        return WeeklyResetResponse(
            milestones=milestones,
            reset_time=next_reset.isoformat(),
        )
