"""DIM-style inventory overview payloads for WebUI clients."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..utils.item_parser import parse_items_from_profile


PROFILE_COMPONENTS = [102, 200, 201, 205, 300, 304]

_BUNGIE_BASE_URL = "https://www.bungie.net"

_BUCKETS = [
    {"id": "kinetic", "label": "动能", "category": "weapons", "equippable": True, "capacity": 10, "vaultBucketId": "kinetic"},
    {"id": "energy", "label": "能量", "category": "weapons", "equippable": True, "capacity": 10, "vaultBucketId": "energy"},
    {"id": "power", "label": "威能", "category": "weapons", "equippable": True, "capacity": 10, "vaultBucketId": "power"},
    {"id": "helmet", "label": "头盔", "category": "armor", "equippable": True, "capacity": 10, "vaultBucketId": "helmet"},
    {"id": "arms", "label": "臂铠", "category": "armor", "equippable": True, "capacity": 10, "vaultBucketId": "arms"},
    {"id": "chest", "label": "胸甲", "category": "armor", "equippable": True, "capacity": 10, "vaultBucketId": "chest"},
    {"id": "legs", "label": "腿甲", "category": "armor", "equippable": True, "capacity": 10, "vaultBucketId": "legs"},
    {"id": "class-item", "label": "职业物品", "category": "armor", "equippable": True, "capacity": 10, "vaultBucketId": "class-item"},
    {"id": "general", "label": "通用", "category": "general", "equippable": False, "capacity": 50},
    {"id": "postmaster", "label": "邮政长", "category": "postmaster", "equippable": False, "capacity": 21},
]

_BUCKET_ID_BY_NAME = {
    "Kinetic Weapons": "kinetic",
    "Energy Weapons": "energy",
    "Power Weapons": "power",
    "Helmet": "helmet",
    "Gauntlets": "arms",
    "Chest Armor": "chest",
    "Leg Armor": "legs",
    "Class Armor": "class-item",
    "Lost Items": "postmaster",
}

_CLASS_BY_TYPE = {
    0: "titan",
    1: "hunter",
    2: "warlock",
}

_CLASS_LABELS = {
    "hunter": "猎人",
    "warlock": "术士",
    "titan": "泰坦",
    "unknown": "未知角色",
}

_CLASS_ORDER = {
    "hunter": 0,
    "warlock": 1,
    "titan": 2,
    "unknown": 3,
}

_TIER_BY_VALUE = {
    6: "exotic",
    5: "legendary",
    4: "rare",
    3: "common",
    2: "common",
}

_DAMAGE_BY_VALUE = {
    1: "kinetic",
    2: "solar",
    3: "arc",
    4: "void",
    5: "stasis",
    6: "strand",
}


async def build_inventory_overview_payload(
    *,
    resolver: Any,
    manifest: Any,
    user_id: str,
    membership_id: str,
    membership_type: int,
    display_name: str,
    profile_name: str,
) -> dict[str, Any]:
    """Fetch and format the current user's inventory overview."""

    if display_name:
        player = await resolver.resolve_player(display_name)
        membership_id = str(player.get("membership_id") or membership_id)
        membership_type = int(player.get("membership_type") or membership_type)

    profile = await resolver.get_profile(membership_id, membership_type, PROFILE_COMPONENTS)
    return format_inventory_overview_payload(
        profile=profile,
        manifest=manifest,
        user_id=user_id,
        display_name=display_name,
        profile_name=profile_name,
    )


