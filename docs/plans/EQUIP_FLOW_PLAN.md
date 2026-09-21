# 计划：装备编排（把"DIM 那套自动换"补上）

状态：**已落地**（用户 2026-09-16 批准开工，当天完成并真机验证）。
版本：换子职业/神器那两件已随 0.3.0 发布，本项顺延到下一个版本。

## 一、这份计划从哪来

用户 2026-09-15 给了一条真实链条：**要把「星火协议」（异域胸甲）穿上，花了 10 轮才成功**。
逐轮复盘后，判决是"**两边都有责任，但主要是我们**"——我们只暴露了 move/equip 两个原语，
没提供 DIM 那样的**冲突感知编排**，于是 Agent 只能自己摸出"先穿一件非异域顶下金装"这条路。

## 二、逐轮判决（哪些是上游铁律、哪些是我们的坑）

| 轮次 | 现象 | 判决 |
| --- | --- | --- |
| 1、7 | `equip` 一件在**仓库**里的物品 → 上游 404 | **上游铁律 + 我们的锅**：`EquipItem` 只接受"在该角色身上"的实例。我们**应该预检**并直接说"先用 move 搬到角色身上（或让我自动搬）"，而不是把 404 原样抛给调用方 |
| 3、4 | 装异域胸甲时还穿着异域臂铠 → 500 `DestinyItemUniqueEquipRestricted` | **上游铁律**：同类异域只能一件（异域**护甲**一件 + 异域**武器**一件，**两类互不冲突**；Manifest 证据见 §七）。当时记成了"全身只能一件金装"——这个**过宽**的说法就是 §七 那次复发的源头。DIM 会**自动把冲突的那件换成非异域**再装目标；我们没做 |
| 5 | `move` 一件**已装备**物品 → 500 `DestinyCannotPerformActionOnEquippedItem` | **上游铁律 + 我们的锅**：应预检并说"它正装备着，要先换下来" |
| 6 | 转移到角色 → 500 `DestinyNoRoomInDestination`（背包 176/176 满） | **上游铁律**，但消息该给数字与出路（腾位/换用背包里现有的非异域） |
| 8 | Agent 列了 103 件臂铠、存文件再 grep | **Agent 侧的浪费**（我们没引导）。它想要的是"术士背包里的非异域臂铠"——`intent="get", armor_slot="gauntlets"` 本来就能直接答 |
| 9、10 | 先穿普通紫臂铠顶下金装 → 再穿星火协议 | **正确解法**，但要用户/Agent 自己摸索 8 轮才找到 |

## 三、要做的：`equip` 变成"冲突感知编排"

`inventory_assistant(intent="equip", ...)`（以及 `move` + `equip=true`）走同一条编排：

1. **预检**（只读，不写）：
   - 实例在不在该角色身上？不在 → 给出"需要先搬"的说明；**并发起一次可选的自动搬**（角色背包有位时）；
   - 目标是不是异域、该角色是否正穿着**另一个槽**的异域？是 → 需要"顶下"步骤；
   - 角色背包是否已满（装不下要搬的东西）→ 说清数字；
2. **出计划**（预览，不写）：
   ```
   步骤 1：把「光芒领主手套」（术士背包，紫）穿上 → 顶下「逃逸艺术家」（异域臂铠）
   步骤 2：把「星火协议」（术士背包，550）穿上
   ```
   返回 `steps[]`，每步给 `action / item / from / to / 为什么`；这一步**必须过我们的确认门槛**；
3. **执行**（`confirmed=true`）：复用 `loadout_equipment_service` 既有的多步写入 + 回滚/取消处理
   （它有 `MoveItemStep`、回滚、取消恢复，别另写一套）；
4. **回读核对**：目标已装备、冲突槽已非异域、中间件状态符合预期；任何一步失败都要**说清停在哪一步、
   账号现在是什么状态**（不允许"部分成功"却报成功）；
5. **失败时的建议**（我们的错误话术规则）：背包满 → 给数字与出路；该类目没有非异域候选 →
   "先去弄一件非异域 X（`intent=get, armor_slot=…` 看你有哪些）"。

**候选怎么挑**（顶下金装用的中间件）：
- 只从**该角色背包**里挑（仓库里的要先搬，会撞"背包满"）；
- 优先已装备过/最高光等的非异域；挑不到就如实说"没有可用的非异域 X"。

## 四、验证（真机，需用户许可）

