"""Structured response helpers for MCP aggregate tools."""

from __future__ import annotations

from typing import Any


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
) -> dict[str, Any]:
    """Return the standard two-step confirmation response for account writes."""
    return error_response(
        "confirmation_required",
        f"{intent} 会修改账号状态。请确认后用 confirmed=true 重新调用。",
        recoverable=True,
        candidates=[payload],
    )


# 写入失败时，游戏给的原因往往配得上一句「那就这么做」。这里按原因的关键词补
# next_actions —— 以前失败分支只有 candidates（多数是空的），调用方拿不到下一步，
# 只能自己想到「先换下来再搬」。
_WRITE_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("equipped item", "CannotPerformActionOnEquippedItem", "已装备"),
        "目标正装备在身上：先用 intent=\"equip\" 把同槽位的另一件换上（或 equip 到别的角色），再对它执行 transfer/move。",
    ),
    (
        ("not found in the character's inventory", "ItemNotFound"),
        "先在账号里确认这件物品的实例 ID 与当前位置（inventory_assistant 的 search/get），再重试。",
    ),
    (
        ("No space", "空间不足", "InventoryFull"),
        "目标位置空间不足：先清出位置，或换一个目标角色/仓库。",
    ),
)


def write_failure_hints(payload: dict[str, Any]) -> list[str]:
    """从写入失败的 payload 里挑出可用的下一步建议（挑不到就返回空）。"""
    haystack = f"{payload.get('code', '')} {payload.get('message', '')}"
    return [hint for keywords, hint in _WRITE_FAILURE_HINTS if any(k in haystack for k in keywords)]
