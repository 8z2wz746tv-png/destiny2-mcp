"""PvP 武器榜的两张身份表：**按模式**与**按活动**（各带自己的名字与图）。

从 `pvp_weapon_service` 抽出来（那边贴着 434 行上限）：榜单本体算的是"哪把枪杀了多少"，
而这两张表是"这些场次分别是**什么**"—— 题材不同，且它们的行形状要跟响应一起冻结。

两条口径写在这里，改之前先读：

- **模式行**（`mode_rows`）：`mode` 是上游的 `modeType` 数字，不是 Manifest hash；
  名字取 `DestinyActivityModeDefinition`，查不到给 `模式<号>`（不编名字），并把号记进
  第二个返回值 —— 调用方要据此提示"Manifest 可能没就绪"。
- **活动行**（`activity_rows`）：这里才是"带活动身份的出口"。`activity_hash` 与
  `icon_url` **同源**（都从 `activityDetails.referenceId` 来），走活动道
  （`manifest_lookup.get_icon_url(activity_hash=…)`）—— 拿 `modeType` 去查物品道会
  静默给空串，"PvP 场次全没图"而且很难查。
"""

from __future__ import annotations

from typing import Any

from ..manifest import ManifestManager
from ..utils.hash_utils import to_unsigned


def mode_rows(
    manifest: ManifestManager, tally: dict[int, int]
) -> tuple[list[dict[str, Any]], list[int]]:
    """`modeType → 场次` → 行式 + 取不到名字的模式号（调用方拿它出 warning）。"""
    rows: list[dict[str, Any]] = []
    unnamed: list[int] = []
    for mode_id, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
        name = manifest.get_activity_mode_name(mode_id)
        if not name:
            unnamed.append(mode_id)
        rows.append({"mode": mode_id, "name": name or f"模式{mode_id}", "matches": count})
    return rows, unnamed


def activity_rows(
    manifest: ManifestManager, matches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """`analyzed` 的场次 → "打过哪些 PvP 活动"（按场次降序，同数按 hash 升序）。

    `matches` 是本次真的取到结算的那些场（`activity_hash` 由取值那段填好）。
    """
    counts: dict[int, int] = {}
    for match in matches:
        activity_hash = match.get("activity_hash") or 0
        counts[activity_hash] = counts.get(activity_hash, 0) + 1
    rows: list[dict[str, Any]] = []
    for activity_hash, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append({
            "activity_hash": to_unsigned(activity_hash),
            "name": manifest.get_activity_name(activity_hash) if activity_hash else "",
            "icon_url": manifest.get_icon_url(activity_hash=activity_hash),
            "matches": count,
        })
    return rows
