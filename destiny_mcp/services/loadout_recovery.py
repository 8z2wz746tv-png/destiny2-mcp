"""配装"执行前状态"的抓取：把当前装备/模组/子职业读成一份可回滚的快照。

以 mixin 挂在 `LoadoutEquipmentService` 上（`self` 上就有 manifest/bungie/resolver）。
搬出 `loadout_equipment_service.py` 的原因：那个文件贴着体量上限，而“抓取/恢复”
与“执行”本来就是两件事。
"""

from __future__ import annotations



from ..exceptions import ItemNotFoundError, TransferError
from ..logging_config import get_logger
from . import profile_components
from ..models import (
    Loadout,
    LoadoutItem,
    LoadoutSubclassConfig,
)
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

