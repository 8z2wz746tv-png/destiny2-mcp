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


def resolve_again_action(svc: Any, player_name: str, exact_build: Any, character: str) -> dict:
    """失败时给一条"**用原来的条件**重新求解"的可回放调用。

    以前这里给的是 `{"intent": "recommend", "character": …}` —— 换了词、还丢掉了金装/套装/属性目标，
    模型照做会求出**另一套配装**（实测 15 次 `stale_inventory_snapshot` 都带着这条）。求解参数在候选里
    存着（`build_candidates.register(search_args=…)`），原样回带；没有就退回最朴素的那条，不编。
    """
    candidate = svc["build_svc"].get_build_candidate(player_name, exact_build.execution_id)
    args = dict(candidate.get("search_args") or {})
    args.setdefault("intent", "find")
    if character and not args.get("character"):
        args["character"] = character
    label = (
        "用原来的条件重新求解（金装/套装/属性目标照旧），再给玩家确认"
        if args.get("exotic_name") or any(k.endswith("_target") for k in args)
        else "重新求解并确认配装"
    )
    return {"label": label, "tool": "build_assistant", "arguments": args}
