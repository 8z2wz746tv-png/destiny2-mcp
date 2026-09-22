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

**性能口径（P5 补）**：`goodness_key` 是**按组合**调用的（真机那条 628 万组合的用例里调了
286 万次），所以它**不许**在里面碰 `BuildConstraints` —— 那是个 pydantic 模型，每次取
`as_vector()`/`max_vector()`/`ordered_priority_indices` 都要重建列表（cProfile：光
`max_vector()` 就被调了 857 万次、占 36 秒，见计划文档 P5 记录）。约束里那些量在整个求解
过程中是常量，一律先用 `vectors_for(constraints)` 抽成 `RankingVectors`（纯元组），
再往内层传。公开函数的签名保持不变（调用方照旧传 constraints），热路径走 `vectors=` 参数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import STAT_NAMES
from .models import BuildConstraints

#: 最多能表达 6 个优先层；多余的一律并进"普通"层（不会发生，`ordered_priority_indices` 已去重）。
_MAX_TIERS = 6
#: 键长 = 布尔位 + 每层(个数, 缺口) + 普通层(个数, 缺口) + 偏好(6 项优先级值 + 封顶总和)
_KEY_LENGTH = 1 + 2 * (_MAX_TIERS + 1) + 6 + 1


@dataclass(frozen=True, slots=True)
class RankingVectors:
    """`BuildConstraints` 的派生向量：**一次算好、按组合复用**。

    里面全是普通元组/整数，取值不再经过 pydantic（实测这是排序热路径上最大的单项开销）。
    """

    minimums: tuple[int, ...]
    maximums: tuple[int, ...]
    priorities: tuple[int, ...]
    #: 每项属于哪一层（没进 `priority_stats` 的是 `_MAX_TIERS`，即"普通层"）
    tier_of: tuple[int, ...]
    #: 这一项有没有**显式规则**（下限或上限）—— 判"这算不算违规"用
    has_rule: tuple[bool, ...]


def vectors_for(constraints: BuildConstraints) -> RankingVectors:
    """把约束里的排序用向量抽出来（**每次求解调一次**，别放进按组合的热路径）。"""
    minimums = tuple(constraints.as_vector())
    maximums = tuple(constraints.max_vector())
    priorities = tuple(constraints.ordered_priority_indices)
    tier_of = [_MAX_TIERS] * len(STAT_NAMES)
    for tier, index in enumerate(priorities):
        if 0 <= index < len(tier_of):
            tier_of[index] = tier if tier < _MAX_TIERS else _MAX_TIERS
    return RankingVectors(
        minimums=minimums,
        maximums=maximums,
        priorities=priorities,
        tier_of=tuple(tier_of),
        has_rule=tuple(
            minimums[index] > 0 or maximums[index] > 0
            for index in range(len(STAT_NAMES))
        ),
    )


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


def _gap_vector(
    stats: list[int], vectors: RankingVectors, *, span: int = 0
) -> list[int]:
    """六项各自的缺口（内层实现：只碰 `RankingVectors`）。"""
    gaps = [0] * len(STAT_NAMES)
    for index in range(len(STAT_NAMES)):
        value = stats[index] + span if span else stats[index]
        minimum = vectors.minimums[index]
        gap = minimum - value if minimum > value else 0
        if span == 0:
            maximum = vectors.maximums[index]
            if maximum > 0 and value > maximum:
                gap += value - maximum
        gaps[index] = gap
    return gaps


def gap_vector(stats: list[int], constraints: BuildConstraints, *, span: int = 0) -> list[int]:
    """六项各自的缺口。`span` 是"这套还可能再涨多少"的乐观余量（剪枝用）。

    给了 `span` 时按**每项都能拿到全部余量**来估 —— 这是上界（不可能比这更好），
    所以用它剪枝是保守的：宁可多算，不会把还能达标的方案剪掉。
    """
    return _gap_vector(stats, vectors_for(constraints), span=span)


def _rule_rank(
    stats: list[int], vectors: RankingVectors, *, span: int = 0
) -> tuple[int, ...]:
    """规则维度（内层实现，口径见 `rule_rank`）。"""
    counts = [0] * (_MAX_TIERS + 1)
    sums = [0] * (_MAX_TIERS + 1)
    any_unmet = 0
    for index in range(len(STAT_NAMES)):
        value = stats[index] + span if span else stats[index]
        minimum = vectors.minimums[index]
        gap = minimum - value if minimum > value else 0
        if span == 0:
            maximum = vectors.maximums[index]
            if maximum > 0 and value > maximum:
                gap += value - maximum
        if gap <= 0:
            continue
        if vectors.has_rule[index]:
            any_unmet = 1
        tier = vectors.tier_of[index]
        counts[tier] += 1
        sums[tier] += gap

    key: list[int] = [-any_unmet]
    for tier in range(_MAX_TIERS + 1):
        key.append(-counts[tier])
        key.append(-sums[tier])
    return tuple(key)


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
    return _rule_rank(stats, vectors_for(constraints), span=span)


