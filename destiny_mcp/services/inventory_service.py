"""Inventory service — item parsing, lookup, and search.

Extracted from server.py per Rule 1: tools should not contain business logic.
"""

from __future__ import annotations

from ..bungie_client import BungieClient
from .. import vocabulary
from ..exceptions import (
    AuthenticationError,
    CharacterNotFoundError,
    ConfigError,
    DestinyMCPError,
    InvalidArgumentError,
    ItemNotFoundError,
)
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..build.models import InventorySnapshot
from ..build.constants import ARMOR_SLOT_MAP, SOLVER_SLOTS, STAT_HASH_TO_NAME
from ..models import InventoryItem, InventoryResponse, SearchItemsResponse
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from ..utils.icons import icon_url as _icon_url
from .armor_payload import slot_key_from_solver, socket_rows
from .inventory_lookup import locate_instance as _locate_instance
from .item_parser import parse_items_from_profile

logger = get_logger(__name__)

_INVENTORY_PROFILE_COMPONENTS = profile_components.INVENTORY
_ARMOR_SNAPSHOT_COMPONENTS = profile_components.ARMOR_SNAPSHOT
MISSING_INVENTORY_SCOPE_MESSAGE = (
    "当前 Bungie OAuth token 缺少库存/仓库权限 ReadDestinyInventoryAndVault，"
    "请在 Bungie Developer Portal 给应用开启该 scope 后重新完成 Bungie 授权。"
    "当前只读到了已装备装备，不能判断仓库或角色背包是否为空。"
)

# 稀有度：英文键 → Manifest 的 tierType（6 异域 / 5 传说 / 4 稀有）
_INVENTORY_ITEM_TYPE_ALIASES = {
    "": None,
    "all": "all",
    "全部": "all",
    "weapon": "weapon",
    "weapons": "weapon",
    "武器": "weapon",
    "armor": "armor",
    "armors": "armor",
    "护甲": "armor",
}


def _normalize_inventory_item_type(value: str | None) -> str | None:
    key = str(value or "").strip().lower()
    if key in _INVENTORY_ITEM_TYPE_ALIASES:
        return _INVENTORY_ITEM_TYPE_ALIASES[key]
    raise ConfigError(
        f"item_type={value!r} 不受支持。get 仅支持 weapon/armor/all；"
        f"查询具体武器类型请使用 inventory_assistant(intent=\"type\", type_name={value!r})。"
    )


