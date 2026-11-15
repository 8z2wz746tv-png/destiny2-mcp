"""PGCR 数值解析与时间窗计算（纯函数，从 `pvp_weapon_service` 抽出来）。

抽出来的原因很直白：`pvp_weapon_service` 贴着体积上限（`tests/test_module_size_ratchet.py`），
而这三件事与"榜单逻辑"无关 —— 它们只是"上游给的数字长这样，怎么安全地取出来"：

- `stat_value`：`values.<key>.basic.value` 里的数字，可能是 int/float/字符串；
- `rounded_int`：取整（`None` 保持 `None`，**不编 0**）；
- `days_span`：两个 ISO 时间之间隔多少天，解析不出来给 `None`（也不编）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def stat_value(values: dict[str, Any], *keys: str) -> float | None:
    """从 `values.<key>.basic.value` 取数字；取不到给 `None`（缺值不编 0）。"""
    for key in keys:
        entry = values.get(key)
        basic = entry.get("basic") if isinstance(entry, dict) else None
        if isinstance(basic, dict):
            value = basic.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def rounded_int(value: float | None) -> int | None:
    """四舍五入成 int；`None` 还是 `None`。"""
    return None if value is None else int(round(value))


def days_span(oldest: str, newest: str) -> int | None:
    """时间窗天数；任一端解析不出来给 `None`（不编一个数字）。"""
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