def _priority_values(stats: list[int], vectors: RankingVectors) -> tuple[int, ...]:
    values = [-stats[index] for index in vectors.priorities]
    values.extend([0] * (6 - len(values)))
    return tuple(values)


def _preference_key(stats: list[int], vectors: RankingVectors) -> tuple[int, ...]:
    """偏好段（`goodness_key` 用）：优先级值**越大越好**，最后是封顶总和。"""
    values = [stats[index] for index in vectors.priorities]
    values.extend([0] * (6 - len(values)))
    return (*values, _clamped_total(stats, vectors.maximums))


def preference_rank(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """偏好维度：按 `priority_stats` 顺序逐个比大小，最后比**封顶后**的六维总和。

    封顶 = 超过上限的部分不计分（DIM 的 `enabledStatsTotal` 同口径）：堆到 200 上限之外
    不该再换来更好的排名，"溢出"也不转移给别的属性。
    """
    return _preference_key(stats, vectors_for(constraints))


def priority_desc(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """优先级值的**降序**形式（`sorted(key=...)` 直接用），长度固定 6。

    给那些"优先级之后还要插自己的成本键"的调用方（`farm_target` 反推待刷件时要先比
    "要改几件"）—— 它们的目标不同，但**属性偏好这一段必须与主口径同源**，
    否则两条路对"哪套属性更好"会给出不同答案。
    """
    return _priority_values(stats, vectors_for(constraints))


def preference_desc(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    """`priority_desc` + 封顶总和的降序（`preference_rank` 的逐项相反数）。

    给 `sorted(key=...)`（升序）用的形式；`goodness_key` 里用的是 `preference_rank`。
    """
    return tuple(-value for value in preference_rank(stats, constraints))


def _clamped_total(stats: list[int], maximums: tuple[int, ...]) -> int:
    total = 0
    for index in range(len(STAT_NAMES)):
        value = stats[index]
        cap = maximums[index] if maximums[index] > 0 else 200
        total += value if value <= cap else cap
    return total


def clamped_total(stats: list[int], constraints: BuildConstraints) -> int:
    """六维总和，每项按"上限（没给就是 200）"封顶。"""
    return _clamped_total(stats, vectors_for(constraints).maximums)


def goodness_key(
    stats: list[int],
    constraints: BuildConstraints,
    *,
    span: int = 0,
    vectors: RankingVectors | None = None,
) -> tuple[int, ...]:
    """搜索/剪枝/候选保留/展示**共用**的排序键（越大越好）。

    `span` > 0 时是"乐观键"：规则维度按"还能补上"来算、偏好维度加上余量。
    剪枝只许用乐观键（保守），落库用真实键。

    `vectors` 给按组合调用的热路径用：调用方先用 `vectors_for(constraints)` 算一次，
    循环里一直传进来，别让每一次调用都去读 pydantic 模型（P5 实测差好几倍）。
    """
    vec = vectors if vectors is not None else vectors_for(constraints)
    rules = _rule_rank(stats, vec, span=span)
    if span:
        optimistic = [stats[index] + span for index in range(len(STAT_NAMES))]
        return (*rules, *_preference_key(optimistic, vec))
    return (*rules, *_preference_key(stats, vec))


def rank_results(results: list[Any], constraints: BuildConstraints) -> list[Any]:
    """把候选按**同一个** `goodness_key` 排好（best first）。展示层唯一入口。

    为什么要抽出来：以前 `find_build` 末尾自己写了一段 `completion_rate` 打头的排序，
    与求解器堆里那套不是同一个口径 —— 而"两套口径"正是这个阶段要消灭的东西。
    抽成函数之后，守门可以直接断言它**只看** `goodness_key`（`score` 改了也不影响顺序）。
    """
    vectors = vectors_for(constraints)
    return sorted(
        results,
        key=lambda result: goodness_key(
            [result.build.stat(name) for name in STAT_NAMES], constraints, vectors=vectors
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
    "RankingVectors",
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
    "vectors_for",
]
