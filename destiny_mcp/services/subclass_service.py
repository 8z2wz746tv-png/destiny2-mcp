"""Subclass service — read and modify subclass ability configuration.

Handles super, melee, grenade, class ability, movement, aspects, and fragments
via Bungie's InsertSocketPlugFree API endpoint.
"""

from __future__ import annotations

import re
import unicodedata

from ..bungie_client import BungieClient
from ..exceptions import SubclassError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import (
    ModifySubclassPlug,
    ModifySubclassResult,
    PlugOption,
    SubclassConfig,
    SubclassPlug,
    SubclassSwitch,
)
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_signed, to_unsigned
from ..vocabulary import (
    CLASS_LABELS_ZH,
    ELEMENT_ALIASES,
    ELEMENT_LABELS_ZH,
    class_key,
    subclass_element_key,
)
from . import profile_components, write_readback
from .account_action_lock import account_action_lock, serialized_account_action

logger = get_logger(__name__)

# Subclass bucket hash — where subclass items live
SUBCLASS_BUCKET_HASH = 3284755031

# Pattern to extract socket type from plugCategoryIdentifier
# Examples: "hunter.solar.supers", "shared.void.grenades", "warlock.arc.aspects"
_CATEGORY_PATTERN = re.compile(
    r"^(?:hunter|warlock|titan|shared)\.\w+\.(.+)$"
)

# 同一个标识串里的**元素**是第二段：`warlock.solar.supers` → solar。
# 上面的 _CATEGORY_PATTERN 抓的是第三段（槽类型），别拿它当元素用。
_ELEMENT_PATTERN = re.compile(
    r"^(?:hunter|warlock|titan|shared)\.(\w+)\."
)

# Map from the regex capture group to our friendly socket type names
_CATEGORY_TO_TYPE: dict[str, str] = {
    "supers": "super",
    "melee": "melee",
    "grenades": "grenade",
    "class_abilities": "class_ability",
    "movement": "movement",
    "aspects": "aspect",
    "fragments": "fragment",
}


def identify_socket_type(plug_category_id: str) -> str:
    """Map a plugCategoryIdentifier to a friendly socket type name.

    Examples:
        'hunter.solar.supers' -> 'super'
        'shared.void.grenades' -> 'grenade'
        'warlock.arc.aspects' -> 'aspect'
    """
    m = _CATEGORY_PATTERN.match(plug_category_id)
    if m:
        return _CATEGORY_TO_TYPE.get(m.group(1), m.group(1))
    return plug_category_id


