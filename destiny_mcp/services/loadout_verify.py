"""装备回读核对：**写完之后，账号上到底是不是要的那套**。

搬出 `loadout_equipment_service.py` 的原因和其他 mixin 一样 —— 那个文件贴着体量上限，
而"回读核对"本来就不属于"执行"：它是**证据**，两边（`_equip_local_unlocked` 的 Step 4 与
`equip_with_recovery` 的外层）都要用同一份判据，放在执行流程里就迟早会各写一份。

以模块函数挂在 `owner`（`LoadoutEquipmentService`）上取 `_resolver` / `_manifest`：
`loadout_armor_state` / `loadout_verify` 这一族都是这个写法。

**判据只回答"是不是要的那套"**：`True`/`False`，不解释为什么。写入有 3～10 秒同步窗口
（见 `write_readback`），所以"读到 False"不等于"没写进去" —— 那个区分由调用方做：
`read_until` 重试够久了才轮到它下结论。
"""

from __future__ import annotations

from typing import Any

from ..models import Loadout, LoadoutItem
from .item_parser import parse_items_from_profile
from . import profile_components


async def verify_loadout(owner: Any, player_name: str, loadout: Loadout) -> bool:
    """逐项核对：装备实例在身上、模组/功能模组插槽对得上、子职业配置对得上。"""
    p = await owner._resolver.resolve_player(player_name)
    mid, mtype = p["membership_id"], p["membership_type"]
    char_id = await owner._resolver.resolve_character_id(
        mid, mtype, loadout.character
    )
    profile = await owner._resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
    equipped = (
        profile.get("characterEquipment", {})
        .get("data", {})
        .get(char_id, {})
        .get("items", [])
    )
    equipped_ids = _equipped_instance_ids(profile)
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
                if (owner._manifest.get_item_info(raw.get("itemHash", 0)) or {}).get(
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


def _equipped_instance_ids(profile: dict) -> set[str]:
    """**全角色**正穿着的实例号。

    取全角色而不是只看目标角色：`_restore_exact_state` 要把件搬回"原来的角色"并核对它确实
    又穿上了，而目标角色之外的穿着状态同样算数（一件东西不可能同时穿在两个角色身上）。
    """
    return {
        str(raw.get("itemInstanceId", ""))
        for equipment in (
            profile.get("characterEquipment", {}).get("data", {}).values()
        )
        for raw in equipment.get("items", [])
    }


async def verify_restored_items(
    owner: Any, player_name: str, target_states: dict[str, LoadoutItem]
) -> bool:
    """回滚核对：候选件是否回到原位、原位该穿着的又穿上了、插槽也回到记录的样子。"""
    p = await owner._resolver.resolve_player(player_name)
    mid, mtype = p["membership_id"], p["membership_type"]
    profile = await owner._resolver.get_profile(
        mid, mtype, profile_components.INVENTORY_SOCKETS
    )
    current_items = {
        item.item_instance_id: item
        for item in parse_items_from_profile(profile, owner._manifest)
    }
    equipped_ids = _equipped_instance_ids(profile)
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
