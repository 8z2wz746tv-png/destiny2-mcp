"""Heap-based set tracker for maintaining top-N armor build results.

Translated from DIM's src/app/loadout-builder/process-worker/set-tracker.ts.
Uses a min-heap where the root is the worst of the top-N sets, allowing O(1)
check for whether a new set could qualify and O(log n) insertion.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class HeapEntry:
    """Entry in the min-heap。

    比较只按 `(rank_key, power)`：`rank_key` 是 `build/ranking.goodness_key` 的产物
    （越大越好），`power` 只是最后打平用。min-heap 的 root 是"最差"的那套，
    所以元组越小越差 —— 违规/超上限的方案因为键更小，会自动沉底、被优先挤出去。

    以前这里散着四个键（caps_ok / enabled_stats_total / stat_mix / stats_total），
    其中 `caps_ok` 是 P2 为"上限沉底"临时加的；现在上限已经并进 `rank_key` 的规则维度，
    那个特判就删掉了。
    """

    rank_key: tuple[int, ...]
    power: int
    # payload fields (not compared)
    armor: list[Any] = field(compare=False, default_factory=list)
    stats: list[int] = field(compare=False, default_factory=list)
    bonus_stats: list[int] = field(compare=False, default_factory=list)
    stat_mods: list[int] = field(compare=False, default_factory=list)
    stat_mod_assignments: dict[str, list[int]] = field(compare=False, default_factory=dict)


class HeapSetTracker:
    """Min-heap based tracker that maintains the top N armor sets.

    Translated from DIM's HeapSetTracker class.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._heap: list[HeapEntry] = []

    def could_insert(self, rank_key: tuple[int, ...]) -> bool:
        """这套有没有可能进 top-N。O(1)，看堆顶（最差的那套）。

        **必须是保守的**：宁可多算，也不能把本来进得来的方案挡在外面，
        所以调用方传进来的必须是**乐观键**（`goodness_key(..., span=...)`）：
        它的每一项都不比真实键差，于是"真实键能进"必然意味着"乐观键也能进"。

        **边界是严格大于，不是 `>=`**（P5 修）：`insert()` 本来就是
        `entry <= heap[0] → 拒绝`，所以**打平的候选永远进不了堆** ——
        用 `>=` 放它们过去，只是让它们白跑一遍最贵的校验（重排属性模组 + 可达上限）。
        真机踩过：一条"只有手雷下限 + 超能上限、没有 priority"的请求，
        排序键区分度低、海量组合打平，内核从 8s 级涨到 **359s**；改成严格比较后
        堆里的东西**一模一样**（进堆与否仍然由 `insert` 说了算），只是不再白算。
        """
        if len(self._heap) < self.capacity:
            return True
        return rank_key > self._heap[0].rank_key

    def insert(self, entry: HeapEntry) -> bool:
        """Insert a set into the heap.

        Returns True if inserted, False if not good enough.
        """
        if len(self._heap) < self.capacity:
            heapq.heappush(self._heap, entry)
            return True

        # Check if better than the worst in our top-N
        if entry <= self._heap[0]:
            return False

        # Replace the worst and re-heapify
        heapq.heapreplace(self._heap, entry)
        return True

    def get_armor_sets(self) -> list[HeapEntry]:
        """Get the top N sets sorted best-first."""
        return sorted(self._heap, reverse=True)

    @property
    def total_sets(self) -> int:
        return len(self._heap)
