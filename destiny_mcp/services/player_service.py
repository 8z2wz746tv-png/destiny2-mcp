"""Player service — Bungie name resolution and profile fetching.

Extracted from server.py per Rule 1: tools should not contain business logic.
"""

from __future__ import annotations

import math
import time

import aiobungie

from ..bungie_client import BungieClient
from ..exceptions import APIError, PlayerNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import CharacterInfo, PlayerInfo, ProfileResponse
from ..player_resolver import CURRENT_OAUTH_PLAYER, PlayerResolver

logger = get_logger(__name__)


class PlayerService:
    """Operations that resolve or fetch player/character data."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    async def search_player(self, player_name: str) -> list[PlayerInfo]:
        """Resolve a Bungie name to membership info.

        Raises:
            PlayerNotFoundError: If the name cannot be resolved.
        """
        logger.info("Searching for player: %s", player_name)
        if player_name == CURRENT_OAUTH_PLAYER:
            current = await self._resolver.resolve_player(CURRENT_OAUTH_PLAYER)
            return [PlayerInfo(**current)]

        results = await self._bungie.search_player(player_name)
        if not results:
            logger.warning("Player not found: %s", player_name)
            raise PlayerNotFoundError(player_name)
        logger.info(
            "Found %d player(s) for '%s'", len(results), player_name
        )
        return [PlayerInfo(**r) for r in results]

    async def find_players(self, name_prefix: str) -> list[dict]:
        """Fuzzy search for players by display name prefix.

        Uses /User/SearchUsers/ to find candidates, then scores them
        by playtime, last login, and triumph score.

        Args:
            name_prefix: Partial display name (without #code).

        Returns:
            List of dicts sorted by confidence score (best first).
        """
        logger.info("Fuzzy player search: '%s'", name_prefix)

        try:
            result = await self._bungie.search_users(name_prefix)
        except aiobungie.HTTPError as e:
            # 以前这里 `return []`：上游 405 被当成"没有这个人"，Agent 于是回答
            # "没找到叫 X 的玩家" —— 语料明令禁止把没查到说成不存在。现在显式失败。
            logger.error("User search failed: %s", e)
            status = int(getattr(getattr(e, "http_status", 0), "value", getattr(e, "http_status", 0)) or 0)
            raise APIError(
                "模糊搜索玩家",
                f"Bungie 的 User/SearchUsers 接口不可用（HTTP {status or '未知状态'}）。"
                "模糊找人现在用不了；请让用户给出完整 Bungie 名（形如 名字#1234）"
                "再用 intent=\"search\" 精确查找。",
            ) from e

        if not isinstance(result, dict) or result.get("ErrorCode", 0) != 1:
            logger.warning("User search API error: %s", result)
            raise APIError(
                "模糊搜索玩家",
                "Bungie 返回了错误响应（"
                + str(result.get("Message") if isinstance(result, dict) else type(result).__name__)
                + "）。模糊找人现在用不了；请让用户给出完整 Bungie 名（形如 名字#1234）"
                "再用 intent=\"search\" 精确查找。",
            )

        users = result.get("Response", [])
        if not users:
            logger.info("No users found for '%s'", name_prefix)
            return []

        # Score each candidate
        scored = []
        for user in users[:10]:  # Limit to 10 candidates
            membership_id = str(user.get("membershipId", ""))
            display_name = user.get("displayName", "")
            membership_type = user.get("membershipType", 0)

            if not membership_id:
                continue

            # Try to get Destiny profile for scoring
            score = 0.0
            last_played = ""
            total_playtime = 0
            triumph_score = 0

            try:
                profile = await self._resolver.get_profile(membership_id, membership_type, [200, 900])
                chars = profile.get("characters", {}).get("data", {})
                records = profile.get("profileRecords", {}).get("data", {})

                # Score: last login recency (30%)
                if chars:
                    last_dates = []
                    for char_data in chars.values():
                        lp = char_data.get("dateLastPlayed", "")
                        if lp:
                            last_dates.append(lp)
                    if last_dates:
                        last_played = max(last_dates)
                        try:
                            from datetime import datetime
                            last_dt = datetime.fromisoformat(last_played.replace("Z", "+00:00"))
                            days_ago = (datetime.now(last_dt.tzinfo) - last_dt).days
                            # Exponential decay: 30 days half-life
                            score += 30 * math.exp(-days_ago / 30)
                        except (ValueError, TypeError):
                            logger.debug("Could not parse last_played date '%s'", last_played)

                    # Score: playtime (30%)
                    for char_data in chars.values():
                        # totalTimeThisCharacter is in seconds
                        t = char_data.get("minutesPlayedTotal", 0)
                        total_playtime += int(t) if t else 0
                    # Logarithmic scaling, cap at 5000 hours
                    if total_playtime > 0:
                        score += 30 * min(1.0, math.log(total_playtime / 60 + 1) / math.log(5001))

                # Score: triumph score (25%)
                if records:
                    active_score = records.get("activeScore", 0)
                    triumph_score = active_score
                    if active_score > 0:
                        # Logarithmic scaling, cap at 100k
                        score += 25 * min(1.0, math.log(active_score + 1) / math.log(100001))

                # Score: has characters (15%)
                if chars:
                    score += 15

            except aiobungie.HTTPError as e:
                logger.debug("Could not fetch profile for %s: %s", membership_id, e)
                # Still include but with low score
                score = 5

            scored.append({
                "display_name": display_name,
                "membership_id": membership_id,
                "membership_type": membership_type,
                "confidence": round(score, 1),
                "last_played": last_played[:10] if last_played else "",
                "playtime_hours": round(total_playtime / 60) if total_playtime else 0,
                "triumph_score": triumph_score,
            })

        # Sort by confidence descending
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        logger.info("Found %d candidates for '%s'", len(scored), name_prefix)
        return scored

    async def get_profile(self, player_name: str) -> ProfileResponse:
        """Fetch a player's Destiny 2 profile with character summaries.

        Raises:
            PlayerNotFoundError: If the name cannot be resolved.
        """
        logger.info("Fetching profile for: %s", player_name)
        p = await self._resolver.resolve_player(player_name)

        mid = p["membership_id"]
        mtype = p["membership_type"]
        profile = await self._resolver.get_profile(mid, mtype, [100, 200])

        chars = profile.get("characters", {}).get("data", {})
        characters: list[CharacterInfo] = []
        for char_id, data in chars.items():
            characters.append(
                CharacterInfo(
                    id=char_id,
                    class_type=data.get("classType", -1),
                    class_name=class_type_name(data.get("classType", -1)),
                    light=data.get("light", 0),
                    emblem_path=data.get("emblemPath"),
                    last_played=str(data.get("dateLastPlayed", "")),
                )
            )

        logger.info(
            "Profile fetched for %s: %d character(s)",
            player_name,
            len(characters),
        )
        return ProfileResponse(
            display_name=str(p.get("display_name", "")),
            membership_id=mid,
            membership_type=mtype,
            characters=characters,
        )

    async def resolve_character_id(
        self,
        membership_id: str,
        membership_type: int,
        character_name: str,
    ) -> str:
        """Convert a friendly character name (hunter/titan/warlock) to a character ID.

        Raises:
            CharacterNotFoundError: If the character doesn't exist on the account.
        """
        return await self._resolver.resolve_character_id(
            membership_id, membership_type, character_name
        )
