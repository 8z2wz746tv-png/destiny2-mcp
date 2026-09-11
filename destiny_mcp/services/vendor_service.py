"""Vendor service — vendor inventory queries.

References DIM's d2-vendors.ts and vendor-item.ts for implementation details.

Two response shapes, decided by whether vendor_name resolves to one vendor:

- menu:   one line per vendor (no items, no extra API calls), used when nothing was
          named or the name matched several vendors
- detail: one vendor's tabs, rank and shelf, with a per-vendor item cap and an
          explicit truncated flag

Sub-pages (Banshee's focusing screens, Eververse sub-shelves) are offered as
next_actions instead of being silently merged into the main vendor's shelf.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..bungie_client import BungieClient
from ..exceptions import CharacterNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import (
    PerkInfo,
    VendorCost,
    VendorInfo,
    VendorInventoryResponse,
    VendorSaleItem,
)
from ..player_resolver import PlayerResolver
from .vendor_menu import (
    build_rank,
    cap_warnings,
    category_entries,
    clamp_limit,
    close_vendor_names,
    match_vendors,
    menu_sort_key,
    vendor_identities,
)

if TYPE_CHECKING:
    from .perk_service import PerkService

logger = get_logger(__name__)

# Major vendor hashes (from DIM's d2-known-values.ts)
# Both English keys and Chinese aliases point to the same hash.
VENDOR_HASHES: dict[str, int] = {
    "Eververse": 3361454721,
    "银币商店": 3361454721,
    "泰斯·艾夫瑞斯": 3361454721,
    "Banshee": 672118013,
    "枪匠": 672118013,
    "班西-44": 672118013,
    "Xur": 2190858386,
    "祖尔": 2190858386,
    "老九": 2190858386,
    "仄": 2190858386,
    "Ada": 350061650,
    "艾达": 350061650,
    "艾达-1": 350061650,
    "Rahool": 2255782930,
    "拉乎尔": 2255782930,
    "拉乎尔大师": 2255782930,
    "Drifter": 248695599,
    "漂流者": 248695599,
    "浪客": 248695599,
    "Zavala": 69482069,
    "扎瓦拉": 69482069,
    "萨瓦拉": 69482069,
    "指挥官萨瓦拉": 69482069,
    "Shaxx": 3603221665,
    "沙克斯": 3603221665,
}

# Vendor hash → Chinese display name (DIM-style localization)
# 从 manifest 中文版查询的官方翻译
VENDOR_DISPLAY_NAMES: dict[int, str] = {
    3361454721: "泰斯·艾夫瑞斯",  # Eververse
    672118013:  "班西-44",        # Banshee-44
    2190858386: "仄",             # Xur
    350061650:  "艾达-1",         # Ada-1
    2255782930: "拉乎尔大师",     # Master Rahool
    248695599:  "浪客",           # The Drifter
    69482069:   "指挥官萨瓦拉",   # Commander Zavala
    3603221665: "领主沙克斯",     # Lord Shaxx
}

# Currency hashes
CURRENCY_NAMES: dict[int, str] = {
    3159615086: "微光",
    1022552290: "传说碎片",
    2817410913: "光尘",
    3147280338: "银币",
    4257549927: "升华晶石",
    3853748946: "升华合金",
    4257549926: "增强棱镜",
}

# Augments bit flags (DestinyVendorItemState)
AUGMENTS_OWNED = 1
AUGMENTS_LOCKED = 2

# Main vendor → displayCategories tag keywords for matching sub-vendors.
# When querying a main vendor, sub-vendors whose tags match are included.
SUB_VENDOR_TAGS: dict[int, list[str]] = {
    3603221665: ["crucible", "arcite"],      # Shaxx
    69482069:   ["vanguard", "nightfall"],    # Zavala
    248695599:  ["gambit"],                   # Drifter
    2255782930: ["decryption"],               # Rahool
    765357505:  ["trials"],                   # Saint-14
    350061650:  ["iron banner"],              # Ada / Saladin
}

# Main vendor → vendorIdentifier prefixes for matching sub-vendors.
# More precise than tag matching — uses the vendor's unique identifier.
SUB_VENDOR_IDENTIFIERS: dict[int, list[str]] = {
    672118013:  ["GUNSMITH"],                 # Banshee-44
    4230408743: ["LIGHT_AND_DARK", "FATE"],   # Monument to Lost Lights
    2190858386: ["TOWER_NINE"],               # Xur (仄)
    3361454721: ["EVERVERSE"],                # Tess Everis' many sub-shelves
}

# Sub-vendor hash → Chinese display name
SUB_VENDOR_DISPLAY_NAMES: dict[int, str] = {
    1092954315: "传承装备 · 通用武器",
    2595490586: "传承装备 · 熔炉",
    2672927612: "传承装备 · 铁旗",
    2906014866: "传承装备 · 智谋",
    3444362755: "传承装备 · 先锋",
    4140351452: "传承装备 · 试炼",
    1248953136: "聚焦解密",
    3388267042: "聚焦解密 · 铁旗",
    444807002:  "聚焦解密 · 苍白之心",
    2484291326: "武器聚焦",           # Gunsmith Weapon Focusing (Banshee)
    537912098:  "更多奇异优惠",       # More Strange Offers (Xur)
    3751514131: "奇异装备优惠",       # Strange Gear Offers (Xur)
}

# Xur availability: Friday 17:00 UTC — Tuesday 17:00 UTC
_XUR_WEEKDAYS = {4, 5, 6, 0}  # Fri, Sat, Sun, Mon

# How many sub-pages to offer as follow-up calls before it becomes a wall of text.
_SUB_PAGE_HINTS = 5


def _is_xur_available() -> bool:
    """Check if Xur is currently available.

    Xur arrives Friday 17:00 UTC and leaves Tuesday 17:00 UTC.
    """
    now = datetime.now(timezone.utc)
    wd = now.weekday()
    # Friday before 17:00 UTC — Xur hasn't arrived yet
    if wd == 4 and now.hour < 17:
        return False
    if wd in _XUR_WEEKDAYS:
        return True
    # Tuesday before 17:00 UTC — Xur still here
    if wd == 1 and now.hour < 17:
        return True
    return False


def _vendor_failure_strings(
    response_failure_strings: list[str],
    vendor_def: dict,
) -> list[str]:
    """Return purchase failure strings in Bungie's index order."""
    if response_failure_strings:
        return response_failure_strings

    # Vendor definitions carry a flat list; older fixtures only had
    # failureCategories, so keep that path alive as a fallback.
    flat = vendor_def.get("failureStrings")
    if isinstance(flat, list) and flat:
        return [str(entry) for entry in flat]

    fallback: list[str] = []
    for category in vendor_def.get("failureCategories", []):
        fallback.extend(category.get("failureStrings", []))
    return fallback


