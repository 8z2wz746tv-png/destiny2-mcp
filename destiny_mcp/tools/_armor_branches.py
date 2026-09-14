"""护甲相关的聚合工具分支：单件详情，以及原来的异域护甲/套装效果。

搬出来的原因和武器一样：`assistants.py` 有体积上限（1407 行），新 intent 必须
先把成块的分支挪出去，而不是抬上限。判断逻辑放这里，注册与认参仍在 `assistants.py`。
"""

from __future__ import annotations

from typing import Any

from ._enrichment import community_enrichment
from ._responses import confirmation_required_response, error_response, ok_response
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
            "要换模组用 inventory_assistant(intent=\"equip_mod\", item_instance_id=…, mod_name=…, "
            "character=…)，确认后才写。",
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


_STAT_LABELS = {
    "weapons": "武器",
    "health": "生命",
    "class_stat": "职业",
    "grenade": "手雷",
    "super_stat": "超能",
    "melee": "近战",
}


def _mod_echo(plan: dict[str, Any]) -> str:
    """确认请求里给人看的那一行。"""
    from ..services.armor_payload import SLOT_DISPLAY

    tier = f"T{plan['gear_tier']}" if plan.get("gear_tier") else "无分级"
    slot = SLOT_DISPLAY.get(plan.get("slot", ""), plan.get("slot", ""))
    old = plan["from"].get("name") or "空"
    new = plan["to"].get("name") or "空"
    bonus = plan["to"].get("stat_bonus") or {}
    bonus_text = "".join(
        f"（+{value} {_STAT_LABELS.get(key, key)}）" for key, value in bonus.items()
    )
    energy = plan["energy"]
    return (
        f"{plan['item_name']}（{slot}，{plan.get('power')}，{tier}）"
        f"槽 {plan['socket_index']}：{old} → {new}{bonus_text}，"
        f"能量 {energy['used']}/{energy['capacity']} → {energy['after']}/{energy['capacity']}"
    )


async def equip_preview(svc: Any, player_name: str, canonical_build: dict[str, Any]) -> list[dict[str, Any]]:
    """装备确认时的逐件预览：光等、槽位、能量、每个模组槽"现在有什么 → 要装什么"。

    放在确认请求里**与 `canonical_build` 并列**，不动 canonical 本身：那份载荷要能被
    原样回传，往里塞展示字段会让回传被 `ExecutableBuild` 拒掉。
    """
    from ..services.armor_payload import SLOT_DISPLAY, socket_kind, slot_key_from_solver

    items = canonical_build.get("items") or []
    instance_ids = [str(item.get("item_instance_id") or "") for item in items]
    raw_by_id = await svc["inventory_svc"].get_armor_items(player_name, instance_ids)
    manifest = svc["manifest"]

    preview: list[dict[str, Any]] = []
    for item in items:
        instance_id = str(item.get("item_instance_id") or "")
        raw = raw_by_id.get(instance_id) or {}
        definition = raw.get("definition") or {}
        instance = raw.get("instance") or {}
        slot_key = slot_key_from_solver(str(item.get("slot") or ""))
        socket_entries = raw.get("sockets") or []
        mods: list[dict[str, Any]] = []
        for mod_hash in item.get("mods") or []:
            mod_definition = manifest.get_item_definition(int(mod_hash)) or {}
            mods.append({
                "hash": int(mod_hash),
                "name": (mod_definition.get("displayProperties") or {}).get("name", ""),
                "energy_cost": ((mod_definition.get("plug") or {}).get("energyCost") or {}).get(
                    "energyCost", 0
                ),
                "currently_installed": any(
                    int(entry.get("plugHash", 0) or 0) == int(mod_hash)
                    for entry in socket_entries
                ),
            })
        energy = instance.get("energy") or {}
        current_mods: list[dict[str, Any]] = []
        for entry in socket_entries:
            plug_hash = int(entry.get("plugHash", 0) or 0)
            if not plug_hash:
                continue
            plug_definition = manifest.get_item_definition(plug_hash) or {}
            category = (plug_definition.get("plug") or {}).get("plugCategoryIdentifier", "")
            kind, _editable = socket_kind(category)
            if kind not in {"general", "helmet", "gauntlets", "chest", "legs", "class_item",
                            "artifice", "raid"}:
                continue
            current_mods.append({
                "hash": plug_hash,
                "name": (plug_definition.get("displayProperties") or {}).get("name", ""),
                "energy_cost": ((plug_definition.get("plug") or {}).get("energyCost") or {}).get(
                    "energyCost", 0
                ),
            })
        preview.append({
            "slot": slot_key,
            "slot_display": SLOT_DISPLAY.get(slot_key, ""),
            "item_instance_id": instance_id,
            "item_hash": item.get("item_hash"),
            "name": (definition.get("displayProperties") or {}).get("name", ""),
            "power": (instance.get("primaryStat") or {}).get("value"),
            "location": raw.get("location", ""),
            "energy": {
                "capacity": energy.get("energyCapacity"),
                "used": energy.get("energyUsed"),
                "unused": energy.get("energyUnused"),
            } if energy else None,
            "mods": mods,
            "current_mods": current_mods,
        })
    return preview


