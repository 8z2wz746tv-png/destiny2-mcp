# 武器格式统一 · 开发计划 v3（定稿，待开工）

状态：**可开工**。四项待拍板已给默认值（第 4 节），不反对就按默认执行。
范围：`weapon_assistant` 全部只读 intent + 依赖服务
不动：写入链路、`build_assistant`、商人、求解器

---

## 1. 现状：一把武器有十处形状

| # | 位置 | 产出 | 实测大小 |
| --- | --- | --- | --- |
| 1 | `manifest_query_service.get_weapon_full_info` | `data.weapon`（info/analyze） | 6.6 KB |
| 2 | `manifest_query_service.get_weapon_stats` | 只有 `stats`，没名字没稀有度 | 0.4 KB |
| 3 | `perk_service.get_weapon_perks` | `WeaponPerkPool`（`slot_name` 英文） | 8–32 KB |
| 4 | `perk_service.get_god_roll` | **一整段文字** | 0.2–0.9 KB |
| 5 | `weapon_roll_filter_service._compact_catalog_weapon` | `catalog.matched[]` | 每项 0.5 KB |
| 6 | `weapon_roll_filter_service._compact_weapon` | `filter_rolls.matched[]`（`perks[]` 字符串） | 每项 0.2 KB |
| 7 | `weapon_detail_service` → `WeaponDetail` | 实例级（`slot_label` 中文） | 3.6 KB/把 |
| 8 | `weapon_compare_service` | `comparison.instances[]` | — |
| 9 | `weapon_popularity_service._weapon_identity` | `popularity.weapon` | — |
| 10 | `inventory_service.search_items_by_type` | `InventoryItem`：没稀有度、没 perk | 每项 0.3 KB |

三个结构性问题：稀有度表抄 3 份（三份只有 5/6 两档）、"固定/随机"在取池时丢失（`randomizedPlugSetHash or reusablePlugSetHash`）、同一概念两套字段名（`slot_name` / `slot_label`）。

---

## 2. 缺口清单（一次补齐）

### 2.1 Manifest 定义级：没用到的数据

| 数据 | 现状 | 价值 |
| --- | --- | --- |
| `inventory.tierType/tierTypeName` | 三处只映射 5/6，蓝绿白变空串 | 稀有度 5 档 |
| `inventory.recipeItemHash` | 没用（原来用 type 30 猜，漏 203 把传说） | 可锻造（实测 219 把） |
| 固有槽（`SOCKET_CAT_INTRINSIC`） | 只在 `intrinsicPerks` 里混着 | `frame`/`intrinsic`/`rpm` |
| plug set `currentlyCanRoll` | 没用 | 退役 perk 不该冒充"能滚到" |
| 强化配对（同名同类 + tierType 2/3 + 描述不同） | 没用 | **228 组 / 216 个 perk 名**（另 56 组跳过并写明理由） |
| `plug.investmentStats` | 没用 | perk 数值效果（箭头制退器 后坐+30/操控+10） |
| `breakerType` | 没用 | 破盾类型（干扰/眩晕/贯穿护盾） |
| `traitIds` | 没用 | 武器族 + 版本 |
| `collectibleHash` → `sourceString` | 只用于收藏品工具 | 来源 |
| `iconWatermark` | 没用 | 赛季水印 |
| `DestinyStatDefinition` + `DestinyStatGroupDefinition`（112 组） | 属性名硬编码、固定 10 项 | 定义里 16 项：缺 攻击/能量/充能时间/冲击力/弹药生成 |

### 2.2 账号实例级：没请求 / 没用的组件

| 组件 | 现状 | 价值 |
| --- | --- | --- |
| **310 ItemReusablePlugs** | 武器服务没请求 | 这一件副本能换的 perk（T 级决定的数） |
| **302 ItemPerks** | 没用 | API 算好的展示 perk |
| 308 plugObjectives | 没用 | 催化剂/击杀进度 |
| 300 的 `itemLevel`/`quality`/`equipRequiredLevel` | 只用 `primaryStat` | 等级/品质/装备要求 |
| 组件列表 | **散在 17 处** | 单一定义 + 与缓存复用 |

