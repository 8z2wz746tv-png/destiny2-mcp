"""周常轮换：把「官方给的那半」和「自维护表那半」拼成一份可读的轮换表。

两半的来源、口径与实测证据见 `docs/plans/ROTATION_PLAN.md`：

- **官方**：本周特色突袭/地牢 = `/Destiny2/Milestones/`（带官方起止时间）；
  本周夜幕/宗师 = profile 组件 204 的 `characterActivities.availableActivities[]`
  （每条带 `modifierHashes` 与 `visibleRewards`，三个角色实测完全一致）。
- **自维护表**：上维挑战 / 异域任务 / 泉源 / 遗失区域（`data/rotations.py`，带锚点与核对日期）。

三条口径红线（写在这里是为了让读代码的人一眼看到）：

1. `source` 只能取 `official` 或 `schedule`——表算出来的**不许**写成官方数据；
2. 没核对过锚点的轮换（遗失区域）**不猜**：只给候选名单 + 说明怎么核对；
3. 词缀/奖励里出现 `{var:...}` 或数量 0 一律**原样给**，不插值、不当成"掉 0 个"。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..bungie_client import BungieClient
from ..data import rotations as tables
from ..exceptions import APIError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from . import profile_components

logger = get_logger(__name__)

SOURCE_OFFICIAL = "official"
SOURCE_SCHEDULE = "schedule"

# 组件 204 里"夜幕/宗师"那一组的识别：名字带难度后缀（宗师 / 日落: 高级|专家|大师）。
_NIGHTFALL_MARKERS = ("宗师", "日落")
_DIFFICULTIES = ("宗师", "大师", "专家", "高级", "普通", "标准")


def _strike_name(entry_name: str) -> tuple[str, str]:
    """从 `切除: 宗师` / `日落: 大师` 这类名字里拆出「打击名 + 难度」。

    上游只给难度时（名字就是 `日落: 宗师`），打击名给空串——**不编**，由话术说明。
    """
    name = (entry_name or "").strip()
    for difficulty in _DIFFICULTIES:
        for sep in (": ", "："):
            suffix = f"{sep}{difficulty}"
            if name.endswith(suffix):
                return name[: -len(suffix)].strip(), difficulty
    return name, ""


class RotationService:
    """轮换表：官方里程碑 + 组件 204 + 自维护周期表。"""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    # ── 官方那半 ─────────────────────────────────────────────────────
    def _activity_type_name(self, activity_hash: int) -> str:
        definition = self._manifest.get_definition("DestinyActivityDefinition", activity_hash) or {}
        type_hash = definition.get("activityTypeHash")
        if not isinstance(type_hash, int):
            return ""
        type_def = self._manifest.get_definition("DestinyActivityTypeDefinition", type_hash) or {}
        return (type_def.get("displayProperties") or {}).get("name") or ""

    def _activity_name(self, activity_hash: int) -> str:
        definition = self._manifest.get_definition("DestinyActivityDefinition", activity_hash) or {}
        return (definition.get("displayProperties") or {}).get("name") or f"#{activity_hash}"

    def _item_name(self, item_hash: int) -> str:
        definition = self._manifest.get_item_definition(item_hash) or {}
        return (definition.get("displayProperties") or {}).get("name") or f"#{item_hash}"

    async def _featured_rows(self) -> list[dict[str, Any]]:
        """本周特色突袭/地牢：里程碑口径（官方，带起止时间）。"""
        data = await self._bungie.fetch_milestones()
        rows: list[dict[str, Any]] = []
        for raw_hash, milestone in data.items():
            activities = milestone.get("activities") or []
            if not activities:
                continue          # 净化/公会记忆水晶这类没有活动的里程碑不在这里答
            try:
                milestone_hash = int(raw_hash)
            except (TypeError, ValueError):
                continue
            definition = self._manifest.get_definition("DestinyMilestoneDefinition", milestone_hash) or {}
            name = (definition.get("displayProperties") or {}).get("name") or f"#{milestone_hash}"
            types = {
                self._activity_type_name(a.get("activityHash") or 0)
                for a in activities if isinstance(a, dict)
            }
            kind = "raid" if "突袭" in types else "dungeon" if "地牢" in types else "featured_activity"
            rows.append({
                "kind": kind,
                "kind_label": {"raid": "特色突袭", "dungeon": "特色地牢"}.get(kind, "特色活动"),
                "name": name,
                # 活动类型是上游给的（实测这一批全是「突袭」，含 永恒沙漠），照实给，不自己改判
                "activity_type": "、".join(sorted(t for t in types if t)),
                "difficulty": "",
                "week_of": milestone.get("startDate"),
                "until": milestone.get("endDate"),
                "activities": [
                    {"name": self._activity_name(a.get("activityHash") or 0),
                     "activity_hash": to_unsigned(a.get("activityHash") or 0)}
                    for a in activities if isinstance(a, dict)
                ],
                "modifiers": [],
                "rewards": [],
                "source": SOURCE_OFFICIAL,
            })
        return rows

    async def _nightfall_rows(self, player_name: str) -> list[dict[str, Any]]:
        """本周夜幕/宗师：组件 204 的 availableActivities（官方，带词缀与掉落）。"""
        player = await self._resolver.resolve_player(player_name)
        profile = await self._resolver.get_profile(
            player["membership_id"], player["membership_type"], [200, *profile_components.CHARACTER_ACTIVITIES]
        )
        holders = ((profile.get("characterActivities") or {}).get("data")) or {}
        if not isinstance(holders, dict) or not holders:
            raise APIError(
                "查询轮换",
                "Bungie 未返回角色活动组件（204），这次拿不到本周夜幕/宗师；"
                "不能把未返回当成没有轮换活动，请稍后重试。",
            )
        # 三个角色的日落/宗师条目实测完全一致，取任意一个即可。
        activities = next(
            (holder.get("availableActivities") for holder in holders.values()
             if isinstance(holder, dict) and holder.get("availableActivities")),
            None,
        )
        if not activities:
            raise APIError("查询轮换", "角色活动组件里没有可用活动列表，拿不到本周夜幕/宗师。")

        rows: list[dict[str, Any]] = []
        for entry in activities:
            if not isinstance(entry, dict):
                continue
            display = self._manifest.get_definition(
                "DestinyActivityDefinition", entry.get("activityHash") or 0
            ) or {}
            entry_name = (display.get("displayProperties") or {}).get("name") or ""
            if not any(marker in entry_name for marker in _NIGHTFALL_MARKERS):
                continue
            strike, difficulty = _strike_name(entry_name)
            if strike.startswith("日落"):
                strike = ""      # 上游只给了难度，不编打击名
            modifiers = [
                self._modifier_name(value) for value in (entry.get("modifierHashes") or [])
            ]
            rows.append({
                "kind": "nightfall",
                "kind_label": "夜幕/宗师",
                # 上游只给难度时不拿 `日落: 高级` 当名字（那会和 difficulty 重复），留空由话术说明
                "name": strike,
                "strike_known": bool(strike),
                "upstream_name": entry_name,
                "difficulty": difficulty,
                "activity_hash": to_unsigned(entry.get("activityHash") or 0),
                "recommended_light": entry.get("recommendedLight"),
                "completed": entry.get("isCompleted"),
                "modifiers": [m for m in modifiers if m],
                "rewards": self._rewards(entry),
                "source": SOURCE_OFFICIAL,
            })
        return rows

    def _modifier_name(self, modifier_hash: Any) -> str:
        """词缀名：Manifest 里查得到就给名字，空 hash 直接跳过（实测本周宗师第一条是空）。

        名字里若出现 `{var:...}` 一律**原样**给（不插值、不猜数字）。
        """
        if not isinstance(modifier_hash, int):
            return ""
        definition = self._manifest.get_definition("DestinyActivityModifierDefinition", modifier_hash) or {}
        return (definition.get("displayProperties") or {}).get("name") or ""

    def _rewards(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        rewards: list[dict[str, Any]] = []
        for block in entry.get("visibleRewards") or []:
            if not isinstance(block, dict):
                continue
            for reward in block.get("rewardItems") or []:
                quantity = (reward or {}).get("itemQuantity") or {}
                item_hash = quantity.get("itemHash")
                if not isinstance(item_hash, int):
                    continue
                rewards.append({
                    "name": self._item_name(item_hash),
                    "item_hash": to_unsigned(item_hash),
                    # 数量 0 是占位（实测日落武器就是 0），原样给，不当成"掉 0 个"
                    "quantity": quantity.get("quantity"),
                })
        return rewards

    # ── 自维护表那半 ─────────────────────────────────────────────────
    def _schedule_rows(self, now: datetime) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for rotation in tables.WEEKLY_ROTATIONS:
            for week, name in rotation.upcoming(now, weeks=2):
                rows.append({
                    "kind": rotation.key,
                    "kind_label": rotation.label,
                    "name": name,
                    "difficulty": "",
                    "week_of": tables.week_stamp(week),
                    "modifiers": [],
                    "rewards": [],
                    "source": SOURCE_SCHEDULE,
                    "verified_at": rotation.verified_at,
                    "verified_against": rotation.verified_against,
                    "note": rotation.note,
                })
        for day, mode in tables.wellspring_upcoming(now, days=2):
            rows.append({
                "kind": "wellspring",
                "kind_label": "泉源",
                "name": f"泉源：{mode}",
                "difficulty": "",
                "week_of": tables.week_stamp(day),
                "day_of": tables.week_stamp(day),
                "modifiers": [],
                "rewards": [],
                "variants": list(tables.WELLSPRING_DIFFICULTIES),
                "source": SOURCE_SCHEDULE,
                "verified_at": tables.WELLSPRING_VERIFIED_AT,
                "verified_against": tables.WELLSPRING_VERIFIED_AGAINST,
            })
        return rows

    def _tables_block(self) -> list[dict[str, Any]]:
        block = [
            {
                "key": rotation.key,
                "label": rotation.label,
                "candidates": list(rotation.candidates),
                "cycle_weeks": len(rotation.candidates),
                "anchor_week": rotation.anchor_week_start_utc,
                "verified_at": rotation.verified_at,
                "verified_against": rotation.verified_against,
            }
            for rotation in tables.WEEKLY_ROTATIONS
        ]
        block.append({
            "key": "wellspring",
            "label": "泉源",
            "candidates": list(tables.WELLSPRING_MODES),
            "cycle_days": len(tables.WELLSPRING_MODES),
            "anchor_day": tables.WELLSPRING_ANCHOR_DAY_UTC,
            "verified_at": tables.WELLSPRING_VERIFIED_AT,
            "verified_against": tables.WELLSPRING_VERIFIED_AGAINST,
        })
        return block

    @staticmethod
    def _lost_sector_block() -> dict[str, Any]:
        """遗失区域：**专家是常驻列表**（27 个地点，按目的地分组，实测于游戏内截图），
        传说/大师有没有「每日轮换」还没核对过 —— 所以不给"今天是谁"。"""
        return {
            "anchored": tables.LOST_SECTOR_ANCHORED,
            "expert_always_available": True,
            "groups": [{"destination": destination, "locations": list(names)}
                       for destination, names in tables.LOST_SECTOR_GROUPS],
            "candidates": list(tables.LOST_SECTOR_LOCATIONS),
            "total": tables.LOST_SECTOR_TOTAL,
            "verified_at": tables.LOST_SECTOR_VERIFIED_AT,
            "verified_against": tables.LOST_SECTOR_VERIFIED_AGAINST,
            "how_to_anchor": (
                "看一眼游戏里「传说/大师遗失区域」那个入口：是常驻全部，还是每天只放一个？"
                "如果是后者，把今天的地点名报出来即可补锚点（游戏已停更，核对一次长期有效）。"
            ),
        }

    # ── 入口 ─────────────────────────────────────────────────────────
    async def rotations(self, player_name: str = "", *, limit: int = 20) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        rows = await self._featured_rows()
        rows += await self._nightfall_rows(player_name)
        rows += self._schedule_rows(now)
        rows.sort(key=lambda row: (row.get("source") != SOURCE_OFFICIAL, row.get("kind", "")))

        total = len(rows)
        limit = max(1, limit)
        page = rows[:limit]
        counts = {
            "total": total,
            "returned": len(page),
            "official": sum(1 for row in rows if row["source"] == SOURCE_OFFICIAL),
            "schedule": sum(1 for row in rows if row["source"] == SOURCE_SCHEDULE),
        }
        return {
            "rows": page,
            "counts": counts,
            "truncated": total > len(page),
            "week_of": tables.week_stamp(tables.week_start(now)),
            "today": tables.week_stamp(tables.day_start(now)),
            "tables": self._tables_block(),
            "lost_sector": self._lost_sector_block(),
            "read": {"milestones": SOURCE_OFFICIAL, "character_activities": profile_components.describe(
                [200, *profile_components.CHARACTER_ACTIVITIES]
            )},
        }
