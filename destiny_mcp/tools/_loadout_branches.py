"""`loadout_assistant` 的清单/详情两个分支。

搬出 `assistants.py` 的原因与其它 `_*_branches` 一样：那边贴着体积上限，而这里有一段自己的口径 ——

- `list` 只给**清单行**（真机 2026-09-24：以前一次 121 KB / 5 套，截断；共 21 套 ≈ 500 KB，
  每件装备还把插槽数据发三遍），行里保留"能不能执行"与"去哪看详情"；
- `get` 才给完整 `build_template`，并且可以用 `loadout_id` 只取一套；
- "被截断"与"怎么翻页"由这一段讲清楚（列表类响应都要能自证全量）。
"""

from __future__ import annotations

from typing import Any

from ._helpers import positive_or_default
from ._responses import confirmation_required_response, dump, ok_response

# 列表默认给几套（切片在服务层做，工具层只负责把"被截断"讲清楚）
_LOADOUT_DEFAULT_LIMIT = 5


async def save_preview(svc: Any, resolved: str, character: str, name: str) -> dict:
    """`save` 的确认信封里那块"这次存什么"。

    真机 2026-09-24：`save` 的确认只有 `{loadout_id:"", character, slot_number, name}`——
    没有一处能看出"存下来是哪套"，玩家只能凭名字点头。预览取自账号（与 save 同一段采集），
    是可选数据：读不到就给 `available=false` + reason，确认本身照常能给。
    """
    from ..services.armor_payload import SLOT_DISPLAY

    preview = await svc["loadout_svc"].describe_save(resolved, character, name)
    for item in preview.get("armor") or []:
        item["slot_display"] = SLOT_DISPLAY.get(item.get("slot", ""), "")
    return preview


async def confirm_write(
    svc: Any,
    resolved: str,
    *,
    intent: str,
    character: str,
    loadout_id: str,
    slot_number: int,
    name: str,
    notes: str,
    name_hash: int | None,
    icon_hash: int | None,
    color_hash: int | None,
) -> dict:
    """`loadout_assistant` 写入前的确认信封：只装这个 intent 真会用到的东西。

    以前所有写入 intent 共用一坨 `{loadout_id, character, slot_number, name}`：`save` 也带着
    它根本不读的 `loadout_id`/`slot_number`，而 `save` 最该说清的"这次存的是哪一套"没有。
    """
    payload: dict[str, Any] = {"intent": intent}
    if intent == "save":
        payload.update({"character": character, "name": name, "notes": notes})
        payload["save_preview"] = await save_preview(svc, resolved, character, name)
    elif intent in {"delete", "equip_loadout"}:
        # 这两个不读 character（delete 按 id、equip 用配装自己的职业），别塞空键进信封
        payload["loadout_id"] = loadout_id
    elif intent in {"snapshot_official", "update_official_identifiers", "clear_official"}:
        payload.update({
            "character": character,
            "slot_number": slot_number,
            "name_hash": name_hash, "icon_hash": icon_hash, "color_hash": color_hash,
        })
    return confirmation_required_response(intent, payload)


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