| 场景 | 期望 |
| --- | --- |
| 装一件仓库里的护甲 | 预检直接给出"先搬"或自动搬 + 装；不再出现裸 404 |
| 异域冲突（金臂铠 → 金胸甲） | 两步计划 + 确认 + 执行 + 回读；不再出现 500 `UniqueEquipRestricted` |
| move 已装备物品 | 预检说明 + 建议（不写） |
| 背包满 | 明确数字 + 出路 |
| 该角色没有非异域候选 | 如实说明 + 指路 |
| 单测 | 四种预检、计划生成、执行顺序、回滚、回读核对（用替身） |

## 五、边界

- **不改游戏规则**：上游禁止的事（同时两件金装、移动已装备物品）我们只是**编排**，不是绕过；
- **不替用户决定顶掉哪件**：中间件从"非异域"里挑，且计划要用户确认；
- 不碰武器/护甲的插槽写入路径（那是另一件事）。

---

## 六、落地记录（进行中，2026-09-16）

### 已落地

1. **形状**：`destiny_mcp/models/equip_plan.py` —— `EquipPlanStep`（`action/item/from_location/to_location/why/replaces`）、
   `EquipPlanBlock`（原因码 + 中文说明 + `slot` + `numbers`，给不出数字就空着不编 0）、`EquipPlan`
   （`status` 只取 `ready/blocked/already_equipped`；`verified: bool | None`，**没核对就是 None**，
   另带 `unverified_reason`）。
2. **预检 + 计划**：`destiny_mcp/services/equip_planner.py`（纯函数、只读输入、不 import bungie），
   四种预检 = `item_equipped`（要 move 的实例正装备着）/ `exotic_conflict`（另一个槽的异域挡住，
   且背包里挑不到非异域顶下）/ `inventory_full` / `item_missing`；
   入参语义写死在 `EquipPlanRequest` 的 docstring 里：`character_inventory` = **该角色装备位 + 背包合并**，
   谁是装备着的以 `equipped_keys` 为准 —— 真机上 `characterEquipment`/`characterInventories` 的条目
   **没有 `isEquipped`**，那个字段只在组件 300 的 `instances.data[实例]` 上。
3. **失败话术**：`tools/_responses.py` 的 `_WRITE_FAILURE_HINTS` 补两条 ——
   `UniqueEquipRestricted` → "全身只能一件异域，先穿一件非异域的同部位顶下它再装目标"
   （**判据过宽，对武器冲突是误导；见 §七**）；
   `ItemNotFound`/"not found in the character's inventory" → "`EquipItem` 只接受在该角色身上的实例，
   先 move 过来或换用他背包里那件"；`NoRoomInDestination` 并入"空间不足"。
4. **单测**：`tests/test_equip_planner.py` 14 条全绿（四种预检、计划顺序、挑不到中间件的如实失败、
   仓库物品要先搬、空 `slot_display` 不留空括号）。

### 真机核对（只读，未写账号）

拿真账号数据直喂 `plan_equip`（warlock 正穿着星火协议、背包里有异域臂铠「逃逸艺术家」）：

```
status = ready
步骤1 [downgrade] 圣贤保护者法袍  warlock → equipped   顶下=星火协议
步骤2 [equip]     逃逸艺术家      warlock → equipped
```

即那条**当时花了 10 轮**的链子，现在被规划成两步；`equipped_keys` 按组件 300 填的语义在真数据上成立。

### 第二刀要用到的实采事实（省得再查）

- **背包容量**取 Manifest 的 `DestinyInventoryBucketDefinition.itemCount`，且必须 `to_signed()` 之后查 `id`
  才命中（uint32 直查时头盔/臂铠两个桶查不到）：Helmet/Gauntlets/Chest/Legs/Class 各 **10**、Artifacts **7**；
- `ManifestManager` **原先没有**取桶定义的方法，第二刀补一个（单一出处，不许在 service 里开 sqlite）；
  `used` 从 profile 数、`capacity` 从桶定义取，给不出就留空。

### 第二刀（已落地）

- **真数据装配**：`services/transfer_service.plan_equip_item`（只读）读 `profile_components.ARMOR_SNAPSHOT`，
  用现成的 `item_parser.parse_items_from_profile` 拼物品（`is_equipped` 就是组件 300 那套），
  `equipped_keys` 只认这个角色身上的实例；容量走新加的 `ManifestManager.get_bucket_definition`
  （`DestinyInventoryBucketDefinition.itemCount`，`to_signed()` 回退）；
- **容量口径**：`used` **含正装备那件**（与 DIM 一致）。两个方向风险不对称：多算顶多让用户白清一格，
  少算会去撞上游 `NoRoomInDestination` —— 真机上 chest/gauntlets/helmet 都是 10/10，旧口径会假报"还能放一件"；