### 2.3 T 级与"能滚几个"（必须按实例报）

| gearTier | 实例数 | 最常见随机栏可选数 | 结论 |
| --- | --- | --- | --- |
| T5 | 343 | (3,3,2,2) × 274 | 特性栏各 3 |
| T4 | 17 | (2,2,2,2) × 10 | 各 2 |
| T3 | 10 | (2,2,2,2) × 6 | 各 2 |
| T2 | 8 | (1,1,1,1) × 5 | 各 1 |
| T0（老武器） | 521 | (2,2,1,1)、(1,1,1,1)… | 无规律 |

定义池是超集（遗产特性栏定义 19 个，T5 实例实际 3 个）；同一把枪会以不同 T 级存在（742 个 hash 里 28 个）；老武器 `gearTier=0` 要显示"无 T 级"。

### 2.4 非 perk 插槽全丢

只取 `WEAPON PERKS`(4241085061) + 固有槽；**大师杰作/武器模组/装饰/纪念物/追踪器/着色器/催化剂都没进模板**；而 manifest 里叫"武器特性"的插槽类别有两个 hash（4241085061 与 3410521964）。

### 2.5 对照 DIM（已读源码）

```ts
DimSocket { plug, reusablePlugs: DimPlugSet }
DimPlugSet { plugs, plugHashesThatCanRoll, plugHashesThatCannotRoll, craftingData }
DimPlug    { plugDef, cannotCurrentlyRoll, enabled }
// 强化：trait-to-enhanced-trait.json + isEnhancedPerkHash/unenhancedVersion
```

四条照搬：socket = 已装 + 能换的集合（组件 310）、`currentlyCanRoll` 区分退役、强化=独立条目+配对表、框架从固有 perk 取（异域没有→退回 RPM）。

---

## 3. 设计（定稿）

### 3.1 结构：`weapon` + `sockets` + `stats`（**没有第三个副本**）

> 审查修正：v2 里的 `perks` 块与 `sockets` 数据重复（描述最占体积）。**取消 `perks` 副本**，roll 相关信息用极小的 `weapon.roll_summary` 表达。

```jsonc
"weapon": {
  "item_hash": 0, "name": "遗产", "name_en": "Heritage",
  "rarity": "传说", "rarity_tier": 5,
  "weapon_type": "霰弹枪",
  "frame": "精确重击框架",       // 固有槽以「框架」结尾时取用；异域常 null
  "intrinsic": "精确重击框架",    // 固有槽原文（异域=专属特性名）
  "rpm": 65,
  "ammo_type": "威能", "damage_type": "动能",
  "breaker_type": "",            // 干扰/眩晕/贯穿护盾
  "is_craftable": true,
  "has_enhanced": true,          // 该武器是否有强化版 perk
  "trait_ids": ["item.weapon.slug_shotgun", "releases.v400.annual"],
  "watermark": "…", "source": "…", "description": "…", "icon_url": "…",
  "gear_tier": 5,                // 仅实例；老武器 0 → null + 说明
  "roll_summary": {              // 回答"能不能滚 / 有几栏"
    "roll_kind": "random",       // random | fixed
    "random_columns": ["枪管", "弹匣", "特性1", "特性2"],
    "option_counts": {"枪管": 2, "弹匣": 2, "特性1": 3, "特性2": 3},
    "scope": "instance"          // 这些数字来自实例还是定义
  },
  "owned": { "status": "checked", "count": 2,
             "instances": [{ "instance_id": "…", "location": "仓库", "power": 1900,
                             "item_level": 1, "gear_tier": 5,
                             "is_equipped": false, "locked": false,
                             "option_counts": {"特性1": 3, "特性2": 3} }] }
             // 未读账号：{"status": "not_checked"}
}
```

