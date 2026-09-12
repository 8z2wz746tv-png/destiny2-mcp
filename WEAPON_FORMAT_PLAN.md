# 武器格式统一 · 开发计划（v2）

状态：**待确认**（确认后按第 4 节分阶段实施）
范围：`weapon_assistant` 全部只读 intent + 依赖服务
不动：写入链路、`build_assistant`、商人、求解器

---

## 1. 现状：一把武器有十处形状

| # | 位置 | 产出 | 实测大小 |
| --- | --- | --- | --- |
| 1 | `manifest_query_service.get_weapon_full_info` | `data.weapon`（info/analyze） | 6.6 KB |
| 2 | `manifest_query_service.get_weapon_stats` | 只有 `stats`，没名字没稀有度 | 0.4 KB |
| 3 | `perk_service.get_weapon_perks` | `WeaponPerkPool`（`slots[].slot_name` 英文） | 8–32 KB |
| 4 | `perk_service.get_god_roll` | **一整段文字** | 0.2–0.9 KB |
| 5 | `weapon_roll_filter_service._compact_catalog_weapon` | `catalog.matched[]` | 每项 0.5 KB |
| 6 | `weapon_roll_filter_service._compact_weapon` | `filter_rolls.matched[]`（`perks[]` 是字符串） | 每项 0.2 KB |
| 7 | `weapon_detail_service` → `WeaponDetail` | 实例级（`sockets[].slot_label` 中文） | 3.6 KB/把 |
| 8 | `weapon_compare_service` | `comparison.instances[]` | — |
| 9 | `weapon_popularity_service._weapon_identity` | `popularity.weapon` | — |
| 10 | `inventory_service.search_items_by_type` | `InventoryItem`：没稀有度、没 perk | 每项 0.3 KB |

另外三个结构性问题：稀有度表抄了 3 份（其中三份只有 5/6 两档）、"固定 perk / 随机 roll"在取池时就丢掉了（`randomizedPlugSetHash or reusablePlugSetHash`）、同一个概念两套字段名（`slot_name` 英文 vs `slot_label` 中文）。

---

## 2. 缺口清单（这一轮一次性补齐）

### 2.1 上游数据没用到的（Manifest 定义级）

| 数据 | 现状 | 价值 |
| --- | --- | --- |
| `inventory.tierType/tierTypeName`、`itemTypeAndTierDisplayName` | 三处只映射 5/6，蓝绿白变空串 | 稀有度（5 档） |
| `inventory.recipeItemHash` | 没用；原来用 type 30 搜索猜，漏了 203 把传说 | 可锻造判定（实测 219 把） |
| 固有槽（`SOCKET_CAT_INTRINSIC`） | 只在 `intrinsicPerks` 里，混着框架与异域特性 | `frame` / `intrinsic` / `rpm` |
| plug set 的 `currentlyCanRoll` | 没用 | 退役 perk 不该冒充"能滚到" |
| 强化配对（同名同类 + tierType 2/3） | 没用 | **272 组**强化 perk |
| `plug.investmentStats` | 没用 | perk 数值效果（箭头制退器：后坐 +30、操控 +10） |
| `breakerType` | 没用 | 破盾类型（干扰/眩晕/贯穿护盾）；弑后者 = 眩晕 |
| `traitIds` | 没用 | 武器族 + 版本（`item.weapon.linear_fusion_rifle`、`releases.v400.annual`） |
| `collectibleHash` → `sourceString` | 只用于收藏品工具 | 来源（"异域记忆水晶；极稀有世界掉落"） |
| `iconWatermark` | 没用 | 赛季/版本水印 |
| `DestinyStatDefinition` + `DestinyStatGroupDefinition`（112 组） | 属性名硬编码，模型固定 10 项 | 定义里 16 项，缺 攻击/能量/充能时间/伤害(冲击力)/弹药生成；属性顺序也该按 StatGroup |
| `DestinyBreakerTypeDefinition` / 伤害 / 弹药 名称表 | 硬编码 3 张表 | 单一来源，版本更新不再手改 |

