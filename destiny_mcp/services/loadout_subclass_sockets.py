"""子职业插槽：读取当前配置、查找兼容插槽并写入。

以 mixin 挂在 LoadoutEquipmentService 上，通过 self 使用 manifest / bungie / resolver。
"""

from __future__ import annotations

import aiobungie

from ..models import Loadout, LoadoutSubclassConfig, MoveItemStep
from .loadout_plug_lookup import PlugLookupMixin
from .subclass_service import identify_socket_type


class SubclassSocketMixin(PlugLookupMixin):
    """子职业一侧的读取与应用。"""

    _PLUG_CAT_SUPER = 3854839014
    _PLUG_CAT_ABILITIES = 3847589792  # grenade/melee/class_ability/movement
    _PLUG_CAT_ASPECTS = 3472571033
    _PLUG_CAT_FRAGMENTS = 2736821379

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
