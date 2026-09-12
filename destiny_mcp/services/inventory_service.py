"""Inventory service — item parsing, lookup, and search.

Extracted from server.py per Rule 1: tools should not contain business logic.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import AuthenticationError, ConfigError, ItemNotFoundError
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..build.models import InventorySnapshot
from ..build.constants import ARMOR_SLOT_MAP, STAT_HASH_TO_NAME
from ..models import InventoryItem, InventoryResponse, SearchItemsResponse
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from ..utils.item_parser import parse_items_from_profile

logger = get_logger(__name__)

_INVENTORY_PROFILE_COMPONENTS = profile_components.INVENTORY
_ARMOR_SNAPSHOT_COMPONENTS = profile_components.ARMOR_SNAPSHOT
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
        require_complete_inventory_components(profile)
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

        items, loc_name = self._items_at_location(profile, location)

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
        self, player_name: str, item_name: str, location: str = ""
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

        item_query = item_name.strip()
        if not item_query:
            raise ConfigError("请提供 item_name。")
        manifest_results = self._manifest.search(item_query, limit=0)

        # Manifest hashes are signed, API returns unsigned — store both
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items, _ = self._items_at_location(profile, location)
        query_key = item_query.casefold()
        matched = [
            item
            for item in all_items
            if item.item_hash in match_hashes
            or query_key in item.name.casefold()
            or query_key in item.name_en.casefold()
        ]

        logger.info(
            "Item search '%s': %d manifest hit(s), %d instance(s) found",
            item_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=item_name, items=matched)

    async def search_items_by_type(
        self, player_name: str, type_name: str, location: str = ""
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

        type_query = type_name.strip()
        if not type_query:
            raise ConfigError("请提供 type_name。")
        manifest_results = self._manifest.search_by_type_name(type_query, limit=0)

        # Build match set with both signed and unsigned hashes
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items, _ = self._items_at_location(profile, location)
        matched = [it for it in all_items if it.item_hash in match_hashes]

        logger.info(
            "Type search '%s': %d manifest hit(s), %d instance(s) found",
            type_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=type_name, items=matched)

    def _items_at_location(
        self,
        profile: dict,
        location: str,
    ) -> tuple[list[InventoryItem], str]:
        normalized = location.strip().lower()
        if normalized in ("", "all", "全部", "账号", "account"):
            return parse_items_from_profile(profile, self._manifest), "all"
        if normalized in ("vault", "仓库"):
            return (
                parse_items_from_profile(
                    profile,
                    self._manifest,
                    target_location="vault",
                ),
                "vault",
            )
        class_type = resolve_character_name(location)
        canonical_location = class_type_name(class_type).lower()
        items = parse_items_from_profile(profile, self._manifest)
        return (
            [item for item in items if item.location == canonical_location],
            canonical_location,
        )

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
        self._validate_armor_snapshot_components(profile, character_class)
        snapshot = InventorySnapshot.from_profile(profile, self._manifest, character_class)
        logger.info(
            "Armor snapshot ready: %d pieces across 5 slots",
            snapshot.total_pieces,
        )
        return snapshot

    def _validate_armor_snapshot_components(
        self,
        profile: dict,
        character_class: str,
    ) -> None:
        """Reject partial armor data before it reaches the optimizer."""
        target_class = (
            resolve_character_name(character_class) if character_class.strip() else -1
        )
        chars = profile["characters"]["data"]
        vault_items = profile["profileInventory"]["data"]["items"]
        inventories = profile["characterInventories"]["data"]
        equipment = profile["characterEquipment"]["data"]
        components = profile.get("itemComponents", {})
        instances = (components.get("instances") or {}).get("data")
        stats = (components.get("stats") or {}).get("data")
        sockets = (components.get("sockets") or {}).get("data")
        if not all(isinstance(value, dict) for value in (instances, stats, sockets)):
            raise ConfigError(
                "Bungie 未返回完整的护甲实例、属性或插槽组件，已停止配装计算。"
            )

        candidates: list[dict] = []
        for raw in vault_items:
            info = self._manifest.get_item_info(raw.get("itemHash", 0))
            if not isinstance(info, dict):
                if raw.get("itemInstanceId"):
                    raise ConfigError("仓库实例缺少 Manifest 定义，已停止配装计算。")
                continue
            bucket = raw.get("bucketHash")
            if bucket == 138197802:
                bucket = info.get("bucketTypeHash")
            if bucket not in ARMOR_SLOT_MAP:
                continue
            item_class = info.get("classType", -1)
            if target_class >= 0 and item_class in {0, 1, 2} and item_class != target_class:
                continue
            candidates.append(raw)

        for char_id, char_info in chars.items():
            if target_class >= 0 and char_info.get("classType") != target_class:
                continue
            candidates.extend(inventories[char_id]["items"])
            candidates.extend(equipment[char_id]["items"])

        incomplete = 0
        for raw in candidates:
            info = self._manifest.get_item_info(raw.get("itemHash", 0))
            bucket = raw.get("bucketHash")
            if bucket == 138197802 and isinstance(info, dict):
                bucket = info.get("bucketTypeHash")
            if bucket not in ARMOR_SLOT_MAP:
                continue
            instance_id = str(raw.get("itemInstanceId") or "")
            instance_data = instances.get(instance_id)
            stat_data = stats.get(instance_id)
            socket_data = sockets.get(instance_id)
            if (
                instance_id in {"", "0"}
                or not isinstance(info, dict)
                or not isinstance(instance_data, dict)
                or not instance_data
                or not isinstance(stat_data, dict)
                or not isinstance(stat_data.get("stats"), dict)
                or any(
                    not isinstance(
                        (stat_data["stats"].get(str(stat_hash)) or stat_data["stats"].get(stat_hash) or {}).get("value"),
                        int,
                    )
                    for stat_hash in STAT_HASH_TO_NAME
                )
                or not isinstance(socket_data, dict)
                or not isinstance(socket_data.get("sockets"), list)
                or not socket_data["sockets"]
            ):
                incomplete += 1
                continue
            for socket in socket_data["sockets"]:
                plug_hash = socket.get("plugHash", 0)
                if plug_hash and not isinstance(self._manifest.get_item_definition(plug_hash), dict):
                    incomplete += 1
                    break

        if incomplete:
            raise ConfigError(
                f"有 {incomplete} 件候选护甲缺少实例、属性或插槽数据，"
                "已停止配装计算，避免把缺失值当成 0。"
            )


def require_complete_inventory_components(profile: dict) -> None:
    """Require the inventory containers needed for an account-wide conclusion."""
    chars = profile.get("characters", {}).get("data")
    vault_items = profile.get("profileInventory", {}).get("data", {}).get("items")
    inventories = profile.get("characterInventories", {}).get("data")
    equipment = profile.get("characterEquipment", {}).get("data")
    if (
        not isinstance(chars, dict)
        or not chars
        or not isinstance(vault_items, list)
        or not isinstance(inventories, dict)
        or not isinstance(equipment, dict)
        or any(
            not isinstance(component.get(char_id, {}).get("items"), list)
            for char_id in chars
            for component in (inventories, equipment)
        )
    ):
        raise ConfigError(
            "Bungie 库存组件不完整，无法可靠判断账号库存；请稍后重试。"
        )


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

    if not isinstance(profile_items, list):
        profile_items = []
    if not isinstance(character_inventory, dict):
        character_inventory = {}
    if not isinstance(character_equipment, dict):
        character_equipment = {}

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
