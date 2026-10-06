"""`precision` 三态的守门：**「试过没有」与「没算」不能共用一个标签**。

真机 2026-10-06：用户报"同一次无解、口径不稳定"——不带套装约束时给了 `ceiling`、
带上套装约束就变成 `ceiling={}` + `precision="not_computed"`。两次都在如实报告，
但后者被读成"根本没算"，而真相是"每档都跑了、都没有解"。仓库自己的规矩要求分开：
**「没探成」≠「试过没有」**。
"""

from __future__ import annotations

import pytest

from destiny_mcp.build.ladder_evidence import (
    PRECISIONS,
    classify_precision,
    precision_note,
)


@pytest.mark.parametrize(
    ("trials", "expected"),
    [
        # 有样本 → sampled（ceiling 是实采的）
        ([{"ok": True, "reached": {"weapons": 120}}], "sampled"),
        ([{"ok": False}, {"ok": True, "reached": {}}], "sampled"),
        # **每一档都跑过**、都没有解 → no_solution（"试过，没有"）
        ([{"ok": False}, {"ok": False}], "no_solution"),
        # 至少一档没探成 → not_computed（"没算"）
        ([{"ok": False}, {"ok": None, "not_probed": True, "reason": "超时"}], "not_computed"),
        ([{"ok": None, "not_probed": True}], "not_computed"),
        # 一档都没跑 → 不能说"试过没有"
        ([], "not_computed"),
    ],
)
def test_classify_precision_separates_tried_from_not_computed(
    trials: list[dict], expected: str,
) -> None:
    assert classify_precision(trials) == expected


def test_every_precision_has_its_own_note() -> None:
    """三种口径**各有各的话**：共用一句就等于没分开。"""
    notes = {precision: precision_note(precision) for precision in PRECISIONS}
    assert len(set(notes.values())) == len(PRECISIONS), notes
    assert "实采" in notes["sampled"]
    assert "试过" in notes["no_solution"], "『算过但没有解』要说出来"
    assert "没探成" in notes["not_computed"], "『没算』也要说出来"


def test_an_unknown_precision_falls_back_instead_of_exploding() -> None:
    """历史调用传 `precision="exact"`（analyze 那条路）：不许 KeyError。"""
    assert precision_note("exact") == precision_note("no_solution")
    assert PRECISIONS == ("sampled", "no_solution", "not_computed")
