"""回滚的**位置**那一半：把候选件放回执行前的位置，原先穿着的再穿回去。

搬出 `loadout_recovery.py` 的原因和这一族其它拆分一样（那边贴着体量上限），但这两件事本来
也不该在一个函数里：**模组现场**是"这一件上装着什么"，**位置**是"这一件在哪" ——
真机 2026-10-03 复测里，前者逐颗重写慢了几分钟、后者一次没出错，两边的取舍完全不同。

以模块函数挂在 `owner`（`LoadoutEquipmentService`）上取 `_transfer` / `_bungie`，
与 `loadout_armor_state` / `loadout_verify` 同一写法。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ItemNotFoundError, TransferError, describe_exception
from ..logging_config import get_logger
from ..models import LoadoutItem, MoveItemStep

logger = get_logger(__name__)

#: 认得出"放回哪"的位置（与 `LoadoutItem.source_location` 的取值域一致）。
_LOCATIONS = {"vault", "hunter", "warlock", "titan"}


async def restore_locations(
    owner: Any,
    player_name: str,
    attempted_character: str,
    target_states: dict[str, LoadoutItem],
    steps: list[MoveItemStep],
) -> bool:
    """逐件放回原位；返回是否全部成功（失败的都写进 `steps`，不抛）。"""
    membership_type = (
        await owner._resolver.resolve_player(player_name)
    )["membership_type"]
    all_ok = True
    for original in target_states.values():
        destination = original.source_location
        if destination not in _LOCATIONS:
            steps.append(MoveItemStep(
                action="rollback_location",
                detail=f"无法确定 '{original.name}' 的原始位置。",
                success=False,
            ))
            all_ok = False
            continue
        if destination == attempted_character:
            continue
        try:
            moved = await owner._transfer.transfer_item(
                player_name,
                original.item_instance_id,
                destination,
                to_character_id=(original.source_character_id or None),
            )
            steps.append(MoveItemStep(
                action="rollback_location",
                detail=f"恢复 '{original.name}' 到 {destination}",
                success=moved.success,
            ))
            all_ok = all_ok and moved.success
            if moved.success and original.was_equipped and destination != "vault":
                if original.source_character_id:
                    response = await owner._bungie.equip_item(
                        original.item_instance_id,
                        original.source_character_id,
                        membership_type,
                    )
                    equipped_ok = response.get("ErrorCode", 0) == 1
                else:
                    equipped = await owner._transfer.equip_item(
                        player_name,
                        original.item_instance_id,
                        destination,
                    )
                    equipped_ok = equipped.success
                steps.append(MoveItemStep(
                    action="rollback_equip",
                    detail=f"重新装备 '{original.name}' 到 {destination}",
                    success=equipped_ok,
                ))
                all_ok = all_ok and equipped_ok
        except (ItemNotFoundError, TransferError) as exc:
            logger.error("Failed to restore location for %s: %s", original.name, exc)
            steps.append(MoveItemStep(
                action="rollback_location", detail=describe_exception(exc), success=False
            ))
            all_ok = False
    return all_ok
