"""0 候选时那句话（`find` 用）。

读的人要能分清四件事：枚举完了真没有满足下限的方案、没搜完/被截断、
**约束本身就不可能**（套装凑不齐 —— 数出来的上界），以及**这次有件要先准备**
（格子满搬不进来 / 与当前金装冲突，ADR-027 起只是附注、不是原因）。
"""

from __future__ import annotations

from typing import Any


def empty_message(search: dict[str, Any] | None, infeasible: list[str] | None = None) -> str:
    """`search` 是求解诊断；`infeasible` 是 `ladder.infeasible_by`（约束凑不齐那几句）。"""
    blockers = (search or {}).get("blockers") or []
    prep = ("另外：这次有件要先准备（" + "；".join(blockers) + "）—— 它**不是**这次 0 候选的原因，"
            "那些件已经算进搜索了（ADR-027）。") if blockers else ""
    if infeasible:
        # 约束判死时**它才是原因**：属性层有数也是白搭（一套都出不来）。
        return "找到 0 个候选配装：" + "；".join(infeasible) + prep
    if search is None or search.get("exhaustive"):
        combos = (search or {}).get("combos")
        scope = f"（枚举了 {combos:,} 套组合）" if isinstance(combos, int) else ""
        return f"找到 0 个候选配装：枚举完了，没有任何一套能满足这些下限{scope}。" + prep
    return (
        "这次**没有搜完**，所以不能说「没有满足下限的方案」"
        f"（截断原因：{search.get('truncated_by') or '未说明'}）。"
        "可以收窄请求后重试，或调高搜索预算。" + prep
    )
