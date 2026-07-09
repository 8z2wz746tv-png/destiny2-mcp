"""Fragment service — business logic for fragment and subclass option lookups.

Extracted from fragment_tools.py per Rule 1: tools should not contain
business logic. This service encapsulates all manifest querying and
data transformation for fragments and subclass options.
"""

from __future__ import annotations

from ..logging_config import get_logger
from ..manifest import ManifestManager, CHARACTER_CLASS_MAP
from ..utils.hash_utils import to_signed

logger = get_logger(__name__)

# Element name mapping (input → canonical)
_ELEMENT_MAP = {
    "void": "void", "虚空": "void",
    "solar": "solar", "烈日": "solar",
    "arc": "arc", "电弧": "arc",
    "stasis": "stasis", "冰影": "stasis",
    "strand": "strand", "编织": "strand",
    "prism": "prism", "prismatic": "prism", "棱镜": "prism",
}

# plugCategoryIdentifier → element
_CAT_TO_ELEMENT = {
    "shared.void.fragments": "void",
    "shared.solar.fragments": "solar",
    "shared.arc.fragments": "arc",
    "shared.stasis.trinkets": "stasis",
    "shared.strand.fragments": "strand",
    "shared.prism.fragments": "prism",
    "shared.fragments": "prism",
}

# Component type mapping (input → canonical)
_COMPONENT_TYPE_MAP = {
    "超能": "supers", "super": "supers",
    "近战": "melee", "melee": "melee",
    "手雷": "grenades", "grenade": "grenades",
    "星象": "aspects", "aspect": "aspects",
    "跳跃": "movement", "movement": "movement",
    "职业技能": "class_ability", "class_ability": "class_ability",
}

# Fragment stat hash → Chinese name (Renegades update)
# Standard 6 armor stats — use same names as manifest.py for consistency
_FRAGMENT_STAT_NAMES = {
    2996146975: "武器",
    392767087: "生命",
    1943323491: "职业",
    1735777505: "手雷",
    144602215: "超能",
    4244567218: "近战",
}


