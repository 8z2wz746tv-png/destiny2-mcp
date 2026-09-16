# Changelog

按日期倒序。版本号来自 `pyproject.toml`，tag 用 `v<版本>`。

## 0.4.6 — 2026-09-16

**用户指出"DIM 能操作模组"，一查果然是我们错了。** 两处：

1. **接口选错了**：`_insert_armor_mod` 按"能量消耗 > 0"去选付费接口 `InsertSocketPlug`，
   但 Bungie 的 "free" 指的是**没有材料消耗** —— 官方文档明确 `InsertSocketPlugFree` 覆盖
   "Perks, **Armor Mods**, Shaders, Ornaments"（<https://bungie-net.github.io/>）。
   护甲模组消耗的是能量、不是材料，本来就该走 free。现在**先走 free**，只有上游回
   1663 `DestinyItemActionForbidden`「只能游戏内做」时才退回付费接口；
   403「scope 不够」不再盲目重试（那不是"这个 plug 不免费"）。
2. **OAuth 从来没申请 scope**：授权 URL 只有 `client_id`/`response_type`/`state`/`redirect_uri`，
   一个 `scope` 都没带 —— 所以令牌里没有 `AdvancedWriteActions`，写入被拒成
   403 `AccessNotPermittedByApplicationScope`（DIM 能做，正是因为 DIM 申请了这个 scope）。
   现在授权 URL 带上 `scope=AdvancedWriteActions`；**生效需要用户重新登录一次**
   （`.venv/bin/destiny-mcp-oauth --no-open --timeout 900`），并且 Bungie 开发者后台里
   这个应用要允许该 scope。

守门测试按新决定重写：free 优先、只有 1663 才退回付费、403 不重试付费接口。

真机进度不变：**五件护甲已全部换上**（武器 106 / 生命 30 / 职业 40 / 手雷 165 / 近战 44 / 超能 125，
差的正是那 5 颗属性模组）；重新登录拿到 scope 后再跑一次，模组应当能通过 free 接口装上。

## 0.4.5 — 2026-09-16

真机配装测试打通到"装备全部换上"这一步，靠的是把上游原因如实带出来。

### 关键发现：护甲模组**没法通过 API 装**（Bungie 策略，不是我们的 bug）

把模组步骤失败时的上游原文带出来之后，真因一目了然：

| 操作 | 上游返回 |
| --- | --- |
| 装属性模组（`InsertSocketPlugFree`） | **403** `Access not permitted by application scope`（装护甲模组要 `AdvancedWriteActions` scope，本应用没有） |
| 为腾能量卸掉一颗模组 | **500** `This action can only be done in-game.`（卸/换模组只能在游戏内做） |

以前这两句都被丢掉，只写"模组 4183296050 → '铁能面罩'"，于是看上去像我们的插槽查找又错了。

### 修复

- `_equip_local_unlocked` 的模组步骤失败时带上游原文；识别上面两种"策略限制"后
  **不再当成硬失败**：记下来、继续走完剩下的写入，最后返回
  `mod_in_game` 步骤 + 明确话术（"装备已经换上；这几颗模组请在游戏里手动装"）；
- `_apply_exact_with_recovery` 见到 `mod_in_game` 时**不回滚装备** —— 装备是好的，
  回滚等于把用户要的东西又脱下来；
- `_capture_recovery_state` / `_restore_exact_state` / `_verify_restored_items`
  全部搬进 `services/loadout_recovery.py`（`RecoveryStateMixin`）：
  `loadout_equipment_service.py` 795 → **508 行**，`tests/test_module_size_ratchet.py`
  的上限随之下调到 **520**（上限只降不升）；`tests/test_profile_components.py`
  的调用点钉桩跟着搬家。

### 真机结果（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器）

- 求解 ✅ → 转移 ✅ → **批量装备 ✅ 五件全部换上（且不再回滚）**；
- 五件装备的 `final` 六维求和（**未含计划里的属性模组**）：
  武器 **106** / 生命 30 / 职业 40 / 手雷 **165** / 近战 44 / 超能 **125**；
- 求解器预测（**含**属性模组）：武器 106 / 生命 30 / 职业 30 / 手雷 180 / 超能 125 ——
  差的正是 5 颗属性模组（2× 武器 +10、3× 手雷 +10），**需要用户在游戏里手动装**（Bungie 策略）；
- 全量测试 **1526 passed**。

## 0.4.4 — 2026-09-16

接着真机配装测试往下打：这一版把"模组装不上"的真正原因挖出来了，是**组件请求**的问题。

### 修复

- **只请求 305（插槽）时上游一个插槽都不返回** —— 真机实测：`get_profile(..., [305])`
  返回 **0 件**带插槽；带上清单类组件（102/200/201/205/300）才有 **1627 件**。
  模组插槽读取正是只写了 `[305]`，于是拿到的是一串空数据，接着每件护甲都报
  "找不到模组 X 的唯一兼容插槽"（前几轮一直以为是插槽匹配逻辑的问题）。
  现在统一走 `profile_components.INVENTORY_SOCKETS`，并加了守门测试：
  **要 305 就必须带清单类组件**（裸组件号一律拒收）；
- **插槽缓存里的"空列表"被当成了"这件的插槽是空的"**：装备刚被搬过来时快照里还没有它，
  旧代码只判"键在不在缓存里" → 每个槽都被跳过。现在空列表会触发重读；
- **两处同步窗口重试**（与写入后回读同一个坑）：① 读某件实例的插槽（`write_readback` 重试到非空）；
  ② `equip_items` 的"物品必须在目标角色背包里"预检 —— 刚搬完立刻批量装备必失败
  （报"请先用 move_item 转移"，可转移明明刚成功）；
- **批量装备失败不再吞掉原因**：以前只写"批量装备 N 件物品"，现在带上游原文
  （就是靠这句才看到真正的原因是"物品还没在目标角色背包里"）。

### 结构

- `_capture_recovery_state`（131 行）抽成 `services/loadout_recovery.py`（`RecoveryStateMixin`），
  `loadout_equipment_service.py` 795 → 671 行；`tests/test_module_size_ratchet.py` 的上限
  **随之下调到 680**（上限只降不升）。

