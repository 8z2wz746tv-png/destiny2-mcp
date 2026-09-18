"""Activity Service — activity history and PGCR queries.

Provides structured access to Bungie's activity history and
Post-Game Carnage Report endpoints with automatic name resolution.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..bungie_client import BungieClient
from .. import activity_stats
from ..data import pvp_counters
from ..exceptions import InvalidArgumentError, APIError, CharacterNotFoundError, ConfigError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver
from ..utils.player_names import bungie_display_name, bungie_display_name_of_player

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
        # 把上游的码和状态一起带出来：只说"失败"的话，调用方分不清是
        # 上游接口问题、限流，还是自己的参数问题。
        status = str(result.get("ErrorStatus") or "").strip()
        detail = str(result.get("Message") or "").strip()
        parts = [f"上游 ErrorCode={error_code}"]
        if status:
            parts.append(f"ErrorStatus={status}")
        message = " ".join(parts) + (f"：{detail}" if detail else "")
        raise APIError(operation, message)
    return result.get("Response", result)


def _stats_response(result: Any, operation: str = "查询生涯统计") -> dict:
    """统计接口的响应：挡掉非 dict 与 ErrorCode≠1（其余地方沿用 `_unwrap_bungie_response`）。"""
    if not isinstance(result, dict):
        raise APIError(operation, "响应格式异常")
    return _unwrap_bungie_response(result, operation)



# 账号级统计只给这两组（真机实测：`mergedAllCharacters.results` 就是 allPvE/allPvP），
# 上游键 → 我们的分组键。单角色那边同一套键也在，所以两边能共用一条映射。
ACCOUNT_GROUPS: dict[str, str] = {"allPvE": "pve", "allPvP": "pvp"}


def _all_time(section: Any) -> dict:
    """`{"allTime": {...}}` → `{...}`；形状不对就是空（缺值不编）。"""
    if not isinstance(section, dict):
        return {}
    all_time = section.get("allTime", section)
    return all_time if isinstance(all_time, dict) else {}


def _results_of(node: Any) -> dict:
    """`mergedAllCharacters` / 单条角色 → 里面的 `results`（分组统计）字典。"""
    results = node.get("results") if isinstance(node, dict) else None
    return results if isinstance(results, dict) else {}



def _first_group_key(response: Any) -> str:
    """按模式请求的响应：第一个真正的分组键（如 `trials_of_osiris`），没有就是空串。

    带 `modes=` 时上游返回的组名就是模式名，且一次只给一个（多模式会多几个键，
    我们一次只问一个模式）；空对象 = 这个角色在这个模式下没有可统计的对局。
    """
    if not isinstance(response, dict):
        return ""
    return next((key for key, value in response.items() if isinstance(value, dict)), "")


def resolve_stats_mode(mode: str) -> tuple[str, int, str]:
    """模式词 → `(key, 上游 modes 数值, 中文标签)`。

    词表与数值的唯一出处是 `data/pvp_counters.py`（取自本地 Manifest 的
    `DestinyActivityModeDefinition.modeType`）。**别用 `ACTIVITY_MODES`**：那里
    `allpvp=9` 是错的，实测 `modes=9` 直接 500。中文标签也认（`铁旗` → iron_banner）。
    """
    word = (mode or "").strip()
    lowered = word.lower()
    key = lowered if lowered in pvp_counters.MODE_ACTIVITY_TYPES else ""
    if not key:
        key = next(
            (k for k, label in pvp_counters.MODE_LABELS_ZH.items() if label == word), ""
        )
    if not key:
        words = "、".join(pvp_counters.MODE_ACTIVITY_TYPES)
        labels = "、".join(
            pvp_counters.MODE_LABELS_ZH[k] for k in pvp_counters.MODE_ACTIVITY_TYPES
        )
        raise InvalidArgumentError(
            f"stats 不支持 mode={mode!r}；可取 {words}（或中文标签 {labels}）。"
            '不按模式查就留空；赛季数字用 intent="counters" + period="season"。'
        )
    return key, pvp_counters.MODE_ACTIVITY_TYPES[key], pvp_counters.MODE_LABELS_ZH[key]


def _stats_period_type(period: str) -> int:
    """周期词 → 上游 `periodType`。上游没有的周期**报错**，不许猜成"生涯"。"""
    key = (period or "career").strip().lower()
    if key in activity_stats.STATS_PERIOD_TYPES:
        return activity_stats.STATS_PERIOD_TYPES[key]
    reason = activity_stats.UNSUPPORTED_PERIOD_REASONS.get(
        key, f"统计接口没有 period={period!r} 这一档"
    )
    raise InvalidArgumentError(
        f"{reason}；统计接口只回答 career（生涯）。"
        '赛季/篇章数字只能由游戏内计数器回答：intent="counters" + period="season"/"act"。'
    )


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

    async def _resolve_membership(self, player_name: str) -> tuple[str, int]:
        """玩家 → (membership_id, membership_type)。账号级查询不需要角色。"""
        player = await self._resolver.resolve_player(player_name)
        return str(player["membership_id"]), int(player["membership_type"])

    async def _resolve_character(
        self, player_name: str, character: str | None = None
    ) -> tuple[str, str, int, str]:
        """(membership_id, character_id, membership_type, 角色名)。

        角色名要一起带出来：生涯统计得能标"这个数是哪个角色的"，光有 ID 说不清。
        `character=None` 时取第一个角色（与其它 intent 一致）。
        """
        from ..manifest import class_type_name, resolve_character_name

        mid, mtype = await self._resolve_membership(player_name)
        profile = await self._resolver.get_profile(mid, mtype, [200])
        chars = profile.get("characters", {}).get("data", {})

        if not chars:
            raise CharacterNotFoundError(character or "any", [])

        if character:
            target_type = resolve_character_name(character)
            for char_id, char_info in chars.items():
                if char_info.get("classType") == target_type:
                    return mid, char_id, mtype, class_type_name(target_type)
            available = [class_type_name(info.get("classType", -1)) for info in chars.values()]
            raise CharacterNotFoundError(character, available)

        char_id = next(iter(chars))
        return mid, char_id, mtype, class_type_name(chars[char_id].get("classType", -1))

    async def _get_character_id(self, player_name: str, character: str | None = None) -> tuple[str, str, int]:
        """Resolve player + character to (membership_id, character_id, membership_type)."""
        mid, char_id, mtype, _ = await self._resolve_character(player_name, character)
        return mid, char_id, mtype

    async def get_historical_stats(
        self,
        player_name: str,
        character: str | None = None,
        mode: str = "",
        period: str = "",
    ) -> dict:
        """Fetch lifetime PvE/PvP statistics.

        **默认是账号级**（P1 起）：不传 `character`/`mode` 时走 `GetHistoricalStatsForAccount`，
        每一行给三档 —— `existing`（现存角色）/ `deleted`（已删角色明细）/ `account_total`
        （账号级合计，**已含已删角色**）。真机实测（2026-09-17）熔炉生涯击败三档是
        50,622 / 28,242 / 78,864，其中 `account_total` 就是 `existing + deleted`：
        `mergedDeletedCharacters` 是那个和的加数之一，**不能再加一遍**（早期计划里的
        107,106 就是这么错的）。

        显式传 `character=` 才走单角色接口：payload 标 `scope="character"` 并带角色名，
        行里只有 `value`（这个角色自己的数）—— 单角色数字不再冒充生涯。

        `mode=` 走按角色的接口（`GetHistoricalStats`）：**`modes` 只在按角色端点上生效**
        （账号级端点会静默忽略，真机实测），所以账号级 + 模式要逐角色取、自己合，payload
        标 `aggregation="computed"`。`period=` 目前只认 `career`（=上游 `periodType=2`）：
        上游**没有赛季/篇章周期**，那两个词会报错并指向 `counters`（见
        `activity_stats.UNSUPPORTED_PERIOD_REASONS`），不猜、也不退化成生涯。

        跨角色怎么合并写在 `activity_stats.aggregate_kind`：可加的按 sum、最多的按 max、
        最快的按 min、比值类按公式重算（derived）、比不出的给 null（none，**不编**）。

        Args:
            player_name: Bungie name.
            character: 显式指定才要单角色（hunter/warlock/titan 或中文）。
            mode: 模式词（crucible/trials/iron_banner/competitive/gambit/raid 或中文标签）。
            period: 周期词（只认 career；season/act 上游没有）。

        Returns:
            Dict with `source`/`scope`/`mode`/`period` 标签与 `groups`（行式统计）。

        Raises:
            APIError: If the API returns an error or unexpected response.
            InvalidArgumentError: 模式词不在对照表里，或周期词上游没有。
            CharacterNotFoundError: 指定的角色不在这个账号上。
        """
        if period:
            _stats_period_type(period)  # 上游没有的周期直接报错，不退化成生涯
        if character:
            return await self._character_stats(player_name, character, mode, period)
        if mode:
            return await self._mode_stats(player_name, mode, period)
        return await self._account_stats(player_name)

    async def _account_stats(self, player_name: str) -> dict:
        """账号级三档统计（`GetHistoricalStatsForAccount`）。"""
        mid, mtype = await self._resolve_membership(player_name)
        logger.info("Fetching account-level historical stats: player=%s", player_name)

        result = await self._bungie.get_historical_stats_for_account(mtype, mid)
        response = _stats_response(result)

        merged = _results_of(response.get("mergedAllCharacters"))
        deleted = _results_of(response.get("mergedDeletedCharacters"))
        characters = [c for c in (response.get("characters") or []) if isinstance(c, dict)]

        # 现存角色那一档上游不直接给，只能自己合：按 aggregate_kind 的语义逐项合并。
        existing = {
            group: activity_stats.merge_sections(
                [
                    _all_time(_results_of(char).get(upstream))
                    for char in characters
                    if not char.get("deleted")
                ]
            )
            for upstream, group in ACCOUNT_GROUPS.items()
        }
        tiers = {
            group: (_all_time(merged.get(upstream)), _all_time(deleted.get(upstream)), existing[group])
            for upstream, group in ACCOUNT_GROUPS.items()
        }
        payload = activity_stats.tiered_groups(tiers)
        payload.update({
            "source": activity_stats.SOURCE_ACCOUNT,
            "scope": activity_stats.SCOPE_ACCOUNT,
            # 三档里 account_total/deleted 是上游自己合并的（比值、均值它都算好了），
            # existing 是我们按公式重算的 —— 这个区别写在 payload 里，别让读的人以为
            # 三个数的算法一样。
            "aggregation": "upstream_merged",
            "tiers": activity_stats.TIER_LABELS_ZH,
            "mode": None,
            "period": activity_stats.period_label("career"),
            "characters": {
                "existing": sum(1 for char in characters if not char.get("deleted")),
                "deleted": sum(1 for char in characters if char.get("deleted")),
                "total": len(characters),
            },
        })
        logger.info(
            "账号级统计：player=%s 角色=%d（现存 %d / 已删 %d）",
            player_name, len(characters), payload["characters"]["existing"],
            payload["characters"]["deleted"],
        )
        return payload

    async def _character_stats(
        self,
        player_name: str,
        character: str,
        mode: str = "",
        period: str = "",
    ) -> dict:
        """单角色统计（`GetHistoricalStats`）：标了角色名，就不再是"生涯"。

        给了 `mode` 就带上 `modes=`（这个参数只在按角色的端点上生效，见 `bungie_stats`）：
        上游返回的组名是模式名（如 `trials_of_osiris`），标签用我们对照表里的中文名。
        """
        period_type = _stats_period_type(period)
        mid, char_id, mtype, class_name = await self._resolve_character(player_name, character)
        logger.info(
            "Fetching character historical stats: player=%s char=%s(%s) mode=%s period=%s",
            player_name, char_id, class_name, mode or "all", period or "career",
        )

        mode_key = mode_type = mode_name = ""
        if mode:
            mode_key, mode_type, mode_name = resolve_stats_mode(mode)
            result = await self._bungie.get_historical_stats(
                mtype, mid, char_id, modes=mode_type, period_type=period_type
            )
            response = _stats_response(result)
            upstream_key = _first_group_key(response)
            sections = {mode_key: _all_time(response.get(upstream_key))} if upstream_key else {}
            payload = activity_stats.stat_groups(sections, labels={mode_key: mode_name})
        else:
            result = await self._bungie.get_historical_stats(mtype, mid, char_id)
            response = _stats_response(result)
            payload = activity_stats.stat_groups({
                "pve": _all_time(response.get("allPvE")),
                "pvp": _all_time(response.get("allPvP")),
            })
            upstream_key = ""

        payload.update({
            "source": activity_stats.SOURCE_CHARACTER,
            "scope": activity_stats.SCOPE_CHARACTER,
            "character": {"name": class_name, "character_id": char_id},
            "mode": (
                {"key": mode_key, "label": mode_name, "upstream_modes": mode_type,
                 "upstream_group": upstream_key}
                if mode else None
            ),
            "period": activity_stats.period_label(period or "career", period_type),
        })
        return payload

    async def _mode_stats(self, player_name: str, mode: str, period: str = "") -> dict:
        """账号级 + 按模式：逐角色取、自己合（账号级端点会**静默忽略** `modes`）。

        为什么要逐角色：真机实测（2026-09-18）`.../Account/{id}/Stats/?modes=84` 与不传
        `modes` 的响应一字不差（都是 allPvE/allPvP 合并视图）—— 想按模式拿数只有按角色这条路。
        角色清单取自账号级响应（含已删角色，它们的按角色统计同样取得到），并发请求。

        合并语义与 P1 三档同一套（`activity_stats.aggregate_kind`）：可加的相加、最多的取最大、
        比值按公式重算、比不出的给 null；**没有上游合并值可抄**（这个模式上游不给合并视图），
        所以 payload 标 `aggregation="computed"`。
        """
        period_type = _stats_period_type(period)
        mode_key, mode_type, mode_name = resolve_stats_mode(mode)
        mid, mtype = await self._resolve_membership(player_name)
        logger.info(
            "Fetching mode stats: player=%s mode=%s(%s) period=%s",
            player_name, mode_key, mode_type, period or "career",
        )

        account = await self._bungie.get_historical_stats_for_account(mtype, mid)
        characters = [
            c for c in (_stats_response(account).get("characters") or []) if isinstance(c, dict)
        ]
        if not characters:
            raise CharacterNotFoundError(mode_key, [])

        # 并发取每个角色这个模式的一段。个别角色读不到不该弄坏整份统计：
        # 记下来、写进 warnings，全失败才当失败（见下面的 raise）。
        fetched = await asyncio.gather(*[
            self._fetch_mode_section(mtype, mid, str(char.get("characterId", "")), mode_type, period_type)
            for char in characters
        ])
        sections: dict[str, list[dict]] = {"all": [], "existing": [], "deleted": []}
        failures: list[str] = []
        upstream_keys: list[str] = []
        for char, (section, upstream_key, error) in zip(characters, fetched):
            if error:
                failures.append(f"{char.get('characterId')}: {error}")
                continue
            if not section:
                continue  # 这个角色在这个模式下没有可统计的对局：不是 0，也不该编一行
            sections["all"].append(section)
            sections["deleted" if char.get("deleted") else "existing"].append(section)
            if upstream_key:
                upstream_keys.append(upstream_key)

        if not sections["all"]:
            if failures:
                raise APIError("查询按模式的生涯统计", "；".join(failures[:3]))
            return activity_stats.empty_mode_payload(mode_key, mode_name, mode_type, period, period_type)

        tiers = {
            mode_key: (
                activity_stats.merge_sections(sections["all"]),
                activity_stats.merge_sections(sections["deleted"]),
                activity_stats.merge_sections(sections["existing"]),
            )
        }
        payload = activity_stats.tiered_groups(tiers, labels={mode_key: mode_name})
        payload.update({
            "source": activity_stats.SOURCE_CHARACTER,
            "scope": activity_stats.SCOPE_ACCOUNT,
            # 与账号级不同：这个模式上游没有合并视图，三个档位全是我们按公式合的。
            "aggregation": "computed",
            "tiers": activity_stats.TIER_LABELS_ZH,
            "mode": {
                "key": mode_key, "label": mode_name, "upstream_modes": mode_type,
                "upstream_group": upstream_keys[0] if upstream_keys else "",
            },
            "period": activity_stats.period_label(period or "career", period_type),
            "characters": {
                "existing": sum(1 for c in characters if not c.get("deleted")),
                "deleted": sum(1 for c in characters if c.get("deleted")),
                "total": len(characters),
                "returned": len(sections["all"]),
            },
        })
        if failures:
            payload["unavailable"] = (
                f"{len(failures)} 个角色的这个模式统计没读到（已用其余 {len(sections['all'])} 个角色合并）："
                + "；".join(failures[:3])
            )
        return payload

    async def _fetch_mode_section(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
        mode_type: int,
        period_type: int,
    ) -> tuple[dict, str, str]:
        """取一个角色在某模式下的一段统计；失败**不抛**，把原因带回去（单个角色不许弄坏整份）。

        返回 `(这个模式的一段, 上游组名, 错误原因)`；调用方按角色清单 `zip` 回去拿
        `deleted` 标志（三档要分现存/已删）。
        """
        if not character_id:
            return {}, "", "角色 ID 缺失"
        try:
            result = await self._bungie.get_historical_stats(
                membership_type, membership_id, character_id,
                modes=mode_type, period_type=period_type,
            )
        except APIError as exc:
            logger.warning("按模式取统计失败：char=%s mode=%s：%s", character_id, mode_type, exc)
            return {}, "", str(exc)
        response = _stats_response(result)
        upstream_key = _first_group_key(response)
        section = _all_time(response.get(upstream_key)) if upstream_key else {}
        return section, upstream_key, ""

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
        # 空 ID 以前会拼出 .../PostGameCarnageReport// 打给 Bungie，拿回 404 后
        # 以"未捕获异常"的形式冒到客户端（没有 ok/error 信封，只有一段原始错误）。
        if not activity_id.strip() or not activity_id.strip().isdigit():
            raise InvalidArgumentError(
                "activity_id 需要是数字形式的活动实例 ID（先用 intent=\"history\" 拿一场的 ID）。"
            )
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
                "player_name": bungie_display_name(char_info) or "Unknown",
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

    async def get_unique_weapon_history(
        self,
        player_name: str,
        character: str | None = None,
        limit: int = 25,
    ) -> dict:
        """Fetch and summarize per-weapon historical usage for one character.

        **口径是"这个角色的全模式（PvE+PvP）武器击杀"**（P3b）：上游
        `GetUniqueWeaponHistory` 没有模式参数，榜首通常是刷本用的枪 ——
        所以 payload 必带 `scope="all_modes"` 与 `source`，话术直说"这不是 PvP 榜"。
        真要 PvP 武器榜只能拿 PGCR 逐场聚合最近 N 场（另做，且必须标"最近 N 场"）。
        """
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
                "stats": activity_stats.stat_rows(values),
            })

        weapons.sort(key=lambda item: item.get("kills", 0), reverse=True)
        limited = weapons[: max(1, min(limit, 250))]
        return {
            "scope": "all_modes",
            "source": "GetUniqueWeaponHistory",
            "message": (
                "已读取武器使用排行（scope=all_modes：**全模式** PvE+PvP 合计的武器击杀，"
                "不是 PvP 榜 —— 榜首多半是刷本用的枪）。"
            ),
            "character_id": char_id,
            "count": len(weapons),
            "weapons": limited,
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
                "stats": activity_stats.stat_rows(values),
            })

        activities.sort(
            key=lambda item: (item.get("completions", 0), item.get("kills", 0)),
            reverse=True,
        )
        limited = activities[: max(1, min(limit, 250))]
        return {
            "character_id": char_id,
            "count": len(activities),
            "activities": limited,
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
            # Bungie 对账号榜单接口会返回 HTTP 200 + ErrorCode:3 UnhandledException +
            # Response:null，SDK 把空响应拆成了 None，码在这一层已经拿不到。
            raise APIError(
                "查询排行榜",
                "Bungie 返回了空响应。这是上游接口的问题，不是你的账号问题；"
                "不要凭记忆给排名，稍后重试或改用生涯统计。",
            )
        response = _unwrap_bungie_response(result, "查询排行榜")
        return {
            "character_id": char_id,
            "leaderboards": {
                "entry_count": len(response.get("entries", []) or []),
                "entries": [
                    {
                        "rank": entry.get("rank"),
                        "player_name": bungie_display_name_of_player(
                            entry.get("player")
                        )
                        or "Unknown",
                        "class": (entry.get("player", {}) or {}).get("characterClass", ""),
                        "light_level": (entry.get("player", {}) or {}).get("lightLevel", 0),
                        "stats": activity_stats.stat_rows(entry.get("values", {})),
                    }
                    for entry in response.get("entries", []) or []
                ],
            },
            "preview": self._leaderboard_preview(response),
        }

    async def get_clan_leaderboards(
        self,
        group_id: str,
        modes: str | None = None,
        statid: str | None = None,
        maxtop: int = 10,
    ) -> dict:
        """Fetch clan leaderboards by Bungie group ID."""
        if not group_id.strip() or not group_id.strip().isdigit():
            raise InvalidArgumentError(
                "group_id 需要是数字形式的公会 ID（Bungie 群组 ID，不是公会名）。"
            )
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
            "group_id": group_id,
            "leaderboards": response,
            "preview": self._leaderboard_preview(response),
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
                        "player": bungie_display_name(destiny_user),
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
