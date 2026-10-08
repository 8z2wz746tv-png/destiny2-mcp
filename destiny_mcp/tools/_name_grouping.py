"""按名字归并：**件数 ≠ 种数**（玩家问"我有哪些手炮"要的是"种"）。

盲测 2026-10-06：`inventory_assistant(intent="type")` 回的是 **117 件** —— 同名多副本逐条列
（月之狂嚎 3 把、备用口粮 5 把…），摘要也只报件数，玩家得自己归并才知道"有哪些"。

纯投影：只读行里的 `name`/`power`/`is_equipped`/`location`，不认识服务层。
"""

from __future__ import annotations

from typing import Any


def group_by_name(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`[{name, count, best_power, equipped, locations}]`，按件数降序、同数按名字。

    `best_power` 只收**整数**光等（缺值给 `None`，不编 0）；`locations` 去重保序。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or row.get("item_name") or "")
        entry = grouped.setdefault(
            name,
            {"name": name, "count": 0, "best_power": None, "equipped": 0, "locations": []},
        )
        entry["count"] += 1
        power = row.get("power")
        if isinstance(power, int) and (entry["best_power"] is None or power > entry["best_power"]):
            entry["best_power"] = power
        if row.get("is_equipped"):
            entry["equipped"] += 1
        where = row.get("location") or ""
        if where and where not in entry["locations"]:
            entry["locations"].append(where)
    return sorted(grouped.values(), key=lambda entry: (-entry["count"], entry["name"]))
