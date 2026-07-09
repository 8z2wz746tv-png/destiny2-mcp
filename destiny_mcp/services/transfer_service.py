"""Transfer service — item movement between characters and vault.

Extracted from server.py per Rule 1: tools should not contain business logic.
This is the most complex service because Destiny 2 does not support direct
character-to-character transfers (must go through vault).
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import ItemNotFoundError, TransferError
from ..logging_config import get_logger
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

logger = get_logger(__name__)


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

    # ── Helpers ──────────────────────────────────────────────────────

    async def _fetch_all_items(
        self, membership_id: str, membership_type: int
    ) -> list[InventoryItem]:
        """Fetch all items across the account."""
        profile = await self._resolver.get_profile(
            membership_id, membership_type, [102, 200, 201, 205, 300, 304]
        )
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

    # ── Transfer ─────────────────────────────────────────────────────

    async def transfer_item(
        self,
        player_name: str,
        item_instance_id: str,
        to_character: str,
        from_character: str | None = None,
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
        if not to_vault:
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
            if found.location == dest_name.lower():
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

            # Step 2: to destination
            r2 = await self._bungie.transfer_item(
                item_instance_id, item_hash,
                character_id=target_char_id,
                membership_type=mtype,
                to_vault=False,
            )
            self._check_result(r2, f"Transfer vault→{dest_name} (second hop)")

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

    async def equip_item(
        self,
        player_name: str,
        item_instance_id: str,
        character: str,
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

        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_type = resolve_character_name(character)

        # Find item name for the response
        try:
            found = await self._find_item(mid, mtype, item_instance_id)
            item_name = found.name
        except ItemNotFoundError:
            item_name = "Unknown"

        result = await self._bungie.equip_item(item_instance_id, char_id, mtype)
        self._check_result(result, "Equip")

        logger.info("Equipped '%s' on %s", item_name, class_type_name(class_type))
        return EquipResult(
            success=True,
            item_name=item_name,
            character=class_type_name(class_type),
            message=f"Equipped '{item_name}' on {class_type_name(class_type)}.",
        )

    # ── Move (convenience) ───────────────────────────────────────────

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
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        steps: list[MoveItemStep] = []

        # ── Step 1: Search ──
        manifest_results = self._manifest.search(item_name, limit=10)
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

        exact_name = manifest_results[0]["name"]
        steps.append(
            MoveItemStep(
                action="search",
                detail=f"Found {len(candidates)} instance(s) of '{exact_name}' across account",
                success=len(candidates) > 0,
            )
        )

        if not candidates:
            return MoveItemResult(
                success=False,
                item_name=exact_name,
                message=f"你的账号上没有找到 '{exact_name}'。它可能已被分解，或从未获得过。",
                steps=steps,
            )

        # Filter by source
        if source:
            src_class = resolve_character_name(source)
            src_name = class_type_name(src_class).lower()
            candidates = [it for it in candidates if it.location == src_name]
            if not candidates:
                return MoveItemResult(
                    success=False,
                    item_name=exact_name,
                    message=f"在 {src_name} 上没有找到 '{exact_name}'。它可能在其他角色或仓库里。",
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
                    item_name=exact_name,
                    message=f"找不到 instance_id='{item_instance_id}' 的 '{exact_name}'。",
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
            lines = [f"找到 {len(candidates)} 件「{exact_name}」，你要转移哪一件？\n"]
            for i, c in enumerate(candidate_items, 1):
                loc = loc_map.get(c.location, c.location)
                equip_mark = " [已装备]" if c.is_equipped else ""
                power_str = f"光等 {c.power}" if c.power else "光等未知"
                lines.append(f"  {i}. {power_str}，{loc}{equip_mark}")
            lines.append(f"\n回复编号（1-{len(candidate_items)}）即可。")
            question_text = "\n".join(lines)

            return MoveItemResult(
                success=False,
                item_name=exact_name,
                needs_disambiguation=True,
                candidates=candidate_items,
                message=f"找到 {len(candidates)} 件 '{exact_name}'，请选择要转移哪一件。",
                question=question_text,
                steps=steps,
            )

        item_id = target_item.item_instance_id
        item_hash = target_item.item_hash
        from_loc = target_item.location

        to_vault = destination.strip().lower() in ("vault", "仓库")
        if not to_vault:
            dest_class = resolve_character_name(destination)
            dest_char_id = await self._resolver.resolve_character_id(
                mid, mtype, destination
            )
            dest_name = class_type_name(dest_class)
        else:
            dest_name = "vault"

        # ── Step 2: Transfer ──
        if from_loc == dest_name.lower():
            steps.append(
                MoveItemStep(
                    action="transfer",
                    detail=f"'{exact_name}' is already at {dest_name}",
                    success=True,
                )
            )
        elif from_loc != "vault" and to_vault:
            # Character → Vault
            r = await self._bungie.transfer_item(
                item_id, item_hash,
                character_id=target_item.character_id,
                membership_type=mtype, to_vault=True,
            )
            ok = r.get("ErrorCode", 0) == 1
            steps.append(
                MoveItemStep(
                    action="transfer",
                    detail=f"Transfer '{exact_name}' from {from_loc} to vault",
                    success=ok,
                )
            )
            if not ok:
                return MoveItemResult(
                    success=False, item_name=exact_name, steps=steps,
                    message=f"移动失败：{r.get('Message', '未知错误')}。可能原因：物品不在该位置、目标背包已满、或服务器暂时不可用。",
                )
        elif from_loc == "vault" and not to_vault:
            # Vault → Character
            r = await self._bungie.transfer_item(
                item_id, item_hash,
                character_id=dest_char_id,
                membership_type=mtype, to_vault=False,
            )
            ok = r.get("ErrorCode", 0) == 1
            steps.append(
                MoveItemStep(
                    action="transfer",
                    detail=f"Transfer '{exact_name}' from vault to {dest_name}",
                    success=ok,
                )
            )
            if not ok:
                return MoveItemResult(
                    success=False, item_name=exact_name, steps=steps,
                    message=f"移动失败：{r.get('Message', '未知错误')}。可能原因：物品不在仓库、目标背包已满、或服务器暂时不可用。",
                )
        elif from_loc != "vault" and not to_vault:
            # Character → Character (via vault)
            deposited = await self._bungie.transfer_item(
                item_id, item_hash,
                character_id=target_item.character_id,
                membership_type=mtype, to_vault=True,
            )
            if deposited.get("ErrorCode", 0) != 1:
                steps.append(
                    MoveItemStep(
                        action="transfer",
                        detail=f"Transfer to vault failed: {deposited.get('Message')}",
                        success=False,
                    )
                )
                return MoveItemResult(
                    success=False, item_name=exact_name, steps=steps,
                    message=f"存入仓库失败：{deposited.get('Message', '未知错误')}。仓库可能已满。",
                )

            pulled = await self._bungie.transfer_item(
                item_id, item_hash,
                character_id=dest_char_id,
                membership_type=mtype, to_vault=False,
            )
            if pulled.get("ErrorCode", 0) != 1:
                steps.append(
                    MoveItemStep(
                        action="transfer",
                        detail=f"Vault→{dest_name} failed: {pulled.get('Message')}",
                        success=False,
                    )
                )
                return MoveItemResult(
                    success=False, item_name=exact_name, steps=steps,
                    message=f"从仓库取出失败：{pulled.get('Message', '未知错误')}。{dest_name}的背包可能已满。",
                )
            steps.append(
                MoveItemStep(
                    action="transfer",
                    detail=f"Transfer '{exact_name}' from {from_loc} to {dest_name} (via vault)",
                    success=True,
                )
            )

        # ── Step 3: Equip (optional) ──
        equip_ok = False
        if equip and not to_vault:
            r = await self._bungie.equip_item(item_id, dest_char_id, mtype)
            equip_ok = r.get("ErrorCode", 0) == 1
            steps.append(
                MoveItemStep(
                    action="equip",
                    detail=f"Equip '{exact_name}' on {dest_name}",
                    success=equip_ok,
                )
            )

        # Transfer always succeeds at this point; equip may fail
        transfer_ok = True
        if equip and not to_vault and not equip_ok:
            transfer_ok = False  # Equip was requested but failed

        return MoveItemResult(
            success=transfer_ok,
            item_name=exact_name,
            from_location=from_loc,
            to_location=dest_name,
            equipped=equip_ok,
            steps=steps,
            message=(
                f"Moved '{exact_name}' from {from_loc} to {dest_name}"
                + (", equipped" if equip_ok else "")
                + (" (equip failed)" if equip and not equip_ok else "")
            ),
        )

    # ── Apply Mod ─────────────────────────────────────────────────────

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

        # Resolve player
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)

        # Find the mod by name — collect ALL matching hashes
        # (manifest may return multiple versions, only one is in the plug set)
        mod_results = self._manifest.search(mod_name, limit=20)
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