```jsonc
"sockets": [                      // 唯一来源：所有插槽（含非 perk）
  { "slot": "框架", "kind": "intrinsic", "scope": "definition",
    "option_count": 1,
    "equipped": {"plug_hash": 1, "name": "精确重击框架"},
    "options": [ { "plug_hash": 1, "name": "精确重击框架", "description": "…",
                   "enhanced": false, "enhanced_plug_hash": 0, "can_roll": true,
                   "stat_effects": [{"stat": "射程", "value": 10}],
                   "god_roll_pve": false, "god_roll_pvp": false,
                   "icon_url": "…",
                   "recommended": { "wishlist": {"pve": true, "pvp": false},
                                    "popularity": {"selection_rate": 12.4, "rank": 2},
                                    "farming": {"column": "四号位", "must_farm": true},
                                    "community": {"knowledge_id": "…"} } } ] }
]
```

```jsonc
"stats": [ {"name": "冲击力", "value": 70, "display": "70", "is_primary": false}, … ]
```

**钉死的规则**（审查新增）：

1. `kind` 取值：`intrinsic` / `barrel` / `magazine` / `trait` / `mod` / `masterwork` / `memento` / `tracker` / `shader` / `ornament` / `catalyst` / `other`。
2. `scope`：`definition`（完整池，含退役与强化配对）或 `instance`（这一件在 310 里能换的）。
3. **强化 perk 保留两条同名条目**（真实数据如此），都带 `enhanced` 与互指的 `enhanced_plug_hash`；武器层给 `has_enhanced` 聚合布尔。
4. **实例级 `options` 只在 `type`（我的同类型武器）与 `compare` 输出**；`analyze` 的实例只给摘要（`gear_tier` + `option_counts` + `locked`），否则体积会爆。
5. `stats[].display` 第一版用**原始数值字符串**；Bungie 的 `displayInterpolation` 插值**不做**（避免半吊子，需要时另开一项）。
6. `schema_version` 放**响应顶层** `data.weapon_schema_version`（列表类也能读到）。
7. 列表类一律带 `total/returned/truncated`；身份块必备字段缺失时输出 `null` + warning，不抛异常。

### 3.2 精简身份块（列表类专用，字段写死）

`item_hash` / `name` / `name_en` / `rarity` / `rarity_tier` / `weapon_type` / `frame` / `rpm` / `roll_kind` / `is_craftable` / `icon_url`
\+ 列表特有：`owned` 或 `location`/`instance_id`/`power`、`matched_perks`、`farming_summary: {tier, source}`。

`inventory.type` 与 `weapon.type` 的边界（审查新增）：

| | `inventory_assistant(intent="type")` | `weapon_assistant(intent="type")` |
| --- | --- | --- |
| 内容 | 精简身份块 + 位置/光等 | 完整武器模板（定义级 sockets + 实例级 options） |
| 请求组件 | 现状（不请求 305/310） | 305 + 310 + 300 |
| 用途 | "我有哪些手炮" | "我这把手炮能换成什么" |

### 3.3 唯一事实源

| 表 | 现在几份 | 收拢到 |
| --- | --- | --- |
| 稀有度 | 3（其中三份不全） | `weapon_profile.RARITY` |
| 伤害 / 弹药 / 破盾 | 2 + 2 + 0 | `manifest_names`（查 `Destiny*Definition`，带缓存） |
| 属性名与顺序 | 硬编码 10 项 | `DestinyStatGroupDefinition` + `DestinyStatDefinition` |
| 槽位标签 | 中英两套 | `weapon_profile.slot_kind()` |
| 组件列表 | 17 处 | `profile_components.py` |
| 强化配对 | 无 | `data/weapon_enhanced_pairs.json` + `scripts/generate_weapon_metadata.py` + 新鲜度测试 |

### 3.4 本地数据挂载 + 覆盖表

三处来源合并进 `weapon.sources`；清单进 `weapon.farming`（含 `recommended_perks` 必刷特性、`source_ref.updated_at`、与 Manifest 的 `cross_check`）；选取率进 `weapon.popularity`（`columns[].items[]` 按 plug_hash 与 sockets 对齐）；社区资料进 `weapon.community`；**每个 plug 的本地结论汇总到 `options[].recommended`**（wishlist/popularity/farming/community），不再让调用方按名字跨四段拼。

