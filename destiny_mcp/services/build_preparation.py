"""每套方案「**要先准备什么**」的投影（ADR-027 修订 ADR-022）。

求解器现在把带执行前提的件也算进池（格子满搬不进来的仓库件、与身上金装冲突的异域），
所以交出去的方案必须自己带一句"这次要先准备什么" —— 否则交出去的是一份
**确认了也写不进去**的方案，那正是 ADR-022 当初要避免的事。

判据不在这里：还是 `build/execution_feasibility` 写在件上的那一句（`Armor.execution_blocker`），
这里只做两件纯投影的事：

- `preparation_index`：把整份快照里"要先准备"的件收成 `instance_id → 那一句`；
- `annotate_preparation`：给每套方案挂 `requires_preparation`（用不到那些件的不挂）。

分析侧那份（`annotate_analysis`）同理：执行前提**不再短路**属性层结论，但结论要带着这条标注。
"""

from __future__ import annotations

from typing import Any

from ..build.constants import SOLVER_SLOTS


def preparation_index(snapshot: Any) -> dict[str, str]:
    """`instance_id → 这件要先准备什么`；空表 = 这次没有要准备的件。"""
    return {
        armor.item_instance_id: armor.execution_blocker
        for slot in SOLVER_SLOTS
        for armor in snapshot.get_slot(slot)
        if armor.execution_blocker
    }


def _instance_id(item: Any) -> str:
    """模型形态与 dict 形态都取得到实例 ID（缺了给空串，**不编**）。"""
    if isinstance(item, dict):
        return str(item.get("item_instance_id") or "")
    return str(getattr(item, "item_instance_id", None) or "")


def _pieces_of(result: Any) -> list[Any]:
    """这套方案的五件（`BuildResult.build.items` 或它的 dict 形态）。"""
    build = result.get("build") if isinstance(result, dict) else getattr(result, "build", None)
    if isinstance(build, dict):
        return list(build.get("items") or [])
    return list(getattr(build, "items", None) or [])


def annotate_preparation(results: list[Any], index: dict[str, str]) -> None:
    """给用得上那些件的方案挂 `requires_preparation`（就地写；其余方案不动）。

    两种形态都吃：`find_build` 交回来的是 `BuildResult`（pydantic），投影那侧读的是它的
    dict 形态 —— 只认一种就会在另一种上炸（2026-10-06 实测：第一版只写 `result.get(...)`，
    四条"确认那一刻复检"的用例直接 `AttributeError`）。
    """
    if not index:
        return
    for result in results:
        needs = sorted(
            {index[iid] for iid in map(_instance_id, _pieces_of(result)) if iid in index}
        )
        if not needs:
            continue
        if isinstance(result, dict):
            result["requires_preparation"] = needs
        else:
            result.requires_preparation = needs


def annotate_analysis(analysis: Any, blocked_by: list[str]) -> Any:
    """分析结论带上前提标注**并原样返回**（前提不再是"为什么没有解"，而是"要先准备什么"）。"""
    if blocked_by:
        analysis.blocked_by = blocked_by
        analysis.assumptions = [
            *analysis.assumptions,
            "这次可用的件里有需要先准备的（见 blocked_by）：它们**已经算进**这些上限，"
            "但装备前要先腾格子 / 先顶下冲突的金装。",
        ]
    return analysis
