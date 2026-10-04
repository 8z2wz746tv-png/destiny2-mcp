"""周常轮换里**自维护表**那一半的只读渲染（与官方接口那半分开）。

从 `rotation_service` 抽出来（那边贴着 313 行上限）：这一块不碰账号、不碰 Bungie，
只把 `data/rotations.py` 里的表按响应形状摆出来 —— 与"从官方接口读里程碑"是两条
来源不同的路，`docs/plans/ROTATION_PLAN.md` 里就是这么分的。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..data import rotations as tables


def schedule_rows(now: datetime) -> list[dict[str, Any]]:
    """自维护表那一半的**每一行**（上维挑战 / 异域任务轮换 / 泉源），按表算出来。

    从 `rotation_service._schedule_rows` 搬来（那边贴着 285 行上限）：这一块只读
    `data/rotations.py`，不碰账号、不碰 Bungie，与 `tables_block` 是同一类东西。

    **这一半刻意没有 `icon_url`**：表里只存名字（异域任务连活动 hash 都不存，
    理由见 `data/rotations.py` 里那段"挑错比不给更糟"），而名字→活动定义这一跳
    实测对不上（上维挑战 6 个候选 0 命中；异域任务 7 个候选 4 个 0 命中）——
    不给图是"给不出"，不是漏了。要给图得先有一份可核的 hash 表。
    """
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
                "source": "schedule",
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
            "source": "schedule",
            "verified_at": tables.WELLSPRING_VERIFIED_AT,
            "verified_against": tables.WELLSPRING_VERIFIED_AGAINST,
        })
    return rows


def lost_sector_block() -> dict[str, Any]:
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


def tables_block() -> list[dict[str, Any]]:
    """周期表**本身**（候选名单、锚点、核对日期）：让调用方能自己核对轮换是怎么算的。

    与 `lost_sector_block` 同一类 —— 只读 `data/rotations.py`，不碰账号、不碰 Bungie，
    所以它搬来这里而不是留在服务里（服务那边贴着上限，而这一块与"取数"无关）。
    """
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