| intent | 身份块 | sockets | 实例级 options | farming | popularity | community | sources |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `analyze` | ✅ | 定义级 | 仅摘要 | ✅ | ✅ | ✅ | ✅ |
| `info` | ✅ | 定义级 | — | ✅ | — | ✅ | ✅ |
| `perk_pool` | ✅ | 定义级 | — | ✅ | ✅ | ✅ | ✅ |
| `god_roll` | ✅ | 定义级 | — | ✅ | — | ✅ | ✅ |
| `popularity` | ✅ | 定义级 | — | ✅ | ✅ | — | ✅ |
| `type` | ✅ | 定义级 | ✅ | 精简 | — | — | 精简 |
| `compare` | ✅ | 实例 | ✅ | ✅ | — | — | ✅ |
| `catalog` / `filter_rolls` | ✅ 精简 | — | — | 精简 | — | — | 精简 |
| `catalyst` | ✅ | 催化槽 | — | — | — | — | ✅ |
| `perk_description` | 不进武器模板：返回独立 `perk` 对象（复用同一 plug 结构） | | | | | | |

### 3.5 固定 / 随机的语义分支

> 审查修正：固定武器不是"列出所有可选部件"，而是列**固有 + 固定特性**。

| intent | `roll_kind="fixed"` | `roll_kind="random"` |
| --- | --- | --- |
| `god_roll` | 列 `kind=intrinsic` 与 `kind=trait 且 option_count==1` 的 plug + `note="固定 roll 武器，没有推荐 roll"`；愿单有内容则作为补充标 `source` | 愿单推荐（结构化） |
| `popularity` | `note="固定 roll 武器，选取率没有意义"` | 现有逻辑 |
| `perk_pool` | `note="固定 perk（仅部件可选），不是随机池"` | 现有逻辑 |
| `filter_rolls` | 可按 perk 筛，但 `note="没有 roll 可变"` | 现有逻辑 |

### 3.6 数据驱动的边界（"改一处会不会牵一发动全身"）

**乱的来源不是数据驱动，而是同一概念有多份定义**（稀有度 3 份、属性硬编码、组件 17 处…）。只接一处是减少耦合。

| 层 | 归谁 | 内容 | 改动影响 |
| --- | --- | --- | --- |
| **契约层**（代码，稳定） | 我们定 | 字段名与结构、枚举值、覆盖表、话术、上限/截断、错误码 | 显式改契约 + 改快照测试 |
| **适配层**（代码，薄） | 一处：`weapon_profile` + `manifest_names` | 把 Manifest 翻译成稳定结构；**其它服务不许自己查库** | 只影响这一层内部 |
| **数据层**（DB，动态） | Bungie | 名称、稀有度、属性集合与顺序、插槽、perk 池、can_roll、强化配对、可锻造、来源、破盾、trait | **不改代码** |

四个护栏：① 一处翻译 + 键集合快照测试；② 生成物 + 过期检测（**Manifest 里没有版本号**——实测 83 张表全 `Destiny*`，故在下载时写 `manifest/fingerprint.json`：sha256 + 时间 + Bungie version 若有；生成物记指纹与表行数，启动比对、过期告警不阻塞）；③ fail-soft（缺字段输出 hash/原值 + warning）；④ 来源与时间标注。

**明确不做**：不让 JSON 结构随武器变化；不在服务层各自查库。

### 3.7 长期维护机制

1. 生成物 + 校验：`scripts/generate_weapon_metadata.py`（强化配对等）+ `tests/test_weapon_metadata_freshness.py`。
2. 契约测试：`tests/test_weapon_keys_snapshot.py`（每个 intent 的键集合）、`tests/test_weapon_socket_contract.py`（`kind`/`scope`/`roll_kind` 枚举、定义级/实例级不混、列表类必须有 `total/returned/truncated`）。
3. 体积闸：定义级 `options` 默认不带 `description`（只给愿单/清单标中的），实例级给全；契约测试卡每 intent 上限（现状 `perk_pool` 32 KB → 目标 10 KB 量级）。
4. `weapon_schema_version`（响应顶层）+ `manifest/fingerprint.json`。
5. 文档三处同源：本计划、`TESTING_CORPUS.md` 武器章节、skill `routing.md` 武器章节；测试加"武器 intent 覆盖"断言。
6. 旧键一次性删除（消费方只有本仓库语料/测试/skill）。

