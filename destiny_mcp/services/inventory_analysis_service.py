"""Inventory analysis service — compact account inventory summaries."""

from __future__ import annotations

from typing import Any

from ..exceptions import AuthenticationError, ConfigError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import InventoryItem
from ..player_resolver import PlayerResolver
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
)
from ..utils.item_parser import parse_items_from_profile

logger = get_logger(__name__)

PROFILE_COMPONENTS = [102, 200, 201, 205, 300, 304]

_WEAPON_BUCKETS = {"Kinetic Weapons", "Energy Weapons", "Power Weapons"}
_ARMOR_BUCKETS = {"Helmet", "Gauntlets", "Chest Armor", "Leg Armor", "Class Armor"}
_LOCATION_ALIASES = {
    "": "",
    "all": "",
    "全部": "",
    "vault": "vault",
    "仓库": "vault",
    "hunter": "hunter",
    "猎人": "hunter",
    "warlock": "warlock",
    "术士": "warlock",
    "titan": "titan",
    "泰坦": "titan",
}
_ITEM_TYPE_ALIASES = {
    "": "",
    "all": "",
    "全部": "",
    "weapon": "weapon",
    "weapons": "weapon",
    "武器": "weapon",
    "armor": "armor",
    "armors": "armor",
    "护甲": "armor",
}
_LOCATION_LABELS = {
    "vault": "仓库",
    "hunter": "猎人",
    "warlock": "术士",
    "titan": "泰坦",
}


class InventoryAnalysisService:
    """Build compact structured summaries for inventory overview questions."""

    def __init__(self, manifest: ManifestManager, resolver: PlayerResolver) -> None:
        self._manifest = manifest
        self._resolver = resolver

    async def summarize_inventory(
        self,
        player_name: str,
        *,
        location: str = "",
        item_type: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Summarize account inventory without dumping every item."""
        player_query = player_name.strip()
        if not player_query:
            raise ConfigError("请先登录 Bungie，或提供 player_name。")

        location_key = _normalize_location(location)
        item_type_key = _normalize_item_type(item_type)
        item_limit = max(1, min(int(limit or 10), 25))

        logger.info(
            "Summarizing inventory for %s (location=%s, item_type=%s, limit=%s)",
            player_query,
            location_key or "all",
            item_type_key or "all",
            item_limit,
        )

        player = await self._resolver.resolve_player(player_query)
        profile = await self._resolver.get_profile(
            player["membership_id"],
            player["membership_type"],
            PROFILE_COMPONENTS,
        )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        items = parse_items_from_profile(profile, self._manifest)
        filtered = _filter_items(items, location_key, item_type_key)

        highest_power = sorted(
            (item for item in filtered if item.power is not None),
            key=lambda item: int(item.power or 0),
            reverse=True,
        )[:item_limit]
        equipped = [item for item in filtered if item.is_equipped][:item_limit]
        sample = filtered[:item_limit]

        warnings: list[str] = []
        if len(filtered) > item_limit:
            warnings.append(f"仅返回前 {item_limit} 件示例/最高光物品，完整列表请用 get_inventory。")

        query_label = _query_label(location_key, item_type_key)
        summary = (
            f"{query_label}共 {len(filtered)} 件物品，"
            f"武器 {sum(1 for item in filtered if _is_weapon(item))} 件，"
            f"护甲 {sum(1 for item in filtered if _is_armor(item))} 件。"
        )

        return {
            "summary": summary,
            "inventory": {
                "query": {
                    "player_name": player_query,
                    "location": location_key or "all",
                    "item_type": item_type_key or "all",
                    "limit": item_limit,
                },
                "total": len(filtered),
                "counts": {
                    "by_location": _count_by(filtered, lambda item: _LOCATION_LABELS.get(item.location, item.location)),
                    "by_bucket": _count_by(filtered, lambda item: item.bucket_type or "未知栏位"),
                    "by_item_type": _count_by(filtered, lambda item: item.item_type_display or item.item_type or "未知类型"),
                },
                "equipped": [_compact_item(item) for item in equipped],
                "highest_power": [_compact_item(item) for item in highest_power],
                "sample": [_compact_item(item) for item in sample],
            },
            "warnings": warnings,
            "next_actions": [
                {
                    "label": "查看某个角色或仓库的完整列表",
                    "tool": "get_inventory",
                }
            ],
        }


def _normalize_location(location: str) -> str:
    key = str(location or "").strip().lower()
    if key in _LOCATION_ALIASES:
        return _LOCATION_ALIASES[key]
    raise ConfigError("location 仅支持 hunter/warlock/titan/vault/all。")


def _normalize_item_type(item_type: str) -> str:
    key = str(item_type or "").strip().lower()
    if key in _ITEM_TYPE_ALIASES:
        return _ITEM_TYPE_ALIASES[key]
    raise ConfigError("item_type 仅支持 weapon/armor/all。")


def _filter_items(
    items: list[InventoryItem],
    location: str,
    item_type: str,
) -> list[InventoryItem]:
    filtered = items
    if location:
        filtered = [item for item in filtered if item.location == location]
    if item_type == "weapon":
        filtered = [item for item in filtered if _is_weapon(item)]
    elif item_type == "armor":
        filtered = [item for item in filtered if _is_armor(item)]
    return filtered


def _is_weapon(item: InventoryItem) -> bool:
    return item.bucket_type in _WEAPON_BUCKETS or item.item_type.lower() == "weapon"


def _is_armor(item: InventoryItem) -> bool:
    return item.bucket_type in _ARMOR_BUCKETS or item.item_type.lower() == "armor"


def _count_by(
    items: list[InventoryItem],
    key_func,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(key_func(item) or "未知")
        counts[key] = counts.get(key, 0) + 1
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _compact_item(item: InventoryItem) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": item.name,
        "instance_id": item.item_instance_id,
        "location": _LOCATION_LABELS.get(item.location, item.location),
        "bucket": item.bucket_type,
        "type": item.item_type_display or item.item_type,
        "power": item.power,
        "equipped": item.is_equipped,
        "icon_url": item.icon_url,
    }
    if item.stats:
        payload["stats"] = item.stats.model_dump(mode="json")
    return payload


def _query_label(location: str, item_type: str) -> str:
    parts = []
    if location:
        parts.append(_LOCATION_LABELS.get(location, location))
    if item_type == "weapon":
        parts.append("武器")
    elif item_type == "armor":
        parts.append("护甲")
    return " / ".join(parts) + "：" if parts else "账号背包："