### 2.2 账号组件没请求 / 没用到的（实例级）

| 组件 | 现状 | 价值 |
| --- | --- | --- |
| **310 ItemReusablePlugs** | **武器服务没请求**（`profile_cache._FULL_COMPONENTS` 里有） | 这一件副本实际能换的 perk = T 级决定的那个数 |
| **302 ItemPerks** | 没用 | API 算好的展示 perk（`perkHash`/`isActive`/`visible`，样本 6 条） |
| 308 plugObjectives | 没用 | 催化剂/击杀进度（按武器有无而存在） |
| 300 里的 `itemLevel`/`quality`/`equipRequiredLevel` | 只用了 `primaryStat` | 武器等级、品质、装备要求 |
| 组件列表 | **散在 17 处**，各自集合还不一样 | 单一定义 + 与缓存复用 |

### 2.3 T 级与"能滚几个"（实测，必须按实例报）

| gearTier | 实例数 | 最常见「随机栏可选数」 | 结论 |
| --- | --- | --- | --- |
| T5 | 343 | (3,3,2,2) × 274 | 两个特性栏各 **3** 个 |
| T4 | 17 | (2,2,2,2) × 10 | 各 2 个 |
| T3 | 10 | (2,2,2,2) × 6 | 各 2 个 |
| T2 | 8 | (1,1,1,1) × 5 | 各 1 个 |
| T0（老武器） | 521 | (2,2,1,1)、(1,1,1,1)… | 无规律 |

- 定义池是**超集**：遗产特性栏定义 19 个，一件 T5 遗产实际只有 3 个；
- 同一把枪会以不同 T 级存在（742 个武器 hash 里 28 个有多个 T 级副本）；
- 老武器 `gearTier=0`：要说"无 T 级"，不能显示 0。

### 2.4 非 perk 插槽全丢

只取了 `WEAPON PERKS`(4241085061) + 固有槽两类，**大师杰作、武器模组、装饰、纪念物、追踪器、着色器、催化剂插槽都没进模板**；而 manifest 里叫"武器特性"的插槽类别不止一个（4241085061 与 3410521964），靠单一 hash 会漏。

### 2.5 对照 DIM 的做法（已读源码）

DIM（`src/app/inventory/store/sockets.ts`、`src/app/utils/perk-utils.ts`）：

```ts
DimSocket { plug, reusablePlugs: DimPlugSet }
DimPlugSet { plugs, plugHashesThatCanRoll, plugHashesThatCannotRoll, craftingData }
DimPlug    { plugDef, cannotCurrentlyRoll, enabled }
// 强化：data/d2/trait-to-enhanced-trait.json + isEnhancedPerkHash/unenhancedVersion
```

四条照搬：**一个 socket = 已装 + 能换的集合（读组件 310）**、**`currentlyCanRoll` 区分退役**、**强化是独立条目+配对表**、**框架从固有 perk 取，异域没有就退回 RPM**。

---

## 3. 设计（终版）

### 3.1 三层结构

```
weapon:   身份与实例标量（这把枪"是什么"）
sockets:  全部插槽（含非 perk 插槽），每槽 = 已装 + 能换
stats:    属性（按 StatGroup 顺序，名称来自 StatDefinition）
perks:    sockets 里 roll 相关栏位（intrinsic/barrel/magazine/trait）的视图
```

