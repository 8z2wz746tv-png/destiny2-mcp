"""P3：排序只有一套口径 —— 逐字段字典序，跨路径必须一致。

以前有三套：求解器堆里的 `_ranking_metric`（优先级字典序）、`scorer.score` 的加权总分
（超出目标**每点倒扣 0.5**、六维总和只加 0.1）、`find_build` 末尾 `completion_rate` 打头。
结果就是"多堆 10 点手雷"在评分里是负收益 —— 求解器只交及格卷。

现在唯一口径是 `build/ranking.goodness_key`，性质三条（见计划文档决定 2）：
① 第一位是布尔（有没有任何显式规则没满足）；② 同层先比未达标个数、再比缺口；
③ 逐字段比较 → **低优先级的富余不能补贴高优先级的缺失**。

本文件里的"反例"来自 d2-armor-solver 的公开自查（`docs/priority-rules-audit.md`）：
他们老代码会选出 [武器90/生命100]，而正确口径必须选 [武器100/生命80]。
"""

from __future__ import annotations

from destiny_mcp.build.models import BuildConstraints
from destiny_mcp.build.ranking import (
    clamped_total,
    goodness_key,
    preference_desc,
    priority_desc,
    stat_gap,
)

WEAPONS, HEALTH, CLASS, GRENADE, MELEE, SUPER = range(6)


def _gap_only_below() -> None:
    """只有下限：超过不罚。"""
    assert stat_gap(120, minimum=100, maximum=0) == 0
    assert stat_gap(80, minimum=100, maximum=0) == 20


def test_gap_is_zero_inside_the_legal_interval() -> None:
    """区间内 gap=0 —— 靠不靠近上限**不影响**达标程度。"""
    assert stat_gap(120, minimum=100, maximum=200) == 0
    assert stat_gap(200, minimum=100, maximum=200) == 0
    assert stat_gap(99, minimum=100, maximum=200) == 1
    assert stat_gap(201, minimum=100, maximum=200) == 1


def test_gap_only_penalises_the_side_that_was_asked_for() -> None:
    _gap_only_below()
    assert stat_gap(120, minimum=0, maximum=100) == 20, "只给上限时只罚超出"
    assert stat_gap(80, minimum=0, maximum=100) == 0
    assert stat_gap(80, minimum=0, maximum=0) == 0, "什么都没要求 = 怎么都行"


# ── d2-armor-solver 审计里的反例：高优先达标必须压过低优先达标 ──────────


def test_high_priority_attainment_beats_low_priority_attainment() -> None:
    """武器高优先：`[武器100/生命80]` 必须赢 `[武器90/生命100]`。"""
    constraints = BuildConstraints(
        weapons_min=100, health_min=100, priority_stat_indices=[WEAPONS]
    )
    high_met = [100, 80, 0, 0, 0, 0]
    low_met = [90, 100, 0, 0, 0, 0]

    assert goodness_key(high_met, constraints) > goodness_key(low_met, constraints)


def test_a_lower_priority_surplus_cannot_pay_for_a_higher_priority_miss() -> None:
    """低优先堆到爆也补不了高优先差 1 点。

    **这条不是单点守门**：同一个方向由两处机制同时保证 —— 规则层的"每个优先项各一层"
    与偏好层的"按优先级顺序比值"。所以单独注掉任何一处，这个用例仍然是绿的；
    它守的是**性质**（谁该赢），不是某一行实现。要单点守门看上面两条。
    """
    constraints = BuildConstraints(
        weapons_min=100, health_min=0, priority_stat_indices=[WEAPONS, HEALTH]
    )
    weapons_one_short = [99, 200, 0, 0, 0, 0]
    weapons_met = [100, 0, 0, 0, 0, 0]

    assert goodness_key(weapons_met, constraints) > goodness_key(weapons_one_short, constraints)


