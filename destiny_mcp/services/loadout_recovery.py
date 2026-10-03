"""配装的"执行前状态"：抓取快照 + 失败时恢复回去。

以 mixin 挂在 `LoadoutEquipmentService` 上（`self` 上就有 manifest/bungie/resolver）。
搬出 `loadout_equipment_service.py` 的原因：那个文件贴着体量上限，而“抓取/恢复”
与“执行”本来就是两件事。
"""

from __future__ import annotations



import aiobungie

from ..exceptions import (
    DestinyMCPError,
    ItemNotFoundError,
    TransferError,
    describe_exception,
)
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
from .loadout_armor_state import socket_diffs
from .loadout_mod_sockets import plug_already_installed
from .loadout_restore_locations import restore_locations
from . import loadout_verify

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
        sockets_data = (
            current_profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        for original in target_states.values():
            current = current_items.get(original.item_instance_id)
            if (
                current is not None
                and current.location != attempted.character
                and original.source_location != attempted.character
            ):
                continue
            # **先比对再写**：口径的唯一出处是 loadout_armor_state.socket_diffs（与穿快照那条
            # 路的 _restore_one 同一份判据）。这个槽已经装着记录里那颗时一个写入都不发 ——
            # 上游对再装一次回 HTTP 500 + 1679，客户端还会退避重试，真机实测每颗白花约 10 秒
            # （2026-10-03 复测），而回滚要恢复的常常本来就是没被改动过的那些格。
            if original.mod_sockets:
                diffs = socket_diffs(
                    self.read_armor_mod_sockets(
                        original.item_instance_id, original.item_hash, sockets_data
                    ),
                    original,
                )
                if not diffs:
                    steps.append(MoveItemStep(
                        action="rollback_mod",
                        detail=f"'{original.name}' 的模组与执行前一致，未改动",
                        success=True,
                    ))
                    continue
                operations = [(plug_hash, index) for index, plug_hash in diffs]
            else:
                # 老记录只存了 mods（没有逐槽现场）：仍然逐颗找槽，没有"已装着"的便宜可捡。
                operations = [(mod_hash, None) for mod_hash in original.mods]
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
                    # 1679「这个槽已经装着它」= 执行前状态已经成立，不算失败。
                    # 真机 2026-09-23：这里漏了这条判据 → 26 条"恢复模组"全被记成失败 →
                    # all_ok=False → 连验证都没跑就报"自动恢复不完整"，而账号其实是好的。
                    already = plug_already_installed(response)
                    ok = response.get("ErrorCode", 0) == 1 or already
                    steps.append(MoveItemStep(
                        action="rollback_mod",
                        detail=(
                            f"恢复 '{original.name}' 的模组 {mod_hash}：已经装着，未改动"
                            if already
                            else f"恢复 '{original.name}' 的模组 {mod_hash}"
                        ),
                        success=ok,
                    ))
                    all_ok = all_ok and ok
                except (aiobungie.HTTPError, ItemNotFoundError, TransferError) as exc:
                    logger.error("Failed to restore mod %s on %s: %s", mod_hash, original.name, exc)
                    steps.append(MoveItemStep(
                        action="rollback_mod", detail=describe_exception(exc), success=False
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
                message=describe_exception(exc),
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

        # 位置那一半搬去 loadout_restore_locations："这一件上装着什么"与"这一件在哪"本就是两件事
        all_ok = await restore_locations(
            self, player_name, attempted.character, target_states, steps
        ) and all_ok

        if all_ok:
            previous_ok = await loadout_verify.verify_loadout(self, player_name, previous)
            target_items_ok = await loadout_verify.verify_restored_items(
                self, player_name, target_states
            )
            all_ok = previous_ok and target_items_ok
        steps.append(MoveItemStep(
            action="rollback_verify",
            detail="执行前状态恢复验证完成。" if all_ok else "执行前状态恢复验证失败。",
            success=all_ok,
        ))
        return all_ok


    # ── Private helpers ──────────────────────────────────────────────

