"""把配装里的东西搬到角色身上：**严格串行**，全部搬完才做批量装备。

（首行以前写的是"有界并发"，而实现从 2026-09-23 起就是顺序搬 —— 同段 bullet 里也写着"只能
顺序搬"，两句话自相矛盾，改文档对齐实现。）

搬出 `loadout_equipment_service.py` 的原因与其它 mixin 一样：那个文件贴着体积上限（520 行），
而这一段有自己的语义 ——

- **只能顺序搬**：账号写入走 `account_action_lock` 的 `ReentrantAsyncLock`，它**按 task 可重入** ——
  把搬运拆成多个 task 并发，每个都要抢同一把锁，而持锁的正是等它们的外层任务 → 死锁。
  2026-09-23 真机实测：并发版卡死 7 分钟、零写入（日志停在 equip_build 的快照之后）。
  要并发得先改那把锁的语义，而那是账号写入的安全边界 —— 计划里"不并发账号写入"这条不动；
- **顺序不能动**：必须"先都搬过来、再一起装"，装异域时逐件装会在旧异域还穿着的时候失败；
- 回执按传入顺序写，调用方读 `steps` 时对得上。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ItemNotFoundError, TransferError
from .make_room import is_no_room_error
from ..manifest import class_type_name
from ..utils.hash_utils import to_signed
from .item_parser import armor_slot_from_bucket
from ..build.constants import SOLVER_SLOT_TO_LOADOUT
from ..models import Loadout, LoadoutItem, MoveItemStep


class TransferStepMixin:
    """`_equip_local_unlocked` 的第一步：搬运（**顺序**，见模块 docstring）+ 逐件回执。"""

    async def transfer_loadout_items(
        self, player_name: str, loadout: Loadout
    ) -> tuple[list[MoveItemStep], list[str], bool]:
        """把 `loadout.items` 搬到 `loadout.character`。

        返回 `(steps, transferred_ids, all_ok)`：`steps` 与 `loadout.items` 中**有实例 ID 的**
        那部分一一对应且同序；`all_ok=False` 表示至少一件没搬成功（调用方据此提前收手）。
        """
        steps: list[MoveItemStep] = []
        all_ok = True
        moved: list[LoadoutItem] = []
        for item in loadout.items:
            if not item.item_instance_id:
                steps.append(MoveItemStep(
                    action="skip",
                    detail=f"跳过 '{item.name}'（无实例 ID）",
                    success=False,
                ))
                all_ok = False
            else:
                moved.append(item)

        async def _transfer_one(item: LoadoutItem) -> tuple[list[MoveItemStep], bool]:
            """搬一件；撞目标格满就腾一件再搬一次（ADR-029 P4）。"""
            try:
                result = await self._transfer.transfer_item(
                    player_name, item.item_instance_id, loadout.character,
                )
                return ([MoveItemStep(
                    action="transfer",
                    detail=f"转移 '{item.name}' → {loadout.character}",
                    success=result.success,
                )], bool(result.success))
            except (ItemNotFoundError, TransferError) as exc:
                failed = MoveItemStep(
                    action="error",
                    detail=f"'{item.name}' 装备失败: {exc}",
                    success=False,
                )
                if not is_no_room_error(exc) or not item.item_instance_id:
                    return [failed], False
                room_steps, _note = await self._transfer.make_room_in_bucket(
                    player_name, loadout.character, for_instance_id=item.item_instance_id
                )
                if not room_steps:
                    return [failed], False
                try:
                    retried = await self._transfer.transfer_item(
                        player_name, item.item_instance_id, loadout.character,
                    )
                except (ItemNotFoundError, TransferError) as retry_exc:
                    return [failed] + room_steps + [MoveItemStep(
                        action="error",
                        detail=f"'{item.name}' 重试仍失败: {retry_exc}",
                        success=False,
                    )], False
                return [failed] + room_steps + [MoveItemStep(
                    action="transfer",
                    detail=f"转移 '{item.name}' → {loadout.character}（腾格后重试）",
                    success=retried.success,
                )], bool(retried.success)

        transferred_ids: list[str] = []
        for item in moved:
            item_steps, ok = await _transfer_one(item)
            steps += item_steps
            if ok:
                transferred_ids.append(str(item.item_instance_id))
            else:
                all_ok = False
        return steps, transferred_ids, all_ok

    async def move_single_to_vault(self, player_name: str, armor) -> None:
        """把**一件**护甲搬进仓库（自动腾格用；ADR-029）。

        `transfer_item` 的目标是"某个角色名"或 `vault`（见 `TransferService`）——这里固定给
        `vault`。失败或搬不动都**抛给调用方**：腾格是"要么腾成、要么如实说没腾成"，
        不许静默跳过（换下一件会让"到底动了什么"说不清）。
        """
        if not armor.item_instance_id:
            raise ItemNotFoundError(f"'{armor.name}' 没有实例 ID，搬不动")
        result = await self._transfer.transfer_item(
            player_name, armor.item_instance_id, "vault",
        )
        if not result.success:
            raise TransferError(
                f"把 '{armor.name}' 搬进仓库失败：{result.message or '上游没给原因'}"
            )


#: 金装在 Manifest 里的 `inventory.tierType`（与 build/models 的 `tier` 同一口径）。
_TIER_EXOTIC = 6

#: 护甲部位键（`InventoryItem.slot` 用的就是这套）
_ARMOR_SLOT_KEYS = ("helmet", "gauntlets", "chest", "legs", "class_item")


class ExoticDequipMixin:
    """批量装备前**先把冲突的金装顶下来**。

    金装规则是**全身只能穿一件**，而上游的批量 `EquipItems` 撞上冲突会**整批**回 `1641`
    （真机 2026-10-06 实测：配装里的金装头盔 + 身上正穿的金装臂铠 → `equip_many` 全灭）。
    DIM 的做法（`src/app/inventory/item-move-service.ts` 的 "Check for (and move aside) exotics"）是
    发批量**之前**：给它找一件**同部位、非金装**的替身先穿上（`getSimilarItem({excludeExotic: true,
    exclusions})`），找不到替身就明确报"先把那件脱下来"。这里照做。
    """

    def _class_matches(self, item_hash: int, character: str) -> bool:
        """这件能不能给这个职业穿（`classType`：0=泰坦 1=猎人 2=术士 3=任意）。"""
        info = self._manifest.get_item_info(item_hash) or {}
        class_type = int(info.get("classType") or 3)
        if class_type == 3:
            return True
        name = class_type_name(class_type) if class_type in (0, 1, 2) else ""
        return name.lower() == character.lower()

    def _armor_slot_of(self, item: Any) -> str:
        """这件是哪个护甲部位。

        ⚠️ 身上的件 `slot` 已经填好；**仓库里的件是空串**（profile 给的是 `Vault (General)`）——
        2026-10-06 真机：顶下金装时按 `slot` 过滤，把仓库里所有替身都丢了，误报"没得顶"。
        认不出来就按物品定义的 `bucketTypeHash` 查（定义里是**无符号**值，两边都试）。
        """
        if item.slot:
            return item.slot
        info = self._manifest.get_item_info(item.item_hash) or {}
        raw = info.get("bucketTypeHash") or 0
        if not raw:
            return ""
        for candidate in (int(raw), to_signed(int(raw))):
            key = armor_slot_from_bucket(candidate)
            if key:
                return key
        return ""

    def _item_is_exotic(self, item_hash: int) -> bool:
        definition = self._manifest.get_item_definition(item_hash) or {}
        tier = (definition.get("inventory") or {}).get("tierType") or 0
        return int(tier) == _TIER_EXOTIC

    async def dequip_conflicting_exotics(
        self, player_name: str, loadout: Loadout
    ) -> tuple[list[MoveItemStep], bool, str]:
        """`equip_loadout` 的入口。"""
        return await self._dequip_conflicting_exotics(
            player_name,
            loadout.character,
            [(i.slot, i.item_hash, i.item_instance_id) for i in loadout.items],
        )

    async def dequip_conflicting_exotics_for_build(
        self, player_name: str, character: str, build: Any
    ) -> tuple[list[MoveItemStep], bool, str]:
        """`equip_build` 的入口：计划件用的是**求解器槽位名**，先换成护甲槽位键再走同一套判据。

        2026-10-08 真机（豆包那侧）：`equip_build` 撞金装冲突时复检直接拒绝，给出的出路是
        「你自己去 inventory equip 顶下」—— 而这套自动顶下只接在 `equip_loadout` 上。同一条规则
        不该因为入口不同就变成手工活，所以补这个入口（ADR-030）。
        """
        items = [
            (
                SOLVER_SLOT_TO_LOADOUT.get(getattr(item, "slot", ""), getattr(item, "slot", "")),
                getattr(item, "item_hash", 0),
                getattr(item, "item_instance_id", ""),
            )
            for item in (build.items or [])
        ]
        return await self._dequip_conflicting_exotics(player_name, character, items)

    async def _dequip_conflicting_exotics(
        self, player_name: str, character: str, items: list[tuple[str, int, str]]
    ) -> tuple[list[MoveItemStep], bool, str]:
        """返回 `(步骤, 能不能继续批量装备, 话术)`；没有冲突时是 `([], True, "")`。"""
        exotic_slots = {slot for slot, item_hash, _ in items if item_hash and self._item_is_exotic(item_hash)}
        if not exotic_slots:
            return [], True, ""
        worn = await self._transfer.list_character_items(
            player_name, character, include_vault=True
        )
        plan_ids = {iid for _slot, _hash, iid in items if iid}
        steps: list[MoveItemStep] = []
        moved_names: list[str] = []
        for slot in _ARMOR_SLOT_KEYS:
            current = next(
                (i for i in worn if i.slot == slot and i.is_equipped), None
            )
            if current is None or not self._item_is_exotic(current.item_hash):
                continue
            if slot in exotic_slots:
                continue  # 这一格本来就要换成金装（DIM：we aren't already equipping into that slot）
            # 替身：同部位、**非金装**、没穿着、不在本次要装的清单里（DIM 的 excludeExotic + exclusions）
            rivals = [
                i for i in worn
                if self._armor_slot_of(i) == slot
                and not i.is_equipped
                and i.item_instance_id not in plan_ids
                and not self._item_is_exotic(i.item_hash)
                and (i.location != "vault" or self._class_matches(i.item_hash, character))
            ]
            if not rivals:
                return steps, False, (
                    f"「{current.name}」占着全身唯一的金装位（金装全身只能穿一件），而这套配装要穿"
                    f"另一件金装；身上和仓库里都没有能给这个职业顶下它的非金装{current.slot_display or slot}。"
                    f"出路：先把「{current.name}」脱下来，或去弄一件同部位的非金装。"
                )
            # 优先身上那件（不用搬）；都没有才去仓库拉一件（DIM 同款：搬进来再穿）
            on_body = [i for i in rivals if i.location != "vault"]
            replacement = sorted(
                on_body or rivals, key=lambda i: (i.power or 0)
            )[-1]
            if replacement.location == "vault":
                moved = await self._transfer.move_item(
                    player_name, replacement.name, character,
                    item_instance_id=replacement.item_instance_id,
                )
                steps.append(MoveItemStep(
                    action="transfer",
                    detail=f"把顶下用的「{replacement.name}」从仓库搬过来",
                    success=bool(moved.success),
                ))
                if not moved.success:
                    return steps, False, (
                        f"要拿仓库里的「{replacement.name}」顶下「{current.name}」，但搬运失败："
                        f"{moved.message}"
                    )
            equipped = await self._transfer.equip_item(
                player_name, replacement.item_instance_id, character
            )
            steps.append(MoveItemStep(
                action="downgrade",
                detail=f"先穿「{replacement.name}」把「{current.name}」顶下来（金装全身只能穿一件）",
                success=bool(equipped.success),
            ))
            if not equipped.success:
                return steps, False, f"顶下「{current.name}」失败：{equipped.message}"
            moved_names.append(current.name)
        note = f"已先把{'、'.join(f'「{n}」' for n in moved_names)}顶下来（金装全身只能穿一件）。" if moved_names else ""
        return steps, True, note
