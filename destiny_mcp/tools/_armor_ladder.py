"""六维优先级阶梯：无解时不是干说"无解"，而是给差距、上限与台阶。

设计来自实机（ARMOR_FORMAT_PLAN.md P5，用猎人实测的三行对比表）：

- **差距（shortfall）**：哪个属性差多少 —— 用户真正想知道的是"差 44 点近战"，
  而不是"配不出来"；
- **上限（ceiling）**：按当前优先级，每个属性最多能到多少。来源有两个：
  `analyze_build` 的精确推算（`precision="exact"`），或超规模时按几种优先级
  顺序**实采**几个点（Pareto 采样）取每个属性的最大值；
- **台阶（ladder）**：只**提议**降哪一项（10 点一档，从优先级最低且真差得多的开始），
  绝不自动改目标 —— 现有安全规则要求降级必须由用户确认。

求解器输出契约一行没改：这里只是把已有的 `analyze_build` / `recommend_build`
再调用几次，然后把结果摊平成人和模型都能读的一张表。
"""

from __future__ import annotations

from typing import Any, Sequence

# 六维键 → 中文（与 build 侧 priority_stats 的英文键同一套）
STAT_LABELS: dict[str, str] = {
    "weapons": "武器",
    "health": "生命",
    "class_stat": "职业",
    "grenade": "手雷",
    "melee": "近战",
    "super_stat": "超能",
}
STAT_ORDER: tuple[str, ...] = tuple(STAT_LABELS)

TARGET_FIELDS: dict[str, str] = {
    "weapons": "weapons_target",
    "health": "health_target",
    "class_stat": "class_target",
    "grenade": "grenade_target",
    "melee": "melee_target",
    "super_stat": "super_target",
}

_LADDER_STEP = 10


def _targets(request: Any) -> dict[str, int]:
    """请求里的硬目标（只取真的给过的）。"""
    return {
        stat: int(getattr(request, field))
        for stat, field in TARGET_FIELDS.items()
        if getattr(request, field, None) is not None
    }


def _priority_order(request: Any) -> list[str]:
    """当前优先级顺序；没给就按"有硬目标的先、其次按目标从高到低"。"""
    given = [str(stat) for stat in (getattr(request, "priority_stats", None) or []) if str(stat)]
    order = [stat for stat in given if stat in STAT_LABELS]
    targets = _targets(request)
    remaining = sorted(
        (stat for stat in targets if stat not in order),
        key=lambda stat: (-targets[stat], STAT_ORDER.index(stat)),
    )
    order.extend(remaining)
    order.extend(stat for stat in STAT_ORDER if stat not in order)
    return order


def relaxation_probes(request: Any, *, max_probes: int = 4) -> list[dict[str, Any]]:
    """阶梯的探测计划：从"原样"到"只留最高优先的目标"，一级一级放松。

    实机教训：把 近战70 + 手雷70 一起当前提时，光换优先级顺序**每一种都是 0 候选** ——
    因为目标本身互斥。要让用户看到"降哪一档就能穿"，必须真的把目标放下来再解。
    这里只**提议**：探测用的请求是副本，原始请求一个字不改。
    """
    targets = _targets(request)
    priority = _priority_order(request)
    unmet_by_priority = [stat for stat in reversed(priority) if stat in targets]

    probes: list[dict[str, Any]] = [{"label": "原样：当前硬目标 + 当前优先级", "drop": []}]
    for count in (1, 2):
        if len(unmet_by_priority) < count:
            break
        dropped = unmet_by_priority[:count]
        labels = "、".join(STAT_LABELS[stat] for stat in dropped)
        probes.append({
            "label": f"放下优先级最低的 {count} 项目标（{labels}）",
            "drop": dropped,
        })
    if len(unmet_by_priority) > 2:
        keep = [stat for stat in priority if stat in targets and stat not in unmet_by_priority]
        if keep:
            probes.append({
                "label": f"只保留最高优先的目标（{'、'.join(STAT_LABELS[s] for s in keep)}）",
                "drop": [stat for stat in targets if stat not in keep],
            })
    return probes[:max_probes]


