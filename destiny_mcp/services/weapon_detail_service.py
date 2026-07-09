"""Weapon detail service — comprehensive weapon details by type.

Requires Bungie API for profile data.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name
from ..models import (
    WeaponDetail,
    WeaponDetailResponse,
    WeaponSocketInfo,
    WeaponStats,
)
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned

logger = get_logger(__name__)


class WeaponDetailService:
    """Comprehensive weapon detail queries by weapon type."""

    # Ammo type mapping
    _AMMO_TYPES = {1: "白弹", 2: "绿弹", 3: "紫弹"}

    # Damage type hash → name (unsigned, matching API response)
    _DAMAGE_TYPE_NAMES = {
        3373582085: "动能",
        2303181850: "电弧",
        1847026933: "烈日",
        3454344768: "虚空",
        151347233: "冰影",
        3949783978: "缚丝",
    }

    def __init__(
        self,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._manifest = manifest
        self._resolver = resolver

    @staticmethod
    def _cat_to_label(cat: str, trait_counter: dict) -> str:
        """Map a plugCategoryIdentifier to a display label."""
        c = cat.lower()
        if "intrinsic" in c:
            return "框架"
        if "barrel" in c:
            return "枪管"
        if "scope" in c:
            return "瞄具"
        if "magazine" in c or "battery" in c or "batteries" in c:
            return "弹匣"
        if "stock" in c:
            return "枪托"
        if "frame" in c:
            trait_counter["count"] += 1
            return f"特性{trait_counter['count']}"
        if "shader" in c:
            return "着色器"
        if "mod" in c:
            return "模组"
        if "masterwork" in c and "tracker" in c:
            return "追踪器"
        if "masterwork" in c:
            return "大师杰作"
        if "kill_vfx" in c:
            return "战斗特效"
        if "skin" in c or "tiering" in c:
            return "纪念物"
        if "catalyst" in c:
            return "催化"
        return ""

    def _categorize_socket(
        self, socket_index: int, plug_hash: int,
        weapon_def: dict,
        trait_counter: dict,
    ) -> WeaponSocketInfo | None:
        """Categorize a socket by its plug's plugCategoryIdentifier."""
        if not plug_hash:
            return None
        if not weapon_def:
            return None

        socket_entries = weapon_def.get("sockets", {}).get("socketEntries", [])
        label = ""
        if socket_index < len(socket_entries):
            entry = socket_entries[socket_index]
            plug_set_hash = entry.get("randomizedPlugSetHash") or entry.get("reusablePlugSetHash")
            if plug_set_hash:
                plug_set_plugs = self._manifest.get_plug_set_plugs(plug_set_hash)
                if plug_set_plugs:
                    first_cat = plug_set_plugs[0].get("plugCategoryIdentifier", "")
                    label = self._cat_to_label(first_cat, trait_counter)
            elif entry.get("singleInitialItemHash"):
                init_cat = self._manifest.get_plug_category_identifier(entry["singleInitialItemHash"]) or ""
                label = self._cat_to_label(init_cat, trait_counter)

        if not label:
            return None

        info = self._manifest.get_item_info(plug_hash)
        name = info.get("name", f"#{plug_hash}") if info else f"#{plug_hash}"
        cat_id = self._manifest.get_plug_category_identifier(plug_hash) or ""

        desc = ""
        sandbox = self._manifest.get_sandbox_perk_description(plug_hash)
        if sandbox:
            desc = sandbox.get("description", "")

        return WeaponSocketInfo(
            slot_label=label,
            plug_name=name,
            plug_hash=plug_hash,
            plug_category=cat_id,
            description=desc,
        )

    def _build_weapon_stats(self, instance_stats: dict) -> WeaponStats:
        """Build WeaponStats from API stats data."""
        kwargs = {}
        stat_field_map = {
            "4284893193": "rpm",
            "4043523819": "damage",
            "1240592695": "range",
            "155624089": "stability",
            "943549884": "handling",
            "4188031367": "reload_speed",
            "1345609583": "aim_assist",
            "3555269338": "zoom",
            "2714457168": "airborne_effectiveness",
            "2715839340": "recoil_direction",
            "3871231066": "magazine",
        }
        for stat_hash_str, field_name in stat_field_map.items():
            stat_entry = instance_stats.get(stat_hash_str, {})
            kwargs[field_name] = stat_entry.get("value", 0)
        return WeaponStats(**kwargs)

    async def get_weapon_details_by_type(
        self, player_name: str, type_name: str
    ) -> WeaponDetailResponse:
        """Get comprehensive details for all weapons of a given type.

        Args:
            player_name: Bungie name.
            type_name: Weapon type display name (e.g. '微型冲锋枪').

        Returns:
            WeaponDetailResponse with all matching weapons and their full details.
        """
        logger.info("Getting weapon details: player=%s, type=%s", player_name, type_name)

        # Step 1: Find all item hashes matching this type
        type_results = self._manifest.search_by_type_name(type_name)
        if not type_results:
            return WeaponDetailResponse(weapon_type_query=type_name, weapons=[])

        # Build target_hashes with BOTH signed and unsigned variants
        target_hashes: set[int] = set()
        hash_to_info: dict[int, dict] = {}
        for r in type_results:
            h = r["itemHash"]
            target_hashes.add(h)
            unsigned = to_unsigned(h)
            target_hashes.add(unsigned)
            hash_to_info[h] = r
            hash_to_info[unsigned] = r

        # Step 2: Resolve player and fetch profile
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 300, 304, 305]
        )

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

        # Step 4: Collect all matching weapon instances
        weapon_instances: list[dict] = []

        # From vault
        vault_items = (
            profile.get("profileInventory", {}).get("data", {}).get("items", [])
        )
        for raw in vault_items:
            if raw.get("itemHash") in target_hashes:
                inst_id = str(raw.get("itemInstanceId", "0"))
                weapon_instances.append({
                    "instance_id": inst_id,
                    "item_hash": raw["itemHash"],
                    "location": "仓库",
                    "is_equipped": False,
                })

        # From characters
        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})

        for char_id, char_info in chars_data.items():
            ct = char_info.get("classType", -1)
            loc_name = class_type_name(ct)

            for raw in inv_data.get(char_id, {}).get("items", []):
                if raw.get("itemHash") in target_hashes:
                    inst_id = str(raw.get("itemInstanceId", "0"))
                    weapon_instances.append({
                        "instance_id": inst_id,
                        "item_hash": raw["itemHash"],
                        "location": loc_name,
                        "is_equipped": False,
                    })

            for raw in equip_data.get(char_id, {}).get("items", []):
                if raw.get("itemHash") in target_hashes:
                    inst_id = str(raw.get("itemInstanceId", "0"))
                    weapon_instances.append({
                        "instance_id": inst_id,
                        "item_hash": raw["itemHash"],
                        "location": loc_name,
                        "is_equipped": True,
                    })

        # Step 5: Build detailed weapon info
        details: list[WeaponDetail] = []
        for wi in weapon_instances:
            inst_id = wi["instance_id"]
            item_hash = wi["item_hash"]

            inst_info = instances_data.get(inst_id, {})
            power = inst_info.get("primaryStat", {}).get("value")
            damage_type_hash = inst_info.get("damageTypeHash", 0)
            damage_type = self._DAMAGE_TYPE_NAMES.get(damage_type_hash, "")

            w_info = hash_to_info.get(item_hash, {})
            weapon_name = w_info.get("name", self._manifest.get_item_name(item_hash))
            # manifest already stores icon as full Bungie CDN URL
            icon_url = w_info.get("icon", "")
            weapon_type = w_info.get("itemTypeNameDisplay", type_name)
            tier_num = w_info.get("tier", 0)
            tier = {5: "传说", 6: "异域"}.get(tier_num, "")

            weapon_def = self._manifest.get_item_definition(item_hash)
            ammo_type_val = 0
            if weapon_def:
                ammo_type_val = weapon_def.get("equippingBlock", {}).get("ammoType", 0)
            ammo_type = self._AMMO_TYPES.get(ammo_type_val, "")

            socket_list = sockets_data.get(inst_id, {}).get("sockets", [])
            sockets: list[WeaponSocketInfo] = []
            trait_counter = {"count": 0}
            for idx, socket in enumerate(socket_list):
                plug_hash = socket.get("plugHash", 0)
                si = self._categorize_socket(
                    idx, plug_hash, weapon_def, trait_counter
                )
                if si:
                    sockets.append(si)

            inst_stats_raw = stats_data.get(inst_id, {})
            inst_stats = inst_stats_raw.get("stats", {})
            weapon_stats = self._build_weapon_stats(inst_stats)

            if weapon_stats.magazine == 0 and weapon_def:
                manifest_stats = weapon_def.get("stats", {}).get("stats", {})
                mag_entry = manifest_stats.get("3871231066") or manifest_stats.get(str(3871231066))
                if mag_entry:
                    weapon_stats.magazine = mag_entry.get("value", 0)

            details.append(WeaponDetail(
                instance_id=inst_id,
                item_hash=item_hash,
                name=weapon_name,
                weapon_type=weapon_type,
                tier=tier,
                damage_type=damage_type,
                ammo_type=ammo_type,
                power=power,
                location=wi["location"],
                is_equipped=wi["is_equipped"],
                sockets=sockets,
                stats=weapon_stats,
                icon_url=icon_url,
            ))

        details.sort(key=lambda d: (not d.is_equipped, -(d.power or 0), d.name))

        logger.info(
            "Weapon details for '%s': %d instance(s) found",
            type_name, len(details),
        )

        return WeaponDetailResponse(
            weapon_type_query=type_name,
            weapons=details,
        )
