"""Weapon detail service — comprehensive weapon details by type.

Requires Bungie API for profile data.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..exceptions import AuthenticationError, ConfigError
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, class_type_name
from ..models import WeaponDetail, WeaponDetailResponse
from . import weapon_payload, weapon_profile
from ..manifest_names import names_for
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .profile_cache import ProfileCache
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
    require_complete_inventory_components,
)

logger = get_logger(__name__)


class WeaponDetailService:
    """Comprehensive weapon detail queries by weapon type."""

    def __init__(
        self,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        profile_cache: ProfileCache | None = None,
        lookup_factory=None,
    ) -> None:
        self._manifest = manifest
        self._resolver = resolver
        # 武器详情要组件 310（这一个副本能换什么），真机实测整个 profile 从 3.3 MB 涨到 10.2 MB。
        # 不接缓存就等于每次 type 查询都重新拉一遍；接上共享缓存后由 TTL 与后台刷新兜住。
        self._profile_cache = profile_cache
        # 愿单标注（god_roll_pve/pvp）：由 perk 服务提供，这里不直接依赖它
        self._lookup_factory = lookup_factory

    async def get_weapon_details_by_type(
        self, player_name: str, type_name: str, limit: int | None = None
    ) -> WeaponDetailResponse:
        """Get comprehensive details for all weapons of a given type.

        Args:
            player_name: Bungie name.
            type_name: Weapon type display name; empty means all owned weapons.
            limit: Max weapons returned; None or <= 0 returns everything.

        Returns:
            WeaponDetailResponse with matching weapons and their full details, plus
            total/returned counts so a cut list is never mistaken for a short one.
        """
        logger.info("Getting weapon details: player=%s, type=%s", player_name, type_name)

        # Type matching must not use the capped general Manifest search.
        type_name = type_name.strip()
        target_hashes = (
            {to_unsigned(r["itemHash"]) for r in self._manifest.list_weapon_catalog(type_name)}
            if type_name else None
        )
        hash_to_info: dict[int, dict] = {}

        # Step 2: Resolve player and fetch profile
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        if self._profile_cache:
            profile = await self._profile_cache.get_profile(
                player_name, profile_components.WEAPON_DETAIL
            )
        else:
            profile = await self._resolver.get_profile(
                mid, mtype, profile_components.WEAPON_DETAIL
            )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        require_complete_inventory_components(profile)

        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})
        vault_items = profile.get("profileInventory", {}).get("data", {}).get("items")

        # Step 3: Extract component data
        instances_data = (
            profile.get("itemComponents", {}).get("instances", {}).get("data", {})
        )
        sockets_data = (
            profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        stats_data = (
            profile.get("itemComponents", {}).get("stats", {}).get("data", {})
        )
        reusable_data = (
            profile.get("itemComponents", {}).get("reusablePlugs", {}).get("data", {})
        )

        # Step 4: Collect all matching weapon instances
        weapon_instances: list[dict] = []
        seen_instances: dict[str, int] = {}

        def add_weapon(raw: dict, location: str, is_equipped: bool) -> None:
            item_hash = to_unsigned(raw["itemHash"])
            info = self._manifest.get_item_info(item_hash)
            if not info:
                if raw.get("itemInstanceId"):
                    raise ConfigError("库存实例缺少 Manifest 定义，无法确认完整武器范围；请更新 Manifest 后重试。")
                return
            if info.get("itemType") != 3:
                return
            if target_hashes is not None and item_hash not in target_hashes:
                return
            inst_id = str(raw.get("itemInstanceId") or "")
            if inst_id in {"", "0"}:
                raise ConfigError("库存武器缺少实例 ID，无法可靠筛选 Perk；请稍后重试。")
            if inst_id in seen_instances:
                if seen_instances[inst_id] != item_hash:
                    raise ConfigError("库存实例 ID 对应多个武器定义；请稍后重试。")
                return
            seen_instances[inst_id] = item_hash
            hash_to_info[item_hash] = info
            weapon_instances.append({
                "instance_id": inst_id,
                "item_hash": item_hash,
                "location": location,
                "is_equipped": is_equipped,
                "state": raw.get("state"),
            })

        # From vault
        for raw in vault_items:
            if raw.get("bucketHash") == 138197802:
                add_weapon(raw, "仓库", False)

        # From characters
        for char_id, char_info in chars_data.items():
            ct = char_info.get("classType", -1)
            loc_name = class_type_name(ct)

            for raw in inv_data.get(char_id, {}).get("items", []):
                add_weapon(raw, loc_name, False)

            for raw in equip_data.get(char_id, {}).get("items", []):
                add_weapon(raw, loc_name, True)

        # Step 5: Build detailed weapon info
        names = names_for(self._manifest)
        # 同一个定义会被多个副本复用：能滚几栏的计数按 item_hash 缓存一次
        summary_cache: dict[int, dict] = {}
        details: list[WeaponDetail] = []
        for wi in weapon_instances:
            inst_id = wi["instance_id"]
            item_hash = wi["item_hash"]

            inst_info = instances_data.get(inst_id, {})
            weapon_def = self._manifest.get_item_definition(item_hash)

            raw_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
            plug_hashes = [int(socket.get("plugHash") or 0) for socket in raw_sockets]
            perks_complete = bool(raw_sockets)
            for plug_hash in plug_hashes:
                if plug_hash and not self._manifest.get_item_info(plug_hash) and not isinstance(
                    self._manifest.get_item_definition(plug_hash), dict
                ):
                    perks_complete = False

            if item_hash not in summary_cache:
                summary_cache[item_hash] = weapon_profile.roll_summary_from_columns(
                    weapon_profile.socket_columns(self._manifest, weapon_def)
                )
            # 列表类只放"这一件能换什么"（组件 310）+ 现在装的；完整池是单把武器的问题
            # （`info`/`perk_pool`）。实测把整张池子塞进列表：5 把武器 597 KB。
            sockets = weapon_payload.column_list(
                self._manifest, weapon_def, equipped=plug_hashes
            )
            options = weapon_payload.socket_list(
                self._manifest,
                weapon_def,
                scope="instance",
                reusable=reusable_data.get(inst_id),
                equipped=plug_hashes,
                names=names,
            )

            inst_stats = (stats_data.get(inst_id, {}) or {}).get("stats", {})
            stats = weapon_payload.stat_list(self._manifest, weapon_def, inst_stats)
            weapon = weapon_payload.weapon_block(
                self._manifest,
                weapon_def,
                roll_summary=summary_cache[item_hash],
                instance=inst_info,
                names=names,
            )

            instance_meta = weapon_profile.instance_fields(inst_info)
            state_flags = weapon_profile.item_state_flags(wi.get("state"))

            notes: list[str] = []
            if instance_meta["missing"]:
                notes.append(
                    "该副本缺少实例信息（组件 300 未返回）："
                    + "、".join(instance_meta["missing"])
                    + " 为 null"
                )
            if state_flags["locked"] is None:
                notes.append("库存条目未带 state 字段：locked/tracked 为 null")
            if not options:
                notes.append(
                    "该副本没有可更换部件数据（组件 310 未返回）：options 为空，"
                    "只能看 sockets 里已装的内容"
                )

            weapon["instance"] = {
                "instance_id": inst_id,
                "location": wi["location"],
                "power": inst_info.get("primaryStat", {}).get("value"),
                "is_equipped": wi["is_equipped"],
                "locked": state_flags["locked"],
                "tracked": state_flags["tracked"],
            }

            details.append(WeaponDetail(
                weapon=weapon,
                sockets=sockets,
                options=options,
                stats=stats,
                perks_complete=perks_complete and bool(raw_sockets),
                notes=notes,
            ))

        details.sort(
            key=lambda d: (
                not d.weapon.get("instance", {}).get("is_equipped", False),
                -int(d.weapon.get("instance", {}).get("power") or 0),
                str(d.weapon.get("name") or ""),
            )
        )

        total = len(details)
        returned = details if not limit or limit <= 0 else details[:limit]

        logger.info(
            "Weapon details for '%s': %d of %d instance(s) returned",
            type_name, len(returned), total,
        )

        return WeaponDetailResponse(
            weapon_type_query=type_name,
            weapons=returned,
            total_weapons=total,
            returned_weapons=len(returned),
            truncated=len(returned) < total,
        )