- **确认门槛**：`equip` 进了 `SELF_GUARDED_WRITE_INTENTS`，分派搬到 `tools/_equip_branches.py`：
  无 `confirmed` → `confirmation_required` + `candidates[0].steps[]`（零写入）；`confirmed=true` → 才执行；
  `status=blocked` → `equip_blocked`（如实说清缺什么，不是"写入失败"）；已在身上 → `ok` + 无事可做；
- **执行与回读**：`execute_equip_plan` 逐步 `EquipItem`（先顶下、再装目标），失败返回
  `stopped_at` + `steps_done` + `equip_blocked`… 之外还带 **`equipped_now`**（受影响部位现在装着什么）；
  回读走 `services/write_readback.py`，超窗报 `unverified`。

### 真机验证（2026-09-16，用户已批准写入，账号已完全还原）

| 调用 | 结果 |
| --- | --- |
| `equip` 逃逸艺术家（不带确认） | `confirmation_required`，`steps` = ①顶下星火协议（穿圣贤保护者法袍）②装逃逸艺术家；**零写入** |
| 同上次 + `confirmed=true` | `ok:true`、`verified:true`、`equipped_now: gauntlets=逃逸艺术家`（"2 步，回读一致"） |
| 换回星火协议（确认） | `ok:true`、`verified:true`、`equipped_now: chest=星火协议` |
| 换回原手套（确认） | `ok:true`、`verified:true`、`equipped_now: gauntlets=光芒领主手套` |
| 最终五部位回读 | 面具/手套/星火协议/护腿/职业物品 —— 与操作前**完全一致，账号已还原** |

**这条就是当时花了 10 轮的链子**：现在 1 次计划 + 1 次确认 = 2 步。

### 与计划原文的一处偏差（有意）

计划里写"复用 `loadout_equipment_service` 的多步写入 + 回滚"。实际实现是**直接用 `EquipItem` 逐步写**
（`transfer_service.execute_equip_plan`）：这条链只有"先顶下、再装"两步，回滚就是"把变过的部位换回去"，
而 `loadout_equipment_service` 那套是给移动/穿整套配装用的（`MoveItemStep` + 取消恢复），拉进来反而多一层。
若以后要支持"一次装多件"再回头复用。

**回滚已实现**（0.4.1）：第 N 步失败 → 把**真正变过**的部位换回动手前那件（只动变过的，避免白写），
回滚失败照实报 `rolled_back: false`。计划步骤为此加了结构化 `slot`。

### 还没做

- `move` 的 `equip=true` 还没走这条编排（只接在 `equip` 上）；
- 语料 runner 里还没有 equip 的两段式行（`docs/testing/TESTING_CORPUS_FULL.md` 只登记了断言）。

### 端到端验收（2026-09-18，真机，用户游戏内配合）

用户按求解结果在游戏内装上那 5 颗属性模组后，读账号核对五件的最终属性：

| | 武器 | 生命 | 职业 | 手雷 | 近战 | 超能 |
| --- | --- | --- | --- | --- | --- | --- |
| 账号实测（五件 `final` 求和） | 106 | 30 | 30 | **180** | 44 | 125 |
| 求解器预测 | 106 | 30 | 30 | **180** | 44 | 125 |
| 差值 | 0 | 0 | 0 | 0 | 0 | 0 |

- 逐件：铁能面罩 20/40、光芒领主手套 30/30、星火协议 6/40、移民号陨落战靴 30/30、光泽臂环 20/40
  （武器/手雷；与计划里的 2× 武器模组 + 3× 手雷模组一致）；
- 意义：**护甲 3.0 的求解模型（基 roll + 大师杰作 + 属性模组 + 调谐）在真账号上首次被完整验证**，
  预测值与游戏内实测值误差为 0；
- 模组由用户在游戏内手动安装 —— API 装不了（**ADR-002**）。

---

## 七、复发：冲突模型写错了（2026-09-21）

本节是**修法定稿**：用户 2026-09-19 那份猎人社区配装复盘里"又出这个问题了"的现场，根因在这里。

### 症状（真机复现，两次都是 `confirmed=false` 的预检，零写入）

