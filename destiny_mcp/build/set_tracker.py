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
    """Entry in the min-heap.

    Comparison is based on (enabled_stats_total, stat_mix, stats_total, power),
    all ascending for min-heap ordering (worst first).
    """

    enabled_stats_total: int
    stat_mix: int
    stats_total: int
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

    def could_insert(self, enabled_stats_total: int) -> bool:
        """Check if a set with this total could qualify for the top N.

        O(1) check using the root (worst set) of the min-heap.
        """
        if len(self._heap) < self.capacity:
            return True
        return enabled_stats_total >= self._heap[0].enabled_stats_total

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


def encode_stat_mix(stats: list[int], desired_max_stats: list[int]) -> int:
    """Encode stat values into a 48-bit integer for fast comparison.

    Each stat uses 8 bits (sufficient for 0-200 range), packed in priority order.
    Only non-ignored stats (with max_stat > 0) are included.

    Translated from DIM's encodeStatMix function.

    Args:
        stats: Stat values in stat priority order.
        desired_max_stats: Max desired stat values (0 = ignored stat).

    Returns:
        Encoded integer maintaining lexical ordering.
    """
    encoded = 0
    for i in range(min(len(stats), 6)):
        if desired_max_stats[i] > 0:
            encoded = encoded * 256 + min(stats[i], 255)
    return encoded
