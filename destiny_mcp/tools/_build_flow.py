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
from ..build.analyzer import narrowing_actions
from ..exceptions import BuildTooLargeError


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


def _not_computed(reason: str, query: dict[str, Any], *, key: str) -> dict[str, Any]:
    """规模超限的统一响应：**没算**就是没算，不能答成"无解"。

    与 `analyze` 的 `precision="not_computed"` 同一套口径：`max_possible` 留空，
    并给可操作的收窄建议。
    """
    analysis = {"reason": reason, "precision": "not_computed", "max_possible": {}}
    payload: dict[str, Any] = {"query": query, "not_computed": analysis}
    if key == "recommendation":
        payload["recommendation"] = {"results": [], "analysis": analysis}
    else:
        payload["builds"] = []
    return ok_response(
        "这次组合规模太大，本次**没有计算**（这不是「配不出来」）。",
        payload,
        next_actions=narrowing_actions(),
        warnings=["没算就是没算：不要把这次结果读成「现有装备配不出」。", reason],
    )


async def recommend(
    svc: Any,
    player_name: str,
    request: Any,
    query: dict[str, Any],
    dump: Callable[[Any], Any],
) -> dict[str, Any]:
    try:
        result = await svc["build_svc"].recommend_build(player_name, request)
    except BuildTooLargeError as exc:
        return _not_computed(str(exc), query, key="recommendation")
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


def _tuning_summary(builds: Any) -> dict[str, Any] | None:
    """结果里有没有"要靠调谐才达标"的方案；没有就返回 None（不占响应）。"""
    if not isinstance(builds, list):
        return None
    rows = [
        build
        for build in builds
        if isinstance(build, dict) and build.get("tuning_changes")
    ]
    if not rows:
        return None
    changes = [change for row in rows for change in row.get("tuning_changes") or []]
    return {
        "build_count": len(rows),
        "change_count": len(changes),
        "changes": changes,
        "note": (
            "这些方案是「按原目标求解器没达标 → 放宽目标复解 → 用真实目标逐套复核」"
            "找出来的；tuning_changes 里是要改的调谐（从什么改成什么、六维怎么变）。"
            "注意：调谐**只能在游戏内手动改**（Bungie 接口实测回 "
            "「This action can only be done in-game.」），"
            "canonical_build 里不含调谐插件 —— 确认装备只换护甲与模组，"
            "调谐这一步要把清单交给玩家。"
        ),
    }


def _empty_message(search: dict[str, Any] | None) -> str:
    """0 候选时说的话**必须自证"搜完了"**。

    否则读的人分不清"枚举完了、真没有满足下限的方案"与"没搜完/被截断"——
    这正是本仓库栽过的坑（`analyze` 曾在没验证过的情况下断言"没有合法组合"）。
    """
    if search is None or search.get("exhaustive"):
        combos = (search or {}).get("combos")
        scope = f"（枚举了 {combos:,} 套组合）" if isinstance(combos, int) else ""
        return f"找到 0 个候选配装：枚举完了，没有任何一套能满足这些下限{scope}。"
    return (
        "这次**没有搜完**，所以不能说「没有满足下限的方案」"
        f"（截断原因：{search.get('truncated_by') or '未说明'}）。"
        "可以收窄请求后重试，或调高搜索预算。"
    )


async def find(
    svc: Any,
    player_name: str,
    request: Any,
    query: dict[str, Any],
    dump: Callable[[Any], Any],
) -> dict[str, Any]:
    diagnostics: list[Any] = []
    try:
        result = await svc["build_svc"].find_build(player_name, request, diagnostics)
    except BuildTooLargeError as exc:
        return _not_computed(str(exc), query, key="builds")
    report = diagnostics[0].to_dict() if diagnostics else None
    search = (
        {key: report[key] for key in ("exhaustive", "combos", "truncated_by")}
        if report
        else None
    )
    builds = with_slot_keys(dump(result))
    tuning = _tuning_summary(builds)
    if not builds:
        ladder = await armor_ladder.no_solution_ladder(
            svc, player_name, request, coverage=search
        )
        return ok_response(
            _empty_message(search),
            {"builds": builds, "query": query, "ladder": ladder, "search": search},
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
    payload: dict[str, Any] = {"builds": builds, "query": query}
    message = f"找到 {len(builds)} 个候选配装。"
    if tuning is not None:
        payload["tuning"] = tuning
        message += (
            f"其中 {tuning['build_count']} 个要先改调谐才能达标"
            "（调谐免费、不占能量；逐件改动见各自的 tuning_changes）。"
        )
    # 有解时也给"每项单独能顶到多少" —— 用户说"不够极限"时，答案就在这几个数里。
    # `reachable_note` 必须一起带上：逐项可达**不等于**同一套能同时达到。
    if report and report.get("reachable"):
        payload["reachable"] = report["reachable"]
        payload["reachable_note"] = report.get("reachable_note")
    violated = [row for row in builds if isinstance(row, dict) and row.get("max_violations")]
    if violated:
        payload["max_violations"] = [
            {"build_index": index, "item": row.get("max_violations")}
            for index, row in enumerate(builds)
            if isinstance(row, dict) and row.get("max_violations")
        ]
        message += (
            f"其中 {len(violated)} 套超过了指定的属性上限（见各自的 max_violations），"
            "已排到没超上限的方案后面。"
        )
    return ok_response(message, payload)


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
