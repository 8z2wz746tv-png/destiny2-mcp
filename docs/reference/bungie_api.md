# Bungie API 实测事实清单

**口径声明（先读这一段）**：本文档记的是**我们自己的实测事实** —— 我们依赖了什么、在哪一步踩过什么坑、
之后代码里怎么做的。Bungie 官方文档（<https://bungie-net.github.io/>，scope 表、端点、AWA、组件枚举都在那一页）
是**定义来源**：字段叫什么、端点怎么拼，以官方为准。

**冲突时以本文档的「实测」为准**（这是项目规矩），并做两件事：① 在对应条目上标 `⚠️ 与官方文档冲突`；
② 去更新那一条 —— 实测变了就改实测，别让文档里躺着一句过期结论。要重下官方文档深查用
`python scripts/fetch_bungie_api_docs.py`（拉到 `~/.destiny_mcp/reference/`，不进 git；`--check` 看版本变没变）。

每条格式：事实 / 出处 / 实测 / 结论。**没实测过的条目不许写成实测**。

---

## 一、OAuth scope

### scope 表：三个名字的官方原文一句话
- 事实：官方 scope 表里与我们相关的三条，原文分别是
  `MoveEquipDestinyItems` —— "Move or equip Destiny items"；
  `ReadDestinyInventoryAndVault` —— "Read Destiny 1 Inventory and Vault contents. For Destiny 2,
  this scope is needed to read anything regarded as private. This is the only scope a Destiny 2 app
  needs for read operations against Destiny 2 data such as inventory, vault, currency, vendors,
  milestones, progression, etc."；
  `AdvancedWriteActions` —— "Can perform actions that will result in a prompt to the user via the Destiny app."
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 把令牌解出来看，`scope` 字段**没有**任何 scope（授权 URL 一个 `scope` 都没带）；
  写入因此被上游拒成 403 `AccessNotPermittedByApplicationScope`。
- 结论：**令牌里有哪个 scope，取决于授权 URL 上显式申请了什么**，不是"应用有权限就自动带"。

### 本项目申请了哪些、为什么
- 事实：`AdvancedWriteActions` 是**按应用审批**的 scope —— 应用没被 Bungie 授予时，授权页会直接回
  `invalid_scope`。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）；申请入口
  <https://www.bungie.net/en/Application>
- 实测：2026-09-16 真机 —— 0.4.6 刚在授权 URL 上加了 `scope=AdvancedWriteActions`，**连登录都做不成**
  （授权页回 `invalid_scope`），因为该应用没被授予。0.4.7 改成：先按"带 scope"试一次，撞到
  `invalid_scope` 就打印说明并自动退回"不带 scope"再登一次。
- 结论：`destiny_mcp/oauth_setup.py` 的 `_WANTED_SCOPES` 只声明 **`AdvancedWriteActions`**，且它只是
  "想要"不是"必须有" —— 降级后功能上只少了「带消耗/不可逆的插槽写入」（付费 `InsertSocketPlug`、
  神器重置）。`MoveEquipDestinyItems` / `ReadDestinyInventoryAndVault` **我们从来没申请过**，
  而读与移动/装备一直是通的；别照文档表"补全"一堆 scope，那会重新引入登录失败。

---

## 二、AdvancedWriteActions（AWA）与"没有它时写入怎么失败"

### AWA 三段端点
- 事实：`POST /Destiny2/Awa/Initialize/` → 上游返回 `correlationId`；`POST
  /Destiny2/Awa/AwaProvideAuthorizationResult/` 把用户在 Destiny app 里的授权结果回填；`GET
  /Destiny2/Awa/GetActionToken/{correlationId}/` 取那一次动作的 token。它对应官方 scope 描述里的
  "a prompt to the user via the Destiny app"。
- 出处：<https://bungie-net.github.io/> → `Destiny2` 端点清单（文档版本 2.21.8，2026-09-17 查阅）
- 实测：**本项目没有实现 AWA 流程**（仓库里只有 scope 名，没有任何 `Awa/` 调用）。我们的写入要么走
  免费插槽接口、要么要求应用具备 `AdvancedWriteActions` 直接拿令牌，不需要用户弹窗。
- 结论：要接"需要用户在 Destiny app 里点确认"的动作时才去实现这三段；在那之前不要照抄流程，
  把 `AdvancedWriteActions` 当成"令牌里有没有"来用就够了。

