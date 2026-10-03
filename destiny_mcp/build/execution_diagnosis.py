"""执行前提的**叙述**：0 候选时怎么把"装不上"说清楚（判据不在这里）。

判据与每条约束的出路只有一处 —— `execution_feasibility`（读现场、把"这件这次装不装得上 +
怎么办"整句写在 `Armor.execution_blocker` 上）。这个模块做的是另一件事：把这个结论
**叙述**成用户读得懂的诊断，两种颗粒度：

- `full_bucket_reasons`：格子满的**格级**汇总（几条、满到什么程度、这次能挑哪些）——
  `find` / `recommend` 的 0 候选要把"装不上"与"配不出来"分开说，靠的就是它；
- `exotic_conflict_reasons`：**指定的**金装与角色正穿着的那件冲突时点名"是哪两件"。

为什么单独成模块：两者都是"话术"，与判据混在一个贴着体积上限的文件里，改措辞就得动判据
那一侧。确认那一刻的复检不走这里 —— 它直接把 `execution_blocker` 那一句拿去当拒绝理由
（`services/build_execution_guard`），件级的话不必再叙述一遍。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from ..utils.hash_utils import to_unsigned
from .constants import SOLVER_SLOTS
from .execution_feasibility import (
    blocked_pieces,
    exotic_way_out,
    vault_full_way_out,
)

if TYPE_CHECKING:  # 只用于标注：判据模块运行时不能反向依赖这里
    from .execution_feasibility import ExecutionFacts
    from .models import InventorySnapshot

__all__ = [
    "exotic_conflict_reasons",
    "full_bucket_reasons",
]


def full_bucket_reasons(
    snapshot: InventorySnapshot, slots: Iterable[str] | None = None
) -> list[str]:
    """哪些格满了、仓库里有多少件因此搬不进来（一条一句话；没满给空表）。

    `slots` 只报这几格（默认全报）：只想知道"跟这次有关的格"时不把别的满格也念一遍。
    """
    facts: ExecutionFacts = snapshot.execution
    reasons: list[str] = []
    for slot in SOLVER_SLOTS if slots is None else slots:
        if not facts.blocks_vault_piece(slot):
            continue
        blocked = blocked_pieces(snapshot, slot)
        bucket = facts.buckets[slot]
        reasons.append(
            f"{facts.character_label}的{facts.label(slot)}格已经满了"
            f"（{bucket.used}/{bucket.capacity}），仓库里那 {len(blocked)} 件搬不进来"
            "（上游会回 DestinyNoRoomInDestination）—— 这次只从他身上/背包里已有的"
            f"{facts.label(slot)}里挑；要用仓库那几件：{vault_full_way_out(facts, slot)}。"
        )
    return reasons


def exotic_conflict_reasons(
    snapshot: InventorySnapshot,
    exotic_hashes: Iterable[int],
    name_of: Callable[[int], str],
) -> list[str]:
    """**指定的**金装与角色正穿着的另一件金装冲突时，说清是哪两件（空表 = 不冲突）。

    只看"请求里指定的金装"：没指定金装时求解器会自己避开冲突的部位（那是它的选择，
    不是用户被挡住的理由），只有用户点名要的那件被挡住才需要解释。
    """
    facts: ExecutionFacts = snapshot.execution
    if not facts.worn_exotic_slot:
        return []
    # 归一在这里做：快照里的 `item_hash` 是无符号（Bungie 给的原值），而 `exotic_hashes`
    # 可能来自 `manifest.search`（有符号）。裸比会**静默失效** —— 这个坑在本仓库复现过
    # 多次（见 `build/constraints.allowed_exotic_hashes`）。
    wanted = {to_unsigned(int(item_hash)) for item_hash in exotic_hashes if item_hash}
    if not wanted:
        return []
    for slot in SOLVER_SLOTS:
        if slot == facts.worn_exotic_slot:
            continue
        for armor in snapshot.get_slot(slot):
            if to_unsigned(int(armor.item_hash)) not in wanted:
                continue
            return [
                f"指定的金装「{name_of(armor.item_hash) or armor.name}」是"
                f"{facts.label(slot)}部位的，"
                f"而{facts.character_label}当前穿着异域「{facts.worn_exotic_name}」"
                f"（{facts.label(facts.worn_exotic_slot)}）：同类异域只能装备一件，"
                "一次性装备会撞 1641 DestinyItemUniqueEquipRestricted（真机实测：整批回滚、"
                f"0 颗模组落地）。出路：{exotic_way_out(facts)}。"
            ]
    return []