async def equip_mod(
    svc: Any,
    player_name: str,
    item_instance_id: str,
    mod_name: str,
    character: str,
    confirmed: bool,
) -> dict:
    """`inventory_assistant(intent="equip_mod")`：给一件护甲换一个模组。

    自己管确认：`confirmed=false` 时返回**带 from/to/能量变化**的确认请求（通用写入
    确认只能回显参数，看不出到底要改什么），确认后才写账号。
    """
    plan = await svc["armor_mod_svc"].plan(player_name, item_instance_id, mod_name, character)
    if plan["from"].get("name") and "空" in plan["from"]["name"]:
        plan["from"] = {"hash": None, "name": None, "energy_cost": 0}
    plan["summary"] = _mod_echo(plan)

    # 调谐写不进去（Bungie 只允许游戏内改）：不要走"确认后写入"那套，
    # 否则用户确认了、我们却只能失败。直接把方案交出去，并说明要游戏内改。
    if plan.get("kind") == "tuning":
        return ok_response(
            f"{plan['item_name']} 的调谐建议：{plan['from'].get('name') or '（空）'} → "
            f"{plan['to']['name']}。调谐只能在游戏内改（Bungie 接口不允许第三方写）。",
            {"armor_mod": {**plan, "written": False}},
            next_actions=[
                "把上面这条改动告诉玩家，让他在游戏里手动改调谐；"
                "不要调用 confirmed=true——那一步会被 Bungie 拒绝。",
                f"要看这件护甲现在的状态，用 inventory_assistant(intent=\"item\", "
                f"item_instance_id=\"{plan['item_instance_id']}\")。",
            ],
            warnings=[plan.get("writable_reason") or "调谐只能游戏内修改。"],
        )

    if not confirmed:
        return confirmation_required_response("equip_mod", plan)

    result = await svc["armor_mod_svc"].apply(plan)
    armor_mod: dict[str, Any] = {"summary": plan["summary"]}
    if isinstance(result, dict):
        armor_mod.update(result)
    return ok_response(
        f"已把 {plan['item_name']} 的槽 {plan['socket_index']} 换成 "
        f"{plan['to']['name']}（能量 {plan['energy']['after']}/{plan['energy']['capacity']}）。",
        {"armor_mod": armor_mod},
        next_actions=[
            "要看这件护甲现在的完整状态，用 inventory_assistant(intent=\"item\", "
            f"item_instance_id=\"{plan['item_instance_id']}\")。",
        ],
    )


def with_slot_keys(payload: Any) -> Any:
    """递归给带 `slot` / `replacement_slot` 的条目补 `slot_key` + `slot_display`。

    求解器内部用复数槽位名（`helmets`/`chests`），而工具参数与单件详情用单数
    （`helmet`/`chest`）。这里**只加键**，不动原字段、也不改求解器模型：
    调用方从此不用自己写映射表。
    """
    from ..services.armor_payload import SLOT_DISPLAY, slot_key_from_solver

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            for key in ("slot", "replacement_slot"):
                raw = node.get(key)
                if isinstance(raw, str) and raw.strip():
                    slot_key = slot_key_from_solver(raw.strip())
                    node.setdefault(f"{key}_key" if key == "replacement_slot" else "slot_key", slot_key)
                    node.setdefault(
                        "slot_display" if key == "slot" else "replacement_slot_display",
                        SLOT_DISPLAY.get(slot_key, ""),
                    )
            for child_key, value in node.items():
                # canonical_build 是"要原样回传"的可执行载荷，展示字段一律不进去
                if child_key == "canonical_build":
                    continue
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)
        return node

    return _walk(payload)