### 3.8 体验保障与迁移

- **人话层**（`summary`/`warnings`/`next_actions`）只增不减；**结构层**会变（这是目的，Agent 每次重读响应）。
- **P0 录基线**（见任务表）：硬门槛是"任何消失的字段都要有理由"。
- **内部消费者**（已核实，必须同批改 + 测试）：

| 消费者 | 依赖 | 不处理的后果 |
| --- | --- | --- |
| `starside_matching.match_build_inventory` | `WeaponDetail.perks_complete`、`sockets[].plug_name`、`InventoryItem.item_instance_id/name` | 社区配装"看看我缺什么"会坏 |
| `vendor_service` | `PerkService.annotate_god_roll(item_hash, plug_hash, perk)` | 商人商品愿单标记会丢 |
| `assistants.py` 武器分支 | 全部服务 | 同批改 |
| `weapon_tools.py` 遗留工具 | 同一批服务 | 跟着变（默认屏蔽），语料注明 |
| `build_assistant` 求解/farm_target | **不读**武器 perk（已核实） | 无影响 |

- **明确不保证**：写在别处的旧字段名会失效（本仓库已确认无此类消费方）；切换后必须新开任务。

---

## 4. 已定默认决策（不反对即执行）

| # | 决策 | 默认 |
| --- | --- | --- |
| 1 | 旧键 | **直接删除**，不留双份；P4 交付 `old → new` 映射表 |
| 2 | `god_roll` | **改成结构化**（`{kind: fixed\|recommended, source, pve[], pvp[], note}`），`analyze` 内同一结构 |
| 3 | P6 瘦身 | **做**（定义级不带 description） |
| 4 | 组件 | **加 310 / 302 / 308**；P0 记录 profile 耗时与体积基线，P3 后对比，若 `type` 明显变慢则改按需取 |

---

## 5. 分阶段任务（每阶段结束必须：`pytest` 全绿 ≥1069 + `scripts/verify_mcp.py` ok + 真机冒烟通过，才进下一阶段）

### P0 · 录基线（不动代码）
- 产出：`tests/baselines/weapon_responses/*.json`（固定问法：语料武器章节全部问法 + 社区配装库存匹配 + vendor 商品 + `catalog`/`type`/`filter_rolls`）+ 每条的**耗时与响应大小**；`scripts/diff_weapon_baseline.py` 生成四类差异报告。
- 验收：未改动状态下 diff 报告为"零差异"。

### P1 · `weapon_profile.py` + `manifest_names.py`
- 稀有度、`frame_of`/`intrinsic_of`/`rpm_of`、`roll_kind`、`socket_kinds`、`is_craftable`、`breaker_type`、`trait_ids`、`slot_kind`、身份块构造；名称表（伤害/弹药/破盾/属性）查 DB + 缓存；`manifest/fingerprint.json` 写入与比对。
- 验收：`tests/test_weapon_profile.py` 覆盖 6 把真实形态 + 框架三情形 + 稀有度 5 档（含 Ψ卷云II=稀有、刚愎自用=普通）+ 破盾 3 档 + 可锻造（遗产 true / 泰拉巴 false）+ 指纹不一致时告警文案可见。

### P2 · sockets（定义级）+ 强化 + can_roll + stat_effects ✅ 已完成
- 遍历**所有**插槽，按 plug 类别归 `kind`；保留 `randomized/reusable/single` 三态。
- 强化配对走生成物：规则是「同 plug 类别 + 同名字 + 强化版唯一 + 描述不同」。
  严格一对一虽然能凑 254 组，但其中 49 组描述完全相同（是同名的另一份副本，属误配），
  所以改成现在的规则：**228 组 / 216 个 perk 名**，另外 56 组跳过并把理由写进生成物。
