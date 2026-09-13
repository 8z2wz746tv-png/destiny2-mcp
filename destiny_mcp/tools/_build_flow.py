"""配装求解三条只读分支：recommend / find / analyze。

搬出 `assistants.py` 的原因和武器、护甲分支一样：那边有 1407 行的体积上限。
顺带在这里接上"无解时的六维阶梯"（`_armor_ladder`），以及"没给硬目标时
`completion_rate` 不该显示 0.0"这个口径修正。
"""

from __future__ import annotations

from typing import Any, Callable

from . import _armor_ladder as armor_ladder
from ._armor_branches import with_slot_keys
from ._responses import ok_response


def _has_hard_targets(request: Any) -> bool:
    return any(
        getattr(request, field, None) is not None
        for field in armor_ladder.TARGET_FIELDS.values()
    )


def _mark_completion_rate_na(payload: dict[str, Any]) -> dict[str, Any]:
    """没给硬目标时 completion_rate 无意义：标 N/A，别让人读成"一个都没满足"。"""
    for result in payload.get("results") or []:
        if isinstance(result, dict) and "completion_rate" in result:
            result["completion_rate"] = None
            result.setdefault(
                "completion_rate_note",
                "没有硬目标时这个比例没有意义（不是 0%）。",
            )
    return payload


async def recommend(
    svc: Any,
    player_name: str,
    request: Any,
    query: dict[str, Any],
    dump: Callable[[Any], Any],
) -> dict[str, Any]:
    result = await svc["build_svc"].recommend_build(player_name, request)
    recommendation = with_slot_keys(dump(result))
    if not _has_hard_targets(request):
        _mark_completion_rate_na(recommendation)
    if not recommendation.get("results"):
        ladder = await armor_ladder.no_solution_ladder(svc, player_name, request)
        return ok_response(
            "真实库存中没有满足原始硬约束的配装；金装和全部属性目标都保持不变。",
            {"recommendation": recommendation, "query": query, "ladder": ladder},
            next_actions=[
                "如果玩家想知道如何达标，保留本次全部参数调用 "
                "build_assistant(intent='farm_target', max_replacements=2)；"
                "只有待刷反推也无解时才询问是否调整硬约束。",
                "ladder 里的 suggestion 是「建议降哪一项」，要用户同意后才带着新参数重试。",
            ],
            warnings=[
                "原始硬约束未改变。无解时不能自动降低属性目标、替换指定金装或去掉碎片设置。"
            ],
        )
    return ok_response(
        "已生成配装推荐。",
        {"recommendation": recommendation, "query": query},
    )


async def find(
    svc: Any,
    player_name: str,
    request: Any,
    query: dict[str, Any],
    dump: Callable[[Any], Any],
) -> dict[str, Any]:
    result = await svc["build_svc"].find_build(player_name, request)
    builds = with_slot_keys(dump(result))
    if not builds:
        ladder = await armor_ladder.no_solution_ladder(svc, player_name, request)
        return ok_response(
            "找到 0 个候选配装。",
            {"builds": builds, "query": query, "ladder": ladder},
            next_actions=[
                "ladder 给了差距（shortfall）、当前能到的上限（ceiling）与建议降哪一项；"
                "降级要用户同意后再重试。",
            ],
        )
    if not _has_hard_targets(request):
        for build in builds:
            if isinstance(build, dict):
                build["completion_rate"] = None
                build.setdefault(
                    "completion_rate_note",
                    "没有硬目标时这个比例没有意义（不是 0%）。",
                )
    return ok_response(
        f"找到 {len(builds)} 个候选配装。",
        {"builds": builds, "query": query},
    )


async def analyze(
    svc: Any,
    player_name: str,
    request: Any,
    query: dict[str, Any],
    dump: Callable[[Any], Any],
) -> dict[str, Any]:
    result = await svc["build_svc"].analyze_build(player_name, request)
    return ok_response(
        "已分析配装约束。",
        {"analysis": dump(result), "query": query},
    )


__all__ = ["analyze", "find", "recommend"]
