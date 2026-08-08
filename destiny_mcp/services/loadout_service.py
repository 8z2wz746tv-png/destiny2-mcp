"""Loadout service — save, list, equip, delete loadouts (配装).

Hybrid approach:
- Bungie native loadouts (10 per character): read + equip via API
- Local loadouts (unlimited): stored in ~/.destiny_mcp/loadouts.json
- Background cache refresh every 5 minutes for fast equip

Equipment logic (mod application, subclass config) is delegated to
LoadoutEquipmentService.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiobungie

from ..bungie_client import BungieClient
from ..exceptions import DestinyMCPError
from ..logging_config import get_logger
from ..manifest import BUNGIE_BASE_URL, ManifestManager, class_type_name, resolve_character_name
from ..models import (
    Loadout,
    LoadoutItem,
    LoadoutListResponse,
    LoadoutOperationResult,
    LoadoutSubclassConfig,
)
from ..player_resolver import PlayerResolver
from .account_action_lock import account_action_lock, serialized_account_action
from .loadout_equipment_service import LoadoutEquipmentService

logger = get_logger(__name__)

# Default loadout storage path
_DEFAULT_LOADOUT_PATH = Path.home() / ".destiny_mcp" / "loadouts.json"

# Bucket hashes for armor slots (unsigned 32-bit)
_ARMOR_SLOTS = {
    3448274439: "helmet",
    3551918588: "gauntlets",
    14239492: "chest",
    20886954: "legs",
    1585787867: "class_item",
}


class LoadoutService:
    """Operations for managing loadouts (配装)."""

    REFRESH_INTERVAL = 300  # 5 minutes

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        loadout_path: Path | None = None,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._account_action_lock = account_action_lock(bungie)
        self._equipment = LoadoutEquipmentService(bungie, manifest, resolver)
        self._path = loadout_path or _DEFAULT_LOADOUT_PATH

        # Cache: player_name -> list of loadouts
        self._cache: dict[str, list[Loadout]] = {}
        self._cache_timestamp: dict[str, float] = {}
        self._refresh_tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _slot_number_to_index(slot_number: int) -> int:
        """Convert user-facing slot number (1-10) to Bungie's zero-based index."""
        if slot_number < 1 or slot_number > 10:
            raise DestinyMCPError("官方配装槽位必须是 1 到 10。")
        return slot_number - 1

    def search_official_loadout_identifiers(
        self,
        kind: str = "all",
        query: str = "",
        limit: int = 30,
    ) -> dict:
        """Search official loadout name/icon/color identifier definitions."""
        tables = {
            "name": "DestinyLoadoutNameDefinition",
            "名称": "DestinyLoadoutNameDefinition",
            "icon": "DestinyLoadoutIconDefinition",
            "图标": "DestinyLoadoutIconDefinition",
            "color": "DestinyLoadoutColorDefinition",
            "颜色": "DestinyLoadoutColorDefinition",
        }
        normalized = kind.strip().lower() if kind else "all"
        selected = (
            list({"name": tables["name"], "icon": tables["icon"], "color": tables["color"]}.items())
            if normalized in {"all", "全部", ""}
            else [(normalized, tables.get(normalized, ""))]
        )
        if not selected or not selected[0][1]:
            return {
                "success": False,
                "message": "kind 只能是 all/name/icon/color（或 全部/名称/图标/颜色）。",
            }

        max_per_kind = max(1, min(limit, 100))
        results: dict[str, list[dict]] = {}
        for result_kind, table in selected:
            definitions = self._manifest.search_definitions_by_name(
                table,
                query,
                limit=max_per_kind,
                scan_limit=5000,
            )
            entries = []
            for definition in definitions:
                display = definition.get("displayProperties") or {}
                icon = display.get("icon", "")
                icon_url = (
                    f"{BUNGIE_BASE_URL}{icon}"
                    if icon.startswith("/")
                    else icon
                )
                entries.append({
                    "hash": int(definition.get("hash", 0)),
                    "name": display.get("name", ""),
                    "description": display.get("description", ""),
                    "icon_url": icon_url,
                })
            results[result_kind] = entries

        total = sum(len(entries) for entries in results.values())
        return {
            "success": True,
            "kind": kind,
            "query": query,
            "results": results,
            "message": f"找到 {total} 个官方配装标识候选。",
        }

    # ── Background cache refresh ────────────────────────────────────

    async def start_refresh(self, player_name: str) -> None:
        """Start background cache refresh task for a player."""
        if player_name in self._refresh_tasks:
            return
        await self._refresh_cache(player_name)
        self._refresh_tasks[player_name] = asyncio.create_task(self._refresh_loop(player_name))
        logger.info("Started loadout cache refresh for %s (interval=%ds)", player_name, self.REFRESH_INTERVAL)

    async def stop_refresh(self) -> None:
        """Stop all background cache refresh tasks."""
        for name, task in list(self._refresh_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped loadout cache refresh for %s", name)
        self._refresh_tasks.clear()

    async def _refresh_loop(self, player_name: str) -> None:
        while True:
            await asyncio.sleep(self.REFRESH_INTERVAL)
            try:
                await self._refresh_cache(player_name)
            except (DestinyMCPError, aiobungie.HTTPError):
                logger.warning("Cache refresh failed for %s", player_name, exc_info=True)

    async def _refresh_cache(self, player_name: str) -> None:
        native = await self._fetch_native(player_name)
        local = self._load_local()
        self._cache[player_name] = native + local
        self._cache_timestamp[player_name] = time.time()
        logger.info(
            "Cache refreshed for %s: %d loadouts (%d native + %d local)",
            player_name, len(self._cache[player_name]), len(native), len(local),
        )

    def _get_cached(self, player_name: str) -> list[Loadout] | None:
        timestamp = self._cache_timestamp.get(player_name)
        if timestamp is None:
            return None
        if time.time() - timestamp > self.REFRESH_INTERVAL * 2:
            return None
        return self._cache.get(player_name)

    # ── Local storage ───────────────────────────────────────────────

    def _load_local(self) -> list[Loadout]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [Loadout(**item) for item in data.get("loadouts", [])]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to load local loadouts: %s", e)
            return []

    def _save_local(self, loadouts: list[Loadout]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"loadouts": [lo.model_dump() for lo in loadouts]}
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Native loadouts ─────────────────────────────────────────────

    async def _fetch_native(self, player_name: str) -> list[Loadout]:
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 206]
        )

        loadouts_data = profile.get("characterLoadouts", {}).get("data", {})
        chars_data = profile.get("characters", {}).get("data", {})

        instance_to_hash: dict[str, int] = {}
        for item in profile.get("profileInventory", {}).get("data", {}).get("items", []):
            iid = str(item.get("itemInstanceId", ""))
            if iid:
                instance_to_hash[iid] = item.get("itemHash", 0)
        for inv_key in ("characterInventories", "characterEquipment"):
            for char_items in profile.get(inv_key, {}).get("data", {}).values():
                if isinstance(char_items, dict):
                    for item in char_items.get("items", []):
                        iid = str(item.get("itemInstanceId", ""))
                        if iid:
                            instance_to_hash[iid] = item.get("itemHash", 0)

        result: list[Loadout] = []
        for char_id, char_loadouts in loadouts_data.items():
            char_info = chars_data.get(char_id, {})
            class_type = char_info.get("classType", -1)
            class_name = {0: "titan", 1: "hunter", 2: "warlock"}.get(class_type, "unknown")

            for idx, lo_data in enumerate(char_loadouts.get("loadouts", [])):
                items = []
                for lo_item in lo_data.get("items", []):
                    instance_id = str(lo_item.get("itemInstanceId", ""))
                    item_hash = instance_to_hash.get(instance_id, 0)
                    plug_hashes = lo_item.get("plugItemHashes", [])
                    name = self._manifest.get_item_name(item_hash) if item_hash else ""
                    items.append(LoadoutItem(
                        item_hash=item_hash, name=name, slot="",
                        item_instance_id=instance_id, perks=plug_hashes,
                    ))

                result.append(Loadout(
                    id=f"bungie:{char_id}:{idx}",
                    name=f"配装 {idx + 1}",
                    character=class_name,
                    items=items,
                    source="bungie",
                    created_at="", notes="",
                ))

        return result

    # ── Public API ──────────────────────────────────────────────────

    async def get_loadouts(
        self, player_name: str, character: str | None = None,
    ) -> LoadoutListResponse:
        """List all loadouts (native + local), optionally filtered by character."""
        cached = self._get_cached(player_name)
        if cached is not None:
            all_loadouts = cached
        else:
            await self._refresh_cache(player_name)
            all_loadouts = self._cache[player_name]

        if character:
            char_lower = character.lower()
            all_loadouts = [lo for lo in all_loadouts if lo.character == char_lower]

        return LoadoutListResponse(player_name=player_name, loadouts=all_loadouts)

    async def save_loadout(
        self, player_name: str, name: str, character: str, notes: str = "",
    ) -> LoadoutOperationResult:
        """Save current equipment as a local loadout.

        Saves armor pieces with their equipped mods, and subclass configuration.
        """
        char_lower = character.lower()

        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        profile = await self._resolver.get_profile(
            mid, mtype, [102, 200, 201, 205, 300, 304, 305]
        )

        # Find the character ID for this class
        chars_data = profile.get("characters", {}).get("data", {})
        char_id = None
        for cid, cinfo in chars_data.items():
            class_type = cinfo.get("classType", -1)
            class_name = {0: "titan", 1: "hunter", 2: "warlock"}.get(class_type, "")
            if class_name == char_lower:
                char_id = cid
                break

        if not char_id:
            return LoadoutOperationResult(
                success=False, message=f"找不到角色 '{character}'。",
            )

        equip_data = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(char_id, {})
            .get("items", [])
        )
        sockets_data = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
        )

        equipped_items: list[LoadoutItem] = []
        subclass_config: LoadoutSubclassConfig | None = None

        for raw_item in equip_data:
            bucket_hash = raw_item.get("bucketHash", 0)
            inst_id = str(raw_item.get("itemInstanceId", ""))
            item_hash = raw_item.get("itemHash", 0)

            slot = _ARMOR_SLOTS.get(bucket_hash)
            if not slot:
                slot = _ARMOR_SLOTS.get(bucket_hash & 0xFFFFFFFF)
            if slot:
                mod_sockets = self._equipment.read_armor_mod_sockets(
                    inst_id, item_hash, sockets_data
                )
                item_name = self._manifest.get_item_name(item_hash)
                equipped_items.append(LoadoutItem(
                    item_hash=item_hash, name=item_name, slot=slot,
                    item_instance_id=inst_id,
                    mods=list(mod_sockets.values()),
                    mod_sockets=mod_sockets,
                ))

            item_info = self._manifest.get_item_info(item_hash) or {}
            if item_info.get("itemType") == 16:
                subclass_config = self._equipment.read_subclass_config(
                    inst_id, item_hash, sockets_data
                )

        loadout = Loadout(
            id=str(uuid.uuid4()),
            name=name, character=char_lower,
            items=equipped_items, subclass=subclass_config,
            source="local",
            created_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )

        local = self._load_local()
        local.append(loadout)
        self._save_local(local)

        mod_count = sum(len(item.mods) for item in equipped_items)
        sub_info = " + 子职业配置" if subclass_config else ""
        logger.info(
            "Saved loadout '%s' for %s (%d items, %d mods%s)",
            name, char_lower, len(equipped_items), mod_count, sub_info,
        )
        return LoadoutOperationResult(
            success=True, loadout_name=name,
            message=f"配装 '{name}' 已保存（{len(equipped_items)} 件装备，{mod_count} 个模组{sub_info}）。",
        )

    async def delete_loadout(self, loadout_id: str) -> LoadoutOperationResult:
        """Delete a local loadout by ID."""
        local = self._load_local()
        before = len(local)
        local = [lo for lo in local if lo.id != loadout_id]

        if len(local) == before:
            return LoadoutOperationResult(
                success=False, message=f"找不到 ID 为 '{loadout_id}' 的配装。",
            )

        self._save_local(local)
        logger.info("Deleted loadout %s", loadout_id)
        return LoadoutOperationResult(success=True, message="配装已删除。")

    @serialized_account_action
    async def equip_loadout(
        self, player_name: str, loadout_id: str,
    ) -> LoadoutOperationResult:
        """Equip a loadout (native via Bungie API, local via equipment service)."""
        if loadout_id.startswith("bungie:"):
            return await self._equip_native(player_name, loadout_id)

        cached = self._get_cached(player_name)
        if cached is None:
            await self._refresh_cache(player_name)
            cached = self._cache[player_name]

        loadout = next((lo for lo in cached if lo.id == loadout_id), None)
        if not loadout:
            return LoadoutOperationResult(
                success=False, message=f"找不到 ID 为 '{loadout_id}' 的配装。",
            )

        return await self._equipment.equip_local(player_name, loadout)

    async def _equip_native(
        self, player_name: str, loadout_id: str,
    ) -> LoadoutOperationResult:
        """Equip a Bungie native loadout via API."""
        parts = loadout_id.split(":")
        if len(parts) != 3:
            return LoadoutOperationResult(success=False, message="无效的官方配装 ID。")

        char_id = parts[1]
        loadout_index = int(parts[2])

        p = await self._resolver.resolve_player(player_name)
        result = await self._bungie.equip_loadout(
            loadout_index, char_id, p["membership_type"]
        )

        ok = result.get("ErrorCode", 0) == 1
        return LoadoutOperationResult(
            success=ok,
            message="官方配装已装备。" if ok else f"装备失败：{result.get('Message', '未知错误')}",
        )

    @serialized_account_action
    async def snapshot_official_loadout(
        self,
        player_name: str,
        character: str,
        slot_number: int,
        name_hash: int | None = None,
        icon_hash: int | None = None,
        color_hash: int | None = None,
    ) -> LoadoutOperationResult:
        """Save current character equipment into a Bungie official loadout slot."""
        loadout_index = self._slot_number_to_index(slot_number)
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_name = class_type_name(resolve_character_name(character))

        result = await self._bungie.snapshot_loadout(
            loadout_index,
            char_id,
            mtype,
            name_hash=name_hash,
            icon_hash=icon_hash,
            color_hash=color_hash,
        )
        ok = result.get("ErrorCode", 0) == 1
        self._cache_timestamp.pop(player_name, None)
        return LoadoutOperationResult(
            success=ok,
            message=(
                f"已把 {class_name} 当前装备保存到游戏内官方配装 {slot_number} 号槽。"
                if ok
                else f"保存官方配装失败：{result.get('Message', '未知错误')}"
            ),
        )

    @serialized_account_action
    async def update_official_loadout_identifiers(
        self,
        player_name: str,
        character: str,
        slot_number: int,
        name_hash: int | None = None,
        icon_hash: int | None = None,
        color_hash: int | None = None,
    ) -> LoadoutOperationResult:
        """Update name/icon/color hashes for a Bungie official loadout slot."""
        if name_hash is None and icon_hash is None and color_hash is None:
            return LoadoutOperationResult(
                success=False,
                message="至少需要提供 name_hash、icon_hash 或 color_hash 中的一个。",
            )

        loadout_index = self._slot_number_to_index(slot_number)
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_name = class_type_name(resolve_character_name(character))

        result = await self._bungie.update_loadout_identifiers(
            loadout_index,
            char_id,
            mtype,
            name_hash=name_hash,
            icon_hash=icon_hash,
            color_hash=color_hash,
        )
        ok = result.get("ErrorCode", 0) == 1
        self._cache_timestamp.pop(player_name, None)
        return LoadoutOperationResult(
            success=ok,
            message=(
                f"已更新 {class_name} 官方配装 {slot_number} 号槽的名称/图标/颜色。"
                if ok
                else f"更新官方配装标识失败：{result.get('Message', '未知错误')}"
            ),
        )

    @serialized_account_action
    async def clear_official_loadout(
        self,
        player_name: str,
        character: str,
        slot_number: int,
    ) -> LoadoutOperationResult:
        """Clear a Bungie official loadout slot."""
        loadout_index = self._slot_number_to_index(slot_number)
        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]
        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_name = class_type_name(resolve_character_name(character))

        result = await self._bungie.clear_loadout(loadout_index, char_id, mtype)
        ok = result.get("ErrorCode", 0) == 1
        self._cache_timestamp.pop(player_name, None)
        return LoadoutOperationResult(
            success=ok,
            message=(
                f"已清空 {class_name} 官方配装 {slot_number} 号槽。"
                if ok
                else f"清空官方配装失败：{result.get('Message', '未知错误')}"
            ),
        )
