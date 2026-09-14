"""Strict Manifest validation and account matching for community builds."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from ..manifest import ITEM_ALIASES, ManifestManager
from ..exceptions import DestinyMCPError
from ..utils.hash_utils import to_unsigned
from .armor_payload import SLOT_DISPLAY, slot_key_from_bucket, slot_key_from_solver

CLASS_TYPES = {"titan": 0, "hunter": 1, "warlock": 2}

# 已经接上来源数据的缺失项类别；其余类别先留结构，等后续接入。
SOURCING_KINDS = ("weapon", "armor_set")
# 其余会出现在要求里的类别：结构保留，数据源未接入。
SOURCING_RESERVED_KINDS = (
    "exotic_armor",
    "armor_mod",
    "artifact",
    "artifact_mod",
    "subclass_component",
)
# 清单行里可以直接透传到来源槽的字段。
SOURCING_FIELDS = (
    "tier",
    "scenario_tiers",
    "role",
    "rank",
    "source",
    "scenario",
    "pieces",
    "frame",
    "element",
    "note",
)


def _sourcing_payload(entry: dict) -> dict:
    payload = {
        "available": True,
        "list": entry["list"],
        "scale": entry["scale"],
        "source_ref": entry.get("source_ref"),
    }
    for key in SOURCING_FIELDS:
        if entry.get(key):
            payload[key] = entry[key]
    if entry.get("perks"):
        # 清单的推荐是「同一栏位里中一个就算达标」，与配装模板的 required_perks 语义不同，不能合并。
        payload["recommended_perks"] = entry["perks"]
        payload["recommended_perk_match"] = "any_within_column"
    return payload


def attach_sourcing(rows: list[dict], lookup: Callable[[list[str]], dict] | None) -> None:
    """给要求行补来源槽。

    已接入的类别查本地评级清单；查到就给来源与评级，查不到说 ``not_listed``；
    清单资料没装说 ``list_unavailable``；未接入的类别固定 ``no_adapter``。
    三种「没有」必须分开，不能都读成「没有来源」。
    """
    pending = [
        row["name"] for row in rows if row["kind"] in SOURCING_KINDS and row.get("name")
    ]
    found: dict[str, dict] = {}
    listed = False
    if pending and lookup is not None:
        try:
            result = lookup(pending)
        except DestinyMCPError:
            listed = False
        else:
            listed = bool(result.get("available"))
            found = {entry["name"]: entry for entry in result.get("results", [])}
    for row in rows:
        if row["kind"] not in SOURCING_KINDS:
            row["sourcing"] = {"available": False, "reason": "no_adapter"}
            continue
        entry = found.get(row.get("name", ""))
        row["sourcing"] = (
            _sourcing_payload(entry)
            if entry is not None
            else {
                "available": False,
                "reason": "not_listed" if listed else "list_unavailable",
            }
        )


def _names(item: dict) -> set[str]:
    return {
        str(item.get(key, "")).strip().casefold()
        for key in ("name", "nameEn")
        if str(item.get(key, "")).strip()
    }


def _exact_definitions(
    manifest: ManifestManager,
    name: str,
    *,
    item_type: int | None = None,
    tier: int | None = None,
    class_type: int | None = None,
) -> list[dict]:
    key = name.strip().casefold()
    accepted = {key, *(value.strip().casefold() for value in ITEM_ALIASES.get(key, []))}
    matches = []
    seen = set()
    for item in manifest.search(name, limit=0):
        if not (_names(item) & accepted):
            continue
        if item_type is not None and item.get("itemType") != item_type:
            continue
        if tier is not None and item.get("tier") != tier:
            continue
        if class_type is not None:
            allowed_classes = {class_type} if item_type == 2 else {-1, 3, class_type}
            if item.get("classType", -1) not in allowed_classes:
                continue
        item_hash = int(item.get("itemHash", 0))
        unsigned = to_unsigned(item_hash)
        if not item_hash or unsigned in seen:
            continue
        seen.add(unsigned)
        matches.append(
            {
                "item_hash": unsigned,
                "name": item.get("name", ""),
                "name_en": item.get("nameEn", ""),
                "item_type": item.get("itemType"),
                "tier": item.get("tier"),
                "class_type": item.get("classType", -1),
            }
        )
    return matches


#: 活动名 → 套装名。社区模板习惯用**活动**称呼套装（「玻璃拱顶」），而 Manifest 与玩家物品
#: 用的是**套装名**（「埃希恩记忆」）。2026-09-14 的实机复盘里，这条差异直接导致
#: "四件套齐不齐"根本没法核对（agent 搜活动名 0 命中 → 标"待自查" → 却回了"能直接玩"）。
#: 这里只登记**已在 Manifest 里核对过**的映射；没登记的一律走候选提示，不猜。
ACTIVITY_SET_ALIASES: dict[str, str] = {
    "玻璃拱顶": "埃希恩记忆",
    "vault of glass": "埃希恩记忆",
}

#: "这一项为什么没做账号校验"的枚举（以前只有 not_account_checked 一个词，
#: 会被读成"没有"）。键是 requirement 的 kind。
UNVERIFIABLE_REASONS: dict[str, str] = {
    "armor_mod": "mod_unlock_state_not_available",
    "artifact": "artifact_unlock_state_not_available",
    "artifact_mod": "artifact_mod_unlock_state_not_available",
    "subclass_component": "subclass_unlock_state_not_available",
    "armor_set": "armor_components_not_read",
    "stat_targets": "stat_feasibility_not_checked",
    "unparsed": "template_text_unparsed",
    "notes": "free_text_not_checkable",
    "review_notes": "free_text_not_checkable",
    "core": "free_text_not_checkable",
    "subclass": "subclass_label_not_checkable",
    "class": "class_not_resolved",
}


def _set_name_candidates(manifest: ManifestManager, name: str, *, limit: int = 3) -> list[str]:
    """给"名字对不上"的套装名找几个相似候选，让调用方能去问用户，而不是只剩"查不到"。"""
    from difflib import SequenceMatcher

    wanted = name.strip().casefold()
    scored = []
    for info in manifest.get_all_set_bonuses().values():
        set_name = str(info.get("set_name", "")).strip()
        if not set_name:
            continue
        score = SequenceMatcher(None, wanted, set_name.casefold()).ratio()
        shared = len({char for char in wanted} & {char for char in set_name.casefold()})
        scored.append((round(score, 3), shared, set_name))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[2] for row in scored[:limit] if row[0] > 0.2 or row[1] >= 2]


def _resolution(kind: str, name: str, definitions: list[dict]) -> dict:
    identities = {
        item.get("name_en") or item.get("name") or item.get("set_name")
        for item in definitions
    }
    status = (
        "ambiguous" if len(identities) > 1 else "resolved" if definitions else "unresolved"
    )
    resolution = {
        "kind": kind,
        "name": name,
        "status": status,
        "definitions": definitions,
    }
    if status == "ambiguous":
        resolution["unresolved_reason"] = "multiple_definitions"
    elif status == "unresolved":
        resolution["unresolved_reason"] = "name_not_matched"
    return resolution


def _set_resolution(manifest: ManifestManager, name: str) -> dict:
    exact = name.strip().casefold()
    matches = [
        {"set_hash": to_unsigned(int(set_hash)), "set_name": info.get("set_name", "")}
        for set_hash, info in manifest.get_all_set_bonuses().items()
        if str(info.get("set_name", "")).strip().casefold() == exact
    ]
    if not matches:
        alias = ACTIVITY_SET_ALIASES.get(exact)
        if alias:
            matches = [
                {"set_hash": to_unsigned(int(set_hash)), "set_name": info.get("set_name", "")}
                for set_hash, info in manifest.get_all_set_bonuses().items()
                if str(info.get("set_name", "")).strip().casefold() == alias.casefold()
            ]
            if matches:
                resolution = _resolution("armor_set", name, matches)
                # 说清"这个名字是活动名、真正的套装名是什么"，下游才敢按下结论核对
                resolution["resolved_via"] = "activity_alias"
                resolution["alias_from"] = name
                resolution["resolved_name"] = matches[0]["set_name"]
                return resolution
    resolution = _resolution("armor_set", name, matches)
    if resolution["status"] == "unresolved":
        candidates = _set_name_candidates(manifest, name)
        if candidates:
            resolution["set_name_candidates"] = candidates
            resolution["unresolved_reason"] = "name_not_matched_candidates_available"
    return resolution


def validate_build(manifest: ManifestManager, build: dict) -> dict:
    class_id = build.get("class", {}).get("id", "")
    class_type = CLASS_TYPES.get(class_id)
    requirements = []
    for weapon in build.get("weapons", []):
        tier = 6 if weapon.get("tier") == "exotic" else 5
        resolution = _resolution(
            "weapon",
            weapon["name"],
            _exact_definitions(manifest, weapon["name"], item_type=3, tier=tier),
        )
        resolution["required_perks"] = list(weapon.get("perks", []))
        resolution["perk_resolutions"] = [
            _resolution(
                "weapon_perk", name, _exact_definitions(manifest, name, item_type=19)
            )
            for name in weapon.get("perks", [])
        ]
        requirements.append(resolution)
    exotic = build.get("armor", {}).get("exotic", "")
    if exotic:
        requirements.append(
            _resolution(
                "exotic_armor",
                exotic,
                _exact_definitions(
                    manifest, exotic, item_type=2, tier=6, class_type=class_type
                )
                if class_type is not None
                else [],
            )
        )
    for required_set in build.get("armor", {}).get("set_requirements", []):
        resolution = _set_resolution(manifest, required_set["name"])
        resolution["required_count"] = required_set["count"]
        requirements.append(resolution)
    for slot, mods in build.get("armor", {}).get("mods", {}).items():
        for mod in mods:
            resolution = _resolution(
                "armor_mod", mod, _exact_definitions(manifest, mod, item_type=19)
            )
            resolution["slot"] = slot
            requirements.append(resolution)
    artifact = build.get("artifact", {})
    if artifact.get("name"):
        requirements.append(
            _resolution(
                "artifact",
                artifact["name"],
                _exact_definitions(manifest, artifact["name"], item_type=28),
            )
        )
    for mod in artifact.get("mods", []):
        requirements.append(
            _resolution(
                "artifact_mod", mod, _exact_definitions(manifest, mod, item_type=19)
            )
        )
    for component, value in build.get("class", {}).items():
        if component in {"name", "id"}:
            continue
        values = value if isinstance(value, list) else [value]
        for name in values:
            # Subclass definitions vary by component; exact-name validation is deliberately type-neutral.
            resolution = _resolution(
                "subclass_component",
                name,
                _exact_definitions(manifest, name, class_type=class_type),
            )
            resolution["component"] = component
            requirements.append(resolution)
    unresolved = [item for item in requirements if item["status"] != "resolved"]
    unresolved.extend(
        perk
        for item in requirements
        for perk in item.get("perk_resolutions", [])
        if perk["status"] != "resolved"
    )
    candidate_targets = {
        key: value["min"]
        for key, value in build.get("stat_targets", {}).items()
        if value.get("kind") == "minimum"
    }
    set_requirements = build.get("armor", {}).get("set_requirements", [])
    solver_handoff = {
        "intent": "find",
        "character": class_id,
        "exotic_name": exotic or None,
        **{f"{key}_target": value for key, value in candidate_targets.items()},
        "set_bonus_name": set_requirements[0]["name"]
        if len(set_requirements) == 1
        else None,
        "set_bonus_count": set_requirements[0]["count"]
        if len(set_requirements) == 1
        else None,
    }
    return {
        "requirements": requirements,
        "manifest_validation_complete": not unresolved and class_type is not None,
        "manifest_validation_scope": "exact_names_types_tiers_and_definition_class_only",
        "class_resolved": class_type is not None,
        "unresolved_requirements": deepcopy(unresolved),
        "template_parse_complete": not build.get("unparsed"),
        "unparsed_requirements": list(build.get("unparsed", [])),
        "solver_handoff": {
            "tool": "build_assistant",
            "arguments": solver_handoff,
            "requires_user_review": True,
            "scope": "partial_armor_requirements_only",
            "limitations": [
                "仅转换带 + 的明确最低值；裸数值、范围、未指定值不自动改成下限，用户须确认后补充。",
                "现有求解器一次只能表达一个套装；未转换多个套装、武器、技能、模组、神器及注解。",
                "不要把这些部分参数用于整套复现；只能经用户同意作为单独护甲求解的起点。",
            ],
            "not_converted_stat_targets": {
                key: value
                for key, value in build.get("stat_targets", {}).items()
                if value.get("kind") != "minimum"
            },
        },
        "execution_supported": False,
        "execution_blockers": [
            "社区模板不是服务器签发的 ExecutableBuild。",
            "武器 Perk、技能、护甲模组和神器不能由当前精确配装执行器作为一个原子计划验证。",
        ],
    }


def _marked_perk(name: str, matched_hashes: set[int]) -> str:
    """命中项按同一套展示口径标强化版：普通版原样，强化版加 ↑。

    判定方式是查配对表（`base_of(hash) != 0` 即这个 hash 本身是强化版），
    所以命中的是强化版时名字带箭头，读的人一眼能看出"这是强化过的"。
    """
    from .weapon_profile import load_enhanced_pairs

    pairs = load_enhanced_pairs()
    enhanced = any(pairs.base_of(int(hash_value)) != 0 for hash_value in matched_hashes)
    text = str(name or "").strip()
    return f"{text}↑" if enhanced and text else text


def _armor_slot_key(item: Any) -> str:
    """库存条目 → 统一槽位键：优先用它已经算好的 `slot`，退回 bucket 显示名。"""
    slot = getattr(item, "slot", "") or ""
    if slot:
        return slot_key_from_solver(slot)
    return slot_key_from_bucket(getattr(item, "bucket_type", "") or "")


def _owned_instances(items, hashes: set[int], *, item_type: str) -> list:
    return [
        item
        for item in items
        if item.item_type.casefold() == item_type
        and to_unsigned(item.item_hash) in hashes
    ]


async def match_inventory(
    manifest: ManifestManager,
    player_name: str,
    build: dict,
    inventory_service: Any,
    weapon_detail_service: Any,
    *,
    lookup: Callable[[list[str]], dict] | None = None,
) -> dict:
    validation = validate_build(manifest, build)
    inventory = await inventory_service.get_inventory(player_name, "all")
    items = list(inventory.items)
    weapon_requirements = [
        item for item in validation["requirements"] if item["kind"] == "weapon"
    ]
    details = None
    read_errors = []
    if any(
        item["status"] == "resolved" and item["required_perks"]
        for item in weapon_requirements
    ):
        try:
            details = await weapon_detail_service.get_weapon_details_by_type(
                player_name, "", include_selectable_plugs=True
            )
        except DestinyMCPError as exc:
            read_errors.append(
                {"scope": "weapon_sockets", "error_type": type(exc).__name__}
            )
    details_by_instance = (
        {
            item.weapon["instance"]["instance_id"]: item
            for item in details.weapons
            if isinstance(item.weapon.get("instance"), dict)
        }
        if details
        else {}
    )
    snapshot = None
    if (
        any(
            item["kind"] == "armor_set" and item["status"] == "resolved"
            for item in validation["requirements"]
        )
        and validation["class_resolved"]
    ):
        try:
            snapshot = await inventory_service.get_armor_snapshot(
                player_name, build["class"]["id"]
            )
        except DestinyMCPError as exc:
            read_errors.append(
                {"scope": "armor_sets", "error_type": type(exc).__name__}
            )
    ownership, known_missing, unknown = [], [], []
    for required in validation["requirements"]:
        row = deepcopy(required)
        for perk in row.get("perk_resolutions", []):
            if perk["status"] != "resolved":
                unknown.append(
                    deepcopy(perk) | {"inventory_status": "unknown_definition"}
                )
        if row["status"] != "resolved":
            row["inventory_status"] = (
                "unknown_definition"
                if row["status"] == "unresolved"
                else "ambiguous_definition"
            )
            # 没解析出定义 ≠ 玩家没有；给枚举原因，别再让调用方自己解释
            row["unverifiable_reason"] = row.get("unresolved_reason") or "name_not_matched"
            unknown.append(row)
            ownership.append(row)
            continue
        if row["kind"] == "weapon":
            hashes = {definition["item_hash"] for definition in row["definitions"]}
            candidates = _owned_instances(items, hashes, item_type="weapon")
            row["owned_instances"] = []
            for item in candidates:
                instance = {
                    "instance_id": item.item_instance_id,
                    "name": item.name,
                    "location": item.location,
                }
                perks = row["required_perks"]
                if perks:
                    detail = details_by_instance.get(item.item_instance_id)
                    identity = detail.weapon if detail else {}
                    instance["socket_status"] = (
                        "complete"
                        if detail
                        and detail.perks_complete
                        and to_unsigned(identity.get("item_hash", 0))
                        == to_unsigned(item.item_hash)
                        else "unknown"
                    )
                    # P4 形状：当前装的 plug 挂在定义级 sockets 的 equipped 上
                    equipped = [
                        socket["equipped"]
                        for socket in (detail.sockets if detail else [])
                        if isinstance(socket.get("equipped"), dict)
                        and socket["equipped"].get("plug_hash")
                    ]
                    instance["current_perks"] = [
                        str(plug.get("name") or "") for plug in equipped if plug.get("name")
                    ]
                    current_hashes = {
                        to_unsigned(plug["plug_hash"]) for plug in equipped
                    }
                    perk_resolved = all(
                        perk["status"] == "resolved" for perk in row["perk_resolutions"]
                    )
                    instance["current_roll_match"] = (
                        all(
                            current_hashes
                            & {
                                definition["item_hash"]
                                for definition in perk["definitions"]
                            }
                            for perk in row["perk_resolutions"]
                        )
                        if instance["socket_status"] == "complete" and perk_resolved
                        else None
                    )
                    # 「能换到但没装」这一层（0.1.11）：以前只声明"没查"，实机复盘里
                    # 被读成"这把枪不行"。现在按组件 310 的 hash 集合逐 perk 判定：
                    # 现在装着 / 能换到 / 两者都没有。
                    detail_sockets = detail.sockets if detail else []
                    # 「字段在不在」和「集合空不空」是两件事：字段缺失 = 这次没读，
                    # 字段存在但为空 = 读了、这件确实没有可换项。混在一起会把"没查"说成"没有"。
                    hashes_provided = any(
                        "selectable_plug_hashes" in socket for socket in detail_sockets
                    )
                    selectable_hashes = {
                        int(hash_value) & 0xFFFFFFFF
                        for socket in detail_sockets
                        for hash_value in (socket.get("selectable_plug_hashes") or [])
                    }
                    instance["selectable_plug_status"] = (
                        "available"
                        if selectable_hashes
                        else "none"
                        if hashes_provided
                        else "not_read"
                    )
                    if perk_resolved and instance["socket_status"] == "complete" and hashes_provided:
                        current_ok, alternate_ok, missing = [], [], []
                        for perk in row["perk_resolutions"]:
                            perk_hashes = {
                                to_unsigned(definition["item_hash"])
                                for definition in perk["definitions"]
                            }
                            if current_hashes & perk_hashes:
                                current_ok.append(_marked_perk(perk["name"], current_hashes & perk_hashes))
                            elif selectable_hashes & perk_hashes:
                                alternate_ok.append(
                                    _marked_perk(perk["name"], selectable_hashes & perk_hashes)
                                )
                            else:
                                missing.append(perk["name"])
                        instance["perks_current_match"] = current_ok
                        instance["perks_available_to_switch"] = alternate_ok
                        instance["perks_unavailable"] = missing
                        instance["alternate_roll_match"] = (
                            not missing and bool(alternate_ok)
                        )
                row["owned_instances"].append(instance)
            if not candidates:
                row["inventory_status"] = "missing"
                known_missing.append(row)
            elif not row["required_perks"]:
                row["inventory_status"] = "owned"
            elif any(
                item["current_roll_match"] is True for item in row["owned_instances"]
            ):
                row["inventory_status"] = "current_roll_matched"
            elif any(
                item.get("alternate_roll_match") is True for item in row["owned_instances"]
            ):
                # 换一下 perk 就能达标：这不是"没有"，说清是"换栏可达"
                row["inventory_status"] = "owned_alternate_roll_available"
            elif any(
                item["current_roll_match"] is None for item in row["owned_instances"]
            ):
                row["inventory_status"] = "unknown_current_roll"
                unknown.append(row)
            else:
                row["inventory_status"] = "owned_no_current_roll_match"
            if row["required_perks"]:
                checked = any(
                    item.get("perks_available_to_switch") is not None
                    or item.get("perks_unavailable") is not None
                    for item in row["owned_instances"]
                )
                row["alternate_perk_options_checked"] = checked
                if checked:
                    # 说清这一层的边界：只判断"这个 perk 在这把枪的某个可换栏里"，
                    # 不判断栏位对不对；锻造件的 310 只给当前选中项，可能漏报。
                    row["alternate_perk_options_scope"] = "any_selectable_socket"
                    row["alternate_perk_options_caveat"] = (
                        "锻造件的组件 310 只列当前选中的项，未命中不等于换不到"
                    )
                else:
                    # 明确"没查"的原因，别让 owned_no_current_roll_match 被读成"这把枪不行"
                    row["alternate_perk_options_reason"] = "instance_socket_options_not_read"
            ownership.append(row)
        elif row["kind"] == "exotic_armor":
            hashes = {definition["item_hash"] for definition in row["definitions"]}
            candidates = _owned_instances(items, hashes, item_type="armor")
            row["owned_instances"] = [
                {
                    "instance_id": item.item_instance_id,
                    "name": item.name,
                    "location": item.location,
                    # 与单件详情/列表同一套槽位键，调用方不用自己猜 bucket 名
                    "slot": _armor_slot_key(item),
                    "slot_display": SLOT_DISPLAY.get(_armor_slot_key(item), ""),
                }
                for item in candidates
            ]
            row["inventory_status"] = "owned" if candidates else "missing"
            if not candidates:
                known_missing.append(row)
            ownership.append(row)
        elif row["kind"] == "armor_set":
            set_hashes = {definition["set_hash"] for definition in row["definitions"]}
            matched, possible_slots = [], set()
            if snapshot is not None:
                for slot, collection in {
                    "helmet": "helmets",
                    "gauntlets": "gauntlets",
                    "chest": "chests",
                    "legs": "legs",
                    "class_item": "class_items",
                }.items():
                    for item in snapshot.get_slot(collection):
                        native = (
                            item.set_bonus_hash is not None
                            and to_unsigned(item.set_bonus_hash) in set_hashes
                        )
                        if native or item.has_set_bonus_mod_socket:
                            possible_slots.add(slot)
                            matched.append(
                                {
                                    "instance_id": item.item_instance_id,
                                    "slot": slot,
                                    "wildcard": not native,
                                }
                            )
            row["owned_candidates"] = matched
            row["potential_distinct_slot_count"] = (
                len(possible_slots) if snapshot is not None else None
            )
            # "这套我有几件、还差几件"要能直接读出来：以前只有 owned_candidates 列表，
            # 调用方得自己数，实际结果就是没人去数（复盘里的"❓待自查"）。
            row["owned_count"] = len(matched)
            row["owned_distinct_slot_count"] = len(possible_slots)
            row["missing_slot_count"] = (
                max(0, int(row["required_count"]) - len(possible_slots))
                if snapshot is not None
                else None
            )
            row["wildcard_count"] = sum(1 for entry in matched if entry["wildcard"])
            row["inventory_status"] = (
                "not_account_checked"
                if snapshot is None
                else "insufficient_slots"
                if len(possible_slots) < row["required_count"]
                else "candidates_available"
            )
            row["compatible_plan_checked"] = False
            if snapshot is None:
                row["unverifiable_reason"] = UNVERIFIABLE_REASONS["armor_set"]
            if row["inventory_status"] == "insufficient_slots":
                known_missing.append(row)
            # Slot availability is not proof that simultaneous set/mod/exotic requirements are achievable.
            unknown.append(row)
            ownership.append(row)
        else:
            row["inventory_status"] = "not_account_checked"
            row["unverifiable_reason"] = UNVERIFIABLE_REASONS.get(
                row["kind"], "category_not_integrated"
            )
            unknown.append(row)
            ownership.append(row)

    # Stat ownership requires complete armor component validation, not the lightweight inventory call.
    if build.get("stat_targets"):
        unknown.append(
            {
                "kind": "stat_targets",
                "inventory_status": "not_account_checked",
                "unverifiable_reason": UNVERIFIABLE_REASONS["stat_targets"],
                "requirements": build["stat_targets"],
            }
        )
    if build.get("unparsed"):
        unknown.append(
            {
                "kind": "unparsed",
                "inventory_status": "not_account_checked",
                "unverifiable_reason": UNVERIFIABLE_REASONS["unparsed"],
                "requirements": build["unparsed"],
            }
        )
    for key in ("notes", "review_notes", "core", "subclass"):
        if build.get(key):
            unknown.append(
                {
                    "kind": key,
                    "inventory_status": "not_account_checked",
                    "unverifiable_reason": UNVERIFIABLE_REASONS[key],
                    "requirements": build[key],
                }
            )
    if not validation["class_resolved"]:
        unknown.append(
            {
                "kind": "class",
                "inventory_status": "unknown_definition",
                "unverifiable_reason": UNVERIFIABLE_REASONS["class"],
            }
        )
    attach_sourcing(ownership, lookup)
    return {
        "inventory_status": "complete",
        "requirements": ownership,
        "known_missing_requirements": known_missing,
        "unresolved_or_unchecked_requirements": unknown,
        "known_missing_count": len(known_missing),
        "unknown_or_unchecked_count": len(unknown),
        "read_errors": read_errors,
        "account_check_scope": [
            "exact weapon definition ownership",
            "currently selected weapon perks",
            "exact exotic armor ownership",
            "potential armor set slots for target class",
        ],
        "account_check_exclusions": [
            "selectable alternate weapon perks",
            "subclass unlocks",
            "mod and artifact unlocks",
            "stat feasibility and energy capacity",
            "requirements embedded in free-form notes",
        ],
        "sourcing_available_kinds": list(SOURCING_KINDS),
        "sourcing_reserved_kinds": list(SOURCING_RESERVED_KINDS),
        "sourcing_scope": "local_community_lists_only",
        "coverage_complete": not unknown,
        "coverage_scope": "listed_requirement_checks_not_full_loadout_feasibility",
        "full_build_verified": False,
        "execution_eligible": False,
        "warnings": [
            "missing 仅表示 Manifest 已精确解析且完整库存中未达到要求；unknown/not_account_checked 不能解释为缺少。",
            "武器 roll 分三层看：current_roll_match=现在装着的就行；"
            "owned_alternate_roll_available=换一下可换栏就能达标（见 owned_instances[].perks_available_to_switch）；"
            "owned_no_current_roll_match=现在没装、可换栏里也没有（见 perks_unavailable），"
            "**不是**「这把枪不行」；alternate_perk_options_checked=false 才表示这一层没查"
            "（并给 alternate_perk_options_reason）。判定范围只到「这个 perk 在这把枪的某个可换栏里」，"
            "不校验栏位与模板是否一致；锻造件的组件 310 只列当前选中项，未命中不等于换不到。",
            "unresolved_reason=name_not_matched 表示名字在 Manifest 里对不上（常见于社区模板用"
            "活动名称呼套装），不是玩家缺少该装备；有 set_name_candidates 时请先跟用户确认名字。",
            "unverifiable_reason 说明这一项**没做账号校验**的原因（例如模组解锁状态接口没有暴露），"
            "任何以它为由的「你没有」都是误读。",
            "sourcing 里的 source 来自社区清单快照，不是官方实时掉落；推荐 Perk 是同一栏位的备选，"
            "与 required_perks 不是同一回事。no_adapter 表示该类别还没有来源数据源，"
            "不能读成「没有来源」或「刷不到」。",
            "此结果不会执行任何游戏写入，也不能作为整套社区配装的一键装备凭据。",
        ],
    }
