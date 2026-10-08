# ADR-030: 批量装备前先顶下冲突金装（照 DIM 的 move aside exotics）

- Status: accepted
- Date: 2026-10-06
- Decision By: maintainer
- Scope: `services/loadout_transfer_step.py`（`ExoticDequipMixin`）、`services/loadout_equipment_service.py`（批量装备之前那一步）、`services/transfer_service.py`（`list_character_items` / `make_room_in_bucket` 的桶与部位判定）

## Context

金装规则是**全身只能穿一件**（异域武器一件 + 异域护甲一件，两类互不冲突）。而上游的**批量**
`EquipItems` 撞上冲突时不做任何补救：整个批次回 `1641`。

真机 2026-10-06 原文（`equip_loadout` 应用一套含异域头盔的配装，而角色正穿着异域臂铠）：

```
equip_many: 批量装备失败：以下物品未成功装备或缺少有效结果：阿罕卡拉之颅 (equipStatus=1641)。
```

后果不是"一件没装上"这么轻：搬运已经做完，只能整条回滚；而且**回滚链自己也可能核对不上**
（同一天实测到一次 `执行前状态恢复验证失败`，需要逐件手工装回）。单件 `equip` 路径**早就有**
顶下逻辑（回执行里的 `action="downgrade"`），差距只在批量路径。

DIM v8.143.0 有这套逻辑，位置与做法（`src/app/inventory/item-move-service.ts`）：

- `equipItems` 发批量**之前**先 *"Check for (and move aside) exotics"*：用
  `getOtherExoticThatNeedsDequipping(i)` 找出身上**别的部位**的另一件金装；
- 替身用 `getSimilarItem(..., { excludeExotic: true, exclusions })` 选 —— 注释写明
  *"Don't pick an exotic to equip in this item's place (because we're specifically trying to dequip an exotic)"*，
  `exclusions` = 本次要装的件；
- 注释 *"Callers (loadout-apply) are responsible for moving them into place first, **including de-equip
  replacements pulled from the vault**"* —— 替身**允许从仓库拉**，拉来先搬进角色身上；
- 找不到替身时 `throw new DimError('ItemService.Deequip', …)`：**不发批量**，明确告诉你先脱哪件。

**What changed**：这条在 ADR-029 之后才做（029 管"格满"，本条管"金装冲突"）。落地时真机上又暴露了
两个只有真机才看得见的形状问题：仓库里的件在 profile 里 `slot` 是**空串**、`bucket_type` 是
`Vault (General)`（都不是它真正的装备桶），所以"同部位"必须按**物品定义**的 `bucketTypeHash` 判。

## Decision

**在 `equip_loadout` 的批量装备之前，先按 DIM 的规则顶下冲突金装**：

1. 只有**配装里有金装**时才检查（`inventory.tierType == 6`）。
2. 冲突 = 角色**正穿着**的金装，且它所在的部位**不是**配装要放金装的那一格（DIM 的
   *"if we aren't already equipping into that slot"*）。
3. 替身必须**同部位、非金装、同职业**，且不在本次要装的清单里（`exclusions`）。优先**身上**已有的；
   身上没有就**去仓库拉一件**（先 `move` 进来再穿）。部位一律按**物品定义**的 `bucketTypeHash` 判，
   绝不用 profile 给的 `bucket_type` / `slot` 直接下结论（仓库件报的是 `Vault (General)` / 空串）。
4. 先穿替身把冲突那件顶下来，回执行里记 `action="downgrade"`（与单件 `equip` 同一个动作名）。
5. 挑不到替身 → **不发批量**，如实点名"先把「X」脱下来"，并把失败并进 `all_ok`
   （回滚由调用方按 `all_ok` 触发，**不许早退绕过它**）。

被否掉的方案：

- **硬发批量、让上游回 1641 再兜底**：实测代价是整条回滚 + 回滚链自己可能核对不上（见上）。
- **让用户自己先去游戏里脱**：这正是本条要消灭的手工活；DIM 也不这么做。
- **在批量里按顺序单件装（用 `EquipItem` 逐件）**：能绕开冲突，但把我们"批量装备"的往返优势丢掉，
  且逐件的失败语义与现有回执行不一致。

## Consequences

- `equip_loadout` 现在会**多动一件**（替身先穿上）。这是必要的副作用，必须**看得见**：
  回执行里有 `transfer`（从仓库搬来）与 `downgrade`（顶下）两步，摘要也会说明。
- 顶下去的替身**留在角色背包**（不分解、不移动回仓库）—— 与单件 `equip` 的既有语义一致；
  想让它回仓库是玩家的选择，工具不替他决定。
- 判据（挑谁、绝不挑谁、怎么认部位）与 ADR-029 的"绝不腾"共享同一批实现：
  部位判定在 `services/item_parser.py::armor_slot_from_bucket`（唯一出处），
  "锁定的绝不腾"在 `utils/item_state.py`。
- 守门：`tests/test_exotic_dequip.py`（顶下 / 找不到替身如实拒绝 / 无冲突不动手 / 替身从仓库拉），
  三条注入都验过会变红。
