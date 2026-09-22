# Starside 实体数据接入计划（作者新归档）

**状态（2026-09-23）**：三条口径已拍板；**P0 已落地**（见第八节的落地记录）。数据来源：**Starside 站点作者给出的新归档**
（`归档/inventory-items.json` 13.1 MB、`归档/sandbox-perks.json` 2.9 MB、`归档/traits.json` 42 KB，
快照时间 2026-09-21 15:30）。归档本身**不进 git**（`.gitignore` 已加），入库的是我们转换后的紧凑子集。

目标一句话：把这份**按 hash 的社区实体数据**接进工具面 —— 现在"这把枪该刷什么/作者怎么评/这个模组
哪一季神器有"只能靠搜页面正文，接完之后是**一次按 hash 查表**，并且能和 Manifest、你的账号库存、
配装结果直接对得上。

---

## 一、这份归档是什么（全量体检结论）

### 1. 三份文件 ↔ 我们 Manifest 的三张表，**逐条对得上**

| 归档文件 | 对应我们的表 | 覆盖 | 我们的表行数 |
| --- | --- | --- | --- |
| `inventory-items.json`（5,760 条，有索引的物品） | `DestinyInventoryItemDefinition` | **5760/5760** | 38,894 |
| `sandbox-perks.json`（5,200 条） | `DestinySandboxPerkDefinition` | **5200/5200** | 5,203 |
| `traits.json`（46 条） | `DestinyTraitDefinition` | 46/46 | **554**（所以我们**不取**这份） |

hash 一律按**无符号 32 位**比对（我们库里存的是有符号写法：`to_unsigned` 归一后交集 100%）。
这一点是硬要求，见第五节第 5 条。

### 2. 真正能用到的字段（覆盖数 = 有多少条带这个字段）

**武器推荐（逐把，按作者分两套刻度，不许合并）**

| 字段 | 覆盖 | 内容 |
| --- | --- | --- |
| `zh.site_authors.Aegis.*` | **748 把** | `aegis_tier`(S/A/B/C/D/E/F) + `barrel`/`magazine`/`masterwork`/`origin`/`perk1`/`perk2` + `explanation_1` + `aegis_source`（例：无足雨燕 S 级、枪管「槽化枪管/箭头制退器」、3 号位「冲击支撑/萤火虫」、4 号位「失衡弹药/枯萎凝视/铤而走险」） |
| `zh.site_authors.LGpig.*` | **339 把** | `lgpig_tier`（**分场景**：`清怪：T1\高难：T2\输出：T2.5`、`特殊用途`、`PVP`）+ `role`144 + `dps`283 / `total_damage`283 / `switch_dps`149（实测数值）+ `perk1`/`perk2`205 + `explanation_1..3` + `notes`149 + `lgpig_source`205 + `lgpig_tier_explanation`205 |
| `zh.site_emblem` | 7 | 站点徽标文件名（**不用**，我们没有那份资源） |

**评语与机制说明（社区写的中文文本，带站内标记）**

| 字段 | 覆盖 | 内容 |
| --- | --- | --- |
| 物品 `zh.realgame_details` | **1,393** | 实机机制细节（数值、触发条件、注意事项） |
| perk `zh.realgame_details` | **2,217** | 同上，perk 级 |
| perk `zh.realgame_details#2` + `zh.右栏` | 各 **28** | 异域职业物品"之灵"的**双栏配对**说明 |
| 物品 `zh.site_source` / perk `zh.来源` | 82 / **183** | 从哪来（`{src|试炼}`、`{src|玻璃拱顶}`） |
| perk `zh.效果` | **139** | 口语化效果（例：快速球「手雷飞行速度与距离提高约 25%」） |
| perk `zh.异域 PERK` + `authors` | 各 **29** | 异域 perk 的注记（作者 `LGpig`：实机细节/应用场景/获取地点） |
| 物品 `enhanced` | **29** | 强化版效果（`by` = perk hash，`realgame_details` 写清强化后变化） |

**数值层（这是页面上有、但**结构化之后才能参与判断**的部分）**

