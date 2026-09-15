"""Build Analyzer — explains why a build request has no solution.

When the solver returns zero candidates, the Analyzer computes the maximum
achievable stats from the current inventory and identifies which targets
cannot be met. It also suggests farming activities for each stat.
"""

from __future__ import annotations

from .. import config
from ..exceptions import BuildTooLargeError
from ..logging_config import get_logger
from .models import (
    STAT_NAMES,
    BuildAnalysis,
    BuildConstraints,
    InventorySnapshot,
)
from ..vocabulary import STAT_LABELS_ZH

logger = get_logger(__name__)

# Which stats are hard to farm (needs specific activities)
_STAT_ADVICE: dict[str, str] = {
    "weapons": "在 HELM 用「武器」机灵模组聚焦护甲（偏武器属性）",
    "health": "在 HELM 用「生命值」机灵模组聚焦护甲（偏生命属性）",
    "class_stat": "在 HELM 用「职业」机灵模组聚焦护甲（偏职业属性）",
    "grenade": "在 HELM 用「手雷」机灵模组聚焦护甲（偏手雷属性）",
    "melee": "在 HELM 用「近战」机灵模组聚焦护甲（偏近战属性）",
    "super_stat": "在 HELM 用「超能」机灵模组聚焦护甲（偏超能属性）",
}


