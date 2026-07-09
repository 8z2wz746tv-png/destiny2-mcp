"""Loadout equipment service — apply mods and subclass config when equipping loadouts.

Handles the complex equipment logic: transfer+equip armor, apply mod sockets,
apply subclass configuration. Extracted from loadout_service.py.
"""

from __future__ import annotations

import aiobungie

from ..bungie_client import BungieClient
from ..exceptions import ItemNotFoundError, TransferError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import Loadout, LoadoutOperationResult, LoadoutSubclassConfig, MoveItemStep
from ..player_resolver import PlayerResolver
from ..services.subclass_service import identify_socket_type
from ..services.transfer_service import TransferService

logger = get_logger(__name__)


class LoadoutEquipmentService:
    """Apply loadout equipment: transfer, equip, mods, subclass config."""

    # Plug category hashes for armor mods (not weapon perks)
    _MOD_CATEGORY_HASHES = {
        595201146,   # EnhancementsArtifice (精工模组)
        4065769746,  # EnhancementsV2 (通用模组)
        3315022374,  # EnhancementsV2ArmorOnly (护甲模组)
    }

    # Plug category hashes for subclass sockets
    _PLUG_CAT_SUPER = 3854839014
    _PLUG_CAT_ABILITIES = 3847589792  # grenade/melee/class_ability/movement
    _PLUG_CAT_ASPECTS = 3472571033
    _PLUG_CAT_FRAGMENTS = 2736821379
    # First N sockets are subclass ability slots (super/melee/grenade/class_ability/movement)
    _SUBCLASS_SOCKET_COUNT = 5

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._transfer = TransferService(bungie, manifest, resolver)

    # ── Reading helpers (used by both save and equip) ─────────────────

    def read_armor_mods(self, inst_id: str, sockets_data: dict) -> list[int]:
        """Read equipped mod plug hashes from an armor piece's sockets."""
        if not inst_id:
            return []

        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        mods: list[int] = []

        for socket in item_sockets:
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue
            plug_info = self._manifest.get_item_info(plug_hash) or {}
            plug_category = plug_info.get("plug", {}).get("plugCategoryHash", 0)
            if plug_category in self._MOD_CATEGORY_HASHES:
                mods.append(plug_hash)

        return mods

    def read_subclass_config(
        self, inst_id: str, item_hash: int, sockets_data: dict
    ) -> LoadoutSubclassConfig | None:
        """Read subclass configuration from sockets."""
        if not inst_id:
            return None

        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        if not item_sockets:
            return None

        aspect_hashes: list[int] = []
        fragment_hashes: list[int] = []
        super_hash = 0
        grenade_hash = 0
        melee_hash = 0
        class_ability_hash = 0
        movement_hash = 0

        for socket in item_sockets:
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue

            plug_info = self._manifest.get_item_info(plug_hash) or {}
            plug_category = plug_info.get("plug", {}).get("plugCategoryHash", 0)

            if plug_category == self._PLUG_CAT_SUPER:
                super_hash = plug_hash
            elif plug_category == self._PLUG_CAT_ABILITIES:
                cat_id = self._manifest.get_plug_category_identifier(plug_hash) or ""
                socket_type = identify_socket_type(cat_id)
                if socket_type == "grenade":
                    grenade_hash = plug_hash
                elif socket_type == "melee":
                    melee_hash = plug_hash
                elif socket_type == "class_ability":
                    class_ability_hash = plug_hash
                elif socket_type == "movement":
                    movement_hash = plug_hash
            elif plug_category == self._PLUG_CAT_ASPECTS:
                aspect_hashes.append(plug_hash)
            elif plug_category == self._PLUG_CAT_FRAGMENTS:
                fragment_hashes.append(plug_hash)

        if not any([super_hash, grenade_hash, melee_hash, class_ability_hash,
                    movement_hash, aspect_hashes, fragment_hashes]):
            return None

        return LoadoutSubclassConfig(
            super_hash=super_hash,
            grenade_hash=grenade_hash,
            melee_hash=melee_hash,
            class_ability_hash=class_ability_hash,
            movement_hash=movement_hash,
            aspect_hashes=aspect_hashes,
            fragment_hashes=fragment_hashes,
        )

    # ── Equipment application ────────────────────────────────────────

    async def equip_local(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Equip a local loadout by moving items one by one.

        Also applies saved mod configuration and subclass configuration.
        """
        steps: list[MoveItemStep] = []
        all_ok = True

        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]

        # Find character ID
        profile = await self._resolver.get_profile(mid, mtype, [200])
        chars_data = profile.get("characters", {}).get("data", {})
        char_id = None
        for cid, cinfo in chars_data.items():
            class_type = cinfo.get("classType", -1)
            class_name = {0: "titan", 1: "hunter", 2: "warlock"}.get(class_type, "")
            if class_name == loadout.character:
                char_id = cid
                break

        if not char_id:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"找不到角色 '{loadout.character}'。",
            )

        # Step 1: Transfer + equip armor pieces
        for lo_item in loadout.items:
            if not lo_item.item_instance_id:
                steps.append(MoveItemStep(
                    action="skip",
                    detail=f"跳过 '{lo_item.name}'（无实例 ID）",
                    success=False,
                ))
                all_ok = False
                continue

            try:
                transfer_result = await self._transfer.transfer_item(
                    player_name, lo_item.item_instance_id, loadout.character,
                )
                steps.append(MoveItemStep(
                    action="transfer",
                    detail=f"转移 '{lo_item.name}' → {loadout.character}",
                    success=transfer_result.success,
                ))

                if transfer_result.success:
                    equip_result = await self._transfer.equip_item(
                        player_name, lo_item.item_instance_id, loadout.character,
                    )
                    steps.append(MoveItemStep(
                        action="equip",
                        detail=f"装备 '{lo_item.name}'",
                        success=equip_result.success,
                    ))
                    if not equip_result.success:
                        all_ok = False
                else:
                    all_ok = False
            except (ItemNotFoundError, TransferError) as e:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"'{lo_item.name}' 装备失败: {e}",
                    success=False,
                ))
                all_ok = False

        # Step 2: Apply mod configuration (fetch sockets once for all items)
        sockets_cache: dict = {}
        for lo_item in loadout.items:
            if not lo_item.mods or not lo_item.item_instance_id:
                continue

            for mod_hash in lo_item.mods:
                try:
                    socket_idx = await self._find_mod_socket(
                        lo_item.item_instance_id, mod_hash, mid, mtype, sockets_cache
                    )
                    if socket_idx is not None:
                        mod_result = await self._bungie.insert_socket_plug_free(
                            lo_item.item_instance_id, mod_hash, socket_idx,
                            0, char_id, mtype,
                        )
                        ok = mod_result.get("ErrorCode", 0) == 1
                        steps.append(MoveItemStep(
                            action="mod",
                            detail=f"模组 {mod_hash} → '{lo_item.name}'",
                            success=ok,
                        ))
                        if not ok:
                            all_ok = False
                except aiobungie.HTTPError as e:
                    steps.append(MoveItemStep(
                        action="error",
                        detail=f"模组 {mod_hash} 应用失败: {e}",
                        success=False,
                    ))
                    all_ok = False

        # Step 3: Apply subclass configuration
        if loadout.subclass:
            subclass_ok = await self._apply_subclass_config(
                player_name, loadout, char_id, mtype, steps
            )
            if not subclass_ok:
                all_ok = False

        return LoadoutOperationResult(
            success=all_ok,
            loadout_name=loadout.name,
            message=(
                f"配装 '{loadout.name}' 已装备。"
                if all_ok
                else f"配装 '{loadout.name}' 部分装备失败。"
            ),
            steps=steps,
        )

    # ── Private helpers ──────────────────────────────────────────────

    async def _find_mod_socket(
        self,
        item_instance_id: str,
        mod_hash: int,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict | None = None,
    ) -> int | None:
        """Find the socket index where a mod should be inserted."""
        if sockets_cache is not None and item_instance_id in sockets_cache:
            sockets_data = sockets_cache[item_instance_id]
        else:
            profile = await self._resolver.get_profile(
                membership_id, membership_type, [305]
            )
            sockets_data = (
                profile.get("itemComponents", {})
                .get("sockets", {})
                .get("data", {})
                .get(item_instance_id, {})
                .get("sockets", [])
            )
            if sockets_cache is not None:
                sockets_cache[item_instance_id] = sockets_data

        mod_info = self._manifest.get_item_info(mod_hash) or {}
        mod_category = mod_info.get("plug", {}).get("plugCategoryHash", 0)

        # Pass 1: find socket with matching category that doesn't already have this exact mod
        for i, socket in enumerate(sockets_data):
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
                continue
            if plug_hash:
                plug_info = self._manifest.get_item_info(plug_hash) or {}
                plug_category = plug_info.get("plug", {}).get("plugCategoryHash", 0)
                if plug_category == mod_category:
                    return i

        # Pass 2: already has this mod
        for i, socket in enumerate(sockets_data):
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
                return i

        # Pass 3: empty socket in mod range
        for i, socket in enumerate(sockets_data):
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == 0 and i >= self._SUBCLASS_SOCKET_COUNT:
                return i

        return None

    async def _apply_subclass_config(
        self,
        player_name: str,
        loadout: Loadout,
        char_id: str,
        membership_type: int,
        steps: list[MoveItemStep],
    ) -> bool:
        """Apply subclass configuration."""
        subclass = loadout.subclass
        if not subclass:
            return True

        all_ok = True

        p = await self._resolver.resolve_player(player_name)
        profile = await self._resolver.get_profile(
            p["membership_id"], membership_type, [205, 305]
        )
        equip_data = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )
        sockets_map = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
        )

        subclass_inst_id = None
        for raw_item in equip_data:
            item_hash = raw_item.get("itemHash", 0)
            item_info = self._manifest.get_item_info(item_hash) or {}
            if item_info.get("itemType") == 16:
                subclass_inst_id = str(raw_item.get("itemInstanceId", ""))
                break

        if not subclass_inst_id:
            steps.append(MoveItemStep(
                action="error", detail="找不到已装备的子职业", success=False,
            ))
            return False

        all_plugs = []
        if subclass.super_hash:
            all_plugs.append(("super", subclass.super_hash))
        if subclass.grenade_hash:
            all_plugs.append(("grenade", subclass.grenade_hash))
        if subclass.melee_hash:
            all_plugs.append(("melee", subclass.melee_hash))
        if subclass.class_ability_hash:
            all_plugs.append(("class_ability", subclass.class_ability_hash))
        if subclass.movement_hash:
            all_plugs.append(("movement", subclass.movement_hash))
        if subclass.aspect_hashes:
            for h in subclass.aspect_hashes:
                all_plugs.append(("aspect", h))
        if subclass.fragment_hashes:
            for h in subclass.fragment_hashes:
                all_plugs.append(("fragment", h))

        assigned_sockets: set[int] = set()
        for plug_type, plug_hash in all_plugs:
            try:
                socket_idx = await self._find_subclass_socket(
                    subclass_inst_id, plug_hash, sockets_map, assigned_sockets
                )
                if socket_idx is not None:
                    assigned_sockets.add(socket_idx)
                    result = await self._bungie.insert_socket_plug_free(
                        subclass_inst_id, plug_hash, socket_idx,
                        0, char_id, membership_type,
                    )
                    ok = result.get("ErrorCode", 0) == 1
                    steps.append(MoveItemStep(
                        action="subclass",
                        detail=f"{plug_type} {plug_hash} 已应用",
                        success=ok,
                    ))
                    if not ok:
                        all_ok = False
            except aiobungie.HTTPError as e:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"{plug_type} {plug_hash} 应用失败: {e}",
                    success=False,
                ))
                all_ok = False

        return all_ok

    async def _find_subclass_socket(
        self,
        subclass_inst_id: str,
        plug_hash: int,
        sockets_map: dict,
        assigned_sockets: set[int] | None = None,
    ) -> int | None:
        """Find the socket index for a subclass plug (aspect/fragment)."""
        sockets = sockets_map.get(subclass_inst_id, {}).get("sockets", [])
        if assigned_sockets is None:
            assigned_sockets = set()

        plug_info = self._manifest.get_item_info(plug_hash) or {}
        plug_category = plug_info.get("plug", {}).get("plugCategoryHash", 0)

        if plug_category == 3472571033:  # Aspects
            candidate_range = range(3, 5)
        elif plug_category == 2736821379:  # Fragments
            candidate_range = range(5, 9)
        else:
            candidate_range = range(len(sockets))

        for i in candidate_range:
            if i < len(sockets) and i not in assigned_sockets:
                return i

        return None
