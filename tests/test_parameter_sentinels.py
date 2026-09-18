"""条数类参数的哨兵规则：`None` 或 **≤0** 都算"没指定" → 该入口的默认值。

为什么单开一个文件：`tools/_param_docs.Limit` 一直承诺"传 0 或负数等于没指定"，
但代码以前只判 `None` —— 传 0 会一路走到服务层的 `max(1, min(...))`，静默变成
"要 1 条/1 场"。这类"文档承诺了、代码没做"的差距没有测试就永远发现不了
（`tests/test_module_size_ratchet.py` 的注释里早就提到过这个文件名，但文件根本不存在）。

`ge=1` 的参数（`top_n`/`slot_number`/`max_replacements`）**不在**这条规则里：
0 与负数由 schema 直接拒收，走的是"失败必须是失败"那条路。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.player_resolver import CURRENT_OAUTH_PLAYER
from destiny_mcp.tools._helpers import positive_or_default
from destiny_mcp.tools.assistants import (
    activity_assistant,
    inventory_assistant,
    loadout_assistant,
    subclass_assistant,
    weapon_assistant,
    world_assistant,
)


def _ctx(svc: dict) -> object:
    return type("C", (), {
        "request_context": type("R", (), {"lifespan_context": svc}),
    })()


@pytest.mark.parametrize("value", [None, 0, -1, -99])
def test_none_and_non_positive_mean_unspecified(value: int | None) -> None:
    assert positive_or_default(value, 7) == 7


@pytest.mark.parametrize("value", [1, 5, 100])
def test_positive_values_pass_through(value: int) -> None:
    assert positive_or_default(value, 7) == value


@pytest.mark.asyncio
async def test_activity_count_zero_means_the_default_not_one() -> None:
    """`count=0` 以前会变成"1 条/1 场"；现在与不传等价（history 默认 20）。"""
    activity = AsyncMock()
    activity.get_activity_history.return_value = []

    await activity_assistant(intent="history", count=0, ctx=_ctx({"activity_svc": activity}))
    assert activity.get_activity_history.await_args.args[3] == 20

    activity.get_activity_history.reset_mock()
    await activity_assistant(intent="history", ctx=_ctx({"activity_svc": activity}))
    assert activity.get_activity_history.await_args.args[3] == 20


@pytest.mark.asyncio
async def test_pvp_weapons_count_zero_means_ten_matches() -> None:
    service = AsyncMock()
    service.get_pvp_weapon_board.return_value = {"message": "ok", "warnings": []}

    await activity_assistant(intent="pvp_weapons", count=0,
                             ctx=_ctx({"pvp_weapon_svc": service}))

    assert service.get_pvp_weapon_board.await_args.kwargs["matches"] == 10


@pytest.mark.asyncio
async def test_leaderboard_maxtop_zero_means_default() -> None:
    activity = AsyncMock()
    activity.get_leaderboards.return_value = {"message": "ok"}

    await activity_assistant(intent="leaderboards", maxtop=0, ctx=_ctx({"activity_svc": activity}))

    assert activity.get_leaderboards.await_args.args[4] == 10


@pytest.mark.asyncio
async def test_world_limit_zero_means_default_but_vendor_keeps_none() -> None:
    """`vendor` 是例外：它按菜单/详情各自取默认，所以传 None 而不是工具层补一个数。"""
    weekly = AsyncMock()
    weekly.summarize_weekly_reset.return_value = {
        "summary": "本周概要", "weekly": {}, "next_actions": [], "warnings": [],
    }

    await world_assistant(intent="weekly", limit=0,
                          ctx=_ctx({"weekly_analysis_svc": weekly}))
    assert weekly.summarize_weekly_reset.await_args.kwargs["limit"] > 0

    weekly.summarize_weekly_reset.reset_mock()
    await world_assistant(intent="weekly", ctx=_ctx({"weekly_analysis_svc": weekly}))
    assert weekly.summarize_weekly_reset.await_args.kwargs["limit"] > 0


@pytest.mark.asyncio
async def test_inventory_and_loadout_page_limits_share_one_rule() -> None:
    """背包与配装原来各写一句 `limit if limit and limit > 0 else …`，现在同一个函数。"""
    inventory = AsyncMock()
    inventory.summarize_inventory.return_value = {
        "summary": "背包概况", "inventory": [], "next_actions": [], "warnings": [],
    }
    await inventory_assistant(intent="summary", limit=0,
                              ctx=_ctx({"inventory_analysis_svc": inventory}))
    assert inventory.summarize_inventory.await_args.kwargs["limit"] > 0

    loadout = AsyncMock()
    loadout.get_loadouts.return_value = {
        "truncated": False, "returned_loadouts": 0, "total_loadouts": 0, "loadouts": [],
        "player_name": "TestGuardian#1234", "next_offset": 0,
        "scope": "account", "loadout_format": "build_template",
    }
    await loadout_assistant(intent="list", limit=0, ctx=_ctx({"loadout_svc": loadout}))
    assert loadout.get_loadouts.await_args.args[2] > 0


@pytest.mark.asyncio
async def test_weapon_and_subclass_limits_are_positive() -> None:
    """武器与子职业也走同一条规则（社区资料那条路读 limit）。"""
    # 社区检索是**同步**接口（`_community_read` 不 await），所以替身用 MagicMock。
    starside = MagicMock()
    starside.search_knowledge.return_value = {"results": [], "total": 0}
    await weapon_assistant(intent="community", limit=0, ctx=_ctx({"starside_svc": starside}))
    assert starside.search_knowledge.call_args.kwargs["limit"] > 0

    starside.search_knowledge.reset_mock()
    await subclass_assistant(intent="community", limit=0, ctx=_ctx({"starside_svc": starside}))
    assert starside.search_knowledge.call_args.kwargs["limit"] > 0


def test_player_name_default_is_still_the_oauth_sentinel() -> None:
    """顺手钉住默认玩家：没传 player_name 时是"当前 OAuth 玩家"哨兵，不是某个写死的名字。"""
    assert CURRENT_OAUTH_PLAYER.startswith("__destiny_")
