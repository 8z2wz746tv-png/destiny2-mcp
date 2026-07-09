"""Braytech-style block formatters for MCP tool responses.

These formatters produce structured text blocks that the frontend can
recognize and render as Braytech UI components (progress bars, stat cards,
achievement cards, etc.) via pure CSS — no JS changes needed.

Format convention
-----------------
Each block uses a ``// :: braytech <type> ::`` sentinel line followed by
structured content. The CSS selectors match the sentinel and style the
following table/code/pre block accordingly.

Supported block types:

- ``// :: braytech progress ::``    → Progress bar group
- ``// :: braytech stat-card ::``   → Statistic summary card
- ``// :: braytech triumph ::``     → Triumph/achievement card
- ``// :: braytech inventory ::``   → Item list with inset borders
"""

from __future__ import annotations

from typing import Any


# ── Sentinel line prefix (hidden by CSS) ────────────────────────────────────
_SENTINEL_PREFIX = "// :: braytech"


def progress_block(
    title: str,
    subtitle: str = "",
    items: list[dict[str, Any]] | None = None,
    status: str = "",
    *,
    description: str = "",
) -> str:
    """Render a progress bar group.

    Parameters
    ----------
    title : str
        Section heading (e.g. "Salvation's Edge — Weekly Challenge")
    subtitle : str
        Category label (e.g. "Raid / 剩余 2 天")
    items : list[dict]
        Each dict::
            {"label": str, "current": int, "total": int, "status": str}

        Status may be "done", "tracked", "seasonal", "unredeemed", or empty for normal.
    status : str
        Overall status for the card border: "completed", "gilded", "seasonal",
        "unredeemed", "tracked", or empty.
    description : str
        Optional description text shown below the subtitle.

    Example
    -------
    >>> progress_block(
    ...     "Savathûn's Spire — GM Clear",
    ...     "Nightfall / 本赛季",
    ...     items=[
    ...         {"label": "Encounter Clears", "current": 4, "total": 5},
    ...         {"label": "Challenge Mode", "current": 1, "total": 1, "status": "done"},
    ...     ],
    ...     status="completed",
    ...     description="Complete all encounters on Master difficulty.",
    ... )
    """
    lines = [f"{_SENTINEL_PREFIX} progress ::{f' status={status}' if status else ''}"]
    lines.append(f"title: {title}")
    if subtitle:
        lines.append(f"subtitle: {subtitle}")
    if description:
        lines.append(f"description: {description}")

    if items:
        lines.append("| # | Objective | Progress | Status |")
        lines.append("|---|-----------|----------|--------|")
        for i, it in enumerate(items, 1):
            label = it.get("label", "")
            cur = it.get("current", 0)
            total = it.get("total", 0)
            st = it.get("status", "")
            pct = int(cur / total * 100) if total > 0 else 0
            lines.append(f"| {i} | {label} | {cur}/{total} ({pct}%) | {_status_icon(st)} |")

    return "\n".join(lines)


def stat_card_block(items: list[dict[str, Any]] | None = None) -> str:
    """Render a statistic summary row (3-4 metric cards).

    Parameters
    ----------
    items : list[dict]
        Each dict::
            {"label": str, "value": str, "color": str}

        Color may be "gold", "experience", "seasonal", or empty for default.

    Example
    -------
    >>> stat_card_block([
    ...     {"label": "Triumph Score", "value": "2,847", "color": "gold"},
    ...     {"label": "Collections", "value": "1,892", "color": "experience"},
    ... ])
    """
    lines = [f"{_SENTINEL_PREFIX} stat-card ::"]
    for item in items or []:
        color = item.get("color", "")
        color_attr = f" color={color}" if color else ""
        lines.append(f"- {item.get('label', '')}::{item.get('value', '')}{color_attr}")
    return "\n".join(lines)


