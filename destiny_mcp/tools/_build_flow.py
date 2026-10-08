"""配装求解三条只读分支：recommend / find / analyze。

搬出 `assistants.py` 的原因和武器、护甲分支一样：那边有 1407 行的体积上限。
顺带在这里接上"无解时的六维阶梯"（`_armor_ladder`），以及"没给硬目标时
`completion_rate` 不该显示 0.0"这个口径修正。
"""

from __future__ import annotations

from typing import Any, Callable

from ._preparation_note import preparation_note
from . import _armor_ladder as armor_ladder
from ._armor_branches import with_slot_keys
from ._responses import ok_response
from ..build.analyzer import narrowing_actions
from ..exceptions import BuildTooLargeError
from ..services.build_projection import (
    ROWS_NOTE,
    candidate_rows,
    row_hint,
    tuning_summary,
)


def _functional_mods_payload(payload: Any) -> dict[str, Any] | None:
    """回执里的照抄清单：服务层已经挂在每套候选上（`functional_mods`），这里只取一份。

    挂的是**清单**不是可执行计划：可执行的那份在服务端签发的候选里（`items[].functional_mod_groups`）。
    """
    rows = payload if isinstance(payload, list) else (payload or {}).get("results")
    for build in rows or []:
        if isinstance(build, dict) and build.get("functional_mods"):
            return build["functional_mods"]
    return None


def summary_with_functional_mods(message: str, payload: dict[str, Any] | None) -> str:
    """摘要里点一句"带上了社区作者的功能模组"，并说清没抄上的几颗。"""
    if not payload:
        return message
    copied = len(payload.get("mods") or [])
    if not copied:
        return message
    text = f"{message}（含照抄社区配装的 {copied} 颗功能模组，已从六维求解里扣掉它们的能量）"
    unresolved = payload.get("unresolved") or []
    if unresolved:
        names = "、".join(str(row.get("entry")) for row in unresolved)
        text += f"；{len(unresolved)} 颗没抄上：{names}"
    return text


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
    functional_mods: Any = None,
) -> dict[str, Any]:
    try:
        result = await svc["build_svc"].recommend_build(
            player_name, request, functional_mods=functional_mods
        )
    except BuildTooLargeError as exc:
        return _not_computed(str(exc), query, key="recommendation")
    recommendation = with_slot_keys(dump(result))
    copied_mods = _functional_mods_payload(recommendation)
    recommendation["results"] = candidate_rows(recommendation.get("results"))
    rows = recommendation["results"]
    if not _has_hard_targets(request):
        _mark_completion_rate_na(recommendation)
    if not recommendation.get("results"):
        ladder = await armor_ladder.no_solution_ladder(
            svc, player_name, request, base_order_empty=True
        )
        # 有执行前提挡路时，**不能**把 0 候选念成"属性配不出来"：那会把下一步指到
        # "降目标/反推待刷"上，而真正要做的是腾一格或先顶下冲突的金装。
        blocked_by = (recommendation.get("analysis") or {}).get("blocked_by") or []
        return ok_response(
            (
                "没有**能装上**的候选：执行前提先把候选砍掉了 —— " + "；".join(blocked_by)
                if blocked_by
                else "真实库存中没有满足原始硬约束的配装；金装和全部属性目标都保持不变。"
            ),
            {"recommendation": recommendation, "query": query, "ladder": ladder},
            next_actions=(
                [
                    "这几条是**执行前提**，不是属性不够：先按 analysis.reason 里的出路做"
                    "（腾一格 / 用 equip 先把冲突的金装顶下来），再原参数重新求解。"
                ]
                if blocked_by
                else [
                    "如果玩家想知道如何达标，保留本次全部参数调用 "
                    "build_assistant(intent='farm_target', max_replacements=2)；"
                    "只有待刷反推也无解时才询问是否调整硬约束。",
                    "ladder 里的 suggestion 是「建议降哪一项」，要用户同意后才带着新参数重试。",
                ]
            ),
            warnings=(
                list(blocked_by)
                if blocked_by
                else [
                    "原始硬约束未改变。无解时不能自动降低属性目标、替换指定金装或去掉碎片设置。"
                ]
            ),
        )
    return ok_response(
        summary_with_functional_mods(
            f"已生成 {len(rows)} 套配装推荐（每套只给行；细节与装备走 execution_id）。", copied_mods
        ),
        {"recommendation": recommendation, "query": query, "builds_note": ROWS_NOTE},
        next_actions=[row_hint(rows[0])] if rows else [],
    )



