"""护甲相关的聚合工具分支：单件详情，以及原来的异域护甲/套装效果。

搬出来的原因和武器一样：`assistants.py` 有体积上限（1407 行），新 intent 必须
先把成块的分支挪出去，而不是抬上限。判断逻辑放这里，注册与认参仍在 `assistants.py`。
"""

from __future__ import annotations

from typing import Any

from ._enrichment import community_enrichment
from ..services.starside_notes import (
    class_item_pairs,
    perk_note,
    set_notes,
)
from ._responses import confirmation_required_response, error_response, ok_response
from ..exceptions import DestinyMCPError, InvalidArgumentError
from ..services.armor_payload import armor_payload
from ..vocabulary import STAT_LABELS_ZH as _STAT_LABELS  # 六维中文名的单一出处


async def armor_item(svc: Any, player_name: str, item_instance_id: str) -> dict:
    """`inventory_assistant(intent="item")`：一件护甲的完整载荷。

    与列清单的分工：列表只给"这件 T 几、在哪、多少光等"，要看"现在装了什么模组、
    能量还剩多少、词条是多少、还能不能调谐"就得走这里。
    """
    # `or ""`：真 MCP 路径上 null 会被 schema 拦（string_type），但进程内直调会传进来 None，
    # 那时 `.strip()` 会抛 AttributeError，把一句"缺参数"变成 500。
    if not (item_instance_id or "").strip():
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
        armor = svc["manifest_query_svc"].get_exotic_armor_details(exotic_name) or {}
        # 异域职业物品（"之灵"）有双栏配对的组合说明，数据在 perk 层的右栏与 realgame_details#2；
        # 非职业物品时这份块是 available=false + 原因。
        starside = class_item_pairs(
            svc["starside_entities_svc"], svc["manifest"], int(armor.get("item_hash") or 0)
        )
        return ok_response("已读取异域护甲详情。", {
            "armor": armor,
            "starside": starside,
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
        found = svc["set_bonus_svc"].lookup_armor_set(set_bonus_name) or {}
        starside = set_notes(
            svc["starside_entities_svc"], svc["manifest"], int(found.get("hash") or 0), set_bonus_name
        )
        return ok_response("已读取套装效果。", {
            "set_bonus": found,
            "starside": starside,
            "community_references": community_enrichment(
                svc.get("starside_svc"), set_bonus_name, "armor"
            ),
        })
    return ok_response("已读取套装效果列表。", {
        "set_bonuses": svc["set_bonus_svc"].list_all_set_bonuses()
    })


def armor_mods(svc: Any, priority_stat: str) -> dict:
    """护甲模组列表（原来在 `assistants.py` 里的分支）+ 有社区注记的那些的前若干条。

    `match` 一起返回：词表外的词（如"速度"）会靠名字/描述子串蒙中一批模组，
    光看 mods 分不出"按属性筛的"和"只在描述里出现过的"。
    """
    picked = svc["manifest"].get_armor_mods_filtered(slot="", category="all", stat=priority_stat or "")
    warning = picked["match"].get("warning")
    notes = []
    for mod in picked["mods"]:
        if len(notes) >= 20:  # 上限：一份列表响应不该塞进几十颗模组的全文
            break
        note = perk_note(svc["starside_entities_svc"], svc["manifest"], int(mod.get("hash") or 0))
        if not note.get("available"):
            continue
        annotations = note.get("annotations") or {}
        text = annotations.get("效果") or annotations.get("realgame_details")
        cooldown = annotations.get("冷却与槽位") or annotations.get("基础冷却") or annotations.get("冷却")
        if text or cooldown:
            notes.append({"hash": mod.get("hash"), "name": mod.get("name"), "effect": text,
                          "cooldown": cooldown, "source": annotations.get("来源")})
    return ok_response(
        "已读取护甲模组。",
        {
            "mods": picked["mods"],
            "match": picked["match"],
            "starside": {
                "attribution": svc["starside_entities_svc"].attribution(),
                "available": bool(notes),
                "mods": notes,
                "coverage": {"annotated": len(notes), "returned_mods": len(picked["mods"]),
                             "note": "只列出有社区注记的模组，最多 20 条。"},
                "reason": "" if notes else "这批模组没有社区注记。",
            },
        },
        warnings=[warning] if warning else None,
    )


__all__ = ["armor_item", "armor_mods", "exotic_armor", "set_bonus", "error_response"]


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

    # 写不进去的不要走"确认后写入"那套：用户确认了、我们却只能失败。
    # 两种情形共用这一条路（原因见 services/armor_mod_service.plan 的 writable_reason）：
    #   调谐 —— 这颗不在**这件护甲**允许的清单里（组件 310，上游回 1675）；
    #   未解锁 —— 非调谐模组不在 Bungie 给这一位角色的可插入清单里（实测回 1676），游戏里也装不上。
    if plan.get("writable") is False:
        return ok_response(
            f"{plan['item_name']} 的模组建议：{plan['from'].get('name') or '（空）'} → "
            f"{plan['to']['name']}（这次不写账号）。",
            {"armor_mod": {**plan, "written": False}},
            next_actions=[
                "把 writable_reason 原样告诉玩家，让他先把条件解决掉；"
                "不要调用 confirmed=true——这一步会被 Bungie 拒绝。",
                f"要看这件护甲现在的状态，用 inventory_assistant(intent=\"item\", "
                f"item_instance_id=\"{plan['item_instance_id']}\")。",
            ],
            warnings=[plan.get("writable_reason") or "这次没有写入账号。"],
        )

    if not confirmed:
        return confirmation_required_response("equip_mod", plan)

    result = await svc["armor_mod_svc"].apply(plan)
    armor_mod: dict[str, Any] = {"summary": plan["summary"]}
    if isinstance(result, dict):
        armor_mod.update(result)
    if isinstance(result, dict) and result.get("already_installed"):
        # 槽里已经是它了（上游 1679）：说成"换上"会让人以为改过东西，也会让人以为失败要重试。
        headline = (
            f"{plan['item_name']} 的槽 {plan['socket_index']} 已经装着 "
            f"{plan['to']['name']}，这次没有改动。"
        )
    else:
        headline = (
            f"已把 {plan['item_name']} 的槽 {plan['socket_index']} 换成 "
            f"{plan['to']['name']}（能量 {plan['energy']['after']}/{plan['energy']['capacity']}）。"
        )
    return ok_response(
        headline,
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
