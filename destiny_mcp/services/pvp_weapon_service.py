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
from datetime import datetime, timezone
from typing import Any

from ..bungie_client import BungieClient
from ..data import activity_modes
from ..exceptions import APIError, CharacterNotFoundError, InvalidArgumentError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name
from ..player_resolver import PlayerResolver
from .activity_service import _unwrap_bungie_response
from .pgcr_cache import PgcrCache

logger = get_logger(__name__)

# 每角色取一页历史（上游单页上限 250）；默认 10 场、上限 100 场。
HISTORY_PAGE_SIZE = 250
DEFAULT_MATCHES = 10
MAX_MATCHES = 100

# 并发固定 3：实测吞吐的甜点（并发 6 反而降到 0.89 场/秒，像撞上游限流）。
PGCR_CONCURRENCY = 3

# 武器榜只认 **PvP 家族（category=2）+ 智谋**。别放全部模式词进来：真机踩过
# `mode="raid"` 被接受、回包却自称 `scope="pvp_recent"`（392 杀的突袭枪登顶），
# 那就是把 PvE 数据贴上 PvP 标签 —— 比报错糟得多。
BOARD_KEYS: tuple[str, ...] = (*activity_modes.pvp_keys(), "gambit")

# 失败场次的样本上限（全量数字在 `failed_matches.total` 与 `window.matches_failed`）。
FAILED_SAMPLE = 10


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
        # 缓存轮换每进程只做一次：目录几千个文件时 listdir 不贵，但没必要每轮都扫。
        self._pruned = False

    def _mode_labels(self) -> dict[str, str]:
        """本榜认的模式词 → 官方中文名（Manifest，zh 优先）。

        只在 `BOARD_KEYS` 上取：载荷与报错话术都用这一份，不再各算一遍
        （以前成功路径算全部 14 个词、报错路径再算一遍）。
        """
        labels: dict[str, str] = {}
        for word in BOARD_KEYS:
            label = self._manifest.get_activity_mode_name(activity_modes.MODE_TYPE[word])
            labels[word] = label if isinstance(label, str) and label.strip() else word
        return labels

    # ── 取数 ────────────────────────────────────────────────────────────────

    async def _characters(
        self, player_name: str, character: str | None
    ) -> tuple[str, int, list[tuple[str, str]]]:
        """返回 `(membership_id, membership_type, [(char_id, 职业名)])`。"""
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
        return mid, mtype, targets

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
    def _weapons_of(entry: dict) -> list[dict]:
        weapons = (entry.get("extended") or {}).get("weapons") or []
        return [weapon for weapon in weapons if isinstance(weapon, dict)]

    @classmethod
    def _own_weapons(cls, pgcr: dict, char_id: str, membership_id: str) -> list[dict] | None:
        """从 PGCR 里取**自己那一行**的武器；这一场里没有你的行就返回 `None`。

        `extended.weapons` 是按玩家分行的：拿 `entries[0]` 就是拿别人（或队友）的枪。
        先按 `characterId` 精确匹配；上游偶尔不给 `characterId`，那就退一步按
        `player.destinyUserInfo.membershipId` 找（本方法一直这么承诺，实现以前漏了这段，
        结果是"整场击杀静默消失、响应还 ok=true"）。
        两条都找不到返回 `None` —— 调用方必须如实报出来，不许当成"这场没杀到人"。
        """
        entries = [entry for entry in (pgcr.get("entries") or []) if isinstance(entry, dict)]
        for entry in entries:
            if str(entry.get("characterId", "")) == str(char_id):
                return cls._weapons_of(entry)
        for entry in entries:
            user = (entry.get("player") or {}).get("destinyUserInfo") or {}
            if str(user.get("membershipId", "")) == str(membership_id):
                return cls._weapons_of(entry)
        return None

    async def get_pvp_weapon_board(
        self,
        player_name: str,
        character: str | None = None,
        mode: str = "pvp",
        matches: int = DEFAULT_MATCHES,
    ) -> dict[str, Any]:
        """最近 N 场 PvP 的武器击杀榜（**不是生涯**，window 必看）。"""
        labels = self._mode_labels()
        key = activity_modes.resolve(mode or "pvp")
        if key is None or key not in BOARD_KEYS:
            # PvE 模式词在这里必须报错：以前它会被接受并贴上 scope="pvp_recent" 的标签
            # （真机：mode="raid" 把 392 杀的突袭枪列成"PvP 武器榜"）。
            raise InvalidArgumentError(
                f"pvp_weapons 不支持 mode={mode!r}；可取 "
                f"{activity_modes.words_with_labels(labels, BOARD_KEYS)}。"
                "（这是 PvP 榜：要全模式的武器击杀用 intent=\"weapon_history\"）"
            )
        mode_type = activity_modes.MODE_TYPE[key]
        mode_label = labels.get(key) or key

        # `matches` 只钳制"实际分析多少场"，**不改写调用方要的值**：
        # 以前 `window.matches_requested` 报的是钳制后的数字，问 500 场会看到"你要了 100 场"。
        requested = int(matches)
        wanted = max(1, min(requested, MAX_MATCHES))

        if not self._pruned:
            self._pruned = True
            self._cache.prune()
        mid, mtype, targets = await self._characters(player_name, character)

        warnings: list[str] = []
        history_failures: list[str] = []
        candidates: dict[str, dict] = {}
        per_character: list[dict] = []
        # 三个角色**同时**拉（与 `activity_service.get_activity_history` 同一取舍）：
        # 角色之间独立，串行要等 3 次往返。用 return_exceptions 保住原来的降级语义 ——
        # 单个角色失败只丢那个角色，其余照算并写进 warnings。
        fetched = await asyncio.gather(
            *(self._pvp_matches(mid, mtype, char_id, mode_type) for char_id, _ in targets),
            return_exceptions=True,
        )

        for (char_id, class_name), outcome in zip(targets, fetched):
            if isinstance(outcome, BaseException):
                logger.warning("PvP 历史拉取失败 char=%s: %s", char_id, outcome)
                history_failures.append(f"{class_name}：{outcome}")
                continue
            activities = outcome
            per_character.append({
                "character_id": char_id,
                "class": class_name,
                "history_count": len(activities),
                # 字段名就说它知道的事：一页 250 场被填满了。**不叫** `has_more_history`
                # —— 恰好 250 场只说明"可能还有"，不说明"确实还有"（少承诺一点）。
                "page_full": len(activities) >= HISTORY_PAGE_SIZE,
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
        missing_rows = 0
        for match, pgcr, reason in loaded:
            if pgcr is None:
                failed.append({"instance_id": match["instance_id"], "reason": reason})
                continue
            analyzed.append(match)
            mode_tally[match["activity_mode"]] = mode_tally.get(match["activity_mode"], 0) + 1
            own = self._own_weapons(pgcr, match["character_id"], mid)
            if own is None:
                # 这一场有结算、但没有你的行：如实计数，不许当成"这场没杀到人"。
                missing_rows += 1
                continue
            for weapon in own:
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
            # `matches_requested` 是调用方要的值（**原样**），`matches_planned` 才是本次
            # 实际打算分析多少场（`count` 上限 100）。两者不同时会带 warning。
            "matches_requested": requested,
            "matches_planned": wanted,
            "matches_analyzed": len(analyzed),
            "matches_failed": len(failed),
            "matches_without_your_row": missing_rows,
            # 每个角色只取最近这一页历史：窗口是"这一页里最近的 N 场"。
            "history_page_size_per_character": HISTORY_PAGE_SIZE,
        }
        tally = []
        unnamed_modes: list[int] = []
        for mode_id, count in sorted(mode_tally.items(), key=lambda kv: (-kv[1], kv[0])):
            name = self._manifest.get_activity_mode_name(mode_id)
            if not name:
                unnamed_modes.append(mode_id)
            tally.append({"mode": mode_id, "name": name or f"模式{mode_id}", "matches": count})

        warnings.append(
            "这是**最近 N 场**的 PvP 武器击杀，不是生涯累计："
            f"本次分析 {len(analyzed)} 场，时间窗 {window['oldest'][:10]} → {window['newest'][:10]}。"
        )
        if requested != wanted:
            warnings.append(
                f"count={requested} 已按 {wanted} 场分析"
                + ("（上限 100 场）" if requested > MAX_MATCHES else "（最少 1 场）")
                + "；window.matches_requested 保留你要的值，matches_planned 是实际用的。"
            )
        if len(ordered) < wanted:
            warnings.append(
                f"这个模式的可用场次只有 {len(ordered)} 场（你要 {wanted} 场），榜单基于这些场次。"
            )
        if missing_rows:
            warnings.append(
                f"有 {missing_rows} 场结算里找不到你的那一行"
                "（上游既没给 characterId 也没给 membershipId），这些场次没有计入武器击杀。"
            )
        if unnamed_modes:
            warnings.append(
                f"有 {len(unnamed_modes)} 个子模式在 Manifest 里取不到名字（显示为 模式<号>），"
                "模式标签可能不完整；这通常是 Manifest 未就绪，不是没有这个模式。"
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
                "available_modes": labels,
            },
            "window": window,
            "mode_tally": tally,
            "characters": per_character,
            "weapon_count": len(weapons),
            "total_weapon_kills": total_kills,
            "weapons": weapons,
            # 失败的场次详情按仓库惯例自证全量：`len(items)` 不是失败总数，`total` 才是
            # （以前只给 `failed[:10]`，与 `window.matches_failed` 数字对不上也没说明）。
            "failed_matches": {
                "total": len(failed),
                "returned": len(failed[:FAILED_SAMPLE]),
                "truncated": len(failed) > FAILED_SAMPLE,
                "items": failed[:FAILED_SAMPLE],
            },
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