```jsonc
"weapon": {
  "item_hash": 0, "name": "遗产", "name_en": "Heritage",
  "rarity": "传说", "rarity_tier": 5,
  "weapon_type": "霰弹枪",
  "frame": "精确重击框架",        // 固有槽以「框架」结尾时取用，异域常为 null
  "intrinsic": "精确重击框架",     // 固有槽原文（异域=专属特性名）
  "rpm": 30,
  "ammo_type": "威能", "damage_type": "动能",
  "breaker_type": "",             // 干扰/眩晕/贯穿护盾；无则空
  "is_craftable": true,           // inventory.recipeItemHash
  "roll_kind": "random",          // random | fixed（有随机池就是 random）
  "trait_ids": ["item.weapon.slug_shotgun", "releases.v400.annual"],
  "watermark": "…",               // 赛季水印
  "source": "来源：…",             // collectible.sourceString
  "description": "…", "icon_url": "…",
  "gear_tier": 5,                 // 只在实例上；老武器为 0 → 输出 null + 说明
  "schema_version": 1,
  "owned": { "status": "checked", "count": 2,
             "instances": [{ "instance_id": "…", "location": "仓库", "power": 1900,
                             "item_level": 1, "is_equipped": false, "locked": false }] }
             // 未读账号：{"status": "not_checked"}
}
```

```jsonc
"sockets": [
  { "slot": "框架", "kind": "intrinsic", "scope": "definition",
    "option_count": 1, "equipped": {"plug_hash": 1, "name": "精确重击框架"},
    "options": [ { "plug_hash": 1, "name": "精确重击框架", "description": "…",
                   "enhanced": false, "enhanced_plug_hash": 0, "can_roll": true,
                   "stat_effects": [{"stat": "射程", "value": 10}],
                   "god_roll_pve": false, "god_roll_pvp": false, "icon_url": "…" } ] },
  { "slot": "特性1", "kind": "trait", "scope": "instance", "option_count": 3 }
]
```

- `kind`：`intrinsic` / `barrel` / `magazine` / `trait` / `mod` / `masterwork` / `memento` / `tracker` / `shader` / `ornament` / `catalyst` / `other`。
- `scope`：`definition`（Manifest 完整池，含退役与强化配对）或 `instance`（这一件在组件 310 里能换的）。

```jsonc
"stats": [ {"name": "冲击力", "value": 70, "display": "70", "is_primary": false}, … ]
```

### 3.2 单一事实源

| 表 | 现在几份 | 收拢到 |
| --- | --- | --- |
| 稀有度 | 3 份（其中三份不全） | `weapon_profile.RARITY` |
| 伤害 / 弹药 / 破盾 | 2 + 2 + 0 | `manifest_names`（查 `Destiny*Definition`，带缓存） |
| 属性名与顺序 | 硬编码 10 项 | `DestinyStatGroupDefinition` + `DestinyStatDefinition` |
| 槽位标签 | 英文一套（manifest 侧）+ 中文一套（实例侧） | `weapon_profile.slot_kind()` 一份，中文标签统一 |
| 组件列表 | 17 处 | `profile_components.py`：`WEAPON_FULL` / `INVENTORY` / … |
| 强化配对 | 无 | 生成物 `data/weapon_enhanced_pairs.json` + 生成脚本 + 校验测试 |

### 3.3 长期维护机制

1. **生成物 + 校验**：强化配对（272 组）由 `scripts/generate_weapon_metadata.py` 从 Manifest 生成落盘；测试断言"生成物与当前 Manifest 一致"，Manifest 更新后跑一次脚本即可。
2. **契约测试**：每个 intent 的**键集合快照**；身份块必备字段；`kind`/`scope`/`roll_kind` 枚举校验；列表类必须带 `total/returned/truncated`；定义级/实例级字段不混。
3. **体积闸**：定义级 perk 默认不带 `description`（只给愿单标中的），实例级给全；契约测试卡住每 intent 的响应上限（现状 `perk_pool` 32 KB）。
4. **`schema_version`**：`weapon` 块带版本号，字段调整时调用方有据可依。
5. **文档三处同源**：本计划、`TESTING_CORPUS.md` 武器章节、skill `routing.md` 武器章节；在现有"语料路由必须存在"的校验上，再加"武器 intent 覆盖"断言。
6. **旧键一次性删掉**：不留双份（旧键只被本仓库语料/测试/skill 消费）。

