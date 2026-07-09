"""Activity Service — activity history and PGCR queries.

Provides structured access to Bungie's activity history and
Post-Game Carnage Report endpoints with automatic name resolution.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import APIError, PlayerNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver

logger = get_logger(__name__)

# Destiny 2 activity mode hashes
ACTIVITY_MODES: dict[str, int] = {
    "故事": 2,
    "strike": 3,
    "突袭": 4,
    "raid": 4,
    "熔炉": 5,
    "crucible": 5,
    "allpve": 7,
    "allpvp": 9,
    "patrol": 6,
    "巡逻": 6,
    "nightfall": 46,
    "日落": 46,
    "trialsofosiris": 84,
    "试炼": 84,
    "dungeon": 82,
    "地牢": 82,
    "gambit": 63,
    "智谋": 63,
    "grandmaster": 46,
    "大师日落": 46,
    "lostsector": 87,
    "失落区域": 87,
    "onslaught": 69,
    "猛攻": 69,
}

# 模式哈希 → 中文名（用于显示）
MODE_NAMES: dict[int, str] = {
    2: "故事",
    3: "打击",
    4: "突袭",
    5: "熔炉",
    6: "巡逻",
    7: "全PvE",
    9: "全PvP",
    46: "日落",
    63: "智谋",
    69: "猛攻",
    82: "地牢",
    84: "试炼",
    87: "失落区域",
}


class ActivityService:
    """Query activity history and Post-Game Carnage Reports."""

    def __init__(self, bungie: BungieClient, manifest: ManifestManager, resolver: PlayerResolver) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    async def _get_character_id(self, player_name: str, character: str | None = None) -> tuple[str, str, int]:
        """Resolve player + character to (membership_id, character_id, membership_type)."""
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        profile = await self._resolver.get_profile(mid, mtype, [200])
        chars = profile.get("characters", {}).get("data", {})

        if not chars:
            from ..exceptions import CharacterNotFoundError
            raise CharacterNotFoundError(character or "any", [])

        if character:
            from ..manifest import resolve_character_name
            target_type = resolve_character_name(character)
            for char_id, char_info in chars.items():
                if char_info.get("classType") == target_type:
                    return str(mid), char_id, mtype
            # Fall through to first character if target not found

        # Return first character
        char_id = next(iter(chars))
        return str(mid), char_id, mtype

    async def get_activity_history(
        self,
        player_name: str,
        character: str | None = None,
        mode: str | None = None,
        count: int = 10,
    ) -> list[dict]:
        """Fetch recent activity history with name resolution.

        Args:
            player_name: Bungie name.
            character: Target class (hunter/warlock/titan). If None, queries ALL characters.
            mode: Activity mode (突袭/熔炉/日落/试炼/地牢/智谋 etc). If None, all.
            count: Number of activities to return (max 250).

        Returns:
            List of activity dicts with resolved names, sorted by time (newest first).
        """
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        profile = await self._resolver.get_profile(mid, mtype, [200])
        chars = profile.get("characters", {}).get("data", {})

        if not chars:
            from ..exceptions import CharacterNotFoundError
            raise CharacterNotFoundError(character or "any", [])

        # Determine which characters to query
        char_ids: list[tuple[str, str]] = []  # (char_id, class_name)
        if character:
            from ..manifest import resolve_character_name, class_type_name
            target_type = resolve_character_name(character)
            for char_id, char_info in chars.items():
                if char_info.get("classType") == target_type:
                    char_ids.append((char_id, class_type_name(target_type)))
                    break
            if not char_ids:
                # Target not found, query all
                for char_id, char_info in chars.items():
                    char_ids.append((char_id, class_type_name(char_info.get("classType", -1))))
        else:
            from ..manifest import class_type_name
            for char_id, char_info in chars.items():
                char_ids.append((char_id, class_type_name(char_info.get("classType", -1))))

        # Resolve mode
        mode_hash = None
        if mode:
            mode_hash = ACTIVITY_MODES.get(mode.lower(), None)
            if mode_hash is None:
                try:
                    mode_hash = int(mode)
                except ValueError:
                    logger.warning("Unknown activity mode '%s', fetching all modes", mode)

        params: dict = {"count": str(min(count, 250))}
        if mode_hash is not None:
            params["mode"] = str(mode_hash)

        # Query each character
        all_activities: list[dict] = []
        for char_id, class_name in char_ids:
            logger.info(
                "Fetching activity history: player=%s char=%s(%s) mode=%s",
                player_name, char_id, class_name, mode or "all",
            )

            result = await self._bungie.get_activity_history(
                mtype, mid, char_id, params=params,
            )

            if not isinstance(result, dict):
                logger.error("Unexpected activity history response type: %s", type(result))
                continue

            error_code = result.get("ErrorCode")
            if error_code is not None and error_code != 1:
                logger.error("Activity history API error: code=%s msg=%s", error_code, result.get("Message", ""))
                continue

            response = result.get("Response", result)
            activities = response.get("activities", [])

            for act in activities:
                details = act.get("activityDetails", {})
                director_hash = details.get("directorActivityHash", 0)
                ref_hash = details.get("referenceId", 0)

                activity_name = self._manifest.get_activity_name(director_hash)
                if ref_hash and ref_hash != director_hash:
                    ref_name = self._manifest.get_activity_name(ref_hash)
                    if not ref_name.startswith("Activity("):
                        activity_name = ref_name

                activity_mode = details.get("mode", 0)
                all_activities.append({
                    "activity_name": activity_name,
                    "character": class_name,
                    "instance_id": details.get("instanceId", ""),
                    "mode": activity_mode,
                    "mode_name": MODE_NAMES.get(activity_mode, f"模式{activity_mode}"),
                    "is_completed": act.get("values", {}).get("completed", {}).get("basic", {}).get("displayValue", "") == "Yes",
                    "kills": act.get("values", {}).get("kills", {}).get("basic", {}).get("value", 0),
                    "deaths": act.get("values", {}).get("deaths", {}).get("basic", {}).get("value", 0),
                    "assists": act.get("values", {}).get("assists", {}).get("basic", {}).get("value", 0),
                    "time_played": act.get("values", {}).get("timePlayedSeconds", {}).get("basic", {}).get("displayValue", ""),
                    "standing": act.get("values", {}).get("standing", {}).get("basic", {}).get("displayValue", ""),
                    "start_time": act.get("period", ""),
                })

        # Sort by time descending, take top N
        all_activities.sort(key=lambda x: x["start_time"], reverse=True)
        result_activities = all_activities[:count]
        logger.info("Resolved %d activities across %d characters", len(result_activities), len(char_ids))
        return result_activities

    async def get_pgcr(self, activity_id: str) -> dict:
        """Fetch Post-Game Carnage Report for a specific activity.

        Args:
            activity_id: The activity instance ID.

        Returns:
            Dict with activity info and per-player entries.
        """
        logger.info("Fetching PGCR: activity_id=%s", activity_id)

        result = await self._bungie.get_pgcr(activity_id)

        if not isinstance(result, dict):
            raise APIError("获取 PGCR", f"响应格式异常: {type(result)}")

        error_code = result.get("ErrorCode")
        if error_code is not None and error_code != 1:
            raise APIError("获取 PGCR", result.get("Message", ""))

        response = result.get("Response", result)

        # Resolve activity name
        ref_hash = response.get("activityDetails", {}).get("referenceId", 0)
        activity_name = self._manifest.get_activity_name(ref_hash) if ref_hash else "Unknown"

        # Build player entries
        entries = []
        for entry in response.get("entries", []):
            player_info = entry.get("player", {})
            char_info = player_info.get("destinyUserInfo", {})
            stats = entry.get("values", {})

            entries.append({
                "player_name": char_info.get("displayName", "Unknown"),
                "platform": char_info.get("membershipType", 0),
                "class": player_info.get("characterClass", "Unknown"),
                "light_level": player_info.get("lightLevel", 0),
                "completed": stats.get("completed", {}).get("basic", {}).get("displayValue", "") == "Yes",
                "kills": stats.get("kills", {}).get("basic", {}).get("value", 0),
                "deaths": stats.get("deaths", {}).get("basic", {}).get("value", 0),
                "assists": stats.get("assists", {}).get("basic", {}).get("value", 0),
                "kd_ratio": stats.get("killsDeathsRatio", {}).get("basic", {}).get("displayValue", ""),
                "score": stats.get("score", {}).get("basic", {}).get("value", 0),
                "standing": stats.get("standing", {}).get("basic", {}).get("displayValue", ""),
            })

        return {
            "activity_name": activity_name,
            "instance_id": activity_id,
            "starting_phase": response.get("startingPhaseIndex", 0),
            "mode": response.get("activityDetails", {}).get("mode", 0),
            "start_time": response.get("period", ""),
            "team_count": len({e.get("values", {}).get("team", {}).get("basic", {}).get("value", 0) for e in response.get("entries", [])}),
            "entries": entries,
        }

    async def get_historical_stats(
        self,
        player_name: str,
        character: str | None = None,
    ) -> dict:
        """Fetch lifetime PvE/PvP statistics for a player.

        Args:
            player_name: Bungie name.
            character: Target class (hunter/warlock/titan). If None, uses first character.

        Returns:
            Dict with 'pve' and 'pvp' stat sections.

        Raises:
            APIError: If the API returns an error or unexpected response.
        """
        mid, char_id, mtype = await self._get_character_id(player_name, character)
        logger.info("Fetching historical stats: player=%s char=%s", player_name, char_id)

        result = await self._bungie.get_historical_stats(mtype, mid, char_id)

        if not isinstance(result, dict):
            raise APIError("查询生涯统计", "响应格式异常")

        error_code = result.get("ErrorCode")
        if error_code is not None and error_code != 1:
            raise APIError("查询生涯统计", result.get("Message", ""))

        response = result.get("Response", result)

        def _extract_stats(section: dict, keys: list[str]) -> dict[str, str]:
            out = {}
            for key in keys:
                val = section.get(key)
                if val:
                    out[val.get("statId", key)] = val.get("basic", {}).get("displayValue", "")
            return out

        pve_keys = ["activitiesEntered", "activitiesWon", "kills", "deaths", "assists",
                     "suicides", "precisionkills", "secondsPlayed"]
        pvp_keys = pve_keys + ["killsDeathsRatio"]

        pve = _extract_stats(response.get("allPvE", {}), pve_keys)
        pvp = _extract_stats(response.get("allPvP", {}), pvp_keys)

        return {"pve": pve, "pvp": pvp}