| 字段 | 覆盖 | 内容 |
| --- | --- | --- |
| 物品 `zh.site_frameStats` | **28 个框架** | **帧级 DPS 模型**：`base_damage`/`base_interval`/`body_mdps`/`max_mdps`/`typical_mdps`/**`typical_edps`**/`boss_total`/`minor_total`/`boss_brick`/`minor_brick`/`minor_scalar`/`elite_scalar`/`1mag_bdps`/`luna_bdps`/`base_dump`/`base_reload`/`modded_*`/`tested_mag`/`tier`/`notes` |
| perk `zh.冷却与槽位` 161 + `zh.基础冷却` 6 + `zh.冷却` 3 | **170** | `{cd|基础冷却 131.7 秒\\ 回复倍率 0.7×}` —— **秒数与倍率都有** |
| perk `zh.属性变化` | **121** | 碎片的属性增减，**分职业**（例：坚韧回声 = 猎人−10武器/泰坦−10生命/术士−10职业） |
| perk `zh.碎片槽位` 39、`zh.费用` 1 | 39 / 1 | 碎片槽位、费用 |

**神器关联（你点名的那条）**

| 字段 | 覆盖 | 内容 |
| --- | --- | --- |
| 物品 `site_artifact` | **139 个模组** | 神器**物品** hash（例：好奇之器、女王兰香炉、猎人日志…）。7 个神器全部能在我们的 `DestinySeasonDefinition.artifactItemHash` 里解出**赛季号与名称**（21 深渊 / 23 终愿 / 24 回响 / 25 怨魂 / 26 异端 / 27 溯回 / **28 凯旋纪念碑**） |
| 物品 `site_tier` / `site_superTier` | 139 / 30 | 神器模组的档位（1/2/3 与 super 档） |
| 物品 `site_cooldownSeconds` / `site_recoveryMultiplier` | 110 / 80 | 模组的冷却秒数与回复倍率 |
| 物品 `derived.tiers` | 7 | 神器**层级结构**（每层有哪些模组） |
| 物品 `site_perkColumns` | 3 | 异域职业物品的**栏位分组**（36 个 perk 全部能解出） |

**标签层（可直接过滤/展示，不必再算）**

| 字段 | 覆盖 | 内容 |
| --- | --- | --- |
| `derived.release` + `derived.season` | **2,555** | 哪个版本/赛季（v970.core / season 29 …）。**注意：站点推导，不是 Bungie 字段**；我们自己的 Manifest 推不出来（水印只覆盖 2,750 件且 11 种水印跨赛季），所以只能按社区资料标源使用 |
| `derived.archetype` | 2,208 | **= 框架/词条原型 plug hash**，28 个框架带帧表 → **武器 → 框架 → DPS 表是干净的 hash join**（例：无足雨燕 `1458010786` → 「轻质框架」带帧表） |
| `derived.foundry` / `craftable` / `catalyst` / `tierable` | 778 / 563 / 145 / 1,109 | 制造厂 / 可锻造 / 有催化（175 个催化 hash 全部解得）/ 可升级 |
| `derived.breakerType` | 2,207 | 破盾类型（Bungie 原始字段只覆盖 17 件，站点补全了） |
| `sameAs` | **627** | 同款/复刻指向（371 个目标全部解得） |
| `isAdept` / `isHolofoil` | 183 / 123 | 专家/亮箔变体 |
| perk `onItems` | **2,541 条 / 6,134 条边** | perk → 物品反查（**0 条越界**，全在物品文件里） |
| perk `onSets` | **112** | perk → 套装（例：集体之力 → 埃希恩记忆；56 个套装 hash 全部解得） |

**标记体系：41 种 token，总出现次数前几名**
`perk`7,957、**`unsure`3,805**、`enemy`3,010、**`enh`2,056**、`stack`1,820、**`pvp`1,673**、`src`1,247、
`el-void/arc/solar/stasis/strand/kinetic/prismatic`、`buff`/`debuff`/`deb-*`、`exotic`、`note`、`orb`、`health`、
`pickup`、`bar-orange/yellow/red`、`slot`、`ammo-heavy/special`、**`art-perk`152**、`armor-charge`、`num`、`cd`、
`na`、`spirit`、`cost`。`\\` 是"多选分隔符"（1,826 条物品文本用到）。