### 真机进度（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器）

- 求解 ✅（5 套候选 100% 达标，预测六维 武器106 / 手雷180 / 超能125）；
- 转移 5 件 ✅ → **批量装备 5 件 ✅**（这一版之前必失败）；
- 卡在**"为属性模组腾出能量"的写入**：`mod_clear` 那一步失败（原因同样被丢掉了，
  下一步要补上原文再修）；
- **账号每次都完整回到执行前状态**（逐件核对：五件护甲的属性与位置、仓库里那件都在原位）。

## 0.4.3 — 2026-09-16

真机做了一次完整配装测试（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器），
这一版修的是那次测试暴露出来的东西。

### 修复（真机复现 → 已修）

- **`equip_build` 直接崩**：`LoadoutEquipmentService` 用了不存在的 `self._ARMOR_SLOTS`
  （那只是 `loadout_service` 的模块级常量，而这个类并不继承它）→
  `AttributeError: 'LoadoutEquipmentService' object has no attribute '_ARMOR_SLOTS'`，
  调用方只看到一句空错误。现在改用**单一出处** `build/constants.ARMOR_SLOT_MAP`
  （经 `item_parser.armor_slot_from_bucket` 归一），这条路径以前没有任何测试覆盖；
- **上游超时被吞成空消息**：Bungie 请求超时时 `TimeoutError()` 没有 message，
  `handle_tool_error` 直接重抛，客户端只看到 `Error executing tool …: `（空的）——
  分不清"网络慢"和"参数错"。现在统一翻译成 `a_p_i_error` +
  "访问 Bungie 超时（网络慢或上游没响应），这不是参数问题：稍后重试即可"，并加了守门测试；
- **三份"桶 hash → 护甲槽位"表收口成一份**：`loadout_service._ARMOR_SLOTS`、
  `equip_planner.ARMOR_BUCKET_BY_SLOT`、`item_parser` 里那段两步查找原本是同一个事实的三份拷贝
  （这次崩溃就是其中一份漂了），现在都从 `build/constants.ARMOR_SLOT_MAP` 派生。

### 尚未修复（已知，真机复现）

**护甲模组插槽预检失败**：`equip_build` 执行到"装模组"这一步时，
对多件护甲报 `找不到模组 <hash> 在 '<装备名>' 上的唯一兼容插槽`，
回滚时同样装不回原模组，于是整条配装以
`配装 '已确认的精确配装' 执行失败，且自动恢复不完整` 结束。
实测影响：**账号最终状态与执行前一致**（逐件核对五件护甲的属性与位置都没变），
所以是"没装成"，不是"装坏了"。修复方向与神器模组同源：
按**该件定义里的插槽与 plug set**（配合组件 310 的 `reusablePlugs`）找候选，
不要假定槽位/plug set 的固定结构。

## 0.4.2 — 2026-09-16

装备编排的三处收口（0.4.0/0.4.1 之后自查出来的）：

- **容量来源统一到 Manifest 桶定义**：规划器原先读 `itemComponents.buckets.data` —— 真机上
  **没有这个组件**，读出来永远是空，"背包满"这条预检等于静默失效。现在两处（规划器的
  `load_plan_request` 与执行侧）都只认 `DestinyInventoryBucketDefinition.itemCount`，
  `used` 含正装备那件；
- **装配收成一处**：`transfer_service.plan_equip_item` 改为委托 `equip_planner.load_plan_request`
  （读哪些组件、容量怎么算、`equipped_keys` 怎么来，只在那边说一次），删掉重复实现；
- **槽位在仓库物品上也有值了**：`InventoryItem.slot` 按 bucketHash 认，而**仓库里的护甲**
  bucket 是仓库格、认不出部位（`slot` 为空）—— 装仓库里的护甲正是最需要编排的场景。
  现在缺失时退回定义的 `itemTypeDisplayName`（`armor_payload.slot_key_from_display`），
  回滚因此知道动了哪个部位；
- 守门：新增"每一步都必须带 `slot`"与"桶定义缺失就不下结论"两条；容量那条测试按新口径重写
  （旧测试断言的是不存在的组件）。

真机核对（只读）：`load_plan_request` + `plan_equip` 在真账号上给出
`[downgrade chest=圣贤保护者法袍 顶下星火协议, equip gauntlets=逃逸艺术家]`，
容量 chest/gauntlets/helmet = 10/10、legs/class_item = 9/10。

## 0.4.1 — 2026-09-16

0.4.0 的装备编排补上**失败回滚**：执行到第 N 步失败时，把**已经改动过**的部位换回动手前那件，
再报"停在第几步、账号现在什么样"。

- 回滚只动**真正变过**的部位（失败那一步通常没落地，把它算进去会白写一次 `EquipItem`，
  而每次写入都要等上游同步窗口）；回滚本身失败也照实说 `rolled_back: false` + 哪个部位没换回，
  不许假装干净；
- 计划步骤新增结构化 `slot` 字段（回滚要靠它知道动了哪个部位，不能从 `why` 的中文里猜）；
- 守门 `tests/test_equip_execute.py`（4 条）：顺序（先顶下再装）、失败回滚、回滚失败如实上报、
  上游说成功但回读没看到 → `unverified` 而不是成功。

## 0.4.0 — 2026-09-16

`equip` 从"直通原语"变成**冲突感知编排**：以前装一件金装会撞 `DestinyItemUniqueEquipRestricted` 500、
装仓库里的会撞 404，用户当时摸索了 10 轮才把星火协议穿上；现在同样的链子是**1 次计划 + 1 次确认**。

### `equip` 两段式（行为变更）

- 不带 `confirmed` → `confirmation_required`，`candidates[0]` 里带 `steps[]`
  （每步 `action/item/from_location/to_location/why/replaces`）与 `blockers[]`，**零写入**；
- `confirmed=true` → 才执行「先顶下、再装目标」，逐步 `EquipItem`，写完**回读核对**
  （`verified`/`unverified`；上游 profile 有同步窗口，走 `services/write_readback.py` 重试）；