| 角色 | 请求 | 预检给出的计划 | 应有的判断 |
| --- | --- | --- | --- |
| hunter | 装异域弓**需求层级**（威能槽正穿异域刀剑**狼毒**） | 只有一步 `equip`，**零冲突识别** | 同类异域武器冲突 → 该先顶下狼毒 |
| warlock | 装异域火箭筒**龙息**（动能槽正穿异域自动步枪**赫沃斯托夫7G-0X**） | 先脱异域**胸甲**「星火协议」再装 | 护甲与武器**不冲突**；真冲突（两把异域武器）反而没识别 |

第二行就是复盘里那个误导现场：调用方被指去"顶下异域护甲"，而真因是异域武器。

### 根因（两处，方向相反）

1. **判据过宽**：`plan_equip` 写的是"另一个槽的异域就算冲突"，话术写成"全身只能装备一件异域"。
   真实规则是**分类别**的 —— 异域武器一件 + 异域护甲一件，两类**互不冲突**。于是"装异域武器 +
   正穿异域护甲"被误判成冲突，凭空多一步顶下护甲。
2. **武器看不见**：`InventoryItem.slot` 只按护甲桶填（`item_parser` 的 `_ARMOR_BUCKETS`），
   武器 `slot` 一律空串；冲突过滤里有 `and item.slot`，等于把武器全部跳过。于是"两把异域武器"
   这种真冲突**永远漏判**，计划停在 `equip` 一步，真执行必撞 500。

`tools/_responses.py` 的 `_WRITE_FAILURE_HINTS` 又把任何 `UniqueEquipRestricted` 都解释成
"从该角色背包挑一件非异域的**同部位护甲**"——对武器冲突是纯误导。

### 规则不用猜：Manifest 自己编码了（本机全量实测）

`DestinyInventoryItemDefinition.equippingBlock` 里两个字段正好是这两件事的唯一正主：

| 字段 | 含义 | 实测取值 |
| --- | --- | --- |
| `uniqueLabel` | 同类互斥的**组** | `exotic_armor`（348 件，**含异域职业物品** Solipsism/Stoicism/Relativism）、`exotic_weapon`（179 件）；其余取值全是一件一号的纪念徽章，与装备无关 |
| `equipmentSlotTypeHash` | 装备槽，**武器与护甲一视同仁** | 查 `DestinyEquipmentSlotDefinition` 得名字：Helmet / Gauntlets / Chest Armor / Leg Armor / Class Armor、Kinetic / Energy / Power Weapons |

**判据收敛成一句话**：两件装备冲突 ⇔ 它们的 `uniqueLabel` 相同且非空。
同槽位换异域不算冲突（装上就把它替换掉了）；异域职业物品与异域护甲冲突也自动落在这条里，不用特判。

### 修法（四处）

1. **冲突判据换成 `uniqueLabel`**：`equip_planner` 不再自己判"是不是异域"（`_is_exotic` 在这条路上退场，
   只保留"定义查不到就不下结论"的 `None` 语义）；
2. **槽位改读 `equipmentSlotTypeHash`**：这一条**顺带把"给武器补 slot"整个绕开** ——
   不必动 `InventoryItem.slot` / `item_parser` / `_DISPLAY_TO_SLOT`（那 43 处读 `slot` 的地方一处不碰），
   而且"仓库里的护甲 bucketHash 认不出部位"这个老问题（`_slot_from_definition` 的由来）一起消失；
3. **失败话术改同类口径**：`_WRITE_FAILURE_HINTS` 那条不再说"异域护甲"，改成"同类的异域"；
4. **`equip_many` 接进同一套编排**：它现在绕过 planner 直接打上游批量 `EquipItems`
   （`transfer_service.equip_items`），既不预检也不给计划。

### 明确不是解法的：调换"传说在前、异域在后"

用户提过这个方向。它对一半：要装异域 X 而同类异域 Y 正穿着，**必须先用一件同部位的非异域把 Y 顶下**。
但它与**全局顺序**无关，只与"顶下来的那件落在哪个槽"有关：

- warlock 那一例，计划里根本没有一件落在冲突槽的传说武器，怎么排序都没用；
- 反过来，只要计划里显式包含"先顶下冲突的那件"，顺序对错都行；
- 批量接口内部是否按调用方给的顺序处理，我们**没有实测依据**，拿它躲冲突是在赌。

### 已定（2026-09-21，用户拍板）

1. **`equip_many` 的编排粒度 = 整套模拟（A）**：把这一批当作"最终要穿上的一套"算一次 ——
   先算穿上后各部位的归属，再看最终 `uniqueLabel` 是否重复。这样"这批里本来就有一件紫臂铠
   会替换掉冲突的异域臂铠"不会被算成冲突、也不会多出没必要的顶下；而**集合内部自带两件同类异域
   时直接报 `blocked`**（那套本身不合法），不让上游去 500。
   （被否掉的 B「逐件独立计划」实现简单，但上面两种情况都会给错计划。）
