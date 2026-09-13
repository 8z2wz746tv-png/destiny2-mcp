"""护甲相关的聚合工具分支：单件详情，以及原来的异域护甲/套装效果。

搬出来的原因和武器一样：`assistants.py` 有体积上限（1407 行），新 intent 必须
先把成块的分支挪出去，而不是抬上限。判断逻辑放这里，注册与认参仍在 `assistants.py`。
"""

from __future__ import annotations

from typing import Any

from ._enrichment import community_enrichment
from ._responses import error_response, ok_response
from ..exceptions import DestinyMCPError, InvalidArgumentError
from ..services.armor_payload import armor_payload


async def armor_item(svc: Any, player_name: str, item_instance_id: str) -> dict:
    """`inventory_assistant(intent="item")`：一件护甲的完整载荷。

    与列清单的分工：列表只给"这件 T 几、在哪、多少光等"，要看"现在装了什么模组、
    能量还剩多少、词条是多少、还能不能调谐"就得走这里。
    """
    if not item_instance_id.strip():
        raise InvalidArgumentError(
            "intent=item 需要 item_instance_id（护甲实例 ID）。"
            "先用 intent=get/search 拿到实例 ID 再传进来。"
        )
    raw = await svc["inventory_svc"].get_armor_item(player_name, item_instance_id)
    definition = raw["definition"]
    display = definition.get("displayProperties") or {}

    set_info = None
    set_hash = (definition.get("equippingBlock") or {}).get("equipableItemSetHash", 0)
    if set_hash:
        try:
            bonus = svc["manifest"].get_set_bonus_info(raw["item"].get("itemHash", 0)) or {}
        except DestinyMCPError:
            bonus = {}
        set_info = {
            "hash": set_hash,
            "name": bonus.get("set_name", ""),
            "tiers": [
                {"count": perk.get("required_set_count"), "name": perk.get("perk_name", "")}
                for perk in (bonus.get("perks") or [])
            ],
        }

    manifest = svc["manifest"]
    info = manifest.get_item_info(raw["item"].get("itemHash", 0)) or {}
    payload = armor_payload(
        item_hash=int(raw["item"].get("itemHash", 0)),
        lookup=manifest.get_item_definition,
        instance=raw["instance"],
        sockets=raw["sockets"],
        stats=raw["stats"],
        item_instance_id=item_instance_id,
        location=raw["location"],
        character_id=raw["character_id"],
        power=(raw["instance"].get("primaryStat") or {}).get("value"),
        is_equipped=bool(raw["instance"].get("isEquipped")),
        bucket=raw["bucket"],
        set_info=set_info,
        # 与列清单同源：manifest.get_item_info 里存的是完整 CDN 地址
        icon_url=info.get("icon", ""),
        name=display.get("name", ""),
        name_en=manifest.get_english_name(raw["item"].get("itemHash", 0)),
        item_type_display=definition.get("itemTypeDisplayName", ""),
    )

    warnings: list[str] = []
    stats_block = payload.get("stats") or {}
    warnings.extend(stats_block.get("notes") or [])
    if payload["identity"]["armor_system"] == "legacy":
        warnings.append(
            "这是没有 T 级的老护甲（15 槽布局），没有词条原型与调谐槽，"
            "也不支持词条反推。"
        )
    elif not (payload["identity"].get("gear_tier")):
        warnings.append(payload["identity"].get("gear_tier_note") or "")

    identity = payload["identity"]
    summary = (
        f"{identity['name']}（{identity['slot_display']}，"
        f"{'T' + str(identity['gear_tier']) if identity.get('gear_tier') else '无分级'}，"
        f"{payload['instance'].get('power')} 光等）"
    )
    if identity.get("archetype"):
        summary += f"，词条 {identity['archetype']['name']}"
    return ok_response(
        summary,
        {
            "armor": payload,
            "community_references": community_enrichment(
                svc.get("starside_svc"), identity["name"], "armor"
            ),
        },
        next_actions=[
            "要看某个模组能不能装进这件护甲，先看 armor.sockets 里该槽的 kind 与 energy_cost；"
            "换模组的写入动作在 equip_mod（后续阶段提供）。",
        ],
        warnings=[w for w in warnings if w],
    )


def exotic_armor(svc: Any, character: str, exotic_name: str) -> dict:
    """异域护甲列表或详情（原来在 `assistants.py` 里的分支）。"""
    if exotic_name:
        return ok_response("已读取异域护甲详情。", {
            "armor": svc["manifest_query_svc"].get_exotic_armor_details(exotic_name),
            "community_references": community_enrichment(
                svc.get("starside_svc"), exotic_name, "armor"
            ),
        })
    return ok_response("已读取异域护甲列表。", {
        "armor": svc["manifest_query_svc"].get_exotic_armor_list(character)
    })


def set_bonus(svc: Any, set_bonus_name: str) -> dict:
    """套装效果列表或详情（原来在 `assistants.py` 里的分支）。"""
    if set_bonus_name:
        return ok_response("已读取套装效果。", {
            "set_bonus": svc["set_bonus_svc"].lookup_armor_set(set_bonus_name),
            "community_references": community_enrichment(
                svc.get("starside_svc"), set_bonus_name, "armor"
            ),
        })
    return ok_response("已读取套装效果列表。", {
        "set_bonuses": svc["set_bonus_svc"].list_all_set_bonuses()
    })


__all__ = ["armor_item", "exotic_armor", "set_bonus", "error_response"]
