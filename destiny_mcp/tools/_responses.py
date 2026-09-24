"""Structured response helpers for MCP aggregate tools."""

from __future__ import annotations

from typing import Any

from ..error_codes import ErrorCode, write_failed


def dump(value: Any) -> Any:
    """Pydantic/领域对象 → 纯 JSON 数据（递归）。

    从 `_helpers` 搬来：它是"把领域结果变成响应"这一步的一半，和信封住一起；
    `_helpers` 反过来依赖本模块的 `error_response`，留在那边就得从上层 import（会成环）。
    """
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    return value


def action_response(intent: str, summary: str, result: Any) -> dict:
    """写入类服务的统一信封：`success` 归顶层，失败给码 + 原因 + 下一步。

    `candidates` 只在信封里发一份：以前信封与 `data.result` 各发一份，同一个清单读两遍。
    「同名多件先选一件」不是失败，走 `disambiguation_response`（写入没发生）。
    """
    payload = dump(result)
    if payload.get("success") is not True:
        candidates = payload.pop("candidates", None) or []
        if payload.get("needs_disambiguation"):
            response = disambiguation_response(payload, candidates)
        else:
            response = error_response(
                payload.get("code") or write_failed(intent),
                payload.get("message") or f"{intent} 执行失败。",
                candidates=candidates,
                next_actions=write_failure_hints(payload),
            )
        response["data"] = {"result": payload}
        return response
    return ok_response(summary, {"result": payload})


def ok_response(
    summary: str,
    data: dict[str, Any] | None = None,
    *,
    candidates: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any] | str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the success shape used by new aggregate MCP tools."""
    return {
        "ok": True,
        "summary": summary,
        "data": data or {},
        "candidates": candidates or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
    }


def error_response(
    code: str,
    message: str,
    *,
    recoverable: bool = True,
    candidates: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any] | str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the error shape used by new aggregate MCP tools."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "recoverable": recoverable,
        },
        "candidates": candidates or [],
        "next_actions": next_actions or [],
        "warnings": warnings or [],
    }


def confirmation_required_response(
    intent: str,
    payload: dict[str, Any],
    *,
    next_actions: list[dict[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Return the standard two-step confirmation response for account writes.

    确认信封也要能自证"下一步做什么"：以前 `next_actions` 恒为空，模型只能自己想到
    「把 candidates 给玩家看、他同意后再传 confirmed=true」；这里给一条统一话术，
    具体意图（保存/装备/搬东西）再各自补自己的那条。
    """
    return error_response(
        ErrorCode.CONFIRMATION_REQUIRED,
        f"{intent} 会修改账号状态。请确认后用 confirmed=true 重新调用。",
        recoverable=True,
        candidates=[payload],
        next_actions=[
            "把 candidates[0] 里这次要改的东西说给玩家听（中文、别念字段名），"
            "得到明确同意后用同一组参数加 confirmed=true 重发；在那之前账号不会被改动。",
            *(next_actions or []),
        ],
    )


def disambiguation_response(
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """「同名多件，先选一件」：不是失败，也不该给写入失败码。

    真机 2026-09-24：`move` 撞上 5 件同名护甲时回的是 `move_failed` + `success:false`，
    调用方会当成"搬失败了"去重试同一个调用；实际是**还没写**，缺的是玩家选哪一件。
    `question` 留在 `data.result` 里原样展示，候选清单只在信封里发一份（以前信封与
    `data.result` 各发一份，同一个列表读两遍）。
    """
    item_name = str(payload.get("item_name") or "").strip()
    return error_response(
        ErrorCode.ITEM_DISAMBIGUATION_REQUIRED,
        str(payload.get("message") or f"「{item_name}」有多个副本，需要先选一件。"),
        recoverable=True,
        candidates=candidates,
        next_actions=[
            "把 data.result.question 原样展示给玩家（编号已经列好），"
            "等他回编号后带该候选的 item_instance_id 重发同一个 intent；"
            "在这次调用返回前账号没有被改动。",
        ],
    )


# 写入失败时，游戏给的原因往往配得上一句「那就这么做」。这里按原因的关键词补
# next_actions —— 以前失败分支只有 candidates（多数是空的），调用方拿不到下一步，
# 只能自己想到「先换下来再搬」。
_WRITE_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("UniqueEquipRestricted", "只能装备一件", "一件异域"),
        "同类的异域只能穿一件（异域**武器**一件 + 异域**护甲**一件，两类互不冲突）：目标与已经"
        "穿着的那件同属一类，先从该角色背包里挑一件**非异域的同部位**装备穿上去顶下它，再装目标"
        '（`inventory_assistant` 的 `intent="get"`：武器用 `item_type="武器"`，护甲用 `armor_slot=…`）。',
    ),
    (
        ("equipped item", "CannotPerformActionOnEquippedItem", "已装备"),
        "目标正装备在身上：先用 intent=\"equip\" 把同槽位的另一件换上（或 equip 到别的角色），再对它执行 transfer/move。",
    ),
    (
        ("not found in the character's inventory", "ItemNotFound", "不在该角色身上"),
        "`EquipItem` 只接受**在该角色身上**的实例：仓库或别的角色身上的要先搬过来"
        '（`intent="move"`，destination 传角色名；他背包满了会撞 NoRoomInDestination），'
        "或者直接换用他背包里已有的那件。",
    ),
    (
        ("No space", "空间不足", "InventoryFull", "NoRoomInDestination"),
        "目标位置空间不足：先清出位置，或换一个目标角色/仓库。",
    ),
)


def data_only(payload: dict[str, Any]) -> dict[str, Any]:
    """摘掉领域结果里的状态字段：状态归顶层信封（ok/summary），`data` 里不再来一套。"""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"success", "message"}
    }


def missing_weapon_name(intent: str) -> dict[str, Any]:
    """缺 `weapon_name` 的统一信封（武器分支共用，原在 `_weapon_branches`）。"""
    return error_response(
        ErrorCode.MISSING_WEAPON_NAME, f"{intent} 需要提供 weapon_name。"
    )


def write_failure_hints(payload: dict[str, Any]) -> list[str]:
    """从写入失败的 payload 里挑出可用的下一步建议（挑不到就返回空）。"""
    haystack = f"{payload.get('code', '')} {payload.get('message', '')}"
    return [hint for keywords, hint in _WRITE_FAILURE_HINTS if any(k in haystack for k in keywords)]
