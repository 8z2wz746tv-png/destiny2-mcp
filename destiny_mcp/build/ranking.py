"""配装排序的**唯一口径**：逐字段字典序，不是加权总分。

口径来自 d2-armor-solver 的 9 元组（`src/core/stat-ranking.mjs`，见
`docs/plans/SOLVER_OPTIMALITY_PLAN.md` 决定 2），三条性质不能破：

1. 第一位是**布尔**（有没有任何显式规则没满足），不是跨优先级的未达标总数加权；
2. 同一层里**先比未达标个数、再比缺口** —— "达没达"比"差多少"重要；
3. 逐字段比较 → **低优先级的富余不能补贴高优先级的缺失**。

与它的差异（有意）：它固定高/中/低三层，我们按 `priority_stats` **逐项展开**成 n 层 ——
我们对外承诺的是"按顺序最大化"，压成三层会把这个承诺悄悄削弱。层数不同，比较方式一致。

**为什么要独立一个模块**：以前排序散在三处（求解器堆里的 `_ranking_metric`、`scorer.score`
的加权总分、`find_build` 末尾的 `completion_rate` 打头），三套口径互相打架 ——
"超一点就扣分"就是这么写进去的。现在搜索、剪枝、候选保留、展示**共用这里**。

`goodness_key` 的约定：**越大的越好**（和 `HeapEntry` 的 min-heap 约定一致：堆顶是最差的）。
所以"未达标个数/缺口/浪费"这类越少越好的项都要取负。
"""

from __future__ import annotations

from typing import Any

from .constants import STAT_NAMES
from .models import BuildConstraints

#: 最多能表达 6 个优先层；多余的一律并进"普通"层（不会发生，`ordered_priority_indices` 已去重）。
_MAX_TIERS = 6
#: 键长 = 布尔位 + 每层(个数, 缺口) + 普通层(个数, 缺口) + 偏好(6 项优先级值 + 封顶总和)
_KEY_LENGTH = 1 + 2 * (_MAX_TIERS + 1) + 6 + 1


def stat_gap(actual: int, *, minimum: int, maximum: int) -> int:
    """这一项离合法区间有多远；**区间内就是 0**。

    - 只有下限：只罚不足（超过不罚）；
    - 只有上限：只罚超出；
    - 两个都有：越界才罚；
    - 两个都没给：0。

    注意"区间内 gap=0"意味着**靠近上限不会加分** —— 区间不表达"越多越好"，
    要顶上去得把目标写成下限再加优先级（真机实测见计划文档 P2 记录）。
    """
    below = max(0, minimum - actual) if minimum > 0 else 0
    above = max(0, actual - maximum) if maximum > 0 else 0
    return below + above


def gap_vector(stats: list[int], constraints: BuildConstraints, *, span: int = 0) -> list[int]:
    """六项各自的缺口。`span` 是"这套还可能再涨多少"的乐观余量（剪枝用）。

    给了 `span` 时按**每项都能拿到全部余量**来估 —— 这是上界（不可能比这更好），
    所以用它剪枝是保守的：宁可多算，不会把还能达标的方案剪掉。
    """
    minimums = constraints.as_vector()
    maximums = constraints.max_vector()
    return [
        stat_gap(
            stats[index] + (span if span else 0),
            minimum=minimums[index],
            maximum=maximums[index] if span == 0 else 0,
        )
        for index in range(6)
    ]


def rule_rank(stats: list[int], constraints: BuildConstraints, *, span: int = 0) -> tuple[int, ...]:
    """规则维度的 goodness（越大越好）：布尔位 + 每层(未达标个数, 缺口) + 普通层。

    **键的确切形状**（别照抄参考实现的"高/中/低三层"来理解）：

    - 布尔位：有没有**任何**显式规则没满足（下限没到、上限超了都算）。
      它其实是**冗余**的 —— 任何违规都会在它所属那一层留下一个负数，而干净的方案那里是 0，
      逐字段比下来干净的必赢。留着是因为它把"先看违规"写成显式契约，也便于早退。
    - `priority_stats` 的**每一项各占一层**：所以那些层的"未达标个数"只可能是 0 或 1，
      "先个数后缺口"在那里退化成"先看达没达、再看差多少"。我们是严格顺序，
      不像参考实现那样让用户把多项放进同一档。
    - **"普通层"（没进 `priority_stats` 的项）才是 count-then-gap 真正起作用的地方**：
      那里最多 6 项，"差两项各 5 点"与"差一项 50 点"要靠它区分。
    """
    gaps = gap_vector(stats, constraints, span=span)
    priorities = constraints.ordered_priority_indices
    explicit = constraints.as_vector()
    maximums = constraints.max_vector()

    tier_of: dict[int, int] = {index: tier for tier, index in enumerate(priorities)}
    counts = [0] * (_MAX_TIERS + 1)
    sums = [0] * (_MAX_TIERS + 1)
    any_unmet = 0
    for index in range(6):
        if gaps[index] <= 0:
            continue
        if explicit[index] > 0 or maximums[index] > 0:
            any_unmet = 1
        tier = tier_of.get(index, _MAX_TIERS)
        if tier >= _MAX_TIERS:
            tier = _MAX_TIERS
        counts[tier] += 1
        sums[tier] += gaps[index]

    key: list[int] = [-any_unmet]
    for tier in range(_MAX_TIERS + 1):
        key.append(-counts[tier])
        key.append(-sums[tier])
    return tuple(key)