- 失败说清**停在第几步**（`stopped_at`/`steps_done`）与**账号现在什么状态**（`equipped_now`），
  不允许部分成功报成功；
- `equip` 因此进了 `SELF_GUARDED_WRITE_INTENTS`，分派在 `tools/_equip_branches.py`。

### 四种预检（只读，先做）

`services/equip_planner.py`（纯函数）+ `models/equip_plan.py`：
`item_missing`（实例不在账号上）/ `item_equipped`（正装备着，上游禁 move）/
`exotic_conflict`（另一个槽的异域挡住，且背包里挑不到非异域顶下）/ `inventory_full`（给数字）；
另有"仓库里的物品要先搬"作为计划里的 `move` 步骤。
`EquipPlanRequest` 的入参语义写死在 docstring：装备位 + 背包**合并**列表，`equipped_keys` 按
组件 300 的 `instances.data[实例].isEquipped` 填（205 的条目没有这个字段）。

### 修复

- 新增 `ManifestManager.get_bucket_definition`（`DestinyInventoryBucketDefinition`）：
  背包容量 `itemCount` 的**唯一出处**，内部 `to_signed()` 回退（头盔/臂铠的桶 hash 超过 int32，直查查不到）；
- 容量 `used` **含正装备那件**（与 DIM 同口径）。真机上 warlock 的头盔/臂铠/胸甲都是 10/10，
  旧口径会假报"还能放一件"，然后去撞上游 `NoRoomInDestination`；
- 写入失败话术补两条：`UniqueEquipRestricted` → "全身只能一件异域，先穿一件非异域的同部位顶下它"；
  `ItemNotFound`/"not found in the character's inventory" → "`EquipItem` 只接受在该角色身上的实例"。

### 新增错误码

`equip_blocked`（上游铁律挡住预检：金装冲突/背包满/正装备着/实例不在账号上，**不是写入失败**）。

### 守门

- `tests/test_equip_planner.py`（14 条：四种预检、计划顺序、挑不到中间件、空 `slot_display` 不留空括号）；
- `tests/test_equip_plan_path.py`（5 条：无确认零写入且带计划、确认后按序执行、blocked 走 `equip_blocked`、
  已在身上不写、执行失败保留停点）。

### 真机验证（账号已完全还原）

`equip` 逃逸艺术家 → 计划两步（顶下星火协议、装逃逸艺术家）→ 确认执行 `verified:true` →
换回星火协议与光芒领主手套 → 最终五部位与操作前**完全一致**。

## 0.3.0 — 2026-09-16

补上两个"想做但做不到"的能力：**换子职业元素**与**换神器**；顺带修掉神器模组的槽位假设。

### 换子职业（新能力）

用户报的原始故障：术士从棱镜切烈日，`modify` 只在**当前**子职业上插 plug，
`loadout_subclass_sockets` 又"子职业不一致就拒绝"，于是怎么都换不过去。

- `subclass_assistant(intent="modify")` 新增变更键 `changes={"subclass": …}`：
  先按别名表解析（`烈日/火/火术/棱镜术士/solar/…`）→ 在该角色的子职业物品里按元素或
  **Manifest 官方名**（破晓/枪手/炎阳…）精确匹配 → `EquipItem` → 回读核对 → **然后**才改插槽
  （槽索引属于具体那件物品，顺序反了就是错的）；
- 已经是目标子职业时**不写**（幂等）；找不到就列出这个角色实际有哪些（官方名 + 元素）；
  职业叫法冲突（猎人说"火术"）**报错不硬来**；不模糊匹配、不查拼音；
- 元素别名进 `destiny_mcp/vocabulary.py`（口语词根 `火/电/冰` + 职业尾缀表），
  官方名不写进表 —— 名字的权威来源是 Manifest；
- `equip_loadout` 的子职业不一致由"直接失败"改为**先换上再配**（用户拍板：顺手换）。

### 换神器（新能力）

- `subclass_assistant(intent="equip_artifact")`（写入，走确认信封）：把该角色背包里的另一件
  神器换上，名字精确匹配，回读核对；
- `subclass_assistant(intent="artifact", character=…)` 附带**角色身上那件**与背包里能换的
  （目录里的"当前神器"按赛季算，与身上那件可以完全不同）；
- 事实校正：神器**不可转移**（`transferStatus` 背包=2、装备位=3），所以只能换同角色背包里的；
  神器有三个 hash 家族（玩家实例族 / 赛季定义族 / `DestinyArtifactDefinition`），
  **写入只能用实例的 hash**；真机实测 `EquipItem` 接受神器实例（推翻"只能装最新赛季"的旧说法）。

### 修复

- **写入后回读要重试**：真机实测 `EquipItem` 返回成功、立刻回读仍是旧值（一次约 3 秒可见、
  另一次 10 秒内仍旧）。新增 `services/write_readback.py`（8 次 × 1.5 秒），
  超出窗口时报 `unverified`（**没确认**）而不是"没换成" —— 上游已经返回成功了，那是两件事；
- **神器的"装没装"只在组件 300 的 `instances.data[实例].isEquipped` 上**（205 的条目没有这个
  字段），而且它对所有装备都为真，必须先在神器桶实例里挑；
- **元素不在子职业物品定义里**，只写在 plug 的 `plugCategoryIdentifier` 第二段；
  `_CATEGORY_PATTERN` 抓的是第三段（槽类型），两者不能混用。

### 守门

- `tests/test_subclass_switch.py`（9 条）、`tests/test_artifact_switch.py`（9 条）、
  `tests/test_write_readback.py`（3 条）；
- 组件表新增 `profile_components.SUBCLASS`（含 201：看不见背包就换不了子职业），
  `tests/test_profile_components.py` 钉住集合与调用点数量；
- 分派契约允许分派层拆到 `tools/_*_branches.py`（这次神器一族搬进 `_subclass_branches.py`，
  顺手把 `assistants.py` 压回体量上限内）。

## 0.2.0 — 2026-09-15

**破坏性变更**：活动统计改成行式（见下）。另外这一版收了上一批架构与守门工作。

### 活动统计改为行式（破坏性）

