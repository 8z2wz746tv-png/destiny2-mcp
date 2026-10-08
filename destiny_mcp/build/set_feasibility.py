"""套装**凑不齐**的确定性判定。

求解器只在每个组合上验 `set_count + wildcard >= set_bonus_count`，验不过就丢；
丢到 0 候选之后，ladder 那套话术是**面向属性**写的 —— 于是"你这套凑不齐"被念成了
"各项目标单看都在单项上限之内……"（真机 2026-10-06 泰坦：「移民号陨落」4 件套，
他能穿的只有 3 个部位：头盔 1 / 腿甲 2 / 臂铠 4，胸甲与职业护甲各 0）。

判定用的是**上界**：每个部位最多穿一件，所以"有这套件的部位数（万能插槽也算一个部位）"
就是这次能凑到的最大件数。`上限 < 要求的件数` 因此是"数学上不可能"，可以直接说死；
反过来（上限够）**一律不下结论** —— 那是属性/金装/优先级的事，交给 ladder。

部位名走 `snapshot.execution.label`（与 `execution_feasibility` 同一份，读不到时退回槽位键）
—— 不在这里再抄一张中文表。
"""

from __future__ import annotations

from typing import Any

from .constants import SOLVER_SLOTS


def set_bonus_shortfall(snapshot: Any, constraints: Any) -> str | None:
    """这套在这个职业上**凑不齐** → 一句能执行的结论；凑得齐、或没要求套装 → `None`。"""
    need = int(getattr(constraints, "set_bonus_count", 0) or 0)
    want = getattr(constraints, "set_bonus_hash", None)
    if need <= 0 or not want:
        return None

    facts = snapshot.execution
    covered: list[str] = []
    wildcard: list[str] = []
    for slot in SOLVER_SLOTS:
        pieces = snapshot.get_slot(slot)
        # ⚠️ 直接取属性，**不要** `getattr(..., None)`：这两个名字分属两个类
        # （`Armor.set_bonus_hash` vs `ProcessItem.set_bonus`），2026-10-06 真机上就是
        # 名字写错 + `getattr` 默认值 = 覆盖率**静默数成 0**，工具因此回了一句
        # "这个职业一个部位都没有"（真实是 3 个部位）。写错就该当场炸。
        if any(armor.set_bonus_hash == want for armor in pieces):
            covered.append(slot)
        elif any(armor.has_set_bonus_mod_socket for armor in pieces):
            wildcard.append(slot)

    most = len(covered) + len(wildcard)
    if most >= need:
        return None

    have = "、".join(facts.label(slot) for slot in covered) or "一个都没有"
    extra = (
        f"，另有 {len(wildcard)} 个部位靠万能插槽顶得上一件"
        f"（{'、'.join(facts.label(slot) for slot in wildcard)}）"
        if wildcard
        else ""
    )
    missing = "、".join(
        facts.label(slot) for slot in SOLVER_SLOTS if slot not in covered and slot not in wildcard
    )
    return (
        f"这套**凑不出 {need} 件**：这个职业身上只有 {most} 个部位有它（{have}）{extra}，"
        f"缺 {missing}。每个部位只能穿一件，所以这是**数出来的上限**，不是猜的 —— "
        "换一套凑得齐的，或先拿到缺的那几个部位。"
    )