### 3.4 本地数据的挂载点（与现有工具功能的接驳）

现在本地数据是**兄弟字段**、不是模板的一部分，五套形状互不相干：

| 本地数据 | 现在挂在哪 | 形状 | 问题 |
| --- | --- | --- | --- |
| 刷取清单 `farming_list` | 只有 5 个 intent（info/analyze/perk_pool/type/filter_rolls）的**顶层兄弟字段** | `results[]`：`tier`/`scenario_tiers`/`role`/`frame`/`source`/`perks{三号位,四号位}`/`source_ref` | `god_roll`、`compare`、`popularity`、`catalyst`、`catalog` **完全没有**；清单里的"必刷特性"没进模板 |
| 愿望单 | 唯一真正挂在 perk 上的：`god_roll_pve/pvp` | PerkInfo 两个布尔 | 只覆盖部分 intent |
| 选取率 `popularity` | 只有 `intent="popularity"`，自带一套身份 | `perk_columns[].items[{plug_hash?,name,selection_rate}]`/`popular_combinations`/`masterworks`/`mods` | 与模板的 `sockets[].options[]` 无对应关系 |
| Starside 社区资料 | info/analyze/perk_pool/perk_description 的 `community_references` | `results[{knowledge_id,title,snippet,source}]` + 顶层 `updated_at/snapshot_id` | 按**武器名**搜的通用结果，不按 perk / 不按栏位 |
| 来源 | Manifest `collectible.sourceString` + 清单 `source` + Starside 文本 | 三处三样 | 模板没有 `sources` 字段 |

清单条目实测（比预期丰富，**它自带一套并行身份**）：

```json
{"name": "遗产", "list": "刷取清单-绿弹紫枪", "scale": "T", "tier": "T1",
 "frame": "精确重击 65", "element": "动能", "source": "深岩墓室",
 "swap_dps": "10610", "remark": "动能合成", "note": "全游戏最高的单弹匣爆发 DPS…",
 "perks": {"三号位": ["转向"], "四号位": ["聚合充能/级联点", "重组"]},
 "source_ref": {"updated_at": "2026.9.6", "trust": "untrusted_reference"}}
```

注意 `frame: "精确重击 65"` —— 清单连**框架和 RPM** 都有，而我们从定义 `investmentStats` 取到的是 30（错的 hash 家族）。所以定两条规则：

1. **Manifest 为准、清单可对照**：`frame`/`rpm` 以 Manifest 推出为准；清单值作为 `cross_check` 附上，两者不一致时标明（这正好是生成物/名称表要修的坑）。
2. **本地数据统一挂进模板**，不再有兄弟字段：

```jsonc
"weapon": {
  "sources": [                                     // 三处来源合并
    {"kind": "manifest", "text": "异域记忆水晶；极稀有世界掉落"},
    {"kind": "farming_list", "text": "深岩墓室", "list": "刷取清单-绿弹紫枪"},
    {"kind": "community", "text": "…", "knowledge_id": "…"} ],
  "farming": {                                     // 值不值得刷 + 必刷特性
    "list": "刷取清单-绿弹紫枪", "scale": "T", "tier": "T1",
    "scenario_tiers": {"输出": "T0"}, "role": null,
    "rank": null, "note": "…", "cross_check": {"frame": "精确重击 65", "rpm": 65},
    "recommended_perks": {"三号位": ["转向"], "四号位": ["聚合充能/级联点", "重组"]},
    "source_ref": {"updated_at": "2026.9.6", "trust": "untrusted_reference"} },
  "community": {"updated_at": "…", "matches": [{"knowledge_id": "…", "title": "…"}]},
  "popularity": {"status": "当前", "version_label": "…", "source": {…},
                 "columns": [{"slot": "枪管", "items": [{plug_hash, name, selection_rate}]}],
                 "combinations": [...], "masterworks": [...], "mods": [...]} }
```