### 3. 不取的东西与理由

| 不取 | 为什么 |
| --- | --- |
| `traits.json`（46 条） | 我们库里有 **554 条** trait 定义，中文名逐条一致 |
| 物品/perk 的**名字**（`i18n.*.name`） | 显示名一律用我们自己 Manifest 的中文名（单一出处；抽查 300/300 一致，但仍以我们库为准） |
| `icon` / `icon_local` / `site_emblem` | 指向站点的 `assets/icons/*.webp`，**资源没随归档给出**；图标继续用我们的 Bungie URL |
| `traitIds`（DIM 自己的标签词表：`releases.*`/`item.weapon.*`/`foundry.*`） | 是第三方词表，不是 Bungie 字段；要"哪个赛季"直接用 `derived.release/season`，要"什么武器类型"用我们自己的类型 |
| `isDisplayable` / `damageType` / `index` / 物品属性与插槽 | 我们 Manifest 里本来就有，且更全 |
| 把 `derived.*` 当事实 | 它是**站点推导**（`foundry`/`craftable`/`release` 我们库里没有，只能按社区资料标源；`breakerType`/RPM 这类我们库里有的以我们为准） |

---

## 二、接入后的能力（逐工具，含数据来源字段）

| 工具 / intent | 新增什么 | 来源字段 |
| --- | --- | --- |
| `weapon_assistant(intent="analyze")` | **该刷什么**：两套推荐的评级 + 枪管/弹匣/大师/起源 + 3、4 号位推荐 + 理由；实测 `dps`/`total_damage`/`switch_dps` 与 `role`；帧级 DPS 参数（用 `derived.archetype` join 帧表）；标签（制造厂/可锻造/有催化/专家/亮箔/同款）；`realgame_details` 机制细节；别名（作者块里的 `name`，20 处） | Aegis/LGpig 块、`site_frameStats`、`derived.*`、`realgame_details`、`sameAs` |
| `weapon_assistant(intent="perk_description")` | perk 的**实机细节 + 口语效果 + 来源 + 冷却秒数/回复倍率 + 异域 PERK 注记**；`onItems` 给"出现在 N 件物品上"（举例前几个） | perk `zh.*`、`onItems` |
| `weapon_assistant(intent="perk_pool")` | 每个 perk 的注记与"能出现在哪些武器类型上"（`site_weapons` 70 条） | perk `zh.*`、`site_weapons` |
| `inventory_assistant(intent="item")`（武器） | 手上这把直接附**推荐与理由**（按实例 → hash 查表），不用再问一次 | 同上 |
| `subclass_assistant(intent="artifact")` | 神器模组**属于哪一季的神器 + 第几层 + 档位 + 冷却/回复倍率 + 实机细节**；碎片/星象的**属性变化**（分职业）与冷却 | `site_artifact`、`derived.tiers`、`site_tier`、`site_cooldown*`、perk `属性变化`/`冷却与槽位` |
| `subclass_assistant(intent="fragment_details")` | 碎片属性增减（分职业）+ 效果文本 | perk `属性变化`/`效果` |
| `build_assistant(intent="armor_mods")` | 模组的效果/冷却/费用注记，以及"这个神器模组哪一季有" | perk `zh.*`、`site_artifact` |
| `build_assistant(intent="set_bonus")` | 套装 2/4 件效果的**社区评语**（`onSets` 112 条，例：集体之力） | perk `onSets` + `realgame_details` |
| `inventory_assistant(intent="item")`（异域职业物品） | 双栏配对的机制说明（28 条）与栏位分组（3 件） | `zh.右栏`、`realgame_details#2`、`site_perkColumns` |
| `world_assistant(intent="community")` | 不变（仍是文本层），但在已知 hash 时**指路**到结构化结果 | — |

