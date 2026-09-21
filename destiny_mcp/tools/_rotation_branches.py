"""`world_assistant(intent="rotations")` 的载荷：本周轮换表 + 口径标注 + 缺口说明。

三条话术红线（与服务层同一套，见 `services/rotation_service.py` 的模块注释）：

1. `source=official`（里程碑 / 组件 204）与 `source=schedule`（自维护表）**必须分开说**；
2. 遗失区域顺序没核对过就**不给"今天是谁"**，只给候选与核对办法；
3. `{var:...}` 未插值、奖励数量 0 一律按原文给，不猜。
"""

from __future__ import annotations

from typing import Any

from ._responses import ok_response

_KIND_ORDER_HINT = ("特色", "夜幕", "上维", "异域", "泉源")


def _first(rows: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("kind") == kind), None)


def _featured_names(rows: list[dict[str, Any]], limit: int = 4) -> list[str]:
    names = [row["name"] for row in rows if row.get("kind") in ("raid", "dungeon", "featured_activity")]
    return names[:limit]


def _nightfall_text(row: dict[str, Any]) -> str:
    head = (row["name"] or row.get("upstream_name") or "夜幕/宗师")
    if row.get("strike_known") and row.get("difficulty"):
        head += f"（{row['difficulty']}）"
    elif not row.get("strike_known"):
        head = f"夜幕/宗师（{row.get('difficulty') or '难度未给'}）"
    modifiers = "、".join(row.get("modifiers") or [])[:80]
    rewards = "、".join(
        item["name"] + (f"×{item['quantity']}" if item.get("quantity") else "")
        for item in (row.get("rewards") or [])[:2]
    )
    parts = [head]
    if modifiers:
        parts.append(f"词缀 {modifiers}")
    if rewards:
        parts.append(f"掉 {rewards}")
    if not row.get("strike_known"):
        parts.append("上游只给了难度、没给打击名")
    return "，".join(parts)


def _summary(result: dict[str, Any]) -> str:
    rows = result["rows"]
    parts: list[str] = []
    featured = _featured_names(rows)
    if featured:
        total_featured = sum(1 for row in rows if row.get("kind") in ("raid", "dungeon", "featured_activity"))
        text = "、".join(featured)
        if total_featured > len(featured):
            text += f" 等 {total_featured} 项"
        parts.append("特色突袭/地牢 " + text)
    nightfall = _first(rows, "nightfall")
    if nightfall:
        parts.append("夜幕/宗师 " + _nightfall_text(nightfall))
    for kind, label in (("ascendant_challenge", "上维挑战"), ("exotic_mission", "异域任务")):
        row = _first(rows, kind)
        if row:
            parts.append(f"{label} {row['name']}")
    today = [row for row in rows if row.get("kind") == "wellspring"]
    if today:
        text = "今天泉源 " + today[0]["name"].split("：")[-1]
        if len(today) > 1:
            text += f"（明天 {today[1]['name'].split('：')[-1]}）"
        parts.append(text)
    summary = "；".join(parts) if parts else "这次没读到轮换数据。"
    if not result["lost_sector"]["anchored"]:
        summary += "。遗失区域的顺序表还没核对过，暂不给「今天是谁」"
    return summary + "。"


def rotations_payload(result: dict[str, Any]) -> dict[str, Any]:
    rows = result["rows"]
    warnings = [
        "`source=official` 是官方口径（本周特色突袭/地牢来自里程碑、夜幕/宗师来自角色活动组件 204）；"
        "`source=schedule` 是我们自己维护的周期表（上维挑战 / 异域任务 / 泉源），名字来自游戏内轮换页。",
    ]
    if not result["lost_sector"]["anchored"]:
        warnings.append(
            "遗失区域：顺序表还没在游戏里核对过，所以只给候选名单，不给「今天是谁」；"
            "核对一次（游戏已停更，长期有效）即可补上。"
        )
    text = str(rows)
    if "{var:" in text:
        warnings.append("有些词缀/奖励文案里有未插值的 `{var:...}` 变量，按原文给，不要猜数字。")
    data: dict[str, Any] = {
        "rows": rows,
        "counts": result["counts"],
        "truncated": result["truncated"],
        "week_of": result["week_of"],
        "today": result["today"],
        "tables": result["tables"],
        "lost_sector": result["lost_sector"],
        "read": result["read"],
    }
    next_actions: list[dict[str, Any] | str] = [
        "只给到本周 + 下周（每日类给今天 + 明天）；要更远就自己按 `tables` 里的周期与锚点推。",
    ]
    if not result["lost_sector"]["anchored"]:
        next_actions.append(
            "想让遗失区域也能答「今天是谁」：在游戏里看一眼今天的传说/大师遗失区域，把地点名报出来即可。"
        )
    return ok_response(_summary(result), data, warnings=warnings, next_actions=next_actions)
