"""Loadout equipment service — apply mods and subclass config when equipping loadouts.

Handles the complex equipment logic: transfer+equip armor, apply mod sockets,
apply subclass configuration. Extracted from loadout_service.py.
"""

from __future__ import annotations

import asyncio

import aiobungie
import anyio

from ..bungie_client import BungieClient
from ..exceptions import DestinyMCPError, ItemNotFoundError, TransferError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import (
    Loadout,
    LoadoutItem,
    LoadoutOperationResult,
    LoadoutSubclassConfig,
    MoveItemStep,
)
from ..player_resolver import PlayerResolver
from ..services.subclass_service import identify_socket_type
from ..services.transfer_service import TransferService
from ..utils.item_parser import parse_items_from_profile
from .account_action_lock import account_action_lock

logger = get_logger(__name__)

_CANCEL_ROLLBACK_TIMEOUT_SECONDS = 60

class LoadoutEquipmentService:
    """Apply loadout equipment: transfer, equip, mods, subclass config."""

    # Plug category hashes for armor mods (not weapon perks)
    _MOD_CATEGORY_HASHES = set(ManifestManager._ARMOR_MOD_CATEGORIES) | {
        595201146,   # Legacy EnhancementsArtifice
        4065769746,  # Legacy EnhancementsV2
        3315022374,  # Legacy EnhancementsV2ArmorOnly
    }

    # Plug category hashes for subclass sockets
    _PLUG_CAT_SUPER = 3854839014
    _PLUG_CAT_ABILITIES = 3847589792  # grenade/melee/class_ability/movement
    _PLUG_CAT_ASPECTS = 3472571033
    _PLUG_CAT_FRAGMENTS = 2736821379
    _PLUG_CAT_TUNING = 3481777685
    _ARMOR_SLOTS = {
        3448274439: "helmet",
        3551918588: "gauntlets",
        14239492: "chest",
        20886954: "legs",
        1585787867: "class_item",
    }

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
        self._equip_lock = account_action_lock(bungie)

    # ── Reading helpers (used by both save and equip) ─────────────────

    def _plug_category_hash(self, plug_hash: int) -> int:
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            definition = {}
        if not (definition.get("plug") or {}):
            summary = self._manifest.get_item_info(plug_hash)
            definition = summary if isinstance(summary, dict) else {}
        category = (definition.get("plug") or {}).get("plugCategoryHash", 0)
        return category if isinstance(category, int) else 0

    def read_armor_mod_sockets(
        self,
        inst_id: str,
        item_hash: int,
        sockets_data: dict,
    ) -> dict[int, int]:
        """Read exact armor mod socket state, including empty default plugs."""
        if not inst_id:
            return {}
        item_sockets = sockets_data.get(inst_id, {}).get("sockets", [])
        item_definition = self._manifest.get_item_definition(item_hash)
        if not isinstance(item_definition, dict):
            item_definition = {}
        socket_entries = (item_definition.get("sockets") or {}).get(
            "socketEntries", []
        )
        result: dict[int, int] = {}
        for index, socket in enumerate(item_sockets):
            plug_hash = socket.get("plugHash", 0)
            if plug_hash and self._plug_category_hash(plug_hash) in self._MOD_CATEGORY_HASHES:
                result[index] = plug_hash
                continue
            if index >= len(socket_entries):
                continue
            default_hash = socket_entries[index].get("singleInitialItemHash", 0)
            if (
                default_hash
                and self._plug_category_hash(default_hash) in self._MOD_CATEGORY_HASHES
            ):
                result[index] = plug_hash or default_hash
        return result

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
            plug_category = self._plug_category_hash(plug_hash)
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
        plug_sockets: dict[int, int] = {}

        for socket_index, socket in enumerate(item_sockets):
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue

            plug_category = self._plug_category_hash(plug_hash)
            category_identifier = (
                self._manifest.get_plug_category_identifier(plug_hash) or ""
            )
            socket_type = (
                identify_socket_type(category_identifier)
                if category_identifier
                else ""
            )
            if not socket_type:
                if plug_category == self._PLUG_CAT_SUPER:
                    socket_type = "super"
                elif plug_category == self._PLUG_CAT_ASPECTS:
                    socket_type = "aspect"
                elif plug_category == self._PLUG_CAT_FRAGMENTS:
                    socket_type = "fragment"

            if socket_type == "super":
                super_hash = plug_hash
            elif socket_type == "grenade":
                grenade_hash = plug_hash
            elif socket_type == "melee":
                melee_hash = plug_hash
            elif socket_type == "class_ability":
                class_ability_hash = plug_hash
            elif socket_type == "movement":
                movement_hash = plug_hash
            elif socket_type == "aspect":
                aspect_hashes.append(plug_hash)
            elif socket_type == "fragment":
                fragment_hashes.append(plug_hash)
            else:
                continue
            plug_sockets[socket_index] = plug_hash

        if not any([super_hash, grenade_hash, melee_hash, class_ability_hash,
                    movement_hash, aspect_hashes, fragment_hashes]):
            return None

        return LoadoutSubclassConfig(
            subclass_item_hash=item_hash,
            subclass_instance_id=inst_id,
            super_hash=super_hash,
            grenade_hash=grenade_hash,
            melee_hash=melee_hash,
            class_ability_hash=class_ability_hash,
            movement_hash=movement_hash,
            aspect_hashes=aspect_hashes,
            fragment_hashes=fragment_hashes,
            plug_sockets=plug_sockets,
        )

    # ── Equipment application ────────────────────────────────────────

    async def equip_local(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Serialize local loadout writes for this user context."""
        async with self._equip_lock:
            return await self._equip_local_unlocked(player_name, loadout)

    async def _equip_local_unlocked(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Equip a local loadout by transferring, then batch-equipping items.

        Also applies saved mod configuration and subclass configuration.
        """
        steps: list[MoveItemStep] = []
        all_ok = True

        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]

        # Find character ID
        profile = await self._resolver.get_profile(mid, mtype, [200, 300, 305])
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

        # Step 1: Transfer every item before one batch equip. Equipping piece by
        # piece can fail for valid exotic swaps while the old exotic is active.
        transferred_ids: list[str] = []
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
                    transferred_ids.append(lo_item.item_instance_id)
                else:
                    all_ok = False
            except (ItemNotFoundError, TransferError) as e:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"'{lo_item.name}' 装备失败: {e}",
                    success=False,
                ))
                all_ok = False

        if loadout.items and len(transferred_ids) == len(loadout.items):
            try:
                equip_result = await self._transfer.equip_items(
                    player_name, transferred_ids, loadout.character
                )
                equipped = bool(equip_result.get("success"))
                steps.append(MoveItemStep(
                    action="equip_many",
                    detail=f"批量装备 {len(transferred_ids)} 件物品",
                    success=equipped,
                ))
                all_ok = all_ok and equipped
            except (ItemNotFoundError, TransferError) as exc:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"批量装备失败: {exc}",
                    success=False,
                ))
                all_ok = False
        elif loadout.items:
            steps.append(MoveItemStep(
                action="equip_many",
                detail="存在转移失败，未执行批量装备。",
                success=False,
            ))
            all_ok = False

        if not all_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装 '{loadout.name}' 装备阶段失败，未继续修改模组或子职业。",
                steps=steps,
            )

        # Step 2: Resolve all socket writes against one socket/energy snapshot.
        item_components = profile.get("itemComponents", {})
        instances_data = item_components.get("instances", {}).get("data", {})
        sockets_cache = {
            instance_id: payload.get("sockets", [])
            for instance_id, payload in (
                item_components.get("sockets", {}).get("data", {})
            ).items()
        }
        mod_operations: dict[str, list[tuple[str, int, int]]] = {}
        for lo_item in loadout.items:
            if (not lo_item.mods and not lo_item.mod_sockets) or not lo_item.item_instance_id:
                continue
            try:
                mod_operations[lo_item.item_instance_id] = (
                    await self._prepare_mod_operations(
                        lo_item,
                        mid,
                        mtype,
                        sockets_cache,
                        instances_data,
                    )
                )
            except TransferError as exc:
                steps.append(MoveItemStep(
                    action="mod_preflight",
                    detail=str(exc),
                    success=False,
                ))
                all_ok = False

        if not all_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装 '{loadout.name}' 模组预检失败，未修改模组或子职业。",
                steps=steps,
            )

        for lo_item in loadout.items:
            for operation, mod_hash, socket_idx in mod_operations.get(
                lo_item.item_instance_id, []
            ):
                try:
                    mod_result = await self._insert_armor_mod(
                        lo_item.item_instance_id,
                        mod_hash,
                        socket_idx,
                        char_id,
                        mtype,
                    )
                    ok = mod_result.get("ErrorCode", 0) == 1
                    steps.append(MoveItemStep(
                        action="mod_clear" if operation == "clear" else "mod",
                        detail=(
                            f"为属性模组腾出能量：'{lo_item.name}' 插槽 {socket_idx}"
                            if operation == "clear"
                            else f"模组 {mod_hash} → '{lo_item.name}'"
                        ),
                        success=ok,
                    ))
                    if not ok:
                        all_ok = False
                        break
                except (aiobungie.HTTPError, TransferError) as e:
                    steps.append(MoveItemStep(
                        action="error",
                        detail=f"模组 {mod_hash} 应用失败: {e}",
                        success=False,
                    ))
                    all_ok = False
                    break

        if not all_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装 '{loadout.name}' 模组阶段失败，未继续修改子职业。",
                steps=steps,
            )

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

    async def equip_exact(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Apply an exact loadout, verify it, and restore prior state on failure."""
        async with self._equip_lock:
            return await self._equip_exact_unlocked(player_name, loadout)

    async def _equip_exact_unlocked(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Apply exact loadout while the per-user equipment lock is held."""
        try:
            recovery = await self._capture_recovery_state(player_name, loadout)
        except (ItemNotFoundError, TransferError) as exc:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装预检失败：{exc}",
                steps=[MoveItemStep(action="preflight", detail=str(exc), success=False)],
            )

        try:
            return await self._apply_exact_with_recovery(player_name, loadout, recovery)
        except asyncio.CancelledError:
            # Stay in the lock-owning task: rollback re-enters account-write methods.
            rollback_ok = False
            steps: list[MoveItemStep] = []
            with anyio.move_on_after(_CANCEL_ROLLBACK_TIMEOUT_SECONDS, shield=True):
                try:
                    rollback_ok = await self._restore_exact_state(
                        player_name, loadout, recovery, steps
                    )
                except Exception:
                    logger.exception("Cancelled exact loadout rollback failed")
            if rollback_ok:
                logger.info("Cancelled exact loadout restored the previous equipment state")
            else:
                logger.error("Cancelled exact loadout recovery incomplete; check character equipment")
            raise

    async def _apply_exact_with_recovery(
        self, player_name: str, loadout: Loadout, recovery: dict
    ) -> LoadoutOperationResult:
        try:
            applied = await self._equip_local_unlocked(player_name, loadout)
        except (DestinyMCPError, aiobungie.HTTPError, OSError) as exc:
            logger.error("Exact loadout application failed: %s", exc)
            applied = LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=str(exc),
                steps=[MoveItemStep(action="apply", detail=str(exc), success=False)],
            )
        verified = False
        if applied.success:
            verification_detail = "执行结果与确认的配装不一致。"
            try:
                verified = await self._verify_loadout(player_name, loadout)
            except (DestinyMCPError, aiobungie.HTTPError, OSError) as exc:
                logger.error("Exact loadout verification failed: %s", exc)
                verification_detail = str(exc)
            applied.steps.append(MoveItemStep(
                action="verify",
                detail=(
                    "已验证装备实例、模组和子职业配置。"
                    if verified
                    else verification_detail
                ),
                success=verified,
            ))

        if applied.success and verified:
            return applied

        rollback_steps: list[MoveItemStep] = []
        try:
            rollback_ok = await self._restore_exact_state(
                player_name,
                loadout,
                recovery,
                rollback_steps,
            )
        except (DestinyMCPError, aiobungie.HTTPError, OSError, TypeError) as exc:
            logger.exception("Exact loadout rollback failed: %s", exc)
            rollback_steps.append(MoveItemStep(
                action="rollback_error", detail=str(exc), success=False
            ))
            rollback_ok = False
        return LoadoutOperationResult(
            success=False,
            loadout_name=loadout.name,
            message=(
                f"配装 '{loadout.name}' 执行失败，已恢复执行前状态。"
                if rollback_ok
                else f"配装 '{loadout.name}' 执行失败，且自动恢复不完整；请检查角色装备。"
            ),
            steps=[*applied.steps, *rollback_steps],
        )

    async def _capture_recovery_state(self, player_name: str, loadout: Loadout) -> dict:
        """Capture equipped gear plus original state of every target item."""
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 300, 304, 305]
        )
        chars = profile.get("characters", {}).get("data", {})
        char_id = next(
            (
                cid
                for cid, info in chars.items()
                if {0: "titan", 1: "hunter", 2: "warlock"}.get(
                    info.get("classType", -1), ""
                )
                == loadout.character
            ),
            None,
        )
        if not char_id:
            raise TransferError("配装预检", f"找不到角色 '{loadout.character}'。")

        sockets_data = (
            profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        equipped_raw = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )
        equipped_ids = {
            str(item.get("itemInstanceId", ""))
            for equipment in (
                profile.get("characterEquipment", {}).get("data", {}).values()
            )
            for item in equipment.get("items", [])
        }

        previous_items: list[LoadoutItem] = []
        previous_subclass: LoadoutSubclassConfig | None = None
        for raw in equipped_raw:
            item_hash = raw.get("itemHash", 0)
            instance_id = str(raw.get("itemInstanceId", ""))
            slot = self._ARMOR_SLOTS.get(raw.get("bucketHash", 0))
            if slot:
                mod_sockets = self.read_armor_mod_sockets(
                    instance_id, item_hash, sockets_data
                )
                previous_items.append(LoadoutItem(
                    item_hash=item_hash,
                    name=self._manifest.get_item_name(item_hash),
                    slot=slot,
                    item_instance_id=instance_id,
                    mods=list(mod_sockets.values()),
                    mod_sockets=mod_sockets,
                    source_location=loadout.character,
                    source_character_id=char_id,
                    was_equipped=True,
                ))
                continue

            item_info = self._manifest.get_item_info(item_hash) or {}
            if item_info.get("itemType") == 16:
                previous_subclass = self.read_subclass_config(
                    instance_id, item_hash, sockets_data
                )

        if loadout.subclass:
            if previous_subclass is None:
                raise TransferError("配装预检", "找不到目标角色当前子职业配置。")
            if (
                loadout.subclass.subclass_item_hash
                and previous_subclass.subclass_item_hash
                != loadout.subclass.subclass_item_hash
            ):
                raise TransferError(
                    "配装预检",
                    "确认后目标角色切换了子职业，请重新生成并确认配装。",
                )
            if (
                loadout.subclass.subclass_instance_id
                and previous_subclass.subclass_instance_id
                != loadout.subclass.subclass_instance_id
            ):
                raise TransferError(
                    "配装预检",
                    "确认后的子职业实例已变化，请重新生成并确认配装。",
                )

        all_items = parse_items_from_profile(profile, self._manifest)
        items_by_id = {item.item_instance_id: item for item in all_items}
        target_states: dict[str, LoadoutItem] = {}
        for expected in loadout.items:
            current = items_by_id.get(expected.item_instance_id)
            if current is None:
                raise ItemNotFoundError(expected.item_instance_id)
            if current.item_hash != expected.item_hash:
                raise TransferError(
                    "配装预检",
                    f"实例 {expected.item_instance_id} 的物品 Hash 已变化。",
                )
            mod_sockets = self.read_armor_mod_sockets(
                current.item_instance_id, current.item_hash, sockets_data
            )
            target_states[expected.item_instance_id] = LoadoutItem(
                item_hash=current.item_hash,
                name=current.name,
                slot=expected.slot,
                item_instance_id=current.item_instance_id,
                mods=list(mod_sockets.values()),
                mod_sockets=mod_sockets,
                source_location=current.location,
                source_character_id=current.character_id,
                was_equipped=current.item_instance_id in equipped_ids,
            )

        return {
            "membership_id": mid,
            "membership_type": mtype,
            "character_id": char_id,
            "previous_loadout": Loadout(
                id="recovery",
                name="执行前配装",
                character=loadout.character,
                items=previous_items,
                subclass=previous_subclass,
            ),
            "target_states": target_states,
        }

    async def _verify_loadout(self, player_name: str, loadout: Loadout) -> bool:
        """Verify exact equipped instances and requested socket plugs."""
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(
            mid, mtype, loadout.character
        )
        profile = await self._resolver.get_profile(mid, mtype, [205, 305])
        equipped = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )
        equipped_ids = {
            str(item.get("itemInstanceId", "")) for item in equipped
        }
        if any(item.item_instance_id not in equipped_ids for item in loadout.items):
            return False

        sockets_data = (
            profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        for item in loadout.items:
            actual_sockets = sockets_data.get(item.item_instance_id, {}).get(
                "sockets", []
            )
            if any(
                socket_index >= len(actual_sockets)
                or actual_sockets[socket_index].get("plugHash", 0) != plug_hash
                for socket_index, plug_hash in item.mod_sockets.items()
            ):
                return False
            actual_plugs = {
                socket.get("plugHash", 0) for socket in actual_sockets
            }
            if any(mod_hash not in actual_plugs for mod_hash in item.mods):
                return False

        if loadout.subclass:
            subclass_item = next(
                (
                    raw
                    for raw in equipped
                    if (self._manifest.get_item_info(raw.get("itemHash", 0)) or {}).get(
                        "itemType"
                    )
                    == 16
                ),
                None,
            )
            if not subclass_item:
                return False
            subclass_id = str(subclass_item.get("itemInstanceId", ""))
            if (
                loadout.subclass.subclass_item_hash
                and subclass_item.get("itemHash", 0)
                != loadout.subclass.subclass_item_hash
            ) or (
                loadout.subclass.subclass_instance_id
                and subclass_id != loadout.subclass.subclass_instance_id
            ):
                return False
            subclass_sockets = sockets_data.get(subclass_id, {}).get("sockets", [])
            if any(
                socket_index >= len(subclass_sockets)
                or subclass_sockets[socket_index].get("plugHash", 0) != plug_hash
                for socket_index, plug_hash in loadout.subclass.plug_sockets.items()
            ):
                return False
            actual_plugs = {
                socket.get("plugHash", 0)
                for socket in subclass_sockets
            }
            expected_plugs = {
                loadout.subclass.super_hash,
                loadout.subclass.grenade_hash,
                loadout.subclass.melee_hash,
                loadout.subclass.class_ability_hash,
                loadout.subclass.movement_hash,
                *loadout.subclass.aspect_hashes,
                *loadout.subclass.fragment_hashes,
            }
            expected_plugs.discard(0)
            if not expected_plugs.issubset(actual_plugs):
                return False

        return True

    async def _restore_exact_state(
        self,
        player_name: str,
        attempted: Loadout,
        recovery: dict,
        steps: list[MoveItemStep],
    ) -> bool:
        """Best-effort rollback of sockets, equipped gear, and item locations."""
        all_ok = True
        target_states: dict[str, LoadoutItem] = recovery["target_states"]
        char_id = recovery["character_id"]
        mid = recovery["membership_id"]
        mtype = recovery["membership_type"]

        current_profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 300]
        )
        current_items = {
            item.item_instance_id: item
            for item in parse_items_from_profile(current_profile, self._manifest)
        }

        sockets_cache: dict = {}
        for original in target_states.values():
            current = current_items.get(original.item_instance_id)
            if (
                current is not None
                and current.location != attempted.character
                and original.source_location != attempted.character
            ):
                continue
            operations = (
                [(mod_hash, socket_index) for socket_index, mod_hash in sorted(original.mod_sockets.items())]
                if original.mod_sockets
                else [(mod_hash, None) for mod_hash in original.mods]
            )
            for mod_hash, exact_socket_index in operations:
                try:
                    socket_idx = (
                        exact_socket_index
                        if exact_socket_index is not None
                        else await self._find_mod_socket(
                            original.item_instance_id,
                            original.item_hash,
                            mod_hash,
                            mid,
                            mtype,
                            sockets_cache,
                        )
                    )
                    if socket_idx is None:
                        raise TransferError("恢复模组", f"找不到模组 {mod_hash} 的插槽。")
                    response = await self._insert_armor_mod(
                        original.item_instance_id,
                        mod_hash,
                        socket_idx,
                        char_id,
                        mtype,
                    )
                    ok = response.get("ErrorCode", 0) == 1
                    steps.append(MoveItemStep(
                        action="rollback_mod",
                        detail=f"恢复 '{original.name}' 的模组 {mod_hash}",
                        success=ok,
                    ))
                    all_ok = all_ok and ok
                except (aiobungie.HTTPError, ItemNotFoundError, TransferError) as exc:
                    logger.error("Failed to restore mod %s on %s: %s", mod_hash, original.name, exc)
                    steps.append(MoveItemStep(
                        action="rollback_mod", detail=str(exc), success=False
                    ))
                    all_ok = False

        previous: Loadout = recovery["previous_loadout"]
        try:
            previous_result = await self._equip_local_unlocked(player_name, previous)
        except (DestinyMCPError, aiobungie.HTTPError, OSError) as exc:
            logger.error("Failed to restore previous loadout: %s", exc)
            previous_result = LoadoutOperationResult(
                success=False,
                loadout_name=previous.name,
                message=str(exc),
                steps=[MoveItemStep(
                    action="error", detail=f"恢复执行前配装失败：{exc}", success=False
                )],
            )
        steps.extend(
            MoveItemStep(
                action=f"rollback_{step.action}",
                detail=step.detail,
                success=step.success,
            )
            for step in previous_result.steps
        )
        all_ok = all_ok and previous_result.success

        for original in target_states.values():
            destination = original.source_location
            if destination not in {"vault", "hunter", "warlock", "titan"}:
                steps.append(MoveItemStep(
                    action="rollback_location",
                    detail=f"无法确定 '{original.name}' 的原始位置。",
                    success=False,
                ))
                all_ok = False
                continue
            if destination == attempted.character:
                continue
            try:
                moved = await self._transfer.transfer_item(
                    player_name,
                    original.item_instance_id,
                    destination,
                    to_character_id=(
                        original.source_character_id or None
                    ),
                )
                steps.append(MoveItemStep(
                    action="rollback_location",
                    detail=f"恢复 '{original.name}' 到 {destination}",
                    success=moved.success,
                ))
                all_ok = all_ok and moved.success
                if moved.success and original.was_equipped and destination != "vault":
                    if original.source_character_id:
                        response = await self._bungie.equip_item(
                            original.item_instance_id,
                            original.source_character_id,
                            mtype,
                        )
                        equipped_ok = response.get("ErrorCode", 0) == 1
                    else:
                        equipped = await self._transfer.equip_item(
                            player_name,
                            original.item_instance_id,
                            destination,
                        )
                        equipped_ok = equipped.success
                    steps.append(MoveItemStep(
                        action="rollback_equip",
                        detail=f"重新装备 '{original.name}' 到 {destination}",
                        success=equipped_ok,
                    ))
                    all_ok = all_ok and equipped_ok
            except (ItemNotFoundError, TransferError) as exc:
                logger.error("Failed to restore location for %s: %s", original.name, exc)
                steps.append(MoveItemStep(
                    action="rollback_location", detail=str(exc), success=False
                ))
                all_ok = False

        if all_ok:
            previous_ok = await self._verify_loadout(player_name, previous)
            target_items_ok = await self._verify_restored_items(
                player_name, target_states
            )
            all_ok = previous_ok and target_items_ok
        steps.append(MoveItemStep(
            action="rollback_verify",
            detail="执行前状态恢复验证完成。" if all_ok else "执行前状态恢复验证失败。",
            success=all_ok,
        ))
        return all_ok

    async def _verify_restored_items(
        self,
        player_name: str,
        target_states: dict[str, LoadoutItem],
    ) -> bool:
        """Verify candidate items returned to their original locations and sockets."""
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 300, 305]
        )
        current_items = {
            item.item_instance_id: item
            for item in parse_items_from_profile(profile, self._manifest)
        }
        equipped_ids = {
            str(raw.get("itemInstanceId", ""))
            for equipment in (
                profile.get("characterEquipment", {}).get("data", {}).values()
            )
            for raw in equipment.get("items", [])
        }
        sockets_data = (
            profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        for original in target_states.values():
            current = current_items.get(original.item_instance_id)
            if current is None or current.location != original.source_location:
                return False
            if original.was_equipped and original.item_instance_id not in equipped_ids:
                return False
            actual_sockets = sockets_data.get(original.item_instance_id, {}).get(
                "sockets", []
            )
            if any(
                socket_index >= len(actual_sockets)
                or actual_sockets[socket_index].get("plugHash", 0) != plug_hash
                for socket_index, plug_hash in original.mod_sockets.items()
            ):
                return False
        return True

    # ── Private helpers ──────────────────────────────────────────────

    def _plug_energy_cost(self, plug_hash: int) -> int | None:
        """Return a plug's manifest energy cost, or None if it is unknown."""
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            return None
        raw_cost = ((definition.get("plug") or {}).get("energyCost") or {}).get(
            "energyCost", 0
        )
        try:
            return max(0, int(raw_cost or 0))
        except (TypeError, ValueError):
            return None

    async def _prepare_mod_operations(
        self,
        item: LoadoutItem,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict[str, list[dict]],
        instances_data: dict,
    ) -> list[tuple[str, int, int]]:
        """Order minimal energy-clearing writes before requested mod writes."""
        sockets = sockets_cache.get(item.item_instance_id, [])
        requested = (
            [
                (mod_hash, socket_index)
                for socket_index, mod_hash in sorted(item.mod_sockets.items())
            ]
            if item.mod_sockets
            else [(mod_hash, None) for mod_hash in item.mods]
        )

        assigned_sockets: set[int] = set()
        target_operations: list[tuple[int, int, int]] = []
        for mod_hash, exact_socket_index in requested:
            socket_index = (
                exact_socket_index
                if exact_socket_index is not None
                else await self._find_mod_socket(
                    item.item_instance_id,
                    item.item_hash,
                    mod_hash,
                    membership_id,
                    membership_type,
                    sockets_cache,
                    excluded_socket_indices=assigned_sockets,
                )
            )
            if (
                socket_index is None
                or socket_index in assigned_sockets
                or socket_index >= len(sockets)
            ):
                raise TransferError(
                    "模组预检",
                    f"找不到模组 {mod_hash} 在 '{item.name}' 上的唯一兼容插槽。",
                )

            target_cost = self._plug_energy_cost(mod_hash)
            current_hash = sockets[socket_index].get("plugHash", 0)
            current_cost = self._plug_energy_cost(current_hash) if current_hash else 0
            if target_cost is None or current_cost is None:
                raise TransferError(
                    "模组预检",
                    f"无法从 Manifest 计算 '{item.name}' 的模组能量。",
                )

            assigned_sockets.add(socket_index)
            target_operations.append(
                (target_cost - current_cost, mod_hash, socket_index)
            )

        energy = instances_data.get(item.item_instance_id, {}).get("energy") or {}
        capacity = energy.get("energyCapacity")
        current_used = energy.get("energyUsed")
        if not isinstance(capacity, int) or capacity < 0:
            raise TransferError(
                "模组预检",
                f"无法读取 '{item.name}' 的能量上限。",
            )
        if not isinstance(current_used, int) or current_used < 0:
            costs = [
                self._plug_energy_cost(socket.get("plugHash", 0))
                for socket in sockets
                if socket.get("plugHash", 0)
            ]
            if any(cost is None for cost in costs):
                raise TransferError(
                    "模组预检",
                    f"无法读取 '{item.name}' 的已用能量。",
                )
            current_used = sum(cost for cost in costs if cost is not None)

        projected_used = current_used + sum(
            delta for delta, _, _ in target_operations
        )
        deficit = max(0, projected_used - capacity)
        clear_operations: list[tuple[str, int, int]] = []
        if deficit:
            item_definition = self._manifest.get_item_definition(item.item_hash)
            socket_entries = (
                (item_definition.get("sockets") or {}).get("socketEntries", [])
                if isinstance(item_definition, dict)
                else []
            )
            candidates: list[tuple[int, int, int]] = []
            for socket_index, socket in enumerate(sockets):
                if (
                    socket_index in assigned_sockets
                    or socket_index >= len(socket_entries)
                ):
                    continue
                current_hash = socket.get("plugHash", 0)
                default_hash = socket_entries[socket_index].get(
                    "singleInitialItemHash", 0
                )
                if not current_hash or not default_hash or current_hash == default_hash:
                    continue
                current_category = self._plug_category_hash(current_hash)
                default_category = self._plug_category_hash(default_hash)
                if current_category == self._PLUG_CAT_TUNING:
                    continue
                if (
                    current_category not in self._MOD_CATEGORY_HASHES
                    and default_category not in self._MOD_CATEGORY_HASHES
                ):
                    continue

                current_cost = self._plug_energy_cost(current_hash)
                default_cost = self._plug_energy_cost(default_hash)
                if current_cost is None or default_cost is None:
                    raise TransferError(
                        "模组预检",
                        f"无法计算 '{item.name}' 插槽 {socket_index} 的能量。",
                    )
                freed = current_cost - default_cost
                if freed > 0:
                    candidates.append((freed, socket_index, default_hash))

            if sum(freed for freed, _, _ in candidates) < deficit:
                raise TransferError(
                    "模组预检",
                    f"'{item.name}' 没有足够的可替换模组来腾出 {deficit} 点能量。",
                )

            remaining = sorted(candidates)
            while deficit > 0:
                sufficient = [
                    candidate for candidate in remaining if candidate[0] >= deficit
                ]
                chosen = min(sufficient) if sufficient else remaining[0]
                remaining.remove(chosen)
                freed, socket_index, default_hash = chosen
                clear_operations.append(("clear", default_hash, socket_index))
                deficit -= freed

        return [
            *clear_operations,
            *[
                ("mod", mod_hash, socket_index)
                for _, mod_hash, socket_index in sorted(target_operations)
            ],
        ]

    async def _find_mod_socket(
        self,
        item_instance_id: str,
        item_hash: int,
        mod_hash: int,
        membership_id: str,
        membership_type: int,
        sockets_cache: dict | None = None,
        excluded_socket_indices: set[int] | None = None,
    ) -> int | None:
        """Find the socket index where a mod should be inserted."""
        excluded_socket_indices = excluded_socket_indices or set()
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

        mod_category = self._plug_category_hash(mod_hash)
        if mod_category not in self._MOD_CATEGORY_HASHES:
            return None

        item_definition = self._manifest.get_item_definition(item_hash)
        if isinstance(item_definition, dict):
            socket_entries = (item_definition.get("sockets") or {}).get(
                "socketEntries", []
            )
            for index, entry in enumerate(socket_entries):
                if index >= len(sockets_data) or index in excluded_socket_indices:
                    continue
                if sockets_data[index].get("plugHash", 0) == mod_hash:
                    return index
                for plug_set_hash in {
                    entry.get("reusablePlugSetHash", 0),
                    entry.get("randomizedPlugSetHash", 0),
                }:
                    if not plug_set_hash:
                        continue
                    plug_set = self._manifest.get_definition(
                        "DestinyPlugSetDefinition", plug_set_hash
                    )
                    if isinstance(plug_set, dict) and any(
                        item.get("plugItemHash", 0) == mod_hash
                        for item in plug_set.get("reusablePlugItems", [])
                    ):
                        return index

        # Pass 1: find socket with matching category that doesn't already have this exact mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
                continue
            if plug_hash:
                current_category = self._plug_category_hash(plug_hash)
                if current_category == mod_category:
                    return i

        # Pass 2: already has this mod
        for i, socket in enumerate(sockets_data):
            if i in excluded_socket_indices:
                continue
            plug_hash = socket.get("plugHash", 0)
            if plug_hash == mod_hash:
                return i

        return None

    async def _insert_armor_mod(
        self,
        item_instance_id: str,
        mod_hash: int,
        socket_index: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Use Bungie's paid or free socket endpoint from manifest energy cost."""
        energy_cost = self._plug_energy_cost(mod_hash) or 0
        insert = (
            self._bungie.insert_socket_plug
            if energy_cost > 0
            else self._bungie.insert_socket_plug_free
        )
        return await insert(
            item_instance_id,
            mod_hash,
            socket_index,
            0,
            character_id,
            membership_type,
        )

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
        subclass_item_hash = 0
        for raw_item in equip_data:
            item_hash = raw_item.get("itemHash", 0)
            item_info = self._manifest.get_item_info(item_hash) or {}
            if item_info.get("itemType") == 16:
                subclass_inst_id = str(raw_item.get("itemInstanceId", ""))
                subclass_item_hash = item_hash
                break

        if not subclass_inst_id:
            steps.append(MoveItemStep(
                action="error", detail="找不到已装备的子职业", success=False,
            ))
            return False

        if (
            subclass.subclass_item_hash
            and subclass.subclass_item_hash != subclass_item_hash
        ) or (
            subclass.subclass_instance_id
            and subclass.subclass_instance_id != subclass_inst_id
        ):
            steps.append(MoveItemStep(
                action="subclass",
                detail="当前子职业与保存/确认的子职业不一致。",
                success=False,
            ))
            return False

        all_plugs: list[tuple[str, int, int | None]] = []
        if subclass.plug_sockets:
            all_plugs.extend(
                ("plug", plug_hash, socket_index)
                for socket_index, plug_hash in sorted(subclass.plug_sockets.items())
            )
        else:
            if subclass.super_hash:
                all_plugs.append(("super", subclass.super_hash, None))
            if subclass.grenade_hash:
                all_plugs.append(("grenade", subclass.grenade_hash, None))
            if subclass.melee_hash:
                all_plugs.append(("melee", subclass.melee_hash, None))
            if subclass.class_ability_hash:
                all_plugs.append(("class_ability", subclass.class_ability_hash, None))
            if subclass.movement_hash:
                all_plugs.append(("movement", subclass.movement_hash, None))
            all_plugs.extend(("aspect", h, None) for h in subclass.aspect_hashes)
            all_plugs.extend(("fragment", h, None) for h in subclass.fragment_hashes)

        assigned_sockets: set[int] = set()
        for plug_type, plug_hash, exact_socket_index in all_plugs:
            try:
                socket_idx = (
                    exact_socket_index
                    if exact_socket_index is not None
                    else await self._find_subclass_socket(
                        subclass_inst_id,
                        subclass_item_hash,
                        plug_hash,
                        plug_type,
                        sockets_map,
                        assigned_sockets,
                    )
                )
                if socket_idx is not None and socket_idx >= len(
                    sockets_map.get(subclass_inst_id, {}).get("sockets", [])
                ):
                    socket_idx = None
                if socket_idx is None:
                    steps.append(MoveItemStep(
                        action="subclass",
                        detail=f"找不到 {plug_type} {plug_hash} 的兼容插槽",
                        success=False,
                    ))
                    all_ok = False
                    continue
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
        subclass_item_hash: int,
        plug_hash: int,
        plug_type: str,
        sockets_map: dict,
        assigned_sockets: set[int] | None = None,
    ) -> int | None:
        """Find a compatible subclass socket from plug sets, then categories."""
        sockets = sockets_map.get(subclass_inst_id, {}).get("sockets", [])
        if assigned_sockets is None:
            assigned_sockets = set()

        subclass_definition = self._manifest.get_item_definition(
            subclass_item_hash
        ) or {}
        socket_entries = (subclass_definition.get("sockets") or {}).get(
            "socketEntries", []
        )
        for index, entry in enumerate(socket_entries):
            if index >= len(sockets) or index in assigned_sockets:
                continue
            plug_set_hashes = {
                entry.get("reusablePlugSetHash", 0),
                entry.get("randomizedPlugSetHash", 0),
            }
            if entry.get("singleInitialItemHash", 0) == plug_hash:
                return index
            for plug_set_hash in plug_set_hashes:
                if not plug_set_hash:
                    continue
                plug_set = self._manifest.get_definition(
                    "DestinyPlugSetDefinition", plug_set_hash
                ) or {}
                if any(
                    item.get("plugItemHash", 0) == plug_hash
                    for item in plug_set.get("reusablePlugItems", [])
                ):
                    return index

        desired_category = self._plug_category_hash(plug_hash)
        category_identifier = (
            self._manifest.get_plug_category_identifier(plug_hash) or ""
        )
        desired_type = (
            identify_socket_type(category_identifier)
            if category_identifier
            else plug_type
        )
        for index, socket in enumerate(sockets):
            if index in assigned_sockets:
                continue
            current_hash = socket.get("plugHash", 0)
            if current_hash == plug_hash:
                return index
            current_identifier = (
                self._manifest.get_plug_category_identifier(current_hash) or ""
            )
            if current_identifier:
                current_type = identify_socket_type(current_identifier)
                if current_type == desired_type:
                    return index
                continue
            if (
                desired_category
                and self._plug_category_hash(current_hash) == desired_category
            ):
                return index

        return None