**呈现纪律**：两套评级**各自成字段**、各自带作者与刻度说明（`aegis_tier` = S/A/B/C/D/E/F；
`lgpig_tier` = T0/T0.5/…T4 + 分场景），**永不合并成一个"评分"**；数值一律带上口径
（PvE / PvP / 理论 / 实测 / 作者条件），`dps` 与 `boss_total` 这类要说明"站点实测口径"。

---

## 三、数据层设计

1. **入库形态**：`data/starside/entities/items.json`、`perks.json`（紧凑 JSON，只保留第一节列出的字段；
   实测 **items 4,489 条 1.34 MB + perks 2,676 条 1.03 MB ≈ 2.4 MB**，与已提交的 17 MB starside 数据同量级）。
   每条按**无符号 hash 字符串**作键；文本块保留原文（含标记），解析放到读取时做。
2. **导入脚本** `scripts/import_starside_entities.py`：读 `归档/*.json`（或用户给的任意路径）→ 过滤字段 →
   写上面两个文件 + 把来源信息写进 `data/starside/index.json` 的 `entities` 段：
   `source_files{name, sha256, bytes}`、`snapshot_at`、`authors`、`counts`、`license_note`。
   脚本**幂等**（同样输入 → 逐字节同样的输出），并且**全有或全无**：任何一个 hash 在我们 Manifest 里
   解不出就报错退出，不静默丢数据。
3. **加载器**：`destiny_mcp/rag/starside_entities.py`（第 2 层，纯查询、不碰账号）——
   `lookup_item(hash)`、`lookup_perk(hash)`、`items_with_perk(perk_hash)`、`sets_of_perk(...)`、
   `frame_stats_for(weapon_hash)`（内部走 `derived.archetype`）、`artifact_of_mod(hash)`。
   **懒加载**：不在启动时读（性能计划第七项那条基线：启动 5.7 秒里没有它的位置），
   首次用到才读一次并缓存（带体积上限的缓存登记，跟 `pgcr_cache` 一个规矩）。
4. **服务层**：`destiny_mcp/services/starside_entities_service.py`（或并入 `StarsideService`，
   实施时按体量定），在 `service_context.ServiceContext` 里登记 key；层号登记进
   `tests/test_architecture_layers.py` 的 `_LAYERS`，模块加进 `AGENTS.md` 的模块表并重跑 `gen_code_map.py`。
5. **hash 纪律（你点名的那条，写成守门）**：
   - 存储键、索引、比较**一律无符号**；对外查询入口对传入的 hash 先过 `to_unsigned`
     （今天真机踩过两次：DIM 导出是无符号、`manifest.search` 是有符号，裸比较会把"清单里有的"判成没有）。
   - 导入时逐类校验：物品 → `DestinyInventoryItemDefinition`；perk → `DestinySandboxPerkDefinition`；
     `site_artifact` → `DestinyInventoryItemDefinition` **且**能在 `DestinySeasonDefinition.artifactItemHash`
     里找到赛季；`onSets` → `DestinyEquipableItemSetDefinition`；`sameAs`/`catalyst`/`perkColumns`/`enhanced.by`
     → item 或 perk 表。**任何一条解不出就失败**。
   - 解析文本里的 `{perk|X}` 时，先查我们 Manifest 的 perk 名，再退到归档 perk 名，都查不到就
     原样保留并留痕（不静默丢）。
6. **口径元数据**：`data/starside/index.json` 的 `entities` 段 + 每条响应里的
   `source: "starside.work"`、`snapshot_at`、`authors`、`unofficial: true`（措辞对齐
   `docs/community/COMMUNITY_DATA_NOTICE.md`）。

---

## 四、标记解析器（41 种 token，单一出处）

新模块 `destiny_mcp/rag/starside_markup.py`（纯函数，第 2 层）：

