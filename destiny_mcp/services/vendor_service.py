"""Vendor service — vendor inventory queries.

References DIM's d2-vendors.ts and vendor-item.ts for implementation details.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..bungie_client import BungieClient
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

    async def get_vendor_inventory(
        self,
        player_name: str,
        character: str,
        vendor_name: str = "",
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
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)

        # Determine which vendors to fetch
        filter_hashes: list[int] | None = None
        if vendor_name:
            q = vendor_name.strip().lower()
            for key, h in VENDOR_HASHES.items():
                if q in key.lower():
                    filter_hashes = [h]
                    break

        # Fetch vendors from API
        raw = await self._bungie.fetch_vendors(mid, mtype, char_id)

        # Parse response
        vendors_data = raw.get("vendors", {}).get("data", {})
        sales_data = raw.get("sales", {}).get("data", {})
        response_failure_strings = raw.get("failureStrings", [])

        results: list[VendorInfo] = []

        # Client-side filter: only process requested vendor(s)
        allowed_hashes = set(filter_hashes) if filter_hashes else None

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

            for item_index_str, sale_item in categories.items():
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

                # Perks from manifest socketEntries (vendor items have no itemComponents)
                perks: list[PerkInfo] | None = None
                if item_info and item_info.get("itemType") == 3:  # Weapon
                    perks = self._extract_perks_from_manifest(item_hash)

                tier_num = (item_info or {}).get("tier", 0)
                tier = {5: "传说", 6: "异域"}.get(tier_num, "")

                # manifest already stores icon as full Bungie CDN URL
                icon_url = (item_info or {}).get("icon", "")

                sale_items.append(VendorSaleItem(
                    vendor_item_index=int(item_index_str),
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
        )
        return self._compact_response(response)

    def _extract_perks_from_manifest(self, item_hash: int) -> list[PerkInfo]:
        """Extract weapon perks from manifest socketEntries + plugSets.

        Vendor items don't have itemComponents (they're templates, not instances).
        We look up the item's socketEntries, then for each socket with a
        reusablePlugSetHash, we list the available perks from the plug set.
        """
        item_def = self._manifest.get_item_definition(item_hash)
        if not item_def:
            logger.debug("No item definition for hash %d, skipping perk extraction", item_hash)
            return []

        socket_entries = item_def.get("sockets", {}).get("socketEntries", [])
        perks: list[PerkInfo] = []

        for entry in socket_entries:
            # Try singleInitialPlugHash first (fixed perk)
            plug_hash = entry.get("singleInitialPlugHash", 0)

            # If no fixed perk, try reusablePlugSetHash (random roll pool)
            plug_set_hash = entry.get("reusablePlugSetHash", 0)
            if not plug_hash and plug_set_hash:
                # Get first perk from plug set as representative
                plug_set = self._manifest.get_definition("DestinyPlugSetDefinition", plug_set_hash)
                if plug_set:
                    plug_items = plug_set.get("reusablePlugItems", [])
                    if plug_items:
                        plug_hash = plug_items[0].get("plugItemHash", 0)

            if not plug_hash:
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

            name = info.get("name", f"#{plug_hash}")
            desc = ""
            sandbox = self._manifest.get_sandbox_perk_description(plug_hash)
            if sandbox:
                desc = sandbox.get("description", "")

            perk = PerkInfo(
                plug_hash=plug_hash,
                name=name,
                description=desc,
                plug_category=cat_id,
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
