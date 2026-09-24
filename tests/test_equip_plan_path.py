"""`equip` 的编排分派：计划必须先给、确认之后才写。

这条守门盯的是"**没有 confirmed 时一个字节都不写**"——装备编排的价值就在于
把"先顶下、再装"算清楚给人确认，而不是像以前那样直接把 404/500 抛出去。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from destiny_mcp.tools._equip_branches import equip_branch
from destiny_mcp.tools._responses import action_response as _action_response
from destiny_mcp.models import EquipPlan

STEP = {
    "action": "downgrade",
    "item": "光芒领主胸甲",
    "item_instance_id": "chest-1",
    "from_location": "warlock",
    "to_location": "equipped",
    "why": "先穿一件非异域把星火协议顶下来",
    "replaces": "星火协议",
    "success": True,
}
TARGET_STEP = {
    "action": "equip",
    "item": "逃逸艺术家",
    "item_instance_id": "gaunt-1",
    "from_location": "warlock",
    "to_location": "equipped",
    "why": "目标",
    "replaces": "",
    "success": True,
}


def _plan(status: str = "ready", steps: list[dict] | None = None, **extra) -> EquipPlan:
    return EquipPlan(
        status=status,
        character="warlock",
        character_id="char-1",
        target_item="逃逸艺术家",
        target_item_instance_id="gaunt-1",
        target_slot="gauntlets",
        steps=steps if steps is not None else [STEP, TARGET_STEP],
        **extra,
    )


def _svc(plan: EquipPlan, result: dict | None = None) -> dict:
    transfer = AsyncMock()
    transfer.plan_equip_item = AsyncMock(return_value=plan)
    transfer.execute_equip_plan = AsyncMock(
        return_value=result
        if result is not None
        else {"success": True, "verified": True, "steps_done": []}
    )
    return {"transfer_svc": transfer}


async def _call(svc: dict, confirmed: bool = False) -> dict:
    return await equip_branch(
        svc, "Player#1", "gaunt-1", "warlock", confirmed, _action_response
    )


async def test_without_confirmation_it_plans_and_writes_nothing() -> None:
    svc = _svc(_plan())

    response = await _call(svc, confirmed=False)

    assert response["error"]["code"] == "confirmation_required"
    payload = response["candidates"][0]
    assert [step["action"] for step in payload["steps"]] == ["downgrade", "equip"]
    assert payload["steps"][0]["replaces"] == "星火协议", "确认话术要能说出顶下的是哪件"
    svc["transfer_svc"].execute_equip_plan.assert_not_awaited()


async def test_confirmed_executes_the_plan_in_order() -> None:
    svc = _svc(_plan())

    response = await _call(svc, confirmed=True)

    assert response["ok"] is True
    svc["transfer_svc"].execute_equip_plan.assert_awaited_once()
    assert svc["transfer_svc"].execute_equip_plan.await_args.args[1].steps[0].action == "downgrade"


async def test_blocked_is_reported_with_the_plan_and_nothing_is_written() -> None:
    blocked = _plan(
        status="blocked",
        steps=[],
        blockers=[
            {
                "reason": "exotic_conflict",
                "detail": "背包里没有可用的非异域臂铠",
                "slot": "gauntlets",
                "numbers": {},
                "success": False,
            }
        ],
        message="全身只能一件异域，且背包里没有可用的非异域臂铠",
    )
    svc = _svc(blocked)

    response = await _call(svc, confirmed=True)

    assert response["error"]["code"] == "equip_blocked"
    assert "非异域" in response["error"]["message"]
    svc["transfer_svc"].execute_equip_plan.assert_not_awaited()


async def test_already_equipped_is_a_noop_success() -> None:
    svc = _svc(_plan(status="already_equipped", steps=[], message="现在装的就是它"))

    response = await _call(svc, confirmed=True)

    assert response["ok"] is True
    svc["transfer_svc"].execute_equip_plan.assert_not_awaited()


async def test_execution_failure_keeps_the_stop_point_in_the_envelope() -> None:
    svc = _svc(
        _plan(),
        result={
            "success": False,
            "verified": False,
            "stopped_at": 1,
            "steps_done": [],
            "message": "第 1 步「光芒领主胸甲」没做成：DestinyItemUniqueEquipRestricted；账号现在：chest=星火协议",
        },
    )

    response = await _call(svc, confirmed=True)

    assert response["ok"] is False
    assert response["error"]["code"] == "equip_failed"
    assert "第 1 步" in response["error"]["message"]
    assert response["data"]["result"]["stopped_at"] == 1