def apply_drops(request: Any, drops: Sequence[str]) -> Any:
    """返回一份"去掉这几项目标"的请求副本（不改原对象）。"""
    update = {TARGET_FIELDS[stat]: None for stat in drops if stat in TARGET_FIELDS}
    return request.model_copy(update=update) if update else request


def sample_orders(request: Any, *, max_orders: int = 3) -> list[list[str]]:
    """采几个优先级顺序：模板顺序 + 把靠后的属性提到最前。

    只用于"精确推算不可用"时的兜底（超规模时 `analyze` 不给上限）。
    """
    base = _priority_order(request)
    orders = [base]
    targets = _targets(request)
    for stat in reversed([s for s in base if s in targets]):
        if len(orders) >= max_orders:
            break
        rotated = [stat, *[s for s in base if s != stat]]
        if rotated not in orders:
            orders.append(rotated)
    return orders


def _stats_of(result: Any) -> dict[str, int]:
    """从求解结果里取六维（不同 intent 的字段名不完全一样）。"""
    build = getattr(result, "build", None) or result
    values: dict[str, int] = {}
    for stat in STAT_ORDER:
        for field in (stat, "class_stat" if stat == "class_stat" else stat):
            value = getattr(build, field, None)
            if isinstance(value, int):
                values[stat] = value
                break
    if not values:
        dumped = build.model_dump() if hasattr(build, "model_dump") else {}
        for stat in STAT_ORDER:
            value = dumped.get(stat) or dumped.get("class_stat" if stat == "class_stat" else stat)
            if isinstance(value, int):
                values[stat] = value
    return values


def ceiling_from_samples(samples: Sequence[dict[str, int]]) -> dict[str, int]:
    """把几次采样的六维按属性取最大值 —— 这就是"还能到多少"。"""
    ceiling: dict[str, int] = {}
    for sample in samples:
        for stat, value in sample.items():
            if value > ceiling.get(stat, 0):
                ceiling[stat] = value
    return ceiling


def build_ladder(
    request: Any,
    *,
    ceiling: dict[str, int],
    precision: str,
    reason: str = "",
    samples: Sequence[dict[str, int]] = (),
) -> dict[str, Any]:
    """把上限、差距与台阶摊平成一张表（只提议，不改目标）。"""
    targets = _targets(request)
    priority = _priority_order(request)
    shortfall = {
        stat: targets[stat] - int(ceiling.get(stat, 0))
        for stat in targets
        if stat in ceiling and targets[stat] > int(ceiling.get(stat, 0))
    }
    met = [stat for stat in targets if stat not in shortfall and stat in ceiling]
    # 台阶：从优先级**最低**且确实差得多的那一项开始降，一次 10 点。
    unmet_by_priority = [stat for stat in reversed(priority) if stat in shortfall]
    suggestion: dict[str, Any] | None = None
    if unmet_by_priority:
        drop = unmet_by_priority[0]
        proposed = max(0, targets[drop] - _LADDER_STEP)
        suggestion = {
            "drop": drop,
            "drop_label": STAT_LABELS[drop],
            "from": targets[drop],
            "to": proposed,
            "argument": TARGET_FIELDS[drop],
            "value": proposed,
            "why": (
                f"{STAT_LABELS[drop]}差 {shortfall[drop]} 点，而它在当前优先级里排在最后："
                f"先把 {TARGET_FIELDS[drop]} 从 {targets[drop]} 降到 {proposed} 再解一次。"
                "这一步要用户明确同意，不能自动降。"
            ),
        }
    return {
        "targets": targets,
        "priority": priority,
        "ceiling": ceiling,
        "shortfall": shortfall,
        "met": met,
        "precision": precision,
        "reason": reason,
        "samples": list(samples),
        "suggestion": suggestion,
        "note": (
            "ceiling = 同一套约束下、按某种优先级**同时**能达到的值（实采，逐项取最大）；"
            "它不等于「游戏里最多能到多少」，也不等于单项上限。降级只能由用户确认后执行。"
            if precision == "sampled"
            else "没采到任何一个可行解：可能是约束互斥到没有任何组合能同时满足，"
            "ceiling 留空而不是编 0。"
        ),
    }