### 没有 scope / 不允许的动作，上游怎么回（三次实测对照）
- 事实：权限不足回 **403** `AccessNotPermittedByApplicationScope`（消息里点名 RequiredScope）；
  策略不允许的动作回 **ErrorCode 1663** `DestinyItemActionForbidden` "This action can only be done in-game."
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测（2026-09-16 真机，`error.code` 与上游原文一起带出来之后才看清真因）：
  ① 装属性模组走 `InsertSocketPlugFree` → **403** `Access not permitted by application scope`；
  ② 为腾能量卸掉一颗模组 → **500** `This action can only be done in-game.`；
  ③ 换调谐类 plug 走 free 接口 → **1663** `DestinyItemActionForbidden` + `This action can only be done in-game.`
- 结论：**403 与 1663 是两件事，处置不同**（见下一条）；两条都必须在话术里带上游原文，
  不能统一写成"写入失败"。旧版本把这两句丢掉，只写"模组 X → '铁能面罩'"，看上去像我们的插槽查找又错了，
  为此白查了好几轮。

### 403 不重试、1663 才换接口
- 事实：403 是应用级权限问题，重试同一个接口永远不会成功；1663 是"这个 plug 不免费/不可逆"，
  换付费接口才可能成。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— free 接口对"非免费可逆"的 plug 回 1663；0.4.5 之前盲目按
  "能量消耗 > 0"选付费接口，真机上装护甲模组一直 403（而护甲模组本来就该走 free）。
- 结论：`services/loadout_mod_sockets.py` 现在**先走 free**，只有拿到 1663
  （`in-game` / `DestinyItemActionForbidden`）才**退回**付费接口；403 直接如实报权限问题、不重试。
  守门测试钉住这三条分支（`tests/test_equip_mod.py`）。

---

## 三、`InsertSocketPlug` vs `InsertSocketPlugFree`

### free 指"没有材料消耗"，不是"不花能量"
- 事实：官方对 `InsertSocketPlugFree` 的说明是**没有材料消耗**，并明确覆盖
  "Perks, **Armor Mods**, Shaders, Ornaments"；`InsertSocketPlug` 是带消耗的那条。
- 出处：<https://bungie-net.github.io/> → `/Destiny2/Actions/Items/InsertSocketPlugFree/`
  与 `/Destiny2/Actions/Items/InsertSocketPlug/`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 我们原先按"能量消耗 > 0"判断该走付费接口，于是护甲模组全走错；
  用户指出"DIM 能操作模组"后复查官方原文，确认护甲模组属于 free 覆盖范围（它消耗的是能量、不是材料）。
- 结论：**接口语义 ≠ 权限**。护甲模组走 free；要不要 scope 是另一条线（付费接口才要
  `AdvancedWriteActions`）。别再拿"消耗大不大"去决定用哪个接口。

### 两个端点官方都标 Preview
- 事实：官方端点清单里 `InsertSocketPlug` 与 `InsertSocketPlugFree` 都挂着
  `Preview - Not Ready for Release` 标记。
- 出处：<https://bungie-net.github.io/> → `Destiny2` 端点清单（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 免费插入子职业碎片/星象、以及神器模组都实际生效（回读 305 确认过）。
- 结论：Preview 只说明**契约可能变**，不代表不能用；我们可以用，但别把它当稳定契约宣传，
  上游一改就以实测更新本文档。

---

## 四、Profile 组件：只请求 305 时上游不返回插槽

### 要插槽就必须带"清单类"组件
- 事实：`GetProfile` 按 `components` 取数；只给插槽组件时，上游不会附带物品清单，插槽自然无处可挂。
- 出处：<https://bungie-net.github.io/> → `DestinyComponentType` 枚举与 `GetProfile` 端点
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— `get_profile(..., [305])` 返回 **0 件**带插槽；
  同一次会话带上清单类组件（102/200/201/205/300）返回 **1627 件**。模组插槽读取当时正是只写了 `[305]`，
  于是拿到一串空数据，接着每件护甲都报"找不到模组 X 的唯一兼容插槽"，查了一整晚。
- 结论：插槽读取统一走 `destiny_mcp/services/profile_components.py` 的 **`INVENTORY_SOCKETS`**
  （`[102, 200, 201, 205, 300, 305]`），不在服务里写裸组件号。两条守门测试钉住：
  `tests/test_profile_components.py::test_socket_reads_always_carry_an_inventory_component`
  （要 305 就必须带 102/200/201/205）与 `…::test_no_service_writes_a_raw_component_list_any_more`
  （服务里禁止裸组件号字面量）。