```jsonc
// sockets[].options[] 里每个 plug 带本地结论，按 plug_hash 对齐（不再让调用方自己按名字拼）
"options": [ { "plug_hash": 1, "name": "重组",
               "recommended": {
                 "wishlist":   {"pve": true, "pvp": false},
                 "popularity": {"selection_rate": 12.4, "rank": 2, "column": "特性2"},
                 "farming":    {"column": "四号位", "must_farm": true},
                 "community":  {"knowledge_id": "…", "title": "…"} } } ]
```

**覆盖规则**（哪些 intent 带哪些本地数据，一次说清、由契约测试钉住）：

| intent | 身份块 | sockets/perks | farming | popularity | community | sources |
| --- | --- | --- | --- | --- | --- | --- |
| `analyze` | ✅ | ✅ | ✅ | ✅（有快照时） | ✅ | ✅ |
| `info` | ✅ | 定义级 | ✅ | — | ✅ | ✅ |
| `perk_pool` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `god_roll` | ✅ | 固定/推荐 | ✅ | — | ✅ | ✅ |
| `popularity` | ✅ | ✅（用于对齐） | ✅ | ✅ | — | ✅ |
| `catalog` / `type` / `filter_rolls` | ✅ 精简 | — | 精简（`tier`+`source`） | — | — | 精简 |
| `compare` | ✅ | 实例 | ✅ | — | — | ✅ |
| `catalyst` | ✅ | 催化槽 | — | — | — | ✅ |

### 3.5 固定 / 随机的语义分支

| intent | `roll_kind="fixed"` | `roll_kind="random"` |
| --- | --- | --- |
| `god_roll` | 列 Manifest 固定 perk + 说明"固定 roll 无推荐" | 愿单推荐（结构化） |
| `popularity` | 说明"固定 roll 选取率无意义" | 现有逻辑 |
| `perk_pool` | 说明"固定 perk，仅部件可选" | 现有逻辑 |
| `filter_rolls` | 可按 perk 筛，但说明"没有 roll 可变" | 现有逻辑 |

---

### 3.6 体验保障与迁移（"问什么答什么"不能变）

两层要分开说：

- **人话层**（Agent 转述给用户的那段）：`ok_response` 的 `summary`、`warnings`、`next_actions` 措辞**只增不减**——这是体验的主体，重构不动它；
- **结构层**（Agent 读的 JSON 键名）：**会变**，这正是重构的目的。Agent 每次调用都重新读响应，不依赖上一次的键名。

#### P0（新增，改动之前先做）：录基线

1. 用固定问法跑一遍（语料里的武器问题 + 社区配装库存匹配 + 商人商品），完整响应落盘到 `tests/baselines/weapon_responses/`。
2. 重构后再跑一遍，生成 diff 报告：**新增 / 重命名 / 消失 / 语义变化**四类。
3. **硬门槛：任何"消失的字段"必须逐个给出理由**（内容进了新结构，或确认是废弃项）；没有理由的消失不允许合并。

#### 内部消费者清单（同批改，且必须有测试）

| 消费者 | 依赖什么 | 不处理的后果 |
| --- | --- | --- |
| `starside_matching.match_build_inventory` | `WeaponDetail.perks_complete`、`sockets[].plug_name`、`InventoryItem.item_instance_id/name` | 社区配装"看看我缺什么"会坏 —— 保留这些字段或同批改 + 测试 |
| `vendor_service`（商人商品） | `PerkService.annotate_god_roll(item_hash, plug_hash, perk)` | 商人商品的愿单标记会丢 —— 保持该 API 签名与语义 |
| `assistants.py` 武器分支 | 全部服务 | 同批改 |
| `weapon_tools.py` 遗留工具 | 同一批服务 | 跟着变（默认屏蔽），语料注明 |
| `build_assistant` 求解/farm_target | **不读**武器 perk（已核实） | 无影响 |

#### Agent 不会"看不懂"的前提

