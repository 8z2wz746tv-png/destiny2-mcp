"""Fragment service — business logic for fragment and subclass option lookups.

Extracted from fragment_tools.py per Rule 1: tools should not contain
business logic. This service encapsulates all manifest querying and
data transformation for fragments and subclass options.

参数不合法或查不到时**抛异常**，不返回 `{"error": ...}`：后者会被上层包进
`ok=true` 的信封里，模型看到的是"成功"，于是把错误原文当结果念出来。
"""

from __future__ import annotations

from ..exceptions import DefinitionNotFoundError, SubclassError
from ..logging_config import get_logger
from ..manifest import ManifestManager, CHARACTER_CLASS_MAP

logger = get_logger(__name__)

# Element name mapping (input → canonical)
# 元素名别名表（输入 → 规范名）。
# 中文以**游戏客户端**的叫法为准：strand=缚丝（「编织」是早期写法，保留兼容）。
# 权威来源是 `manifest_names` 的伤害类型名称表（那边也是缚丝），这里只是输入别名。
_ELEMENT_MAP = {
    "void": "void", "虚空": "void",
    "solar": "solar", "烈日": "solar",
    "arc": "arc", "电弧": "arc",
    "stasis": "stasis", "冰影": "stasis",
    "strand": "strand", "缚丝": "strand", "编织": "strand",
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
    "超能": "supers", "super": "supers", "supers": "supers",
    "近战": "melee", "melee": "melee",
    "手雷": "grenades", "grenade": "grenades", "grenades": "grenades",
    "星象": "aspects", "aspect": "aspects", "aspects": "aspects",
    "跳跃": "movement", "移动": "movement", "movement": "movement",
    "职业技能": "class_ability", "class_ability": "class_ability",
}

# 报错时列出的"认得的写法"（与上面两张表保持同步）
_ELEMENT_HINT = "void/solar/arc/stasis/strand/prism（或 虚空/烈日/电弧/冰影/缚丝/棱镜）"
_COMPONENT_HINT = (
    "super/melee/grenade/aspect/movement/class_ability"
    "（或 超能/近战/手雷/星象/跳跃/职业技能）"
)

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
            raise SubclassError(
                f"element 缺失或不认识（给的是 {element!r}），支持 {_ELEMENT_HINT}"
            )

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
            Fragment info dict.

        Raises:
            ItemNotFoundError: 名字在 Manifest 里找不到对应碎片。
        """
        results = self._manifest.search(fragment_name, limit=10)
        for item in results:
            info = self._extract_fragment_info(item["itemHash"])
            if info:
                return info

        raise DefinitionNotFoundError(fragment_name, "没有匹配的碎片。")

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

        # 缺什么就说什么，而且一次性说完：以前 class 为空时先报"不支持的职业"，
        # 而调用方真正漏的是 element/component（报错指错字段会把人带偏）。
        problems: list[str] = []
        if not target_element:
            problems.append(f"element 缺失或不认识（给的是 {element!r}），支持 {_ELEMENT_HINT}")
        if not target_component:
            problems.append(
                f"component 缺失或不认识（给的是 {component!r}），支持 {_COMPONENT_HINT}"
            )
        if target_class is None:
            problems.append(
                f"职业缺失或不认识（给的是 {class_name!r}），支持 hunter/warlock/titan 或中文职业名"
            )
        if problems:
            raise SubclassError("；".join(problems) + "。")

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
