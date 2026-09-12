"""Weapon compare service — compare all instances of a weapon.

Requires Bungie API for profile data.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..exceptions import AuthenticationError, ConfigError, ItemNotFoundError
from ..logging_config import get_logger
from ..manifest_names import names_for
from . import profile_components, weapon_payload, weapon_profile
from ..manifest import ManifestManager, class_type_name
from ..models import WeaponComparison
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .perk_service import PerkService
from .profile_cache import ProfileCache
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
    require_complete_inventory_components,
)
from .wishlist_service import WishListService

logger = get_logger(__name__)


def _installed_plugs(instance: dict) -> dict[int, str]:
    """这一件现在装着的 plug（实例级 sockets 上的 `equipped`）。"""
    installed: dict[int, str] = {}
    for socket in instance.get("sockets") or []:
        equipped = socket.get("equipped")
        if isinstance(equipped, dict) and equipped.get("plug_hash"):
            installed[int(equipped["plug_hash"])] = str(equipped.get("name") or "")
    return installed


class WeaponCompareService:
    """Compare all instances of a weapon across a player's account."""

    def __init__(
        self,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        perk_service: PerkService,
        wishlist: WishListService | None = None,
        profile_cache: ProfileCache | None = None,
    ) -> None:
        self._manifest = manifest
        self._resolver = resolver
        self._perk_svc = perk_service
        self._wishlist = wishlist
        self._profile_cache = profile_cache

    async def compare_weapon_instances(
        self,
        player_name: str,
        weapon_name: str,
        item_instance_id: str | None = None,
    ) -> WeaponComparison:
        """Compare all instances of a weapon across the account.

        Finds every copy of the weapon in inventory/vault, reads each one's
        current perks, and highlights the differences.

        Args:
            player_name: Bungie name.
            weapon_name: Weapon name to search for (Chinese or English).
            item_instance_id: When provided, return only this owned instance.

        Returns:
            WeaponComparison with per-instance perks and diff.

        Raises:
            PlayerNotFoundError, ItemNotFoundError.
        """
        selected_instance_id = str(item_instance_id or "").strip()
        weapon_query = weapon_name.strip()
        if not weapon_query:
            raise ConfigError("请提供 weapon_name。")
        logger.info(
            "Comparing weapon instances: %s for %s (instance=%s)",
            weapon_query,
            player_name,
            selected_instance_id or "<all>",
        )

        # Step 1: Resolve player
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        # Step 2: Find weapon in manifest — collect ALL matching hashes
        manifest_results = self._manifest.search(weapon_query, limit=0)
        weapon_defs = [r for r in manifest_results if r["itemType"] == 3]
        if not weapon_defs:
            raise ItemNotFoundError(weapon_name, "没有找到匹配的武器。")

        weapon_display_name = weapon_defs[0]["name"]
        # 身份块以 Manifest 定义为准（搜索结果只有索引字段）
        primary_definition = self._manifest.get_item_definition(weapon_defs[0]["itemHash"]) or {}

        # API returns unsigned hashes, manifest uses signed — match both
        target_hashes: set[int] = set()
        for wd in weapon_defs:
            h = wd["itemHash"]
            target_hashes.add(h)
            target_hashes.add(to_unsigned(h))
        logger.info(
            "Weapon '%s': %d manifest entries, target_hashes=%s",
            weapon_display_name, len(weapon_defs), target_hashes,
        )

        # Step 3: Fetch profile with inventory + socket data
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

        # Step 4: Find all instances of this weapon
        sockets_data = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
        )
        instances_data = (
            profile.get("itemComponents", {})
            .get("instances", {})
            .get("data", {})
        )

        if not isinstance(sockets_data, dict):
            raise ConfigError("Bungie 未返回武器插槽组件，无法可靠对比 Perk。")

        # Collect from vault
        weapon_instances: list[dict] = []

        for raw in vault_items:
            if raw.get("bucketHash") == 138197802 and raw.get("itemHash") in target_hashes:
                inst_id = str(raw.get("itemInstanceId", "0"))
                weapon_instances.append({
                    "instance_id": inst_id,
                    "item_hash": raw.get("itemHash", 0),
                    "location": "仓库",
                    "power": instances_data.get(inst_id, {}).get("primaryStat", {}).get("value"),
                    "sockets": sockets_data.get(inst_id, {}).get("sockets", []),
                    "state": raw.get("state"),
                    "is_equipped": False,
                })

        # Collect from characters
        for char_id, char_info in chars_data.items():
            ct = char_info.get("classType", -1)
            loc_name = class_type_name(ct)

            for source_name, source in [
                ("inv", inv_data.get(char_id, {}).get("items", [])),
                ("equip", equip_data.get(char_id, {}).get("items", [])),
            ]:
                for raw in source:
                    if raw.get("itemHash") in target_hashes:
                        logger.info("CHAR %s %s: matched hash=%d", loc_name, source_name, raw.get("itemHash"))
                        inst_id = str(raw.get("itemInstanceId", "0"))
                        weapon_instances.append({
                            "instance_id": inst_id,
                            "item_hash": raw.get("itemHash", 0),
                            "location": loc_name,
                            "power": instances_data.get(inst_id, {}).get("primaryStat", {}).get("value"),
                            "sockets": sockets_data.get(inst_id, {}).get("sockets", []),
                            "state": raw.get("state"),
                            "is_equipped": source_name == "equip",
                        })

        # Fallback: match by name for items whose hashes aren't in manifest
        matched_instances = {inst["instance_id"] for inst in weapon_instances}
        name_matched = 0

        def _check_name_match(raw: dict, location: str) -> None:
            nonlocal name_matched
            h = raw.get("itemHash", 0)
            inst_id = str(raw.get("itemInstanceId", "0"))
            if inst_id in matched_instances or h in target_hashes:
                return
            name = self._manifest.get_item_name(h)
            if name == weapon_display_name:
                weapon_instances.append({
                    "instance_id": inst_id,
                    "item_hash": h,
                    "location": location,
                    "power": instances_data.get(inst_id, {}).get("primaryStat", {}).get("value"),
                    "sockets": sockets_data.get(inst_id, {}).get("sockets", []),
                    "state": raw.get("state"),
                    "is_equipped": False,
                })
                matched_instances.add(inst_id)
                name_matched += 1
                logger.info("NAME MATCH: hash=%d location=%s", h, location)

        for raw in vault_items:
            if raw.get("bucketHash") == 138197802:
                _check_name_match(raw, "仓库")

        for char_id, char_info in chars_data.items():
            ct = char_info.get("classType", -1)
            loc_name = class_type_name(ct)
            for source in [
                inv_data.get(char_id, {}).get("items", []),
                equip_data.get(char_id, {}).get("items", []),
            ]:
                for raw in source:
                    _check_name_match(raw, loc_name)

        if name_matched:
            logger.info("Name-based fallback found %d additional instance(s) for '%s'",
                        name_matched, weapon_display_name)

        if not weapon_instances:
            raise ItemNotFoundError(
                weapon_display_name,
                "你的账号上没有这把武器。",
            )

        if selected_instance_id:
            weapon_instances = [
                instance
                for instance in weapon_instances
                if instance["instance_id"] == selected_instance_id
            ]
            if not weapon_instances:
                raise ItemNotFoundError(
                    weapon_display_name,
                    f"账号内未找到实例 {selected_instance_id}。",
                )

        # Step 5: 每个副本 = 身份块（含副本字段）+ 实例级 sockets（能换的 + 现在装的）
        reusable_data = (
            profile.get("itemComponents", {}).get("reusablePlugs", {}).get("data", {})
        )
        stats_data = profile.get("itemComponents", {}).get("stats", {}).get("data", {})
        names = names_for(self._manifest)

        comparison_instances: list[dict] = []
        for inst in weapon_instances:
            inst_id = inst["instance_id"]
            socket_component = sockets_data.get(inst_id)
            if (
                not isinstance(socket_component, dict)
                or not isinstance(socket_component.get("sockets"), list)
                or not socket_component["sockets"]
            ):
                raise ConfigError(
                    f"武器实例 {inst_id} 缺少当前插槽数据，无法可靠对比 Perk。"
                )
            plug_hashes = [
                int(socket.get("plugHash") or 0) for socket in socket_component["sockets"]
            ]
            for plug_hash in plug_hashes:
                if not plug_hash:
                    continue
                if self._manifest.get_item_info(plug_hash):
                    continue
                get_definition = getattr(self._manifest, "get_item_definition", None)
                plug_def = get_definition(plug_hash) if callable(get_definition) else None
                display = (plug_def or {}).get("displayProperties") or {}
                if isinstance(plug_def, dict) and not display.get("name"):
                    continue
                raise ConfigError(
                    f"武器实例 {inst_id} 的插槽 {plug_hash} 缺少 Manifest 定义，"
                    "无法可靠对比 Perk。"
                )

            inst_definition = self._manifest.get_item_definition(inst["item_hash"]) or {}
            lookup = self._perk_svc.god_roll_lookup(inst["item_hash"])
            # sockets = 定义级的**列**（含"现在装的"），不展开池子：多个副本各带一份
            # 完整池子会让 analyze 从 60 KB 涨到 350 KB（实测），而且四份内容一模一样。
            # 池子是单把武器的问题（info/perk_pool）；这里要的是"这件装了什么、能换什么"。
            sockets = weapon_payload.column_list(
                self._manifest, inst_definition, equipped=plug_hashes
            )
            options = weapon_payload.socket_list(
                self._manifest,
                inst_definition,
                scope="instance",
                reusable=reusable_data.get(inst_id),
                equipped=plug_hashes,
                names=names,
                god_roll_lookup=lookup,
            )
            installed_hashes = [
                socket["equipped"]["plug_hash"]
                for socket in sockets
                if socket.get("equipped")
            ]

            score_str = ""
            if self._wishlist and self._wishlist.has_data(inst["item_hash"]):
                score_str = str(self._wishlist.score_roll(inst["item_hash"], installed_hashes))

            instance_block = weapon_payload.weapon_block(
                self._manifest,
                inst_definition,
                roll_summary=weapon_profile.roll_summary_from_columns(
                    weapon_profile.socket_columns(self._manifest, inst_definition)
                ),
                instance=instances_data.get(inst_id),
                names=names,
                fallback_name=weapon_display_name,
            )
            instance = {
                "instance_id": inst_id,
                "location": inst["location"],
                "power": inst["power"],
                "is_equipped": bool(inst.get("is_equipped")),
                "locked": weapon_profile.item_state_flags(inst.get("state"))["locked"],
                "god_roll_score": score_str,
            }
            instance_block["instance"] = instance
            comparison_instances.append({
                "weapon": instance_block,
                "sockets": sockets,
                "options": options,
                "stats": weapon_payload.stat_list(
                    self._manifest, inst_definition, (stats_data.get(inst_id) or {}).get("stats"), names=names
                ),
            })

        # Step 6: Find differences
        differences: list[dict] = []
        if len(comparison_instances) >= 2:
            ref = comparison_instances[0]
            for other in comparison_instances[1:]:
                ref_perks = _installed_plugs(ref)
                other_perks = _installed_plugs(other)
                ref_meta = ref["weapon"]["instance"]
                other_meta = other["weapon"]["instance"]
                # 必须定位到具体副本：两把都在仓库时，写 location 会得到
                # "present_in=仓库 / absent_in=仓库"，结论不可用。
                for ph, name in ref_perks.items():
                    if ph not in other_perks:
                        differences.append({
                            "perk_name": name,
                            "present_in_instance": ref_meta["instance_id"],
                            "present_in_location": ref_meta["location"],
                            "absent_in_instance": other_meta["instance_id"],
                            "absent_in_location": other_meta["location"],
                        })
                for ph, name in other_perks.items():
                    if ph not in ref_perks:
                        differences.append({
                            "perk_name": name,
                            "present_in_instance": other_meta["instance_id"],
                            "present_in_location": other_meta["location"],
                            "absent_in_instance": ref_meta["instance_id"],
                            "absent_in_location": ref_meta["location"],
                        })

        logger.info(
            "Compared %d instance(s) of '%s': %d difference(s)",
            len(comparison_instances),
            weapon_display_name,
            len(differences),
        )

        weapon_block = weapon_payload.weapon_block(
            self._manifest,
            primary_definition,
            names=names,
            fallback_name=weapon_display_name,
        )
        owned_summaries = []
        for entry in comparison_instances:
            meta = entry["weapon"]["instance"]
            owned_summaries.append(
                weapon_payload.owned_instance(
                    instance_id=meta["instance_id"],
                    location=meta["location"],
                    power=meta["power"],
                    is_equipped=meta["is_equipped"],
                    instance=instances_data.get(meta["instance_id"]),
                    locked=meta["locked"],
                    option_counts={
                        socket["slot"]: socket["option_count"]
                        for socket in entry["options"]
                        if socket.get("option_count", 0) > 1
                    },
                )
            )
        weapon_block["owned"] = weapon_payload.owned_block(owned_summaries)
        return WeaponComparison(
            weapon=weapon_block,
            instances=comparison_instances,
            differences=differences,
        )
