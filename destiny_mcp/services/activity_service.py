"""Activity Service — activity history and PGCR queries.

Provides structured access to Bungie's activity history and
Post-Game Carnage Report endpoints with automatic name resolution.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import APIError, CharacterNotFoundError, ConfigError
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


def _unwrap_bungie_response(result: dict, operation: str) -> dict:
    error_code = result.get("ErrorCode")
    if error_code is not None and error_code != 1:
        raise APIError(operation, result.get("Message", ""))
    return result.get("Response", result)


def _stat_entry(values: dict, stat_id: str) -> dict:
    value = values.get(stat_id, {})
    basic = value.get("basic", {}) if isinstance(value, dict) else {}
    return {
        "value": basic.get("value", 0),
        "display": basic.get("displayValue", ""),
    }


def _first_stat(values: dict, stat_ids: list[str]) -> dict:
    for stat_id in stat_ids:
        if stat_id in values:
            return _stat_entry(values, stat_id)
    return {"value": 0, "display": ""}


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
            raise CharacterNotFoundError(character or "any", [])

        if character:
            from ..manifest import class_type_name, resolve_character_name
            target_type = resolve_character_name(character)
            for char_id, char_info in chars.items():
                if char_info.get("classType") == target_type:
                    return str(mid), char_id, mtype
            available = [class_type_name(info.get("classType", -1)) for info in chars.values()]
            raise CharacterNotFoundError(character, available)

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
                available = [class_type_name(info.get("classType", -1)) for info in chars.values()]
                raise CharacterNotFoundError(character, available)
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
                    raise ConfigError(
                        f"不支持活动模式 {mode!r}；请使用已知中英文模式名称或数字模式 ID。"
                    ) from None

        result_count = max(1, min(int(count), 250))
        params: dict = {"count": str(result_count)}
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
                raise APIError(
                    "读取活动历史",
                    f"{class_name} 返回了异常响应格式。",
                )
            response = _unwrap_bungie_response(
                result,
                f"读取 {class_name} 活动历史",
            )
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
        result_activities = all_activities[:result_count]
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

        pve_section = response.get("allPvE", {})
        pvp_section = response.get("allPvP", {})
        pve_all_time = (
            pve_section.get("allTime", pve_section)
            if isinstance(pve_section, dict)
            else {}
        )
        pvp_all_time = (
            pvp_section.get("allTime", pvp_section)
            if isinstance(pvp_section, dict)
            else {}
        )

        pve = _extract_stats(pve_all_time, pve_keys)
        pvp = _extract_stats(pvp_all_time, pvp_keys)

        return {"pve": pve, "pvp": pvp}

    async def get_unique_weapon_history(
        self,
        player_name: str,
        character: str | None = None,
        limit: int = 25,
    ) -> dict:
        """Fetch and summarize per-weapon historical usage for one character."""
        mid, char_id, mtype = await self._get_character_id(player_name, character)
        logger.info("Fetching unique weapon history: player=%s char=%s", player_name, char_id)

        result = await self._bungie.get_unique_weapon_history(mtype, mid, char_id)
        if not isinstance(result, dict):
            raise APIError("查询武器使用历史", "响应格式异常")
        response = _unwrap_bungie_response(result, "查询武器使用历史")

        weapons = []
        for raw in response.get("weapons", []):
            item_hash = raw.get("referenceId", 0)
            values = raw.get("values", {})
            kills = _first_stat(values, ["uniqueWeaponKills", "kills"])
            precision = _first_stat(values, ["uniqueWeaponPrecisionKills", "precisionKills"])
            item_info = self._manifest.get_item_info(item_hash)
            icon = item_info.get("icon", "") if isinstance(item_info, dict) else ""
            icon_url = (
                icon
                if isinstance(icon, str) and icon.startswith(("http://", "https://"))
                else f"https://www.bungie.net{icon}"
                if isinstance(icon, str) and icon.startswith("/")
                else ""
            )
            weapons.append({
                "item_hash": item_hash,
                "name": self._manifest.get_item_name(item_hash),
                "icon_url": icon_url,
                "kills": kills["value"],
                "kills_display": kills["display"],
                "precision_kills": precision["value"],
                "precision_kills_display": precision["display"],
                "values": values,
            })

        weapons.sort(key=lambda item: item.get("kills", 0), reverse=True)
        limited = weapons[: max(1, min(limit, 250))]
        return {
            "success": True,
            "character_id": char_id,
            "count": len(weapons),
            "weapons": limited,
            "message": f"找到 {len(weapons)} 把有历史记录的武器。",
        }

    async def get_aggregate_activity_stats(
        self,
        player_name: str,
        character: str | None = None,
        limit: int = 25,
    ) -> dict:
        """Fetch aggregate activity stats for one character."""
        mid, char_id, mtype = await self._get_character_id(player_name, character)
        logger.info("Fetching aggregate activity stats: player=%s char=%s", player_name, char_id)

        result = await self._bungie.get_destiny_aggregate_activity_stats(mtype, mid, char_id)
        if not isinstance(result, dict):
            raise APIError("查询活动聚合统计", "响应格式异常")
        response = _unwrap_bungie_response(result, "查询活动聚合统计")

        activities = []
        for raw in response.get("activities", []):
            activity_hash = raw.get("activityHash", 0)
            values = raw.get("values", {})
            completions = _first_stat(values, ["activityCompletions", "activitiesWon"])
            kills = _first_stat(values, ["activityKills", "kills"])
            seconds = _first_stat(values, ["activitySecondsPlayed", "secondsPlayed"])
            activities.append({
                "activity_hash": activity_hash,
                "activity_name": self._manifest.get_activity_name(activity_hash),
                "completions": completions["value"],
                "completions_display": completions["display"],
                "kills": kills["value"],
                "kills_display": kills["display"],
                "seconds_played": seconds["value"],
                "seconds_played_display": seconds["display"],
                "values": values,
            })

        activities.sort(
            key=lambda item: (item.get("completions", 0), item.get("kills", 0)),
            reverse=True,
        )
        limited = activities[: max(1, min(limit, 250))]
        return {
            "success": True,
            "character_id": char_id,
            "count": len(activities),
            "activities": limited,
            "message": f"找到 {len(activities)} 条活动聚合统计。",
        }

    async def get_leaderboards(
        self,
        player_name: str,
        character: str | None = None,
        modes: str | None = None,
        statid: str | None = None,
        maxtop: int = 10,
    ) -> dict:
        """Fetch account or character leaderboards."""
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id = None
        if character:
            char_id = await self._resolver.resolve_character_id(mid, mtype, character)
            result = await self._bungie.get_leaderboards_for_character(
                mtype,
                mid,
                char_id,
                maxtop=maxtop,
                modes=modes,
                statid=statid,
            )
        else:
            result = await self._bungie.get_leaderboards(
                mtype,
                mid,
                maxtop=maxtop,
                modes=modes,
                statid=statid,
            )
        if not isinstance(result, dict):
            raise APIError("查询排行榜", "响应格式异常")
        response = _unwrap_bungie_response(result, "查询排行榜")
        return {
            "success": True,
            "character_id": char_id,
            "leaderboards": response,
            "preview": self._leaderboard_preview(response),
            "message": "已读取排行榜数据。",
        }

    async def get_clan_leaderboards(
        self,
        group_id: str,
        modes: str | None = None,
        statid: str | None = None,
        maxtop: int = 10,
    ) -> dict:
        """Fetch clan leaderboards by Bungie group ID."""
        result = await self._bungie.get_clan_leaderboards(
            group_id,
            maxtop=maxtop,
            modes=modes,
            statid=statid,
        )
        if not isinstance(result, dict):
            raise APIError("查询公会排行榜", "响应格式异常")
        response = _unwrap_bungie_response(result, "查询公会排行榜")
        return {
            "success": True,
            "group_id": group_id,
            "leaderboards": response,
            "preview": self._leaderboard_preview(response),
            "message": "已读取公会排行榜数据。",
        }

    @staticmethod
    def _leaderboard_preview(response: dict, max_entries: int = 5) -> list[dict]:
        """Build a compact preview from Bungie's nested leaderboard response."""
        preview = []
        for mode_key, stats in response.items():
            if mode_key in {"focusMembershipId", "focusCharacterId"}:
                continue
            if not isinstance(stats, dict):
                continue
            for stat_key, board in stats.items():
                if not isinstance(board, dict):
                    continue
                entries = []
                for entry in board.get("entries", [])[:max_entries]:
                    player = entry.get("player", {}) or {}
                    destiny_user = player.get("destinyUserInfo", {}) or {}
                    value = entry.get("value", {}) or {}
                    basic = value.get("basic", {}) or {}
                    entries.append({
                        "rank": entry.get("rank"),
                        "player": destiny_user.get("displayName", ""),
                        "character_id": str(entry.get("characterId", "")),
                        "value": basic.get("value", 0),
                        "display": basic.get("displayValue", ""),
                    })
                preview.append({
                    "mode": mode_key,
                    "stat": board.get("statId", stat_key),
                    "entries": entries,
                })
        return preview
