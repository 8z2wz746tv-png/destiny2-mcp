"""Inventory service — item parsing, lookup, and search.

Extracted from server.py per Rule 1: tools should not contain business logic.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import AuthenticationError, ConfigError, ItemNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..build.models import InventorySnapshot
from ..models import InventoryItem, InventoryResponse, SearchItemsResponse
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from ..utils.item_parser import parse_items_from_profile

logger = get_logger(__name__)

_INVENTORY_PROFILE_COMPONENTS = [102, 200, 201, 205, 300, 304]
_ARMOR_SNAPSHOT_COMPONENTS = [102, 200, 201, 205, 300, 304, 305]
MISSING_INVENTORY_SCOPE_MESSAGE = (
    "当前 Bungie OAuth token 缺少库存/仓库权限 ReadDestinyInventoryAndVault，"
    "请在 Bungie Developer Portal 给应用开启该 scope 后重新完成 Bungie 授权。"
    "当前只读到了已装备装备，不能判断仓库或角色背包是否为空。"
)

_INVENTORY_ITEM_TYPE_ALIASES = {
    "": None,
    "all": "all",
    "全部": "all",
    "weapon": "weapon",
    "weapons": "weapon",
    "武器": "weapon",
    "armor": "armor",
    "armors": "armor",
    "护甲": "armor",
}


def _normalize_inventory_item_type(value: str | None) -> str | None:
    key = str(value or "").strip().lower()
    if key in _INVENTORY_ITEM_TYPE_ALIASES:
        return _INVENTORY_ITEM_TYPE_ALIASES[key]
    raise ConfigError(
        f"item_type={value!r} 不受支持。get 仅支持 weapon/armor/all；"
        f"查询具体武器类型请使用 inventory_assistant(intent=\"type\", type_name={value!r})。"
    )


class InventoryService:
    """Operations for reading and searching player/vault inventories."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    # ── Inventory ────────────────────────────────────────────────────

    async def _resolve_and_fetch(
        self, player_name: str, components: list[int]
    ) -> tuple[dict, dict]:
        """Resolve player name and fetch profile. Returns (player_dict, profile)."""
        p = await self._resolver.resolve_player(player_name)
        profile = await self._resolver.get_profile(
            p["membership_id"], p["membership_type"], components
        )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        return p, profile

    async def get_inventory(
        self,
        player_name: str,
        location: str,
        item_type: str | None = None,
        armor_slot: str | None = None,
        rarity: str | None = None,
    ) -> InventoryResponse:
        """Get inventory for a specific character or the vault.

        Args:
            player_name: Bungie name.
            location: 'hunter', 'warlock', 'titan', 'vault' (or Chinese).
            item_type: Filter by type — 'weapon'/'armor'/'all'. None = all.
            armor_slot: Filter by armor slot — 'helmet'/'gauntlets'/'chest'/'legs'/'class_item'/'all'. None = all.
            rarity: Filter by rarity — 'exotic'/'legendary'/'rare'/'all'. None = all.

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
            ConfigError: If item_type is not weapon/armor/all.
        """
        item_type = _normalize_inventory_item_type(item_type)
        logger.info(
            "Fetching inventory for %s, location=%s, type=%s, slot=%s, rarity=%s",
            player_name, location, item_type, armor_slot, rarity,
        )
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        normalized_location = location.strip().lower()

        if normalized_location in ("", "all", "全部", "账号", "account"):
            items = parse_items_from_profile(profile, self._manifest)
            loc_name = "all"
        elif normalized_location in ("vault", "仓库"):
            items = parse_items_from_profile(
                profile, self._manifest, target_location="vault"
            )
            loc_name = "vault"
        else:
            class_type = resolve_character_name(location)
            items = parse_items_from_profile(
                profile, self._manifest, target_class_type=class_type
            )
            loc_name = class_type_name(class_type).lower()

        # Apply filters
        items = self._filter_items(items, item_type, armor_slot, rarity)

        return InventoryResponse(location=loc_name, items=items)

    def _filter_items(
        self,
        items: list[InventoryItem],
        item_type: str | None,
        armor_slot: str | None,
        rarity: str | None,
    ) -> list[InventoryItem]:
        """Filter items by type, armor slot, and rarity."""
        result = items

        # Filter by item_type (weapon/armor)
        if item_type and item_type.lower() not in ("all", ""):
            t = item_type.lower()
            if t == "weapon":
                weapon_buckets = {
                    "Kinetic Weapons", "Energy Weapons", "Power Weapons",
                }
                result = [
                    i for i in result
                    if i.item_type.lower() == "weapon" or i.bucket_type in weapon_buckets
                ]
            elif t == "armor":
                armor_buckets = {
                    "Helmet", "Gauntlets", "Chest Armor", "Leg Armor", "Class Armor",
                }
                result = [
                    i for i in result
                    if i.item_type.lower() == "armor" or i.bucket_type in armor_buckets
                ]

        # Filter by armor_slot
        if armor_slot and armor_slot.lower() not in ("all", ""):
            slot_map = {
                "helmet": "Helmet",
                "gauntlets": "Gauntlets",
                "chest": "Chest Armor",
                "legs": "Leg Armor",
                "class_item": "Class Armor",
            }
            target_bucket = slot_map.get(armor_slot.lower())
            if target_bucket:
                result = [i for i in result if i.bucket_type == target_bucket]

        # Filter by rarity (tier)
        if rarity and rarity.lower() not in ("all", ""):
            tier_map = {
                "exotic": 6,
                "legendary": 5,
                "rare": 4,
            }
            target_tier = tier_map.get(rarity.lower())
            if target_tier is not None:
                # Need to look up tier from manifest
                result = [
                    i for i in result
                    if self._get_item_tier(i.item_hash) == target_tier
                ]

        return result

    def _get_item_tier(self, item_hash: int) -> int:
        """Get item tier from manifest. Returns 0 if not found."""
        info = self._manifest.get_item_info(item_hash)
        if info:
            return info.get("tier", 0)
        return 0

    async def search_items(
        self, player_name: str, item_name: str
    ) -> SearchItemsResponse:
        """Search for an item by name across all characters and vault.

        Args:
            player_name: Bungie name.
            item_name: Partial item name (Chinese or English).

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info("Searching items: player=%s, item=%s", player_name, item_name)
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        manifest_results = self._manifest.search(item_name, limit=50)
        if not manifest_results:
            return SearchItemsResponse(query=item_name, items=[])

        # Manifest hashes are signed, API returns unsigned — store both
        match_hashes: set[int] = set()
        target_name = ""
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))
            if not target_name and r.get("itemType") in (2, 3):
                target_name = r["name"]

        all_items = parse_items_from_profile(profile, self._manifest)
        matched = [it for it in all_items if it.item_hash in match_hashes]

        # Name-based fallback: find items whose hashes weren't in manifest results
        # (deprecated/reissued items with hashes removed from current manifest)
        if target_name:
            matched_hashes = {it.item_hash for it in matched}
            for it in all_items:
                if it.item_hash not in matched_hashes and it.item_hash not in match_hashes:
                    if it.name == target_name:
                        matched.append(it)
                        matched_hashes.add(it.item_hash)

        logger.info(
            "Item search '%s': %d manifest hit(s), %d instance(s) found",
            item_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=item_name, items=matched)

    async def search_items_by_type(
        self, player_name: str, type_name: str
    ) -> SearchItemsResponse:
        """Search for items by weapon/armor type across all characters and vault.

        Args:
            player_name: Bungie name.
            type_name: Type display name (e.g. '微型冲锋枪', '手炮', '自动步枪').

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info(
            "Searching items by type: player=%s, type=%s", player_name, type_name
        )
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        manifest_results = self._manifest.search_by_type_name(type_name)
        if not manifest_results:
            return SearchItemsResponse(query=type_name, items=[])

        # Build match set with both signed and unsigned hashes
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items = parse_items_from_profile(profile, self._manifest)
        matched = [it for it in all_items if it.item_hash in match_hashes]

        logger.info(
            "Type search '%s': %d manifest hit(s), %d instance(s) found",
            type_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=type_name, items=matched)

    async def find_item(
        self,
        player_name: str,
        item_instance_id: str,
    ) -> InventoryItem:
        """Find a specific item instance across the account.

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
            ItemNotFoundError: If the item instance is not found.
        """
        logger.info("Finding item instance: %s", item_instance_id)
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )
        all_items = parse_items_from_profile(profile, self._manifest)
        found = next(
            (it for it in all_items if it.item_instance_id == item_instance_id),
            None,
        )
        if not found:
            raise ItemNotFoundError(
                item_instance_id,
                "It may have been moved or dismantled.",
            )
        return found

    # ── Armor Snapshot (Build Engine) ─────────────────────────────────

    async def get_armor_snapshot(self, player_name: str, character_class: str = "") -> InventorySnapshot:
        """Fetch all armor pieces with stats, grouped by slot.

        This is the data input for Build Engine. Fetches components
        102+200+201+205+300+304+305 (includes ItemStats for the 6 armor
        stats and ItemSockets for artifice detection).

        Must be called after the manifest is loaded.

        Args:
            player_name: Bungie name.
            character_class: Target class filter — "hunter"/"warlock"/"titan" (or Chinese).
                           If empty, includes all classes.

        Returns:
            InventorySnapshot with armor grouped by slot.

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info("Building armor snapshot for %s (class=%s)", player_name, character_class or "all")
        _, profile = await self._resolve_and_fetch(
            player_name, _ARMOR_SNAPSHOT_COMPONENTS
        )
        snapshot = InventorySnapshot.from_profile(profile, self._manifest, character_class)
        logger.info(
            "Armor snapshot ready: %d pieces across 5 slots",
            snapshot.total_pieces,
        )
        return snapshot


def looks_like_missing_inventory_scope(profile: dict) -> bool:
    """Detect OAuth tokens that only return public equipped items.

    When ReadDestinyInventoryAndVault is missing, Bungie can still return
    characterEquipment while profileInventory and characterInventories are
    empty. Treating that as an empty Vault would be misleading.
    """
    profile_items = (
        profile.get("profileInventory", {})
        .get("data", {})
        .get("items", [])
    )
    character_inventory = profile.get("characterInventories", {}).get("data", {})
    character_equipment = profile.get("characterEquipment", {}).get("data", {})

    inventory_count = len(profile_items) + sum(
        len(payload.get("items", []))
        for payload in character_inventory.values()
        if isinstance(payload, dict)
    )
    equipment_count = sum(
        len(payload.get("items", []))
        for payload in character_equipment.values()
        if isinstance(payload, dict)
    )

    return inventory_count == 0 and equipment_count > 0
