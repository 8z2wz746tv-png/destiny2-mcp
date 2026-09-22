"""配装的一个**展示用**数字：封顶后的六维总和。

它以前是"多目标加权评分"（达标率 ×1000 + 六维总和 ×0.1 − 超出目标每点 ×0.5），
并且被当成排序键用 —— 于是"多堆 10 点手雷"在评分里是**负收益**，求解器只交及格卷。
现在排序口径收到 `build/ranking.goodness_key` 一处，这个数**只用于展示**：
它是"这套配装总共堆了多少点"（每项按上限封顶，溢出不计），读者一眼能懂，
也不会再有任何权重偷偷替用户做取舍。

为什么保留这个模块而不是就地删掉：`BuildResult.score` 是对外字段，
它的含义必须**能一句话讲清**。封顶总和符合这个要求；加权总分不符合。
"""

from __future__ import annotations

from ..logging_config import get_logger
from .constants import STAT_NAMES
from .models import BuildCandidate, BuildConstraints
from .ranking import clamped_total

logger = get_logger(__name__)


def score(candidate: BuildCandidate, constraints: BuildConstraints) -> float:
    """封顶后的六维总和（越大 = 这套堆得越多）。**不是排序键**。

    排序由 `build/ranking.goodness_key` 决定：先看有没有违规、再按优先级逐层比
    未达标个数与缺口、最后才比这个总和。
    """
    stats = [candidate.stat(name) for name in STAT_NAMES]
    total = clamped_total(stats, constraints)
    logger.debug("Score (display only): clamped total=%d", total)
    return float(total)


__all__ = ["score"]
