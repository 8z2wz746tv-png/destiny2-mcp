"""**不跑求解就能判死**的两条早退（`analyze_build` 与它的调用方共用）。

- `oversized_reason`：组合规模超限 → `precision="not_computed"`（**没有**推算上限，不许当成"上限为零"）；
- `set_bonus_shortfall`：指定的套装在这个职业身上**凑不齐** → `precision="exact"`
  （这是数出来的上界，不是猜的）—— 真机 2026-10-06 泰坦要「移民号陨落」4 件套，
  他能穿的只有 3 个部位，而工具当时回的是"各项目标单看都在单项上限之内……"。

放在服务层而不是 `build/`：这两条要同时读**快照**（部位名/件数）与 `BuildAnalysis` 这个响应形状，
而 `build/` 是纯计算层、不认识响应形状。判据本体仍在 `build/`（`analyzer.oversized_reason`、
`set_feasibility.set_bonus_shortfall`），这里只做"合成一份可以原样返回的结论"。
"""

from __future__ import annotations

from typing import Any

from ..build.analyzer import oversized_reason
from ..build.models import BuildAnalysis
from ..build.set_feasibility import set_bonus_shortfall


def early_analysis(snapshot: Any, constraints: Any) -> BuildAnalysis | None:
    """能判死就给一份可直接返回的结论；判不了给 `None`（继续往下跑属性层）。"""
    oversized = oversized_reason(snapshot, constraints)
    if oversized:
        return BuildAnalysis(reason=oversized, precision="not_computed")
    shortfall = set_bonus_shortfall(snapshot, constraints)
    if shortfall:
        return BuildAnalysis(
            reason=shortfall,
            infeasible_by=[shortfall],
            precision="exact",
            assumptions=[
                "这条是按**每个部位最多穿一件**数出来的上限：有这套件（或万能插槽）的部位数"
                "少于你要的件数，所以这次一套都出不来 —— 与属性目标无关。"
            ],
        )
    return None
