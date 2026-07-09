"""Weapon compare service — compare all instances of a weapon.

Requires Bungie API for profile data.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..exceptions import ItemNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name
from ..models import (
    PerkInfo,
    WeaponComparison,
    WeaponComparisonInstance,
)
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .perk_service import PerkService
from .profile_cache import ProfileCache
from .wishlist_service import WishListService

logger = get_logger(__name__)


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
        self, player_name: str, weapon_name: str
    ) -> WeaponComparison:
        """Compare all instances of a weapon across the account.

        Finds every copy of the weapon in inventory/vault, reads each one's
        current perks, and highlights the differences.

        Args:
            player_name: Bungie name.
            weapon_name: Weapon name to search for (Chinese or English).

        Returns:
            WeaponComparison with per-instance perks and diff.

        Raises:
            PlayerNotFoundError, ItemNotFoundError.
        """
        logger.info("Comparing weapon instances: %s for %s", weapon_name, player_name)

        # Step 1: Resolve player
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        # Step 2: Find weapon in manifest — collect ALL matching hashes
        # Use limit=50 to catch variants like 回文（专家） that sort later
        manifest_results = self._manifest.search(weapon_name, limit=50)
        weapon_defs = [r for r in manifest_results if r["itemType"] == 3]
        if not weapon_defs:
            raise ItemNotFoundError(weapon_name, "没有找到匹配的武器。")

        weapon_display_name = weapon_defs[0]["name"]

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
                player_name, [102, 200, 201, 205, 300, 305]
            )
        else:
            profile = await self._resolver.get_profile(
                mid, mtype, [102, 200, 201, 205, 300, 305]
            )

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

        # Collect from vault
        weapon_instances: list[dict] = []
        vault_items = (
            profile.get("profileInventory", {})
            .get("data", {})
            .get("items", [])
        )

        for raw in vault_items:
            if raw.get("itemHash") in target_hashes:
                inst_id = str(raw.get("itemInstanceId", "0"))
                weapon_instances.append({
                    "instance_id": inst_id,
                    "item_hash": raw.get("itemHash", 0),
                    "location": "仓库",
                    "power": instances_data.get(inst_id, {}).get("primaryStat", {}).get("value"),
                    "sockets": sockets_data.get(inst_id, {}).get("sockets", []),
                })

        # Collect from characters
        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})

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
                        })

        # Fallback: match by name for items whose hashes aren't in manifest
        matched_hashes = {inst["item_hash"] for inst in weapon_instances}
        name_matched = 0

        def _check_name_match(raw: dict, location: str) -> None:
            nonlocal name_matched
            h = raw.get("itemHash", 0)
            if h in matched_hashes or h in target_hashes:
                return
            name = self._manifest.get_item_name(h)
            if name == weapon_display_name:
                inst_id = str(raw.get("itemInstanceId", "0"))
                weapon_instances.append({
                    "instance_id": inst_id,
                    "item_hash": h,
                    "location": location,
                    "power": instances_data.get(inst_id, {}).get("primaryStat", {}).get("value"),
                    "sockets": sockets_data.get(inst_id, {}).get("sockets", []),
                })
                matched_hashes.add(h)
                name_matched += 1
                logger.info("NAME MATCH: hash=%d location=%s", h, location)

        for raw in vault_items:
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

        # Step 5: Read current perks for each instance
        comparison_instances: list[WeaponComparisonInstance] = []
        for inst in weapon_instances:
            current_perks: list[PerkInfo] = []
            for socket in inst["sockets"]:
                plug_hash = socket.get("plugHash")
                if not plug_hash:
                    continue
                info = self._manifest.get_item_info(plug_hash)
                if not info:
                    continue
                cat_id = self._manifest.get_plug_category_identifier(plug_hash) or ""
                if "shader" in cat_id.lower() or "mod" in cat_id.lower():
                    continue
                if "tracker" in cat_id.lower():
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
                self._perk_svc.annotate_god_roll(inst["item_hash"], plug_hash, perk)
                current_perks.append(perk)

            # Compute god roll score
            inst_item_hash = inst["item_hash"]
            score_str = ""
            if self._wishlist and self._wishlist.has_data(inst_item_hash):
                perk_hashes = [p.plug_hash for p in current_perks]
                score = self._wishlist.score_roll(inst_item_hash, perk_hashes)
                score_str = str(score)

            inst_weapon_def = next(
                (wd for wd in weapon_defs if inst_item_hash in {wd["itemHash"], to_unsigned(wd["itemHash"])}),
                weapon_defs[0],
            )
            icon_path = inst_weapon_def.get("icon", "")
            icon_url = f"https://www.bungie.net{icon_path}" if icon_path else ""

            comparison_instances.append(WeaponComparisonInstance(
                instance_id=inst["instance_id"],
                location=inst["location"],
                power=inst["power"],
                perks=current_perks,
                god_roll_score=score_str,
                icon_url=icon_url,
            ))

        # Step 6: Find differences
        differences: list[dict] = []
        if len(comparison_instances) >= 2:
            ref = comparison_instances[0]
            for other in comparison_instances[1:]:
                ref_perks = {p.plug_hash: p.name for p in ref.perks}
                other_perks = {p.plug_hash: p.name for p in other.perks}
                for ph, name in ref_perks.items():
                    if ph not in other_perks:
                        differences.append({
                            "perk_name": name,
                            "present_in": ref.location,
                            "absent_in": other.location,
                        })
                for ph, name in other_perks.items():
                    if ph not in ref_perks:
                        differences.append({
                            "perk_name": name,
                            "present_in": other.location,
                            "absent_in": ref.location,
                        })

        logger.info(
            "Compared %d instance(s) of '%s': %d difference(s)",
            len(comparison_instances),
            weapon_display_name,
            len(differences),
        )

        return WeaponComparison(
            weapon_name=weapon_display_name,
            instances=comparison_instances,
            differences=differences,
        )