1. 响应自描述：`kind`/`scope`/`roll_kind`/`schema_version` 都在响应里；
2. skill `routing.md` 武器章节与代码**同批更新**——旧文档会让 Agent 按已删字段名去找值，这是最现实的翻车点；
3. 语料武器章节同批重写，供你上线前逐条实跑。

#### 明确不保证

- 写在**别处**的旧字段名会失效：自定义 prompt、第三方前端、外部脚本里如果写死了 `weapon.nameEn`、`god_roll`（字符串）、`perk_pool.slots[].slot_name` 这类，需要一起改（本仓库内已确认没有这类外部消费方）；
- 切换后必须**新开任务**：工具 schema 与 docstring 是宿主连接时读的。

### 3.7 数据驱动的边界（"改一处会不会牵一发动全身"）

先把话分开：**"乱"的来源不是数据驱动，而是同一个概念有多份定义** —— 稀有度表 3 份、属性硬编码 10 项、组件列表 17 处、名称表 3 张、强化配对 0 份。把数据库接进来、并且**只接一处**，是在减少耦合；让每个服务各自查库、各自硬编码，才是牵一发动全身。

#### 三层边界

| 层 | 归谁 | 内容 | 改这里的影响 |
| --- | --- | --- | --- |
| **契约层**（代码，稳定） | 我们定义 | 字段名与结构、枚举取值（`kind`/`scope`/`roll_kind`）、哪些 intent 输出哪几块、话术、上限/截断、错误码 | 显式改契约 + 改快照测试，diff 可见 |
| **适配层**（代码，薄） | 一处：`weapon_profile` + `manifest_names` | 把 Manifest 翻译成稳定结构；**其它服务不许自己查库** | 只影响这一层内部 |
| **数据层**（DB，动态） | Bungie | 名称文案、稀有度、属性集合与顺序、插槽集合、perk 池、`can_roll`、强化配对、可锻造、来源、破盾、trait | **不用改代码** |

#### 什么数据驱动、什么写死

| 由 DB 决定（内容动态） | 由代码写死（结构稳定） |
| --- | --- |
| 武器有哪些属性、顺序（`DestinyStatGroupDefinition`） | `stats[]` 里的字段名（`name`/`value`/`display`） |
| 属性名、伤害/弹药/破盾名 | `kind`/`scope`/`roll_kind` 的取值集合 |
| 有哪些插槽、每个插槽归哪类 | `sockets[]` 的字段名 |
| perk 池、`can_roll`、强化配对、可锻造 | `options[].*` 的字段名 |
| 五档稀有度、来源、trait、水印 | `rarity`/`rarity_tier` 这两个键本身 |
| —— | 3.4 那张"哪个 intent 带哪几块本地数据"的覆盖表 |

一句话：**结构固定、内容动态**。禁止让字段随武器变化（那才会牵一发动全身：调用方无法依赖键、测试无法快照）。

#### 四个护栏

1. **一处翻译 + 契约测试**：键集合快照测试；结构变更必须显式改测试，不允许悄悄变。
2. **生成物 + 过期检测**：Manifest 里**没有版本号**（实测 83 张表全是 `Destiny*`，无 metadata），所以：下载 Manifest 时写一份指纹（文件 sha256 + 下载时间 + Bungie 响应里的 version 若存在）到 `manifest/fingerprint.json`；生成物（强化配对等）记录该指纹与参与生成的表行数。启动时廉价比对（行数 + 指纹），不一致就**告警"跑一下生成脚本"**，不阻塞启动。
3. **fail-soft**：DB 缺字段（新属性没名字、新插槽没归类）→ 输出 hash/原值 + warning，**不抛异常、不静默丢**。
4. **数据变了可追溯**：响应里标 `source` 与 `updated_at`；本地清单/选取率本来就带 `source_ref`。

#### 明确不做

- 不让 JSON 结构由 DB 推导（例如"属性字段随武器类型变"）—— 结构是契约，内容才是数据；
- 不在服务层各自查库（那会把 17 处组件列表的坑复制到所有字段上）。