- `can_roll` 来自 plug set 的 `currentlyCanRoll`（退役 perk 不再冒充"能滚到"）。
- `stat_effects` 来自 plug 的 `investmentStats`（箭头制退器：后坐 +30、操控 +10）。
- **选项分级**：roll 相关栏位（框架/枪管/弹匣/特性/瞄具/握把）给全量；装饰类只给
  `option_count` + 3 个样本 + `options_truncated`（实测遗产着色器 694 项、大师杰作 167 项，
  全量展开会把响应撑到上百 KB）。遗产 sockets 实测 45 KB（P6 再砍描述）。
- `god_roll` 结构化：`{weapon, kind: fixed|recommended|none, source, source_detail, pve[], pvp[], fixed_perks[], note}`。
- 验收（`tests/test_weapon_sockets.py` 13 条 + `tests/test_weapon_metadata_freshness.py` 5 条）：
  合成替身覆盖三态与退役/强化/效果；真实数据断言遗产覆盖大师杰作/模组/纪念物/追踪器、
  装饰类被裁、箭头制退器效果、强化配对可用；生成物与当前 Manifest 重跑逐条一致。
- 基线 diff：唯一"消失"是 `data.god_roll`（字符串 → 结构），已按规矩登记 allowlist 与理由。

### P3 · 组件与实例级 ✅ 已完成
- `profile_components.py` 收拢 17 处字面量，且**每个常量精确等于它替换掉的那串数字**
  （`[102,200,201,205,300,305]` 与 `[102,200,201,205,300,304,305]` 是两个集合，替错就悄悄多取组件）。
  `tests/test_profile_components.py` 把数字钉死，并禁止服务里再出现裸组件字面量。
- 加 310/302/308：`WEAPON_DETAIL = INVENTORY + [305, 302, 310, 308]`，实例级字段进 `WeaponDetail`：
  `gear_tier` / `item_level` / `quality` / `locked` / `tracked` / `options`（副本级可换部件）+ `notes`。
- **`item.state` 位序按官方生成类型**（`bungie-api-ts` 的 `ItemState`）：Locked=1、Tracked=2、
  Masterwork=4、Crafted=8、HighlightedObjective=16。老资料里的 Crafted=4 / Masterworked=32 是错的；
  真机交叉核对：Locked 250 件、Tracked 0 件、Crafted 置位的 204 件里 170 件定义可锻造。
- **"能换几个"以 310 为准，不能拿 T 级去算**（745 件武器特性栏实测）：

  | 情形 | 每栏可插数 |
  | --- | --- |
  | T5（非锻造） | 大多 3（305 件中 286 件），少数 2 或 1 |
  | T4 / T3（非锻造） | 2 |
  | T2（非锻造） | 1 |
  | 锻造件（Crafted 位） | 通常只有 1 —— 310 只给当前选中的那个 |
  | 无 T 的旧装备 | 1–6 不等，无规律 |

- **成本与对策**（P0 基线对比，26 例）：
  - 体积 391.3 KB → 455.3 KB（+64 KB，全部是新增字段）；唯一明显增长是 `type_list`
    31 → 89.4 KB（5 把武器带实例选项 ≈ +11.7 KB/把）。已砍一刀：实例级只对 roll 栏给全量，
    模组/大师杰作/纪念物给"计数 + 3 样本 + `options_truncated`"（砍前该例 135.5 KB）。
  - 取档：310 让整份 profile 从 **3.3 MB 涨到 10.2 MB**（1425 个实例带 reusablePlugs）。
    对策：武器详情改走共享 `ProfileCache`，`FULL` 补上 308 以免后台刷新把并集降级；
    实测第一次 `type` 25.3 s（真实取档 1 次）→ 紧接着同一查询 **2.7 s、0 次取档**。
  - 耗时总计 44 s → 83 s 受 Bungie 网络波动影响大（本例 `vendor_banshee` 单例就从 3.0 s 飘到 8.5 s），
    故以"取档次数 + 暖态耗时"为准，不以总耗时为判据。
  - 未读字段一律 `null` + `notes` 说明（无 310 → `options` 空并注明；无 300 → 三个字段 null；无 state → locked/tracked null）。
- 验收：`tests/test_weapon_instance.py`（30 条）覆盖位序、缺失、canInsert 过滤、去重、
  未知槽位下标、裁断、以及服务层"组件缺了要说清楚"；真机四档 T 各一例核对通过；
  基线 diff 无「无理由消失」。