def test_ordinary_tier_compares_count_before_gap() -> None:
    """**普通层**里先比未达标个数、再比缺口：差两项各 5 点 输给 差一项 50 点。

    注意只有"普通层"（没进 `priority_stats` 的那些项）才有多项，
    每个优先项各占一层、个数只可能是 0 或 1 —— 别把这条性质套到优先级层上。
    """
    constraints = BuildConstraints(weapons_min=100, health_min=100, grenade_min=100)
    two_small = [95, 95, 0, 100, 0, 0]   # 两项各差 5 → 个数 2、缺口 10
    one_big = [50, 100, 0, 100, 0, 0]    # 一项差 50 → 个数 1、缺口 50

    assert goodness_key(one_big, constraints) > goodness_key(two_small, constraints), (
        "先比个数：少差一项赢（若先比缺口，10 < 50 会让 two_small 赢）"
    )


def test_the_first_field_is_a_boolean_not_a_weighted_count() -> None:
    """违规的方案输给不违规的方案 —— 哪怕后者优先级低得多、总体也低得多。"""
    constraints = BuildConstraints(
        weapons_min=100, priority_stat_indices=[GRENADE]
    )
    clean_but_dull = [100, 0, 0, 0, 0, 0]
    flashy_but_missing = [0, 0, 0, 180, 0, 0]

    assert goodness_key(clean_but_dull, constraints) > goodness_key(flashy_but_missing, constraints)


def test_exceeding_a_cap_ranks_like_a_miss() -> None:
    """上限是规则的一部分：超上限与没达下限一样，掉到"不违规"的后面。"""
    constraints = BuildConstraints(weapons_min=100, super_stat_max=100)
    over_cap = [100, 0, 0, 0, 0, 110]
    inside = [100, 0, 0, 0, 0, 100]

    assert goodness_key(inside, constraints) > goodness_key(over_cap, constraints)


# ── 偏好维度：达标之后按优先级 + 封顶总和 ───────────────────────────────


def test_preference_follows_priority_order_then_clamped_total() -> None:
    constraints = BuildConstraints(priority_stat_indices=[GRENADE, WEAPONS])
    more_grenade = [0, 0, 0, 120, 0, 0]
    more_weapons = [200, 0, 0, 110, 0, 0]

    assert goodness_key(more_grenade, constraints) > goodness_key(more_weapons, constraints)


def test_clamped_total_does_not_pay_for_overflow() -> None:
    """超过上限的部分不计分（也不转移给别的属性）—— DIM 的 enabledStatsTotal 口径。"""
    constraints = BuildConstraints(super_stat_max=100)

    assert clamped_total([0, 0, 0, 0, 0, 150], constraints) == 100
    assert clamped_total([0, 0, 0, 0, 0, 100], constraints) == 100
    assert clamped_total([0, 0, 0, 0, 0, 90], constraints) == 90


# ── 跨路径一致：三处必须同源 ─────────────────────────────────────────────


def test_display_ranking_only_looks_at_the_shared_key() -> None:
    """展示排序（`rank_results`）只看共享键：**把 score 改成反向也不影响顺序**。"""
    from destiny_mcp.build.models import BuildResult, BuildCandidate
    from destiny_mcp.build.ranking import rank_results

    constraints = BuildConstraints(weapons_min=100, priority_stat_indices=[GRENADE])
    better = BuildResult(score=0.0, completion_rate=1.0,
                         build=BuildCandidate(items=[], weapons=100, grenade=120))
    worse = BuildResult(score=999.0, completion_rate=1.0,
                        build=BuildCandidate(items=[], weapons=100, grenade=110))

    assert [row is better for row in rank_results([worse, better], constraints)] == [True, False], (
        "手雷优先 → 120 的那套在前，哪怕它的 score 更低"
    )


def test_farm_target_preference_matches_the_main_comparator() -> None:
    """反推待刷件自己插了成本键，但**属性那一段**必须与主口径同源。"""
    constraints = BuildConstraints(priority_stat_indices=[GRENADE, WEAPONS])
    a = [0, 0, 0, 120, 0, 0]
    b = [200, 0, 0, 110, 0, 0]

    # 主口径：手雷优先 → a 赢
    assert goodness_key(a, constraints) > goodness_key(b, constraints)
    # 共享助手：同样 a 赢（`sorted` 用 `key=priority_desc` 时升序取负）
    assert priority_desc(a, constraints) < priority_desc(b, constraints)
    assert preference_desc(a, constraints) < preference_desc(b, constraints)


