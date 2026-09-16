"""配装的"执行前状态"：抓取快照 + 失败时恢复回去。

以 mixin 挂在 `LoadoutEquipmentService` 上（`self` 上就有 manifest/bungie/resolver）。
搬出 `loadout_equipment_service.py` 的原因：那个文件贴着体量上限，而“抓取/恢复”
与“执行”本来就是两件事。
"""

from __future__ import annotations



import aiobungie

from ..exceptions import DestinyMCPError, ItemNotFoundError, TransferError
from ..logging_config import get_logger
from ..models import (
    Loadout,
    LoadoutItem,
    LoadoutOperationResult,
    LoadoutSubclassConfig,
    MoveItemStep,
)
from . import profile_components
from .item_parser import armor_slot_from_bucket, parse_items_from_profile

logger = get_logger(__name__)

_CANCEL_ROLLBACK_TIMEOUT_SECONDS = 60



class RecoveryStateMixin:
    async def _capture_recovery_state(self, player_name: str, loadout: Loadout) -> dict:
        """Capture equipped gear plus original state of every target item."""
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        profile = await self._resolver.get_profile(
            mid, mtype, profile_components.ARMOR_SNAPSHOT
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
            slot = armor_slot_from_bucket(raw.get("bucketHash", 0))
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
            mid, mtype, profile_components.INVENTORY_MINIMAL
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
            mid, mtype, profile_components.INVENTORY_SOCKETS
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