### P4 · 十处形状收敛 ✅ 已完成
- 新增 `services/weapon_payload.py`：**唯一**造形状的地方（`weapon` / `sockets` / `stats`），
  `WEAPON_SCHEMA_VERSION` 随响应顶层输出。十处形状全部改成调它：
  `manifest_query_service`（info/stats/catalyst/perk_description）、`perk_service`（perk_pool/god_roll）、
  `weapon_analysis_service`、`weapon_detail_service`、`weapon_compare_service`、
  `weapon_roll_filter_service`（目录 + 持有两条路径）、`weapon_popularity_service`、`inventory.type` 边界。
- 工具层：武器分支搬到 `tools/_weapon_branches.py`（`assistants.py` 只认参数与分发，上限从 1431 收到 1352）；
  社区富集搬到 `tools/_enrichment.py`。
- `stats` 改数据驱动：顺序与"是否按数字展示"来自 `DestinyStatGroupDefinition`，名字查 `DestinyStatDefinition`，
  `is_primary` 取 `primaryBaseStatHash`（遗产 12 项，含以前硬编码表里没有的后坐方向/弹药生成）。
- 键映射表（old → new）写进 `TESTING_CORPUS.md` 武器章节附录；119 处消失字段逐条登记进基线 allowlist。
- **对计划表的一处修正（有实测依据）**：原表写 `type` = "完整武器模板（定义级 sockets + 实例级 options）"，
  实测 5 把武器 **597 KB**（每把约 120 KB，50 把就是 6 MB）。列表要回答的是"我这把能换什么"，
  完整池子是单把武器的问题，故改成：`type`/`compare` 用 `column_list()`（列计数 + `equipped`）
  + 实例级 `options`（310）；`info`/`perk_pool`/`analyze` 才展开完整池（`options_available=true`）。
  同一次实测还发现 compare 给每个副本各带一份完整池子（analyze 356 KB），改成列计数后总基线
  1484 KB → 832 KB。`sockets[].options_available` 就是"这次是没展开、不是没有"的标记。
- 验收：`tests/test_weapon_keys_snapshot.py`（23 条，走工具层逐 intent 钉键集合，并覆盖 perk_pool 的 await 回归）；
  真机四把武器 × 11 个 intent 形状一致（`weapon` 20 键、`sockets` 8 键、`stats` 6 键）；消费者（starside 匹配、
  filter_rolls、vendor 愿单标注）测试仍绿；基线 diff 无「无理由消失」。

### P5 · 本地数据 + 语义 + 文档 ✅ 已完成
- 新增 `services/weapon_local_data.py`：愿单／选取率／刷取清单／社区四路读进来（全部 fail-soft，
  坏数据只缺一块 + warning），汇总成：
  - `weapon.farming`（评级 + `recommended_perks` + **与 Manifest 的 `cross_check`**：
    清单写的框架/伤害类型对不对得上、必刷特性现在还滚不滚得到）；
  - `weapon.popularity`（选取率摘要：每栏前 5 + 热门组合 + 来源）；
  - `weapon.community`（社区条目）；`weapon.sources[]`（来源/更新时间/`trust` 一张表）；
  - 每个插槽选项的 `recommended`：`wishlist` / `popularity{selection_rate,rank,column}` /
    `farming{columns,must_farm}` / `community{knowledge_id}` 就地汇总。
- 三个兄弟字段（`farming_list` / `community_references` / 散在各处的愿单标注）收进模板；
  列表类（type/catalog/filter_rolls）保留覆盖整张列表的顶层 `farming_list`，逐件只给清单摘要，
  且 `popularity`/`community` 明说"这个 intent 不查"（键不少、语义不糊）。
- **覆盖表落地**：`weapon_local_data.attach(include_popularity=…, include_community=…)` 逐 intent 控制，
  `tests/test_weapon_local_data.py` 逐 intent 核对（info/god_roll 不带选取率、popularity 不带社区）。
