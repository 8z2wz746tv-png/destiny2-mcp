# 护甲格式统一与能力补齐 · 开发计划 v2（数据勘测版）

日期：2026-09-13　状态：**已完成（P0–P6 全部落地，见文末「完成记录」）**
方法沿用 `WEAPON_FORMAT_PLAN.md`；本版把 v1 里"凭印象"的部分全部换成实测。

---

## 0. 先更正两条（v1 里的错误）

| v1 说的 | 实测 |
| --- | --- |
| 至高狂徒腿铠"542 光等" | **550**（`primaryStat.value=550`）。同一件的另一个副本（当前穿着）是 540。**是我写错，工具没错** |
| "读不到插槽/能量" | 要限定为**工具载荷里读不到**。底层组件（300/304/305）本来就拉了，能量也有值（`capacity 11 / used 0 / unused 11`） |

---

## 1. 数据勘测（本账号 478 件护甲 + Manifest 全量 plug）

### 1.1 护甲分两族，规则完全不同

| | **Armor 3.0（有 T 级）** | **老护甲（无 T 级）** |
| --- | --- | --- |
| 本账号件数 | T5 **418**、T4 3、T3 3（T1/T2 **无样本**） | 54 |
| 插槽总数 | **12** | **15** |
| 词条原型 | 有（`armor_archetypes`，12 种） | 无 |
| 词条数值 | 3 个 `armor_stats` 槽承载（不可手改） | 5 个 `intrinsics` |
| 大师升级 | `v460.plugs.armor.masterworks`：给能量容量 + 给**最低 3 项**属性 +1…+5 | `...masterworks.stat.resistance_2`：给抗性，**不是**加六维 |
| 能量容量 | T5/T4 = **11**，T3 = **10** | **10** |
| 六维分配 | 网格：主 30 / 副 25 / 第三 20，其余 0 | 连续值，不落网格（实测 生命27/近战10/手雷18/超能8/职业4/武器12，总和 22–93） |

### 1.2 T5 的 12 个插槽（用你的至高狂徒腿铠逐槽验过）

| 槽 | 类别 | 作用 | 这件现在的状态 |
| --- | --- | --- | --- |
| 0 | `enhancements.v2_general` | **属性模组**（每件仅 1 个；+5 花 1 能量 / +10 花 3 能量，共 28 种） | 空 |
| 1–3 | `enhancements.v2_legs` | **部位模组**（3 个；纯功能，无属性，82 种） | 3 个空 |
| 4 | `shader` | 着色器 | 默认着色器 |
| 5 | `v460.plugs.armor.masterworks` | **大师升级**：容量 + 最低 3 项 +N | 升级护甲（容量 11，未满级） |
| 6 | `armor_archetypes` | **词条原型**（只是个标签，本身不带数值！） | 高能者 |
| 7–9 | `armor_stats` | **三条词条数值本体** | 武器 30 / 超能 25 / 手雷 20 |
| 10 | `armor_skins_empty` / `armor_skins` | 皮肤 | 默认皮肤 |
| 11 | `...armor_tiering.plugs.tuning.mods` | **调谐模组**：+5/−5 定向（32 种，含"平衡调整"+1×6） | 空 |

**词条规律（418 件 T5 全部吻合，0 例外）**：

```
槽7 = 主属性 = 30     槽8 = 副属性 = 25     槽9 = 第三属性 = 20（从剩余 4 项随机）
其余三项 = 0；六维基础总和恒为 75
```

12 种原型固定"主/副"两项，例如 **高能者 = 武器(主30) + 超能(副25)**；楷模典范 = 超能30/近战25；掷雷手 = 手雷30/职业25。

**属性上限是叠加出来的**（不是 30 封顶）：
`词条(≤30/25/20) + 大师(满级给最低 3 项各 +5) + 调谐(±5 或 +1×6) + 属性模组(槽 0，+5 或 +10)`。
实测例：`相对主义` 基础 近战25/手雷20/超能30 → 最终 生命5/职业5/武器5/近战25/手雷20/超能30（大师把三项 0 顶到 5）。

**T 级差异**（本账号样本，样本量小如实标注）：T5 = 30/25/20（418 件零例外）；T4 实测三件 (30,24,16)/(30,24,18)/(30,25,20) → 合计 70/72/75；T3 实测 (29,22,14)/(30,23,15)/(29,22,16) → 合计 65/68/67；**T1/T2 本账号无样本**。

