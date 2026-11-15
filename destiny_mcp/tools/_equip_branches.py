"""`equip` 的编排分派：先给计划、确认后才写。

搬出 `assistants.py` 的原因与武器/子职业分支一样：主文件只剩个位数行余量，
而这里有自己的两段式确认语义。

对外形状（两次调用）：

1. 不带 `confirmed` → `confirmation_required`，`candidates[0]` 里带 `steps[]`
   （每步 `action/item/from_location/to_location/why`）与 `blockers[]`，**零写入**；
2. 带 `confirmed=true` → 真执行「先顶下、再装目标」，写完回读核对。

`equip` 因此是**自守卫写入**（见 `assistants.SELF_GUARDED_WRITE_INTENTS`）：
确认信封由这里出，通用确认门槛不再拦它。
"""

from __future__ import annotations

from typing import Any

from ..error_codes import ErrorCode
from ._responses import confirmation_required_response, failure_response, ok_response


async def equip_branch(
    svc: dict[str, Any],
    resolved: str,
    item_instance_id: str,
    character: str,
    confirmed: bool,
    action_response: Any,
) -> dict[str, Any]:
    """`intent="equip"` 的全部行为：预检 → 计划 → （确认后）执行。"""
    plan = await svc["transfer_svc"].plan_equip_item(
        resolved, item_instance_id, character
    )
    payload = plan.model_dump()

    if plan.status == "blocked":
        # 上游铁律挡住（另一个槽的金装、背包满、正装备着、实例不在身上）：
        # 如实说清缺什么与下一步，不是"写入失败"（没写、也不是失败族）。
        # 计划整包进 `data.result`（与其它写入路径同一条读法），`candidates` 只留真候选。
        return failure_response(
            ErrorCode.EQUIP_BLOCKED, plan.message, payload,
            next_actions=[
                "blockers 里每条都是游戏规则（同类异域只能穿一件 / 目标正装备在身上 / "
                "不在该角色身上 / 背包满），按它说的先解决，再重发同一个调用；"
                "这一次没有写入账号。",
            ],
        )

    if plan.status == "already_equipped":
        return ok_response(plan.message, {"result": payload})

    if not confirmed:
        return confirmation_required_response(
            "equip",
            {
                "item": plan.target_item,
                "item_instance_id": plan.target_item_instance_id,
                "steps": payload["steps"],
                "blockers": payload["blockers"],
            },
        )

    result = await svc["transfer_svc"].execute_equip_plan(resolved, plan, character)
    return action_response("equip", "装备编排已执行。", result)
