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

logger = get_logger(__name__)

# Catalyst mapping cache
_CATALYST_MAPPING: dict[int, list[int]] | None = None

# Damage type hash → Chinese name
DAMAGE_TYPE_NAMES = {
    1: "动能", 2: "烈日", 3: "电弧", 4: "虚空",
    5: "冰影", 6: "编织", 7: "棱镜",
}

# Ammo type hash → Chinese name
AMMO_TYPE_NAMES = {
    1: "主要", 2: "特殊", 3: "威能",
}

# Tier type hash → Chinese name
TIER_NAMES = {2: "普通", 3: "罕见", 4: "稀有", 5: "传说", 6: "异域"}

# Weapon stat hash → Chinese name
WEAPON_STAT_NAMES = {
    1480404414: "攻击", 3897883278: "防御", 1935470627: "力量",
    2523465841: "速度", 4284893193: "每分钟发射数",
    3614673599: "爆伤", 2961396640: "射程",
    155624089: "稳定性", 943549884: "操控性",
    1345609583: "填装速度", 2715839340: "辅助瞄准",
    3555269338: "变焦", 2714457168: "空中效率",
    1263414103: "后坐方向", 3871231066: "弹匣",
    4043523819: "伤害", 209426660: "护盾",
}

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

    def get_weapon_full_info(self, weapon_name: str) -> dict:
        """Get complete weapon info: stats, intrinsics, catalysts.

        Raises ManifestError if not found or not a weapon.
        """
        results = self._manifest.search(weapon_name, limit=5)
        weapon_def = None
        weapon_zh_name = ""

        for item in results:
            if item.get("itemType") == 3:
                weapon_zh_name = item.get("name", "")
                weapon_def = self._manifest.get_item_definition(item["itemHash"])
                break

        if not weapon_def:
            raise ManifestError(f"找不到武器: {weapon_name}")

        display = weapon_def.get("displayProperties") or {}
        inventory = weapon_def.get("inventory") or {}
        equipping = weapon_def.get("equippingBlock") or {}

        damage_type = weapon_def.get("defaultDamageType", 0)
        ammo_type = equipping.get("ammoType", 0)
        tier_type = inventory.get("tierType", 0)

        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        weapon_en_name = self._get_en_name(weapon_def.get("hash", 0))

        info = {
            "name": weapon_zh_name or display.get("name", ""),
            "nameEn": weapon_en_name,
            "weaponType": weapon_def.get("itemTypeDisplayName", ""),
            "tier": TIER_NAMES.get(tier_type, f"未知({tier_type})"),
            "damageType": DAMAGE_TYPE_NAMES.get(damage_type, f"未知({damage_type})"),
            "ammoType": AMMO_TYPE_NAMES.get(ammo_type, f"未知({ammo_type})"),
            "description": display.get("description", "") or weapon_def.get("flavorText", ""),
            "intrinsicPerks": self._extract_intrinsic_perks(weapon_def),
            "stats": self._extract_weapon_stats(weapon_def),
            "icon_url": icon_url,
        }

        if tier_type == 6:
            info["catalysts"] = self._find_catalysts(weapon_def)

        return info

    def get_weapon_stats(self, weapon_name: str) -> dict:
        """Get weapon investment stats. Raises ManifestError if not found or not weapon.

        用与 analyze/info 相同的方式解析名字：按名字搜索后取第一条**武器**。
        不能按精确名取定义 —— 存在与武器同名的非武器条目（例如「遗产」），
        精确名会拿到那一条，然后误报「不是武器」。
        """
        definition = None
        for item in self._manifest.search(weapon_name, limit=5):
            if item.get("itemType") == 3:
                definition = self._manifest.get_item_definition(item["itemHash"])
                break
        if not definition:
            raise ManifestError(f"找不到武器: {weapon_name}")
        if definition.get("itemType") != 3:
            raise ManifestError(f"{weapon_name} 不是武器")

        display = definition.get("displayProperties", {})
        icon = display.get("icon") or ""
        icon_url = f"https://www.bungie.net{icon}" if icon else ""
        en_name = self._get_en_name(definition.get("hash", 0))

        stats = {}
        for s in definition.get("investmentStats", []):
            stat_hash = s.get("statTypeHash", 0)
            value = s.get("value", 0)
            stat_def = (
                self._manifest.get_localized_definition("DestinyStatDefinition", stat_hash)
                or self._manifest.get_definition("DestinyStatDefinition", stat_hash)
            )
            if stat_def:
                stat_name = (stat_def.get("displayProperties") or {}).get("name", "")
                if stat_name and value != 0:
                    stats[stat_name] = value

        return {
            "name": display.get("name", ""),
            "nameEn": en_name,
            "weaponType": definition.get("itemTypeDisplayName", ""),
            "stats": stats,
            "icon_url": icon_url,
        }

    # ── Catalyst ─────────────────────────────────────────────────────

    def get_catalyst_details(self, weapon_name: str) -> dict:
        """Get catalyst details for an exotic weapon.

        Raises ManifestError if weapon not found or no catalyst exists.
        """
        weapon_results = self._manifest.search(weapon_name, limit=5)
        weapon_def = None
        weapon_zh_name = ""
        weapon_en_name = ""

        for item in weapon_results:
            if item.get("itemType") == 3:
                weapon_zh_name = item.get("name", "")
                weapon_def = self._manifest.get_item_definition(item["itemHash"])
                if weapon_def:
                    weapon_en_name = self._get_en_name(weapon_def.get("hash", 0))
                break

        if not weapon_def:
            raise ManifestError(f"找不到武器: {weapon_name}")

        catalysts = self._find_catalysts(weapon_def)

        if not catalysts:
            raise ManifestError(f"找不到 {weapon_name} 的催化剂（该武器可能没有催化剂）")

        if len(catalysts) > 1:
            result_catalysts = []
            for cat in catalysts:
                cat_info = self._get_catalyst_effect_info(cat["itemHash"])
                if cat_info:
                    result_catalysts.append(cat_info)
            return {
                "weapon": weapon_zh_name or weapon_name,
                "weaponEn": weapon_en_name,
                "count": len(result_catalysts),
                "catalysts": result_catalysts,
            }

        # Single catalyst
        cat_info = self._get_catalyst_effect_info(catalysts[0]["itemHash"])
        if not cat_info:
            raise ManifestError(f"找不到催化剂定义：{weapon_name}")
        return cat_info

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
            "nameEn": en_name,
            "description": display.get("description", ""),
            "flavorText": definition.get("flavorText", ""),
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

    def _extract_weapon_stats(self, definition: dict) -> dict:
        """Extract weapon investment stats."""
        stats = {}
        for s in definition.get("investmentStats", []):
            stat_hash = s.get("statTypeHash", 0)
            value = s.get("value", 0)
            stat_name = WEAPON_STAT_NAMES.get(stat_hash)
            if stat_name and value != 0:
                stats[stat_name] = value
        return stats

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