def preference_rank(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """偏好维度：按 `priority_stats` 顺序逐个比大小，最后比**封顶后**的六维总和。

    封顶 = 超过上限的部分不计分（DIM 的 `enabledStatsTotal` 同口径）：堆到 200 上限之外
    不该再换来更好的排名，"溢出"也不转移给别的属性。
    """
    return tuple(-value for value in preference_desc(stats, constraints))


def priority_desc(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """优先级值的**降序**形式（`sorted(key=...)` 直接用），长度固定 6。

    给那些"优先级之后还要插自己的成本键"的调用方（`farm_target` 反推待刷件时要先比
    "要改几件"）—— 它们的目标不同，但**属性偏好这一段必须与主口径同源**，
    否则两条路对"哪套属性更好"会给出不同答案。
    """
    priorities = constraints.ordered_priority_indices
    values = [-stats[index] for index in priorities]
    values.extend([0] * (6 - len(values)))
    return tuple(values)


def preference_desc(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """`priority_desc` + 封顶总和的降序（"优先级优先、再比总共堆了多少"）。"""
    return (*priority_desc(stats, constraints), -clamped_total(stats, constraints))


def clamped_total(stats: list[int], constraints: BuildConstraints) -> int:
    """六维总和，每项按"上限（没给就是 200）"封顶。"""
    maximums = constraints.max_vector()
    return sum(
        min(stats[index], maximums[index] if maximums[index] > 0 else 200)
        for index in range(6)
    )


def goodness_key(
    stats: list[int], constraints: BuildConstraints, *, span: int = 0
) -> tuple[int, ...]:
    """搜索/剪枝/候选保留/展示**共用**的排序键（越大越好）。

    `span` > 0 时是"乐观键"：规则维度按"还能补上"来算、偏好维度加上余量。
    剪枝只许用乐观键（保守），落库用真实键。
    """
    rules = rule_rank(stats, constraints, span=span)
    if span:
        optimistic = [
            stats[index] + span for index in range(6)
        ]
        return (*rules, *preference_rank(optimistic, constraints))
    return (*rules, *preference_rank(stats, constraints))


def rank_results(results: list[Any], constraints: BuildConstraints) -> list[Any]:
    """把候选按**同一个** `goodness_key` 排好（best first）。展示层唯一入口。

    为什么要抽出来：以前 `find_build` 末尾自己写了一段 `completion_rate` 打头的排序，
    与求解器堆里那套不是同一个口径 —— 而"两套口径"正是这个阶段要消灭的东西。
    抽成函数之后，守门可以直接断言它**只看** `goodness_key`（`score` 改了也不影响顺序）。
    """
    return sorted(
        results,
        key=lambda result: goodness_key(
            [result.build.stat(name) for name in STAT_NAMES], constraints
        ),
        reverse=True,
    )


def describe_gaps(stats: list[int], constraints: BuildConstraints) -> dict[str, int]:
    """对外用：哪些项没在合法区间里、差多少（键是 `STAT_NAMES`）。"""
    gaps = gap_vector(stats, constraints)
    return {
        STAT_NAMES[index]: gaps[index]
        for index in range(6)
        if gaps[index] > 0
    }


__all__ = [
    "clamped_total",
    "preference_desc",
    "priority_desc",
    "rank_results",
    "describe_gaps",
    "gap_vector",
    "goodness_key",
    "preference_rank",
    "rule_rank",
    "stat_gap",
]
