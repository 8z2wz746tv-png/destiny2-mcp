"""`inventory_assistant` 的分支载荷。

从 `_weapon_branches.py` 搬过来：它是**库存** intent（`inventory_assistant(intent="type")`），
放在武器文件里既占位置又容易让人以为它走武器服务 —— 按仓库分工，分支响应按域放
`_*_branches.py`。数据形状仍然来自 `services.weapon_payload`，这里只挑字段与写话术。
"""

from __future__ import annotations

from typing import Any

from ..services import weapon_payload, weapon_profile
from ..services.weapon_payload import schema_block
from ._responses import ok_response


def inventory_type_payload(
    svc: dict[str, Any],
    result: Any,
    type_name: str,
    location: str = "",
) -> dict[str, Any]:
    """`inventory_assistant(intent="type")`：按类型列**持有**的物品。

    边界（与 `weapon.type` 的分工）：这里只请求库存+实例组件，不请求 305/310，
    所以给的是精简身份块 + 位置/光等，**没有 perk 与可换部件**；要看"能换成什么"
    用 `weapon_assistant(intent="compare", weapon_name=…, item_instance_id=…)`。物品不是武器时原样返回，不硬套武器字段。
    """
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    manifest = svc.get("manifest")
    rows: list[dict[str, Any]] = []
    weapons = 0
    for item in payload.get("items") or []:
        definition = (
            manifest.get_item_definition(item.get("item_hash", 0)) if manifest else None
        )
        if not isinstance(definition, dict) or definition.get("itemType") != 3:
            rows.append({"kind": "item", **item})
            continue
        weapons += 1
        row = weapon_payload.lean_identity(
            manifest,
            definition,
            roll_kind=weapon_profile.roll_kind(definition),
            fallback_name=str(item.get("name") or ""),
        )
        row.update({
            "kind": "weapon",
            "instance_id": item.get("item_instance_id", ""),
            "location": item.get("location", ""),
            "power": item.get("power"),
            "is_equipped": item.get("is_equipped", False),
            "bucket_type": item.get("bucket_type", ""),
            "icon_url": row.get("icon_url") or item.get("icon_url", ""),
        })
        rows.append(row)

    label = type_name or "全部"
    scope = f"（{location}）" if location else ""
    return ok_response(
        f"「{label}」{scope}持有 {len(rows)} 件（其中武器 {weapons} 件）；"
        "这里只有身份与位置；要看某一件的插槽与可换部件用 weapon_assistant(intent=\"compare\", "
        "weapon_name=…, item_instance_id=…)。",
        {
            "result": {
                "query": payload.get("query", type_name),
                "location": location,
                "total": len(rows),
                "returned": len(rows),
                "truncated": False,
                "items": rows,
                "weapon_count": weapons,
            },
            **schema_block(),
        },
    )