旧实现手写 8 个键、只取 `displayValue`：真机抓下来上游 `allPvE` 有 **65 项**（`allPvP` 66 项），
实际只拿到 6 项，而且 `precisionkills` 拼错（上游是 `precisionKills`）导致「精准击杀」静默消失，
原始数值与场均（`pga`）全被丢掉——想做"PvE 折合多少小时"这类计算只能去解析 `"42d 11h"`。

- 新增 `destiny_mcp/activity_stats.py`：上游 `statId` → 行式统计
  `{stat_id, upstream_id, name, group, value, display, unit?, per_game?}`，
  与武器属性 `{stat_hash, name, value, display}` 同一套写法；
- **全量给项**：65/66 项一项不丢，没登记中文名的也出行（`name` 留空、`group="other"`）；
- **数值回归**：`value` 给原始数（整数还原 int）、`display` 给人读串、时长标 `unit: "seconds"`、
  场均（`pga`）有就给、没有就不给字段（**不编 0**）；
- **中文名 + 分组**：66 个键全部有中文名，按 `core/activities/objectives/averages/weapons/other` 排序；
- 接线：`stats`/`career`/`historical_stats`、`weapon_history`、`aggregate`、
  `leaderboards`/`clan_leaderboards`（排行榜不再透传上游原始载荷，改自有条目形状）；
- **旧键一个不留**（不双写、不别名）：`data.stats.pve.activitiesEntered` 这类写法在 0.2.0 起不存在；
- 守卫：`tests/baselines/activity_stat_keys.json` 存真机抓的上游键清单，
  `tests/test_activity_stats.py` 据此断言「每个键都有中文名」「行数等于键数」「缺值给 None」——
  上游新增统计项会让测试红，逼着做决定，不再静默少项。

### 架构与守门（本版同批）

- **CI**：`.github/workflows/ci.yml`（Python 3.12/3.13：`pytest` + 无凭据构造工具面冒烟）；
- **错误码单一出处**：新增 `destiny_mcp/error_codes.py`（`ErrorCode` 枚举 +
  `code_for_exception()` + `write_failed()`），43 处裸字符串全部替换；
  `tests/test_error_codes.py` 禁止裸字符串、并钉住"异常类名即契约"；
- **词表归一**：新增 `destiny_mcp/vocabulary.py`（六维/职业/位置/元素/旧属性名），
  此前同一份词表散在 9 个文件、值已经漂了——顺带修掉真 bug：
  旧名「韧性模组」被映射成不存在的「生命模组」（游戏里叫「生命值模组」），真机报「没找到护甲模组」；
- **结论路径不许静默降级**：护甲阶梯的探测失败不再被记成"试过没有解"（改 `ok: null` + `not_probed` +
  `reason`），调谐额度拿不到会带 `tuning_unavailable_reason`，参数归属名单读不出会 ERROR 日志 + warning；
  `tests/test_conclusion_paths.py` 扫描结论模块的 `except` 必须留痕；
- **信封统一第二批**：`data` 及其子块不再有 `success`/`message`（写入领域结果除外），
  自有键一律 snake_case（`fragments[].name_en`、金装候选 `name_en`）；`sweep` 组新增两条守卫，
  110 个 intent 全量检查；
- **兼容面定规矩**：`docs/COMPATIBILITY.md` 登记 21 组**实测等价**的别名与 69 个历史工具的去留，
  `tests/test_intent_aliases.py` 防"别名偷偷跑偏"；
- **分层守门**：`tests/test_architecture_layers.py`（依赖只能向下、禁止环、`svc` key 必须在
  `ServiceContext` 里声明）+ `utils/item_parser.py` 挪到 `services/`。

## 0.1.13 — 2026-09-15

新增一份全面语料：`docs/testing/TESTING_CORPUS_FULL.md` + `scripts/run_corpus_all_rows.py`，把八个工具面
**全部 110 个 intent** 的信封体检、其余六个工具面的字段级契约和 MCP 协议层都变成可执行断言。
第一轮真机跑（205 行）抓出 7 个问题，这次一并修掉：

- **`analyze` 不再替求解器下结论**（P1）：`build/analyzer.py` 的兜底文案以前写死
  `No valid armor combination found`，但它只算了单项上限，从来没验证过有没有合法组合 ——
  实机出现过 analyze 说配不出来、**同一组约束** `recommend` 给出 `completion_rate=1.0` 的方案，
  而且 `max_possible.health=134 ≥ 目标 100`。现在如实说明「各项目标都在单项上限内，单看上限
  解释不了」并指路 `recommend`/`find`；两处 reason 与刷取建议也一起中文化（以前是英文）。
- **`exotic_armor` 与 `intent="item"` 统一键集**（P2）：`get_exotic_armor_details` /
  `get_exotic_armor_list` 以前直吐 Manifest 原始键（`nameEn`/`flavorText`/`classType`/`tierType`/
  `intrinsicPerks`），同一件护甲两条路径两套键集。现在走同一个身份块工厂
  （`armor_payload.armor_definition_payload`）：`identity.{name, name_en, slot, slot_display,
  item_type_display, rarity_tier, class_type, class_display, …}` + `intrinsic_perks`。
  定义级没有实例，`gear_tier`/`armor_system` 给 `null` 并附说明，不编 T 级。
- **收藏品 hash 统一无符号**（P2）：`collectible_item` / `collectible_node` 以前直接吐 Manifest
  原值，同一份响应里有正有负（实测 `-2064629060`、`-1315203219` 等，负数拿去别的面按 hash 查
  必然查不中）。现在对外统一 `to_unsigned`，读收藏状态时无符号与有符号两种键都试。
- **`artifact` 不带名字也给当前神器**（P2）：`get_seasonal_artifact("")` 现在也返回
  `current_artifact`，「我现在用哪个神器」不用先知道神器名字。
- **报错不再叠句号**（P3）：`SubclassError` / `APIError` 的包装遇到已经带句号的整句不再拼第二个
  句号（实测出现「…或中文职业名。。」）。
- **背包搜索未命中说「没找到」**（P3）：0 命中时 summary 从「已搜索物品。」改成
  「没找到叫「X」的物品。」；文案抽到 `_formatters.inventory_search_summary`，
  `tools/assistants.py` 的行数上限同步收紧到 1403。