| token | 含义 | 我们输出的样子 |
| --- | --- | --- |
| `perk` | perk 引用 | 我们 Manifest 的官方中文名（查不到就原文 + 留痕） |
| `el-*`（void/arc/solar/stasis/strand/kinetic/prismatic） | 元素/伤害类型 | 我们 `vocabulary.py` 的元素词 |
| `deb-*` / `buff` / `debuff` | 减益/增益状态 | 状态名原文（这些是游戏内状态名，不在六维词表里） |
| `bar-red/orange/yellow` | 敌人档位 | 红血/橙血/初级首领（首领等） |
| `pvp` | **PvP 专用数值** | `（PvP）20` —— 与 PvE 数值**分开**呈现 |
| `enh` | 强化后的增减 | `强化：冷却 −15%` |
| `unsure` | **作者自己标的不确定** | `（作者标注：不确定）` —— 必须保留 |
| `stack` / `num` / `na` | 层数 / 纯数字 / 无 | 层数原样；`na` → `—` |
| `src` / `art-perk` / `armor-charge` / `orb` / `pickup` / `health` / `slot` / `ammo-*` / `cost` / `cd` / `spirit` / `exotic` / `enemy` / `note` | 来源 / 神器 perk / 护甲充能 / 能量球 / 技能能量 / 生命值 / 槽位 / 弹药类型 / 费用 / 冷却 / 之灵 / 异域 / 敌人 / 备注 | 按各自语义换成我们的词或普通中文；`{slot|![](icons/…)}` **丢掉站点图标路径**，只留槽位名 |
| `\\` | 多选分隔 | `／`（"槽化枪管／箭头制退器"） |

**守门**：解析器必须覆盖**数据里出现过的每一个 token**（发现新 token 就红，逼着显式处理）；
输出里**不许残留 `{…|…}`**（有一条"禁止原样回显标记"的扫描测试）。

---

## 五、硬口径（八条，写进守门）

1. **两套评级不合并**：`aegis_tier` 与 `lgpig_tier` 各自成字段、各自带作者与刻度；LGpig 的分场景
   （清怪/高难/输出）也必须分开列，不取平均。
2. **`{unsure|…}` 必须保留**（3,805 处）。作者标了不确定，我们转述时不许抹平成确定。
3. **覆盖缺口给 `null` + 原因**：只有 748/2,208 把武器有 Aegis 推荐、339 把有 LGpig、28 个框架有帧表、
   神器只到赛季 28 —— 缺就说"这位作者没评过"，**不编**，并在响应元数据里给覆盖率。
4. **数值带口径**：PvE/PvP 分开；理论 vs 实机标清；`dps`/`boss_total` 这类注明是站点实测口径
   （不是我们对游戏机制的断言）。
5. **hash 无符号归一**：见第三节第 5 条；任何"按名字查"的入口都不许用来做关联（重名实测：
   「精密框架」50 处、「速射框架」31 处）。
6. **社区层不与事实层混**：Manifest 定义 / 你的账号 / 社区资料三档分开；社区块永远带
   `source`+`snapshot_at`+`authors`+`unofficial`。
7. **我们库有的以我们为准**：显示名、RPM、`breakerType`、可锻造（图样）、催化（我们也有）——归档只作
   补全与评语；冲突时以我们为准并在必要时提一句口径差异（抽样核对：帧 `rate` 与库内 RPM 40 把里 32 把一致，
   8 处不一致多半是变体/改型，所以 RPM 不用它）。
8. **不碰写入路径**：这份数据**不得**影响账号写入的决定（装什么、换什么），只用于解释、推荐与展示。

---

## 六、守门清单（每条都要注入验证）

1. 导入幂等 + `_meta.source_files[].sha256` 与输入一致；输入缺字段/多余时失败而不是静默。
2. **hash 完整性**：每类实体在对应表里解得开；每条引用（`onItems`/`onSets`/`sameAs`/`catalyst`/
   `perkColumns`/`enhanced.by`/`site_artifact`）都指得到；有孤儿就红。
3. **符号双向**：同一 hash 用有符号/无符号两种写法查询结果相同（今天踩过两次的坑）。
4. **覆盖表 ratchet**：条目数与各字段覆盖数写成一张表（像 `test_module_size_ratchet` 那样），
   数据缩水必须是一次显式修改。
