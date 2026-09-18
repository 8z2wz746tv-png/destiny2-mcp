"""PvP 武器榜：**逐场 PGCR 聚合**（唯一可行的口径，见 `docs/plans/PVP_WEAPON_BOARD_PLAN.md`）。

为什么不走其它三条路（都实测过，别再试）：

| 路 | 为什么不行 |
| --- | --- |
| `GetUniqueWeaponHistory` | **没有模式参数**，只能全模式 PvE+PvP（榜首是刷本枪） |
| `GetHistoricalStats*` | 武器聚合没有模式维度，拆不出 PvP |
| 计数器（组件 1100） | 只有"击败对手数"这类总量，没有按武器拆 |

所以答案是"**最近 N 场**"而不是生涯 —— 整个模块的关键不是聚合算法，是**别让人以为是生涯**：
`scope="pvp_recent"`、`matches_analyzed`、`window`（起止时间）三样一起给，
`window` 尤其不能省：真机上"最近 250 场"对某些角色是 2023-09 → 2025-12，跨两年多。

上游形状（2026-09-18 真机实测）：

- `GetActivityHistory` 的**单数** `mode=` 接受伞形模式（`mode=5` → 250 场全是 PvP，
  子模式 43/44/71/73/84/89/91…）；**复数 `modes=` 会被静默忽略**（返回 PvE 场次），
  所以这里只拼 `mode`。`page` 用来回溯，一页 250 场。
- PGCR 的武器数据在 `entries[].extended.weapons[]`，每条给
  `referenceId` + `values.uniqueWeaponKills / uniqueWeaponPrecisionKills /
  uniqueWeaponKillsPrecisionKills`。**它是按玩家分行的** ——
  必须按 `characterId` 找到自己那一行，取 `entries[0]` 会把别人的枪算到你头上
  （第一版探针就这么错过，见守门测试）。
- 成本（同一批 8 场实测）：串行 0.54 场/秒、**并发 3 → 0.98 场/秒**、并发 6 → 0.89 场/秒
  （再高没收益）。单场 PGCR 46–52 KB。所以并发固定 3，默认只分析 10 场（≈10 秒）。
- PGCR **不可变**：落盘缓存后重跑是 0 网络请求（缓存只写我们自己的目录，不碰账号）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..bungie_client import BungieClient
from ..data import activity_modes
from ..exceptions import APIError, CharacterNotFoundError, InvalidArgumentError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name
from ..player_resolver import PlayerResolver
from .activity_service import _unwrap_bungie_response

logger = get_logger(__name__)

# 每角色取一页历史（上游单页上限 250）；默认 10 场、上限 100 场。
HISTORY_PAGE_SIZE = 250
DEFAULT_MATCHES = 10
MAX_MATCHES = 100

# 并发固定 3：实测吞吐的甜点（并发 6 反而降到 0.89 场/秒，像撞上游限流）。
PGCR_CONCURRENCY = 3

# 缓存目录：`~/.destiny_mcp/cache/pgcr/`。PGCR 不可变，缓存命中即等价于上游结果。
CACHE_SUBDIR = ("cache", "pgcr")


def _cache_dir() -> Path:
    from .. import config

    return Path(config.DESTINY_TOKEN_PATH, *CACHE_SUBDIR)


def _stat_value(values: dict, *keys: str) -> float | None:
    """从 `values.<key>.basic.value` 取数字；取不到给 `None`（缺值不编 0）。"""
    for key in keys:
        entry = values.get(key)
        basic = entry.get("basic") if isinstance(entry, dict) else None
        if isinstance(basic, dict):
            value = basic.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _as_int(value: float | None) -> int | None:
    return None if value is None else int(round(value))


class PgcrCache:
    """PGCR 落盘缓存（`instanceId → 原始响应`）。

    只读上游、只写自己的缓存目录，不涉及账号写入，所以不需要 `confirmed`。
    缓存坏掉/写不进去都不许影响主结果：读不到就当没缓存，写失败只记日志
    （磁盘满、只读挂载都不该让一次查询失败）。
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or _cache_dir()

    def path_for(self, instance_id: str) -> Path:
        return self.directory / f"{instance_id}.json"

    def get(self, instance_id: str) -> dict | None:
        path = self.path_for(instance_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def put(self, instance_id: str, payload: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.path_for(instance_id).with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            tmp.replace(self.path_for(instance_id))
        except OSError as exc:
            logger.debug("PGCR 缓存写入失败 %s: %s", instance_id, exc)


class PvpWeaponService:
    """`activity_assistant(intent="pvp_weapons")` 的服务层。"""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        cache: PgcrCache | None = None,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._cache = cache or PgcrCache()

    # ── 取数 ────────────────────────────────────────────────────────────────

    async def _characters(self, player_name: str, character: str | None) -> tuple[str, int, dict, list[tuple[str, str]]]:
        """返回 `(membership_id, membership_type, characters, [(char_id, 职业名)])`。"""
        player = await self._resolver.resolve_player(player_name)
        mid = str(player["membership_id"])
        mtype = int(player["membership_type"])
        profile = await self._resolver.get_profile(mid, mtype, [200])
        chars = (profile.get("characters") or {}).get("data") or {}
        if not chars:
            raise CharacterNotFoundError(character or "any", [])

        targets: list[tuple[str, str]] = []
        if character:
            from ..manifest import resolve_character_name

            wanted = resolve_character_name(character)
            for char_id, info in chars.items():
                if info.get("classType") == wanted:
                    targets.append((char_id, class_type_name(wanted)))
            if not targets:
                available = [class_type_name(info.get("classType", -1)) for info in chars.values()]
                raise CharacterNotFoundError(character, available)
        else:
            for char_id, info in chars.items():
                targets.append((char_id, class_type_name(info.get("classType", -1))))
        return mid, mtype, chars, targets

    async def _pvp_matches(
        self, mid: str, mtype: int, char_id: str, mode_type: int
    ) -> list[dict]:
        """该角色最近一页 PvP 历史（按上游返回顺序，已是最新在前）。"""
        result = await self._bungie.get_activity_history(
            mtype,
            mid,
            char_id,
            # 单数 `mode=`：复数 `modes=` 会被上游静默忽略（实测返回 PvE 场次）。
            params={"count": str(HISTORY_PAGE_SIZE), "mode": str(mode_type), "page": "0"},
        )
        response = _unwrap_bungie_response(result, "读取 PvP 活动历史")
        return [act for act in (response.get("activities") or []) if isinstance(act, dict)]

    async def _pgcr(self, instance_id: str) -> dict:
        cached = self._cache.get(instance_id)
        if cached is not None:
            logger.debug("PGCR 命中缓存 %s", instance_id)
            return cached
        result = await self._bungie.get_pgcr(instance_id)
        payload = _unwrap_bungie_response(result, "读取对局结算")
        self._cache.put(instance_id, payload)
        return payload

    @staticmethod
    def _own_weapons(pgcr: dict, char_id: str) -> list[dict]:
        """从 PGCR 里取**自己那一行**的武器。

        `extended.weapons` 是按玩家分行的：拿 `entries[0]` 就是拿别人（或队友）的枪。
        上游在有些场次里 `characterId` 缺失，那就退一步按 membership 找，仍找不到就
        返回空清单（**不猜**：宁少一场，也不把别人的武器算进来）。
        """
        for entry in pgcr.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("characterId", "")) != str(char_id):
                continue
            extended = entry.get("extended") or {}
            weapons = extended.get("weapons") or []
            return [w for w in weapons if isinstance(w, dict)]
        return []

    async def get_pvp_weapon_board(
        self,
        player_name: str,
        character: str | None = None,
        mode: str = "pvp",
        matches: int = DEFAULT_MATCHES,
    ) -> dict[str, Any]:
        """最近 N 场 PvP 的武器击杀榜（**不是生涯**，window 必看）。"""
        key = activity_modes.resolve(mode or "pvp")
        if key is None:
            labels = {
                word: self._manifest.get_activity_mode_name(activity_modes.MODE_TYPE[word])
                for word in activity_modes.MODES
            }
            raise InvalidArgumentError(
                f"pvp_weapons 不支持 mode={mode!r}；可取 "
                f"{activity_modes.words_with_labels(labels)}。"
            )
        mode_type = activity_modes.MODE_TYPE[key]
        mode_label = self._manifest.get_activity_mode_name(mode_type) or key

        wanted = max(1, min(int(matches), MAX_MATCHES))
        mid, mtype, _chars, targets = await self._characters(player_name, character)

        warnings: list[str] = []
        history_failures: list[str] = []
        candidates: dict[str, dict] = {}
        per_character: list[dict] = []
        for char_id, class_name in targets:
            try:
                activities = await self._pvp_matches(mid, mtype, char_id, mode_type)
            except Exception as exc:  # 单角色失败只丢这个角色，其余照算（带 warnings）
                logger.warning("PvP 历史拉取失败 char=%s: %s", char_id, exc)
                history_failures.append(f"{class_name}：{exc}")
                continue
            per_character.append({
                "character_id": char_id,
                "class": class_name,
                "history_count": len(activities),
                "has_more_history": len(activities) >= HISTORY_PAGE_SIZE,
            })
            for act in activities:
                details = act.get("activityDetails") or {}
                instance_id = str(details.get("instanceId", "") or "")
                if not instance_id or instance_id in candidates:
                    continue
                candidates[instance_id] = {
                    "instance_id": instance_id,
                    "character_id": char_id,
                    "class": class_name,
                    "period": str(act.get("period", "") or ""),
                    "activity_mode": details.get("mode", 0),
                }

        if not candidates:
            detail = "；".join(history_failures) if history_failures else "上游没有返回比赛记录"
            raise APIError(
                "查询 PvP 武器榜",
                f"{mode_label} 最近没有可用的比赛记录（{detail}）。"
                "换个模式（如 mode=\"pvp\"）或先在游戏里打几场再看。",
            )

        ordered = sorted(
            candidates.values(), key=lambda item: item["period"], reverse=True
        )[:wanted]

        semaphore = asyncio.Semaphore(PGCR_CONCURRENCY)

        async def load(match: dict) -> tuple[dict, dict | None, str]:
            async with semaphore:
                try:
                    pgcr = await self._pgcr(match["instance_id"])
                except Exception as exc:  # 单场失败不毁整轮：如实记进 failed_matches
                    logger.warning("PGCR 拉取失败 %s: %s", match["instance_id"], exc)
                    return match, None, str(exc)
                return match, pgcr, ""

        loaded = await asyncio.gather(*(load(match) for match in ordered))

        totals: dict[int, dict] = {}
        mode_tally: dict[int, int] = {}
        analyzed: list[dict] = []
        failed: list[dict] = []
        for match, pgcr, reason in loaded:
            if pgcr is None:
                failed.append({"instance_id": match["instance_id"], "reason": reason})
                continue
            analyzed.append(match)
            mode_tally[match["activity_mode"]] = mode_tally.get(match["activity_mode"], 0) + 1
            for weapon in self._own_weapons(pgcr, match["character_id"]):
                item_hash = weapon.get("referenceId")
                if not isinstance(item_hash, int):
                    continue
                values = weapon.get("values") or {}
                kills = _as_int(_stat_value(values, "uniqueWeaponKills"))
                precision = _as_int(_stat_value(values, "uniqueWeaponPrecisionKills"))
                if kills is None:
                    continue
                row = totals.setdefault(
                    item_hash, {"item_hash": item_hash, "kills": 0, "precision_kills": 0,
                                "matches_with_kills": 0, "characters": set()}
                )
                row["kills"] += kills
                row["precision_kills"] += precision or 0
                if kills > 0:
                    row["matches_with_kills"] += 1
                row["characters"].add(match["class"])

        if not analyzed:
            raise APIError(
                "查询 PvP 武器榜",
                f"{mode_label} 的比赛结算一场都没取到（{len(failed)} 场失败）。"
                "这是上游的问题，稍后重试。",
            )

        total_kills = sum(row["kills"] for row in totals.values())
        weapons = []
        unresolved = 0
        for item_hash, row in totals.items():
            info = self._manifest.get_item_info(item_hash)
            icon = info.get("icon", "") if isinstance(info, dict) else ""
            icon_url = (
                icon if isinstance(icon, str) and icon.startswith(("http://", "https://"))
                else f"https://www.bungie.net{icon}"
                if isinstance(icon, str) and icon.startswith("/")
                else ""
            )
            name = self._manifest.get_item_name(item_hash)
            if not name or name.startswith("#"):
                unresolved += 1
            kills = row["kills"]
            weapons.append({
                "item_hash": item_hash,
                "name": name,
                "icon_url": icon_url,
                "kills": kills,
                "precision_kills": row["precision_kills"],
                "precision_rate": round(row["precision_kills"] / kills, 4) if kills else None,
                "matches_with_kills": row["matches_with_kills"],
                "kill_share": round(kills / total_kills, 4) if total_kills else None,
                "kills_per_match": round(kills / len(analyzed), 2),
                "characters": sorted(row["characters"]),
            })
        weapons.sort(key=lambda item: (-item["kills"], item["name"]))

        periods = [match["period"] for match in analyzed if match["period"]]
        window = {
            "newest": max(periods) if periods else "",
            "oldest": min(periods) if periods else "",
            "matches_requested": wanted,
            "matches_analyzed": len(analyzed),
            "matches_failed": len(failed),
        }
        tally = [
            {
                "mode": mode_id,
                "name": self._manifest.get_activity_mode_name(mode_id) or f"模式{mode_id}",
                "matches": count,
            }
            for mode_id, count in sorted(mode_tally.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        labels_all = {
            word: self._manifest.get_activity_mode_name(activity_modes.MODE_TYPE[word])
            for word in activity_modes.MODES
        }
        warnings.append(
            "这是**最近 N 场**的 PvP 武器击杀，不是生涯累计："
            f"本次分析 {len(analyzed)} 场，时间窗 {window['oldest'][:10]} → {window['newest'][:10]}。"
        )
        if len(analyzed) < len(ordered):
            warnings.append(
                f"有 {len(ordered) - len(analyzed)} 场结算没取到（上游失败），"
                "榜单只统计取到的场次。"
            )
        if history_failures:
            warnings.append("有角色的历史没读成，已跳过：" + "；".join(history_failures))
        if window["oldest"]:
            days = self._days_span(window["oldest"], window["newest"])
            if days is not None and days > 60:
                warnings.append(
                    f"时间窗跨度 {days} 天：这是\"最近打过的 {len(analyzed)} 场\"，"
                    "不代表最近几周的状态。"
                )
        if unresolved:
            warnings.append(f"有 {unresolved} 把武器在 Manifest 里查不到名字，按 #hash 列出。")
        if character is None and len(per_character) > 1:
            warnings.append("账号级榜单按\"所有角色最近 N 场\"合并；要单角色请传 character=。")

        return {
            "scope": "pvp_recent",
            "source": "pgcr_aggregation",
            "message": (
                f"已分析最近 {len(analyzed)} 场{mode_label}的结算，"
                f"统计你自己的武器击杀（{window['oldest'][:10]} → {window['newest'][:10]}）。"
            ),
            "mode_group": {
                "key": key,
                "label": mode_label,
                "mode_type": mode_type,
                "category": activity_modes.MODES[key]["category"],
                "is_pvp_only": activity_modes.is_pvp(key),
                "available_modes": labels_all,
            },
            "window": window,
            "mode_tally": tally,
            "characters": per_character,
            "weapon_count": len(weapons),
            "total_weapon_kills": total_kills,
            "weapons": weapons,
            "failed_matches": failed[:10],
            "warnings": warnings,
        }

    @staticmethod
    def _days_span(oldest: str, newest: str) -> int | None:
        """时间窗天数；解析不出来给 `None`（不编一个数字）。"""
        def parse(value: str) -> datetime | None:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

        start, end = parse(oldest), parse(newest)
        if start is None or end is None:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return abs((end - start).days)