### 缓存里的"空列表"不等于"这件没有插槽"
- 事实：profile 是异步快照，物品刚被搬动时这次响应里可能还没有它的插槽。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 插槽缓存只判"键在不在"，装备刚被搬过来时键在、值是空列表，
  于是每个槽都被跳过（看上去像"这件护甲没有模组槽"）。
- 结论：**空列表要触发重读**，不能当"这件是空的"用（没查到 ≠ 没有）。

---

## 五、写入后的同步窗口（3～10 秒）

### 写完立刻回读会读到旧值
- 事实：写入接口返回成功 ≠ profile 立刻可见。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— `EquipItem` 回 `ErrorCode=1`，**立刻**回读仍是旧子职业，约 3 秒后再读才是新的；
  同一晚两次写入的窗口不一样：一次 3 秒可见、一次 5 秒还没现身。
- 结论：统一走 `destiny_mcp/services/write_readback.py` 的 `read_until`（`ATTEMPTS=8` × `DELAY_SECONDS=1.5`，
  约 10.5 秒），用**最后一次**读到的值判断。同一坑还有两处：读实例插槽、`equip_items` 的
  "物品必须在目标角色背包里"预检 —— 刚搬完立刻批量装备必失败（报"请先 move_item"）。

### 超窗只能说"没确认"
- 事实：重试到次数用尽还没看到新值，与"写入失败"是两件事。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 回读窗口"几秒到十几秒不固定"，超过 10.5 秒仍读不到的情况出现过。
- 结论：超窗报 **`unverified`** + `unverified_reason`，**不许**说"没换成"（上游已回成功），
  也**不许**当成功；"写完立刻读一次就下结论"会把成功的写入报成假失败。

---

## 六、`isEquipped` 只在组件 300

### `characterEquipment` 的条目没有这个字段
- 事实：`isEquipped` 在 `itemInstances`（组件 300）的 `instances.data[实例]` 上；
  `characterEquipment`（205）/`characterInventories`（201）的条目里没有它。
- 出处：<https://bungie-net.github.io/> → `DestinyItemComponent` 与 `DestinyItemInstanceComponent`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 直喂真账号数据时，205/201 条目缺 `isEquipped`；直接按桶判断"谁装备着"
  会得出对所有装备都为真的结论（那只是"它在装备桶里"）。
- 结论：谁是装备着的，只看组件 300 填出来的 `is_equipped`；`characterEquipment` 里的条目
  **必须先按桶过滤**再用。"装备位 + 背包"合并的语义写死在 `EquipPlanRequest` 的 docstring 里
  （见 `docs/plans/EQUIP_FLOW_PLAN.md` 第六节）。

---

## 七、赛季神器：三个 hash 家族

### 玩家实例 / 赛季定义 / 目录定义不是一回事
- 事实：神器同时存在三类标识 —— ①**玩家实例**（profile 条目的 `itemInstanceId` + 那件东西的
  `itemHash`）；②**赛季定义族**（`DestinyArtifactDefinition` 的那一行，报的是本赛季神器）；
  ③ 神器模组的目录条目（`DestinyInventoryItemDefinition`，候选池从这里查）。
- 出处：<https://bungie-net.github.io/> → `DestinyArtifactDefinition`、`DestinyInventoryItemDefinition`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 目录里的「当前神器」（`DestinyArtifactDefinition` 全表只有 1 行，报 s27 好奇之器）
  与角色身上那件**可以完全不同**（同一时刻三角色分别装着 s26/s21/s25）；而且**同名不同 hash**
  （好奇之器：目录 `-1600062152`、玩家实例 `23349941`）。hash 还有 signed/unsigned 两种写法，
  查定义前要 `to_signed()`。
- 结论：**"我现在用哪个神器、能不能换"只读账号实例**，目录只配用来查模组池；
  槽位从"这件神器的定义"算（不写死槽数/槽号，不同赛季不同）。

### 神器不可转移
- 事实：神器与子职业物品同类，`transferStatus` 表明不能搬。
- 出处：<https://bungie-net.github.io/> → `DestinyItemComponent.transferStatus`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 背包里 `transferStatus=2`、装备位 `=3`；仓库与邮政长里神器 **0 件**。
- 结论：能换的只有**同一角色背包/装备位**里那几件；别规划"从仓库搬神器"，也别报"仓库里有一件更好的"。

---

## 八、子职业元素在 `plugCategoryIdentifier` 的第二段

### 第二段是元素，第三段才是槽类型
- 事实：子职业 plug 的 `plugCategoryIdentifier` 形如 `warlock.solar.supers` / `shared.void.grenades` /
  `warlock.arc.aspects` —— 第一段是职业（或 `shared`），**第二段是元素**，第三段是槽类型。