## 4. 分阶段任务

### P0 · 录基线（改动前）
- 固定问法跑一遍并落盘 `tests/baselines/weapon_responses/*.json`；写一个 `scripts/diff_weapon_baseline.py` 生成四类差异报告。
- 验收：基线文件入库；diff 脚本能在"未改动"状态下报告"零差异"。

### P1 · `weapon_profile.py` + 名称表（纯函数，可单测）
- 稀有度、`frame_of`/`intrinsic_of`/`rpm_of`、`roll_kind`、`socket_kinds`、`is_craftable`、`breaker_type`、`trait_ids`、`slot_kind`、身份块构造函数。
- `manifest_names.py`：伤害/弹药/破盾/属性名查表（缓存），替掉 3 张硬编码表。
- `manifest/fingerprint.json`：下载 Manifest 时写 sha256 + 时间（+ Bungie version 若有）；生成物记指纹与表行数，启动时比对并在过期时告警。
- 验收：单测覆盖 6 把真实形态 + 框架三情形 + 稀有度 5 档 + 破盾 3 档 + 可锻造判定（遗产 true / 泰拉巴 false）；指纹不一致时告警文案可见。

### P2 · 插槽与 perk 块（含强化、can_roll、数值效果、非 perk 插槽）
- 遍历**所有**插槽（不再只认一个类别 hash），按 plug 类别归 `kind`；保留 `randomized/reusable/single` 三态。
- 强化配对表生成脚本 + 落盘 + 校验测试；`can_roll` 来自 `currentlyCanRoll`；`stat_effects` 来自 plug 的 `investmentStats`。
- `god_roll` 改结构化。
- 验收：单测断言遗产 4 个随机栏、泰拉巴 0 个；272 组配对抽查 3 组；退役 perk 被标；非 perk 插槽（大师杰作/模组/纪念物）都出现在 `sockets` 里。

### P3 · 组件与实例级数据
- `profile_components.py` 收拢组件列表；武器服务加 **310/302/308**，与 `profile_cache` 共用。
- 实例级：`gear_tier`、`item_level`、`quality`、`locked`、`tracked`、`equipped`、`options`（来自 310）。
- 验收：T5/T4/T3/T2 各一例核对特性栏可选项数（3/2/2/1）；未读账号时字段为 null 且说明；同一 intent 不再出现两套组件集合。

### P4 · 十处形状接进统一结构
- 改：`manifest_query_service`（info/stats/catalyst 键名 snake_case）、`weapon_detail_service`、`weapon_roll_filter_service`、`weapon_compare_service`、`weapon_popularity_service`、`inventory_service` 的 type、`assistants.py` 武器分支。
- `stats` 改为动态属性列表（按 StatGroup 顺序，补上攻击/能量/充能时间/冲击力/弹药生成）。
- 验收：工具级测试逐 intent 断言键集合；真机四把武器 × 全部 intent 形状一致。

### P5 · 本地数据挂载 + 语义分支 + 测试/语料/skill
- 把 `farming_list` / `community_references` / `popularity` 三个**兄弟字段**收进模板（`weapon.farming` / `weapon.community` / `weapon.popularity`），并把 wishlist/选取率/清单推荐/社区资料按 `plug_hash` 落到 `options[].recommended`。
- 覆盖规则按 3.4 的表实现，由契约测试钉住（哪个 intent 必须带哪几块）。
- 清单与 Manifest 的 `frame`/`rpm` 交叉核对（清单 `frame: "精确重击 65"` vs 我们推出的值），不一致时输出 `cross_check`。
- 固定/随机分支；语料武器章节重写；skill `routing.md` 武器章节重写；回归测试补齐。
- 验收：`pytest` 全绿 + `scripts/verify_mcp.py` + 语料武器行逐条实跑。

