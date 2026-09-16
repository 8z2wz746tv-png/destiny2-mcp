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
from . import profile_components
from ..manifest import ManifestManager
from ..models import (
    Loadout,
    LoadoutOperationResult,
    MoveItemStep,
)
from ..player_resolver import PlayerResolver
from ..services.transfer_service import TransferService
from .account_action_lock import account_action_lock
from .loadout_mod_sockets import ModSocketMixin
from .loadout_recovery import RecoveryStateMixin
from .loadout_subclass_sockets import SubclassSocketMixin

logger = get_logger(__name__)

_CANCEL_ROLLBACK_TIMEOUT_SECONDS = 60

def _mod_write_needs_in_game(result: dict) -> bool:
    """模组写入是不是被 Bungie 的策略挡了（而不是我们写错）。

    真机实测两种：
    - 403 `Access not permitted by application scope`：装护甲模组要 `AdvancedWriteActions`，
      本应用没有这个 scope；
    - 500 `This action can only be done in-game.`：卸/换模组只能在游戏里做。

    这两种都不是"重试能成"的错误，也不该把已经换好的装备回滚掉 —— 如实告诉用户
    "这几颗模组请在游戏里装"才是对的。
    """
    text = f"{result.get('Message', '')} {result.get('ErrorStatus', '')}"
    return (
        "Access not permitted by application scope" in text
        or "can only be done in-game" in text
        or result.get("ErrorCode") in (403, 500) and "in-game" in text
    )


class LoadoutEquipmentService(RecoveryStateMixin, ModSocketMixin, SubclassSocketMixin):
    """Apply loadout equipment: transfer, equip, mods, subclass config."""

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
        profile = await self._resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
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
                # 失败要说清上游为什么（以前只写"批量装备 N 件物品"，原因被丢掉，
                # 真机排查时只能靠猜）。
                steps.append(MoveItemStep(
                    action="equip_many",
                    detail=(
                        f"批量装备 {len(transferred_ids)} 件物品"
                        if equipped
                        else f"批量装备失败：{equip_result.get('message') or '上游没给原因'}"
                    ),
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

        manual_mods: list[tuple[str, int, int]] = []
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
                    upstream = str(mod_result.get("Message") or "").strip()
                    steps.append(MoveItemStep(
                        action="mod_clear" if operation == "clear" else "mod",
                        detail=(
                            f"为属性模组腾出能量：'{lo_item.name}' 插槽 {socket_idx}"
                            if operation == "clear"
                            else f"模组 {mod_hash} → '{lo_item.name}'"
                        )
                        # 失败要带上游原文（以前只写"为…腾出能量"，真因看不到）
                        + ("" if ok else f" 失败：{upstream or '上游没给原因'}"),
                        success=ok,
                    ))
                    if not ok:
                        if _mod_write_needs_in_game(mod_result):
                            # 策略限制：记下来，继续走完剩下的模组，最后如实汇报。
                            manual_mods.append((lo_item.name, mod_hash, socket_idx))
                            continue
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

        if manual_mods and all_ok:
            names = "、".join(f"'{name}' 上的模组 {mod_hash}" for name, mod_hash, _ in manual_mods)
            steps.append(MoveItemStep(
                action="mod_in_game",
                detail=(
                    f"这些模组 Bungie 不允许通过 API 装（403/仅游戏内），请在游戏里手动装：{names}"
                ),
                success=False,
            ))
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=(
                    f"配装 '{loadout.name}' 的装备已经换上；但 {len(manual_mods)} 颗模组需要你"
                    "在游戏里手动装（Bungie 不允许 API 改护甲模组），装完六维就是求解器给的那套。"
                ),
                steps=steps,
            )

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

        # 模组被上游策略挡住时 equipment 是好的：不回滚，交给上层如实汇报。
        if applied.success and verified or any(
            st.action == "mod_in_game" for st in applied.steps
        ):
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

    async def _verify_loadout(self, player_name: str, loadout: Loadout) -> bool:
        """Verify exact equipped instances and requested socket plugs."""
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(
            mid, mtype, loadout.character
        )
        profile = await self._resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
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