5. 标记解析：token 全覆盖 + 输出无 `{…}` 残留 + `{unsure}` 保留 + `{pvp}` 不进 PvE 字段。
6. 两套评级各自成字段、不被合并（扫描响应里不存在"综合评分"这类键）。
7. 缺数据：没评过的物品返回 `None` + 原因，不返回空字符串或 0。
8. 响应元数据：社区块必带 `source`/`snapshot_at`/`authors`/`unofficial`。
9. 分层/体量/服务容器/参数契约（`_param_contracts`、`_requests`、语料信封）按项目既有规矩同步。
10. 性能：懒加载（启动不变慢）、首查耗时与缓存命中实测（给前后数字）。

---

## 七、验收

- `pytest -q` 全绿 + 新守门注入验证；`scripts/run_corpus_all_rows.py` 一次（响应形状变了就跑）；
  基线 diff（`tests/baselines/` 里相关响应）。
- **真机**：`weapon_assistant(analyze)` 查一把我们有的枪（高炉/无足雨燕）→ 出两套推荐 + 评级 + 理由 + 帧参数；
  `subclass_assistant(artifact)` → 模组能指到赛季与层级；`perk_description` → 出实机细节与冷却数值。
- 文档同步：`docs/COMPATIBILITY.md`（新字段登记）、`README.md`、`skills/destiny2-mcp/references/routing.md`
  （+ `scripts/install_skill.py`）、`CHANGELOG.md`、`docs/community/COMMUNITY_DATA_NOTICE.md`（新归档与作者署名）、
  `docs/testing/TESTING_CORPUS.md`（新增语料行）、`AGENTS.md`（模块表/文档索引）。

---

## 八、阶段（每阶段独立可交付、可回退）

| 阶段 | 做什么 | 产出 |
| --- | --- | --- |
| **P0** ✅ | 导入脚本 + 紧凑实体文件 + 懒加载器 + hash/覆盖守门 | 数据入库，**响应一个字不变** |
| **P1** | 标记解析器 + `weapon_assistant(analyze)` 的社区块（Aegis/LGpig + 标签 + 机制细节） | 最想要的那条能力能用 |
| **P2** | perk 层接线（`perk_description`/`perk_pool` + `onItems`/`onSets`/`异域 PERK`） | perk 详情与反查 |
| **P3** | 神器/碎片/模组/套装层（`artifact`/`fragment_details`/`armor_mods`/`set_bonus` + 双栏配对） | 神器关联与数值注记 |
| **P4** | 帧级 DPS 模型（`derived.archetype` → 帧表）：给理论 MDPS/EDPS 与口径说明 | "这把枪打多少" |
| **P5** | 交叉指路（文本层 ↔ 实体层）、语料行、性能实测与体积登记 | 收尾 |

---

## 九、不做清单

- 不抓 starside.work（数据来自作者给的导出；要更新就再要一份新归档）。
- 不把归档当数据源里的"事实层"（名字/属性/插槽一律用我们 Manifest）。
- 不引入 `traits.json`、`traitIds`、`icon_local`/`icon`。
- 不合并两套评级、不抹平 `{unsure}`、不把 PvP 数值混进 PvE。
- 不让这份数据参与任何账号写入决策。
- 不为它新增"每件物品一个工具/intent"——能挂进现役 intent 的就挂，确实需要新入口时按项目契约走一遍
  （`_requests`/`_param_contracts`/skill/routing/语料）。

---

## 十、已确认的口径（2026-09-23 用户拍板）

1. **`derived.*`（`release`/`season`/`foundry`/`craftable`）按"Starside 整理"标源**，附快照时间，
   并写明"非 Bungie 官方字段"。凡是能从我们 Manifest 得到的（名字、RPM、破盾类型、图样、催化）
   **以我们为准**。
2. **神器缺当季要明说**：归档只到赛季 28，响应里写"本季神器作者尚未整理"，
   不让玩家把"查不到"读成"游戏里没有"。
3. **数值必须带适用条件**：`dps`/`total_damage`/帧表一律附口径（弹药类型、是否含增伤、打什么目标、
   PvE/PvP），只给数字不给条件算误导。

---

## 十一、P0 落地记录（2026-09-23）

