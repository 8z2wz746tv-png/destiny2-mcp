"""调谐补齐：把"差 5 点"从一句提示变成可执行方案。

**为什么是两趟，而不是把调谐塞进求解器循环**

调谐是零和的（+5 一定伴随 −5），把它做成每件护甲的变体塞进 5 层循环，组合数会再乘
几倍（术士本来 2.4 亿），而它真正能改变结论的场景只有"缺口不超过调谐额度"这一小类。
所以这里的做法是：

1. 按**原目标**解一次 —— 与今天完全一样，达标就直接返回，不动结果（基线因此不变）；
2. 只有当没有一套达标时：把每项目标按调谐额度（见 `tuning_allowance`）放宽再解一次，
   然后对两池候选**逐套精确复核** —— 用真实目标重新分配属性模组、逐项比对六维，
   复核通过的才算"调谐救回来"，并附上要改哪几件、从什么改成什么。

代价说清楚：只救得回"放宽后能找到、复核能过"的方案；枚举之外的可能性（比如必须靠调谐
把优先级属性堆更高，而不是补缺口）这一版不做。响应里 `tuning_note` 会写明这套要不要动调谐。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from ..build.constants import STAT_NAMES
from ..build.models import Armor, BuildConstraints
from ..build.process_types import (
    ARTIFICE_STAT_BOOST,
    MAJOR_STAT_BOOST,
    ProcessArmorSet,
    SearchCoverage,
    armor_to_process_item,
)
from ..build.solver import prepare_fixed_set_context, validate_fixed_process_items
from ..build.tuning import (
    armor_with_tuning,
    improve_pool_with_local_tuning,
    PieceTuning,
    TuningChange,
    TuningChoice,
    TuningPlan,
    piece_tuning,
    plan_tuning,
)
from .build_results import set_key

#: 五个护甲部位在快照里的属性名。
SLOT_ATTRS = ("helmets", "gauntlets", "chests", "legs", "class_items")

#: 放宽那一趟要多留多少套。实测（猎人 118 件，武器150+生命103）：
#: 上限 200 时一套可救的都没有，1500 时能救回 16 套 —— 可救的方案按"放宽后的目标"
#: 排名排在 200 名之外，只留 200 就永远看不到它们。
_RELAXED_POOL_SIZE = 1500
#: 复核很贵（实测每套约 0.1 秒），所以池子放大之后只挑最值得的前 K 套去复核。
_VERIFY_TOP_K = 40


def tuning_allowance(snapshot: Any, manifest: Any) -> dict[str, int]:
    """每项最多能靠调谐加多少点（**一套护甲**的口径）。

    算法：每个部位先取"该部位里最能补这一项的那一件"的增益，再把 5 个部位加起来。
    不能把整个仓库的件数加进去 —— 实机踩过：118 件的仓库会算出"每项能补 563 点"，
    拿去跟缺口比就会得出"求解器明明能补却没用"的荒唐结论。

    单件增益可能超过 5：这件现在要是 +武器/−生命值，"换成 +生命值/−武器"就是 +10 生命
    （先撤掉 −5，再加 +5）。所以每项上限是 5 个部位 × 10 = 50。
    """
    per_stat = {name: 0 for name in STAT_NAMES}
    for attr in SLOT_ATTRS:
        best_in_slot = {name: 0 for name in STAT_NAMES}
        for armor in getattr(snapshot, attr, ()) or ():
            piece = piece_tuning(armor, manifest)
            if piece is None:
                continue
            for choice in piece.options:
                delta = piece.delta_of(choice)
                for index, name in enumerate(STAT_NAMES):
                    if delta[index] > best_in_slot[name]:
                        best_in_slot[name] = delta[index]
        for name in STAT_NAMES:
            per_stat[name] += best_in_slot[name]
    return per_stat


def relax_targets(
    constraints: BuildConstraints,
    allowance: dict[str, int],
) -> BuildConstraints | None:
    """按调谐额度放宽目标；没有可放宽的项时返回 None（省掉一次无意义的求解）。"""
    updates: dict[str, int] = {}
    for name in STAT_NAMES:
        field = f"{name}_min"
        current = int(getattr(constraints, field, 0) or 0)
        if current > 0 and allowance.get(name, 0) > 0:
            updates[field] = max(0, current - allowance[name])
    if not updates or all(
        getattr(constraints, field) == value for field, value in updates.items()
    ):
        return None
    return constraints.model_copy(update=updates)


def within_tuning_reach(
    sets: Iterable[ProcessArmorSet],
    constraints: BuildConstraints,
    allowance: dict[str, int],
) -> bool:
    """有没有哪一套"差的量"落在调谐额度之内。

    用来决定要不要花第二次求解：如果连最好的一套都差得比额度多，放宽目标再解一遍
    也救不回来（额度是调谐的物理上限），直接跳过、如实报无解更省时间。
    """
    raw_min = constraints.as_vector()
    bonus_vector = constraints.subclass_and_fragment_vector()
    wanted = [index for index, value in enumerate(raw_min) if value > 0]
    if not wanted:
        return False
    names = list(STAT_NAMES)
    for armor_set in sets:
        final = _final_stats(armor_set, bonus_vector)
        if all(
            raw_min[index] - final[index] <= allowance.get(names[index], 0)
            for index in wanted
        ):
            return True
    return False


def sets_meet_targets(
    sets: Iterable[ProcessArmorSet],
    constraints: BuildConstraints,
) -> bool:
    """有没有哪一套同时满足全部非零目标（含子职业/碎片加成，和响应里的口径一致）。"""
    raw_min = constraints.as_vector()
    bonus_vector = constraints.subclass_and_fragment_vector()
    wanted = [index for index, value in enumerate(raw_min) if value > 0]
    if not wanted:
        return False
    for armor_set in sets:
        final = _final_stats(armor_set, bonus_vector)
        if all(final[index] >= raw_min[index] for index in wanted):
            return True
    return False


def _final_stats(armor_set: ProcessArmorSet, bonus_vector: Sequence[int]) -> list[int]:
    """一套方案的六维 = 护甲（含调谐）+ 属性模组 + 子职业/碎片。"""
    stats = list(armor_set.stats or [0] * len(STAT_NAMES))
    bonus = list(armor_set.bonus_stats or [0] * len(STAT_NAMES))
    return [
        int(stats[index]) + int(bonus[index]) + int(bonus_vector[index])
        for index in range(len(STAT_NAMES))
    ]


def _mod_budget(arms: Sequence[Armor], context: Any) -> int:
    """这套护甲的属性模组最多能补多少点（与求解器里那条乐观上界同一个口径）。

    注意：只有当**真的有模组定义**时这个预算才存在。求解器那条上界是按"每件一个槽"
    算的，快照里没有模组定义时它照样给 50 —— 拿它当调谐的替代预算，就会把
    "只有调谐能补"的方案误判成"模组能补、不用动调谐"（单测里没有模组定义，实测踩过）。
    """
    auto_mod_data = getattr(context, "auto_mod_data", None)
    has_definitions = bool(
        getattr(auto_mod_data, "general_mods", None)
        or getattr(auto_mod_data, "artifice_mods", None)
    )
    if not has_definitions:
        return 0
    num_artifice = sum(1 for armor in arms if getattr(armor, "is_artifice", False))
    general = int(getattr(context.info, "num_available_general_mods", 0) or 0)
    return num_artifice * ARTIFICE_STAT_BOOST + general * MAJOR_STAT_BOOST


@dataclass(slots=True)
class Rescue:
    """一套被调谐救回来的方案。"""

    armor_set: ProcessArmorSet
    plan: TuningPlan


def rescue_sets(
    snapshot: Any,
    constraints: BuildConstraints,
    manifest: Any,
    candidate_sets: Sequence[ProcessArmorSet],
) -> list[Rescue]:
    """对候选逐套试调谐，返回复核达标的那些（附改动清单）。

    复核用 `validate_fixed_process_items`：它会按**真实目标**重新分配属性模组，
    再逐项比对六维 —— 所以"改完调谐反而掉破另一项"的方案不会混进来。
    """
    if not candidate_sets:
        return []
    context = prepare_fixed_set_context(snapshot, constraints)
    priority = [STAT_NAMES[index] for index in context.priority_indices] or list(STAT_NAMES)

    raw_min = constraints.as_vector()
    raw_targets = {
        name: int(raw_min[index])
        for index, name in enumerate(STAT_NAMES)
        if int(raw_min[index]) > 0
    }

    rescues: list[Rescue] = []
    searched_sets = 0
    for armor_set in candidate_sets:
        arms = list(armor_set.armor)
        if len(arms) != 5:
            continue
        pieces = [piece_tuning(armor, manifest) for armor in arms]
        # 只动"反推得出基础六维"的件：反推不出来的件连它现在是什么都算不准，
        # 更不该拿它去规划改法（`movable` 是机器可读的那一份，见 build/tuning.py）。
        tunable = [piece for piece in pieces if piece is not None and piece.movable]
        if not tunable:
            continue

        # 规划基线 = **护甲（含当前调谐）+ 子职业/碎片**，不含属性模组：
        # 模组是复核时才分配的，而求解器在放宽目标下配的模组（比如全堆武器）
        # 不代表真实目标下会这么配。实机踩过：拿"护甲+放宽解的模组"当基线，
        # 再把模组预算加一遍，等于把同一笔模组算两次，方案被判成"已经达标"而跳过。
        armor_only = [
            sum(int(getattr(armor.stats, name, 0) or 0) for armor in arms)
            + int(context.bonus_vector[index])
            for index, name in enumerate(STAT_NAMES)
        ]
        budget = _mod_budget(arms, context)
        # 调谐要补的是"模组之后的残差"，而模组是复核时才分配的 —— 所以规划用
        # **合计口径**：只要剩余缺口合计 ≤ 模组预算，就算可行。精确复核随后裁决。
        solved = False
        plan = plan_tuning(
            tunable, armor_only, raw_targets, priority, total_budget=budget, collect=12
        )
        if plan.feasible:
            # 排序只是启发式：把前几个方案都拿去精确复核，第一个过的才算数。
            for candidate_plan in (plan, *(replace(plan, changes=alt) for alt in plan.alternatives)):
                tuned_arms = (
                    _apply_plan(arms, tunable, candidate_plan)
                    if candidate_plan.changes
                    else list(arms)
                )
                if len(tuned_arms) != 5:
                    continue
                verified = validate_fixed_process_items(
                    [armor_to_process_item(armor) for armor in tuned_arms],
                    context,
                )
                if verified is None or not _meets(verified, context):
                    continue
                rescues.append(
                    Rescue(armor_set=_rebuild(verified, tuned_arms), plan=candidate_plan)
                )
                solved = True
                break
        if solved:
            continue
        # 启发式说不可行、或者排出来的方案一个都没过复核 → 复核驱动的兜底搜索。
        # 注意别在"不可行"那一支直接 continue：实机踩过，正是那些被判不可行的套里
        # 有复核能过的方案。
        if searched_sets >= _VERIFY_SEARCH_MAX_SETS:
            continue
        if not _worth_search(arms, armor_only, raw_targets, budget, tunable):
            continue
        searched_sets += 1
        found = verification_search(arms, tunable, context, raw_targets, priority)
        if found is None:
            continue
        tuned_arms, fallback_plan = found
        verified = validate_fixed_process_items(
            [armor_to_process_item(armor) for armor in tuned_arms], context
        )
        if verified is not None and _meets(verified, context):
            rescues.append(Rescue(armor_set=_rebuild(verified, tuned_arms), plan=fallback_plan))
    return rescues


#: 兜底搜索最多改几件（每步都要过精确复核，代价高，所以只给前几套候选跑）
_VERIFY_SEARCH_MAX_STEPS = 5
#: 最多给几套候选跑兜底（实测：每套最多几百次复核，跑太多会拖成分钟级）
_VERIFY_SEARCH_MAX_SETS = 3
#: 每件护甲在兜底里最多试几个调谐（按"补缺口 + 让步代价"排序后的前 K 个）
_VERIFY_SEARCH_OPTIONS_PER_PIECE = 4
#: 一次 rescue_sets 里最多做多少次精确复核（兜底搜索的总预算）
_VERIFY_SEARCH_MAX_CHECKS = 400


def _shortfall(verified: ProcessArmorSet, context: Any) -> int:
    """复核结果离真实目标还差多少（合计，供局部搜索当"打分"）。"""
    stats = list(verified.stats or [0] * len(STAT_NAMES))
    bonus = list(verified.bonus_stats or [0] * len(STAT_NAMES))
    return sum(
        max(0, int(context.desired_min[index]) - int(stats[index]) - int(bonus[index]))
        for index in range(len(STAT_NAMES))
        if context.desired_min[index] > 0
    )


def _donor_penalty(name: str, raw_targets: dict[str, int], priority: Sequence[str]) -> int:
    """牺牲某一项要付的"代价"：有目标的最贵，其次看优先级（DIM 的 dump-stat 口径）。

    调谐是零和的，所以"从哪一项拿 5 点"是这件事的关键：宁可牺牲没设目标、
    优先级又靠后的那一项。
    """
    if name in raw_targets:
        return 1000 + raw_targets[name]
    rank = priority.index(name) if name in priority else len(priority)
    return rank


def _pick_and_rescue(
    snapshot: Any,
    constraints: BuildConstraints,
    manifest: Any,
    allowance: dict[str, int],
    candidate_sets: Sequence[ProcessArmorSet],
) -> list[Rescue]:
    """从大池子里挑出最值得复核的前 K 套，再交给 `rescue_sets`。

    为什么需要挑：放宽那一趟为了不漏掉可救的方案，池子放到了 1500 套（实测依据见
    `_RELAXED_POOL_SIZE`），而精确复核每套约 0.1 秒 —— 1500 套全复核要三分钟，
    工具会超时。排序用**便宜的算术**（估计"护甲 + 调谐额度"之后还差多少），
    复核仍然是唯一的裁判：挑进来的还要逐套过 `rescue_sets` 才会出现在结果里。
    """
    if not candidate_sets:
        return []
    context = prepare_fixed_set_context(snapshot, constraints)
    names = list(STAT_NAMES)
    raw_min = constraints.as_vector()
    raw_targets = {
        name: int(raw_min[index])
        for index, name in enumerate(names)
        if raw_min[index] > 0
    }

    def residual(armor_set: ProcessArmorSet) -> int:
        """估计还差多少点（越小越值得复核）。"""
        stats = list(armor_set.stats or [0] * len(names))
        return sum(
            max(0, int(raw_min[index]) - int(stats[index]) - allowance.get(names[index], 0))
            for index in range(len(names))
            if raw_min[index] > 0
        )

    picked: list[ProcessArmorSet] = []
    for armor_set in sorted(candidate_sets, key=residual):
        arms = list(armor_set.armor)
        tunable = [
            piece
            for armor in arms
            if (piece := piece_tuning(armor, manifest)) is not None and piece.movable
        ]
        if not _worth_search(
            arms,
            list(armor_set.stats or [0] * len(names)),
            raw_targets,
            _mod_budget(arms, context),
            tunable,
        ):
            continue
        picked.append(armor_set)
        if len(picked) >= _VERIFY_TOP_K:
            break
    if not picked:
        return []
    return rescue_sets(snapshot, constraints, manifest, picked)


def _worth_search(
    arms: Sequence[Armor],
    armor_only: Sequence[int],
    raw_targets: dict[str, int],
    budget: int,
    tunable: Sequence[PieceTuning],
) -> bool:
    """便宜的必要条件：缺口合计超过"模组预算 + 调谐最多能搬的量"就没必要搜。"""
    needed = sum(
        max(0, target - int(armor_only[index]))
        for index, target in (
            (list(STAT_NAMES).index(name), value) for name, value in raw_targets.items()
        )
    )
    best_per_piece = 0
    for piece in tunable:
        best_gain = max(
            (max(piece.delta_of(choice)) for choice in piece.options), default=0
        )
        best_per_piece += max(0, best_gain)
    return needed <= budget + best_per_piece


def verification_search(
    arms: Sequence[Armor],
    tunable: Sequence[PieceTuning],
    context: Any,
    raw_targets: dict[str, int],
    priority: Sequence[str],
) -> tuple[list[Armor], TuningPlan] | None:
    """复核驱动的局部搜索：每步挑一个"最可能达标"的调谐改动，由精确复核裁决。

    为什么需要它：启发式规划（`plan_tuning`）用的是"剩余缺口合计 ≤ 模组预算"这种
    简化口径，会漏掉一些确实可行的组合 —— 实机踩过：同一套护甲、同一组目标，
    复核明明能过，规划却说不可行。
    DIM 的做法是把调谐变体直接铺进搜索（非金装逐件展开、金装在主循环里换），
    这个 Python 实现扛不住那种展开量（组合上限 2000 万），所以改成
    "小步走 + 每步精确复核"：起点是现状，每步只在**真正减少缺口**的改动里挑最好的，
    最多 5 步；一旦复核达标就返回。所有结论都过了复核，不做没验证的推断。
    """
    if not tunable:
        return None
    current = {piece.item_instance_id: piece.current for piece in tunable}

    # 候选裁剪：全量 31 个调谐 × 5 件 × 5 步会到分钟级（实机踩过，工具直接超时）。
    # 只留"能补某个缺口项"的，并按让步代价排序取前 K 个 —— 精确复核仍然是唯一的裁判。
    def shortlist(piece: PieceTuning) -> list[TuningChoice]:
        scored = [
            choice
            for choice in piece.options
            if any(
                piece.delta_of(choice)[index] > 0
                for index in range(len(STAT_NAMES))
                if raw_targets.get(STAT_NAMES[index], 0) > 0
            )
        ]
        scored.sort(
            key=lambda choice: (
                _donor_penalty(choice.decreased or "", raw_targets, priority),
                -sum(piece.delta_of(choice)),
            )
        )
        return scored[:_VERIFY_SEARCH_OPTIONS_PER_PIECE]

    def build(assignment: dict[str, TuningChoice]) -> list[Armor]:
        by_id = {piece.item_instance_id: piece for piece in tunable}
        out: list[Armor] = []
        for armor in arms:
            piece = by_id.get(str(armor.item_instance_id))
            choice = assignment.get(str(armor.item_instance_id))
            if piece is None or choice is None or choice.plug_hash == piece.current.plug_hash:
                out.append(armor)
                continue
            out.append(armor_with_tuning(armor, piece, choice))
        return out

    def plan_of(assignment: dict[str, TuningChoice]) -> TuningPlan:
        changes: list[TuningChange] = []
        for piece in tunable:
            choice = assignment.get(piece.item_instance_id)
            if choice is None or choice.plug_hash == piece.current.plug_hash:
                continue
            changes.append(
                TuningChange(
                    item_instance_id=piece.item_instance_id,
                    item_name=piece.name,
                    slot=piece.slot,
                    from_plug=piece.current.plug_hash,
                    from_name=piece.current.name,
                    to_plug=choice.plug_hash,
                    to_name=choice.name,
                    delta=piece.delta_of(choice),
                    increased=choice.increased,
                    decreased=choice.decreased,
                )
            )
        return TuningPlan(feasible=True, changes=tuple(changes))

    best_assignment = dict(current)
    best_shortfall: int | None = None
    checks = 0
    for _step in range(_VERIFY_SEARCH_MAX_STEPS):
        if checks >= _VERIFY_SEARCH_MAX_CHECKS:
            break
        improved = False
        for piece in tunable:
            for choice in shortlist(piece):
                if choice.plug_hash == best_assignment.get(piece.item_instance_id, piece.current).plug_hash:
                    continue
                candidate = dict(best_assignment)
                candidate[piece.item_instance_id] = choice
                checks += 1
                if checks > _VERIFY_SEARCH_MAX_CHECKS:
                    break
                verified = validate_fixed_process_items(
                    [armor_to_process_item(armor) for armor in build(candidate)],
                    context,
                )
                if verified is None:
                    continue
                if _meets(verified, context):
                    return build(candidate), plan_of(candidate)
                shortfall = _shortfall(verified, context)
                if best_shortfall is None or shortfall < best_shortfall:
                    best_shortfall = shortfall
                    best_assignment = candidate
                    improved = True
        if not improved:
            break
    return None


def _apply_plan(
    arms: Sequence[Armor],
    tunable: Sequence[PieceTuning],
    plan: TuningPlan,
) -> list[Armor]:
    """按方案换调谐，返回新的 5 件；有任何一处对不上就返回空列表（不猜）。"""
    by_id = {piece.item_instance_id: piece for piece in tunable}
    by_change = {change.item_instance_id: change for change in plan.changes}
    tuned: list[Armor] = []
    for armor in arms:
        piece = by_id.get(str(armor.item_instance_id))
        change = by_change.get(str(armor.item_instance_id))
        if piece is None or change is None:
            tuned.append(armor)
            continue
        choice = piece.option(change.to_plug)
        if choice is None:
            return []
        tuned.append(armor_with_tuning(armor, piece, choice))
    return tuned


def _meets(verified: ProcessArmorSet, context: Any) -> bool:
    """复核结果是否真的达到（真实）目标。"""
    wanted = [
        index for index, value in enumerate(context.desired_min) if value > 0
    ]
    if not wanted:
        return False
    stats = list(verified.stats or [0] * len(STAT_NAMES))
    bonus = list(verified.bonus_stats or [0] * len(STAT_NAMES))
    return all(
        int(stats[index]) + int(bonus[index]) >= int(context.desired_min[index])
        for index in wanted
    )


def _rebuild(verified: ProcessArmorSet, arms: Sequence[Armor]) -> ProcessArmorSet:
    """把复核结果装回一个带护甲对象的集合（求解器那边只回填 process_items）。"""
    return ProcessArmorSet(
        armor=list(arms),
        process_items=list(verified.process_items),
        stats=list(verified.stats),
        bonus_stats=list(verified.bonus_stats),
        stat_mods=list(verified.stat_mods),
        stat_mod_assignments=dict(verified.stat_mod_assignments),
        rank_key=verified.rank_key,
        power=verified.power,
    )


def apply_local_tuning(
    pool: list[ProcessArmorSet],
    tuning_map: dict[tuple[str, ...], TuningPlan],
    snapshot: Any,
    constraints: BuildConstraints,
    manifest: Any,
    *,
    top_n: int = 5,
) -> tuple[list[ProcessArmorSet], dict[tuple[str, ...], TuningPlan]]:
    """把**免费的调谐额度**在一池候选上吃干净（有解时走的便宜路）。

    与 `solve_with_tuning` 的分工：那个是"差得补不上"时的穷举补救（贵，只在严格解为空时走）；
    这个是"已经达标、还想更好"时的局部提升（池子大小 × 单件选项，亚秒级）。
    `improve_pool_with_local_tuning` 在 `build/tuning.py`，判据与补救路径相同。

    放在这里的理由：`build_service.py` 卡在 901 行上限，判断逻辑按约定落服务层。
    """
    if not pool:
        return pool, tuning_map
    context = prepare_fixed_set_context(snapshot, constraints)
    # ── 成本护栏：只吃"有机会被返回"的那一段 ────────────────────────────────
    # 内核一次会交回整整一池（`RETURNED_ARMOR_SETS` = 200 套），而用户看到的只有 `top_n` 套。
    # 逐套跑贪心的代价实测是 **60–120 秒**（titan 那条：内核实测 8s，端到端 130s）——
    # 也就是说这一趟比枚举本身贵一个数量级。窗口按当前 `rank_key` 取前 K 套，
    # K = max(2 × top_n, 10)：调谐提升的幅度有上界（每件几十点），窗口外的套要挤进
    # 前 top_n 得先跨过整个窗口，这不是证明、是**有意的成本取舍**（数字见计划文档 P5 记录）。
    window = max(top_n * 2, 10)
    plans = dict(tuning_map)
    if len(pool) <= window:
        pairs = improve_pool_with_local_tuning(pool, context, manifest)
    else:
        head_index = sorted(
            range(len(pool)), key=lambda index: pool[index].rank_key, reverse=True
        )[:window]
        improved_head = improve_pool_with_local_tuning(
            [pool[index] for index in head_index], context, manifest
        )
        pairs = [(armor_set, None) for armor_set in pool]
        for slot, index in enumerate(head_index):
            pairs[index] = improved_head[slot]
    for armor_set, plan in pairs:
        if plan is not None and plan.changes:
            plans[set_key(armor_set.armor)] = plan
    return [armor_set for armor_set, _ in pairs], plans


@dataclass(frozen=True)
class TuningOutcome:
    """一次"求解 + 调谐补救"的完整结果。

    为什么不是三元组：`coverage` 必须跟着候选一起走 —— 调用方拿它区分"搜完了真没解"
    与"没搜完"，这个判断不能被漏掉（P1 的整条纪律见 `SearchCoverage` 的注释）。
    """

    pool: list[ProcessArmorSet]
    tuning_map: dict[tuple[str, ...], TuningPlan]
    coverage: SearchCoverage
    #: 这一趟有没有动用"放宽目标 + 调谐补救"（给话术与诊断用，不参与判定）
    rescued: bool
    #: 逐项可达上限，**只取严格那一趟**的（放宽目标那趟的口径和请求不一样，混起来是错的）
    reachable_ceilings: list[int] = field(default_factory=list)


async def solve_with_tuning(
    compute: Any,
    snapshot: Any,
    constraints: BuildConstraints,
    manifest: Any,
) -> TuningOutcome:
    """按原目标求解；没达标时再用调谐额度复解一遍并逐套复核。

    候选池里被调谐救回来的方案排在前面 —— `find_build` 只取前 top_n 套，得先保证它们进得来。
    """
    solved = await compute.run(_solve, snapshot, constraints)
    strict_sets = list(solved.sets)
    if strict_sets and sets_meet_targets(strict_sets, constraints):
        # 已经达标：一个字都不改（基线里那些本来就绿的方案必须保持一致）
        return TuningOutcome(
            strict_sets, {}, solved.coverage, False, solved.reachable_ceilings
        )

    rescues = rescue_sets(snapshot, constraints, manifest, strict_sets)
    rescued_keys = {set_key(rescue.armor_set.armor) for rescue in rescues}

    allowance = tuning_allowance(snapshot, manifest)
    # 严格解一套都没有时不能只看"已解的差距"：那种空池往往是求解器自己的裁剪
    # （缺口 > 它能给的模组上限）造成的，不代表调谐也够不着 —— 所以照样试一次，
    # 让 plan_tuning 的额度检查去否决真正够不着的目标。
    # 反过来说：池子非空、却每套都差得超过额度时就直接跳过第二次求解。
    # （实机上"池子非空但差得远"很少见：模组经济够宽时求解器要么补上、要么干脆裁掉，
    #   所以这条分支主要在多目标请求里省时间；判定本身有单测。）
    reachable = within_tuning_reach(strict_sets, constraints, allowance) if strict_sets else True
    relaxed = relax_targets(constraints, allowance) if reachable else None
    if relaxed is not None and any(value > 0 for value in allowance.values()):
        relaxed_solved = await compute.run(_solve_wide, snapshot, relaxed)
        # 复核用**真实**目标：放宽只负责把候选找出来，达标与否按原目标算。
        for rescue in _pick_and_rescue(
            snapshot, constraints, manifest, allowance, list(relaxed_solved.sets)
        ):
            key = set_key(rescue.armor_set.armor)
            if key in rescued_keys:
                continue
            rescued_keys.add(key)
            rescues.append(rescue)

    plans = {set_key(rescue.armor_set.armor): rescue.plan for rescue in rescues}
    pool = [rescue.armor_set for rescue in rescues]
    pool.extend(
        armor_set
        for armor_set in strict_sets
        if set_key(armor_set.armor) not in rescued_keys
    )
    return TuningOutcome(
        pool, plans, solved.coverage, True, solved.reachable_ceilings
    )


def _solve(snapshot: Any, constraints: BuildConstraints) -> Any:
    """求解器入口（单独一层是为了让 `compute.run` 拿到可 pickle 的裸函数）。"""
    from ..build import solver as _solver

    return _solver.solve(snapshot, constraints)


def _solve_wide(snapshot: Any, constraints: BuildConstraints) -> Any:
    """放宽目标那一趟：多留一些候选（见 `_RELAXED_POOL_SIZE` 的实测依据）。"""
    from ..build import solver as _solver

    return _solver.solve(snapshot, constraints, returned_sets=_RELAXED_POOL_SIZE)


def headroom_payload(snapshot: Any, manifest: Any) -> dict[str, Any]:
    """调谐额度证据（给"无解"阶梯用，证明"已经试过调谐"而不是没试）。

    `allowance` 与 `per_stat_max_gain` 是同一个数：前者是求解器放宽目标用的额度，
    后者是阶梯拿去跟缺口比的每项上限。两个键都留着，读的人不用猜。
    """
    allowance = tuning_allowance(snapshot, manifest)
    return {
        "allowance": dict(allowance),
        "per_stat_max_gain": {
            name: value for name, value in allowance.items() if value
        },
        "note": (
            "调谐是零和的（+5 一项、−5 另一项），上面是每项理论上最多能加的点数"
            "（按「每个部位出一件」算，已含「撤掉当前反向调谐」的 +10 情况）；"
            "实际方案还要看被减的那一项够不够让。"
        ),
    }


__all__ = [
    "Rescue",
    "SLOT_ATTRS",
    "apply_local_tuning",
    "armor_with_tuning",
    "headroom_payload",
    "relax_targets",
    "rescue_sets",
    "sets_meet_targets",
    "TuningOutcome",
    "solve_with_tuning",
    "tuning_allowance",
    "verification_search",
    "within_tuning_reach",
]