def format_inventory_overview_payload(
    *,
    profile: dict[str, Any],
    manifest: Any,
    user_id: str,
    display_name: str,
    profile_name: str,
) -> dict[str, Any]:
    """Convert a Bungie GetProfile response into the WebUI inventory DTO."""

    parsed_items = parse_items_from_profile(profile, manifest)
    stores = _format_character_stores(profile, parsed_items, manifest)
    stores.append(_format_vault_store(parsed_items, manifest))

    return {
        "ok": True,
        "profile": {
            "userId": str(user_id),
            "displayName": str(display_name or ""),
            "profileName": str(profile_name or ""),
            "refreshedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "buckets": list(_BUCKETS),
        "stores": stores,
    }


def _format_character_stores(profile: dict[str, Any], parsed_items: list[Any], manifest: Any) -> list[dict[str, Any]]:
    chars = profile.get("characters", {}).get("data", {}) or {}
    latest_played = max((str(c.get("dateLastPlayed") or "") for c in chars.values()), default="")
    stores: list[dict[str, Any]] = []

    for character_id, char in chars.items():
        class_type = _class_from_type(char.get("classType"))
        items = [
            _format_item(item, manifest, owner_id=str(character_id), fallback_class=class_type, index=index)
            for index, item in enumerate(parsed_items)
            if str(getattr(item, "character_id", "") or "") == str(character_id)
        ]
        stores.append(
            {
                "id": str(character_id),
                "kind": "character",
                "label": _CLASS_LABELS.get(class_type, _CLASS_LABELS["unknown"]),
                "classType": class_type,
                "emblemUrl": _cdn_url(char.get("emblemPath", "")),
                "backgroundUrl": _cdn_url(char.get("emblemBackgroundPath", "")),
                "power": char.get("light"),
                "current": bool(str(char.get("dateLastPlayed") or "") == latest_played and latest_played),
                "items": items,
            }
        )

    stores.sort(key=lambda store: (_CLASS_ORDER.get(store["classType"], 99), store["label"]))
    return stores


def _format_vault_store(parsed_items: list[Any], manifest: Any) -> dict[str, Any]:
    vault_items = [
        _format_item(item, manifest, owner_id="vault", fallback_class="unknown", index=index)
        for index, item in enumerate(parsed_items)
        if getattr(item, "location", "") == "vault"
    ]
    return {
        "id": "vault",
        "kind": "vault",
        "label": "仓库",
        "classType": "unknown",
        "current": False,
        "items": vault_items,
    }


def _format_item(item: Any, manifest: Any, *, owner_id: str, fallback_class: str, index: int) -> dict[str, Any]:
    item_hash = int(getattr(item, "item_hash", 0) or 0)
    info = manifest.get_item_info(item_hash) or {}
    instance_id = str(getattr(item, "item_instance_id", "") or "")
    item_id = instance_id if instance_id and instance_id != "0" else f"{owner_id}-{item_hash}-{index}"
    stats = _armor_stats_payload(getattr(item, "stats", None))

    payload: dict[str, Any] = {
        "id": item_id,
        "hash": item_hash,
        "name": str(getattr(item, "name", "") or manifest.get_item_name(item_hash)),
        "typeName": str(getattr(item, "item_type_display", "") or getattr(item, "item_type", "") or info.get("itemTypeNameDisplay") or info.get("itemTypeName") or "Unknown"),
        "bucketId": _bucket_id(getattr(item, "bucket_type", "")),
        "ownerId": owner_id,
        "locationId": str(getattr(item, "character_id", "") or owner_id),
        "iconUrl": _cdn_url(getattr(item, "icon_url", "") or info.get("icon", "")),
        "power": getattr(item, "power", None),
        "quantity": int(getattr(item, "quantity", 1) or 1),
        "equipped": bool(getattr(item, "is_equipped", False)),
        "locked": False,
        "transferable": True,
        "tier": _tier(info.get("tier")),
        "classType": _item_class(info.get("classType"), fallback_class),
        "damageType": _damage(info.get("damageType")),
    }
    if stats:
        payload["stats"] = stats
    return payload


def _bucket_id(bucket_name: str) -> str:
    return _BUCKET_ID_BY_NAME.get(str(bucket_name or ""), "general")


def _class_from_type(class_type: Any) -> str:
    try:
        return _CLASS_BY_TYPE.get(int(class_type), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _item_class(class_type: Any, fallback: str) -> str:
    resolved = _class_from_type(class_type)
    return fallback if resolved == "unknown" else resolved


def _tier(tier_value: Any) -> str:
    try:
        return _TIER_BY_VALUE.get(int(tier_value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _damage(damage_value: Any) -> str:
    try:
        return _DAMAGE_BY_VALUE.get(int(damage_value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _armor_stats_payload(stats: Any) -> dict[str, int] | None:
    if not stats:
        return None
    return {
        "mobility": int(getattr(stats, "mobility", 0) or 0),
        "resilience": int(getattr(stats, "resilience", 0) or 0),
        "recovery": int(getattr(stats, "recovery", 0) or 0),
        "discipline": int(getattr(stats, "discipline", 0) or 0),
        "intellect": int(getattr(stats, "intellect", 0) or 0),
        "strength": int(getattr(stats, "strength", 0) or 0),
    }


def _cdn_url(path_or_url: Any) -> str:
    value = str(path_or_url or "")
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("/"):
        return _BUNGIE_BASE_URL + value
    return value