- 固定/随机分支（计划 §3.5）：`god_roll` 固定武器给固有+固定特性（P2 已做）、
  `popularity` 固定武器加"选取率不反映哪套 roll 更值得刷"、`perk_pool` 加"仅部件可选"、
  `filter_rolls` 命中固定武器时加"没有 roll 可变"。
- 文档：`TESTING_CORPUS.md` 武器章节补本地资料口径与形状说明；`skills/.../routing.md`
  武器章节重写（新增 `sockets` 字段语义与"能不能换成某 Perk 看哪两处"），并跑 `install_skill.py` 同步。
- 验收：`tests/test_weapon_local_data.py`（12 条）+ 键集合快照仍绿；真机 `perk_pool` 遗产实测
  清单 T1/来源/`cross_check` 齐全、24 个选项带 `recommended`；基线 diff 无「无理由消失」
  （闸门新增"按用例 + 子树"登记，避免整块搬家逼出几百条叶子登记）。

### P6 · 瘦身与缓存
- 定义级不带 description；perk 池/配对表进程内缓存；体积对比记录。
- 验收：`perk_pool` ≤ 10 KB 量级，且 `describe` 命中项仍带描述。

---

## 6. 风险与取舍

| 项 | 说明 |
| --- | --- |
| 破坏性 | 换新结构后旧键消失；消费方只有本仓库语料/测试/skill |
| 影响面 | 6 服务 + 工具分支 + 3 新模块 + 2 脚本；`analyze` 是最大聚合，重点测 |
| **组件代价** | 310 让 profile 响应变大（实测 1425 个实例带 reusablePlugs）；P3 后必须对比 P0 基线，必要时按需取 |
| 中间态 | P4 改完服务、P5 才更新文档 —— **中间不要给外部测试**，一阶段一 commit 便于回滚 |
| Manifest 更新 | 配对表与名称表走生成脚本 + 校验测试 |
| 明确不做 | 武器"菜单→详情"；催化剂/强化 perk 的**账号解锁状态**（需记录组件，保持 `not_checked` 明说）；属性插值显示 |

---

## 7. 验收清单

- [ ] 稀有度一份表；蓝/绿/白不再是空串（Ψ卷云II=稀有、刚愎自用=普通）；老武器 T 级显示"无"
- [ ] 身份块含 `frame`/`intrinsic`/`rpm`(遗产 65)/`breaker_type`/`trait_ids`/`source`/`is_craftable`/`has_enhanced`/`gear_tier`/`roll_summary`
- [ ] 泰拉巴 `frame=null` + `intrinsic="贪食野兽"`，话术用"异域专属特性"
- [ ] `sockets` 覆盖全部插槽（含大师杰作/模组/纪念物/追踪器）；`kind`/`scope` 正确
- [ ] 定义级给完整池（遗产特性栏 19 + 退役标记 + 强化配对）；实例级给 T 级与可选项（T5 遗产特性栏 3）
- [ ] 强化成对（`enhanced` + 互指 `enhanced_plug_hash`）；`has_enhanced` 正确
- [ ] perk 带 `stat_effects`（箭头制退器：后坐 +30、操控 +10）
- [ ] `stats` 按 StatGroup 输出，含 冲击力/充能时间
- [ ] 本地数据都在模板里：`farming`（含必刷特性与 `source_ref.updated_at`）、`popularity`（按 plug_hash 对齐）、`community`、`sources`
- [ ] 遗产 `farming.list="刷取清单-绿弹紫枪"`、`tier="T1"`、`source="深岩墓室"`；`cross_check` 标出清单的 RPM 65
- [ ] 同一 perk 的本地结论汇总在 `options[].recommended`
- [ ] 组件集合唯一；未读账号字段为 null + 说明
- [ ] 键集合快照通过；列表类都有 `total/returned/truncated`
- [ ] 人话层与基线一致（只允许增补）；`baseline diff` 无"无理由消失"
- [ ] 内部消费者回归：社区配装匹配、vendor 愿单标记测试仍绿
- [ ] `perk_pool` ≤ 10 KB 量级（P6）
- [ ] `pytest` 全绿、`PARAMETER_GUARD=ok`、8 个工具、语料武器行逐条实跑