- **`intent="item"` 传 null 不再裸抛**（P3）：`armor_item` 的实例 ID 判断补 `or ""`。
  真 MCP 路径本来就被 schema 层拦住（`string_type`），这是进程内的防御性缺口。

语料第一轮结果：sweep 110 个 intent 全部干净（无异常、无超时、无「ok=true 但 data 空」、
无写入漏网），mcp 组 11 行全过；上面 7 条都是 rows 组抓出来的。回归锁见
`tests/test_corpus_full_regressions.py`（12 条），另外更正了主语料里两处被实测证伪的口径
（「subclass 默认 10 条」其实只有 `community` 读 `limit`；`leaderboards` 不是恒失败而是间歇）。

## 0.1.12 — 2026-09-14

强化版 perk 的展示名后面加 `↑`，普通版保持原字符串不变。

- 约定：`高爆载荷` 是普通版，`高爆载荷↑` 是强化版。适用位置包括
  `weapons[].sockets[].options[].name`、`equipped.name`、实例可换项，以及社区核对里的
  `perks_current_match` / `perks_available_to_switch`。带箭头的项同时给 `name_plain`
  （Manifest 规范名），按名字查表或比对时用它。
- 内部按名字索引的地方（刷取清单 `farm_index`、愿单、`filter_rolls` 的匹配）改用规范名，
  避免箭头影响匹配；`weapon_local_data` 直接读选项上的 `name_plain`。
- 修正 `analyze` 的 `differences` 口径：它以前按名字比对副本，一把普通版加一把强化版会显示成
  双向缺失；现在名字能区分版本，差异才有意义。
- 结构调整：`with_equipped` 从 `weapon_payload.py`（已顶到 365 行上限）移到
  `weapon_profile.py`，该文件上限同步收紧到 334，用搬移而不是抬上限来腾空间。

实机复验（术士邮政长里的「无感」）：特性 1 显示 `现在=快速命中↑ / 可换=[快速命中↑, 即兴弹药↑, 集体爆破↑]`，
特性 2 显示 `现在=高爆载荷↑ / 可换=[高爆载荷↑, 肾上腺素成瘾↑, 盒式呼吸法↑]`；社区核对里
`巅峰捕食者` 的一把为 `perks_current_match=['爆炸光能↑','爆炸协议']`，其余副本是普通版。

同时重录了武器面基线：它从 0.1.6 前后就没再录过，累积了若干版本的漂移（hash 统一为无符号、
`farming_list` 挂载、`name_en` 字段等）。核对过 `catalog_*` / `filter_rolls_*` 的命中集合变化，
是有符号与无符号的同一批物品（例：-1205549507 与 3089417789），不是筛选逻辑变化；
diff 报出的消失项全部是早期登记过的（P4 精简身份块）。

## 0.1.11 — 2026-09-14

把「可切换但未装备的 Perk」真正查出来。0.1.10 只做到如实说明没有查。

- 武器详情新增可选开关 `include_selectable_plugs`（默认关闭，其他调用方载荷不变），打开后给每栏
  挂 `selectable_plug_hashes`（组件 310 的 hash，不展开为带名字的 options）。社区配装匹配走这条路，
  因此每个副本能报三层：`perks_current_match`（当前装备）、`perks_available_to_switch`（可换栏可达）、
  `perks_unavailable`（两者都没有）；行状态依次为 `current_roll_matched` →
  `owned_alternate_roll_available` → `owned_no_current_roll_match`。
- 「没读」与「读了没有」分开：`selectable_plug_status` 取 `available` / `none` / `not_read`，
  只有 `available` 时「换也换不到」才是结论，否则仍是 `alternate_perk_options_checked=false` 加原因。
- 两条边界写进字段：判定范围是 `any_selectable_socket`，不校验栏位是否与模板一致；锻造件的组件 310
  只列当前选中项（`alternate_perk_options_caveat`），未命中不等于换不到。
- 实现过程中修掉一个方向相反的坑：组件 310 的真实形状是 `{"plugs": {"<槽>": [...]}}`，第一版直接遍历
  外层，`_instance_plug_hashes` 收到的是 Mapping，静默返回空集，于是「有 4 栏可换」被报成「读了但没有
  可换项」。实机复验时才发现，`selectable_plug_status` 就是为区分这种情况加的。

实机复验（复盘里的同一套配装）：`笛卡尔坐标` 两把的 `重建/洪涝/老兵睿智` 确证换也换不到
（`perks_unavailable`，`selectable_plug_status=available`）；`巅峰捕食者` 一把的
`爆炸光能`、`爆炸协议` 当前装备，`爆破专家` 换也换不到。

## 0.1.10 — 2026-09-14

按另一台机器的实机复盘（Windows，另一个 agent）修正否定结论的表达。那次事故的链条是：模板要求
「玻璃拱顶 ×4」，工具标 `unresolved`，调用方标「待自查」，最后却回了「这套能直接玩」，
而同一份响应里已经写着 `execution_supported=false` / `execution_eligible=false`。
问题不在数据，是否定结论没有出现在调用方一定会读到的位置。

- summary 带可执行性判定：社区详情返回「已读取社区配装：X；不可直接执行（社区模板不是服务器签发的
  ExecutableBuild）」，并把 `execution_eligible=false`、首要 blocker、以及「要装备必须走
  find → canonical_build → 确认」放进第一条 warning。
- 活动名到套装名的解析：社区模板按活动称呼套装（玻璃拱顶），Manifest 与玩家物品用套装名
  （埃希恩记忆）。已核对的映射会自动解析并说明来路（`resolved_via` / `alias_from` / `resolved_name`）；
  没有登记的返回 `unresolved_reason=name_not_matched_candidates_available` 和 `set_name_candidates`
  相似候选，调用方可以据此向用户确认，而不是只得到「查不到」。
- 套装持有数量可以直接读：套装行新增 `owned_count`、`owned_distinct_slot_count`、
  `missing_slot_count`、`wildcard_count`。实机复验同一套配装：玻璃拱顶解析为埃希恩记忆，需 4 件，
  持有 20 件覆盖 5 个部位，缺 0。