class SubclassService:
    """Operations for reading and modifying subclass ability configuration."""

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

    def _find_subclass_in_equipment(
        self, profile: dict, character_id: str
    ) -> dict | None:
        """Find the equipped subclass item from character equipment.

        Returns the raw equipment item dict for the subclass, or None.
        """
        equip_data = (
            profile.get("characterEquipment", {})
            .get("data", {})
            .get(character_id, {})
            .get("items", [])
        )
        for item in equip_data:
            if item.get("bucketHash") == SUBCLASS_BUCKET_HASH:
                return item
        return None

    @staticmethod
    def _subclass_items(profile: dict, character_id: str) -> list[dict]:
        """该角色身上的子职业物品（装备位 + 背包，按实例去重）。

        实采：一个角色背包里躺着该职业的**全部**子职业，bucketTypeHash=3284755031、itemType=16，
        正装备的那件同时出现在 205 里 —— 所以 201(角色背包) 是"换子职业"的必要组件，
        只看 205 永远只有一个候选。
        """
        items: list[dict] = []
        seen: set[str] = set()
        for bucket in ("characterEquipment", "characterInventories"):
            for item in (
                profile.get(bucket, {})
                .get("data", {})
                .get(character_id, {})
                .get("items", [])
            ):
                if item.get("bucketHash") != SUBCLASS_BUCKET_HASH:
                    continue
                instance = str(item.get("itemInstanceId", ""))
                if instance in seen:
                    continue
                seen.add(instance)
                items.append(item)
        return items

    def _element_of(self, profile: dict, instance_id: str) -> str:
        """从已装 plug 的分类标识读元素（`warlock.solar.supers` → `solar`）。

        子职业物品的**定义里没有元素**：实采 18 件子职业物品，`defaultDamageType` 全是 0、
        没有 element 字段、`socketEntries[].plugCategoryIdentifier` 也是空的。
        元素只写在 plug 自己的 `plugCategoryIdentifier` 上，所以"按定义猜元素"的写法一律不成立。
        """
        sockets = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
            .get(instance_id, {})
            .get("sockets", [])
        )
        for socket in sockets:
            plug_hash = socket.get("plugHash")
            if not plug_hash:
                continue
            category = (
                self._manifest.get_plug_category_identifier(to_signed(plug_hash)) or ""
            )
            match = _ELEMENT_PATTERN.match(category)
            if not match:
                continue
            key = ELEMENT_ALIASES.get(match.group(1), "")
            if key:
                return key
        return ""

    def _subclass_name(self, item_hash: int) -> str:
        definition = self._manifest.get_item_definition(item_hash) or {}
        return (definition.get("displayProperties") or {}).get("name") or (
            self._manifest.get_item_name(item_hash) or f"#{item_hash}"
        )

    async def _switch_subclass(
        self,
        requested: str,
        current: SubclassConfig,
        membership_id: str,
        membership_type: int,
        character_id: str,
        class_type: str,
    ) -> SubclassSwitch:
        """换子职业物品：解析叫法 → 在这个角色身上找目标物品 → 装备 → 回读核对。

        **只做换物品这一件事**。插槽变更由调用方在本方法成功之后重读配置再做 ——
        槽索引属于具体那件物品，拿旧物品的索引去写新物品是错的。
        """
        element_key, declared_class = subclass_element_key(requested)
        record = SubclassSwitch(requested=requested, from_name=current.subclass_name)
        # class_type 是 `resolve_character_name` 给的英文展示名（"Warlock"），比之前先归一到键，
        # 话术用中文（用户看的是中文，不是 "Hunter"）
        class_hint = class_key(class_type)
        class_label = CLASS_LABELS_ZH.get(class_hint, class_type_name(class_type))

        profile = await self._resolver.get_profile(
            membership_id, membership_type, profile_components.SUBCLASS
        )
        items = self._subclass_items(profile, character_id)
        by_instance = {str(i.get("itemInstanceId", "")): i for i in items}
        names = {
            instance: self._subclass_name(int(item.get("itemHash", 0)))
            for instance, item in by_instance.items()
        }
        elements = {instance: self._element_of(profile, instance) for instance in by_instance}
        record.from_element = elements.get(current.item_instance_id, "")

        # 「火术」是术士的叫法：目标角色不是术士时**报错**，不能悄悄切了别的职业的子职业
        if declared_class and class_hint and declared_class != class_hint:
            record.message = (
                f"「{requested}」是{CLASS_LABELS_ZH[declared_class]}的叫法，"
                f"这个角色是{class_label}。"
                "可以直接说元素（"
                + "/".join(ELEMENT_LABELS_ZH.values())
                + "），或者说该角色子职业的官方名。"
            )
            return record

        target_instance = ""
        if element_key:
            target_instance = next(
                (i for i, element in elements.items() if element == element_key), ""
            )
        else:
            # 官方名（破晓/枪手/炎阳……）：名字的权威来源是 Manifest，只在**这个角色自己的**
            # 子职业里精确匹配；不做模糊匹配、不查拼音 —— 猜错比报错更坑
            wanted = unicodedata.normalize("NFKC", requested).strip().casefold()
            target_instance = next(
                (
                    i
                    for i, name in names.items()
                    if unicodedata.normalize("NFKC", name).strip().casefold() == wanted
                ),
                "",
            )

        if not target_instance:
            available = "、".join(
                f"{names[i]}（{ELEMENT_LABELS_ZH.get(elements[i], '元素未知')}）"
                for i in by_instance
            )
            record.message = (
                f"在{class_label}身上找不到「{requested}」对应的子职业。"
                f"这个角色现在有：{available}。"
            )
            return record

        target_hash = int(by_instance[target_instance].get("itemHash", 0))
        record.item_hash = target_hash
        record.item_instance_id = target_instance
        record.to_name = names[target_instance]
        record.to_element = elements[target_instance]

        # 幂等：已经是目标子职业就一件都不写（白写一次 equip 会让插槽数据白白失效）
        if target_instance == current.item_instance_id:
            record.success = True
            record.message = (
                f"当前就是{record.to_name}"
                f"（{ELEMENT_LABELS_ZH.get(record.to_element, '')}），不用换。"
            )
            return record

        result = await self._bungie.equip_item(
            item_instance_id=target_instance,
            character_id=character_id,
            membership_type=membership_type,
        )
        if result.get("ErrorCode", 0) != 1:
            record.message = (
                f"换上「{record.to_name}」失败：{result.get('Message', '上游没给原因')}"
            )
            return record

        # 回读核对：装备位换到目标实例才算成功（假成功比失败更糟）。
        # 上游 profile 有"刚写完还读到旧值"的窗口（真机实采约 3 秒），所以要重试几次再下结论。
        def _equipped_instance(profile: dict) -> str:
            return str(
                (self._find_subclass_in_equipment(profile, character_id) or {}).get(
                    "itemInstanceId", ""
                )
            )

        fresh = await write_readback.read_until(
            lambda: self._resolver.get_profile(
                membership_id, membership_type, profile_components.SUBCLASS
            ),
            lambda profile: _equipped_instance(profile) == target_instance,
        )
        if _equipped_instance(fresh) != target_instance:
            equipped = self._find_subclass_in_equipment(fresh, character_id) or {}
            window = int(write_readback.ATTEMPTS * write_readback.DELAY_SECONDS)
            record.unverified = True
            record.message = (
                f"「{record.to_name}」的写入上游返回成功，但 {window} 秒内回读装备位仍是 "
                f"#{equipped.get('itemHash', 0)}（实例 {equipped.get('itemInstanceId', '空')}）；"
                "上游 profile 同步有延迟，这次**没确认**，"
                '别重复写，先用 intent="get" 重新读一次看看到底换没换。'
            )
            return record

        record.success = True
        record.message = (
            f"已换上{record.to_name}"
            f"（{ELEMENT_LABELS_ZH.get(record.to_element, '')}），原为{record.from_name}。"
        )
        return record

    def _get_plug_info(self, plug_hash: int) -> dict:
        """Get plug item info from manifest, handling unsigned hash conversion."""
        # Try direct lookup first
        info = self._manifest.get_item_info(plug_hash)
        if info:
            return info
        # Try unsigned→signed conversion
        signed = to_signed(plug_hash)
        info = self._manifest.get_item_info(signed)
        if info:
            return info
        return {
            "name": f"#{plug_hash}",
            "itemType": 0,
            "itemTypeName": "Unknown",
        }

    def _get_plug_name(self, plug_hash: int) -> str:
        """Get plug name from manifest."""
        info = self._get_plug_info(plug_hash)
        return info.get("name", f"#{plug_hash}")

    def _classify_socket(
        self,
        socket_data: dict,
        socket_def: dict,
    ) -> str:
        """Determine the socket type from the socket definition and current plug.

        Looks at the currently equipped plug's plugCategoryIdentifier first,
        then falls back to reusable plugs from the socket definition.
        """
        # Check the currently active plug
        current_plug_hash = socket_data.get("plugHash")
        if current_plug_hash:
            signed_hash = to_signed(current_plug_hash)
            # Query manifest directly for the plug's category identifier
            info = self._manifest.get_plug_category_identifier(signed_hash)
            if info:
                return identify_socket_type(info)

        # Fallback: look at reusable plugs to identify the category
        reusable = socket_data.get("reusablePlugs", [])
        for plug in reusable:
            ph = plug.get("plugItemHash")
            if ph:
                signed_hash = to_signed(ph)
                info = self._manifest.get_plug_category_identifier(signed_hash)
                if info:
                    return identify_socket_type(info)

        return "unknown"

    # ── Public API ──────────────────────────────────────────────────

    async def get_subclass(
        self, player_name: str, character: str
    ) -> SubclassConfig:
        """Read a character's current subclass configuration.

        Returns the subclass name and all configured plugs (super, melee,
        grenade, class ability, movement, aspects, fragments).

        Raises:
            PlayerNotFoundError, CharacterNotFoundError, SubclassError.
        """
        logger.info(
            "Getting subclass config: player=%s character=%s",
            player_name, character,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_type = resolve_character_name(character)

        # Fetch profile with equipment + socket data
        # 200=Characters, 205=CharacterEquipment, 305=ItemSockets；201 只有"换子职业"用得上，
        # 但读和写共用同一个集合 —— 不给"差不多但不一样"的第二份组件表留口子（改一处就够）
        profile = await self._resolver.get_profile(
            mid, mtype, profile_components.SUBCLASS
        )

        # Find equipped subclass
        subclass_item = self._find_subclass_in_equipment(profile, char_id)
        if not subclass_item:
            raise SubclassError(
                "Get subclass",
                f"No subclass equipped on {class_type_name(class_type)}.",
            )

        subclass_hash = subclass_item["itemHash"]
        subclass_name = self._manifest.get_item_name(subclass_hash)
        subclass_instance_id = str(subclass_item.get("itemInstanceId", "0"))

        # Read socket data
        sockets_data = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
            .get(subclass_instance_id, {})
            .get("sockets", [])
        )

        # Read the subclass definition from manifest for socket type hashes
        subclass_def = self._manifest.get_item_definition(subclass_hash)
        socket_entries = (
            subclass_def.get("sockets", {}).get("socketEntries", [])
            if subclass_def else []
        )

        plugs: list[SubclassPlug] = []
        for i, socket_data in enumerate(sockets_data):
            current_plug_hash = socket_data.get("plugHash")
            if not current_plug_hash:
                continue

            # Get the socket definition if available
            socket_def = socket_entries[i] if i < len(socket_entries) else {}

            # Identify socket type
            socket_type = self._classify_socket(socket_data, socket_def)

            plug_name = self._get_plug_name(current_plug_hash)

            # Get all available options from the plug set
            available: list[PlugOption] = []
            plug_set_hash = socket_def.get("reusablePlugSetHash")
            if plug_set_hash:
                plug_set_plugs = self._manifest.get_plug_set_plugs(plug_set_hash)
                if plug_set_plugs:
                    available = [
                        PlugOption(plug_hash=p["plugItemHash"], name=p["name"])
                        for p in plug_set_plugs
                    ]

            plugs.append(
                SubclassPlug(
                    plug_hash=current_plug_hash,
                    name=plug_name,
                    socket_index=i,
                    socket_type=socket_type,
                    is_active=socket_data.get("isEnabled", True),
                    available=available,
                )
            )

        logger.info(
            "Subclass config for %s/%s: %s with %d plugs",
            player_name, character, subclass_name, len(plugs),
        )
        return SubclassConfig(
            character_id=char_id,
            character_class=class_type_name(class_type),
            subclass_name=subclass_name,
            subclass_hash=subclass_hash,
            item_instance_id=subclass_instance_id,
            plugs=plugs,
        )

    @serialized_account_action
    async def modify_subclass(
        self,
        player_name: str,
        character: str,
        changes: dict[str, str],
    ) -> ModifySubclassResult:
        """Modify a character's subclass configuration.

        Args:
            player_name: Bungie name.
            character: Character name (hunter/warlock/titan or Chinese).
            changes: Dict mapping socket type to target plug name.
                     Keys: super, melee, grenade, class_ability, movement,
                     aspect, fragment.
                     Values: Plug name in Chinese or English (fuzzy matched).

        Returns:
            ModifySubclassResult with per-change results.

        Raises:
            PlayerNotFoundError, CharacterNotFoundError, SubclassError.
        """
        logger.info(
            "Modifying subclass: player=%s character=%s changes=%s",
            player_name, character, changes,
        )
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        class_type = resolve_character_name(character)

        # First, read current subclass config
        config = await self.get_subclass(player_name, character)

        # 换子职业（`changes={"subclass": "烈日"}`）：**先换物品、回读，再改槽**——
        # 槽索引属于具体那件物品，拿旧物品的索引去写新物品是错的（计划文档 P2 的顺序要求）。
        switch_request = next(
            (value for key, value in changes.items() if key.strip().casefold() == "subclass"),
            None,
        )
        switch_record: SubclassSwitch | None = None
        if switch_request is not None:
            switch_record = await self._switch_subclass(
                switch_request, config, mid, mtype, char_id, class_type
            )
            if not switch_record.success:
                # 换不过去就不要接着改槽：那份 changes 是按换之前的子职业算出来的
                return ModifySubclassResult(
                    success=False,
                    character=class_type_name(class_type),
                    subclass_name=config.subclass_name,
                    subclass_switch=switch_record,
                    message=switch_record.message,
                )
            if switch_record.item_instance_id != config.item_instance_id:
                config = await self.get_subclass(player_name, character)

        # Build maps for socket lookup:
        # - single sockets: type → plug (super, melee, grenade, class_ability, movement)
        # - multi sockets: type → [plug, ...] (aspects, fragments)
        current_by_type: dict[str, SubclassPlug] = {}
        multi_by_type: dict[str, list[SubclassPlug]] = {}
        for plug in config.plugs:
            if plug.socket_type == "unknown":
                continue
            if plug.socket_type in ("aspect", "fragment"):
                multi_by_type.setdefault(plug.socket_type, []).append(plug)
            else:
                current_by_type[plug.socket_type] = plug

        plug_results: list[ModifySubclassPlug] = []

        for target_key, target_name in changes.items():
            normalized_key = target_key.strip().lower()
            if normalized_key == "subclass":
                continue  # 已在上面换过物品，它不是插槽

            # Parse the target: "super" → type=super, index=0
            # "aspect_2" → type=aspect, index=1 (0-based)
            # "fragment_3" → type=fragment, index=2
            if "_" in normalized_key and normalized_key[-1].isdigit():
                base_type = normalized_key.rsplit("_", 1)[0]
                slot_num = int(normalized_key.rsplit("_", 1)[1]) - 1  # 0-based
            else:
                base_type = normalized_key
                slot_num = 0

            # Find the current plug
            if base_type in ("aspect", "fragment"):
                plugs_list = multi_by_type.get(base_type, [])
                if slot_num >= len(plugs_list):
                    plug_results.append(
                        ModifySubclassPlug(
                            socket_type=target_key,
                            socket_index=-1,
                            new_plug_name=target_name,
                            new_plug_hash=0,
                            success=False,
                            message=f"Slot '{target_key}' not found on this subclass (only {len(plugs_list)} {base_type} slot(s)).",
                        )
                    )
                    continue
                current = plugs_list[slot_num]
            else:
                current = current_by_type.get(base_type)
            if not current:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=-1,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"Socket type '{base_type}' not found on this subclass.",
                    )
                )
                continue

            # Search for the target plug in the manifest
            search_results = self._manifest.search(target_name, limit=10)
            if not search_results:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"Plug '{target_name}' not found in manifest.",
                    )
                )
                continue

            # Find the best match that's a valid plug for this socket type
            target_plug_hash = None
            target_plug_name = ""
            for result in search_results:
                signed_hash = to_signed(result["itemHash"])
                cat_id = self._manifest.get_plug_category_identifier(signed_hash)
                if cat_id:
                    identified_type = identify_socket_type(cat_id)
                    if identified_type == base_type:
                        target_plug_hash = to_unsigned(result["itemHash"])
                        target_plug_name = result["name"]
                        break

            if not target_plug_hash:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_name,
                        new_plug_hash=0,
                        success=False,
                        message=f"'{target_name}' is not a valid {base_type} for this subclass.",
                    )
                )
                continue

            # Check if already equipped
            if target_plug_hash == current.plug_hash:
                plug_results.append(
                    ModifySubclassPlug(
                        socket_type=target_key,
                        socket_index=current.socket_index,
                        old_plug_name=current.name,
                        new_plug_name=target_plug_name,
                        new_plug_hash=target_plug_hash,
                        success=True,
                        message=f"'{target_plug_name}' is already equipped.",
                    )
                )
                continue

            # Call InsertSocketPlugFree
            result = await self._bungie.insert_socket_plug_free(
                item_instance_id=config.item_instance_id,
                plug_item_hash=target_plug_hash,
                socket_index=current.socket_index,
                socket_array_type=0,
                character_id=char_id,
                membership_type=mtype,
            )

            ok = result.get("ErrorCode", 0) == 1
            plug_results.append(
                ModifySubclassPlug(
                    socket_type=target_key,
                    socket_index=current.socket_index,
                    old_plug_name=current.name,
                    new_plug_name=target_plug_name,
                    new_plug_hash=target_plug_hash,
                    success=ok,
                    message=(
                        f"Changed {base_type}: '{current.name}' → '{target_plug_name}'"
                        if ok
                        else f"Failed: {result.get('Message', 'Unknown error')}"
                    ),
                )
            )

            if ok:
                logger.info(
                    "Changed %s: '%s' → '%s' on %s/%s",
                    target_key, current.name, target_plug_name,
                    player_name, character,
                )

        all_ok = all(r.success for r in plug_results)
        message = (
            f"All changes applied to {config.subclass_name}."
            if all_ok
            else f"Some changes failed on {config.subclass_name}."
        )
        if switch_record:
            message = f"{switch_record.message} {message}"
        return ModifySubclassResult(
            success=all_ok,
            character=class_type_name(class_type),
            subclass_name=config.subclass_name,
            subclass_switch=switch_record,
            changes=plug_results,
            message=message,
        )