async def no_solution_ladder(
    svc: Any,
    player_name: str,
    request: Any,
    *,
    max_probes: int = 4,
) -> dict[str, Any]:
    """无解时算这张表：逐级放松目标再解，把"降哪一档就能穿"摊开。

    两件事要分清（实机踩过）：

    - `analyze_build` 的 `max_possible` 是**单项上限**：把点全堆在这一项上能到多少
      （实测猎人 武器200/职业200/手雷160/近战150/超能180/生命127）。拿它当"同时能达到"
      会得出"你什么都够"的荒唐结论 —— 所以它单独放在 `single_stat_ceiling`。
    - `ceiling` 必须是**同一套约束下、按某种优先级同时能达到**的值，只能靠实采；
      而且当目标是互斥的时候，光换优先级每一种都是 0 候选，得**把目标放下来**再解。

    采样只在无解时发生（有解时一次都不用多跑）。
    """
    analysis = await svc["build_svc"].analyze_build(player_name, request)
    single_stat = {
        str(stat): int(value)
        for stat, value in (getattr(analysis, "max_possible", None) or {}).items()
    }
    reason = str(getattr(analysis, "reason", ""))
    analysis_precision = str(getattr(analysis, "precision", "exact"))

    trials: list[dict[str, Any]] = []
    samples: list[dict[str, int]] = []
    for probe in relaxation_probes(request, max_probes=max_probes):
        relaxed = apply_drops(request, probe["drop"])
        for order in sample_orders(relaxed, max_orders=2):
            candidate = relaxed.model_copy(update={"priority_stats": order})
            try:
                results = await svc["build_svc"].find_build(player_name, candidate)
            except Exception:  # noqa: BLE001 - 探测失败不该把无解诊断也带崩
                continue
            if results:
                stats = _stats_of(results[0])
                samples.append(stats)
                trials.append({
                    "label": probe["label"],
                    "dropped": list(probe["drop"]),
                    "priority": list(order),
                    "reached": stats,
                    "ok": True,
                })
                break
        else:
            trials.append({
                "label": probe["label"],
                "dropped": list(probe["drop"]),
                "priority": None,
                "reached": {},
                "ok": False,
            })

    ceiling = ceiling_from_samples(samples)
    precision = "sampled" if samples else "not_computed"
    table = build_ladder(
        request, ceiling=ceiling, precision=precision, reason=reason, samples=samples
    )
    table["trials"] = trials
    table["single_stat_ceiling"] = single_stat
    table["analysis_precision"] = analysis_precision
    # 台阶优先给"最小改动就能穿"的那一档；全都没解就给 None（不编）。
    workable = next((trial for trial in trials if trial["ok"]), None)
    if workable is not None:
        table["suggestion"] = {
            "drop": workable["dropped"],
            "drop_labels": [STAT_LABELS[stat] for stat in workable["dropped"]],
            "priority": workable["priority"],
            "reached": workable["reached"],
            "why": (
                f"{workable['label']}之后能解出来："
                + "、".join(
                    f"{STAT_LABELS[stat]}={value}"
                    for stat, value in workable["reached"].items()
                    if value
                )
                + "。这一步要用户明确同意，不能自动降。"
            ),
        }
    if single_stat:
        table["single_stat_note"] = (
            "single_stat_ceiling 是「把点全堆在这一项上」的上限，**不是**同时能达到的值；"
            "ceiling 才是同一套约束下按优先级实采出来的。"
        )
    return table


__all__ = [
    "STAT_LABELS",
    "STAT_ORDER",
    "TARGET_FIELDS",
    "build_ladder",
    "ceiling_from_samples",
    "no_solution_ladder",
    "sample_orders",
]
