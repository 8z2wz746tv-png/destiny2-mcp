"""`activity_assistant` 的武器使用榜分支：两个来源，各自自报家门。

- `weapon_history`（别名 `weapons` / `weapon_usage` / `weapon_leaderboard`）：
  上游 `GetUniqueWeaponHistory`，**没有模式参数** → `scope="all_modes"`（全模式 PvE+PvP 合计，
  榜首多半是刷本枪）。这是老口径，行为不变。
- `pvp_weapons`：上游给不了"纯 PvP 的武器榜"，只能逐场 PGCR 聚合最近 N 场 →
  `scope="pvp_recent"`、`source="pgcr_aggregation"`，**必须带时间窗**。

两个来源放在一个分支模块里，是因为它们回答的是同一个问题（"我的武器榜"），
差别只在口径；调用方看 `scope`/`source` 就知道自己拿到的是哪一种。
"""

from __future__ import annotations

from typing import Any

from ._responses import ok_response


# 两组 intent 的并集：`pvp_weapons` 是新口径，其余四个词是老口径（含中文别名之外的英文近义）。
INTENTS = frozenset({
    "weapon_history", "weapons", "weapon_usage", "weapon_leaderboard", "pvp_weapons",
})


async def weapon_usage_response(
    svc: dict[str, Any],
    intent: str,
    player_name: str,
    character: str | None,
    mode: str,
    count: int,
) -> dict:
    """武器使用榜的统一入口（`count` 对 PvP 榜表示"分析多少场"）。"""
    if intent == "pvp_weapons":
        result = await svc["pvp_weapon_svc"].get_pvp_weapon_board(
            player_name, character=character, mode=mode or "pvp", matches=count
        )
        # warnings 进信封（项目惯例）：data 里不再留一份，免得两处各说各话。
        warnings = list(result.get("warnings") or [])
        data = {key: value for key, value in result.items() if key != "warnings"}
        return ok_response(result.get("message", "已读取 PvP 武器榜。"), {"pvp_weapons": data},
                           warnings=warnings)

    result = await svc["activity_svc"].get_unique_weapon_history(
        player_name, character, limit=count
    )
    return ok_response(result.get("message", "已读取武器历史。"), result)
