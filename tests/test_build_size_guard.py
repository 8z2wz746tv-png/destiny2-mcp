"""组合规模闸门：`precision` 必须说真话，两条路（analyze / find）口径一致。"""

from __future__ import annotations

from typing import Any

import pytest

from destiny_mcp import config
from destiny_mcp.build.analyzer import analyze, ensure_within_combination_limit, too_large_reason
from destiny_mcp.exceptions import BuildTooLargeError


class _Piece:
    def __init__(self, item_hash: int) -> None:
        self.item_hash = item_hash


class _Snapshot:
    """只要能被估算函数数件数就行。"""

    def __init__(self, size: int) -> None:
        self.helmets = [_Piece(i) for i in range(size)]
        self.gauntlets = [_Piece(i) for i in range(size)]
        self.chests = [_Piece(i) for i in range(size)]
        self.legs = [_Piece(i) for i in range(size)]
        self.class_items = [_Piece(i) for i in range(size)]


def _constraints() -> Any:
    return type("C", (), {"exotic_hashes": set()})()


def test_analyze_marks_the_early_return_as_not_computed() -> None:
    """超过上限时 `precision` 必须是 not_computed —— 默认的 exact 会骗人。"""
    size = int((config.BUILD_MAX_COMBINATIONS + 1) ** 0.2) + 2
    result = analyze(_Snapshot(size), _constraints())

    assert result.precision == "not_computed"
    assert result.max_possible == {}, "没算就不许编上限"
    assert "组合规模太大" in result.reason


def test_guard_raises_with_the_same_reason() -> None:
    size = int((config.BUILD_MAX_COMBINATIONS + 1) ** 0.2) + 2
    with pytest.raises(BuildTooLargeError) as excinfo:
        ensure_within_combination_limit(_Snapshot(size), _constraints())

    assert "组合规模太大" in str(excinfo.value)
    assert excinfo.value.reason.startswith("这次分析的组合规模太大")


def test_guard_is_silent_within_the_limit() -> None:
    ensure_within_combination_limit(_Snapshot(2), _constraints())  # 不该抛


def test_reason_lists_the_counts_and_the_ways_to_narrow() -> None:
    reason = too_large_reason(243_400_640, [38, 56, 38, 70, 43], 20_000_000)

    assert "38/56/38/70/43" in reason and "243,400,640" in reason
    for hint in ("金装", "farm_target", "DESTINY_BUILD_MAX_COMBINATIONS"):
        assert hint in reason
