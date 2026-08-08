"""Weekly analysis service — compact summaries of reset milestones."""

from __future__ import annotations

from typing import Any

from ..models import WeeklyMilestone
from .weekly_service import WeeklyService

_CATEGORY_LABELS = {
    "nightfall": "日落",
    "trials": "试炼",
    "raid": "突袭",
    "dungeon": "地牢",
    "crucible": "熔炉",
    "gambit": "智谋",
    "other": "其他",
}
_HIGHLIGHT_ORDER = ["nightfall", "trials", "raid", "dungeon"]


class WeeklyAnalysisService:
    """Build compact structured summaries for weekly reset questions."""

    def __init__(self, weekly_service: WeeklyService) -> None:
        self._weekly_svc = weekly_service

    async def summarize_weekly_reset(self, *, limit: int = 12) -> dict[str, Any]:
        """Summarize current weekly reset milestones."""
        item_limit = max(1, min(int(limit or 12), 25))
        weekly = await self._weekly_svc.get_weekly_reset()
        milestones = weekly.milestones
        highlights = _highlight_milestones(milestones, item_limit)
        categories = _category_counts(milestones)

        warnings: list[str] = []
        if len(milestones) > item_limit:
            warnings.append(
                f"仅返回前 {item_limit} 个重点活动，"
                "完整列表请用 world_assistant(intent=\"weekly_full\")。"
            )

        reset_label = weekly.reset_time or "未知"
        summary = (
            f"本周共 {len(milestones)} 个重置活动，"
            f"重点活动 {len(highlights)} 个，下次重置：{reset_label}。"
        )

        return {
            "summary": summary,
            "weekly": {
                "reset_time": weekly.reset_time,
                "total": len(milestones),
                "categories": categories,
                "highlights": [_compact_milestone(item) for item in highlights],
            },
            "warnings": warnings,
            "next_actions": [
                {
                    "label": "查看完整周常明细",
                    "tool": "world_assistant",
                    "arguments": {"intent": "weekly_full"},
                }
            ],
        }


def _highlight_milestones(
    milestones: list[WeeklyMilestone],
    limit: int,
) -> list[WeeklyMilestone]:
    ordered: list[WeeklyMilestone] = []
    seen_hashes: set[int] = set()

    for category in _HIGHLIGHT_ORDER:
        for milestone in milestones:
            if milestone.category != category:
                continue
            if milestone.milestone_hash in seen_hashes:
                continue
            ordered.append(milestone)
            seen_hashes.add(milestone.milestone_hash)
            if len(ordered) >= limit:
                return ordered

    for milestone in milestones:
        if milestone.milestone_hash in seen_hashes:
            continue
        ordered.append(milestone)
        seen_hashes.add(milestone.milestone_hash)
        if len(ordered) >= limit:
            break

    return ordered


def _category_counts(milestones: list[WeeklyMilestone]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for milestone in milestones:
        category = milestone.category or "other"
        counts[category] = counts.get(category, 0) + 1
    return [
        {
            "category": category,
            "label": _CATEGORY_LABELS.get(category, category),
            "count": count,
        }
        for category, count in sorted(counts.items(), key=lambda kv: (_category_sort(kv[0]), kv[0]))
    ]


def _category_sort(category: str) -> int:
    order = {
        "nightfall": 0,
        "trials": 1,
        "raid": 2,
        "dungeon": 3,
        "crucible": 4,
        "gambit": 5,
        "other": 9,
    }
    return order.get(category, 8)


def _compact_milestone(milestone: WeeklyMilestone) -> dict[str, Any]:
    return {
        "name": milestone.name,
        "description": milestone.description,
        "category": milestone.category or "other",
        "category_label": _CATEGORY_LABELS.get(milestone.category, milestone.category or "其他"),
        "end_date": milestone.end_date,
        "activities": [
            {
                "name": activity.name,
                "activity_type": activity.activity_type,
            }
            for activity in milestone.activities[:5]
        ],
    }
