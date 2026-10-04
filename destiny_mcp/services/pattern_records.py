"""组件 900（记录）的一条记录 → 状态格子，以及"同一记录号出现多处怎么并"。

从 `pattern_service` 抽出来（那边贴着 488 行上限）：这一段是**不碰账号、不碰 Manifest**
的纯合并规则，与"图样目录/进度/来源"三件事并列，但它自己就是一条可单独讲的判据。
"""

from __future__ import annotations

from typing import Any

from ..utils.hash_utils import to_unsigned


def cell(component: Any) -> dict[str, Any]:
    """一条记录的状态：只取第一个目标的进度与需求（实测模式记录就是这个形状）。"""
    objectives = (component or {}).get("objectives") or []
    first = objectives[0] if objectives and isinstance(objectives[0], dict) else {}
    return {
        "progress": first.get("progress"),
        "need": first.get("completionValue"),
        "state": (component or {}).get("state"),
    }


def candidate_rows(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """名字有歧义时的候选行（`patterns` 出口的 `data.candidates[]`）。

    只留"认出是哪一个"要用的列 + `icon_url`：这几行**进的是响应**，而同一批 `rows`
    在 `_row` 里已经带图 —— 2026-10-05 靠"拿真实响应逆推"才发现投影时把它丢了
    （键清单写成内联元组，静态守门当时只认**命名**的键清单常量）。
    """
    keys = ("name", "weapon_type", "status", "item_hash", "icon_url")
    return [{key: row[key] for key in keys} for row in rows[:limit]]


def merge_cell(state: dict[int, dict], key: Any, component: Any) -> None:
    """把一条记录并进状态表：同一记录号出现在多处（档案级 + 角色级）时取进度更靠前的那个。

    模式解锁是账号级的，所以"角色 A 满了、角色 B 没满"应当算已解锁（实测那 32 条角色级记录
    三个角色数值一致，这条规则只是为了不把话说反）。
    """
    if not isinstance(component, dict):
        return
    try:
        record_hash = to_unsigned(int(key))
    except (TypeError, ValueError):
        return
    candidate = cell(component)
    current = state.get(record_hash)
    if current is None or (candidate.get("progress") or -1) > (current.get("progress") or -1):
        state[record_hash] = candidate