class InventoryService:
    """Operations for reading and searching player/vault inventories."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    # ── Inventory ────────────────────────────────────────────────────

    async def _resolve_and_fetch(
        self, player_name: str, components: list[int]
    ) -> tuple[dict, dict]:
        """Resolve player name and fetch profile. Returns (player_dict, profile)."""
        p = await self._resolver.resolve_player(player_name)
        profile = await self._resolver.get_profile(
            p["membership_id"], p["membership_type"], components
        )
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        require_complete_inventory_components(profile)
        return p, profile

    async def get_inventory(
        self,
        player_name: str,
        location: str,
        item_type: str | None = None,
        armor_slot: str | None = None,
        rarity: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> InventoryResponse:
        """Get inventory for a specific character or the vault.

        Args:
            player_name: Bungie name.
            location: 'hunter', 'warlock', 'titan', 'vault' (or Chinese).
            item_type: Filter by type — 'weapon'/'armor'/'all'. None = all.
            armor_slot: Filter by armor slot — 'helmet'/'gauntlets'/'chest'/'legs'/'class_item'/'all'. None = all.
            rarity: Filter by rarity — 'exotic'/'legendary'/'rare'/'all'. None = all.
            limit: 最多返回多少件；None 或 <=0 = 不限（调用方负责给默认上限）。
            offset: 从第几件开始（配合 next_offset 翻页）。

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
            ConfigError: If item_type is not weapon/armor/all.
        """
        item_type = _normalize_inventory_item_type(item_type)
        logger.info(
            "Fetching inventory for %s, location=%s, type=%s, slot=%s, rarity=%s",
            player_name, location, item_type, armor_slot, rarity,
        )
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        items, loc_name = self._items_at_location(profile, location)

        # Apply filters
        items = self._filter_items(items, item_type, armor_slot, rarity)

        # **已装备的排最前**（稳定排序，其余保持原顺序）：盲测 2026-10-06 里
        # "我泰坦现在身上穿着什么" 返回的前 100 件**一件 is_equipped 都没有** ——
        # 玩家要翻到第 2 页才看得到身上那套。装备是这类查询第一眼要看的东西。
        items.sort(key=lambda item: not item.is_equipped)

        # 截断与自证：以前一次能把整个仓库倒出来（真机 1260 件 ≈ 511 KB / 13–15 万 tokens），
        # 而响应里没有总数也没有 truncated，调用方既没法少要一点、也察觉不到自己只看到一部分。
        total = len(items)
        start = max(0, offset)
        if limit is not None and limit > 0:
            window = items[start : start + limit]
        else:
            window = items[start:]
        truncated = start + len(window) < total
        return InventoryResponse(
            location=loc_name,
            items=window,
            total_items=total,
            returned_items=len(window),
            truncated=truncated,
            next_offset=(start + len(window)) if truncated else None,
        )

    def _filter_items(
        self,
        items: list[InventoryItem],
        item_type: str | None,
        armor_slot: str | None,
        rarity: str | None,
    ) -> list[InventoryItem]:
        """Filter items by type, armor slot, and rarity."""
        result = items

        # Filter by item_type (weapon/armor)
        if item_type and item_type.lower() not in ("all", ""):
            t = item_type.lower()
            if t == "weapon":
                weapon_buckets = {
                    "Kinetic Weapons", "Energy Weapons", "Power Weapons",
                }
                result = [
                    i for i in result
                    if i.item_type.lower() == "weapon" or i.bucket_type in weapon_buckets
                ]
            elif t == "armor":
                armor_buckets = {
                    "Helmet", "Gauntlets", "Chest Armor", "Leg Armor", "Class Armor",
                }
                result = [
                    i for i in result
                    if i.item_type.lower() == "armor" or i.bucket_type in armor_buckets
                ]

        # Filter by armor_slot
        if armor_slot and armor_slot.lower() not in ("all", ""):
            slot_map = {
                "helmet": "Helmet",
                "gauntlets": "Gauntlets",
                "chest": "Chest Armor",
                "legs": "Leg Armor",
                "class_item": "Class Armor",
            }
            target_bucket = slot_map.get(armor_slot.lower())
            if target_bucket:
                result = [i for i in result if i.bucket_type == target_bucket]

        # Filter by rarity (tier)。封闭词表：认不出来就报错，不能当成"没传"。
        # 以前只映射英文，传"异域"会安静地返回未过滤的清单 —— 而参数说明里写着中文可用，
        # 调用方会以为筛过了，把传说件当异域答。
        if rarity and rarity.strip().lower() not in ("all", "", "全部"):
            key = rarity.strip().lower()
            target_tier = vocabulary.rarity_key(key)
            if target_tier is None:
                raise InvalidArgumentError(
                    f"rarity={rarity!r} 不受支持。可用：exotic/legendary/rare"
                    "（也认 异域/传说/稀有）。不传或传 all = 不筛。"
                )
            result = [
                i for i in result
                if self._get_item_tier(i.item_hash) == target_tier
            ]

        return result

    def _get_item_tier(self, item_hash: int) -> int:
        """Get item tier from manifest. Returns 0 if not found."""
        info = self._manifest.get_item_info(item_hash)
        if info:
            return info.get("tier", 0)
        return 0

    async def search_items(
        self, player_name: str, item_name: str, location: str = ""
    ) -> SearchItemsResponse:
        """Search for an item by name across all characters and vault.

        Args:
            player_name: Bungie name.
            item_name: Partial item name (Chinese or English).

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info("Searching items: player=%s, item=%s", player_name, item_name)
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        item_query = item_name.strip()
        if not item_query:
            raise ConfigError("请提供 item_name。")
        manifest_results = self._manifest.search(item_query, limit=0)

        # Manifest hashes are signed, API returns unsigned — store both
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items, _ = self._items_at_location(profile, location)
        query_key = item_query.casefold()
        matched = [
            item
            for item in all_items
            if item.item_hash in match_hashes
            or query_key in item.name.casefold()
            or query_key in item.name_en.casefold()
        ]

        logger.info(
            "Item search '%s': %d manifest hit(s), %d instance(s) found",
            item_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=item_name, items=matched)

    async def search_items_by_type(
        self, player_name: str, type_name: str, location: str = ""
    ) -> SearchItemsResponse:
        """Search for items by weapon/armor type across all characters and vault.

        Args:
            player_name: Bungie name.
            type_name: Type display name (e.g. '微型冲锋枪', '手炮', '自动步枪').

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info(
            "Searching items by type: player=%s, type=%s", player_name, type_name
        )
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )

        type_query = type_name.strip()
        if not type_query:
            raise ConfigError("请提供 type_name。")
        manifest_results = self._manifest.search_by_type_name(type_query, limit=0)

        # Build match set with both signed and unsigned hashes
        match_hashes: set[int] = set()
        for r in manifest_results:
            h = r["itemHash"]
            match_hashes.add(h)
            match_hashes.add(to_unsigned(h))

        all_items, _ = self._items_at_location(profile, location)
        matched = [it for it in all_items if it.item_hash in match_hashes]

        logger.info(
            "Type search '%s': %d manifest hit(s), %d instance(s) found",
            type_name,
            len(match_hashes),
            len(matched),
        )
        return SearchItemsResponse(query=type_name, items=matched)

    def _items_at_location(
        self,
        profile: dict,
        location: str,
    ) -> tuple[list[InventoryItem], str]:
        normalized = location.strip().lower()
        if normalized in ("", "all", "全部", "账号", "account"):
            return parse_items_from_profile(profile, self._manifest), "all"
        if normalized in ("vault", "仓库"):
            return (
                parse_items_from_profile(
                    profile,
                    self._manifest,
                    target_location="vault",
                ),
                "vault",
            )
        if normalized in ("postmaster", "邮政官", "邮政长", "lost items", "lost_items"):
            items = parse_items_from_profile(profile, self._manifest)
            return (
                [item for item in items if item.location == "postmaster"],
                "postmaster",
            )
        try:
            class_type = resolve_character_name(location)
        except DestinyMCPError as exc:
            # 以前这里直接冒"该账号上没有 'postmaster' 角色"—— 调用方读成"账号没这个人"，
            # 实际是"location 取值不认"。按工具层前置校验的口径报 invalid_arguments + 合法取值。
            raise InvalidArgumentError(
                f"location 取值不认识：{location!r}。可用：all/全部、vault/仓库、"
                "postmaster/邮政官、hunter/warlock/titan（或中文职业名）。"
            ) from exc
        canonical_location = class_type_name(class_type).lower()
        items = parse_items_from_profile(profile, self._manifest)
        return (
            [item for item in items if item.location == canonical_location],
            canonical_location,
        )

    async def find_item(
        self,
        player_name: str,
        item_instance_id: str,
    ) -> InventoryItem:
        """Find a specific item instance across the account.

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
            ItemNotFoundError: If the item instance is not found.
        """
        logger.info("Finding item instance: %s", item_instance_id)
        _, profile = await self._resolve_and_fetch(
            player_name, _INVENTORY_PROFILE_COMPONENTS
        )
        all_items = parse_items_from_profile(profile, self._manifest)
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

    async def get_armor_items(
        self,
        player_name: str,
        item_instance_ids: list[str],
    ) -> dict[str, dict]:
        """一次 profile 调用取多件护甲的原始组件（装备回显要用五件）。"""
        wanted = [str(i) for i in item_instance_ids if str(i).strip()]
        if not wanted:
            return {}
        _, profile = await self._resolve_and_fetch(
            player_name, _ARMOR_SNAPSHOT_COMPONENTS
        )
        components = profile.get("itemComponents") or {}
        instances = (components.get("instances") or {}).get("data") or {}
        sockets = (components.get("sockets") or {}).get("data") or {}
        stats = (components.get("stats") or {}).get("data") or {}
        out: dict[str, dict] = {}
        for item_instance_id in wanted:
            located = _locate_instance(profile, item_instance_id)
            if located is None:
                continue
            item, location, character_id = located
            definition = self._manifest.get_item_definition(item.get("itemHash", 0)) or {}
            out[item_instance_id] = {
                "item": item,
                "definition": definition,
                "instance": instances.get(item_instance_id) or {},
                "sockets": (sockets.get(item_instance_id) or {}).get("sockets") or [],
                "stats": (stats.get(item_instance_id) or {}).get("stats") or {},
                "location": location,
                "character_id": character_id,
                "bucket": self._manifest.bucket_name(
                    (definition.get("inventory") or {}).get("bucketTypeHash", 0)
                ),
            }
        return out

    async def get_armor_item(
        self,
        player_name: str,
        item_instance_id: str,
    ) -> dict:
        """取一件护甲的原始组件，够 `armor_payload` 拼出完整载荷。

        和 `find_item` 的区别：那条路用的是轻量组件集（没有插槽），够列清单、不够回答
        "这件现在装了什么、能量还剩多少"。这里按护甲快照的组件集取，并额外回报它在
        哪个容器里（仓库/角色）。

        Raises:
            PlayerNotFoundError: 玩家名解析失败。
            ItemNotFoundError: 这件实例不在账号里（可能已分解或转移）。
        """
        if not item_instance_id.strip():
            raise InvalidArgumentError("必须提供 item_instance_id（护甲实例 ID）。")
        logger.info("Reading armor item instance: %s", item_instance_id)
        _, profile = await self._resolve_and_fetch(
            player_name, _ARMOR_SNAPSHOT_COMPONENTS
        )
        located = _locate_instance(profile, item_instance_id)
        if located is None:
            raise ItemNotFoundError(
                item_instance_id,
                "It may have been moved or dismantled.",
            )
        item, location, character_id = located
        components = profile.get("itemComponents") or {}
        instances = (components.get("instances") or {}).get("data") or {}
        sockets = (components.get("sockets") or {}).get("data") or {}
        stats = (components.get("stats") or {}).get("data") or {}
        return {
            "item": item,
            "definition": self._manifest.get_item_definition(item.get("itemHash", 0)) or {},
            "instance": instances.get(item_instance_id) or {},
            "sockets": (sockets.get(item_instance_id) or {}).get("sockets") or [],
            "stats": (stats.get(item_instance_id) or {}).get("stats") or {},
            "location": location,
            "character_id": character_id,
            "bucket": self._manifest.bucket_name(
                ((self._manifest.get_item_definition(item.get("itemHash", 0)) or {})
                 .get("inventory") or {}).get("bucketTypeHash", 0)
            ),
        }

    async def get_equipped_armor_mods(
        self, player_name: str, character: str = ""
    ) -> dict:
        """一次读回「已装备的护甲 + 每件的插槽」：**独立回读**用（写入回执之外的另一条证据）。

        为什么一次就够：组件 305 覆盖**账号里全部物品**的已装插槽（实测 3.47 MB / 0.85 秒），
        而"哪五件在装备位上"就在同一次响应的组件 205 里。逐件走 `get_armor_item` 是 N+1：
        每件重读一次整份 profile（实测 4.31 MB / 3.32 秒 + 载荷组装），五件 ≈ 70 秒。

        与写入流程 `verify` 的分工：那条是**写入路径内部的自证**（写在回执的 steps 里）；
        这一条不信任回执，只看账号现在到底装着什么 —— 所以它只读、不改、也不判断"该不该"。

        Args:
            character: 只读这一位角色；留空 = 三位角色都读（各带职业标签，调用方自己挑）。

        Returns:
            `{"characters": [{"character", "class", "class_display", "item_count", "items"}]}`；
            `items[]` = `{slot, slot_key, name, item_instance_id, item_hash, icon_url, is_exotic,
            power, energy, mods}`，`mods` 与 `intent="item"` 的 `armor.sockets` **同一形状**
            （同一形状工厂 `armor_payload.socket_rows`），否则两边对不上。

        Raises:
            PlayerNotFoundError: 玩家名解析失败。
            CharacterNotFoundError: 指定了角色但账号里没有这一位。
        """
        class_type = resolve_character_name(character) if character.strip() else -1
        _, profile = await self._resolve_and_fetch(
            player_name, profile_components.INVENTORY_SOCKETS
        )
        chars = (profile.get("characters") or {}).get("data") or {}
        equipment = (profile.get("characterEquipment") or {}).get("data") or {}
        components = profile.get("itemComponents") or {}
        sockets_map = (components.get("sockets") or {}).get("data") or {}
        instances = (components.get("instances") or {}).get("data") or {}

        blocks: list[dict] = []
        for char_id, info in chars.items():
            char_class_type = info.get("classType", -1)
            if class_type >= 0 and char_class_type != class_type:
                continue
            items: list[dict] = []
            for raw in (equipment.get(char_id) or {}).get("items", []):
                item_hash = int(raw.get("itemHash", 0) or 0)
                definition = self._manifest.get_item_info(item_hash) or {}
                bucket = raw.get("bucketHash", 0)
                if bucket == 138197802:
                    bucket = definition.get("bucketTypeHash", 0)
                slot = ARMOR_SLOT_MAP.get(bucket)
                if slot is None:
                    continue  # 不是护甲（武器/子职业/机灵…）
                instance_id = str(raw.get("itemInstanceId", "") or "")
                instance = instances.get(instance_id) or {}
                energy = instance.get("energy") if isinstance(instance, dict) else None
                items.append({
                    "slot": slot,
                    "slot_key": slot_key_from_solver(slot),
                    "name": self._manifest.get_item_name(item_hash),
                    "item_instance_id": instance_id,
                    "item_hash": item_hash,
                    # 这张表是"哪五件、每件装着什么"的核对入口，UI 拿它画卡片；
                    # 少了图标就只能放色块（2026-10-04 用户实拍）。`definition` 是
                    # `get_item_info()` 的结果，`icon` 已经是绝对地址。
                    "icon_url": _icon_url(definition.get("icon")),
                    "is_exotic": definition.get("tier") == 6,
                    "power": (instance.get("primaryStat") or {}).get("value"),
                    "energy": {
                        "capacity": (energy or {}).get("energyCapacity"),
                        "used": (energy or {}).get("energyUsed"),
                    } if isinstance(energy, dict) else None,
                    "mods": socket_rows(
                        (sockets_map.get(instance_id) or {}).get("sockets") or [],
                        self._manifest.get_item_definition,
                    ),
                })
            # 按**规范部位顺序**排（头盔→职业物品）：随便按字母排的话，读的人要自己对
            # 五件的位置，而"这五件是哪五件"正是这个入口要回答的。
            items.sort(key=lambda row: SOLVER_SLOTS.index(row["slot"]))
            class_key = class_type_name(char_class_type).lower()
            blocks.append({
                "character": class_key,
                "class": char_class_type,
                # 中文名走词表（唯一出处），别在这里再抄一张
                "class_display": vocabulary.CLASS_LABELS_ZH.get(class_key, ""),
                "item_count": len(items),
                "items": items,
            })
        if class_type >= 0 and not blocks:
            raise CharacterNotFoundError(character, ["hunter", "warlock", "titan"])
        return {"characters": blocks}

    async def get_armor_socket_plugs(self, player_name: str) -> dict[str, list[dict]]:
        """一次读回全账号的「已装插槽」：`{item_instance_id: sockets}`。

        和 `get_armor_item` 的区别是**一次 vs 每件一次** —— 后者每件都要重读整份 profile，
        要核对多件时就是 N+1（项目文档点过这条）。这里只要组件 305，不带 304 属性：
        实测 3.47 MB / 0.85 秒（带 304 是 4.31 MB / 3.32 秒），覆盖全部 1628 件。

        用途是「这件东西上到底装着哪几颗」——职业金装的两个异域特性只有这里看得到
        （Manifest 的定义里那两个槽是占位，`plugSources: 1` 只从实例来）。
        """
        _, profile = await self._resolve_and_fetch(
            player_name, profile_components.INVENTORY_SOCKETS
        )
        data = ((profile.get("itemComponents") or {}).get("sockets") or {}).get("data") or {}
        return {
            instance_id: (entry or {}).get("sockets") or []
            for instance_id, entry in data.items()
        }

    # ── Armor Snapshot (Build Engine) ─────────────────────────────────

    async def get_armor_snapshot(
        self,
        player_name: str,
        character_class: str = "",
        *,
        with_tuning_options: bool = True,
        reserved_mod_energy: dict[str, int] | None = None,
    ) -> InventorySnapshot:
        """Fetch all armor pieces with stats, grouped by slot.

        This is the data input for Build Engine. Fetches components
        102+200+201+205+300+304+305 (ItemStats for the 6 armor stats,
        ItemSockets for artifice detection) plus **310 `ItemReusablePlugs`**
        when `with_tuning_options` is on (the default) — 每件允许装哪些调谐只在 310 里。

        Must be called after the manifest is loaded.

        Args:
            player_name: Bungie name.
            character_class: Target class filter — "hunter"/"warlock"/"titan" (or Chinese).
                           If empty, includes all classes.

        Returns:
            InventorySnapshot with armor grouped by slot.

        Raises:
            PlayerNotFoundError: If the player name cannot be resolved.
        """
        logger.info("Building armor snapshot for %s (class=%s)", player_name, character_class or "all")
        # 默认带 310：这个方法的定位就是"求解器的数据输入"，而**每件允许装哪些调谐只在 310 里**
        # （Manifest 那份是全局 32 颗，照它会规划出装不上的调谐）。代价 4.11→8.90 MB，
        # 不规划调谐的调用方（社区模板核对）显式传 `with_tuning_options=False` 省掉它。
        components = (
            profile_components.BUILD_ARMOR
            if with_tuning_options
            else _ARMOR_SNAPSHOT_COMPONENTS
        )
        _, profile = await self._resolve_and_fetch(player_name, components)
        self._validate_armor_snapshot_components(profile, character_class)
        snapshot = InventorySnapshot.from_profile(
            profile, self._manifest, character_class,
            reserved_mod_energy=reserved_mod_energy,
        )
        logger.info(
            "Armor snapshot ready: %d pieces across 5 slots",
            snapshot.total_pieces,
        )
        return snapshot

    def _validate_armor_snapshot_components(
        self,
        profile: dict,
        character_class: str,
    ) -> None:
        """Reject partial armor data before it reaches the optimizer."""
        target_class = (
            resolve_character_name(character_class) if character_class.strip() else -1
        )
        chars = profile["characters"]["data"]
        vault_items = profile["profileInventory"]["data"]["items"]
        inventories = profile["characterInventories"]["data"]
        equipment = profile["characterEquipment"]["data"]
        components = profile.get("itemComponents", {})
        instances = (components.get("instances") or {}).get("data")
        stats = (components.get("stats") or {}).get("data")
        sockets = (components.get("sockets") or {}).get("data")
        if not all(isinstance(value, dict) for value in (instances, stats, sockets)):
            raise ConfigError(
                "Bungie 未返回完整的护甲实例、属性或插槽组件，已停止配装计算。"
            )

        candidates: list[dict] = []
        for raw in vault_items:
            info = self._manifest.get_item_info(raw.get("itemHash", 0))
            if not isinstance(info, dict):
                if raw.get("itemInstanceId"):
                    raise ConfigError("仓库实例缺少 Manifest 定义，已停止配装计算。")
                continue
            bucket = raw.get("bucketHash")
            if bucket == 138197802:
                bucket = info.get("bucketTypeHash")
            if bucket not in ARMOR_SLOT_MAP:
                continue
            item_class = info.get("classType", -1)
            if target_class >= 0 and item_class in {0, 1, 2} and item_class != target_class:
                continue
            candidates.append(raw)

        for char_id, char_info in chars.items():
            if target_class >= 0 and char_info.get("classType") != target_class:
                continue
            candidates.extend(inventories[char_id]["items"])
            candidates.extend(equipment[char_id]["items"])

        incomplete = 0
        for raw in candidates:
            info = self._manifest.get_item_info(raw.get("itemHash", 0))
            bucket = raw.get("bucketHash")
            if bucket == 138197802 and isinstance(info, dict):
                bucket = info.get("bucketTypeHash")
            if bucket not in ARMOR_SLOT_MAP:
                continue
            instance_id = str(raw.get("itemInstanceId") or "")
            instance_data = instances.get(instance_id)
            stat_data = stats.get(instance_id)
            socket_data = sockets.get(instance_id)
            if (
                instance_id in {"", "0"}
                or not isinstance(info, dict)
                or not isinstance(instance_data, dict)
                or not instance_data
                or not isinstance(stat_data, dict)
                or not isinstance(stat_data.get("stats"), dict)
                or any(
                    not isinstance(
                        (stat_data["stats"].get(str(stat_hash)) or stat_data["stats"].get(stat_hash) or {}).get("value"),
                        int,
                    )
                    for stat_hash in STAT_HASH_TO_NAME
                )
                or not isinstance(socket_data, dict)
                or not isinstance(socket_data.get("sockets"), list)
                or not socket_data["sockets"]
            ):
                incomplete += 1
                continue
            for socket in socket_data["sockets"]:
                plug_hash = socket.get("plugHash", 0)
                if plug_hash and not isinstance(self._manifest.get_item_definition(plug_hash), dict):
                    incomplete += 1
                    break

        if incomplete:
            raise ConfigError(
                f"有 {incomplete} 件候选护甲缺少实例、属性或插槽数据，"
                "已停止配装计算，避免把缺失值当成 0。"
            )


def require_complete_inventory_components(profile: dict) -> None:
    """Require the inventory containers needed for an account-wide conclusion."""
    chars = profile.get("characters", {}).get("data")
    vault_items = profile.get("profileInventory", {}).get("data", {}).get("items")
    inventories = profile.get("characterInventories", {}).get("data")
    equipment = profile.get("characterEquipment", {}).get("data")
    if (
        not isinstance(chars, dict)
        or not chars
        or not isinstance(vault_items, list)
        or not isinstance(inventories, dict)
        or not isinstance(equipment, dict)
        or any(
            not isinstance(component.get(char_id, {}).get("items"), list)
            for char_id in chars
            for component in (inventories, equipment)
        )
    ):
        raise ConfigError(
            "Bungie 库存组件不完整，无法可靠判断账号库存；请稍后重试。"
        )


def looks_like_missing_inventory_scope(profile: dict) -> bool:
    """Detect OAuth tokens that only return public equipped items.

    When ReadDestinyInventoryAndVault is missing, Bungie can still return
    characterEquipment while profileInventory and characterInventories are
    empty. Treating that as an empty Vault would be misleading.
    """
    profile_items = (
        profile.get("profileInventory", {})
        .get("data", {})
        .get("items", [])
    )
    character_inventory = profile.get("characterInventories", {}).get("data", {})
    character_equipment = profile.get("characterEquipment", {}).get("data", {})

    if not isinstance(profile_items, list):
        profile_items = []
    if not isinstance(character_inventory, dict):
        character_inventory = {}
    if not isinstance(character_equipment, dict):
        character_equipment = {}

    inventory_count = len(profile_items) + sum(
        len(payload.get("items", []))
        for payload in character_inventory.values()
        if isinstance(payload, dict)
    )
    equipment_count = sum(
        len(payload.get("items", []))
        for payload in character_equipment.values()
        if isinstance(payload, dict)
    )

    return inventory_count == 0 and equipment_count > 0