2. **冲突模型单独开一条 ADR**：`docs/adr/011-exotic-exclusivity-follows-unique-label.md`
   （编号规矩见 `docs/adr/README.md`：写 `ADR-NNN` 就必须真有那一篇，否则 `tests/test_agent_docs.py` 红）。

### 验收

- 单测（替身）：`uniqueLabel` 冲突（武器↔武器、护甲↔护甲、职业物品↔护甲、跨类**不**冲突）、
  同槽位换异域不冲突、集合内部两件同类异域 → `blocked`、槽位来自 `equipmentSlotTypeHash`；
- 真机（只读，`confirmed=false`）：上面两行症状必须反过来 ——
  hunter 那行要给出"先顶下狼毒"的步骤，warlock 那行**不许**再出现顶下星火协议；
- 语料：补 `equip` 的两段式行（`docs/testing/TESTING_CORPUS_FULL.md` 至今只登记了断言、没有行）；
- 提交前 `pytest` 全量 + 语料各跑一次。

### 落地记录（2026-09-21）

**已完成（第一刀：判据与槽位）**

| 改动 | 位置 |
| --- | --- |
| 装备槽键补上武器那一半（`kinetic`/`energy`/`power`），武器护甲同一套键 | `services/armor_payload.py` 的 `EQUIP_SLOT_LABELS` + `equip_slot_of(definition)` |
| 冲突判据换 `equippingBlock.uniqueLabel`；槽位换 `equipmentSlotTypeHash`；`_is_exotic` 与 `_slot_from_definition` 退场 | `services/equip_planner.py` 的 `item_traits()` + `plan_equip` |
| **连带修复**：回滚/回读以前按 `item.slot` 认部位（护甲专用），武器步骤会整个漏掉 —— 现在与规划层共用 `item_traits` | `services/transfer_service.py` 的 `_equipped_by_equip_slot()` |
| 失败话术改成同类口径 | `tools/_responses.py` 的 `_WRITE_FAILURE_HINTS` |

- 单测：`tests/test_equip_planner.py` 20 条（含跨类**不**冲突、武器↔武器冲突、异域职业物品↔异域护甲、
  槽位来自定义而非 bucket），`tests/test_equip_execute.py` 新增"武器步骤也要能回滚"；
- 全量 `pytest`：**1657 passed**（改动前 1652）；
- 一条测试写错被自己抓住：异域职业物品与异域胸甲同组，要装它得先把**胸甲**换成非异域，
  拿另一件披风顶不了事 —— 代码判对了，是测试预期错了。

**还没做（第二刀：`equip_many`）**

`equip_many` 目前仍绕过 planner 直接打上游批量 `EquipItems`（`transfer_service.equip_items`），
即"同一条判据"还没作用到它身上；整套模拟（集合内部两件同类异域 → `blocked`）也还没实现。
在它落地之前，`equip` 已经是对的，`equip_many` 保持原样。

**真机只读复验（2026-09-21，零写入）**

MCP 服务是宿主拉起的**常驻子进程**，改了代码要重启宿主才生效（项目既有约定）；为了不等重启就能验，
新加 `scripts/verify_equip_planner.py` —— 直接用仓库里的新代码打真账号的**只读预检**
（`TransferService.plan_equip_item`），一个字都不写账号。

| 请求 | 改动前 | 改动后（实测） |
| --- | --- | --- |
| hunter 装异域弓**需求层级**（已穿异域刀剑**狼毒**） | 只有一步 `equip`，零冲突识别 | `blocked`：判到**威能槽的狼毒**「属于同一类异域（威能武器）」，并如实说背包里没有非异域威能武器；出路用 `item_type="武器"` 而不是 `armor_slot` |
| hunter 装异域火箭筒**加拉尔号角**（狼毒同在威能槽） | — | `ready`：**同槽**装上即替换，不算冲突（这条必须仍是 ready） |
| warlock 装异域火箭筒**龙息**（已穿异域自动步枪**赫沃斯托夫7G-0X**） | 先脱异域胸甲「星火协议」 | `blocked`：判到**动能槽的赫沃斯托夫7G-0X**；**"顶下星火协议"没有了** |

§七 开头那两行症状至此**都反过来了**。

**还剩**：`equip` 两段式语料行（`docs/testing/TESTING_CORPUS_FULL.md` 至今只登记了断言、没有行）。