### P6 ·（可选）体积与性能
- 定义级 perk 不带 description（32 KB → 10 KB 量级）；perk 池/配对表进程内缓存。

---

## 5. 风险与取舍

| 项 | 说明 |
| --- | --- |
| 破坏性 | 换新结构后旧键消失；消费方只有本仓库语料/测试/skill |
| 影响面 | 6 个服务 + 工具分支 + 2 个新模块 + 1 个生成脚本；`analyze` 是最大聚合 |
| Manifest 更新 | 强化配对与名称表走生成脚本 + 校验测试，不再手改硬编码 |
| 遗留工具 | `weapon_tools.py` 老工具跟着服务变，不额外适配，语料注明 |
| 明确不做 | 武器"菜单→详情"两态；催化剂解锁状态与锻造**强化解锁状态**（需要账号记录组件，保持 `not_checked` 明说） |

---

## 6. 验收清单

- [ ] 稀有度表只有一份；蓝/绿/白不再是空字符串；老武器 T 级显示"无"而不是 0
- [ ] 身份块含 `frame`/`intrinsic`/`rpm`/`breaker_type`/`trait_ids`/`source`/`is_craftable`/`gear_tier`
- [ ] 遗产 `frame="精确重击框架"`；泰拉巴 `frame=null` + `intrinsic="贪食野兽"`（话术用"异域专属特性"）
- [ ] 遗产 `is_craftable=true`（recipeItemHash）、泰拉巴 `false`
- [ ] `sockets` 覆盖全部插槽：框架/枪管/弹匣/特性1/特性2/模组/大师杰作/纪念物/追踪器；`kind` 与 `scope` 正确
- [ ] 定义级给完整池（遗产特性栏 19 个 + 退役标记 + 强化配对）；实例级给这一件的 T 级与实际可选项（T5 遗产特性栏 3 个）
- [ ] 强化 perk 成对：同一 perk `enhanced=false/true` 两条，`enhanced_plug_hash` 互指
- [ ] perk 带 `stat_effects`（箭头制退器：后坐 +30、操控 +10）
- [ ] `stats` 按 StatGroup 输出，含 冲击力/充能时间 等（不再固定 10 项）
- [ ] 同一次调用的组件集合唯一（310/302 到位）；未读账号时实例字段为 null 并说明来源
- [ ] 每个 intent 的键集合快照测试通过；列表类都有 `total/returned/truncated`
- [ ] 本地数据全部在模板里：`weapon.farming`（含 `recommended_perks` 必刷特性与 `source_ref.updated_at`）、`weapon.popularity`（按 plug_hash 与 sockets 对齐）、`weapon.community`、`weapon.sources`
- [ ] 同一个 perk 的本地结论汇总在一处：`options[].recommended.{wishlist,popularity,farming,community}`，不再让调用方按名字跨四段拼
- [ ] 遗产的 `farming.list="刷取清单-绿弹紫枪"`、`tier="T1"`、`source="深岩墓室"`、`recommended_perks` 与清单一致
- [ ] 清单 `frame`/`rpm` 与 Manifest 推出值不一致时输出 `cross_check` 说明（遗产清单写 65）
- [ ] 响应体积：`perk_pool` 从 32 KB 降到 10 KB 量级（若做 P6）
- [ ] baseline diff 报告里**没有"无理由消失"的字段**（P0 的硬门槛）
- [ ] 人话层不变：同一批问法的 `summary`/`warnings`/`next_actions` 与基线一致（只允许增补）
- [ ] 结构与内容分离：换一份 Manifest（行数变了）时，字段名不变、内容自动跟随；生成物过期会告警而不是静默用旧数据
- [ ] DB 缺字段时不抛异常：输出 hash/原值 + warning
- [ ] 内部消费者回归：社区配装库存匹配（starside）与商人商品愿单标记的测试仍绿
- [ ] `pytest` 全绿、`PARAMETER_GUARD=ok`、8 个工具、语料武器行逐条实跑