- 「没校验」与「没有」分开：所有 `not_account_checked` 行带 `unverifiable_reason` 枚举
  （`mod_unlock_state_not_available`、`artifact_*`、`subclass_unlock_state_not_available`、
  `stat_feasibility_not_checked`、`free_text_not_checkable` 等）；`unresolved` 行带
  `unresolved_reason`；武器行补 `alternate_perk_options_reason=instance_socket_options_not_read`，
  警告文字改为说明 `owned_no_current_roll_match` 只表示当前选中的 Perk 不符。
- 参数说明补上各 intent 读取范围：`character` 一栏列出会返回 `ignored_parameter` 的 intent；
  `component` 一栏写明碎片不属于 component，应使用 `intent=fragments`。

当时仍未做的：真正的「可切换但未选中」比对需要武器详情载荷带上实例可换项（组件 310），
属于武器面改动，有自己的基线与体积上限，留到 0.1.11 完成。

## 0.1.9 — 2026-09-14

干净安装冒烟（从 GitHub 安装 v0.1.8 到全新 venv）发现两处一致性问题。

- 包内 `__version__` 停在 0.1.0：无论发布到哪个版本，`destiny_mcp.__version__` 一直写着 0.1.0。
  现在改为源码树读 `pyproject.toml`、安装后读发行版元数据，`tests/test_package_version.py`
  校验两处一致。
- 注册模板里的客户端超时 180 秒余量偏小：无解诊断实测 80–165 秒，还要叠加调谐补齐那一趟，
  慢一次就会被客户端中断，用户看到的是超时而不是阶梯。模板与文档统一改为 300000 ms。

冒烟结果：从 `git+https://…@v0.1.9` 装进全新 venv，8 个工具握手成功；复用 tokens 与 manifest 后
真机读取正常；技能安装器在临时 `DSH_HOME` 下正确写出指针块与 MCP 注册文件。

## 0.1.8 — 2026-09-14

真机写入实测发现两个发布阻断问题。

写入失败被报成成功。`ArmorModService.apply()` 收到的形状是 `{"ErrorCode": …, "Message": …}`，
Bungie 把错误也放在 200 响应的信封里，而这里以前不看内容直接返回 `success: True`。
实测换调谐时账号一个字节都没变，工具却回了「已把槽 11 换成 +手雷 / -职业」。现在必须核对
`ErrorCode == 1`，否则抛 `TransferError` 并带出 Bungie 原文；权限类错误
（`AccessNotPermittedByApplicationScope`）会点名 `AdvancedWriteActions`。回归测试写进
`tests/test_equip_mod.py`，假客户端改成 Bungie 的真实信封形状（以前回 `{"success": True}`，
正好遮住了这个 bug）。

调谐无法通过 API 写入，据此改了设计：免费插槽接口（`InsertSocketPlugFree`）对调谐返回
`This action can only be done in-game.`（ErrorCode 1663）；付费接口（`InsertSocketPlug`）需要
Bungie 应用的 `AdvancedWriteActions` 权限，当前授权没有，返回 403。因此 0.1.6 与 0.1.7 里
「`canonical_build` 已带上调谐插件、确认即可执行」是做不到的承诺。本版改为：`equip_mod` 对调谐
只给方案（`writable=false`、`written=false`，附「只能在游戏内改」的 warning，`confirmed=true`
也不写账号）；`canonical_build.items[].mods` 不再包含调谐插件；`tuning_changes` 明确为给玩家的
手动清单，`tuning_note` 同步改写。

其他：README 补充写入权限、调谐只能游戏内修改、无解诊断 80–165 秒与客户端超时建议；
语料护甲章节新增两行（调谐只给方案、付费写入如实报权限），共 26 行。

## 0.1.7 — 2026-09-14

修掉调谐救援的候选池上限。求解器只保留排名前 200 套（`RETURNED_ARMOR_SETS`，沿用 DIM 的设计），
而放宽目标那一趟的池子按放宽后的目标排名，可救的方案可能排在 200 名之外，表现为明明能补却报补不上。
实测（猎人 118 件护甲，目标武器 150 加生命 103）：池上限 200 时救回 0 套，放到 1500 时救回 16 套。

修法两步：`solve()` 增加可选参数 `returned_sets`（不传即旧行为），放宽那一趟传 1500；
池子变大后无法逐套精确复核（每套约 0.1 秒，1500 套约三分钟），改为按「护甲加调谐额度之后还差多少」
排序，只复核最值得的前 40 套，复核仍是唯一裁判。修完实测：武器 150 加生命 103 从 0 候选变为
5 套候选、每套只改 1 件调谐（47 秒）；武器 150 加生命 106 为 5 套、2 件调谐（36 秒）。

已知限制：救援覆盖放宽解排名前 1500 套且复核能过的方案，再往后的套仍然看不到；
挑候选用的是算术估计，理论上可能把可救的套排在 40 名之外。

## 0.1.6 — 2026-09-14

调谐进入求解器。此前目标差 5 点只能给人工提示，现在 `find` 与 `recommend` 会实际尝试更换调谐再算一遍，
能补上就直接返回带方案的候选。

- 做法是两趟加精确复核：按原目标解一次，达标就原样返回（基线里的绿方案不变）；没达标才按调谐额度
  放宽目标复解，然后对候选逐套精确复核，用真实目标重新分配属性模组并逐项比对六维，通过复核才算数。
- 对外字段：`builds[].tuning_changes`（逐件 from/to/delta，中文名加 hash）、`requires_tuning`、
  `tuning_note`，以及响应级 `tuning` 汇总。
- `equip_mod` 支持调谐：报全名（如 `+武器 / -生命值`）即可，方案里给 `kind="tuning"`、
  `stat_bonus`（含 −5 一侧）与 `energy`；只说「手雷调谐」这类未指明减少项的说法会报错并列出 5 个选项。
- 阶梯口径改实：`tuning_first` 变成「已经试过调谐」之后的结论，新增 `solver_attempted` 与三种 lever
  （额度够但无法让步、额度不够、只看属性模组）；新增 `verdict`，`satisfiable=false` 表示按原始优先级
  实采 0 候选，并说明 `ceiling` 是各次探测逐项取的最大值、不等于同一套能同时达到。

