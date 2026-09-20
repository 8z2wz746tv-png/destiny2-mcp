"""`weapon_assistant(intent="patterns")` 的载荷：图样总览 + 列表行 + 社区来源。

三件事分三层，响应里也要看得出层次：

- **账号事实**：`counts` 与每行的 `progress` / `status`（组件 900，就是游戏里那条「图样进度 4/5」）；
- **Manifest 事实**：`need`（要萃取几次）、`weapon_type` / `group` / `tier`、`catalog_total`；
- **社区参考**：`data.sources` 与每行的 `source`（Starside「锻造武器来源」，带页面更新时间与
  `trust=untrusted_reference`）。

两条话术红线：

1. **「未开始」是没有记录，不是 0/5** —— 行里 `progress` 是 `null`，摘要与警告里都不许写成"进度 0"，
   也不能说成"这把没有来源/没有图样"；
2. 来源清单没收录某一行时，只说**这份本地资料没有这一行**，不说这把武器没有来源。
"""

from __future__ import annotations

from typing import Any

from ..services.pattern_service import (
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_UNLOCKED,
)
from ._responses import ok_response
# 摘要在没有筛选时最多点名几把进行中的武器（再多就该看列表了）。
_IN_PROGRESS_NAMES = 3


async def patterns_branch(
    svc: dict[str, Any],
    player_name: str,
    weapon_name: str,
    weapon_type: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """`intent="patterns"` 的入口：调服务、拼载荷；异常交给工具层的 `handle_tool_error`。"""
    result = await svc["pattern_svc"].patterns(
        player_name,
        weapon_name=weapon_name,
        weapon_type=weapon_type,
        limit=limit,
        offset=offset,
    )
    return patterns_payload(result)


def _progress_text(row: dict[str, Any]) -> str:
    need = row.get("need")
    progress = row.get("progress")
    if progress is None or not need:
        return f"{row['status']}"
    return f"{row['status']} {progress}/{need}"


def _summary(result: dict[str, Any]) -> str:
    counts = result["counts"]
    rows = result["rows"]
    if result["variant_of"]:
        row = rows[0]
        return (
            f"「{result['variant_of']}」是变体，图样挂在基础版上："
            f"「{row['name']}」{_progress_text(row)}。"
        )
    if result["candidates"]:
        names = "、".join(item["name"] for item in result["candidates"])
        return f"有多把图样匹配：{names}（见 candidates；可把名字写全再问一次）。"
    if result["filtered"]:
        if not rows:
            if result["available_types"]:
                return (
                    "按这个条件没有筛到图样。可用类型："
                    + "、".join(result["available_types"])
                    + "。"
                )
            return f"图样里没有找到匹配的可锻造武器（图鉴共 {result['catalog_total']} 条）。"
        if len(rows) == 1:
            row = rows[0]
            text = f"「{row['name']}」：{_progress_text(row)}"
            if row["status"] == STATUS_UNLOCKED:
                return text + "（图样已完成，可以塑形）。"
            if row["status"] == STATUS_NOT_STARTED:
                return text + "（账号里没有这条图样记录，游戏里是未解锁）。"
            left = (row.get("need") or 0) - (row.get("progress") or 0)
            return text + f"，还差 {left} 次深视萃取。"
        return (
            f"筛出 {counts['total']} 条图样：已解锁 {counts['unlocked']}、"
            f"进行中 {counts['in_progress']}、未开始 {counts['not_started']}。"
        )
    doing = [row for row in result["rows"] if row["status"] == STATUS_IN_PROGRESS]
    detail = ""
    if doing:
        named = "、".join(
            f"{row['name']} {row['progress']}/{row['need']}" for row in doing[:_IN_PROGRESS_NAMES]
        )
        detail = f"进行中：{named}"
        if len(doing) > _IN_PROGRESS_NAMES:
            detail += f" 等 {len(doing)} 把"
        detail += "；"
    return (
        f"锻造图样：已解锁 {counts['unlocked']} / {counts['total']}；"
        f"{detail}未开始 {counts['not_started']} 把。"
    )


def _sources_block(result: dict[str, Any]) -> dict[str, Any]:
    sources = result["sources"]
    total = result["total"]
    block: dict[str, Any] = {
        "available": bool(sources.get("available")),
        "page": sources.get("page") or {},
        "matched": sources.get("matched_count") or 0,
        "total": total,
    }
    if not block["available"]:
        block["note"] = "这次没有读到本地「锻造武器来源」资料；图样进度不受影响。"
    else:
        block["note"] = (
            f"本地资料覆盖 {block['matched']}/{total} 条；"
            "没有对应行只说明这份清单没收录，不代表这把武器没有来源。"
        )
    return block


def patterns_payload(result: dict[str, Any]) -> dict[str, Any]:
    counts = result["counts"]
    items = [
        {
            "name": row["name"],
            "weapon_type": row["weapon_type"],
            "group": row["group"],
            "tier": row["tier"],
            "need": row["need"],
            "progress": row["progress"],
            "status": row["status"],
            "item_hash": row["item_hash"],
            "source": row["source"],
        }
        for row in result["rows"]
    ]
    data: dict[str, Any] = {
        "counts": counts,
        "catalog_total": result["catalog_total"],
        "by_group": result["by_group"],
        "patterns": {
            "total": result["total"],
            "returned": result["returned"],
            "offset": result["offset"],
            "next_offset": result["next_offset"],
            "items": items,
        },
        "sources": _sources_block(result),
        "read": result["read"],
        "note": (
            "图样按武器算：同一把的（专家）/（失时）/（痛苦）变体不单列图样，"
            "图样记录挂在基础版上（直接问变体名会指回基础版）。"
        ),
    }
    if result["available_types"]:
        data["available_types"] = result["available_types"]

    warnings = list(result["sources"].get("warnings") or [])
    if any(row["status"] == STATUS_NOT_STARTED for row in result["rows"]):
        warnings.append(
            "「未开始」= 账号里没有这条图样记录（游戏里显示未解锁），`progress` 为 null；"
            "这不能读成「进度 0」，也不能读成「这把没有图样」。"
        )
    next_actions: list[dict[str, Any] | str] = []
    if result["candidates"]:
        next_actions.append("把 candidates 里的名字报给玩家，让他确认是哪一把；别自己挑一把。")
    elif not result["filtered"]:
        next_actions.append({
            "label": "看单把的进度与需求",
            "tool": "weapon_assistant",
            "arguments": {"intent": "patterns", "weapon_name": "累积救赎"},
        })
        next_actions.append(
            "图样进度只在游戏里涨（深视萃取）；这里只读，不改账号。"
        )
    return ok_response(_summary(result), data, warnings=warnings, next_actions=next_actions)
