"""Item parsing utilities — pure functions for extracting items from Bungie API responses.

This module contains no service logic, no API calls, no state. It transforms
raw Bungie API response dicts into typed InventoryItem models.
"""

from __future__ import annotations

from ..build.constants import STAT_HASH_TO_NAME as _STAT_HASHES
from ..manifest import ManifestManager, class_type_name
from ..models import ArmorStats, InventoryItem

# Armor bucket hashes (unsigned)
_ARMOR_BUCKETS = {3448274439, 3551918588, 14239492, 20886954, 1585787867}
_VAULT_BUCKET_HASH = 138197802


def _parse_armor_stats(stats_data: dict, inst_id: str) -> ArmorStats | None:
    """Extract armor stats from component 304 data.

    Returns ArmorStats if the item has stat data, None otherwise.
    """
    item_stats = stats_data.get(inst_id, {}).get("stats", {})
    if not item_stats:
        return None

    kwargs = {}
    for stat_hash, field_name in _STAT_HASHES.items():
        entry = item_stats.get(str(stat_hash), {}) or item_stats.get(stat_hash, {})
        kwargs[field_name] = entry.get("value", 0) if isinstance(entry, dict) else 0

    return ArmorStats(**kwargs)


def parse_items_from_profile(
    profile: dict,
    manifest: ManifestManager,
    target_class_type: int | None = None,
    target_location: str | None = None,
) -> list[InventoryItem]:
    """Parse items from a GetProfile response into InventoryItem list.

    Args:
        profile: Raw GetProfile response (already unwrapped).
        manifest: ManifestManager for name lookups.
        target_class_type: Filter by classType (None = all characters).
        target_location: Filter by location name. If given, ignores
            target_class_type and only returns items from that location.
    """
    items: list[InventoryItem] = []
    chars_data = profile.get("characters", {}).get("data", {})

    instances_data = (
        profile.get("itemComponents", {})
        .get("instances", {})
        .get("data", {})
    )
    stats_data = (
        profile.get("itemComponents", {})
        .get("stats", {})
        .get("data", {})
    )

    def _build_item(
        raw: dict, location: str, character_id: str = ""
    ) -> InventoryItem:
        h = raw["itemHash"]
        inst_id = str(raw.get("itemInstanceId", "0"))
        instance = instances_data.get(inst_id, {})
        info = manifest.get_item_info(h)

        # Parse armor stats from component 304
        bucket_hash = raw.get("bucketHash", 0)
        armor_stats = None
        if bucket_hash in _ARMOR_BUCKETS:
            armor_stats = _parse_armor_stats(stats_data, inst_id)

        # manifest already stores icon as full Bungie CDN URL
        icon_url = info.get("icon", "") if info else ""

        return InventoryItem(
            item_instance_id=inst_id,
            item_hash=h,
            name=manifest.get_item_name(h),
            item_type=manifest.item_type_name(
                info.get("itemType", 0) if info else 0
            ),
            item_type_display=info.get("itemTypeNameDisplay", "") if info else "",
            power=(
                instance.get("primaryStat", {}).get("value")
                if instance
                else None
            ),
            bucket_type=manifest.bucket_name(bucket_hash),
            is_equipped=(
                raw.get("isEquipped", False)
                or instance.get("isEquipped", False)
            ),
            quantity=raw.get("quantity", 1),
            location=location,
            character_id=character_id,
            stats=armor_stats,
            icon_url=icon_url,
        )

    # Vault items — filter by classType when targeting a specific class
    vault_items = (
        profile.get("profileInventory", {})
        .get("data", {})
        .get("items", [])
    )
    for raw in vault_items:
        is_vault_item = raw.get("bucketHash") == _VAULT_BUCKET_HASH
        if target_location == "vault" and not is_vault_item:
            continue
        if target_class_type is not None:
            if not is_vault_item:
                continue
            info = manifest.get_item_info(raw.get("itemHash", 0))
            item_class = info.get("classType", -1) if info else -1
            # classType -1 = any class (weapons), 0/1/2 = specific class (armor)
            if item_class >= 0 and item_class != target_class_type:
                continue
        items.append(_build_item(raw, location="vault" if is_vault_item else "profile"))

    # Character inventories + equipment
    inv_data = profile.get("characterInventories", {}).get("data", {})
    equip_data = profile.get("characterEquipment", {}).get("data", {})

    for char_id, char_info in chars_data.items():
        ct = char_info.get("classType", -1)
        loc_name = class_type_name(ct).lower()

        if target_class_type is not None and ct != target_class_type:
            continue

        char_items = list(inv_data.get(char_id, {}).get("items", []))
        char_items.extend(
            equip_data.get(char_id, {}).get("items", [])
        )

        for raw in char_items:
            items.append(
                _build_item(raw, location=loc_name, character_id=char_id)
            )

    # Filter by location if specified (vault / hunter / warlock / titan)
    if target_location is not None:
        items = [it for it in items if it.location == target_location.lower()]

    return items
