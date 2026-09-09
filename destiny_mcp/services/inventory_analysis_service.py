"""Inventory analysis service — compact account inventory summaries."""

from __future__ import annotations

from typing import Any

from ..exceptions import AuthenticationError, ConfigError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import InventoryItem
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
    require_complete_inventory_components,
)
from ..utils.item_parser import parse_items_from_profile

logger = get_logger(__name__)

PROFILE_COMPONENTS = [102, 200, 201, 205, 300, 304]
DUPLICATE_WEAPON_PROFILE_COMPONENTS = [102, 200, 201, 205, 300, 305]

_WEAPON_BUCKETS = {"Kinetic Weapons", "Energy Weapons", "Power Weapons"}
_ARMOR_BUCKETS = {"Helmet", "Gauntlets", "Chest Armor", "Leg Armor", "Class Armor"}
_VAULT_BUCKET_HASH = 138197802
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
        require_complete_inventory_components(profile)
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
            warnings.append(
                f"仅返回前 {item_limit} 件示例/最高光物品，"
                "完整列表请用 inventory_assistant(intent=\"get\")。"
            )

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
                    "tool": "inventory_assistant",
                    "arguments": {"intent": "get"},
                }
            ],
        }

    async def find_duplicate_weapons(
        self,
        player_name: str,
        *,
        item_name: str = "",
        type_name: str = "",
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Group account weapons by exact hash and distinct instance ID."""
        player_query = str(player_name or "").strip()
        if not player_query:
            raise ConfigError("请先登录 Bungie，或提供 player_name。")

        page_limit = _duplicate_page_limit(limit)
        page_offset = _duplicate_page_offset(offset)
        item_name_filter = str(item_name or "").strip()
        type_name_filter = str(type_name or "").strip()

        logger.info(
            "Scanning duplicate weapons for %s "
            "(item_name=%s, type_name=%s, limit=%s, offset=%s)",
            player_query,
            item_name_filter or "all",
            type_name_filter or "all",
            page_limit,
            page_offset,
        )
        player = await self._resolver.resolve_player(player_query)
        profile = await self._resolver.get_profile(
            player["membership_id"],
            player["membership_type"],
            DUPLICATE_WEAPON_PROFILE_COMPONENTS,
        )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        require_complete_inventory_components(profile)

        items = parse_items_from_profile(profile, self._manifest)
        weapons = [item for item in items if _is_weapon(item)]
        profile_items = (
            profile.get("profileInventory", {})
            .get("data", {})
            .get("items", [])
        )
        unclassified_vault_items = 0
        if isinstance(profile_items, list):
            unclassified_vault_items = sum(
                1
                for raw in profile_items
                if isinstance(raw, dict)
                and _int_value(raw.get("bucketHash")) == _VAULT_BUCKET_HASH
                and str(raw.get("itemInstanceId") or "").strip() not in {"", "0"}
                and not isinstance(
                    self._manifest.get_item_info(_int_value(raw.get("itemHash"))),
                    dict,
                )
            )
        instances_data = _profile_component_data(profile, "instances")
        if weapons and not instances_data:
            raise ConfigError(
                "Bungie 返回了武器列表，但缺少实例组件 itemComponents.instances；"
                "无法可靠判断账号重复武器，请稍后重试。"
            )
        sockets_data = _profile_component_data(profile, "sockets")

        groups: dict[int, dict[str, Any]] = {}
        seen_instances: dict[str, int] = {}
        invalid_instance_ids = 0
        missing_instance_metadata = 0
        missing_socket_data = 0
        unresolved_plugs = 0
        missing_weapon_definitions = 0

        for item in weapons:
            item_hash = to_unsigned(item.item_hash)
            instance_id = item.item_instance_id.strip()
            if not instance_id or instance_id == "0":
                invalid_instance_ids += 1
                continue

            previous_hash = seen_instances.get(instance_id)
            if previous_hash is not None:
                if previous_hash != item_hash:
                    raise ConfigError(
                        f"Bungie 返回的实例 {instance_id} 同时对应多个武器 hash，"
                        "无法可靠判断重复武器。"
                    )
                continue
            seen_instances[instance_id] = item_hash

            instance = instances_data.get(instance_id)
            if not isinstance(instance, dict):
                instance = {}
                missing_instance_metadata += 1

            has_definition = isinstance(
                self._manifest.get_item_info(item_hash),
                dict,
            )
            if not has_definition:
                missing_weapon_definitions += 1

            group = groups.setdefault(
                item_hash,
                {
                    "item_hash": item_hash,
                    "name": item.name if has_definition else "",
                    "weapon_type": item.item_type_display or item.item_type,
                    "icon_url": _cdn_url(item.icon_url),
                    "instances": [],
                },
            )
            group["instances"].append(
                {
                    "instance_id": instance_id,
                    "location": _LOCATION_LABELS.get(item.location, item.location),
                    "character_id": item.character_id,
                    "equipped": bool(item.is_equipped or instance.get("isEquipped", False)),
                    "power": (
                        instance.get("primaryStat", {}).get("value")
                        if instance
                        else None
                    ),
                }
            )

        duplicate_groups: list[dict[str, Any]] = []
        for group in groups.values():
            instances = group["instances"]
            if len(instances) < 2:
                continue
            instances.sort(
                key=lambda item: (
                    not item["equipped"],
                    _location_order(item["location"]),
                    item["instance_id"],
                )
            )
            group["instance_count"] = len(instances)
            duplicate_groups.append(group)
        duplicate_groups.sort(
            key=lambda group: (
                -group["instance_count"],
                group["name"],
                group["item_hash"],
            )
        )

        duplicate_instance_count = sum(
            group["instance_count"] for group in duplicate_groups
        )
        filtered_groups = duplicate_groups
        if item_name_filter:
            query = item_name_filter.casefold()
            filtered_groups = [
                group
                for group in filtered_groups
                if query in str(group["name"] or "").casefold()
            ]
        if type_name_filter:
            query = type_name_filter.casefold()
            filtered_groups = [
                group
                for group in filtered_groups
                if query in str(group["weapon_type"] or "").casefold()
            ]

        filtered_total = len(filtered_groups)
        page_groups = filtered_groups[page_offset : page_offset + page_limit]
        for group in page_groups:
            for instance in group["instances"]:
                perks, perks_complete, unresolved_count = _current_perks(
                    self._manifest,
                    instance["instance_id"],
                    sockets_data,
                )
                instance["perks_complete"] = perks_complete and unresolved_count == 0
                instance["perks"] = perks
                if not perks_complete:
                    missing_socket_data += 1
                unresolved_plugs += unresolved_count

        warnings: list[str] = []
        duplicate_scan_complete = not (
            invalid_instance_ids
            or missing_instance_metadata
            or missing_weapon_definitions
            or unclassified_vault_items
        )
        if invalid_instance_ids:
            warnings.append(
                f"有 {invalid_instance_ids} 件武器缺少 item_instance_id，已跳过；"
                "本次结果不能视为完整账号结论。"
            )
        if missing_instance_metadata:
            warnings.append(
                f"有 {missing_instance_metadata} 个武器实例缺少实例组件；"
                "已保留可核对的实例 ID，但本次结果不能视为完整账号结论。"
            )
        if missing_weapon_definitions or unclassified_vault_items:
            warnings.append(
                "有武器或仓库实例缺少 Manifest 定义，无法可靠给出名称或确认类型；"
                "本次结果不能视为完整账号结论。"
            )
        if missing_socket_data:
            warnings.append(
                f"本页有 {missing_socket_data} 个武器实例缺少当前 Perk socket 数据；"
                "重复关系仍按实例 ID 计算，但这些实例的 Perk 列表不完整。"
            )
        if unresolved_plugs:
            warnings.append(
                f"本页有 {unresolved_plugs} 个当前 socket 插件缺少 Manifest 定义，"
                "未猜测其名称。"
            )

        scanned_instances = len(seen_instances)
        if duplicate_scan_complete:
            summary = (
                f"已完整扫描 {scanned_instances} 个武器实例，"
                f"发现 {len(duplicate_groups)} 组重复武器。"
            )
        else:
            summary = (
                f"已扫描到 {scanned_instances} 个可核对的武器实例，"
                f"发现 {len(duplicate_groups)} 组重复武器；另有数据不完整。"
            )
        if item_name_filter or type_name_filter:
            summary += f"筛选后 {filtered_total} 组，本页返回 {len(page_groups)} 组。"
        elif page_offset or len(page_groups) < filtered_total:
            summary += (
                f"本页返回 {len(page_groups)} 组"
                f"（offset={page_offset}, limit={page_limit}）。"
            )

        next_offset = page_offset + len(page_groups)
        has_more = next_offset < filtered_total

        return {
            "summary": summary,
            "duplicates": page_groups,
            "scan": {
                "grouping_rule": "exact_item_hash_and_distinct_instance_id",
                "duplicate_scan_complete": duplicate_scan_complete,
                "perk_data_complete": not missing_socket_data and not unresolved_plugs,
                "perk_data_scope": "returned_page",
                "weapon_instances": scanned_instances,
                "unique_weapon_hashes": len(groups),
                "duplicate_groups": len(duplicate_groups),
                "duplicate_instances": duplicate_instance_count,
            },
            "filters": {
                "item_name": item_name_filter,
                "type_name": type_name_filter,
            },
            "pagination": {
                "offset": page_offset,
                "limit": page_limit,
                "returned": len(page_groups),
                "total": filtered_total,
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
            },
            "warnings": warnings,
        }


def _profile_component_data(
    profile: dict[str, Any],
    component: str,
) -> dict[str, dict[str, Any]]:
    data = (
        profile.get("itemComponents", {})
        .get(component, {})
        .get("data", {})
    )
    return data if isinstance(data, dict) else {}


def _current_perks(
    manifest: ManifestManager,
    instance_id: str,
    sockets_data: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, int]:
    socket_component = sockets_data.get(instance_id)
    if not isinstance(socket_component, dict):
        return [], False, 0
    sockets = socket_component.get("sockets")
    if not isinstance(sockets, list):
        return [], False, 0

    perks: list[dict[str, Any]] = []
    unresolved = 0
    for socket_index, socket in enumerate(sockets):
        if not isinstance(socket, dict):
            continue
        plug_hash = to_unsigned(_int_value(socket.get("plugHash")))
        if not plug_hash:
            continue
        category = manifest.get_plug_category_identifier(plug_hash)
        category = category if isinstance(category, str) else ""
        if not _is_relevant_weapon_plug(category):
            continue
        info = manifest.get_item_info(plug_hash)
        if not isinstance(info, dict):
            unresolved += 1
            continue
        name = str(info.get("name") or "").strip()
        if not name:
            unresolved += 1
            continue
        perks.append(
            {
                "socket_index": socket_index,
                "plug_hash": plug_hash,
                "name": name,
                "plug_category": category,
                "icon_url": _cdn_url(info.get("icon")),
            }
        )
    return perks, True, unresolved


def _is_relevant_weapon_plug(category: str) -> bool:
    key = category.lower()
    if any(token in key for token in ("shader", "tracker", "skin", "kill_vfx", "ornament")):
        return False
    return "mod" not in key or "weapon.mod" in key


def _cdn_url(value: Any) -> str:
    path = str(value or "").strip()
    if path.startswith("https://www.bungie.net/"):
        return path
    if path.startswith("/"):
        return f"https://www.bungie.net{path}"
    return ""


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _location_order(location: str) -> int:
    return {"猎人": 0, "泰坦": 1, "术士": 2, "仓库": 3}.get(location, 4)


def _duplicate_page_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("duplicates limit 必须是 1 到 25 的整数。") from exc
    if not 1 <= limit <= 25:
        raise ConfigError("duplicates limit 必须是 1 到 25 的整数。")
    return limit


def _duplicate_page_offset(value: Any) -> int:
    try:
        offset = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("duplicates offset 必须是大于或等于 0 的整数。") from exc
    if offset < 0:
        raise ConfigError("duplicates offset 必须是大于或等于 0 的整数。")
    return offset



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
