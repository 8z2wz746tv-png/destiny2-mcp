"""六维优先级阶梯：无解时不是干说"无解"，而是给差距、上限与台阶。

设计来自实机（docs/plans/ARMOR_FORMAT_PLAN.md P5，用猎人实测的三行对比表）：

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

from ..logging_config import get_logger
from ..vocabulary import STAT_LABELS_ZH as STAT_LABELS  # 单一出处：vocabulary.py

logger = get_logger(__name__)

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
    tuning: dict[str, Any] | None = None,
    tuning_unavailable_reason: str = "",
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
    # "免费的杠杆"：每件护甲一个 ±5 调谐槽（零和）、一个 +10/+5 属性模组槽。
    # P7 之后求解器**真的会试调谐**（services/build_tuning.py）：能靠调谐达标的方案
    # 会直接出现在结果里，轮到这个阶梯就说明"试过了、没成"。所以这里的措辞必须分两种：
    # 有额度数据时给证据（差多少、每项最多能补多少、为什么补不上），
    # 没有额度数据时只能老实说"这是杠杆提示"。
    tuning = tuning or {}
    per_stat_gain = {
        str(stat): int(value)
        for stat, value in (tuning.get("per_stat_max_gain") or {}).items()
    }
    attempted = bool(tuning)
    tuning_first: list[dict[str, Any]] = []
    for stat, gap in sorted(shortfall.items(), key=lambda item: item[1]):
        headroom = per_stat_gain.get(stat, 0)
        # 有额度数据就是"有证据"：per_stat_max_gain 里没有这项 = 这项补不了 0 点以上。
        evidence = attempted
        if evidence and gap <= headroom:
            rung = {
                "stat": stat,
                "stat_label": STAT_LABELS[stat],
                "gap": gap,
                "headroom": headroom,
                "lever": "tuning",
                "why": (
                    f"{STAT_LABELS[stat]}差 {gap} 点，而现有护甲的调谐每项最多能补 {headroom} 点："
                    "求解器已经试过调谐组合，仍然没有合规方案 —— 多半卡在"
                    "「调谐零和，+5 这一项就得 −5 另一项，而那一项让不出来」。"
                    "可以先用 inventory_assistant(intent=\"item\") 看哪几件的调谐是空的/有余量，"
                    "再决定要不要降目标。"
                ),
            }
            if attempted:
                rung["solver_attempted"] = True
        elif not evidence and gap <= 5:
            rung = {
                "stat": stat,
                "stat_label": STAT_LABELS[stat],
                "gap": gap,
                "headroom": headroom,
                "lever": "tuning",
                "why": (
                    f"{STAT_LABELS[stat]}只差 {gap} 点：一件护甲的调谐（+5/−5）就能补上，"
                    "先用 inventory_assistant(intent=\"item\") 看哪件还有调谐余量，"
                    "再决定要不要降目标。"
                ),
            }
        elif evidence and gap <= 5:
            rung = {
                "stat": stat,
                "stat_label": STAT_LABELS[stat],
                "gap": gap,
                "headroom": headroom,
                "lever": "no_tuning",
                "why": (
                    (
                        f"{STAT_LABELS[stat]}只差 {gap} 点，但现有护甲的调谐每项最多只补 "
                        f"{headroom} 点，补不上；只能看属性模组或降目标。"
                    )
                    if headroom
                    else (
                        f"{STAT_LABELS[stat]}只差 {gap} 点，但这批护甲没有可用的调谐槽"
                        "（legacy 护甲没有调谐），靠调谐补不了；只能看属性模组或降目标。"
                    )
                ),
            }
        elif evidence and gap > headroom:
            rung = {
                "stat": stat,
                "stat_label": STAT_LABELS[stat],
                "gap": gap,
                "headroom": headroom,
                "lever": "tuning_insufficient",
                "solver_attempted": True,
                "why": (
                    f"{STAT_LABELS[stat]}差 {gap} 点，而现有护甲的调谐每项最多只能补 "
                    f"{headroom} 点：调谐补不上这一档，要么降目标，要么换更强的护甲。"
                ),
            }
        elif gap <= 10:
            rung = {
                "stat": stat,
                "stat_label": STAT_LABELS[stat],
                "gap": gap,
                "headroom": headroom,
                "lever": "stat_mod",
                "why": (
                    f"{STAT_LABELS[stat]}差 {gap} 点：一件护甲的属性模组（+10 花 3 能量）"
                    "可能就够，先看能量还剩多少再决定要不要降目标。"
                ),
            }
        else:
            rung = None
        if rung is not None:
            tuning_first.append(rung)

    return {
        "targets": targets,
        "priority": priority,
        "ceiling": ceiling,
        "shortfall": shortfall,
        "met": met,
        "precision": precision,
        "reason": reason,
        "samples": list(samples),
        "tuning_first": tuning_first,
        "tuning_first_note": (
            (
                "求解器已经试过调谐（每项最多 +{} 点，零和：+5 一项要 −5 另一项），"
                "上面这些是「试过仍不达标」的项，不是「没试」。"
            ).format(max(per_stat_gain.values(), default=0))
            if tuning_first and attempted
            else (
                "这一档是**人工可用的杠杆提示**：本次没有拿到调谐额度数据，"
                "不能当作已经算进 ceiling 的方案。"
                + (f"（原因：{tuning_unavailable_reason}）" if tuning_unavailable_reason else "")
                if tuning_first
                else ""
            )
        ),
        "tuning_headroom": per_stat_gain,
        "tuning_attempted": attempted,
        "tuning_unavailable_reason": tuning_unavailable_reason or None,
        "suggestion": suggestion,
        "note": (
            "ceiling = 同一套约束下、按某种优先级**同时**能达到的值（实采，逐项取最大）；"
            "它不等于「游戏里最多能到多少」，也不等于单项上限。降级只能由用户确认后执行。"
            if precision == "sampled"
            else "没采到任何一个可行解：可能是约束互斥到没有任何组合能同时满足，"
            "ceiling 留空而不是编 0。"
        ),
    }


async def _tuning_evidence(
    svc: Any,
    player_name: str,
    request: Any,
) -> dict[str, Any] | None:
    """调谐额度证据 → `(证据, 不可用原因)`。

    走的是同一个库存快照接口；失败（角色名不对、组件缺失）不能让无解诊断跟着崩，
    但**必须留下原因**：这张阶梯是结论性输出，降级可以，静默降级不行
    （`tests/test_conclusion_paths.py` 会扫这个文件）。
    """
    inventory = svc.get("inventory_svc")
    manifest = svc.get("manifest")
    if inventory is None or manifest is None:
        return None, "这次调用没拿到库存快照或 Manifest 服务"
    try:
        from ..services.build_tuning import headroom_payload

        snapshot = await inventory.get_armor_snapshot(
            player_name, getattr(request, "character_class", "") or ""
        )
        return headroom_payload(snapshot, manifest), ""
    except Exception as exc:  # noqa: BLE001 - 降级可以，静默不行：原因随载荷返回
        logger.warning("调谐额度证据读取失败（阶梯会退回杠杆提示口径）：%s", exc)
        return None, f"{type(exc).__name__}: {exc}"


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
        probe_error = ""
        for order in sample_orders(relaxed, max_orders=2):
            candidate = relaxed.model_copy(update={"priority_stats": order})
            try:
                results = await svc["build_svc"].find_build(player_name, candidate)
            except Exception as exc:  # noqa: BLE001 - 探测失败要留痕，不能算"试过没成"
                probe_error = f"{type(exc).__name__}: {exc}"
                logger.warning("阶梯探测 %s 失败：%s", probe["label"], exc)
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
            if probe_error:
                # 一次都没探成：**不能**记成"试过没有解"，那会把没测过的档说成否定结论。
                trials.append({
                    "label": probe["label"],
                    "dropped": list(probe["drop"]),
                    "priority": None,
                    "reached": {},
                    "ok": None,
                    "not_probed": True,
                    "reason": probe_error,
                })
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
    tuning_evidence, tuning_unavailable = await _tuning_evidence(svc, player_name, request)
    table = build_ladder(
        request,
        ceiling=ceiling,
        precision=precision,
        reason=reason,
        samples=samples,
        tuning=tuning_evidence,
        tuning_unavailable_reason=tuning_unavailable,
    )
    table["trials"] = trials
    not_probed = [trial["label"] for trial in trials if trial.get("ok") is None]
    if not_probed:
        table["probe_failures"] = not_probed
    # 「原样」那一档实测没解 = 这批护甲不可能同时满足原始目标。
    # 说清楚 ceiling 是**逐项**取最大（来自不同探测），免得读成"其实都能满足"。
    # 这张阶梯**只在工具按原始请求 0 候选时**才会生成，所以 satisfiable 一定是 false；
    # 但 trials 里可能有 ok=true 的档 —— 那是"换了优先级顺序"或"放下目标"之后的结果。
    # 实机踩过：把 trials[0].ok 当结论会说成"其实配得出来"，而工具自己那一次是 0 候选。
    table["verdict"] = {
        "satisfiable": False,
        "evidence": "工具按原始优先级实测 0 候选（这张阶梯就是因此生成的）",
        "note": (
            "ceiling 是各次探测**逐项**取的最大值，不等于同一套护甲能同时达到；"
            "trials 里 ok=true 的档是**换了优先级顺序或放下目标**之后的解，原始请求一个字没改；"
            "ok=null 的档是**这次没探成**（带 reason），不能读成「试过、没有解」。"
        ),
        "solved_after_rotation": bool(trials and trials[0]["ok"]),
    }
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
