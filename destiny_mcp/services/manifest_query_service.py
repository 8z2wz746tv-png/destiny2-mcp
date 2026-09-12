"""Manifest query service — business logic for item/weapon/armor/perk lookups.

Extracted from inventory_tools.py per Rule 1: tools should not contain
business logic. This service encapsulates all manifest querying and
data transformation that tools previously did inline.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..exceptions import ManifestError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..manifest_names import names_for
from . import weapon_payload, weapon_profile

logger = get_logger(__name__)

# Catalyst mapping cache
_CATALYST_MAPPING: dict[int, list[int]] | None = None

# Damage type hash → Chinese name

# Weapon stat hash → Chinese name
# Fragment stat hash → Chinese name (Renegades update, consistent with build/constants.py)
FRAGMENT_STAT_NAMES = {
    2996146975: "武器",
    392767087: "生命",
    1943323491: "职业",
    1735777505: "手雷",
    144602215: "超能",
    4244567218: "近战",
}


def _load_catalyst_mapping() -> dict[int, list[int]]:
    """Load catalyst mapping from YAML file."""
    global _CATALYST_MAPPING
    if _CATALYST_MAPPING is None:
        mapping_path = Path(__file__).parent.parent / "data" / "catalyst_mapping.yaml"
        if mapping_path.exists():
            with open(mapping_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _CATALYST_MAPPING = {int(k): v for k, v in data.items()}
        else:
            _CATALYST_MAPPING = {}
    return _CATALYST_MAPPING


class ManifestQueryService:
    """Business logic for querying manifest definitions."""

    def __init__(self, manifest: ManifestManager) -> None:
        self._manifest = manifest

    # ── Item Definition ──────────────────────────────────────────────

    def get_item_full_definition(self, item_name: str = "", item_hash: int = 0) -> dict:
        """Get enriched item definition with English name and intrinsic perks.

        Returns a dict with display info, intrinsic perks, weapon-specific fields.
        Raises ManifestError if not found.
        """
        if item_name:
            definition = self._manifest.get_item_definition_by_name(item_name)
        elif item_hash:
            definition = self._manifest.get_item_definition(item_hash)
        else:
            raise ManifestError("请提供 item_name 或 item_hash")

        if not definition:
            raise ManifestError(f"找不到物品：{item_name or item_hash}")

        display = definition.get("displayProperties", {})
        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        en_name = self._get_en_name(definition.get("hash", 0))
        intrinsic_perks = self._extract_intrinsic_perks(definition)

        result = {
            "name": display.get("name", ""),
            "nameEn": en_name,
            "description": display.get("description", ""),
            "flavorText": definition.get("flavorText", ""),
            "itemType": definition.get("itemType", 0),
            "itemTypeDisplayName": definition.get("itemTypeDisplayName", ""),
            "tierType": definition.get("tierType", 0),
            "classType": {0: "Titan", 1: "Hunter", 2: "Warlock"}.get(
                definition.get("classType", -1), "Any"
            ),
            "intrinsicPerks": intrinsic_perks,
            "icon_url": icon_url,
        }

        if definition.get("itemType") == 3:
            result["weaponType"] = definition.get("itemTypeDisplayName", "")
            result["ammoType"] = (definition.get("equippingBlock") or {}).get("ammoType", 0)
            result["damageType"] = definition.get("defaultDamageType", 0)

        return result

    # ── Exotic Armor ─────────────────────────────────────────────────

    def get_exotic_armor_details(self, armor_name: str) -> dict:
        """Get exotic armor details with intrinsic perks.

        Returns enriched dict. Raises ManifestError if not found.
        """
        results = self._manifest.search(armor_name, limit=5)
        if not results:
            raise ManifestError(f"找不到物品：{armor_name}")

        exotic_armor = None
        for item in results:
            if item.get("tier") == 6 and item.get("itemType") == 2:
                exotic_armor = item
                break

        if not exotic_armor:
            raise ManifestError(f"找不到异域护甲：{armor_name}")

        definition = self._manifest.get_item_definition(exotic_armor["itemHash"])
        if not definition:
            raise ManifestError(f"找不到物品定义：{armor_name}")

        display = definition.get("displayProperties", {})
        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        en_name = self._get_en_name(definition.get("hash", 0))
        intrinsic_perks = self._extract_intrinsic_perks(definition)

        return {
            "name": display.get("name", ""),
            "nameEn": en_name,
            "description": display.get("description", ""),
            "flavorText": definition.get("flavorText", ""),
            "itemTypeDisplayName": definition.get("itemTypeDisplayName", ""),
            "tierType": definition.get("tierType", 0),
            "classType": {0: "Titan", 1: "Hunter", 2: "Warlock"}.get(
                definition.get("classType", -1), "Any"
            ),
            "intrinsicPerks": intrinsic_perks,
            "icon_url": icon_url,
        }

    def get_exotic_armor_list(self, class_name: str) -> dict:
        """Get all exotic armor for a class with intrinsic perks."""
        exotics = self._manifest.get_exotic_armor_by_class(class_name)
        if not exotics:
            raise ManifestError(f"找不到 {class_name} 的异域护甲")

        class_type_map = {0: "Titan", 1: "Hunter", 2: "Warlock"}
        results = []
        for item in exotics:
            item_hash = item.get("itemHash", 0)
            definition = self._manifest.get_item_definition(item_hash)
            if not definition:
                continue

            en_name = self._get_en_name(item_hash)
            intrinsic_perks = self._extract_intrinsic_perks(definition)

            icon = item.get("icon", "")
            results.append({
                "name": item.get("name", ""),
                "nameEn": en_name,
                "itemHash": item_hash,
                "slot": item.get("bucketTypeHash", 0),
                "intrinsicPerks": intrinsic_perks,
                "icon_url": icon,
            })

        return {
            "class": class_type_map.get(exotics[0].get("classType", -1), class_name),
            "count": len(results),
            "exotics": results,
        }

    # ── Weapon Info ──────────────────────────────────────────────────

    def get_weapon_full_info(
        self,
        weapon_name: str,
        *,
        lookup_factory=None,
        instance_stats: dict | None = None,
    ) -> dict:
        """完整武器模板：`{weapon, sockets, stats}`（info / analyze 共用）。

        形状一律由 `weapon_payload` 造；这里只负责找定义、带愿单标注、异域补催化剂。
        `lookup_factory(item_hash)` 返回 `plug_hash -> (pve, pvp)`，由愿单服务提供
        （这里不直接依赖它，避免服务之间互相认识）。
        Raises ManifestError if not found or not a weapon.
        """
        item_hash, weapon_def = self._find_weapon_definition(weapon_name)
        names = names_for(self._manifest)
        sockets = weapon_payload.socket_list(
            self._manifest,
            weapon_def,
            names=names,
            god_roll_lookup=lookup_factory(item_hash) if lookup_factory else None,
        )
        template: dict = {
            "weapon": weapon_payload.weapon_block(
                self._manifest, weapon_def, sockets=sockets, names=names
            ),
            "sockets": sockets,
            "stats": weapon_payload.stat_list(
                self._manifest, weapon_def, instance_stats, names=names
            ),
        }
        # 催化剂**不**挂进身份块：它只在 `catalyst` intent 给（`data.catalyst`），
        # 否则异域武器的身份块会比别的 intent 多一个键，"同一把武器到哪都一个样"就破了。
        return template

    def _find_weapon_definition(self, weapon_name: str) -> tuple[int, dict]:
        """按名字找武器定义（实现在 weapon_profile.find_weapon，多处共用）。"""
        return weapon_profile.find_weapon(self._manifest, weapon_name)

    def get_weapon_stats(self, weapon_name: str) -> dict:
        """武器属性：`{weapon, stats}`（同一个模板的身份块 + 属性列表）。

        属性顺序与"是否按数字展示"来自 `DestinyStatGroupDefinition`，名字查
        `DestinyStatDefinition`；值取显示值（不是 `investmentStats`，否则遗产的
        每分钟发射数会从 65 变成 30）。
        """
        _item_hash, definition = self._find_weapon_definition(weapon_name)
        names = names_for(self._manifest)
        return {
            "weapon": weapon_payload.lean_identity(
                self._manifest,
                definition,
                names=names,
                roll_kind=weapon_profile.roll_kind(definition),
            ),
            "stats": weapon_payload.stat_list(self._manifest, definition, names=names),
        }

    # ── Catalyst ─────────────────────────────────────────────────────

    def get_catalyst_details(self, weapon_name: str) -> dict:
        """Get catalyst details for an exotic weapon.

        Raises ManifestError if weapon not found or no catalyst exists.
        """
        _item_hash, weapon_def = self._find_weapon_definition(weapon_name)

        tier = (weapon_def.get("inventory") or {}).get("tierType", 0)
        identity = weapon_payload.lean_identity(
            self._manifest, weapon_def, names=names_for(self._manifest)
        )
        weapon_zh_name = identity["name"] or weapon_name
        base = {
            "weapon": identity,
            "is_exotic": tier == 6,
            "count": 0,
            "catalysts": [],
            # 本地不读账号记录组件，所以不猜"解锁没有"；要说清是没查，不是没解锁。
            "unlock_state": "not_checked",
        }

        if tier != 6:
            return {
                **base,
                "note": f"「{weapon_zh_name}」不是异域武器；只有异域武器才有催化剂。",
            }

        result_catalysts = []
        for cat in self._find_catalysts(weapon_def):
            cat_info = self._get_catalyst_effect_info(cat["itemHash"])
            if cat_info:
                result_catalysts.append(cat_info)

        if not result_catalysts:
            return {
                **base,
                "note": f"本地 Manifest 里没有「{base['weapon']}」的催化剂定义（它可能确实没有催化剂）。",
            }

        return {**base, "count": len(result_catalysts), "catalysts": result_catalysts}

    # ── Perk Description ─────────────────────────────────────────────

    def get_perk_description(self, perk_name: str) -> dict:
        """Look up perk name and description. Raises ManifestError if not found."""
        results = self._manifest.search(perk_name, limit=5)
        if not results:
            raise ManifestError(f"找不到 perk：{perk_name}")

        perk_item = None
        for item in results:
            if item.get("itemType") == 19:
                perk_item = item
                break

        if not perk_item:
            raise ManifestError(f"找不到 perk：{perk_name}")

        definition = self._manifest.get_item_definition(perk_item["itemHash"])
        if not definition:
            raise ManifestError(f"找不到 perk 定义：{perk_name}")

        display = definition.get("displayProperties", {})
        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        en_name = self._get_en_name(definition.get("hash", 0))

        return {
            "name": display.get("name", ""),
            "name_en": en_name,
            "item_hash": definition.get("hash", 0),
            "description": display.get("description", ""),
            "flavor_text": definition.get("flavorText", ""),
            "plug_category": self._manifest.get_plug_category_identifier(
                definition.get("hash", 0)
            )
            or "",
            "icon_url": icon_url,
        }

    # ── Private Helpers ──────────────────────────────────────────────

    def _get_en_name(self, item_hash: int) -> str:
        """Get English name from English manifest."""
        return self._manifest.get_english_name(item_hash)

    def _extract_intrinsic_perks(self, definition: dict) -> list[dict]:
        """Extract intrinsic perks from item sockets."""
        intrinsic_perks = []
        sockets = definition.get("sockets", {}).get("socketEntries", [])
        for socket in sockets:
            plug_set_hash = socket.get("reusablePlugSetHash", 0)
            if not plug_set_hash:
                continue
            plug_set = self._manifest.get_plug_set_plugs(plug_set_hash)
            if not plug_set:
                continue
            first_plug_hash = plug_set[0].get("plugItemHash", 0)
            first_plug_def = self._manifest.get_item_definition(first_plug_hash)
            if not first_plug_def:
                continue
            cat_id = (first_plug_def.get("plug") or {}).get("plugCategoryIdentifier", "")
            if cat_id == "intrinsics":
                for plug in plug_set:
                    plug_hash = plug.get("plugItemHash", 0)
                    plug_def = self._manifest.get_item_definition(plug_hash)
                    if plug_def:
                        plug_display = plug_def.get("displayProperties", {})
                        name = plug_display.get("name", "")
                        if name:
                            intrinsic_perks.append({
                                "name": name,
                                "description": plug_display.get("description", ""),
                                "icon_url": _absolute_icon_url(plug_display.get("icon")),
                            })
        return intrinsic_perks

    def _find_catalysts(self, weapon_def: dict) -> list[dict]:
        """Find catalyst plugs for an exotic weapon."""
        catalysts = []
        weapon_hash = weapon_def.get("hash", 0)
        sockets = weapon_def.get("sockets", {}).get("socketEntries", [])

        # Try recipe version (type=30) sockets first
        recipe_items = self._manifest.find_items_by_type(
            30, extra_json_like=str(weapon_hash)
        )
        for recipe_data in recipe_items:
            recipe_sockets = recipe_data.get("sockets", {}).get("socketEntries", [])
            catalysts.extend(self._extract_catalyst_from_sockets(recipe_sockets))
            break

        # Fallback: weapon version sockets
        if not catalysts:
            catalysts = self._extract_catalyst_from_sockets(sockets)

        # Fallback: manual mapping
        if not catalysts:
            mapping = _load_catalyst_mapping()
            if weapon_hash in mapping:
                for cat_hash in mapping[weapon_hash]:
                    cat_def = self._manifest.get_item_definition(cat_hash)
                    if cat_def:
                        cat_name = (cat_def.get("displayProperties") or {}).get("name", "")
                        if cat_name:
                            catalysts.append({"itemHash": cat_hash, "name": cat_name})

        return self._deduplicate_catalysts(catalysts)

    def _extract_catalyst_from_sockets(self, sockets: list[dict]) -> list[dict]:
        """Extract catalyst plugs from a socket list."""
        catalysts = []
        for socket in sockets:
            single_hash = socket.get("singleInitialItemHash", 0)
            plug_set_hash = socket.get("reusablePlugSetHash", 0)
            if not single_hash:
                continue
            single_def = self._manifest.get_item_definition(single_hash)
            if not single_def:
                continue
            cat_id = (single_def.get("plug") or {}).get("plugCategoryIdentifier", "")
            is_catalyst = (
                "empty.exotic.masterwork" in cat_id
                or "catalyst" in cat_id.lower()
                or ("masterwork" in cat_id.lower() and "tracker" not in cat_id.lower())
            )
            if is_catalyst and plug_set_hash:
                plug_set = self._manifest.get_plug_set_plugs(plug_set_hash)
                for plug in plug_set:
                    ph = plug.get("plugItemHash", 0)
                    pd = self._manifest.get_item_definition(ph)
                    if pd:
                        pn = (pd.get("displayProperties") or {}).get("name", "")
                        pc = (pd.get("plug") or {}).get("plugCategoryIdentifier", "")
                        # 通用锻造/大师杰作属性池（v400.plugs.weapons.masterworks.stat.*）
                        # 是每条武器都有的"1阶：稳定性…"，不是催化剂；传说武器会因此
                        # 吐出上百条噪声，看起来像它有 140 个催化剂。
                        if "masterworks.stat." in pc.lower():
                            continue
                        if pn and "empty" not in pc.lower():
                            catalysts.append({"itemHash": ph, "name": pn})
        return catalysts

    def _deduplicate_catalysts(self, catalysts: list[dict]) -> list[dict]:
        """Deduplicate catalysts by itemHash and English name."""
        seen_hashes: set[int] = set()
        seen_names: set[str] = set()
        unique = []
        for cat in catalysts:
            if cat["itemHash"] in seen_hashes:
                continue
            cat_en_name = self._get_en_name(cat["itemHash"])
            if cat_en_name and cat_en_name in seen_names:
                continue
            seen_hashes.add(cat["itemHash"])
            if cat_en_name:
                seen_names.add(cat_en_name)
            unique.append(cat)
        return unique

    def _get_catalyst_effect_info(self, item_hash: int) -> dict | None:
        """Get catalyst effect info by hash, handling type=20→19 fallback."""
        definition = self._manifest.get_item_definition(item_hash)
        if not definition:
            return None

        # type=20 is empty shell, find type=19 with actual effects
        if definition.get("itemType") == 20:
            cat_name = (definition.get("displayProperties") or {}).get("name", "")
            if cat_name:
                alt_items = self._manifest.find_items_by_type(19, extra_json_like=cat_name)
                for alt_data in alt_items:
                    alt_name = (alt_data.get("displayProperties") or {}).get("name", "")
                    if alt_name == cat_name:
                        definition = alt_data
                        break

        display = definition.get("displayProperties", {})
        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        en_name = self._get_en_name(definition.get("hash", 0))

        effects = []
        for perk in definition.get("perks", []):
            perk_hash = perk.get("perkHash", 0)
            if perk_hash:
                perk_info = self._manifest.get_sandbox_perk_description(perk_hash)
                if perk_info:
                    p_name = perk_info.get("name", "")
                    p_desc = perk_info.get("description", "")
                    if p_name and p_desc:
                        effects.append({"name": p_name, "description": p_desc})

        return {
            "name": display.get("name", ""),
            "nameEn": en_name,
            "description": display.get("description", ""),
            "flavorText": definition.get("flavorText", ""),
            "effects": effects,
            "icon_url": icon_url,
        }


def _absolute_icon_url(value: object) -> str:
    icon = str(value or "").strip()
    if not icon:
        return ""
    if icon.startswith("https://www.bungie.net/"):
        return icon
    if icon.startswith("/"):
        return f"https://www.bungie.net{icon}"
    return ""
