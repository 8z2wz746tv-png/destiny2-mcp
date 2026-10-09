"""确认信封里的"照抄就行的那条调用"（B 件事，唯一出处）。

实测（2026-10-09 全量审计 5407 次调用）：`confirmation_required` **176 次** —— 模型看得懂"要确认"，
却常把参数重写一遍（少传、换 intent），于是白跑一个来回（中位 ~40 秒）。这里把
"同一组参数 + `confirmed=true`"拼成一条**可直接回放**的调用，模型照抄即可。

为什么单独一个模块：`_responses.py` 贴着体量上限，而这段是话术+形状的混合体，跟信封本身分开更好改。
"""

from __future__ import annotations

from typing import Any


def replay_action(tool: str, replay: dict[str, Any] | None) -> list[dict[str, Any]]:
    """要放回执里的回放动作；给不出（没 `tool` 或没 `replay`）时返回空表，不编。"""
    if not tool or replay is None:
        return []
    return [{
        "label": "确认后原样重发这次调用（只补 confirmed=true，参数一个不改）",
        "tool": tool,
        "arguments": {**replay, "confirmed": True},
    }]
