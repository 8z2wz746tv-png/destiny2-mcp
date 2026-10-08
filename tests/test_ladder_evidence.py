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
    positive_single_stat,
    precision_note,
    single_stat_note,
    verdict_block,
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


def test_a_blocked_run_never_claims_the_attributes_cannot_be_met() -> None:
    """有件**要先准备**时不许说「这些目标同时满足不了」——那是属性层的结论。

    两代事故都钉在这一条上：
    · 2026-10-06 上午（ADR-022 时代）：执行前提把候选砍光，`precision` 落到 `no_solution`，
      而那次的属性层**根本没算**（`analyze` 被同一条前提短路）——照那句念就是编结论；
    · 2026-10-06 晚（ADR-027 起）：件不再被剔，属性层**照算**了 —— 但"要先准备"仍然
      不等于"属性配不出来"，所以这句话还得单独说。
    """
    blocked = precision_note("no_solution", blocked=True)

    assert blocked != precision_note("no_solution")
    assert "先准备" in blocked, "点出这件事本身（装备前要先腾格 / 先顶下冲突的金装）"
    assert "已经算进" in blocked, "说明那些件**在**这些结论里（ADR-027 起不再剔件）"
    assert "同时满足不了" not in blocked, "绝不替属性层下结论"


@pytest.mark.parametrize(
    ("single_stat", "expected_fragment"),
    [
        ({"weapons": 200, "health": 142}, "同时能达到"),
        ({}, "没算出来"),
    ],
)
def test_single_stat_note_distinguishes_empty_from_zero(
    single_stat: dict, expected_fragment: str,
) -> None:
    assert expected_fragment in single_stat_note(single_stat)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 0 = 没算出来（真机：带套装约束时六项全 0，同一账号本来是 200/142/200/195/180/185）
        ({"weapons": 200, "health": 0, "class_stat": 0}, {"weapons": 200}),
        ({"weapons": 0, "health": 0}, {}),
        (None, {}),
        ({}, {}),
    ],
)
def test_zero_single_stat_entries_are_dropped_not_reported_as_zero(
    raw: dict | None, expected: dict,
) -> None:
    assert positive_single_stat(raw) == expected


def test_verdict_refuses_to_call_an_unfinished_search_infeasible() -> None:
    """没搜完 → `satisfiable=None`：**no limit can create an infeasibility proof**。"""
    covered = verdict_block(covered=True, truncated_by=None, rotated_ok=False)
    unfinished = verdict_block(covered=False, truncated_by="预算", rotated_ok=True)

    assert covered["satisfiable"] is False
    assert unfinished["satisfiable"] is None
    assert "没搜完" in unfinished["evidence"] and "预算" in unfinished["evidence"]
    assert unfinished["solved_after_rotation"] is True