实机验证（118 件护甲的猎人，只读）：调谐额度实测为每个部位取最强的一件、五项合计 25 点，
含撤掉反向调谐的 +10 情况，所以单件上限是 10 而不是 5；`武器 150 加生命 103` 严格解为 0 候选，
放宽复解后给出 4 套达标方案并逐件列出调谐改动；`武器 150 加生命 106` 返回
`verdict.satisfiable=false`，与手算边界一致（护甲 110/52 加模组 50 加调谐最多 33，凑不出 150/106）。

对比 DIM（读源码后的结论）：DIM 在主循环里展开调谐变体，非金装逐件展开成多个 ProcessItem，
金装因为一套只能有一件而改成在主循环里换 variant，并用 dump stat 把变体数压到个位数。
本项目的组合上限是 2000 万，扛不住那种展开量，所以采用同样思路的收敛版：零和语义加牺牲价值最低的一项，
能否达标交给精确复核裁决，另加一个复核驱动的局部搜索兜底（最多改 5 件，每步都过复核）。

过程中修掉的实机问题：调谐额度把整个仓库相加（118 件得出每项能补 563 点）；规划基线把求解器已配的
模组又加一遍；剪枝按每件最多 +5 计算，漏掉撤掉反向调谐等于 +10 与模组预算，把可行组合整支砍掉；
`plan_tuning` 把 −5 打在本就为 0 的属性上当成有代价（游戏里属性下限为 0，属于白给）。

## 0.1.5 — 2026-09-13

L2 冒烟集全跑（25 条 ⭐ 行）发现两个问题。

- 0.1.4 重构 `analyze` 提前返回时，把 `precision="not_computed"` 一并删掉了：超规模时 `reason`
  说明没算，`precision` 却报 `exact` 且 `max_possible={}`，调用方会读成什么都达不到。已补回，
  并新增 `tests/test_build_size_guard.py`（4 条）固定该口径：超限必须
  `precision="not_computed"` 加空 `max_possible`，analyze 与 find 两条路同一句说明。
- 语料写错了路径：武器 T 级那条原写 `analyze` 的 `weapon.gear_tier`，实测 `analyze` 的 T 级在
  `data.inventory.instances[].weapon.gear_tier`（逐副本），`type` 才是
  `weapons.items[].weapon.gear_tier`；`gear_tier_note` 也并非处处都有（`owned.instances[]` 里有，
  `analyze` 的副本块只有 `gear_tier`）。语料行按实测改写，武器逐行脚本新增第 17 行锁住这三条路径。

冒烟集结果 26/26 PASS（25 条 ⭐ 加工具面核对），覆盖 8 个工具、写入拦截、参数误用指路、
schema 层拒绝、社区资料不可信提示、护甲四条新行。

## 0.1.4 — 2026-09-13

修 0.1.3 实测跑出来的 5 个问题。

- `rarity` 中文值被静默忽略：参数说明写着传说/异域可用，代码只映射英文，取不到就跳过过滤，
  问有哪些异域腿甲会把传说件一起返回（实测 `异域` 26 件、`exotic` 8 件）。现在中英结果一致，
  不认识的稀有度直接报错并列出可用取值。
- `recommend` 与 `find` 在组合规模超限时会跑到客户端超时：术士同参数下 `analyze` 秒回
  「2.43 亿组合超上限、未计算」，而 `recommend` 会真的枚举，客户端拿到
  `-32001 Request timed out`。现在两条路共用同一套估算与同一句收窄建议，超限立即返回
  `not_computed` 和四条可操作建议。
- `equip_mod` 的 `alternatives[].stat_bonus_hashes` 口径不一致（原始 hash，还把非六维的费用属性算进去），
  改成与 `to.stat_bonus` 同一套六维可读键。
- `with_slot_keys` 会把展示字段塞进 `canonical_build.items[]`，改为递归时跳过 `canonical_build` 子树，
  并加断言。
- `intent="item"` 的 `next_actions` 还写着「换模组后续阶段提供」，改为指向 `equip_mod` 的正确用法。

另外修了差异闸门自身的问题：`--allowlist` 传相对路径时，报错分支的 `relative_to` 会崩，
把真正的字段消失吞成一条 traceback。语料护甲章节从 18 行扩到 20 行，冒烟 ⭐ 25 条。

## 0.1.3 — 2026-09-13

护甲部分统一格式、支持更换单个模组、无解时给出六维阶梯。

此前护甲在六个地方出现且字段各不相同，也无法更换单个模组（默认工具面没有写入入口，
legacy `apply_mod` 又不经确认直接改账号）。本版按 `docs/plans/ARMOR_FORMAT_PLAN.md` 的 P0–P6 完成：

- 统一载荷 `ArmorPayload`：`identity`（`slot`/`slot_display`/`gear_tier`/`archetype`/套装）加
  `instance`（光等、位置、能量、大师、调谐）加三层属性 `roll`/`base`/`final` 加插槽清单。
  列表保持轻量（只加 `slot`/`slot_display`/`gear_tier`/`armor_system`，`bucket_type` 保留）。
- 新增 `inventory_assistant(intent="item")`：单件护甲的完整载荷，词条本体槽与 `intrinsics`
  标 `editable=false`。
- 新增 `inventory_assistant(intent="equip_mod")`：更换一个模组。`confirmed=false` 时返回
  「哪件护甲、哪个槽、从什么换成什么、能量怎么变」的确认请求，确认后才写；校验实例是否在该角色身上、
  该槽是否接受该模组、能量是否足够。legacy `apply_mod` 收编到同一条确认路径。
- 无解时返回 `ladder`：`shortfall`（差多少）、`ceiling`（同一套约束下同时能达到的上限，实采）、
  `single_stat_ceiling`（单项上限，两者不能混用）、`trials`（逐级放松试了哪些档）、
  `suggestion`（最小可行降档，只是提议，不自动降目标）。
