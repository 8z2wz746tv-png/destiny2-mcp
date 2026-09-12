"""Transfer service — item movement between characters and vault.

Extracted from server.py per Rule 1: tools should not contain business logic.
This is the most complex service because Destiny 2 does not support direct
character-to-character transfers (must go through vault).
"""

from __future__ import annotations

import asyncio

from ..bungie_client import BungieClient
from ..exceptions import AuthenticationError, ItemNotFoundError, TransferError
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import (
    EquipResult,
    InventoryItem,
    ItemCandidate,
    MoveItemResult,
    MoveItemStep,
    TransferResult,
)
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from ..utils.item_parser import parse_items_from_profile
from .account_action_lock import account_action_lock, serialized_account_action
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
    require_complete_inventory_components,
)

logger = get_logger(__name__)

_TRANSFER_HOP_DELAY_SECONDS = 0.75


class TransferService:
    """Operations for transferring and equipping items."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._account_action_lock = account_action_lock(bungie)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _fetch_all_items(
        self, membership_id: str, membership_type: int
    ) -> list[InventoryItem]:
        """Fetch all items across the account."""
        profile = await self._resolver.get_profile(
            membership_id, membership_type, profile_components.INVENTORY
        )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        require_complete_inventory_components(profile)
        return parse_items_from_profile(profile, self._manifest)

    async def _find_item(
        self,
        membership_id: str,
        membership_type: int,
        item_instance_id: str,
    ) -> InventoryItem:
        """Find a specific item by instance ID. Raises ItemNotFoundError."""
        all_items = await self._fetch_all_items(membership_id, membership_type)
        found = next(
            (it for it in all_items if it.item_instance_id == item_instance_id),
            None,
        )
        if not found:
            raise ItemNotFoundError(
                item_instance_id,
                "It may have been moved or dismantled.",
            )
        return found

    async def _resolve_action_character(
        self,
        membership_id: str,
        membership_type: int,
        character: str | None = None,
        item: InventoryItem | None = None,
        character_id: str | None = None,
    ) -> tuple[str, str]:
        """Resolve a character id for item action endpoints."""
        if character_id:
            profile = await self._resolver.get_profile(membership_id, membership_type, [200])
            char_data = (
                profile.get("characters", {})
                .get("data", {})
                .get(character_id)
            )
            if not char_data:
                raise TransferError(
                    "Resolve character",
                    f"Target character ID {character_id} no longer exists.",
                )
            return character_id, class_type_name(char_data.get("classType", -1))

        if character:
            class_type = resolve_character_name(character)
            char_id = await self._resolver.resolve_character_id(
                membership_id,
                membership_type,
                character,
            )
            return char_id, class_type_name(class_type)

        if item and item.character_id:
            profile = await self._resolver.get_profile(membership_id, membership_type, [200])
            char_data = profile.get("characters", {}).get("data", {}).get(item.character_id, {})
            return item.character_id, class_type_name(char_data.get("classType", -1))

        profile = await self._resolver.get_profile(membership_id, membership_type, [200])
        chars = profile.get("characters", {}).get("data", {})
        if not chars:
            raise TransferError("Resolve character", "账号下没有可用角色。")
        char_id, char_data = next(iter(chars.items()))
        return char_id, class_type_name(char_data.get("classType", -1))

    # ── Transfer ─────────────────────────────────────────────────────

    @serialized_account_action
    async def transfer_item(
        self,
        player_name: str,
        item_instance_id: str,
        to_character: str,
        from_character: str | None = None,
        to_character_id: str | None = None,
    ) -> TransferResult:
        """Transfer a single item between characters or vault.

        Handles three cases:
        1. Vault → Character (direct)
        2. Character → Vault (direct)
        3. Character → Character (via vault, two API calls)
        4. Already at destination (no-op)

        Raises:
            PlayerNotFoundError, ItemNotFoundError, CharacterNotFoundError,
            TransferError.
        """
        logger.info(
            "Transfer: item=%s to=%s from=%s (player=%s)",
            item_instance_id, to_character, from_character, player_name,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        found = await self._find_item(mid, mtype, item_instance_id)
        item_name = found.name
        item_hash = found.item_hash

        # Validate source
        if from_character:
            expected = resolve_character_name(from_character)
            expected_name = class_type_name(expected).lower()
            if found.location != expected_name and found.location != "vault":
                raise TransferError(
                    "Transfer",
                    f"Item '{item_name}' is on {found.location}, not {from_character}.",
                )

        to_vault = to_character.strip().lower() in ("vault", "仓库")
        target_info = None
        validated_target_character_id = ""
        if to_character_id:
            profile = await self._resolver.get_profile(mid, mtype, [200])
            target_info = (
                profile.get("characters", {})
                .get("data", {})
                .get(to_character_id)
            )
            if not target_info:
                raise TransferError(
                    "Transfer",
                    f"Target character ID {to_character_id} no longer exists.",
                )
            validated_target_character_id = to_character_id

        if not to_vault:
            if target_info is not None:
                target_class = target_info.get("classType", -1)
                target_char_id = validated_target_character_id
            else:
                target_class = resolve_character_name(to_character)
                target_char_id = await self._resolver.resolve_character_id(
                    mid, mtype, to_character
                )
        else:
            target_class = -1
            target_char_id = ""

        # Case 1: Vault → Character
        if found.location == "vault" and not to_vault:
            result = await self._bungie.transfer_item(
                item_instance_id, item_hash,
                character_id=target_char_id,
                membership_type=mtype,
                to_vault=False,
            )
            self._check_result(result, "Transfer vault→character")
            logger.info("Transferred '%s' vault→%s", item_name, class_type_name(target_class))
            return TransferResult(
                success=True,
                item_name=item_name,
                item_instance_id=item_instance_id,
                from_location="vault",
                to_location=class_type_name(target_class),
                message=f"Moved '{item_name}' from vault to {class_type_name(target_class)}.",
            )

        # Case 2: Character → Vault
        if found.location != "vault" and to_vault:
            result = await self._bungie.transfer_item(
                item_instance_id, item_hash,
                character_id=found.character_id,
                membership_type=mtype,
                to_vault=True,
            )
            self._check_result(result, "Transfer character→vault")
            logger.info("Transferred '%s' %s→vault", item_name, found.location)
            return TransferResult(
                success=True,
                item_name=item_name,
                item_instance_id=item_instance_id,
                from_location=found.location,
                to_location="vault",
                message=f"Moved '{item_name}' from {found.location} to vault.",
            )

        # Case 3: Character → Character (via vault)
        if found.location != "vault" and not to_vault:
            dest_name = class_type_name(target_class)

            # Already at same destination? No-op.
            already_at_destination = (
                found.character_id == target_char_id
                if to_character_id
                else found.location == dest_name.lower()
            )
            if already_at_destination:
                return TransferResult(
                    success=True,
                    item_name=item_name,
                    item_instance_id=item_instance_id,
                    from_location=found.location,
                    to_location=dest_name,
                    message=f"'{item_name}' is already at {dest_name}.",
                )

            # Step 1: to vault
            r1 = await self._bungie.transfer_item(
                item_instance_id, item_hash,
                character_id=found.character_id,
                membership_type=mtype,
                to_vault=True,
            )
            self._check_result(r1, "Transfer to vault (first hop)")

            await asyncio.sleep(_TRANSFER_HOP_DELAY_SECONDS)

            # Step 2: to destination
            second_hop_operation = f"Transfer vault→{dest_name} (second hop)"
            try:
                r2 = await self._bungie.transfer_item(
                    item_instance_id, item_hash,
                    character_id=target_char_id,
                    membership_type=mtype,
                    to_vault=False,
                )
                self._check_result(r2, second_hop_operation)
            except Exception as second_hop_error:
                logger.warning(
                    "%s failed for '%s'; attempting rollback to %s: %s",
                    second_hop_operation,
                    item_name,
                    found.location,
                    second_hop_error,
                )
                await asyncio.sleep(_TRANSFER_HOP_DELAY_SECONDS)

                rollback_error: Exception | None = None
                try:
                    rollback = await self._bungie.transfer_item(
                        item_instance_id, item_hash,
                        character_id=found.character_id,
                        membership_type=mtype,
                        to_vault=False,
                    )
                    self._check_result(rollback, f"Rollback vault→{found.location}")
                except Exception as error:
                    rollback_error = error
                    logger.error(
                        "Rollback failed for '%s'; refresh inventory to confirm its location: %s",
                        item_name,
                        rollback_error,
                    )

                if isinstance(second_hop_error, TransferError):
                    if rollback_error is None:
                        raise TransferError(
                            second_hop_operation,
                            f"Second hop failed: {second_hop_error} "
                            f"The item was rolled back to {found.location}.",
                        ) from second_hop_error
                    raise TransferError(
                        second_hop_operation,
                        f"Second hop failed: {second_hop_error} "
                        f"Rollback failed: {rollback_error}. The item remains in vault.",
                    ) from second_hop_error

                if rollback_error is not None:
                    second_hop_error.add_note(
                        f"Rollback failed: {rollback_error}. "
                        "Refresh inventory to confirm the item's location."
                    )
                raise

            logger.info("Transferred '%s' %s→%s (via vault)", item_name, found.location, dest_name)
            return TransferResult(
                success=True,
                item_name=item_name,
                item_instance_id=item_instance_id,
                from_location=found.location,
                to_location=dest_name,
                message=f"Moved '{item_name}' from {found.location} to {dest_name} (via vault).",
            )

        # Case 4: Already at destination
        dest_name = "vault" if to_vault else class_type_name(target_class)
        return TransferResult(
            success=True,
            item_name=item_name,
            item_instance_id=item_instance_id,
            from_location=found.location,
            to_location=dest_name,
            message=f"'{item_name}' is already at {dest_name}.",
        )

    # ── Equip ────────────────────────────────────────────────────────

    @serialized_account_action
    async def equip_item(
        self,
        player_name: str,
        item_instance_id: str,
        character: str,
        character_id: str | None = None,
    ) -> EquipResult:
        """Equip an item on a character.

        Raises:
            PlayerNotFoundError, ItemNotFoundError, CharacterNotFoundError,
            TransferError.
        """
        logger.info(
            "Equip: item=%s character=%s (player=%s)",
            item_instance_id, character, player_name,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id, char_name = await self._resolve_action_character(
            mid, mtype, character, character_id=character_id
        )

        # Find item name for the response
        try:
            found = await self._find_item(mid, mtype, item_instance_id)
            item_name = found.name
        except ItemNotFoundError:
            item_name = "Unknown"

        result = await self._bungie.equip_item(item_instance_id, char_id, mtype)
        self._check_result(result, "Equip")

        logger.info("Equipped '%s' on %s", item_name, char_name)
        return EquipResult(
            success=True,
            item_name=item_name,
            character=char_name,
            message=f"Equipped '{item_name}' on {char_name}.",
        )

    @serialized_account_action
    async def equip_items(
        self,
        player_name: str,
        item_instance_ids: list[str],
        character: str,
    ) -> dict:
        """Equip several items on one character through Bungie's batch endpoint."""
        logger.info(
            "EquipItems: items=%s character=%s (player=%s)",
            item_instance_ids,
            character,
            player_name,
        )
        if not item_instance_ids:
            return {"success": False, "message": "必须提供至少一个 item_instance_id。"}

        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        target_class = resolve_character_name(character)
        target_location = class_type_name(target_class).lower()
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)

        all_items = await self._fetch_all_items(mid, mtype)
        by_id = {it.item_instance_id: it for it in all_items}
        missing = [item_id for item_id in item_instance_ids if item_id not in by_id]
        wrong_location = [
            {
                "item_instance_id": item_id,
                "name": by_id[item_id].name,
                "location": by_id[item_id].location,
            }
            for item_id in item_instance_ids
            if item_id in by_id and by_id[item_id].location != target_location
        ]
        if missing or wrong_location:
            return {
                "success": False,
                "message": "批量装备要求物品已经在目标角色背包中。请先用 move_item 转移后再批量装备。",
                "missing_item_instance_ids": missing,
                "wrong_location": wrong_location,
            }

        result = await self._bungie.equip_items(item_instance_ids, char_id, mtype)
        response = result.get("Response")
        equip_results = response.get("equipResults") if isinstance(response, dict) else None
        statuses = {}
        if isinstance(equip_results, list):
            for entry in equip_results:
                if isinstance(entry, dict):
                    statuses.setdefault(str(entry.get("itemInstanceId", "")), []).append(
                        entry.get("equipStatus")
                    )
        item_results = [
            {
                "item_instance_id": item_id,
                "name": by_id[item_id].name,
                "equip_status": (
                    statuses[item_id][0] if len(statuses.get(item_id, [])) == 1 else None
                ),
                "success": statuses.get(item_id) == [1],
            }
            for item_id in item_instance_ids
        ]
        ok = result.get("ErrorCode", 0) == 1 and all(
            entry["success"] for entry in item_results
        )
        names = [by_id[item_id].name for item_id in item_instance_ids]
        failures = "; ".join(
            f"{entry['name']} (equipStatus={entry['equip_status']})"
            for entry in item_results if not entry["success"]
        )
        failure_message = (
            result.get("Message", "未知错误")
            if result.get("ErrorCode", 0) != 1
            else f"以下物品未成功装备或缺少有效结果：{failures}。请刷新装备确认状态。"
        )
        return {
            "success": ok,
            "items": names,
            "item_results": item_results,
            "character": class_type_name(target_class),
            "message": (
                f"已在 {class_type_name(target_class)} 上批量装备：{', '.join(names)}。"
                if ok
                else f"批量装备失败：{failure_message}"
            ),
        }

    @serialized_account_action
    async def pull_from_postmaster(
        self,
        player_name: str,
        item_instance_id: str,
        character: str | None = None,
        character_id: str | None = None,
    ) -> dict:
        """Pull a postmaster item into the owning or requested character inventory."""
        logger.info(
            "PullFromPostmaster: item=%s character=%s (player=%s)",
            item_instance_id,
            character,
            player_name,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        found = await self._find_item(mid, mtype, item_instance_id)

        if "lost" not in found.bucket_type.lower():
            return {
                "success": False,
                "item_name": found.name,
                "message": f"'{found.name}' 当前不在邮政官/Lost Items 中，不能用 PullFromPostmaster。",
            }

        char_id, char_name = await self._resolve_action_character(
            mid, mtype, character, found, character_id
        )
        result = await self._bungie.pull_from_postmaster(
            item_instance_id,
            found.item_hash,
            char_id,
            mtype,
            stack_size=found.quantity,
        )
        ok = result.get("ErrorCode", 0) == 1
        return {
            "success": ok,
            "item_name": found.name,
            "character": char_name,
            "message": (
                f"已从邮政官取回 '{found.name}' 到 {char_name}。"
                if ok
                else f"邮政官取回失败：{result.get('Message', '未知错误')}"
            ),
        }

    @serialized_account_action
    async def set_item_lock_state(
        self,
        player_name: str,
        item_instance_id: str,
        locked: bool,
        character: str | None = None,
    ) -> dict:
        """Lock or unlock one item instance."""
        logger.info(
            "SetItemLockState: item=%s locked=%s character=%s (player=%s)",
            item_instance_id,
            locked,
            character,
            player_name,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        found = await self._find_item(mid, mtype, item_instance_id)
        char_id, char_name = await self._resolve_action_character(mid, mtype, character, found)

        result = await self._bungie.set_item_lock_state(
            item_instance_id,
            char_id,
            mtype,
            state=locked,
        )
        ok = result.get("ErrorCode", 0) == 1
        return {
            "success": ok,
            "item_name": found.name,
            "locked": locked,
            "character": char_name,
            "message": (
                f"已{'锁定' if locked else '解锁'} '{found.name}'。"
                if ok
                else f"设置锁定状态失败：{result.get('Message', '未知错误')}"
            ),
        }

    @serialized_account_action
    async def set_quest_tracked_state(
        self,
        player_name: str,
        item_instance_id: str,
        tracked: bool,
        character: str | None = None,
    ) -> dict:
        """Track or untrack one quest/bounty item instance."""
        logger.info(
            "SetQuestTrackedState: item=%s tracked=%s character=%s (player=%s)",
            item_instance_id,
            tracked,
            character,
            player_name,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        found = await self._find_item(mid, mtype, item_instance_id)
        char_id, char_name = await self._resolve_action_character(mid, mtype, character, found)

        result = await self._bungie.set_quest_tracked_state(
            item_instance_id,
            char_id,
            mtype,
            state=tracked,
        )
        ok = result.get("ErrorCode", 0) == 1
        return {
            "success": ok,
            "item_name": found.name,
            "tracked": tracked,
            "character": char_name,
            "message": (
                f"已{'追踪' if tracked else '取消追踪'} '{found.name}'。"
                if ok
                else f"设置任务追踪失败：{result.get('Message', '未知错误')}"
            ),
        }

    # ── Move (convenience) ───────────────────────────────────────────

    @serialized_account_action
    async def move_item(
        self,
        player_name: str,
        item_name: str,
        destination: str,
        equip: bool = False,
        source: str | None = None,
        item_instance_id: str | None = None,
    ) -> MoveItemResult:
        """Convenience: search + transfer + optional equip by item name.

        When multiple copies exist and no item_instance_id is given, returns
        a disambiguation result with candidates for the user to choose from.

        Args:
            item_instance_id: Optional. If given, transfers this specific instance.
                              If not given and multiple copies exist, returns candidates.

        Raises:
            PlayerNotFoundError, CharacterNotFoundError.
        """
        logger.info(
            "Move: item=%s dest=%s equip=%s (player=%s)",
            item_name, destination, equip, player_name,
        )
        item_name = item_name.strip()
        if not item_name:
            return MoveItemResult(
                success=False,
                item_name="",
                message="必须提供物品名称。",
            )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        steps: list[MoveItemStep] = []

        # ── Step 1: Search ──
        manifest_results = self._manifest.search(item_name, limit=0)
        if not manifest_results:
            return MoveItemResult(
                success=False,
                item_name=item_name,
                message=f"找不到名为 '{item_name}' 的物品，请检查名称是否正确。支持中英文模糊搜索。",
                steps=[
                    MoveItemStep(
                        action="search",
                        detail=f"Searched for '{item_name}'",
                        success=False,
                    )
                ],
            )

        # Manifest hashes are signed, API returns unsigned — store both
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items = await self._fetch_all_items(mid, mtype)
        candidates = [it for it in all_items if it.item_hash in match_hashes]

        steps.append(
            MoveItemStep(
                action="search",
                detail=f"Found {len(candidates)} instance(s) matching '{item_name}' across account",
                success=len(candidates) > 0,
            )
        )

        if not candidates:
            return MoveItemResult(
                success=False,
                item_name=item_name,
                message=f"你的账号上没有找到 '{item_name}'。它可能已被分解，或从未获得过。",
                steps=steps,
            )

        # Filter by source
        if source:
            src_name = source.strip().lower()
            if src_name in {"vault", "仓库"}:
                src_name = "vault"
            else:
                src_class = resolve_character_name(source)
                src_name = class_type_name(src_class).lower()
            candidates = [it for it in candidates if it.location == src_name]
            if not candidates:
                return MoveItemResult(
                    success=False,
                    item_name=item_name,
                    message=f"在 {src_name} 上没有找到 '{item_name}'。它可能在其他角色或仓库里。",
                    steps=steps,
                )

        # Select target item: by instance_id, or single match, or disambiguate
        if item_instance_id:
            target_item = next(
                (it for it in candidates if it.item_instance_id == item_instance_id),
                None,
            )
            if not target_item:
                return MoveItemResult(
                    success=False,
                    item_name=item_name,
                    message=f"找不到 instance_id='{item_instance_id}' 的 '{item_name}'。",
                    steps=steps,
                )
        elif len(candidates) == 1:
            target_item = candidates[0]
        else:
            # Multiple copies — return candidates for disambiguation
            candidate_items = [
                ItemCandidate(
                    item_instance_id=it.item_instance_id,
                    name=it.name,
                    power=it.power,
                    location=it.location,
                    is_equipped=it.is_equipped,
                )
                for it in candidates
            ]
            # Build formatted question for the LLM to present directly
            loc_map = {"vault": "📦 仓库", "hunter": "🏹 猎人", "warlock": "⚡ 术士", "titan": "🛡️ 泰坦"}
            lines = [f"找到 {len(candidates)} 件匹配「{item_name}」的物品，你要转移哪一件？\n"]
            for i, c in enumerate(candidate_items, 1):
                loc = loc_map.get(c.location, c.location)
                equip_mark = " [已装备]" if c.is_equipped else ""
                power_str = f"光等 {c.power}" if c.power else "光等未知"
                lines.append(f"  {i}. {power_str}，{loc}{equip_mark}")
            lines.append(f"\n回复编号（1-{len(candidate_items)}）即可。")
            question_text = "\n".join(lines)

            return MoveItemResult(
                success=False,
                item_name=item_name,
                needs_disambiguation=True,
                candidates=candidate_items,
                message=f"找到 {len(candidates)} 件匹配 '{item_name}' 的物品，请选择具体实例。",
                question=question_text,
                steps=steps,
            )

        to_vault = destination.strip().lower() in ("vault", "仓库")
        if equip and to_vault:
            return MoveItemResult(
                success=False, item_name=target_item.name, steps=steps,
                message="Cannot equip an item in the vault.",
            )

        # Name lookup only selects an instance; all mutations use the exact-ID path.
        try:
            moved = await self.transfer_item(
                player_name, target_item.item_instance_id, destination,
                from_character=source,
            )
        except TransferError as exc:
            steps.append(MoveItemStep(action="transfer", detail=str(exc), success=False))
            return MoveItemResult(
                success=False, item_name=target_item.name,
                from_location=target_item.location, steps=steps, message=str(exc),
            )
        steps.append(MoveItemStep(action="transfer", detail=moved.message, success=moved.success))
        equip_ok = False
        if equip and moved.success:
            try:
                equipped = await self.equip_item(
                    player_name, target_item.item_instance_id, destination,
                )
                equip_ok = equipped.success
                steps.append(MoveItemStep(
                    action="equip", detail=equipped.message, success=equip_ok,
                ))
            except TransferError as exc:
                steps.append(MoveItemStep(action="equip", detail=str(exc), success=False))

        return MoveItemResult(
            success=moved.success and (not equip or equip_ok),
            item_name=target_item.name,
            from_location=moved.from_location,
            to_location=moved.to_location,
            equipped=equip_ok,
            steps=steps,
            message=steps[-1].detail,
        )

    # ── Apply Mod ─────────────────────────────────────────────────────

    @serialized_account_action
    async def apply_mod(
        self,
        player_name: str,
        item_instance_id: str,
        mod_name: str,
        character: str,
    ) -> dict:
        """Apply a mod to an armor piece.

        Args:
            player_name: Bungie name.
            item_instance_id: The armor instance ID.
            mod_name: Mod name (Chinese or English, e.g. '手雷模组').
            character: Character name (hunter/warlock/titan).

        Returns:
            Dict with success status and message.
        """
        logger.info(
            "apply_mod: player=%s item=%s mod=%s char=%s",
            player_name, item_instance_id, mod_name, character,
        )
        mod_name = mod_name.strip()
        if not mod_name:
            return {"success": False, "message": "必须提供模组名称。"}

        # Resolve player
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)

        # Find the mod by name — collect ALL matching hashes
        # (manifest may return multiple versions, only one is in the plug set)
        mod_results = self._manifest.search(mod_name, limit=0)
        mod_hashes: list[int] = []
        mod_display_name = mod_name
        for r in mod_results:
            if r.get("itemType") == 19:  # Mod
                mod_hashes.append(r["itemHash"])
                if not mod_display_name or mod_display_name == mod_name:
                    info = self._manifest.get_item_definition(r["itemHash"])
                    if info:
                        mod_display_name = info.get("displayProperties", {}).get("name", mod_name)

        if not mod_hashes:
            return {
                "success": False,
                "message": f"未找到模组 '{mod_name}'。",
            }

        logger.info("apply_mod: found %d mod hashes: %s", len(mod_hashes), mod_hashes)

        # Find the armor item to get its socket info
        all_items = await self._fetch_all_items(mid, mtype)
        armor_item = next(
            (it for it in all_items if it.item_instance_id == item_instance_id),
            None,
        )
        if not armor_item:
            return {
                "success": False,
                "message": f"未找到物品实例 {item_instance_id}。",
            }

        # Get armor definition to find socket entries
        armor_def = self._manifest.get_item_definition(armor_item.item_hash)
        if not armor_def:
            return {
                "success": False,
                "message": "无法获取护甲定义。",
            }

        socket_entries = armor_def.get("sockets", {}).get("socketEntries", [])

        # Find the socket that contains any of the matching mod hashes
        target_socket_index = None
        matched_mod_hash = None
        mod_hash_set = set(mod_hashes)

        for idx, entry in enumerate(socket_entries):
            # Check if this socket accepts user-pluggable mods
            plug_sources = entry.get("plugSources", 0)
            if plug_sources == 0 or plug_sources == 1:
                # 0 = no source, 1 = intrinsic only
                continue

            reusable_plug_set_hash = entry.get("reusablePlugSetHash", 0)
            if not reusable_plug_set_hash:
                continue

            # Check if this plug set contains any of our mod hashes
            plug_set = self._manifest.get_definition(
                "DestinyPlugSetDefinition", reusable_plug_set_hash
            )
            if not plug_set:
                continue

            for plug_item in plug_set.get("reusablePlugItems", []):
                plug_item_hash = plug_item.get("plugItemHash", 0)
                if plug_item_hash in mod_hash_set:
                    target_socket_index = idx
                    matched_mod_hash = plug_item_hash
                    break

            if target_socket_index is not None:
                break

        if target_socket_index is None:
            return {
                "success": False,
                "message": f"护甲 '{armor_item.name}' 没有可安装 '{mod_display_name}' 的插槽。",
            }

        # Insert the plug
        result = await self._bungie.insert_socket_plug(
            item_instance_id=item_instance_id,
            plug_item_hash=matched_mod_hash,
            socket_index=target_socket_index,
            socket_array_type=0,
            character_id=char_id,
            membership_type=mtype,
        )

        err = result.get("ErrorCode", 0)
        if err == 1:
            logger.info("apply_mod OK: %s → %s", mod_display_name, armor_item.name)
            return {
                "success": True,
                "item_name": armor_item.name,
                "mod_name": mod_display_name,
                "message": f"已将 '{mod_display_name}' 装备到 '{armor_item.name}'。",
            }
        else:
            msg = result.get("Message", "Unknown error")
            logger.error("apply_mod failed: %s", msg)
            return {
                "success": False,
                "item_name": armor_item.name,
                "mod_name": mod_display_name,
                "message": f"装备模组失败: {msg}",
            }

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _check_result(result: dict, operation: str) -> None:
        """Raise TransferError if the API result indicates failure."""
        err = result.get("ErrorCode", 0)
        if err != 1:
            msg = result.get("Message", "Unknown error")
            logger.error("%s failed: ErrorCode=%s, %s", operation, err, msg)
            raise TransferError(operation, msg)
