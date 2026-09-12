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

    async def find_players(self, name_prefix: str, page: int = 0) -> dict:
        """Fuzzy search for players by display name prefix.

        Uses /User/SearchUsers/ to find candidates, then scores them
        by playtime, last login, and triumph score.

        Args:
            name_prefix: Partial display name (without #code).

        Returns:
            ``{"players": [...], "page": p, "has_more": bool, "candidate_count": n}``；
            `players` 按置信度排序（最好的在前）。`has_more` 为真时说明上游还有下一页候选。
        """
        logger.info("Fuzzy player search: '%s'", name_prefix)

        try:
            result = await self._bungie.search_users(name_prefix, page=page)
        except aiobungie.HTTPError as e:
            # 以前这里 `return []`：上游错误被当成"没有这个人"，Agent 于是回答
            # "没找到叫 X 的玩家" —— 语料明令禁止把没查到说成不存在。现在显式失败。
            logger.error("User search failed: %s", e)
            status = int(getattr(getattr(e, "http_status", 0), "value", getattr(e, "http_status", 0)) or 0)
            raise APIError(
                "模糊搜索玩家",
                f"Bungie 的用户搜索接口调用失败（HTTP {status or '未知状态'}）。"
                "请让用户给出完整 Bungie 名（形如 名字#1234）再用 intent=\"search\" 精确查找。",
            ) from e

        # 注意：`static_request` 已经把外层 ServerResponse 拆掉了，所以这里拿到的是
        # 内层载荷（searchResults/page/hasMore），**没有** ErrorCode 可查；
        # 上游 200 + 错误码的情况会以 None/非 dict 的形式落到这里。
        if not isinstance(result, dict):
            logger.warning("User search returned an unusable payload: %r", result)
            raise APIError(
                "模糊搜索玩家",
                "Bungie 返回了无法解析的搜索结果（可能上游临时故障）。"
                "请让用户给出完整 Bungie 名（形如 名字#1234）再用 intent=\"search\" 精确查找。",
            )

        # 官方现行端点（User/Search/GlobalName/）的形状：
        #   {searchResults: [{bungieGlobalDisplayName, bungieGlobalDisplayNameCode,
        #                     destinyMemberships: [{membershipId, membershipType, ...}]}],
        #    page, hasMore}
        # 旧的 User/SearchUsers/ 形状（顶层 displayName/membershipId）已经不存在了。
        users = [
            candidate
            for candidate in (result.get("searchResults") or [])
            if isinstance(candidate, dict)
        ]
        if not users:
            logger.info("No users found for '%s'", name_prefix)
            return {"players": [], "page": result.get("page", page),
                    "has_more": bool(result.get("hasMore")), "candidate_count": 0}

        # Score each candidate
        scored = []
        for user in users[:10]:  # Limit to 10 candidates
            membership_id, membership_type, display_name = _identity_of(user)
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
        logger.info(
            "Found %d candidate(s) for '%s' (page=%s has_more=%s)",
            len(scored), name_prefix, result.get("page", page), bool(result.get("hasMore")),
        )
        return {
            "players": scored,
            "page": int(result.get("page", page) or 0),
            "has_more": bool(result.get("hasMore")),
            "candidate_count": len(users),
        }

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


def _identity_of(candidate: dict) -> tuple[str, int, str]:
    """从官方搜索结果里取 (membership_id, membership_type, 显示名)。

    新形状把显示名与账号标识拆成两层：
    - 显示名：``bungieGlobalDisplayName`` + ``bungieGlobalDisplayNameCode``（拼成 名字#1234）
    - 账号：``destinyMemberships[]``（可能跨平台多条；优先 crossSaveOverride 指向的那条，
      否则取第一条）
    返回空 membership_id 表示这条候选没有可查的 Destiny 账号。
    """
    name = str(candidate.get("bungieGlobalDisplayName") or "")
    code = candidate.get("bungieGlobalDisplayNameCode")
    display_name = f"{name}#{code}" if name and code is not None else name

    memberships = [m for m in (candidate.get("destinyMemberships") or []) if isinstance(m, dict)]
    if not memberships:
        return "", 0, display_name
    override = [
        m for m in memberships
        if m.get("crossSaveOverride") and m.get("membershipType") == m.get("crossSaveOverride")
    ]
    chosen = (override or memberships)[0]
    return str(chosen.get("membershipId") or ""), int(chosen.get("membershipType") or 0), display_name