- 装备确认逐件预览：`candidates[0].items_preview` 给出五件的光等、能量、现有模组与将要装的模组。
- 实机勘测修掉两个问题：老护甲也带 `gearTier: 0`（按字段分族会误判为 3.0）；异域护甲的固定属性
  分布在 `intrinsics` 里（不算进 `roll` 会把大师等级算成 30）。
- 文档：`routing.md` 补护甲统一键口径与 `find`/`recommend` 分工（要装备走 `find`）、`ladder` 读法；
  `docs/testing/TESTING_CORPUS.md` 新增「十六、护甲」章与逐行脚本 `scripts/run_corpus_armor_rows.py`（12 行）；
  护甲基线 19 例（`--surface armor`）。

## 0.1.2 — 2026-09-13

技能只装给正在对话的那个 Agent。

此前 `install_skill.py` 会把技能铺给本机探测到的每一个宿主（Codex、Claude、Cursor 等），
对新用户来说是错的：他在某个 Agent 里提问，只需要那个 Agent 用上这套 MCP。

- 默认只装当前宿主，靠环境变量识别：`DSH_HOME`/`DSH_SHELL` 对应 dsh、`CLAUDECODE` 对应 claude、
  `CODEX_*` 对应 codex；识别不出来才退回旧行为。`--all` 保留全装，`--host <name>` 显式指定，
  `--list` 会标出当前宿主。
- 新增 DeepSeek Harness 宿主支持：技能根 `~/.dsh/skills/`（DSH 热发现，装完不用重启），
  全局指令文件 `~/.dsh/AGENTS.md`。
- 新增 `--mcp`：注册 MCP 服务器。DSH 直接幂等写入 `$DSH_HOME/profiles/web/cordis.patch.yml`
  （带 `destiny2-mcp:mcp-begin/end` 标记，先备份、重复运行只更新），工具以 `mcp__destiny__*` 出现；
  Claude Code 与 Codex 只打印可直接粘贴的 `claude mcp add` / `codex mcp add` 命令，不改它们的配置。
- README、AGENTS.md、安装 Skill 把「装给提问的 Agent」写成显式规则，并补上 DSH 的注册步骤。

首次安装的耗时预期也写进 README：`pip install -e .` 约 5 分钟（下载依赖，无进度条），
预构建 Manifest 685 MB 约 5 分钟。

## 0.1.1 — 2026-09-13

修 P0：干净环境安装后无法启动。

- 原因：`pyproject.toml` 只写了 `mcp[cli]>=1.27.2`，从零安装会解析到 mcp 2.2.0；2.x 把
  `mcp.server.fastmcp` 改名为 `MCPServer`，服务在 import 阶段即失败，自检报
  `VERIFY_FAILED=MCPError: Connection closed`。开发机装着 1.x，本地测不出来。
- 改为 `mcp[cli]>=1.27.2,<2`，并新增 `tests/test_dependency_bounds.py`（上界、lock 钉住 1.x、
  代码确实使用 v1 API，三条一起才算完整约束）。
- 验证方式：从 GitHub 克隆到干净目录，建立 venv 并 `pip install -e .`，下载预构建 Manifest，
  完成 OAuth 登录，`verify_mcp.py` 全绿（8 工具、`BUNGIE_PROFILE_CHECK=ok`），12 项真机冒烟
  全部符合文档。此时安装到的是 mcp 1.30.0。

`v0.1.0` 的 tag 停留在修复前，请使用 `v0.1.1`。

## 0.1.0 — 2026-09-13

第一次公开快照：本地运行的 Destiny 2 MCP 服务器，通过 8 个面向自然语言的聚合工具
（`player_assistant` / `inventory_assistant` / `weapon_assistant` / `build_assistant` /
`loadout_assistant` / `subclass_assistant` / `activity_assistant` / `world_assistant`，
共 108 个 intent）读取 Bungie 账号、Manifest 定义与本地社区资料。

能做什么

- 以只读为主：角色概况、模糊找人、仓库与背包检索、武器词条与 god roll 标注、护甲词条反推、
  商人货架、单场结算、收藏品解锁状态、官方配装与本地配装读取。
- 写入类操作（转移、装备、改模组、存配装）一律先返回确认请求，`confirmed=false` 时不触碰账号；
  装备配装还要求传回服务端签发的 `canonical_build`，自拼 hash 会被拒绝。
- 证据分层：账号数据、Manifest 定义、社区资料三类来源在响应里分开放，缺数据就说缺数据，
  不把没扫完答成你没有。

安装与运行

- Python 3.12+；`pip install -e .`；自带 OAuth 登录助手与 MCP 自检脚本。
- 首次启动需要约 717 MB Manifest（可先取 `manifest-data-v1` 预构建库）。
- 只暴露 8 个聚合工具；69 个历史工具需要 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 才出现。

为公开发布做的准备

- 修正 README 推荐的启动方式：`python -m destiny_mcp.server` 会让配装求解的子进程起不来，
  改为 `python -m destiny_mcp`。
- 自检脚本不再误报：`DESTINY_OAUTH_REDIRECT_URI` 未写进 `.env` 时按运行时默认值判定；
  缺 Manifest 时自动把超时放宽到 1800 秒并打印原因；venv 路径与 token 权限检查按平台分支
  （Windows 不再必然失败）。
- 登录助手缺 `openssl` 时给出提示并指向 `--manual`，不再直接抛出 traceback。
- `equip_build` 传错 `canonical_build` 时改为中文说明（缺哪些字段、应先运行哪个 intent）。
- 护甲模组筛选补 `match` 口径：词表外的词只在名字或描述里命中时标 `kind="keyword"` 并给 warning；
  词表内 0 条时说明本地数据里没有。
- 补 `LICENSE`（MIT）与 `pyproject` 的 license 元数据；README 增加「前置条件与已知限制」，
  写明平台支持、审计日志落盘位置（`~/.destiny_mcp/audit/`，明文、不上传）、
  跑测试需要 `pip install -e ".[dev]"`。
- 删除长期失效的 `Dockerfile`（引用了不存在的 `src/`）；补 `tests/conftest.py`，
  干净克隆（没有 `.env`）也能跑全量测试。

已知限制见 README「前置条件与已知限制」与 `docs/testing/TESTING_CORPUS.md` 的「已知问题」。
