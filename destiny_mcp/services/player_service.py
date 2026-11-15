"""Player service — Bungie name resolution and profile fetching.

Extracted from server.py per Rule 1: tools should not contain business logic.
"""

from __future__ import annotations

import asyncio
import math
import time

import aiobungie

from datetime import datetime

from ..bungie_client import BungieClient
from ..exceptions import APIError, PlayerNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name
from ..models import CharacterInfo, PlayerInfo, ProfileResponse
from ..player_resolver import CURRENT_OAUTH_PLAYER, PlayerResolver
from ..utils.player_names import bungie_display_name

logger = get_logger(__name__)

# 并发补候选档案的信号量。真机实测（10 个候选，同一账号连续跑）：
#   串行 16.7s ｜ 并发 3 → 3.9s ｜ 并发 5 → 4.3–14.5s（不稳）｜ 并发 10 → 2.8–4.2s（稳）
# 为什么 10 反而比 5 稳：候选上限就是 10，**一次扇出只等最慢的那一个**；
# 分两批等于把"某个人档案特别慢"的风险翻倍（实测就是这么出现 14.5s 的）。
_ENRICH_CONCURRENCY = 10
_ENRICH_SEMAPHORE = asyncio.Semaphore(_ENRICH_CONCURRENCY)

# 别人档案摘要的 TTL 缓存：`enrich=true` 是唯一还会读别人档案的入口（真机 1.1–4.6s/人），
# 而"名字记不全"往往要改一两个字搜几次 —— 同一批人 5 分钟内不必重拉。
# 只缓存**成功**的结果（失败走原来的降级，不把"没读到"存起来）。
_CANDIDATE_PROFILE_TTL_SECONDS = 300
_CANDIDATE_PROFILE_CACHE_LIMIT = 200


