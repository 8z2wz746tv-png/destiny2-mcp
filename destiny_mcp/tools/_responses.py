"""Structured response helpers for MCP aggregate tools."""

from __future__ import annotations

from typing import Any

from ..error_codes import ErrorCode, write_failed
from ._write_failure_hints import write_failure_hints


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


def failure_response(
    code: str,
    message: str,
    result: Any,
    *,
    candidates: list[dict[str, Any]] | None = None,
    next_actions: list[dict[str, Any] | str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """写入失败（或"这一步做不了"）的统一形状：**详情永远在 `data.result`**。

    真机 2026-09-24 收口：以前同一个"写不成"有三套读法 —— 通用写入路径把整包结果放
    `data.result`、`equip_build` 放 `candidates[0].result`、`equip` 被挡住时把计划放
    `candidates[0]`。调用方得按 intent 记三种找法（`scripts/benchmark_equip_chain.py` 里
    那句"失败时 steps 在 candidates[0].result 里"就是这个坑的化石）。
    `candidates` 从此只放**真候选**：多件同名让你选、可选方案、确认载荷。
    """
    response = error_response(
        code, message, candidates=candidates, next_actions=next_actions, warnings=warnings,
    )
    response["data"] = {"result": dump(result)}
    return response


def action_response(intent: str, summary: str, result: Any) -> dict:
    """写入类服务的统一信封：`success` 归顶层，失败给码 + 原因 + 下一步。

    - 失败走 `failure_response`（详情在 `data.result`）；
    - `candidates` 只在信封里发一份：以前信封与 `data.result` 各发一份，同一个清单读两遍；
    - 「同名多件先选一件」不是失败，走 `disambiguation_response`（写入没发生）；
    - **成功摘要优先用服务层那句 `message`**：它带着"改了什么/核对结果"（如"已装备，回读核对通过"），
      固定话术（"…流程已执行。"）会把真正有用的信息埋在 `data.result` 里，模型常常只念摘要。
    """
    payload = dump(result)
    if payload.get("success") is not True:
        candidates = payload.pop("candidates", None) or []
        if payload.get("needs_disambiguation"):
            response = disambiguation_response(payload, candidates)
            response["data"] = {"result": payload}
            return response
        return failure_response(
            payload.get("code") or write_failed(intent),
            payload.get("message") or f"{intent} 执行失败。",
            payload,
            candidates=candidates,
            next_actions=write_failure_hints(payload),
        )
    return ok_response(str(payload.get("message") or summary), {"result": payload})


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