def test_score_is_no_longer_a_ranking_key() -> None:
    """`score` 只剩展示含义：改了它不会改变候选顺序（顺序由 goodness_key 决定）。"""
    from destiny_mcp.build.models import BuildCandidate
    from destiny_mcp.build.scorer import score

    constraints = BuildConstraints(weapons_min=100, super_stat_max=100)
    candidate = BuildCandidate(items=[], weapons=100, super_stat=150)

    # 武器 100（没给上限，按 200 封顶）+ 超能 min(150, 100) = 200
    assert score(candidate, constraints) == 200.0, "超上限的 150 只按 100 计"
    assert score(candidate, constraints) == float(
        clamped_total([100, 0, 0, 0, 0, 150], constraints)
    )


# ── 乐观键的保守性：剪枝靠它，错了会**悄悄丢解**（P5 补） ──────────────────


def _reachable_vectors(stats: list[int], budget: int) -> list[list[int]]:
    """把 `budget` 点预算分给六项的**全部**方案（每项 0..budget，总和 ≤ budget）。"""
    out: list[list[int]] = []

    def walk(index: int, left: int, current: list[int]) -> None:
        if index == 6:
            out.append(list(current))
            return
        for spend in range(left + 1):
            current.append(stats[index] + spend)
            walk(index + 1, left - spend, current)
            current.pop()

    walk(0, budget, [])
    return out


def test_optimistic_key_is_an_upper_bound_for_every_reachable_vector() -> None:
    """`goodness_key(..., span=budget)` 必须 ≥ **任何**把预算花出去之后的真实键。

    为什么值得一条单独的测试：`span` 是模组余量，剪枝用乐观键筛人 ——
    如果上界算小了，本来能进 top-N 的方案会被悄悄丢掉，而且**不会有任何报错**。
    第一版把 `span` 每项都加一遍（等于把一份共享预算当成六份），那是"太松"（慢但不丢解）；
    P5 改成按排序键顺序分配预算（那是最紧的合法上界）。这条测试钉的就是"紧而不越界"。
    """
    stats = [40, 30, 20, 25, 15, 10]
    shapes = [
        BuildConstraints(),                                    # 什么都没有
        BuildConstraints(grenade_min=100),                     # 只有下限
        BuildConstraints(grenade_min=100, super_stat_max=100),  # 下限 + 上限（真机那条）
        BuildConstraints(weapons_min=80, grenade_min=90,
                         priority_stat_indices=[GRENADE, WEAPONS]),   # 带优先级
        BuildConstraints(weapons_min=80, super_stat_max=60,
                         priority_stat_indices=[WEAPONS, SUPER]),     # 优先级撞上限
    ]
    budget = 4
    for constraints in shapes:
        optimistic = goodness_key(stats, constraints, span=budget)
        for reachable in _reachable_vectors(stats, budget):
            assert optimistic >= goodness_key(reachable, constraints), (
                f"乐观键不是上界：{constraints} 下 {reachable} 的真实键更大"
            )


def test_optimistic_key_is_tighter_than_adding_the_budget_to_every_stat() -> None:
    """回归：不许退回"每项各加一份预算"的那个松上界。

    真机代价：一条没有 priority 的请求（排序键只剩"封顶总和"能区分）因此把几乎每个组合
    都放进最贵的校验，内核 **355 秒**；按预算分配之后同一条请求 64 秒。
    """
    constraints = BuildConstraints(grenade_min=100, super_stat_max=100)
    stats = [40, 30, 20, 25, 15, 10]
    budget = 30

    optimistic = goodness_key(stats, constraints, span=budget)
    loose_total = sum(min(value + budget, 200) for value in stats)
    assert optimistic[-1] < loose_total, "封顶总和那一项又变回'每项各加一遍'了"
    # 但也必须至少是真实键（保守）
    assert optimistic >= goodness_key(stats, constraints)
