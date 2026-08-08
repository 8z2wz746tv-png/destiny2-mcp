"""Vendor service — vendor inventory queries.

References DIM's d2-vendors.ts and vendor-item.ts for implementation details.
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

    # Legacy fallback for older tests/local fixtures that only include
    # vendor-definition failureCategories.
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

    async def get_vendor_inventory(
        self,
        player_name: str,
        character: str,
        vendor_name: str = "",
        *,
        compact: bool = True,
    ) -> VendorInventoryResponse:
        """Get vendor inventory for a character.

        Args:
            player_name: Bungie name.
            character: Character name (vendor inventory varies by character).
            vendor_name: Optional vendor filter (e.g. 'Xur', 'Banshee').

        Returns:
            VendorInventoryResponse with vendor info and sale items.
        """
        logger.info("get_vendor_inventory: player=%s char=%s vendor=%s",
                     player_name, character, vendor_name)

        # Resolve player and character
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        character, char_id = await self._resolve_vendor_character(mid, mtype, character)

        # Determine which vendors to fetch
        filter_hashes: list[int] | None = None
        if vendor_name:
            q = vendor_name.strip().lower()
            for key, h in VENDOR_HASHES.items():
                if q == key.lower():
                    filter_hashes = [h]
                    break
            if filter_hashes is None:
                logger.warning("Unknown vendor requested: %s", vendor_name)
                return VendorInventoryResponse(
                    player_name=player_name,
                    character=class_type_name(resolve_character_name(character)),
                    warnings=[f"未识别商人“{vendor_name}”，本次未查询任何商品。"],
                )

        # Fetch vendors from API
        raw = await self._bungie.fetch_vendors(mid, mtype, char_id)

        # Parse response
        vendors_data = raw.get("vendors", {}).get("data", {})
        sales_data = raw.get("sales", {}).get("data", {})
        item_components = raw.get("itemComponents", {})
        response_failure_strings = raw.get("failureStrings", [])

        results: list[VendorInfo] = []
        warnings: list[str] = []

        # Client-side filter: only process requested vendor(s)
        allowed_hashes: set[int] = set(filter_hashes or [])

        # If a single main vendor was requested, also include matching sub-vendors
        if filter_hashes and len(filter_hashes) == 1:
            main_hash = filter_hashes[0]
            tag_keywords = SUB_VENDOR_TAGS.get(main_hash)
            id_prefixes = SUB_VENDOR_IDENTIFIERS.get(main_hash)

            if tag_keywords or id_prefixes:
                # Collect all grouped vendor hashes to identify ungrouped ones
                grouped_hashes: set[int] = set()
                for g in raw.get("vendorGroups", {}).get("data", {}).get("groups", []):
                    for h in g.get("vendorHashes", []):
                        grouped_hashes.add(h)

                # Scan ungrouped vendors for matching displayCategories tags or vendorIdentifier
                for vh_str in vendors_data:
                    vh = int(vh_str)
                    if vh in grouped_hashes:
                        continue  # Skip main vendors
                    vdef = self._manifest.get_vendor_definition(vh)
                    if not vdef:
                        logger.debug("Sub-vendor %d: no definition, skipping", vh)
                        continue

                    matched = False

                    # Match by displayCategories tags
                    if tag_keywords:
                        dc_list = vdef.get("displayCategories", [])
                        tags_text = " ".join(
                            (d.get("displayProperties", {}).get("name", "") or d.get("identifier", ""))
                            for d in dc_list
                        ).lower()
                        if any(kw in tags_text for kw in tag_keywords):
                            matched = True

                    # Match by vendorIdentifier prefix
                    if not matched and id_prefixes:
                        vendor_id = (vdef.get("vendorIdentifier") or "").upper()
                        if any(vendor_id.startswith(prefix) for prefix in id_prefixes):
                            matched = True

                    if matched:
                        allowed_hashes.add(vh)
                        logger.info("Sub-vendor matched: %s", vh)

        if vendor_name:
            for vendor_hash_str in vendors_data:
                vendor_hash = int(vendor_hash_str)
                if allowed_hashes and vendor_hash not in allowed_hashes:
                    continue
                vendor_sales = sales_data.get(vendor_hash_str, {}).get("saleItems", {})
                if not self._sale_items_include_weapon(vendor_sales):
                    continue
                existing = (
                    item_components.get(vendor_hash_str)
                    if isinstance(item_components, dict)
                    else None
                )
                if _vendor_socket_data(existing):
                    continue
                try:
                    detail = await self._bungie.fetch_vendor_components(
                        mid, mtype, char_id, vendor_hash
                    )
                except Exception as exc:
                    logger.warning(
                        "GetVendor components failed for vendor=%s: %s",
                        vendor_hash,
                        exc,
                    )
                    warnings.append(
                        f"商人 {vendor_hash} 的实际 Perk 组件读取失败，已省略 Perk。"
                    )
                    continue
                components = detail.get("itemComponents") if isinstance(detail, dict) else None
                if _vendor_socket_data(components) is None:
                    warnings.append(
                        f"商人 {vendor_hash} 未返回可核对的实际 socket，已省略 Perk。"
                    )
                    continue
                if not isinstance(item_components, dict):
                    item_components = {}
                item_components[vendor_hash_str] = components
        elif any(
            self._sale_items_include_weapon(sales.get("saleItems"))
            for sales in sales_data.values()
            if isinstance(sales, dict)
        ):
            warnings.append(
                "未指定商人时不批量请求所有单商人详情；缺失的实际 Perk 已省略。"
            )

        for vendor_hash_str, vendor_data in vendors_data.items():
            vendor_hash = int(vendor_hash_str)
            if allowed_hashes and vendor_hash not in allowed_hashes:
                continue

            # Xur availability check
            if vendor_hash == VENDOR_HASHES["Xur"] and not _is_xur_available():
                continue

            vendor_def = self._manifest.get_vendor_definition(vendor_hash)
            if not vendor_def:
                logger.debug("Vendor %d: no definition, skipping", vendor_hash)
                continue
            vendor_name_str = (vendor_def.get("displayProperties") or {}).get("name", "")
            vendor_icon = (vendor_def.get("displayProperties") or {}).get("icon", "")
            next_refresh = vendor_data.get("nextRefreshDate", "")

            vendor_failure_strings = _vendor_failure_strings(
                response_failure_strings,
                vendor_def,
            )

            # Parse sale items
            sale_items: list[VendorSaleItem] = []
            vendor_sales = sales_data.get(vendor_hash_str, {})
            categories = vendor_sales.get("saleItems", {})
            vendor_item_components: object = {}
            if isinstance(item_components, dict):
                vendor_item_components = (
                    item_components.get(vendor_hash_str)
                    or item_components.get(vendor_hash)
                    or {}
                )

            for item_index_str, sale_item in categories.items():
                vendor_item_index = sale_item.get("vendorItemIndex")
                if not isinstance(vendor_item_index, int):
                    vendor_item_index = int(item_index_str)
                item_hash = sale_item.get("itemHash", 0)
                item_info = self._manifest.get_item_info(item_hash)
                item_name = self._manifest.get_item_name(item_hash)

                # Costs
                costs: list[VendorCost] = []
                for cost in sale_item.get("costs", []):
                    cost_hash = cost.get("itemHash", 0)
                    cost_name = CURRENCY_NAMES.get(
                        cost_hash,
                        self._manifest.get_item_name(cost_hash),
                    )
                    costs.append(VendorCost(
                        item_hash=cost_hash,
                        item_name=cost_name,
                        quantity=cost.get("quantity", 0),
                    ))

                # Failure reasons (failureIndexes point to response failureStrings)
                failure_idxs = sale_item.get("failureIndexes", [])
                failures: list[str] = []
                for idx in failure_idxs:
                    if idx < len(vendor_failure_strings):
                        failures.append(vendor_failure_strings[idx])

                # Owned state from augments
                augments = sale_item.get("augments", 0)
                owned = bool(augments & AUGMENTS_OWNED)

                # Only live vendor socket data describes the actual sale roll.
                perks: list[PerkInfo] | None = None
                if item_info and item_info.get("itemType") == 3:  # Weapon
                    perks = self._extract_perks_from_vendor_components(
                        item_hash,
                        vendor_item_index,
                        vendor_item_components,
                    )
                    if perks is None:
                        warning = (
                            f"商品“{item_name}”未返回可核对的实际 socket，已省略 Perk。"
                        )
                        if warning not in warnings:
                            warnings.append(warning)

                tier_num = (item_info or {}).get("tier", 0)
                tier = {5: "传说", 6: "异域"}.get(tier_num, "")

                # manifest already stores icon as full Bungie CDN URL
                icon_url = (item_info or {}).get("icon", "")

                sale_items.append(VendorSaleItem(
                    vendor_item_index=vendor_item_index,
                    item_hash=item_hash,
                    name=item_name,
                    item_type=(item_info or {}).get("itemTypeNameDisplay", "") or (item_info or {}).get("itemTypeName", ""),
                    tier=tier,
                    icon=icon_url,
                    icon_url=icon_url,
                    costs=costs,
                    owned=owned,
                    can_be_sold=sale_item.get("apiPurchasable", True),
                    failure_reasons=failures,
                    perks=perks,
                ))

            if sale_items:
                # Use Chinese display name if available, otherwise API name
                display_name = (
                    VENDOR_DISPLAY_NAMES.get(vendor_hash)
                    or SUB_VENDOR_DISPLAY_NAMES.get(vendor_hash)
                    or vendor_name_str
                )
                results.append(VendorInfo(
                    vendor_hash=vendor_hash,
                    name=display_name,
                    icon=vendor_icon,
                    next_refresh=next_refresh,
                    sale_items=sale_items,
                ))

        logger.info("get_vendor_inventory: %d vendors with items", len(results))
        response = VendorInventoryResponse(
            player_name=player_name,
            character=class_type_name(
                resolve_character_name(character)
            ),
            vendors=results,
            warnings=warnings,
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
        """Compress response for LLM consumption — strip large/unnecessary fields."""
        for vendor in response.vendors:
            vendor.icon = ""
            vendor.next_refresh = ""
            for item in vendor.sale_items:
                item.icon = ""
                item.vendor_item_index = 0
                item.item_hash = 0
                item.can_be_sold = True
                # Compress costs to single line
                if item.costs:
                    parts = [f"{c.item_name}x{c.quantity}" for c in item.costs]
                    item.costs = [VendorCost(item_hash=0, item_name=", ".join(parts), quantity=0)]
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