- `scripts/import_starside_entities.py`：读作者导出 → 只留有社区字段的物品与 perk → 写
  `data/starside/entities/{items,perks}.json`（**物品 4,483 / perk 2,676；1.64 + 1.14 MB**），
  并把来源（sha256/字节数/快照时间/作者/覆盖数/引用计数）写进 `data/starside/index.json` 的 `entities` 段。
  **确定性**：同样输入逐字节同样输出（改坏后重跑还原，已实测）。
  **全有或全无**：物品→item 表、perk→sandboxPerk 表、神器→item 表 + `DestinySeasonDefinition.artifactItemHash`、
  套装→set 表、以及 `onItems`/`sameAs`/`catalyst`/`perkColumns`/`enhanced.by` 全部逐条校验，一条不通就退出。
- `scripts/audit_starside_entities.py`：真机只读审计（退出码 0/1），Manifest 更新后随时可复核。
  **已跑：全部对得上 ✓**（神器 7 个全部指到赛季）。
- `destiny_mcp/services/starside_entities.py`：懒加载 + 按 hash 查询（`item`/`perk`/`items_with_perk`/
  `sets_of_perk`/`frame_stats`/`frame_stats_for_weapon`/`artifact_of`/`authors_of`/`attribution`）。
- `tests/test_starside_entities.py`：6 条守门（无符号键与出处元数据、覆盖/引用计数对账、引用格式、
  有符号/无符号双向查询、缺数据给 None+原因、三条真实链路）。三条注入验证过（覆盖数改小 / 加有符号键 /
  引用计数改错 → 各自变红）。

**两处实施偏差（与方案的差异，记在这里）**

1. 加载器放在 `destiny_mcp/services/starside_entities.py`，**不是** `destiny_mcp/rag/`：`rag/` 至今是
   占位包（社区检索实际在 `services/starside_service.py`），放 `services/` 与
   `starside_crafting_sources.py` 同一分工。P0 **不登记进 `ServiceContext`**（P1 接线时再登记，
   免得现在就有个没被用到的服务）。
2. 我们只收**有社区字段**的物品（5,760 → 4,483），所以 `onItems` 的 6,134 条边里有 922 条指向的物品
   在本文件里没有条目 —— 那些物品的名字/类型按"单一出处"去 Manifest 取，真机审计保证每条边都能解出。
   原始导出实测是 6,134/6,134 全在文件内；过滤后的数字写进 `_meta.counts` 当 ratchet
   （`onItems_edges_in_file` = 5,212、`sameAs_in_file` = 627）。

### P0 的真机语料（`scripts/run_corpus_starside_entities.py`，8 行，2026-09-23 全过）

拿账号里真实的武器/模组跑数据层：我的 500 把武器 **100% 命中实体层**，其中 **Aegis 189 把、LGpig 72 把**；
326 把能 join 到帧表；本季神器「好奇之器」35 个模组里归档指到 **21 个（60%）**。语料还挖出四件
**P1 解析器必须处理**的事（都已写进检查里）：

1. **数值带站点包装**：`{num|13514\\11384}`、`{num|7087\\(级联点)}`，还有 **`∞`**（真的出现过）。
   解析器要先解包再判数值，不能拿原串当数字。
2. **推荐里有 2.4% 的 perk 名我们库里查不到**（例：「B 计划」「Häkke 突入武装」「Suros 协同」——
   品牌/瞄准具类的站点叫法）。规则：**原样保留 + 留痕**，不许丢、不许猜。
3. **plug 物品 hash ≠ sandbox perk hash**：`manifest.search` 给的是 plug 物品，而归档 `onItems` 的键在
   `DestinySandboxPerkDefinition` 空间里（plug 的 `perks[].perkHash` 才是）。第一版语料拿 plug hash
   直接查，闭环率 **0%**；改走 plug → `perks[].perkHash` 后 **592/925 走通**。
4. **神器模组不是背包物品**：查"我身上有没有"永远是 0；正确做法是从我们 Manifest 的本季神器定义取模组
   列表再查归档（覆盖率 60%，缺口要在响应里说明）。
