"""榜单/聚合分支（`tools/_leaderboard_branches.py`）的搬运等价性守门。

这些分支是从 `assistants.py` 搬出来的纯重构，**不许改变对下游的调用**。
真实风险：搬的时候顺手把 `mode` 写成 `mode or ""`，而客户端只过滤 `None`
（`bungie_client.py` 的 `if value is not None`），于是 URL 上多一个空的 `modes=`。
httpx 实测会把空串发出去（`?maxtop=10&modes=`）—— 这不是等价重构。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from destiny_mcp.tools import _leaderboard_branches as branches


def _svc() -> dict:
    activity = AsyncMock()
    activity.get_leaderboards.return_value = {"message": "已读取排行榜。"}
    activity.get_clan_leaderboards.return_value = {"message": "已读取公会排行榜。"}
    activity.get_aggregate_activity_stats.return_value = {"activities": []}
    return {"activity_svc": activity}


@pytest.mark.asyncio
async def test_absent_mode_is_passed_as_none_not_empty_string() -> None:
    """没给 mode 时必须传 `None`（客户端据此省略参数），不能传空串。"""
    svc = _svc()

    await branches.leaderboard_response(
        svc, "leaderboards", "TestGuardian#1234", None, "", None, 10, "", 20
    )
    assert svc["activity_svc"].get_leaderboards.await_args.args[2] is None

    await branches.leaderboard_response(
        svc, "clan_leaderboards", "TestGuardian#1234", None, "", None, 10, "123", 20
    )
    assert svc["activity_svc"].get_clan_leaderboards.await_args.args[1] is None


@pytest.mark.asyncio
async def test_given_mode_is_passed_through_unchanged() -> None:
    svc = _svc()

    await branches.leaderboard_response(
        svc, "leaderboards", "TestGuardian#1234", "hunter", "allpvp", "activitiesCleared", 5, "", 20
    )

    args = svc["activity_svc"].get_leaderboards.await_args.args
    assert args == ("TestGuardian#1234", "hunter", "allpvp", "activitiesCleared", 5)


@pytest.mark.asyncio
async def test_aggregate_uses_count_as_limit() -> None:
    svc = _svc()

    response = await branches.leaderboard_response(
        svc, "activity_stats", "TestGuardian#1234", "titan", "", None, 10, "", 7
    )

    assert response["ok"] is True
    assert svc["activity_svc"].get_aggregate_activity_stats.await_args.kwargs == {"limit": 7}
