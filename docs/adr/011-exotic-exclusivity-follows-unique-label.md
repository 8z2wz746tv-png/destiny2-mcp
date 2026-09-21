# ADR-011: 异域互斥按 Manifest 的 uniqueLabel 判，槽位按 equipmentSlotTypeHash 取

- Status: accepted
- Date: 2026-09-21
- Decision By: maintainer
- Scope: `destiny_mcp/services/equip_planner.py`、`destiny_mcp/tools/_responses.py`（`_WRITE_FAILURE_HINTS`）、
  `inventory_assistant(intent="equip"/"equip_many")`

## Context

**What changed**：`equip` 的冲突感知编排（0.4.0）把异域互斥写成"全身只能装备一件异域" ——
只要该角色另一个槽穿着异域就算冲突。这个说法**过宽**，2026-09-21 在真机上复发（两次只读预检，
零写入）：

| 角色 | 请求 | 预检给出的计划 | 应有的判断 |
| --- | --- | --- | --- |
| hunter | 装异域弓「需求层级」（威能槽正穿异域刀剑「狼毒」） | 只有一步 `equip`，**零冲突识别** | 同类异域武器冲突，该先顶下狼毒 |
| warlock | 装异域火箭筒「龙息」（动能槽正穿异域自动步枪「赫沃斯托夫7G-0X」） | 先脱异域**胸甲**「星火协议」 | 护甲与武器**不冲突**；真冲突（两把异域武器）反而没识别 |

根因是两处、方向相反：

1. **判据过宽** —— 跨类误伤：装异域武器时被要求脱异域护甲，理由"全身只能一件异域"是错的；
2. **武器看不见** —— `InventoryItem.slot` 只按护甲桶填（`item_parser` 的 `_ARMOR_BUCKETS`），
   武器 `slot` 一律空串，而冲突过滤带 `and item.slot`，等于把武器全部跳过，真冲突永远漏判。

2026-09-21 本机全量实测 Manifest：`DestinyInventoryItemDefinition.equippingBlock` 里正好有两个字段是
这两件事的唯一正主 ——

| 字段 | 含义 | 实测取值 |
| --- | --- | --- |
| `uniqueLabel` | 同类互斥的**组** | `exotic_armor`（348 件，**含异域职业物品**）、`exotic_weapon`（179 件）；其余取值是一件一号的纪念徽章，与装备无关 |
| `equipmentSlotTypeHash` | 装备槽，**武器与护甲一视同仁** | 查 `DestinyEquipmentSlotDefinition` 得名字：Helmet / Gauntlets / Chest Armor / Leg Armor / Class Armor、Kinetic / Energy / Power Weapons |

## Decision

1. **冲突判据 = `uniqueLabel` 相同且非空。** 两件装备互斥，当且仅当它们的 `uniqueLabel` 相等且非空。
   同槽位换异域不算冲突（装上即替换）；异域职业物品与异域护甲冲突也自动落在这条里，不需要特判。
   定义查不到时**不下结论**（保持 `None` 语义），不把"没查到"说成"不冲突"。
2. **槽位 = `equipmentSlotTypeHash`**，名字查 `DestinyEquipmentSlotDefinition`。
   冲突判定的"同槽位"与"顶下用同部位替代品"都用它。
3. **失败话术按"同类异域"说**：`_WRITE_FAILURE_HINTS` 里 `UniqueEquipRestricted` 那条不许写死"护甲"。
4. **同一条判据必须同时作用于 `equip` 与 `equip_many`**：后者此前绕过编排直接打上游批量
   `EquipItems`，等于同一条规则有两个实现、其中一个还是缺的。

被否掉的选项：

- **自己判"是不是异域 + 分武器/护甲两桶"**：要另维护一份桶表与一份分类表，且漏掉异域职业物品
  这第三种情况 —— 而 Manifest 已经把答案给了；
- **给 `InventoryItem.slot` 补武器槽位，让现有判据能用**：`slot` 是**护甲语义**，全仓 43 处读者
  （`build/` 求解器整套在内）都按这个语义用它，改含义的爆炸半径远大于收益；
- **靠调用顺序规避（"先装传说、再装异域"）**：与全局顺序无关，只与"被替换的那件是否落在冲突槽"
  有关；且批量接口内部是否按调用方给的顺序处理，我们**没有实测依据** —— 拿它躲冲突是在赌。

## Consequences

- 冲突模型不再有任何手写分类：游戏改规则（新增 `uniqueLabel` 取值）时判据自动跟上，
  不需要改代码；定义缺失时照旧如实说"判不了"。
- `_slot_from_definition`（读 `itemTypeDisplayName` 推槽位）与 `_is_exotic` 在冲突这条路上退场 ——
  前者存在的理由是"仓库里的护甲 bucketHash 认不出部位"，`equipmentSlotTypeHash` 一并解决了。
- `EquipPlanStep.slot` / `EquipPlanBlock.slot` 可能出现武器槽位取值（此前只会是护甲槽位），
  属**新增取值**、非破坏性，但要登记进 `docs/COMPATIBILITY.md`。
- 改这条决定要同时改：`destiny_mcp/services/equip_planner.py`、`destiny_mcp/tools/_responses.py`、
  `destiny_mcp/services/transfer_service.py`（`equip_many`）、`tests/test_equip_planner.py`、
  `docs/plans/EQUIP_FLOW_PLAN.md`（§七 有完整证据与验收口径）、
  `skills/destiny2-mcp/references/routing.md`（如果话术有变）。