def triumph_block(
    title: str,
    subtitle: str = "",
    status: str = "",
    *,
    description: str = "",
    items: list[dict[str, Any]] | None = None,
) -> str:
    """Render a triumph/achievement card.

    Parameters
    ----------
    title : str
        Card title.
    subtitle : str
        Category line (e.g. "Raid · Master").
    status : str
        "completed", "gilded", "seasonal", "unredeemed", "tracked", or empty.
    description : str
        Card description text.
    items : list[dict]
        Progress items, same format as ``progress_block``.

    Example
    -------
    >>> triumph_block(
    ...     "Conqueror ×7",
    ...     "Title · Gilded",
    ...     status="gilded",
    ...     description="Gild the Conqueror title across 7 seasons.",
    ...     items=[{"label": "This Season", "current": 1, "total": 1, "status": "done"}],
    ... )
    """
    lines = [f"{_SENTINEL_PREFIX} triumph ::{f' status={status}' if status else ''}"]
    lines.append(f"title: {title}")
    if subtitle:
        lines.append(f"subtitle: {subtitle}")
    if description:
        lines.append(f"description: {description}")

    if items:
        lines.append("| # | Objective | Progress | Status |")
        lines.append("|---|-----------|----------|--------|")
        for i, it in enumerate(items, 1):
            label = it.get("label", "")
            cur = it.get("current", 0)
            total = it.get("total", 0)
            st = it.get("status", "")
            pct = int(cur / total * 100) if total > 0 else 0
            lines.append(f"| {i} | {label} | {cur}/{total} ({pct}%) | {_status_icon(st)} |")

    return "\n".join(lines)


def inventory_block(
    title: str,
    items: list[str] | None = None,
    *,
    columns: str = "",
) -> str:
    """Render an item list with Braytech-style inset formatting.

    Parameters
    ----------
    title : str
        Section heading (e.g. "猎人 · 动能武器 (8)").
    items : list[str]
        One string per item line.
    columns : str
        Column header hint, e.g. "No, Name, Type, Power, Status".

    Example
    -------
    >>> inventory_block("猎人 · 动能武器 (8)", [
    ...     "1. 千语 | 手炮 | 2010 | [已装备]",
    ...     "2. 命运悲剧 | 手炮 | 2005",
    ... ])
    """
    lines = [f"{_SENTINEL_PREFIX} inventory ::"]
    lines.append(f"title: {title}")
    if columns:
        lines.append(f"columns: {columns}")
    for it in items or []:
        lines.append(f"  {it}")
    return "\n".join(lines)


def weekly_reset_block(
    milestones: list[dict[str, Any]] | None = None,
    reset_time: str = "",
) -> str:
    """Render the weekly reset overview.

    Parameters
    ----------
    milestones : list[dict]
        Each dict::
            {"name": str, "category": str, "activities": list[str]}
    reset_time : str
        Next reset ISO timestamp or human-readable string.

    Example
    -------
    >>> weekly_reset_block(
    ...     milestones=[
    ...         {"name": "The Corrupted", "category": "nightfall", "activities": ["Grandmaster"]},
    ...     ],
    ...     reset_time="2026-07-07T17:00:00Z",
    ... )
    """
    lines = [f"{_SENTINEL_PREFIX} weekly-reset ::"]
    if reset_time:
        lines.append(f"reset: {reset_time}")

    if milestones:
        lines.append("| Category | Milestone | Activities |")
        lines.append("|----------|-----------|------------|")
        for m in milestones:
            cat = m.get("category", "")
            name = m.get("name", "")
            acts = ", ".join(m.get("activities", []) or [])
            lines.append(f"| {_category_label(cat)} | {name} | {acts} |")

    return "\n".join(lines)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _status_icon(status: str) -> str:
    """Map status string to display icon."""
    icons = {
        "done": "✓ 完成",
        "completed": "✓ 完成",
        "gilded": "✦ 镀金",
        "tracked": "◎ 追踪中",
        "unredeemed": "◷ 待兑换",
        "seasonal": "◆ 赛季",
    }
    return icons.get(status, "")


def _category_label(cat: str) -> str:
    """Map category to Chinese label."""
    labels = {
        "nightfall": "日落",
        "trials": "试炼",
        "raid": "突袭",
        "dungeon": "地牢",
        "crucible": "熔炉",
        "gambit": "智谋",
    }
    return labels.get(cat, cat)


# ── Quick wrapper: render any block list ────────────────────────────────────

def render_blocks(*blocks: str) -> str:
    """Concatenate multiple formatted blocks with double-newline separators.

    Example
    -------
    >>> render_blocks(
    ...     stat_card_block([...]),
    ...     progress_block(...),
    ...     triumph_block(...),
    ... )
    """
    return "\n\n".join(b for b in blocks if b)