- 出处：<https://bungie-net.github.io/> → `DestinyPlugItemDefinition.plugCategoryIdentifier`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-17 查本地 Manifest（38894 条物品定义）：第二段取值是
  `arc/solar/void/stasis/strand/prism`（另有 `shared` 前缀族）；第三段取值是
  `supers/melee/grenades/class_abilities/movement/aspects/fragments`，**没有** `movement_abilities`
  这种写法（另有 `totems/trinkets/transcendence/prism_grenade` 等少量族）。
- 结论：`subclass_service._CATEGORY_PATTERN` 的捕获组只包了**第三段**（槽类型：`supers`/`melee`/
  `grenades`/`movement`/…），拿它当元素用必然错位；断元素只能用 `_ELEMENT_PATTERN`（捕获第二段）。
  另一条实采结论（`_element_of` 的 docstring）：元素**不写在子职业物品的定义里**
  （18 件子职业物品的 `defaultDamageType` 全是 0、没有 element 字段、
  `socketEntries[].plugCategoryIdentifier` 也是空的），只能从**已装 plug 的类别串**读 ——
  "按定义猜元素"的写法一律不成立。

---

## 九、上游铁律清单（只编排，不绕过）

### 四条硬规则
- 事实：上游有四条**我们绕不过**的规则：
  ① `DestinyItemUniqueEquipRestricted`（`UniqueEquipRestricted`）—— 同一个角色全身只能一件异域护甲；
  ② `DestinyCannotPerformActionOnEquippedItem` —— 正装备着的物品不能被移动（要换下来先）；
  ③ `DestinyNoRoomInDestination` —— 目标位置空间不足（含背包满）；
  ④ `EquipItem` 只接受**在该角色身上**的实例（仓库/别的角色上的会报 "not found in the character's inventory"）。
- 出处：<https://bungie-net.github.io/> → 上述异常/错误码与 `EquipItem` 端点说明（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 这条链子当时花了 **10 轮**才走通；0.4.4 把批量装备失败的上游原文带出来之后，
  才看清真正原因是"物品还没在目标角色背包里"（同步窗口），不是插槽匹配。
- 结论：我们的角色是**编排**：预检（`services/equip_planner.py` 四类）→ 计划（先顶下、再装目标）→
  `confirmed=true` 才写 → 回读核对；失败照实转述上游原文并给下一步（`tools/_responses.py` 的
  `_WRITE_FAILURE_HINTS`）。**不许循环重试、不许回滚成"假装没发生"、不许把上游策略限制包装成用户账号问题**；
  回滚只回**真正变过**的部位，失败照实报 `rolled_back: false`。

### 背包容量口径：正装备那件也算占用
- 事实：护甲桶容量取 Manifest 的 `DestinyInventoryBucketDefinition.itemCount`，且要 `to_signed()` 之后查
  `id` 才命中（uint32 直查时头盔/臂铠两个桶查不到）。
- 出处：<https://bungie-net.github.io/> → `DestinyInventoryBucketDefinition.itemCount`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— Helmet/Gauntlets/Chest/Legs/Class 各 **10**、Artifacts **7**；
  真机上 chest/gauntlets/helmet 当时都是 10/10，旧口径（不含正装备那件）会假报"还能放一件"。
- 结论：`used` **含正装备那件**（与 DIM 一致）—— 两个方向风险不对称：多算顶多让用户白清一格，
  少算会去撞 `NoRoomInDestination`。

---

## 十、与本文档有关的代码位置

| 事实 | 唯一出处 |
| --- | --- |
| 组件号集合 | `destiny_mcp/services/profile_components.py` |
| 游戏内生涯计数器（组件 1100）与统计接口的分工 | `destiny_mcp/services/activity_counters_service.py`（见本文第十一节） |
| 写入回读重试 | `destiny_mcp/services/write_readback.py` |
| 模组/插槽写入与 free→付费回退 | `destiny_mcp/services/loadout_mod_sockets.py` |
| 装备编排与预检 | `destiny_mcp/services/equip_planner.py`、`services/transfer_service.py` |
| 失败话术与上游原文 | `destiny_mcp/tools/_responses.py`（`_WRITE_FAILURE_HINTS`） |
| OAuth scope | `destiny_mcp/oauth_setup.py`（`_WANTED_SCOPES`） |
| 神器三个 hash 家族 | `destiny_mcp/services/artifact_service.py`、`manifest_artifacts.py` |
| 子职业元素第二段 | `destiny_mcp/services/subclass_service.py`（`_ELEMENT_PATTERN`） |

