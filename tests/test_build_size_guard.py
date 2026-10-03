"""组合规模闸门：`precision` 必须说真话，两条路（analyze / find）口径一致。"""

from __future__ import annotations

from typing import Any

import pytest

from destiny_mcp import config
from destiny_mcp.build import analyzer
from destiny_mcp.build.analyzer import analyze, ensure_within_combination_limit, too_large_reason
from destiny_mcp.exceptions import BuildTooLargeError


class _Piece:
    def __init__(self, item_hash: int) -> None:
        self.item_hash = item_hash
        # 真模型上的"这次装不装得上"（求解器与闸门都读它）；替身一律"能装"。
        self.execution_blocker = ""


class _Snapshot:
    """只要能被估算函数数件数就行（按真模型的槽位接口给：`get_slot`）。"""

    def __init__(self, size: int) -> None:
        self.helmets = [_Piece(i) for i in range(size)]
        self.gauntlets = [_Piece(i) for i in range(size)]
        self.chests = [_Piece(i) for i in range(size)]
        self.legs = [_Piece(i) for i in range(size)]
        self.class_items = [_Piece(i) for i in range(size)]

    def get_slot(self, slot: str) -> list:
        return getattr(self, slot)


def _constraints() -> Any:
    # 替身要跟真 `BuildConstraints` 的接口一致：`allowed_exotic_hashes` 会读这两个字段
    return type("C", (), {"exotic_hashes": set(), "exotic_hash": None})()


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


def test_指定金装必须真的收窄估算() -> None:
    """真机 2026-10-01：这条收窄**静默失效**过，后果是任何带金装的 `find` 都撞闸门。

    hash 有两套值域：`manifest.search()` 给**有符号**（`黎明副歌` = -1978053128），
    账号快照 `Armor.item_hash` 来自 Bungie API 是**无符号**（3767088557）。
    当时 `estimate_combinations` 用裸比较 `p.item_hash in constraints.exotic_hashes` →
    恒为 False → 金装部位退化成全量件数 → 术士 2.43 亿组合撞闸。
    **而闸门自己给的第一条建议正是"指定一件金装"** —— 建议了却不生效。

    这条测试钉住的是"那句话必须算数"：两种写法喂进来，估算都得收窄到 1 件。
    """
    # 真机这对值：账号那件 `黎明副歌` 是 hash=3767088557，Manifest 侧同一条是 id=-527878739
    # （注意 `黎明副歌` 在 Manifest 里有三个 hash，别拿另一个变体来当夹具——第一版就写错了）
    signed = -527878739
    unsigned = 3767088557  # 同一件的账号侧写法

    for constraint_hash in (signed, unsigned):
        snapshot = _Snapshot(5)
        snapshot.helmets = [_Piece(unsigned), _Piece(1), _Piece(2)]
        constraints = type("C", (), {"exotic_hashes": {constraint_hash}, "exotic_hash": None})()
        total, counts = analyzer.estimate_combinations(snapshot, constraints)

        assert counts[0] == 1, (
            f"喂 {constraint_hash} 时金装部位没有收窄（数了 {counts[0]} 件）—— "
            "有符号/无符号没归一，'指定金装'会静默失效"
        )
        assert total == 625, f"其余四个部位各 5 件，应当是 1×5×5×5×5；实得 {total}"