def _vendor_socket_data(item_components: object) -> dict | None:
    """Return live socket records from a vendor component payload."""
    if not isinstance(item_components, dict):
        return None
    sockets = item_components.get("sockets")
    if not isinstance(sockets, dict):
        return None
    data = sockets.get("data")
    return data if isinstance(data, dict) else None


class VendorService:
    """Operations for querying vendor inventory."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        perk_svc: "PerkService | None" = None,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._perk_svc = perk_svc

    def _sale_items_include_weapon(self, sale_items: object) -> bool:
        if not isinstance(sale_items, dict):
            return False
        return any(
            (self._manifest.get_item_info(item.get("itemHash", 0)) or {}).get(
                "itemType"
            ) == 3
            for item in sale_items.values()
            if isinstance(item, dict)
        )

    def _vendor_labels(self) -> dict[int, str]:
        """Chinese labels, so vendors the manifest leaves unnamed still have a name."""
        labels = dict(VENDOR_DISPLAY_NAMES)
        labels.update(SUB_VENDOR_DISPLAY_NAMES)
        return labels

    def _progression_name(self, progression_hash: int) -> str:
        """Name of a reputation track, e.g. 先锋; empty when the manifest has no entry."""
        if not progression_hash:
            return ""
        definition = self._manifest.get_definition(
            "DestinyProgressionDefinition", progression_hash
        )
        if not isinstance(definition, dict):
            return ""
        return str((definition.get("displayProperties") or {}).get("name") or "")

    def _submenu_targets(
        self,
        sale_items: dict,
        api_categories: list,
    ) -> dict[int, int]:
        """Category index → vendor hash for tabs that open another vendor page.

        A sub-page tab holds a single placeholder item whose definition points at the
        vendor it opens (preview.previewVendorHash). Tabs whose placeholder points
        nowhere (help tiles, pursuits) are not sub-pages.
        """
        targets: dict[int, int] = {}
        for entry in api_categories or []:
            index = entry.get("displayCategoryIndex")
            item_indexes = entry.get("itemIndexes") or []
            if not isinstance(index, int) or len(item_indexes) != 1:
                continue
            sale_item = sale_items.get(str(item_indexes[0])) or {}
            item_hash = sale_item.get("itemHash")
            if not item_hash:
                continue
            definition = self._manifest.get_definition(
                "DestinyInventoryItemDefinition", item_hash
            )
            target = ((definition or {}).get("preview") or {}).get("previewVendorHash") or 0
            if target and self._manifest.get_vendor_definition(int(target)):
                targets[index] = int(target)
        return targets

    def _sub_vendor_hashes(
        self,
        main_hash: int,
        vendors_data: dict,
        grouped_hashes: set[int],
    ) -> set[int]:
        """Sibling vendor pages belonging to a main vendor (e.g. Banshee's focusing screens)."""
        tag_keywords = SUB_VENDOR_TAGS.get(main_hash)
        id_prefixes = SUB_VENDOR_IDENTIFIERS.get(main_hash)
        if not tag_keywords and not id_prefixes:
            return set()

        matched: set[int] = set()
        for vendor_hash_str in vendors_data:
            vendor_hash = int(vendor_hash_str)
            if vendor_hash in grouped_hashes:
                continue  # grouped vendors are main vendors, not sub-pages
            vendor_def = self._manifest.get_vendor_definition(vendor_hash)
            if not vendor_def:
                continue
            if tag_keywords:
                tags_text = " ".join(
                    (
                        (d.get("displayProperties") or {}).get("name", "")
                        or d.get("identifier", "")
                    )
                    for d in vendor_def.get("displayCategories", [])
                ).lower()
                if any(keyword in tags_text for keyword in tag_keywords):
                    matched.add(vendor_hash)
                    continue
            if id_prefixes:
                vendor_id = (vendor_def.get("vendorIdentifier") or "").upper()
                if any(vendor_id.startswith(prefix) for prefix in id_prefixes):
                    matched.add(vendor_hash)
        return matched

    async def _ensure_vendor_components(
        self,
        mid: str,
        mtype: int,
        char_id: str,
        vendor_hash: int,
        existing: object,
    ) -> tuple[object, list[str]]:
        """Fetch the live socket payload for one vendor when it is missing."""
        if _vendor_socket_data(existing):
            return existing, []
        try:
            detail = await self._bungie.fetch_vendor_components(
                mid, mtype, char_id, vendor_hash
            )
        except Exception as exc:
            logger.warning(
                "GetVendor components failed for vendor=%s: %s", vendor_hash, exc
            )
            return existing, [f"商人 {vendor_hash} 的实际 Perk 组件读取失败，已省略 Perk。"]
        components = detail.get("itemComponents") if isinstance(detail, dict) else None
        if _vendor_socket_data(components) is None:
            return existing, [
                f"商人 {vendor_hash} 未返回可核对的实际 socket，已省略 Perk。"
            ]
        return components, []

    async def _resolve_vendor_character(
        self,
        membership_id: str,
        membership_type: int,
        character: str,
    ) -> tuple[str, str]:
        """Resolve an explicit character or choose the most recently played one."""
        if str(character or "").strip():
            resolved = str(character).strip()
            return resolved, await self._resolver.resolve_character_id(
                membership_id, membership_type, resolved
            )

        profile = await self._bungie.get_profile(membership_id, membership_type, [200])
        chars = profile.get("characters", {}).get("data", {})
        if not isinstance(chars, dict) or not chars:
            raise CharacterNotFoundError("可用角色")
        candidates = [
            (str(character_id), character_data)
            for character_id, character_data in chars.items()
            if isinstance(character_data, dict)
        ]
        if not candidates:
            raise CharacterNotFoundError("可用角色")
        character_id, character_data = max(
            candidates,
            key=lambda candidate: (
                str(candidate[1].get("dateLastPlayed") or ""),
                candidate[0],
            ),
        )
        return class_type_name(int(character_data.get("classType", -1))), character_id

    def _build_sale_item(
        self,
        sale_item: dict,
        item_index: int,
        index_to_category: dict[int, int],
        vendor_failure_strings: list[str],
        vendor_item_components: object,
        warnings: list[str],
    ) -> VendorSaleItem:
        """One shelf item, priced, gated honestly, and with live perks when available."""
        vendor_item_index = sale_item.get("vendorItemIndex")
        if not isinstance(vendor_item_index, int):
            vendor_item_index = item_index
        item_hash = sale_item.get("itemHash", 0)
        item_info = self._manifest.get_item_info(item_hash) or {}
        item_name = self._manifest.get_item_name(item_hash)

        costs: list[VendorCost] = []
        for cost in sale_item.get("costs", []) or []:
            cost_hash = cost.get("itemHash", 0)
            costs.append(VendorCost(
                item_hash=cost_hash,
                item_name=CURRENCY_NAMES.get(
                    cost_hash, self._manifest.get_item_name(cost_hash)
                ),
                quantity=cost.get("quantity", 0),
            ))

        # failureIndexes point into the vendor's failureStrings; empty means buyable.
        # 上游有些槽位的文案本身就是空串（帮派按钮之类），过滤掉再判断，
        # 否则会出现「不可买但原因是空字符串」这种等于没说的答案。
        failure_indexes = sale_item.get("failureIndexes") or []
        failures: list[str] = [
            str(vendor_failure_strings[index]).strip()
            for index in failure_indexes
            if isinstance(index, int) and 0 <= index < len(vendor_failure_strings)
        ]
        failures = [text for text in failures if text]
        if failure_indexes and not failures:
            failures.append(f"上游标记为不可购买（原因索引 {list(failure_indexes)}）")

        owned = bool(sale_item.get("augments", 0) & AUGMENTS_OWNED)

        # Only live vendor socket data describes the actual sale roll.
        perks: list[PerkInfo] | None = None
        if item_info.get("itemType") == 3:  # Weapon
            perks = self._extract_perks_from_vendor_components(
                item_hash,
                vendor_item_index,
                vendor_item_components,
            )
            if perks is None:
                warning = f"商品“{item_name}”未返回可核对的实际 socket，已省略 Perk。"
                if warning not in warnings:
                    warnings.append(warning)

        # manifest already stores icon as full Bungie CDN URL
        icon_url = item_info.get("icon", "")

        return VendorSaleItem(
            vendor_item_index=vendor_item_index,
            item_hash=item_hash,
            name=item_name,
            item_type=item_info.get("itemTypeNameDisplay", "") or item_info.get("itemTypeName", ""),
            tier={5: "传说", 6: "异域"}.get(item_info.get("tier", 0), ""),
            icon=icon_url,
            icon_url=icon_url,
            costs=costs,
            owned=owned,
            can_be_sold=not failure_indexes,
            failure_reasons=failures,
            category_index=index_to_category.get(item_index),
            sale_status=sale_item.get("saleStatus"),
            perks=perks,
        )

    async def get_vendor_inventory(
        self,
        player_name: str,
        character: str,
        vendor_name: str = "",
        *,
        compact: bool = True,
        limit: int | None = None,
    ) -> VendorInventoryResponse:
        """Get vendor inventory for a character.

        Args:
            player_name: Bungie name.
            character: Character name (vendor inventory varies by character).
            vendor_name: Vendor name, alias, identifier fragment, or hash. Empty lists
                every vendor with something on the shelf (menu mode, no items).
            compact: Strip fields that only matter for rendering.
            limit: Max vendors in menu mode, max items per vendor in detail mode.

        Returns:
            VendorInventoryResponse; `mode` says which shape came back.
        """
        logger.info("get_vendor_inventory: player=%s char=%s vendor=%s",
                     player_name, character, vendor_name)

        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        character, char_id = await self._resolve_vendor_character(mid, mtype, character)

        raw = await self._bungie.fetch_vendors(mid, mtype, char_id)
        vendors_data = raw.get("vendors", {}).get("data", {})
        sales_data = raw.get("sales", {}).get("data", {})
        categories_data = raw.get("categories", {}).get("data", {})
        components_by_vendor = raw.get("itemComponents", {})
        response_failure_strings = raw.get("failureStrings", [])

        grouped_hashes: set[int] = set()
        for group in raw.get("vendorGroups", {}).get("data", {}).get("groups", []):
            for vendor_hash in group.get("vendorHashes", []) or []:
                grouped_hashes.add(int(vendor_hash))

        identities = vendor_identities(
            vendors_data,
            self._manifest.get_vendor_definition,
            label_overrides=self._vendor_labels(),
        )
        identity_by_hash = {identity.vendor_hash: identity for identity in identities}

        def label_for(vendor_hash: int) -> str:
            identity = identity_by_hash.get(int(vendor_hash))
            return identity.label if identity else f"#{vendor_hash}"

        warnings: list[str] = []
        next_actions: list[str] = []
        question: str | None = None
        mode = "menu"
        selected = list(identities)

        if vendor_name.strip():
            match = match_vendors(vendor_name, identities, VENDOR_HASHES)
            if match.how == "absent":
                # Known vendor hash, just not in this character's payload today.
                known = self._manifest.get_vendor_definition(match.hash_hint or 0) or {}
                known_name = (known.get("displayProperties") or {}).get("name") or label_for(
                    match.hash_hint or 0
                )
                warnings.append(
                    f"商人“{known_name}”（hash={match.hash_hint}）这次没有返回，"
                    "可能是本周期不在、或该角色看不到这页。"
                )
                return VendorInventoryResponse(
                    player_name=player_name,
                    character=class_type_name(resolve_character_name(character)),
                    mode="menu",
                    total_vendors=len(identities),
                    question="要改看哪个商人？",
                    next_actions=[
                        f'world_assistant(intent="vendor", vendor_name="{identity.vendor_hash}")'
                        f"  # {identity.label}"
                        for identity in identities[:3]
                    ],
                    warnings=warnings,
                )
            if match.how == "none":
                suggestions = close_vendor_names(vendor_name, identities)
                if suggestions:
                    warnings.append(
                        f"未识别商人“{vendor_name}”；相近的名字：{'、'.join(suggestions)}。"
                    )
                    next_actions = [
                        f'world_assistant(intent="vendor", vendor_name="{name}")'
                        for name in suggestions[:3]
                    ]
                else:
                    warnings.append(f"未识别商人“{vendor_name}”，也没有相近的名字。")
                return VendorInventoryResponse(
                    player_name=player_name,
                    character=class_type_name(resolve_character_name(character)),
                    mode="menu",
                    total_vendors=len(identities),
                    question=f"没找到商人“{vendor_name}”，要查哪一个？",
                    next_actions=next_actions,
                    warnings=warnings,
                )
            selected = list(match.identities)
            if len(selected) == 1:
                mode = "detail"
            else:
                question = f"“{vendor_name}”匹配到 {len(selected)} 个商人，要查哪一个？"
                warnings.append(f"“{vendor_name}”匹配到 {len(selected)} 个商人，先列出候选。")
                next_actions = [
                    f'world_assistant(intent="vendor", vendor_name="{identity.vendor_hash}")'
                    f"  # {identity.label}"
                    for identity in selected[:5]
                ]
        else:
            question = "要查看哪个商人？可以直接传商人名字或 hash。"

        detail_mode = mode == "detail"
        item_limit = clamp_limit(limit, mode)
        results: list[VendorInfo] = []

        for identity in selected:
            vendor_hash = identity.vendor_hash
            if vendor_hash == VENDOR_HASHES["Xur"] and not _is_xur_available():
                warnings.append("仄（Xur）本周期不在，周五 17:00 UTC 后才回来；他的货架已跳过。")
                continue

            vendor_data = vendors_data.get(str(vendor_hash)) or {}
            vendor_def = self._manifest.get_vendor_definition(vendor_hash) or {}
            sale_items_raw = (sales_data.get(str(vendor_hash)) or {}).get("saleItems") or {}
            api_categories = (categories_data.get(str(vendor_hash)) or {}).get("categories") or []
            if not sale_items_raw and not detail_mode:
                continue

            vendor_failure_strings = _vendor_failure_strings(
                response_failure_strings, vendor_def
            )

            live_components = components_by_vendor.get(str(vendor_hash))
            if detail_mode and self._sale_items_include_weapon(sale_items_raw):
                live_components, component_warnings = await self._ensure_vendor_components(
                    mid, mtype, char_id, vendor_hash, live_components
                )
                warnings.extend(component_warnings)

            index_to_category: dict[int, int] = {}
            for entry in api_categories:
                category_index = entry.get("displayCategoryIndex")
                for item_index in entry.get("itemIndexes") or []:
                    index_to_category[int(item_index)] = category_index

            categories = category_entries(
                api_categories,
                vendor_def.get("displayCategories"),
                self._submenu_targets(sale_items_raw, api_categories),
                available_vendors=identity_by_hash,
                vendor_label=label_for,
            )
            # 装饰性 tab（帮助按钮/纯展示）不进 categories；挂在它们下面的条目也不是商品，
            # 否则会出现 sale_items[].category_index 指向一个没列出来的分类。
            listed_indexes = {category.index for category in categories}
            decorative_indexes = {
                int(entry["displayCategoryIndex"])
                for entry in api_categories
                if entry.get("itemIndexes") and entry.get("displayCategoryIndex") not in listed_indexes
            }

            items: list[VendorSaleItem] = []
            total_items = 0
            purchasable_items = 0
            hidden_items = 0
            for item_index_str, sale_item in sale_items_raw.items():
                if not isinstance(sale_item, dict):
                    continue
                if index_to_category.get(int(item_index_str)) in decorative_indexes:
                    hidden_items += 1
                    continue
                total_items += 1
                if not (sale_item.get("failureIndexes") or []):
                    purchasable_items += 1
                if detail_mode and len(items) < item_limit:
                    items.append(self._build_sale_item(
                        sale_item,
                        int(item_index_str),
                        index_to_category,
                        vendor_failure_strings,
                        live_components,
                        warnings,
                    ))

            results.append(VendorInfo(
                vendor_hash=vendor_hash,
                name=identity.label,
                identifier=identity.identifier,
                icon=(vendor_def.get("displayProperties") or {}).get("icon", ""),
                next_refresh=vendor_data.get("nextRefreshDate", ""),
                rank=build_rank(vendor_data.get("progression"), self._progression_name),
                categories=categories,
                total_items=total_items,
                purchasable_items=purchasable_items,
                hidden_items=hidden_items,
                truncated=detail_mode and total_items > len(items),
                sale_items=items,
            ))

        if detail_mode and results:
            # Offer the sub-pages of this vendor instead of merging their shelves in.
            sub_pages: dict[int, str] = {}
            for category in results[0].categories:
                if category.kind == "submenu" and category.target_vendor_hash:
                    sub_pages[category.target_vendor_hash] = (
                        f"{category.name} → {category.target_vendor_name or label_for(category.target_vendor_hash)}"
                    )
            for sub_hash in sorted(
                self._sub_vendor_hashes(results[0].vendor_hash, vendors_data, grouped_hashes)
            ):
                sub_pages.setdefault(sub_hash, label_for(sub_hash))
            sub_pages.pop(results[0].vendor_hash, None)
            for sub_hash, label in list(sub_pages.items())[:_SUB_PAGE_HINTS]:
                if sub_hash in identity_by_hash:
                    next_actions.append(
                        f'world_assistant(intent="vendor", vendor_name="{sub_hash}")  # {label}'
                    )
                else:
                    next_actions.append(f"子页面“{label}”本次没有返回，暂时看不到它的货架。")

        if mode == "menu":
            results.sort(key=menu_sort_key)
            returned = results[:item_limit]
            truncated = len(results) > len(returned)
            if not vendor_name.strip():
                next_actions.extend(
                    f'world_assistant(intent="vendor", vendor_name="{vendor.vendor_hash}")'
                    f"  # {vendor.name}"
                    for vendor in returned[:3]
                )
                next_actions.append("要按名字找：vendor_name 传商人名字或名字片段即可。")
        else:
            returned = results
            truncated = False

        response = VendorInventoryResponse(
            player_name=player_name,
            character=class_type_name(resolve_character_name(character)),
            mode=mode,
            vendors=returned,
            total_vendors=len(results),
            returned_vendors=len(returned),
            truncated=truncated,
            question=question,
            next_actions=next_actions,
            warnings=cap_warnings(warnings),
        )
        logger.info(
            "get_vendor_inventory: mode=%s vendors=%d items=%d",
            mode,
            len(returned),
            sum(len(vendor.sale_items) for vendor in returned),
        )
        return self._compact_response(response) if compact else response

    def _extract_perks_from_vendor_components(
        self,
        item_hash: int,
        vendor_item_index: int,
        item_components: object,
    ) -> list[PerkInfo] | None:
        """Read actual perk plugs from a live vendor sale item."""
        sockets_data = _vendor_socket_data(item_components)
        if sockets_data is None:
            return None
        socket_component = sockets_data.get(str(vendor_item_index))
        if socket_component is None:
            socket_component = sockets_data.get(vendor_item_index)
        if not isinstance(socket_component, dict):
            return None
        socket_states = socket_component.get("sockets")
        if not isinstance(socket_states, list):
            return None

        perks: list[PerkInfo] = []
        for socket_state in socket_states:
            if not isinstance(socket_state, dict):
                continue
            plug_hash = socket_state.get("plugHash")
            if (
                not isinstance(plug_hash, int)
                or isinstance(plug_hash, bool)
                or plug_hash <= 0
            ):
                continue

            # Skip intrinsic traits, trackers, shaders, mods
            cat_id = self._manifest.get_plug_category_identifier(plug_hash) or ""
            cat_lower = cat_id.lower()
            if any(skip in cat_lower for skip in ["shader", "mod", "tracker", "intrinsic", "masterwork"]):
                continue

            info = self._manifest.get_item_info(plug_hash)
            if not info:
                logger.debug("No item info for plug hash %d, skipping", plug_hash)
                continue

            name = str(info.get("name") or "").strip()
            if not name:
                continue
            desc = ""
            sandbox = self._manifest.get_sandbox_perk_description(plug_hash)
            if sandbox:
                desc = sandbox.get("description", "")

            perk = PerkInfo(
                plug_hash=plug_hash,
                name=name,
                description=desc,
                plug_category=cat_id,
                icon_url=info.get("icon", ""),
            )

            # Annotate god roll if wish list service available
            if self._perk_svc:
                self._perk_svc.annotate_god_roll(item_hash, plug_hash, perk)

            perks.append(perk)

        return perks

    @staticmethod
    def _compact_response(response: VendorInventoryResponse) -> VendorInventoryResponse:
        """Compress response for LLM consumption — strip large/unnecessary fields.

        Item hashes, costs, purchase state and category indexes all stay: callers
        chain them into other tools. Only rendering-only fields and perk text go.
        """
        for vendor in response.vendors:
            vendor.icon = ""
            vendor.next_refresh = ""
            for item in vendor.sale_items:
                item.icon = ""
                item.vendor_item_index = 0
                # Compress perks to names only, mark god rolls
                if item.perks:
                    names = []
                    for p in item.perks:
                        tag = ""
                        if p.god_roll_pve:
                            tag = " [PvE]"
                        if p.god_roll_pvp:
                            tag = " [PvP]"
                        names.append(p.name + tag)
                    item.perks = [PerkInfo(plug_hash=0, name=n) for n in names]
        return response