def estimate_combinations(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> tuple[int, list[int]]:
    """估算这次分析要枚举多少种组合，返回 (组合数, 各部位件数)。

    纯计数、不枚举：五个部位件数相乘，指定金装时该部位只算金装那几件
    （所以"指定金装"是最有效的收窄手段）。
    """
    slots = [
        snapshot.helmets,
        snapshot.gauntlets,
        snapshot.chests,
        snapshot.legs,
        snapshot.class_items,
    ]
    counts: list[int] = []
    for pieces in slots:
        if constraints.exotic_hashes:
            exotic = [p for p in pieces if p.item_hash in constraints.exotic_hashes]
            counts.append(len(exotic) if exotic else len(pieces))
            continue
        counts.append(len(pieces))
    total = 1
    for count in counts:
        total *= max(1, count)
    return total, counts


def ensure_within_combination_limit(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> None:
    """规模超限就抛 `BuildTooLargeError`：**先给怎么收窄**，而不是让调用方等到超时。

    与 `analyze` 共用同一个估算函数和同一句说明，两条路的建议不会漂移。
    """
    total, counts = estimate_combinations(snapshot, constraints)
    limit = config.BUILD_MAX_COMBINATIONS
    if limit > 0 and total > limit:
        logger.info(
            "Combination estimate %s exceeds limit %s; refusing to enumerate", total, limit
        )
        raise BuildTooLargeError(too_large_reason(total, counts, limit))

def narrowing_actions() -> list[str]:
    """规模超限时可以怎么收窄（工具层的 next_actions 用同一份，避免两处说法漂移）。"""
    return [
        "指定一件金装：该部位直接锁成它，收窄最明显。",
        "减少属性目标，或只留最在意的一两项。",
        "只想补某一个部位，用 build_assistant(intent=\"farm_target\") 反推那一件。",
        "确实要跑，把 DESTINY_BUILD_MAX_COMBINATIONS 设为 0 或调高上限后重试。",
    ]


def too_large_reason(total: int, counts: list[int], limit: int) -> str:
    """组合规模超限时的统一说明（analyze 与 recommend/find 共用同一句话）。"""
    return (
        f"这次分析的组合规模太大（各部位 {counts[0]}/{counts[1]}/{counts[2]}/"
        f"{counts[3]}/{counts[4]} 件，预估约 {total:,} 种组合，超过上限 {limit:,}），"
        "没有做精确的属性上限推算。可以这样收窄：①指定一件金装（该部位直接锁成它，"
        "收窄最明显）；②减少属性目标，或只留最在意的一两项；③只想补某一个部位，"
        "用 intent=\"farm_target\" 反推那一件；④确实要跑精确分析，把 "
        "DESTINY_BUILD_MAX_COMBINATIONS 设为 0 或调高上限后重试。"
    )

def analyze(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> BuildAnalysis:
    """Analyze why no build satisfies the given constraints.

    Args:
        snapshot: The player's armor inventory.
        constraints: The target constraints.

    Returns:
        BuildAnalysis with failure reason and farming suggestions.
    """
    total, counts = estimate_combinations(snapshot, constraints)
    limit = config.BUILD_MAX_COMBINATIONS
    if limit > 0 and total > limit:
        # 提前失败：精确上限要对每个属性各跑一次求解器，规模一大必然跑满预算，
        # 让用户干等 5 分钟才拿到"缩小请求"是浪费。这里直接给可操作建议，
        # 并**不**编一个 max_possible（没算就是没算）。
        logger.info(
            "Analyzer: combination estimate %s exceeds limit %s; returning early", total, limit
        )
        return BuildAnalysis(
            reason=too_large_reason(total, counts, limit),
            # 这一行不能少：默认值是 "exact"，漏了就会变成"精确算过、上限为空"——
            # 调用方会读成"你什么都达不到"。实测 smoke 行抓到过这次回归。
            precision="not_computed",
        )

    max_possible = _max_possible_stats(snapshot, constraints)

    # Check each target
    failures: list[str] = []
    suggestions: list[str] = []
    for stat_name in STAT_NAMES:
        target = getattr(constraints, f"{stat_name}_min")
        if target > 0 and target > max_possible[stat_name]:
            gap = target - max_possible[stat_name]
            label = STAT_LABELS_ZH.get(stat_name, stat_name)
            failures.append(
                f"{label}要 {target}，把点全堆这一项也只有 {max_possible[stat_name]}（差 {gap}）"
            )
            if stat_name in _STAT_ADVICE:
                suggestions.append(_STAT_ADVICE[stat_name])

    # Deduplicate suggestions
    suggestions = list(dict.fromkeys(suggestions))

    if failures:
        reason = (
            f"有 {len(failures)} 项目标超过当前背包的单项上限："
            + "；".join(failures)
            + "。单项上限是把点全堆一项时的最大值，不是同一套护甲能同时达到的值。"
        )
    else:
        # 这条分支以前写的是 "No valid armor combination found"：它**没有**验证过
        # 有没有合法组合（只算了单项上限），实机出现过「analyze 说配不出来、同约束
        # recommend 给出 completion_rate=1.0 的方案」。不能替求解器下结论。
        reason = (
            "各项目标单看都在单项上限之内，所以配不出来的原因不在「某一项堆不上去」，"
            "而在同一套护甲要同时满足这些目标（还可能被金装、优先级与组合规模限制）。"
            "这里只算单项上限，不下「能不能配出来」的结论；"
            "要结论请用 intent=\"recommend\"/\"find\"，或指定金装/减少目标后再看。"
        )

    logger.info("Analyzer: %d failures, %d suggestions", len(failures), len(suggestions))
    return BuildAnalysis(
        reason=reason,
        max_possible=max_possible,
        suggested_farm=suggestions,
    )


def _max_possible_stats(
    snapshot: InventorySnapshot,
    constraints: BuildConstraints,
) -> dict[str, int]:
    """Compute max per-stat values with the same mod rules as the solver.

    The previous analyzer only summed raw armor stats, so it understated
    achievable values whenever +10 general mods or +3 artifice mods could
    close a gap. Probe the solver once per stat with that stat as the dump
    stat so diagnostics match the optimizer's real rules.
    """
    from .solver import solve

    bonus_vector = constraints.subclass_and_fragment_vector()
    max_possible: dict[str, int] = {}

    for stat_index, stat_name in enumerate(STAT_NAMES):
        probe = _probe_constraints_for_stat(constraints, stat_index)
        result = solve(snapshot, probe)
        if not result.sets:
            max_possible[stat_name] = bonus_vector[stat_index]
            continue

        max_possible[stat_name] = max(
            armor_set.stats[stat_index]
            + (
                armor_set.bonus_stats[stat_index]
                if stat_index < len(armor_set.bonus_stats)
                else 0
            )
            + bonus_vector[stat_index]
            for armor_set in result.sets
        )

    return max_possible


def _probe_constraints_for_stat(
    constraints: BuildConstraints,
    stat_index: int,
) -> BuildConstraints:
    """Build a solver probe that keeps non-stat constraints intact."""
    return BuildConstraints(
        exotic_hash=constraints.exotic_hash,
        exotic_hashes=set(constraints.exotic_hashes),
        class_type=constraints.class_type,
        top_n=200,
        subclass_stats=list(constraints.subclass_stats),
        fragment_stats=list(constraints.fragment_stats),
        set_bonus_hash=constraints.set_bonus_hash,
        set_bonus_count=constraints.set_bonus_count,
        priority_stat_index=stat_index,
    )
