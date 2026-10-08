"""候选里带**执行前提**时的那句话（ADR-027 修订 ADR-022）。

判据不在这个模块：件上的那一句由 `services/build_preparation` 挂到候选行上
（`requires_preparation`）。这里只负责把它变成"摘要后面加的一句 + warnings 一份"，
让模型不可能漏掉 —— 带前提的候选也进池之后，漏掉这句话的后果就是
ADR-022 当初要避免的那种"确认了才发现装不上"。
"""

from __future__ import annotations

from typing import Any


def preparation_note(builds: list[Any]) -> tuple[str, list[str]]:
    """返回 `(加在摘要后面的一句, warnings)`；没有要准备的件时返回 `("", [])`。"""
    needs = sorted({
        sentence
        for row in builds
        if isinstance(row, dict)
        for sentence in (row.get("requires_preparation") or [])
    })
    if not needs:
        return "", []
    count = sum(
        1 for row in builds if isinstance(row, dict) and row.get("requires_preparation")
    )
    return (
        f"其中 {count} 套用到了**要先准备**的件（见各自的 requires_preparation）：装备前先腾出"
        "格子 / 先顶下冲突的金装；确认那一刻还会复检，没准备好会被拒绝、不会去撞上游。",
        needs,
    )
