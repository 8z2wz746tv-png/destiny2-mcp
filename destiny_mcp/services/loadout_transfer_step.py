"""把配装里的东西搬到角色身上：**有界并发**，全部搬完才做批量装备。

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

from ..exceptions import ItemNotFoundError, TransferError
from ..models import Loadout, LoadoutItem, MoveItemStep


class TransferStepMixin:
    """`_equip_local_unlocked` 的第一步：搬运（并发）+ 逐件回执。"""

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

        async def _transfer_one(item: LoadoutItem) -> tuple[MoveItemStep, bool]:
            try:
                result = await self._transfer.transfer_item(
                    player_name, item.item_instance_id, loadout.character,
                )
                return (
                    MoveItemStep(
                        action="transfer",
                        detail=f"转移 '{item.name}' → {loadout.character}",
                        success=result.success,
                    ),
                    bool(result.success),
                )
            except (ItemNotFoundError, TransferError) as exc:
                return (
                    MoveItemStep(
                        action="error",
                        detail=f"'{item.name}' 装备失败: {exc}",
                        success=False,
                    ),
                    False,
                )

        transferred_ids: list[str] = []
        for item in moved:
            step, ok = await _transfer_one(item)
            steps.append(step)
            if ok:
                transferred_ids.append(str(item.item_instance_id))
            else:
                all_ok = False
        return steps, transferred_ids, all_ok
