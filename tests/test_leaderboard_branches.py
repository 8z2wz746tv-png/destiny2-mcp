"""榜单/聚合分支（`tools/_leaderboard_branches.py`）的搬运等价性守门。

这些分支是从 `assistants.py` 搬出来的纯重构，**不许改变对下游的调用**。
真实风险：搬的时候顺手把 `mode` 写成 `mode or ""`，而客户端只过滤 `None`
（`bungie_client.py` 的 `if value is not None`），于是 URL 上多一个空的 `modes=`。
httpx 实测会把空串发出去（`?maxtop=10&modes=`）—— 这不是等价重构。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from destiny_mcp.player_resolver import CURRENT_OAUTH_PLAYER
from destiny_mcp.tools import _leaderboard_branches as branches
from destiny_mcp.tools.assistants import activity_assistant


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


def _tool_context(activity: AsyncMock) -> object:
    return type("C", (), {
        "request_context": type("R", (), {"lifespan_context": {"activity_svc": activity}}),
    })()


@pytest.mark.asyncio
async def test_tool_call_site_maps_every_argument_to_the_right_parameter() -> None:
    """工具层的**位置传参顺序**必须与分支签名一致。

    CI 只跑 pytest（不跑 mypy/ruff），所以"签名一改、参数错位"没有类型网兜底 ——
    以前 `statid` 收到 `maxtop` 这类错误会一路静默到"排行榜返回空"。
    这条测试把 5 个相邻参数（character/mode/statid/maxtop）的映射钉死。
    """
    activity = AsyncMock()
    activity.get_leaderboards.return_value = {"message": "已读取排行榜。"}

    await activity_assistant(
        intent="leaderboards", character="hunter", mode="allpvp",
        statid="activitiesCleared", maxtop=7, ctx=_tool_context(activity),
    )

    assert activity.get_leaderboards.await_args.args == (
        # 没传 player_name → 用"当前 OAuth 玩家"哨兵（真名在服务层才解析）。
        CURRENT_OAUTH_PLAYER, "hunter", "allpvp", "activitiesCleared", 7,
    )


@pytest.mark.asyncio
async def test_clan_leaderboard_call_site_passes_group_id_and_defaults() -> None:
    activity = AsyncMock()
    activity.get_clan_leaderboards.return_value = {"message": "已读取公会排行榜。"}

    await activity_assistant(
        intent="clan_leaderboards", group_id="4611686018490000000", ctx=_tool_context(activity),
    )

    # maxtop 没传 → 工具层补 10；mode/statid 没传 → 不带上游参数。
    assert activity.get_clan_leaderboards.await_args.args == (
        "4611686018490000000", None, None, 10,
    )


@pytest.mark.asyncio
async def test_aggregate_call_site_passes_count_as_limit() -> None:
    activity = AsyncMock()
    activity.get_aggregate_activity_stats.return_value = {"activities": []}

    await activity_assistant(intent="aggregate", count=5, ctx=_tool_context(activity))

    assert activity.get_aggregate_activity_stats.await_args.args == (CURRENT_OAUTH_PLAYER, None)
    assert activity.get_aggregate_activity_stats.await_args.kwargs == {"limit": 5}