def _confidence_of(row: dict) -> float:
    """排序用的置信度：没算过或不是数值都当 0（不编分，也不让排序炸）。"""
    value = row.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


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
        self._candidate_profiles: dict[str, tuple[float, dict]] = {}

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

    async def find_players(
        self, name_prefix: str, page: int = 0, *, enrich: bool = False
    ) -> dict:
        """按名字前缀模糊找人。

        **默认只给候选清单**（名字#数字、ID、平台），**不拉任何人的档案** ——
        真机实测：给前 10 个候选逐个拉档案要 **21.8 秒**（整次调用 22.6 秒的 96%），
        而返回的数据只有 1.9 KB。那些档案只用来排"置信度"（最近游玩/时长/凯旋分），
        不该让"搜个人"比"查生涯"还慢。

        `enrich=True` 时才补这些字段，并且**一次扇出**地并发拉（候选上限 10），
        真机实测从 16.7 秒（串行）降到 **2.8–4.2 秒**。

        Args:
            name_prefix: 名字片段（不含 `#数字`）。
            page: 上游分页。
            enrich: 是否补"游玩时长/最近游玩/凯旋分"（默认否；要排序或分辨谁是谁时再开）。

        Returns:
            `{"players": [...], "page", "has_more", "candidate_count", "enriched": bool}`；
            `enrich=False` 时每行只有名字/ID/平台，`enrich=True` 时多四个评分字段。
        """
        logger.info("Fuzzy player search: '%s' (enrich=%s)", name_prefix, enrich)

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
                    "has_more": bool(result.get("hasMore")), "candidate_count": 0,
                    "enriched": enrich}

        candidates = []
        for user in users[:10]:  # Limit to 10 candidates
            membership_id, membership_type, display_name = _identity_of(user)
            if not membership_id:
                continue
            candidates.append({
                "display_name": display_name,
                "membership_id": membership_id,
                "membership_type": membership_type,
            })

        if enrich:
            await asyncio.gather(*(self._enrich_candidate(row) for row in candidates))

        candidates.sort(key=_confidence_of, reverse=True)
        logger.info(
            "Found %d candidate(s) for '%s' (page=%s has_more=%s enriched=%s)",
            len(candidates), name_prefix, result.get("page", page), bool(result.get("hasMore")), enrich,
        )
        return {
            "players": candidates,
            "page": int(result.get("page", page) or 0),
            "has_more": bool(result.get("hasMore")),
            "candidate_count": len(users),
            "enriched": enrich,
        }

    async def _candidate_profile(self, membership_id: str, membership_type: int) -> dict:
        """候选档案摘要：TTL 内直接复用（`enrich=true` 专用，见上面的常量注释）。"""
        key = f"{membership_type}:{membership_id}"
        cached = self._candidate_profiles.get(key)
        if cached and (time.monotonic() - cached[0]) < _CANDIDATE_PROFILE_TTL_SECONDS:
            return cached[1]
        profile = await self._resolver.get_profile(membership_id, membership_type, [200, 900])
        if len(self._candidate_profiles) >= _CANDIDATE_PROFILE_CACHE_LIMIT:
            self._candidate_profiles.clear()
        self._candidate_profiles[key] = (time.monotonic(), profile)
        return profile

    async def _enrich_candidate(self, row: dict) -> None:
        """给一个候选补"最近游玩/时长/凯旋分"（就地写进 row）。

        并发受 `_ENRICH_CONCURRENCY`（=10）限制：真机实测上游单次档案 1.1–4.6 秒，
        串行 16.7 秒；并发 3/5 会忽快忽慢（3.9–14.5 秒），10 个稳定在 2.8–4.2 秒。
        （PGCR 那边逐场结算的取舍不同：那里是并发 3 最划算，别把两处数字抄混。）
        失败只降级成低分并留痕，不让整次搜索失败 —— 搜索本身已经成功了。
        """
        async with _ENRICH_SEMAPHORE:
            membership_id = row["membership_id"]
            membership_type = row["membership_type"]
            score = 0.0
            last_played = ""
            total_playtime = 0
            triumph_score = 0
            try:
                profile = await self._candidate_profile(membership_id, membership_type)
                chars = profile.get("characters", {}).get("data", {})
                records = profile.get("profileRecords", {}).get("data", {})

                if chars:
                    last_dates = [
                        char_data.get("dateLastPlayed", "")
                        for char_data in chars.values()
                        if char_data.get("dateLastPlayed")
                    ]
                    if last_dates:
                        last_played = max(last_dates)
                        try:
                            last_dt = datetime.fromisoformat(last_played.replace("Z", "+00:00"))
                            days_ago = (datetime.now(last_dt.tzinfo) - last_dt).days
                            score += 30 * math.exp(-days_ago / 30)
                        except (ValueError, TypeError):
                            logger.debug("Could not parse last_played date '%s'", last_played)

                    for char_data in chars.values():
                        t = char_data.get("minutesPlayedTotal", 0)
                        total_playtime += int(t) if t else 0
                    if total_playtime > 0:
                        score += 30 * min(1.0, math.log(total_playtime / 60 + 1) / math.log(5001))

                if records:
                    active_score = records.get("activeScore", 0)
                    triumph_score = active_score
                    if active_score > 0:
                        score += 25 * min(1.0, math.log(active_score + 1) / math.log(100001))
                if chars:
                    score += 15
            except aiobungie.HTTPError as e:
                logger.debug("Could not fetch profile for %s: %s", membership_id, e)
                score = 5
            except Exception as exc:  # noqa: BLE001 - 别人的档案拉不到不该毁掉整次搜索
                logger.warning("候选档案读取失败 %s：%s", membership_id, exc)
                score = 5

            row.update({
                "confidence": round(score, 1),
                "last_played": last_played[:10] if last_played else "",
                "playtime_hours": round(total_playtime / 60) if total_playtime else 0,
                "triumph_score": triumph_score,
            })

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
    # 显示名走唯一出处（`utils/player_names`）：真机实测 Bungie 搜索接口对部分账号把
    # `bungieGlobalDisplayNameCode` 返回成**空字符串**，这里以前自己拼 `f"{name}#{code}"`
    # 于是得到 `名字#`（尾随一个空 #）—— 四个候选全看不出来是谁。别在这里再拼一次。
    display_name = bungie_display_name(candidate)

    memberships = [m for m in (candidate.get("destinyMemberships") or []) if isinstance(m, dict)]
    if not memberships:
        return "", 0, display_name
    override = [
        m for m in memberships
        if m.get("crossSaveOverride") and m.get("membershipType") == m.get("crossSaveOverride")
    ]
    chosen = (override or memberships)[0]
    return str(chosen.get("membershipId") or ""), int(chosen.get("membershipType") or 0), display_name