class FragmentService:
    """Business logic for fragment and subclass option queries."""

    def __init__(self, manifest: ManifestManager) -> None:
        self._manifest = manifest

    def list_fragments(self, element: str) -> dict:
        """List all fragments for a given element.

        Args:
            element: Element name (void/solar/arc/stasis/strand/prism, or Chinese).

        Returns:
            {"element": str, "count": int, "fragments": list}
        """
        target_element = _ELEMENT_MAP.get(element.lower())
        if not target_element:
            return {"error": f"不支持的元素: {element}，支持: void/solar/arc/stasis/strand/prism"}

        # Search for items whose plugCategoryIdentifier contains 'fragment' or 'trinket'
        all_candidates = []
        for keyword in ("fragment", "trinket"):
            all_candidates.extend(
                self._manifest.find_items_by_plug_category(keyword)
            )

        fragments = []
        for item in all_candidates:
            plug_cat = item.get("plugCategoryIdentifier", "")
            if _CAT_TO_ELEMENT.get(plug_cat) != target_element:
                continue
            info = self._extract_fragment_info(item.get("hash", 0))
            if info:
                fragments.append(info)

        fragments.sort(key=lambda x: x["name"])
        return {"element": target_element, "count": len(fragments), "fragments": fragments}

    def get_fragment_details(self, fragment_name: str) -> dict:
        """Get detailed info for a specific fragment.

        Args:
            fragment_name: Fragment name (Chinese or English, fuzzy match).

        Returns:
            Fragment info dict, or {"error": str} if not found.
        """
        results = self._manifest.search(fragment_name, limit=10)
        for item in results:
            info = self._extract_fragment_info(item["itemHash"])
            if info:
                return info

        return {"error": f"找不到碎片: {fragment_name}"}

    def list_subclass_options(
        self, class_name: str, element: str, component: str
    ) -> dict:
        """List all options for a subclass component.

        Args:
            class_name: Class (hunter/warlock/titan, or Chinese).
            element: Element (void/solar/arc/stasis/strand/prism, or Chinese).
            component: Component type (super/melee/grenade/aspect/movement, or Chinese).

        Returns:
            {"class": str, "element": str, "component": str, "count": int, "options": list}
        """
        target_class = CHARACTER_CLASS_MAP.get(class_name.lower())
        if target_class is not None:
            # Convert int back to string name
            target_class = {0: "titan", 1: "hunter", 2: "warlock"}.get(target_class)

        target_element = _ELEMENT_MAP.get(element.lower())
        target_component = _COMPONENT_TYPE_MAP.get(component.lower())

        if target_class is None:
            return {"error": f"不支持的职业: {class_name}，支持: hunter/warlock/titan"}
        if not target_element:
            return {"error": f"不支持的元素: {element}，支持: void/solar/arc/stasis/strand/prism"}
        if not target_component:
            return {"error": f"不支持的组件: {component}，支持: super/melee/grenade/aspect/movement"}

        if target_component == "grenades":
            target_cat = f"shared.{target_element}.grenades"
        else:
            target_cat = f"{target_class}.{target_element}.{target_component}"

        options = []
        all_candidates = self._manifest.find_items_by_plug_category(target_cat)
        for item in all_candidates:
            plug_cat = item.get("plugCategoryIdentifier", "")
            if plug_cat != target_cat:
                continue

            h = item.get("hash", 0)
            display = item.get("displayProperties") or {}
            en_name = display.get("name", "")
            desc = display.get("description", "")

            # Get description from perks if empty
            if not desc:
                definition = self._manifest.get_item_definition(h)
                if definition:
                    for p in definition.get("perks", []):
                        perk_hash = p.get("perkHash", 0)
                        if perk_hash:
                            perk_info = self._manifest.get_sandbox_perk_description(perk_hash)
                            if perk_info and perk_info.get("description"):
                                desc = perk_info["description"]
                                break

            # Get Chinese name and description
            zh_name = en_name
            zh_desc = desc
            localized = self._manifest.get_localized_definition(
                "DestinyInventoryItemDefinition", h
            )
            if localized:
                zh_name = (localized.get("displayProperties") or {}).get("name", zh_name)
                zh_desc = (localized.get("displayProperties") or {}).get("description", zh_desc)

                if not zh_desc:
                    definition = self._manifest.get_item_definition(h)
                    if definition:
                        for p in definition.get("perks", []):
                            perk_hash = p.get("perkHash", 0)
                            if perk_hash:
                                zh_perk = self._manifest.get_localized_definition(
                                    "DestinySandboxPerkDefinition", perk_hash
                                )
                                if zh_perk:
                                    zh_p_desc = (zh_perk.get("displayProperties") or {}).get("description", "")
                                    if zh_p_desc:
                                        zh_desc = zh_p_desc
                                        break

            if zh_name and "空" not in zh_name and "插槽" not in zh_name:
                options.append({"name": zh_name, "nameEn": en_name, "description": zh_desc})

        options.sort(key=lambda x: x["name"])
        return {
            "class": target_class,
            "element": target_element,
            "component": target_component,
            "count": len(options),
            "options": options,
        }

    # ── Private Helpers ──────────────────────────────────────────────

    def _extract_fragment_info(self, item_hash: int) -> dict | None:
        """Extract fragment info from manifest definition."""
        definition = self._manifest.get_item_definition(item_hash)
        if not definition:
            return None

        plug_cat = (definition.get("plug") or {}).get("plugCategoryIdentifier", "")
        if "fragment" not in plug_cat.lower() and "trinket" not in plug_cat.lower():
            return None

        display = definition.get("displayProperties") or {}
        element = _CAT_TO_ELEMENT.get(plug_cat, "unknown")

        en_name = self._manifest.get_english_name(item_hash)

        # Fragment effect is in perks
        description = ""
        for p in definition.get("perks", []):
            perk_hash = p.get("perkHash", 0)
            if perk_hash:
                perk_info = self._manifest.get_sandbox_perk_description(perk_hash)
                if perk_info:
                    perk_name = perk_info.get("name", "")
                    perk_desc = perk_info.get("description", "")
                    if perk_name and perk_desc:
                        description = perk_desc
                        break

        stats = {}
        for s in definition.get("investmentStats", []):
            stat_hash = s.get("statTypeHash", 0)
            value = s.get("value", 0)
            stat_name = _FRAGMENT_STAT_NAMES.get(stat_hash)
            if stat_name and value != 0:
                stats[stat_name] = value

        icon = (display.get("icon") or "")
        icon_url = f"https://www.bungie.net{icon}" if icon else ""

        return {
            "name": display.get("name", ""),
            "nameEn": en_name,
            "element": element,
            "description": description,
            "stats": stats,
            "hash": item_hash,
            "icon_url": icon_url,
        }
