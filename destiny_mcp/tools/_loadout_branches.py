"""`loadout_assistant` 的清单/详情两个分支。

搬出 `assistants.py` 的原因与其它 `_*_branches` 一样：那边贴着体积上限，而这里有一段自己的口径 ——

- `list` 只给**清单行**（真机 2026-09-24：以前一次 121 KB / 5 套，截断；共 21 套 ≈ 500 KB，
  每件装备还把插槽数据发三遍），行里保留"能不能执行"与"去哪看详情"；
- `get` 才给完整 `build_template`，并且可以用 `loadout_id` 只取一套；
- "被截断"与"怎么翻页"由这一段讲清楚（列表类响应都要能自证全量）。
"""

from __future__ import annotations

from typing import Any

from ._helpers import dump, positive_or_default
from ._responses import ok_response

# 列表默认给几套（切片在服务层做，工具层只负责把"被截断"讲清楚）
_LOADOUT_DEFAULT_LIMIT = 5


async def list_or_get(
    svc: Any,
    resolved: str,
    *,
    character: str,
    limit: int | None,
    offset: int,
    loadout_id: str,
    intent: str,
) -> dict:
    page_limit = positive_or_default(limit, _LOADOUT_DEFAULT_LIMIT)
    result = await svc["loadout_svc"].get_loadouts(
        resolved, character or None, page_limit, offset, loadout_id=loadout_id
    )
    payload = dump(result)
    cut = bool(payload.get("truncated"))
    warnings: list[str] = []
    if cut:
        warnings.append(
            f"只返回了 {payload['returned_loadouts']} / {payload['total_loadouts']} 套"
            f"（按 limit={page_limit} 截断）；继续读传 offset={payload['next_offset']}，"
            "或用 character 收窄。"
        )

    if intent == "list":
        from ..services.loadout_service import loadout_rows

        rows = loadout_rows(payload["loadouts"])
        return ok_response(
            "已读取玩家已存配装清单"
            + (f"，{payload['returned_loadouts']}/{payload['total_loadouts']} 套。" if cut else "。"),
            {
                "player_name": payload["player_name"],
                "loadouts": rows,
                "total_loadouts": payload["total_loadouts"],
                "returned_loadouts": payload["returned_loadouts"],
                "truncated": cut,
                "next_offset": payload["next_offset"],
                "scope": payload["scope"],
                "list_note": (
                    "这是**清单行**（每套一行）。要看某一套的逐件装备/模组/子职业，"
                    '用 intent="get" + loadout_id；要穿它用 intent="equip_loadout"。'
                ),
            },
            next_actions=[
                '要看某一套的细节：loadout_assistant(intent="get", loadout_id="…")'
                "（id 就在每行的 loadout_id 里）。",
                '要穿某一套：loadout_assistant(intent="equip_loadout", loadout_id="…", '
                "confirmed=true)，写入前先向玩家确认。",
            ],
            warnings=warnings + [
                "这里仅包含玩家本地配装和 Bungie 官方槽位，不代表社区热门或推荐排序。",
            ],
        )

    return ok_response(
        "已读取玩家已存配装（统一模板格式）"
        + (f"，{payload['returned_loadouts']}/{payload['total_loadouts']} 套。" if cut else "。"),
        {
            "player_name": payload["player_name"],
            "loadouts": payload["loadouts"],
            # 列表类响应都要能自证是否全量
            "total_loadouts": payload["total_loadouts"],
            "returned_loadouts": payload["returned_loadouts"],
            "truncated": cut,
            "next_offset": payload["next_offset"],
            "scope": payload["scope"],
            "loadout_format": payload["loadout_format"],
            "community_route": {
                "tool": "build_assistant",
                "arguments": {
                    "intent": "community",
                    "character": character or "",
                },
            },
        },
        warnings=warnings + [
            "这里仅包含玩家本地配装和 Bungie 官方槽位，不代表社区热门或推荐排序。",
        ],
    )