**T 级规则能不能从 Manifest 读出来？不能（已验证）**：拿 T3/T4/T5 各一件比对 `armor_stats` 槽的 plug set 值域，三件**完全相同**（槽 7 ∈ 28–30、槽 9 ∈ 8–20）。也就是说词条是掉落时在实例上生成的，定义里只有值域、没有分档规则。

**外部资料（非官方）与本地数据冲突**：[skycoach 的 Armor 3.0 指南](https://skycoach.gg/blog/destiny/articles/armor-3-0-guide) 给出分档总量 T1 48–53、T2 53–58、T3 59–64、T4 65–72、T5 73–75；我账号实测的 T3/T4 合计（65–68 / 70–75）**比它高**。一致的一点是能量：T1–T3 = 10、T4/T5 = 11（我这边 T3=10、T4=11、T5=11）。

→ 结论写进实现口径：**不写死各档总量**，一律按实例读（`armor_stats` 槽 + `gearTier`）；只对 T5 建"可反推"规则，其余在响应里明确说"不支持反推 + 原因"。原型以 Manifest 的 **12 种**为准（外部指南只列 6 种，已过时）。

### 1.2b P1 实测补上的两条规则（都影响分族与属性分层）

1. **分族判据不能用"组件里有没有 `gearTier` 字段"**：54 件老护甲也带 `gearTier: 0`，但它们是
   15 槽布局、没有词条原型/调谐槽。按字段判会把这 54 件误判成 3.0，然后套一套不存在的规则。
   正确判据是"有可用的 T 级（1–5）"。
2. **异域护甲的固定属性分布藏在 `intrinsics` 插槽里**，不在 `armor_stats`：实测
   「相对主义」= 超能30/近战25（来自 `至纯光能之灵`）+ 手雷20（来自词条槽）。把 `intrinsics`
   算进 `roll` 之后，大师的实际生效量才回到 1–5；否则会出现 `masterwork.level=30` 这种假象
   （实机验证时 27 件中招）。

顺带确认：大师与"平衡调整"的实际生效量都只落在**最低三项**上（plug 定义里写的是"六维各
+5/+1"，照它算会多算），所以载荷报的是**实际生效量**，并把 plug 的声明值单独放
`declared_delta` 备查。

### 1.3 套装 / 外观

- 每件带 `equipableItemSetHash`；套装效果是 **2 件 / 4 件** 两档（实测 Atheon's Memory：2 件 Radiolaria Breach、4 件 Collective Power）。你拥有最多的一套有 41 件。
- 皮肤（`armor_skins`）与着色器（`shader`）都是插槽，当前值可以读出来。

### 1.3b 突袭模组槽：新旧护甲的差别在这里（实测）

突袭/特殊模组槽是**物品定义上的插槽**，类别形如 `enhancements.raid_vXXX`。判定必须**按插槽逐个查它的 plug set**，不能按"N 个类别命中"粗筛 —— 我这次就用粗筛踩了假阳性：`rivens_curse` 命中 4557 个护甲定义，因为**一般模组槽本来就能插 Riven's Curse**。

现存带突袭槽的护甲（Manifest 定义数 / 中文名）：

| 类别 | 定义数 | 套名 | 对应突袭 |
| --- | --- | --- | --- |
| `raid_v600` | 15 | 共振狂怒 | 门徒誓约 |
| `raid_v620` | 15 | 乌空／战神／虫语 | 王者陨落 |
| `raid_v700` | 15 | 剧痛／惊恐／憎恶 | 梦魇根源 |
| `raid_v720` | 15 | 死亡歌者／碎志者 | 克罗塔的终结 |
| `raid_garden` | 30 | 权势／正义／诱惑／赞美／超越 | 救赎花园 |
| `artifice` | 9 | 回音套 | 大师地牢（精工） |
| **`raid_v520`** | **0** | — | **玻璃拱顶：模组本体还在（12 条，Anti-Oracle 等），但已无任何护甲定义能吃它** |
| `raid_v800` | 0 | — | 救赎边缘 |
| `raid_descent` | 0（粗筛） | — | 深石地窖：类别里有 5 条 augment，粗筛会漏，精确判定要按插槽查 plug set |

本账号实测：两件「至高狂徒腿铠」（550 与 540）**都是 T5、12 槽**，特殊槽只有"一般模组槽可插 Riven's Curse"，**没有玻璃拱顶槽**；全号只有一个真突袭槽（`raid_v700`），另有 `rivens_curse` 6 件、`artifice` 53 件、`artifice.exotic` 80 件。

**苏拉娅·霍桑（hash 3347378076）** 的分类是：突袭(2)、等级奖励(6)、消耗品(2)、**传承聚焦破译**(submenu)、任务(1) —— "传承装备"在子菜单里。但**实现上不建议维护名单**：按插槽 plug set 判定更可靠，也自动跟着 Manifest 更新。

### 1.4 这些数据推翻/修正了 v1 的哪些设计

1. **统一载荷必须分族**：`armor_system: "armor_3" | "legacy"`，legacy 没有的概念（原型/调谐/词条反推）**不出现**，而不是给一排 `null`。
2. **"六维"必须分三层**（现在只给了一层）：
   - `roll`：三条词条本体（30/25/20）+ 原型 → 回答"这件是什么词条、能不能反推"；
   - `base`：去掉可换部件（模组/大师/调谐）→ 配装计算用；
   - `final`：游戏里看到的那个数 → 用户核对用。
3. **调谐槽是免费的 ±5 杠杆**，这直接改变 P5 阶梯的算法：缺口 ≤5 时先试调谐（+武器/−生命 或 平衡 +1×6），而不是去刷装备。求解器**已经建模**（`armor_rules.ArmorTuningOption`、farm_target 的 `_virtual_rolls`），但载荷里没暴露，所以 agent 完全看不到这条路。
4. **`farm_target` 的 `unverified_armor3_baseline` 现在有准确解释**：只对 T5 + 合法词条模板反推；T3/T4/legacy 的规则样本不足。计划里改成"明确说'这件不支持反推，因为 T3/老护甲不在建模范围'"，而不是笼统拒绝。
5. **换模组的校验有据可依**：属性模组只能进槽 0（每件最多 1 个）；槽 1–3 是部位功能模组（无属性）；槽 7–9 是词条本体**不可手改**；能量容量 11。

---

## 2. 目标形状：`ArmorPayload` v1（分族、三层属性、按需带插槽）

```
identity   : {item_hash, name, name_en, slot, slot_display, class_type, icon_url,
              gear_tier, gear_tier_note,                 # null = 无分级
              armor_system:"armor_3"|"legacy",
              archetype:{hash,name,primary_stat,secondary_stat}?,   # 仅 armor_3
              set:{hash,name,owned_pieces,bonus_tiers:[{count,perk}]}?}
instance   : {item_instance_id, location, character_id, power, is_equipped, quantity,
              energy:{capacity,used,unused}?,            # 老护甲也给（10）
              masterwork:{level,stat_bonus}?,            # 由槽 5 的 plug 反推
              tuning:{hash,name,delta:{stat:+5,stat:-5}}?,
              verification:{roll_verified, roll_parse_error}}   # 仅 armor_3
stats      : {roll:{...}, base:{...}, final:{...}, notes:[...]}   # 三层，算不出的层给 null + notes
sockets    : [{index, category, category_display, plug_hash, name, energy_cost, editable, empty}]?
source     : {sourcing:{...}}?
armor_schema_version : 1
```

约定：

- `slot` 统一成求解器键（`helmet/gauntlets/chest/legs/class_item`），显示名另开 `slot_display`（"腿部护甲"）。列表里现在的 `bucket_type: "Leg Armor"` 要换掉。
- **`editable: false` 标出词条本体槽**（7–9），让 agent 别去"改词条"。
- 列表默认**不带** `sockets`；`include_sockets=true` 才带。`roll`/`base` 同理按需。
- **插槽类别用短键**：实测一条护甲的 sockets 原始载荷 783 B、精简后仍有 769 B，全被 `enhancements.v2_general` 这种长字符串占掉；换成短键（`general`/`helmet|arms|chest|legs|class_item`/`archetype`/`roll`/`masterwork`/`tuning`/`shader`/`skin`）可压到 ≈300 B。
- 未知一律 `null` + `notes`，**不编默认值**（待刷虚拟件的能量就是 `null`）。

---

## 3. 分阶段

闸门固定：`pytest` 全绿 → `verify_mcp.py`（`PARAMETER_GUARD=ok` + 8 工具）→ 护甲语料真机逐行 → 基线 diff 无「无理由消失」→ 真机冒烟。

### P0 录基线（不改产出）
- 泛化武器那套工具：`capture_surface_baseline.py --surface armor`（或 `tests/baselines/armor_responses/`），用例覆盖：按部位列表、按稀有度列表、单件详情、`exotic_armor`、`farm_target`、`community_build.inventory_match`、`equip_build` 确认回显。
- 单独一份 `armor_response_allowlist.json`。
- **验收**：可重放；护甲用例 0 条无理由消失。

### P1 形状与工厂
- `destiny_mcp/services/armor_payload.py`：`armor_payload()`、`lean_identity_keys`、`sockets_list`、`ARMOR_SCHEMA_VERSION = 1`。
- 三层属性：`roll` 由 `armor_stats` 槽的 plug 值直接读（**不推断**）；`base` = final 去掉模组/大师/调谐（复用求解器里已有的剔除逻辑，搬到公共位置）；`final` = 组件 304。
- 大师等级与调谐增量由装着的 plug 反推（plug 的 `investmentStats` 里就有 +N 和 ±5）。
- **验收**：单测覆盖 T5 全空 / T5 有模组 / 有调谐 / 老护甲 / 无插槽数据 五种件；键集合快照。

### P2 单件详情入口
- `inventory_assistant(intent="item", item_instance_id=…)` → 完整载荷（含 sockets/energy/roll/base/final/masterwork/tuning/verification）。
- 列表保持轻量（不加 305）。
- **验收**：真机取 `至高狂徒腿铠` → 读出 T5、能量 11、`roll={武器30,超能25,手雷20}`、`archetype=高能者`、槽 0/1–3 空、槽 7–9 `editable=false`。

### P3 五处统一
- 列表 / `exotic_armor` / `farm_target` / `set_bonus` / `community_build.inventory_match` / `equip_build` 回显全部改读同一套 `identity` + `instance`。
- `equip_build` 确认回显补：每件光等、能量、每个模组槽"现在是什么 → 换成什么"。
- **验收**：基线 diff 只允许登记过的键改名；routing.md + 语料护甲章节同步。

### P4 换单个模组（`equip_mod`）
- 读：P2 的 `sockets` + `energy`。
- 写：复用 `loadout_mod_sockets._insert_armor_mod`。
- 校验（全部有数据支撑）：①护甲属于该角色 ②**槽类别匹配**（属性模组 → 槽 0；部位功能模组 → 槽 1–3；**槽 7–9 拒绝并说明"这是词条本体"**）③能量预算 `used − 旧 + 新 ≤ capacity` ④模组已解锁 ⑤必须传 `item_instance_id`（同名副本 550/540 要分清）。
- 确认文本：`至高狂徒腿铠（腿部护甲，550，T5）槽 0：空 → 纪律模组（+10 纪律），能量 0/11 → 3/11`。
- 收口 legacy `apply_mod`（现在是无确认直写）。
- **验收**：三类错误各自的 `invalid_argument_error` 文案；`confirmed=false` 不改账号；真机只做确认前冒烟。

### P5 六维优先级阶梯（你的方案 + 三步补足，按实测修正）
- 解析模板六维：`~` 不进优先级、`0` 明确不要、`N+` 下限、`A～B` 下限当硬约束/上限当软目标、平级保留并列。
- **调谐优先**：缺口 ≤5 时先给"靠调谐补"的方案（每件一个 ±5 杠杆，或平衡 +1×6）。
  **注意这是新增工作量**：实测调谐目前只在 `farm_target` 的虚拟件里建模（`armor_rules.tuning_options` / `apply_tuning` 只被 `farm_target.py` 引用），对**已有护甲**的 `find`/`recommend` 不试调谐 —— 我实测解出来的 5 件都只装了 `武器模组`，没有任何调谐 plug。而"给已有件装调谐"本身是一次写入，正好复用 P4 的确认流程。
- 无解时输出 `shortfall` + `ceiling`（按当前优先级每项最多能到多少，**含大师/调谐/模组的叠加上限**）+ `binding`（哪两项互斥、哪一件拖后腿）。
- 两档提案：①现在就能穿（标明相对模板降了什么）②farm 可用时给"刷一件就达标"；farm 不可用**要说清原因**（用 P1 的 `roll_parse_error`，例如"这件是 T3，不在反推范围"）。
- Pareto 采样：按 2–4 种优先级各解一次给可交换关系（本轮实测：`武器200 ⇒ 近战26`／`近战150 ⇒ 武器80、手雷25`／`生命127 ⇒ 武器100`）。只在无解时触发。
- 降档 10 点一档；**降级必须用户点头**（不自动降）。
- `completion_rate` 在没有硬目标时标 `N/A`。
- **验收**：复现本轮三行对比表；无解给 shortfall/ceiling；`find` vs `recommend` 的分工写进 routing.md 与语料。

### P6 收尾
- 文档：routing.md 护甲章节（新增 `item`/`equip_mod` + 字段表 + **两族差异与 T 级规则**）、语料护甲章节（含 ⭐ 冒烟行）、README 护甲能力说明。
- 体积预算表进文档与 ratchet 口径。
- CHANGELOG + 版本 + release。

---

## 4. 顺序

```
P0 基线 → P1 形状 → P2 详情 → P4 换模组 → P3 统一 → P5 阶梯 → P6 收尾
```

- **P4 提前**：只依赖 P1/P2，是你等着用的功能，先用真实使用把形状压出来。
- **P5 最后**：要改求解器输出契约（推荐/无解/降级三条路径），单独一轮做、单独验证。
- 不占阶段、可先做的一件事：把「要装备走 `find`，`recommend` 只给建议」写进 routing.md 与语料。

---

## 5. 体积与性能

| 项 | 实测/估算 |
| --- | --- |
| 现在一条护甲行（实测腿部） | **517 B** |
| 同一件的 sockets 原始 / 精简 | **783 B / 769 B**（长类别字符串占大头） |
| 改成短键后的 sockets（估算） | ≈ 300 B |
| 完整载荷（12 槽 + 三层属性 + 能量） | ≈ 0.9–1.4 KB／件 |
| 100 件列表若全带 | ≈ 100 KB（不可默认带） |
| 305 组件 | 同一次 `GetProfile` 多一个组件，不是多一跳 |

结论：**列表轻、详情按需**，与武器 `type` vs `info` 的分工一致。

---

## 6. 风险

1. **改键名会波及调用方**（`bucket_type` → `slot`）：靠 P0 基线 + allowlist 审计。
2. **T1/T2 无样本、T3/T4 各 3 件**：只对 T5 建规则；其余"报数据不反推"，文档写明样本量。
3. **老护甲规则与 3.0 不同族**：分族建模，不要用 3.0 的字段去套（它们的"大师"是抗性，不是六维）。
4. **能量容量不是常数**：实测 T5/T4 = 11、T3/legacy = 10，不能写死；按件读 `energy.capacity`。
5. **模块体积闸门**：`assistants.py` 已在 1407/1407 上限，新 intent 必须先把分支搬出去，不能抬上限。

---

## 7. 需要你拍板（每条给一个字面例子）

**① 列表要不要带插槽/能量？**

现在（实测 517 B，就是你号上那件 T5 腿部）：
```json
{"item_instance_id":"6917530198796768597","item_hash":157934631,"name":"至高狂徒腿铠",
 "item_type":"Armor","item_type_display":"腿部护甲","power":550,"bucket_type":"Leg Armor",
 "is_equipped":false,"location":"hunter","stats":{"weapons":30,"health":0,"class_stat":0,
 "grenade":20,"melee":0,"super_stat":25},"icon_url":"https://…"}
```
带插槽后（同一件，估算 ≈1.0–1.4 KB）：多出 12 条插槽、能量、`roll/base/final`、大师/调谐。
→ **建议：列表保持上面这个轻量形状；新增 `intent="item"` 专门给"这件现在装了什么、还能插什么"。**

**② `slot` 怎么统一？**

今天同一件护甲三种叫法：列表 `bucket_type:"Leg Armor"`；求解器/`equip_build` 回显 `"slot":"legs"`；
而 `farm_target` 的 `replacement_slot` **只认** `helmet/gauntlets/chest/legs/class_item`。
→ **建议：新增 `slot:"legs"` + `slot_display:"腿部护甲"` 作为唯一权威；`bucket_type` 保留不动**
（原因见 §8：武器载荷也在用 `bucket_type`，删它会影响武器）。

**③ legacy `apply_mod` 怎么处理？**

现在：默认 8 工具里**没有**它（要 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1`），调用直接生效、无确认、
返回裸 dict。改完（走 `equip_mod` 的确认流程）：
```json
{"ok": false, "error": {"code": "confirmation_required", "recoverable": true},
 "candidates": [{"item_instance_id":"6917530198796768597","slot":"legs","silent_slot":0,
   "from": {"mod": null, "energy_used": 0},
   "to": {"mod":"纪律模组","stat_bonus":{"grenade":10},"energy_cost":3},
   "energy": {"capacity": 11, "used": 0, "after": 3},
   "character": "hunter"}],
 "summary": "至高狂徒腿铠（腿部护甲，550，T5）槽 0：空 → 纪律模组（+10 手雷），能量 0/11 → 3/11"}
```
→ **建议：收编（同一套校验+确认）；legacy 入口保留但改走同一确认，不再直写。**

**④ 阶梯先试调谐，还是直接降目标？**

用你的猎人实测：模板要 近战 70，我这边按"武器优先"最多给到 **近战 26**，差 44 点。而调谐是**每件一个 ±5 杠杆**、
属性模组槽还能装 `近战模组`（+10）：
```
近战 26 + 5 件 × 调谐(+5) + 5 件 × 模组(+10) = 101   ← 够了
代价：每件 −5 武器（5 件共 −25 武器）；模组槽占掉
```
→ **建议：先算"靠调谐/模组能不能补"，补得上就给你这个方案（要写入，所以走确认）；补不上再降目标。**
注意这是新增工作量：**实测 `find/recommend` 目前完全不试调谐**（我解出来的 5 件全是 `武器模组`，没有调谐 plug），
调谐只在 `farm_target` 的虚拟件里建模。

**⑤ Pareto 采样什么时候跑？**

实测成本 6–7 秒/次；无解时跑 3–4 个优先级顺序 ≈ 25 秒，换来的是这张表（你号上真实数据）：

| 优先级 | 武器 | 生命 | 职业 | 手雷 | 近战 | 超能 |
| --- | --- | --- | --- | --- | --- | --- |
| 模板顺序 | **200** | 6 | 130 | 41 | 26 | 100 |
| 近战优先 | 80 | 20 | 55 | 25 | **150** | 103 |
| 生命优先 | 100 | **127** | 80 | 12 | 67 | 75 |

→ **建议：只在"无解"时触发**（有解就别多花 25 秒）。

---

## 8. 影响面审查：改了会不会碰坏别的工具

**结论：只要遵守"只加键、不删键、不动求解器内部模型"，8 个聚合工具的现有调用都不受影响。**
逐条列证据：

| 消费方 | 现在依赖什么 | 本次改动 | 风险与处理 |
| --- | --- | --- | --- |
| `inventory_assistant`（get/search/type/summary/duplicates） | `InventoryItem.bucket_type` 做**筛选**（`inventory_service._filter_items`、`inventory_analysis_service._is_armor`）、`_formatters.format_inventory` 按 bucket **分组渲染** | 新增 `slot`/`slot_display`/`gear_tier`/`set`；`bucket_type` 保留 | 低。内部筛选改用统一的 slot 映射函数，保证 `armor_slot="legs"` 与 `slot` 同源 |
| `weapon_assistant` | `_weapon_branches.py:432` 把 `bucket_type` 放进**武器行** | 不动 | **不能删 `bucket_type`**：武器载荷也在用。这也是决策②坚持"新增而非改名"的原因 |
| `inventory_analysis_service`（summary/duplicates） | `bucket_type` 计数、`item_type_display`（"腿部护甲"） | 新增键 | 低。若要显示 T 级/词条是新增字段，不改旧字段 |
| `build_assistant(farm_target)` | `replacement_slot` 只认求解器键 | 与列表的 `slot` 统一 | **收益**：agent 不用再手写映射 |
| `build_assistant(equip_build)` 确认回显 | `canonical_build.items[].slot`（已是求解器键）+ `mods` | 只**增加**光等/能量/模组名 | 低。键不变，`ExecutableBuild` 校验不变 |
| `build_assistant(exotic_armor)` | Manifest 级列表/详情 | 可加同形 identity 键 | 低。现有字段全留 |
| `build_assistant(set_bonus)` / `community_build.inventory_match` | `starside_matching` 按**名字**匹配套装/金装/模组（`SOURCING_KINDS`），走 `get_armor_snapshot` | 可加 `set.hash` 精确匹配 | **要留名字兜底**：社区资料只有名字，改成只认 hash 会让老模板失配 |
| `loadout_assistant` | `loadout_service._armor_slot()` 自己从 bucket hash 推 slot；`build_template` 带 `armor.mods`/`armor_exotic` | 复用同一映射 | 中低。两套映射必须合并，否则长期漂移；`test_loadout_templates.py` 要跑 |
| 求解器内部 `build/models.Armor`（`ArmorStats`/`energy_capacity`/`armor3_roll_verified`） | 配装计算 | **不动** | 载荷只是它的视图，序列化层改动不影响计算 |
| 新增 intent 的注册 | — | `item`、`equip_mod` | **必须同步 4 处**，否则测试红：`_requests.py` 的 Literal、`_param_contracts.PARAMETER_OWNERS`、`routing.md`（`python -m destiny_mcp.tools._param_contracts --write-doc` 重生成）、`tests/test_ignored_parameters.py` 的 `BASELINE` |
| `tests/test_skill_contracts.py` | 校验 routing.md 覆盖每个 intent | 新 intent 要进 routing | 必须改文档，不能只改代码 |
| 模块体积闸门 | `assistants.py` 已在 1407/1407 | 新分支 | 必须先把分支搬到新模块（建议 `tools/_armor_branches.py` + `services/armor_payload.py`），**不许抬上限** |
| 武器基线闸门 | `tests/baselines/weapon_responses/` + allowlist | 不涉及 | 不受影响；护甲另开 `armor_responses/` 独立目录与 allowlist |
| `verify_mcp.py` | 8 工具 + `PARAMETER_GUARD` 自检 | 不加工具、不加参数别名 | 不受影响（仍 8 工具） |
| 文档 | `routing.md` 护甲章节、`TESTING_CORPUS.md` 护甲章节、README、CHANGELOG | 要更新 | P6 统一做；语料加护甲 ⭐ 冒烟行 |

**三条硬约束**（写进实现纪律）：

1. **只加键、不删键**：任何消失的字段都必须登记进 `armor_response_allowlist.json` 并写明去向（沿用武器那套闸门）。
2. **不动求解器内部模型**：`Armor`/`ArmorStats`/`canonical_build` 的字段与校验一律不改；载荷只做视图。
3. **模块不许长胖**：新 intent 的分支进新模块，`assistants.py` 上限保持 1407。


---

## 完成记录（2026-09-13）

| 阶段 | 提交 | 关键产出 |
| --- | --- | --- |
| P0 录基线 | `d371b9f` | `--surface armor` 19 例基线 + `tests/test_armor_baseline.py` 五条闸门 |
| P1 形状工厂 | `08e2956` | `services/armor_payload.py`（三层属性、分族、插槽短键）；478 件全量校验 |
| P2 单件详情 | `1e6d1f5` | `intent="item"` + `_armor_branches.py`（`assistants.py` 不增反降） |
| P4 换模组 | `f05d30d` | `intent="equip_mod"` + `ArmorModService`；legacy `apply_mod` 收编 |
| P3 五处统一 | `8c8300a` | 列表/单件/反推/社区核对/装备回显共用槽位与属性口径（纯新增、零删除） |
| P5 六维阶梯 | `0eb6fc3` | 无解给 `shortfall`/`ceiling`/`trials`/`suggestion`；`completion_rate` 标 N/A |
| P6 语料与发版 | `ad1c5d8` | 护甲语料 12 行真机脚本 + 章节 + 0.1.3 |

**过程中修掉的真问题**（都是实机勘测才发现）：

1. 老护甲也带 `gearTier: 0` —— 按"有没有这个字段"分族会把 54 件老护甲误判成 3.0；
2. 异域护甲的固定属性分布在 `intrinsics` 插槽里 —— 不算进 `roll` 会把大师等级算成 30（27 件中招）；
3. 换模组只请求 `[300,305]` 拿不到背包容器 —— 每件护甲都被误判成"不在该角色身上"；
4. 同名模组有多个版本（「手雷模组」既有 +0/1 能量的占位版，也有 +10/3 能量的真模组）——
   随便挑一个会装上去一个没用的；
5. `analyze` 的 `max_possible` 是**单项**上限 —— 当成"同时能达到"会得出"你什么都够"；
6. 目标是互斥时，光换优先级顺序每种都是 0 候选 —— 阶梯必须真的把目标放下来再解。

**未做（有意留在范围外）**：T1–T4 的词条反推规则（样本不足，只报数据不建模）；
"自动降目标"（安全规则要求必须用户确认）；求解器内部模型一行未改。