## 十一、Metrics（组件 1100）vs Stats：两个生涯数字，各有各的出处

**口径声明**：这一节里的数字全部来自本机真账号实测（2026-09-17，只读，未做任何写入）。
同一个"生涯击败"，**游戏内显示 124,495，统计接口账号级 107,106** —— 这不是谁算错了，
是两个数据源的口径不同。谁问哪一路，就给哪一路，并且把出处一起给出去。

### 组件 1100 的响应形状

- **事实**：形状是 `Response.metrics.data.metrics = {metricHash: {invisible,
  objectiveProgress: {objectiveHash, progress, completionValue, complete, visible}}}` ——
  **名字和描述不在里面**，只有 hash 和数字。
- **出处**：<https://bungie-net.github.io/> → `DestinyComponentType.Metrics`（文档 2.21.8，
  2026-09-17 查阅）；计数器定义在 Manifest 表 `DestinyMetricDefinition` 的
  `displayProperties.name/description`。
- **实测**：本账号该组件共 **402 条**计数器（原始响应约 66 KB）。其中
  `811894228` = `Opponents Defeated`，描述原文 *"The total number of opponents defeated in
  Crucible matches. Tracks from Season 1 onward."*，`progress = 124495` ——
  **与游戏内、第三方机器人显示的数字一字不差**；数值最大的一条是 PvE 累计 `79,712,994`。
- **结论**：问"游戏里显示的那个数"必须读组件 1100；组件号进
  `services/profile_components.py` 的 `METRICS`，读实现在
  `services/activity_counters_service.py`（`activity_assistant(intent="counters")`）。
  查定义要 `to_signed()` 回退（与 `get_bucket_definition` 同套路：uint32 hash 直查 `id` 可能不中）。

### 读取不稳定：会出现整块 metrics 缺失

- **事实**：**同一个 URL 连续请求**，会返回**整块 `metrics` 缺失**（0 条）的响应，
  重试后恢复 402 条。
- **实测**：2026-09-17 真机连续请求复现多次；缺失是**整个 `data.metrics` 为空**，
  不是个别条目丢字段。
- **结论**：读 1100 **必须重试**（判据 = 拿到非空 metrics，用
  `services/write_readback.read_until`）；重试后仍为空时只能如实报"不可用"，
  **绝不能把空当 0** —— "空"和"这个账号一条计数都没有"是两件事。
  这是本项目「缺值给 None，不编 0」在组件读取上的具体落点。

### 与统计接口（`GetHistoricalStats`）的关系

- **事实**：统计接口的生涯数字**分三档**，且**不含**上面那个计数器口径。
- **实测**：账号级 `mergedAllCharacters.results.allPvP.allTime.opponentsDefeated = 78,864`
  （= 现存 3 角色 `50,622` + 已删 5 角色 `28,242`）；`mergedDeletedCharacters = 28,242`；
  单角色最高那是第一个角色（`…5779`）的 `17,703 kills / 12,496 deaths`。
- **结论**：**计数器从 S1 起累计，含统计接口已不再列举的旧角色**，所以它比统计接口大。
  两个数都给、各自带 `source`（`profile.metrics` vs `GetHistoricalStats`），
  不要互相覆盖，也不要用其中一个去"纠正"另一个。

---

## 玩家名：游戏内 ID 与平台名是两个字段（2026-09-17 实采）

- **事实**：玩家结构里 `bungieGlobalDisplayName` + `bungieGlobalDisplayNameCode` 是**游戏内 ID**
  （`名字#1234`），同一账号在**所有平台完全一致**；`displayName` 是**平台 persona**
  （Steam / Xbox / PSN / Epic 各自的昵称），**每个平台都不一样**。
- **出处**：<https://bungie-net.github.io/> 的 `DestinyProfileUserInfoCard` /
  `UserInfoCard`（2026-09-17 查阅，文档 2.21.8）；`User/GetMembershipsById`。
- **实测**：本账号游戏内 ID = `OneTop丶Husky#6641`；四平台 `displayName` 分别是
  `OneTop丶Husky`（Steam）/ `SecHusky`（Xbox）/ `early_moccasin0`（PSN）/
  `此人以嫖到广东`（Epic）。社区反馈的"显示成 Steam 名而不是游戏内 ID"由此而来。
- **结论**：展示玩家名一律走 `destiny_mcp/utils/player_names.bungie_display_name()`
  （游戏内 ID 优先，平台名只作兜底），`tests/test_player_display_name.py` 会扫**裸用
  `displayName` 拼名字**的代码并判红。