def _empty_message(search: dict[str, Any] | None) -> str:
    """0 候选时说的话**必须自证"搜完了"**，并且分清"配不出来"与"装不上"。

    读的人要能分清三件事：枚举完了真没有满足下限的方案、没搜完/被截断、以及
    **执行前提把候选砍掉了**（格子满搬不进来 / 与当前金装冲突）。第三种最要紧：
    真机实测那两次都是"求解成功、写入在动第一颗模组之前整批回滚"，用户看到的
    "0 候选"如果被念成"属性配不出来"，下一步就全错了。
    """
    blockers = (search or {}).get("blockers") or []
    if blockers:
        return (
            "找到 0 个**能装上**的候选配装：执行前提先把候选砍掉了 —— "
            + "；".join(blockers)
        )
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
    functional_mods: Any = None,
) -> dict[str, Any]:
    diagnostics: list[Any] = []
    try:
        result = await svc["build_svc"].find_build(
            player_name, request, diagnostics, functional_mods=functional_mods
        )
    except BuildTooLargeError as exc:
        return _not_computed(str(exc), query, key="builds")
    report = diagnostics[0].to_dict() if diagnostics else None
    search = (
        {key: report[key] for key in ("exhaustive", "combos", "truncated_by", "blockers")
         if key in report}
        if report
        else None
    )
    dumped = with_slot_keys(dump(result))
    copied_mods = _functional_mods_payload(dumped)
    tuning = tuning_summary(dumped)
    builds = candidate_rows(dumped)
    if not builds:
        ladder = await armor_ladder.no_solution_ladder(
            svc, player_name, request, coverage=search, base_order_empty=True
        )
        blocked = (search or {}).get("blockers") or []
        return ok_response(
            _empty_message(search),
            {"builds": builds, "query": query, "ladder": ladder, "search": search},
            next_actions=(
                [
                    "这几条是**执行前提**，不是属性不够：先按上面每一条里的出路做"
                    "（腾一格 / 用 equip 先把冲突的金装顶下来），再原参数重新求解。",
                ]
                if blocked
                else [
                    "ladder 给了差距（shortfall）、当前能到的上限（ceiling）与建议降哪一项；"
                    "降级要用户同意后再重试。",
                ]
            ),
            warnings=blocked,
        )
    if not _has_hard_targets(request):
        for build in builds:
            if isinstance(build, dict):
                build["completion_rate"] = None
                build.setdefault(
                    "completion_rate_note",
                    "没有硬目标时这个比例没有意义（不是 0%）。",
                )
    payload: dict[str, Any] = {"builds": builds, "query": query, "builds_note": ROWS_NOTE}
    if copied_mods is not None:
        payload["functional_mods"] = copied_mods
    message = summary_with_functional_mods(
        f"找到 {len(builds)} 个候选配装。", copied_mods
    )
    if tuning is not None:
        payload["tuning"] = tuning
        message += (
            f"其中 {tuning['build_count']} 个要先改调谐才能达标"
            "（调谐不占能量、不影响模组；逐件改动见各自的 tuning_changes）。"
        )
    # 有解时也给"每项单独能顶到多少"（用户说"不够极限"时答案就在这几个数里）；
    # `reachable_note` 必须一起带上：逐项可达**不等于**同一套能同时达到。
    # "没量过就不给"的规则在形状工厂里（`SearchDiagnostics.to_dict`：全 0 视为没量过）。
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
    prep_note, prep_warnings = preparation_note(builds)
    return ok_response(message + prep_note, payload, next_actions=[row_hint(builds[0])] if builds else [], warnings=prep_warnings)


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
