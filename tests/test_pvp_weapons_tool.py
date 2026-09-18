"""`activity_assistant(intent="pvp_weapons")` 的**工具层**守门：默认值、信封、口径标签。

为什么单独一层：服务层单测只覆盖 `scope/source`，新 intent 的
`count` 默认值（10 场）与 `data.pvp_weapons` 这一层形状当时**完全没有单测** ——
真机语料那行还显式传了 `count=5`，所以"不传 count"这条路径从来没被走过。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from destiny_mcp.tools.assistants import activity_assistant

PAYLOAD = {
    "scope": "pvp_recent",
    "source": "pgcr_aggregation",
    "message": "已分析最近 10 场熔炉竞技场的结算。",
    "mode_group": {"key": "crucible", "label": "熔炉竞技场", "is_pvp_only": True},
    "window": {"matches_requested": 10, "matches_planned": 10, "matches_analyzed": 10},
    "weapons": [{"item_hash": 1, "name": "鹰月", "kills": 28}],
    "warnings": ["这是**最近 N 场**的 PvP 武器击杀，不是生涯累计。"],
}


def _context(service: AsyncMock) -> tuple[dict, object]:
    service.get_pvp_weapon_board.return_value = dict(PAYLOAD)
    svc = {"pvp_weapon_svc": service}
    ctx = type("C", (), {
        "request_context": type("R", (), {"lifespan_context": svc}),
    })()
    return svc, ctx


@pytest.mark.asyncio
async def test_default_count_is_ten_matches() -> None:
    """不传 count 时按 10 场分析 —— 文档（routing.md / 参数说明）承诺的就是这个数。"""
    service = AsyncMock()
    _svc, ctx = _context(service)

    response = await activity_assistant(intent="pvp_weapons", ctx=ctx)

    assert response["ok"] is True
    assert service.get_pvp_weapon_board.await_args.kwargs["matches"] == 10


@pytest.mark.asyncio
async def test_explicit_count_and_mode_are_passed_through() -> None:
    service = AsyncMock()
    _svc, ctx = _context(service)

    await activity_assistant(intent="pvp_weapons", count=30, mode="trials", ctx=ctx)

    kwargs = service.get_pvp_weapon_board.await_args.kwargs
    assert kwargs["matches"] == 30 and kwargs["mode"] == "trials"


@pytest.mark.asyncio
async def test_mode_defaults_to_pvp_when_omitted() -> None:
    service = AsyncMock()
    _svc, ctx = _context(service)

    await activity_assistant(intent="pvp_weapons", ctx=ctx)

    assert service.get_pvp_weapon_board.await_args.kwargs["mode"] == "pvp"


@pytest.mark.asyncio
async def test_envelope_carries_warnings_and_data_holds_the_board() -> None:
    """口径标签进 `data.pvp_weapons`，warning 进信封（data 里不留第二份）。"""
    service = AsyncMock()
    _svc, ctx = _context(service)

    response = await activity_assistant(intent="pvp_weapons", ctx=ctx)

    board = response["data"]["pvp_weapons"]
    assert board["scope"] == "pvp_recent" and board["source"] == "pgcr_aggregation"
    assert "warnings" not in board, "warnings 只该在信封里"
    # `message` 同理：data 里带 message/success 是信封违规（全量语料抓过 5 条）。
    assert "message" not in board, "message 只该做顶层 summary"
    assert response["warnings"] and "不是生涯" in response["warnings"][0]
    assert response["summary"].startswith("已分析最近")


@pytest.mark.asyncio
async def test_weapon_history_alias_still_goes_to_the_all_modes_source() -> None:
    """老口径（全模式）不能被新 intent 顶掉：别名仍走 `GetUniqueWeaponHistory`。"""
    activity = AsyncMock()
    activity.get_unique_weapon_history.return_value = {
        "scope": "all_modes", "message": "已读取武器使用排行。", "weapons": [],
    }
    ctx = type("C", (), {
        "request_context": type("R", (), {"lifespan_context": {"activity_svc": activity}}),
    })()

    response = await activity_assistant(intent="weapon_history", ctx=ctx)

    assert response["data"]["scope"] == "all_modes"
    activity.get_unique_weapon_history.assert_awaited_once()
