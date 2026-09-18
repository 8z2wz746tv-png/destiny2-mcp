"""`activity_assistant` 的榜单与聚合分支（个人榜 / 公会榜 / 活动累计）。

从 `assistants.py` 搬出来：那里的体量上限只降不升（`tests/test_module_size_ratchet.py`），
按域放 `_*_branches.py` 是仓库既定分工。三个 intent 的判据与话术原样搬过来，行为不变。
"""

from __future__ import annotations

from typing import Any

from ._responses import ok_response


# 三个分支的 intent 并集；`count` 只有活动累计读（榜单用 maxtop）。
INTENTS = frozenset({
    "aggregate", "activity_aggregate", "activity_stats",
    "leaderboards", "leaderboard", "clan_leaderboards",
})


async def leaderboard_response(
    svc: dict[str, Any],
    intent: str,
    player_name: str,
    character: str | None,
    mode: str,
    statid: str | None,
    maxtop: int,
    group_id: str,
    count: int,
) -> dict:
    """榜单/聚合三类 intent 的统一入口（玩家名 `resolved` 由工具层解析后传进来）。"""
    if intent in {"aggregate", "activity_aggregate", "activity_stats"}:
        return ok_response(
            "已读取活动聚合统计。",
            await svc["activity_svc"].get_aggregate_activity_stats(
                player_name, character, limit=count
            ),
        )

    if intent in {"leaderboards", "leaderboard"}:
        result = await svc["activity_svc"].get_leaderboards(
            player_name, character, mode, statid, maxtop
        )
        return ok_response(result.get("message", "已读取排行榜。"), result)

    result = await svc["activity_svc"].get_clan_leaderboards(group_id, mode, statid, maxtop)
    return ok_response(result.get("message", "已读取公会排行榜。"), result)
