# 图标 URL 全覆盖 + 模型侧 HTML 渲染（开发档案）

状态：**两轮都已实现并验证**（第一轮：物品/装备/perk 的 `icon_url` 覆盖；第二轮：活动道 + Starside 相对路径 + `popularity` 统一）。§十 的 7 项拍板**已落地**（第 6 项按"不刷基线夹具"处理）。
下一阶段：渲染 skill（见 §九）
最后更新：2026-10-04

---

## 一、为什么要做这件事

调用方（宿主 / 模型）要能在对话里**渲染装备卡片**——卡片要有真图标，不能只有文字和色块。

实测过的症状（用户截图）：`weapon_assistant(intent="analyze")` 的 `weapon` 块**带** `icon_url` → 卡片能出真图标；而 `world_assistant(intent="community")`（Starside 社区资料）**只回文字摘录、没有图片 URL** → 只能放色块。

目标：**MCP 的所有输出，凡是能关联图片的，都要带上图片 URL。**

---

## 二、基准：老 web 的口径（照它，别自己发明）

**老 web = `~/项目/Destiny_MCP`**（同机姊妹仓库）。本仓 `legacy/` 里**没有任何图标代码**——查过了。

| 口径 | 老 web 的做法 | 位置 |
| --- | --- | --- |
| **键名** | 一律 `icon_url` | `webui/api/destiny_render_blocks.py:270`（`_icon()` 统一入口）、`src/destiny_mcp/models/base.py:38` |
| **perk** | **与物品同键名，不另造** | `destiny_render_blocks.py:316`（`_perk()`）、`:623`（duplicates 的 perk）、`models/base.py:38` `PerkInfo.icon_url` |
| **perk 图标来源** | perk 池每个 plug 都带；描述走 `displayProperties.icon` | `services/perk_service.py:172`、`:254 _item_icon_url()`；`manifest_query_service.py:356-365` |
| **CDN 归一** | **生产侧一处**：加载 Manifest 时拼好 `BUNGIE_BASE_URL + display["icon"]`，下游只读 `info["icon"]` | 老 MCP `manifest.py:347` |
| **消费侧校验** | 再做一道白名单：只放行 `https://www.bungie.net/common/destiny2_content/icons/` | `webui/api/destiny_icons.py:34 normalize_destiny_icon_url()` |
| **消费侧容错** | 容忍旧键 `icon_url / iconUrl / image_url / imageUrl / icon` | `destiny_icons.py:13`（说明生产侧主键是 `icon_url`） |

**结论**：我们照抄"键名 `icon_url`、perk 同名、值一律绝对 Bungie CDN 地址"，**唯一差别**是生产侧对"已经是绝对地址"的 URL 原样放过。

---

## 三、设计决定（用户拍板）

1. **内部只传 hash，不传 URL**——URL 是**派生物**，由 hash 决定；派生物不该进管线，否则每个中间结构都要带它、体积涨、投影还要专门记它。
2. **扫描 / 分页过程中不带 URL**——只在**最终出口**现取。
3. **图片源只有一条：Bungie CDN**——不管资料来自哪，图片统一按物品 hash 从 Bungie CDN 取（绕开"社区资料是不可信参考、外链属于别人的数据"这条边界）。
4. **活动 / 副本也带上**（突袭、地牢、PvP 场次）——**⚠️ 走另一条道**，见 §八。

### ⚠️ 两个必须写清的技术前提

**（1）"按 hash 绑定"必须经过 definition 查表，不能拼字符串。**

```
✅ .../icons/6495d6a04cc9e7c0515b27b26ad8be60.jpg   ← 文件名在 definition 里
❌ .../icons/{item_hash}.jpg                        ← 不成立
```

**图标文件名 ≠ 物品 hash**。实测基准：星狐座 → `https://www.bungie.net/common/destiny2_content/icons/6495d6a04cc9e7c0515b27b26ad8be60.jpg`（用户实测，与本轮真机核对**逐字一致**）。

所以准确定义是：`hash → definition → displayProperties.icon → 绝对 URL`。

**（2）出口现取要"零额外成本"。**

50 件武器的清单若逐件查 definition = 50 次查询。**优先复用出口手上已经读到的定义**（它们本来就要读名字/类型）；只有真没有时才查，且要能批量。

---

## 四、实现

### 单一出处

新增 `destiny_mcp/utils/icons.py`：

- `BUNGIE_ORIGIN` —— **全仓唯一**的源站字面量
- `icon_url(value)` —— 把相对路径变成绝对地址；已是绝对地址则原样放过（**两条道共用的归一**）
- `is_bungie_icon(url)` / `is_bungie_activity_image(url)` / `is_bungie_image(url)` —— 形状判据（物品图与活动图**两条前缀**，不合成一条宽前缀）

**出口函数**只有一个：`manifest_lookup.get_icon_url(*, item_hash=…, activity_hash=…)`，
内部两条道（`_activity_image_path` 走活动表、`get_item_info` 走物品表）。活动道还要
"从一组 hash 里挑第一个查得到图的"（副本有多个难度档）：`first_activity_with_icon`。

`manifest_data.BUNGIE_BASE_URL` 改成**再导出**同一份字面量。

**12 个构造点全部收敛**（`manifest_search.py:72`、`inventory_analysis_service._cdn_url`（删除）、`manifest_query_service._absolute_icon_url`（删除）与 4 处内联、`weapon_profile:338`、`fragment_service:268`、`activity_service:686`、`pvp_weapon_service:315`、`build/models.py:634`、`loadout_service:139`）。现在**全仓只有 `utils/icons.py` 含该字面量**（守门扫）。

### 体量：先抽代码，上限只降不抬

第一轮：新增 3 个小模块 + 2 处搬家：`services/inventory_lookup.py`（`locate_instance`）、`services/pattern_records.py`（`cell/merge_cell`）、`services/rotation_tables.py`（`lost_sector_block`）、`_tuning_rows` → `build_results.tuning_rows`、`crafting_sources_block` → `tools/_farming.py`。

第二轮（补活动道时）：`services/pvp_match_tally.py`（PvP 榜的两张身份表）、
`rotation_service._tables_block` → `rotation_tables.tables_block()`、
`raid_report_service` 的组件读法 → `activity_counters_service.metric_progress`（顺带消掉
**同一组件两份读法**）。

上限随之**收紧**：`build_projection 137→120`、`inventory_service 783→762`、`pattern_service 488→465`、
`rotation_service 313→300→285`、`_patterns_branches 215→200`、`raid_report_service 269→268`、
`pvp_weapon_service 434→429`。**一处理都没抬。**


---

## 五、清点表（权威来源：守门当场打印）

**44 有 / 29 在台账**（台账每条写明理由，`tests/test_icon_url_output.py` 的 `_EXEMPT`）。

### 本轮补齐（原本没有 → 现在有）

| 出口 | 位置 |
| --- | --- |
| `inventory_assistant(intent="mods")` 的**五件护甲**（用户点名） | `services/inventory_service.py:509` |
| 护甲模组槽行（`item` 的 sockets / `mods` 的 mods） | `services/armor_payload.py:278` |
| `weapon_assistant(intent="patterns")` 图样行 | `services/pattern_service.py:237`、`tools/_patterns_branches.py:139` |
| `build_assistant(find/recommend)` **默认出口**候选行 | `services/build_projection.py:27`（`_ROW_ITEM_FIELDS`） |
| `loadout_assistant(get)` 的 `items[]` / `build_template` | `models/loadout.py:70`、`services/loadout_service.py:365/546/703`、`services/build_results.py:269` |
| `duplicates` **组级**图标 | `services/inventory_analysis_service.py:525` |
| `world_assistant(rotations)` 奖励行 | `services/rotation_service.py:204` |
| `set_bonus` 套装五件 | `services/set_bonus_service.py:107` 附近 `armor_pieces[]` |
| `artifact` / `artifact_mod` | `manifest_artifacts.py:57/97/118/231`、`services/artifact_service.py:381/428` |
| `subclass_assistant(get)` 的 plugs / available | `models/subclass.py`、`services/subclass_service.py:443/452`、`manifest_plugs.py:55` |
| `armor_mods` 列表 | `manifest_armor.py` `_scan_armor_mods` |
| `vendor` 价格行 | `models/vendor.py` `VendorCost`、`services/vendor_service.py:391` |
| 职业金装 roll 特性（3 处） | `services/armor_class_item.py:86/131/190`、`services/armor_payload.py:359` |
| `equip_preview` / 金装唯一精确匹配回执 | `tools/_armor_branches.py:352`、`tools/_build_confirmation.py:200` |
| 愿单读取的 perk 行 | `services/perk_service.py:124/139` |

### 本来就有

`weapon.analyze / catalog / filter_rolls / type / info / stats / compare / compare_duplicates / perk_description / catalyst`、`exotic_armor`、`armor_item`、`vendor(vendor_banshee)`、`weapon_history`、`pvp_weapons`、`catalog.matched_perk_details`、实例级 perk options 等（44 处全表可由守门当场打印）。

---

## 六、守门

`tests/test_icon_url_output.py`，**10 条**（前 5 条是 2026-10-04 第一轮，后 5 条是同日补活动道时加的）：

1. `test_no_second_icon_url_constructor` —— 谁再自己拼 Bungie 图标 URL 就红（判据：f-string / `+` 拼接里含 `bungie.net` 字面量，且绑给 `icon*` 名字 / `icon*` 字典键 / `icon*` 关键字实参）
2. `test_the_icon_source_is_the_only_place_with_the_origin_literal` —— 源站字面量只许在 `utils/icons.py`
3. `test_identity_rows_carry_icon_url` —— **三种**身份行（字典字面量 / 键清单常量 / `Model(item_hash=…, name=…)` 构造调用）必须带 `icon_url`，否则进 25 条 `_EXEMPT` 台账
4. `test_icon_exemption_ledger_is_not_stale` —— 台账条目过期也判红
5. `test_recorded_baselines_use_openable_bungie_icon_urls` —— 基线里每个非空 `icon_url` 必须是 `is_bungie_icon()` 认可的地址
6. `test_activity_rows_carry_icon_url` —— **活动/副本身份行**（带 `activity_name`/`activity` 的行）必须带 `icon_url`，**没有台账**（每一行都是真实活动，给不出图就是漏了）
7. `test_activity_images_come_from_the_activity_lane` —— `pgcrImage` 在全仓**只有** `manifest_lookup.py` 读（按"代码里的字符串字面量"判，docstring 里提它不算）
8. `test_the_two_lanes_are_dispatched_by_definition_table` —— 出口按**表**分道：活动 hash 去物品表会当场断言失败；`pgcrImage` 优先、占位横幅退到 `displayProperties.icon`、两个哨兵都给空串
9. `test_popularity_paths_agree_on_perk_rows` —— 同一个 `popularity` intent 的两条路 perk 行**同一套键**（下次再漂就红）
10. `test_duplicates_perk_rows_stay_lean_by_volume` —— `duplicates` 的 perk 行正面钉在 `{name, slot}`（体积口径，不是忘了）

### 注入矩阵（第一轮 6 种 + 本轮 4 种，全咬红；`touch` + `PYTHONDONTWRITEBYTECODE=1`，恢复后 sha256/`cmp` 逐字节通过）

| 注入 | 结果 |
| --- | --- |
| 身份行缺图（`armor_payload.socket_rows` 字典） | 红：点名 `armor_payload.py:278 socket_rows` |
| 构造调用缺图（`build_results` 的 `LoadoutItem`） | 红：点名 `build_results.py:269` ← **这条当初漏了，补上判据后才咬住** |
| 自己拼 URL 绑给 `icon_url` | 红：`loadout_service.py:705` |
| 第二处源站字面量 | 红：`['weapon_profile.py', 'utils/icons.py']` |
| 台账条目过期 | 红：点名 `build_results()→LoadoutItem` |
| **全新模块里的新出口** | 红：`_probe_new_exit.py:8 new_weapon_rows` ← **证明能抓"以后新加的漏网出口"** |
| 活动行去掉 `icon_url`（`activity_service` 的 history 行） | 红：点名 `activity_service.py:575 get_activity_history ['activity_name']` |
| 第二处读 `pgcrImage` | 红：`['manifest_lookup.py', 'services/weapon_profile.py']` |
| 两条道对调（活动 hash 走物品道） | 红：`AssertionError: 活动 hash 走进了 DestinyItemDefinition（不是活动道）` |
| `popularity` 的 perk 行加回 `icon_url` | 红：键集合不一致（本地摘要 vs 选取率快照） |
| `duplicates` 的 perk 行加 `icon_url` | 红：`duplicates 的 perk 行变胖了：['icon_url', 'name', 'slot']` |
| Starside 图标退回"抹掉" | 红：`![](assets/…)` 断言失败 |

### 守门的已知边界（别当成"图标全查过了"）

- 名字/hash 键表是**封闭词表**（**不含裸 `hash`**——那会把套装层级/记录/档位表全冲进来）
- 只扫 `destiny_mcp/**`（`legacy/`、`tests/` 不在内）
- **不追运行期拼装**
- 活动行判据只看**字面量行**（`result["activity"] = …` 这种下标赋值扫不到）


---

## 七、验证

| 项 | 结果 |
| --- | --- |
| 开发机 `pytest -q` | **2002 passed**（第一轮基线 1995；本轮 +7 条守门） |
| **干净树**（`git archive HEAD` 导出、`env -i`、干净 HOME、**无 `.env`**、`PYTHONPATH` 钉住临时树） | `import destiny_mcp.server` OK + **1984 passed / 18 skipped**（= 1977 + 7，**没有新增跳过**；18 条要本地 `manifest/*.sqlite3`） |
| 语料 runner `scripts/run_corpus_all_rows.py` | **264 PASS / 3 INFO / 1 SKIP / 0 FAIL**（与第一轮基线逐项一致；4 次调用层失败全是上游：排行榜空 ×3、`stats period=season` 是文档化的接口限制） |
| **受控基线 diff（本轮）** | `git worktree` 出改动前（`2a4cd8f`，物品道那一轮）跑一次、**同账号紧接着**跑改动后：武器面 27 例**路径零新增零消失**（仅 `god_roll.source_detail` 这个既有的非确定性值变化）；护甲面同。活动出口另做逐字段对比（武器/护甲基线不覆盖活动 intent）：history **+2**、pgcr **+2**、raid_report **+2**（dungeon 同）、rotations **+3**、pvp_weapons **+4**，**消失路径 0** → 纯加法 |
| 真机 URL 形状 | 第一轮：星狐座 → `.../icons/6495d6a04cc9e7c0515b27b26ad8be60.jpg`，与用户实测逐字一致 |
| 真机出口取样（第一轮，物品道） | 9 个新出口逐个 `curl`：**全部 HTTP 200 + `image/jpeg\|png`**，`打开失败: []` |
| **真机 curl（本轮，活动道）** | 突袭 `raid_report(mode=raid)` 的世界吞噬者、地牢 `raid_report(mode=dungeon)` 的二象性、PvP `history`/`pvp_weapons` 的光辉悬崖：三条 **HTTP/2 200 + `content-type: image/jpeg`**（150,101 / 58,827 / 185,694 字节） |
| **注入验证（本轮 6 条）** | 活动行去图 → 红并点名 `activity_service.py:575`；第二处读 `pgcrImage` → 红并列出两个持有者；两条道对调 → 活动 hash 走进物品表当场断言失败；`popularity` 加回图 → 两条路键集合不一致；`duplicates` perk 行加图 → 行变胖；Starside 图标退回抹掉 → 断言失败。**恢复后 6 个文件 sha256 全部逐字节一致** |
| Starside 图标落点（数据面） | 渲染 `entities/perks.json` 全部文本后共 **6** 个不同相对路径，**`missing on disk: 0`**、**0 条外链**、**0 条未解析**（`assets/elements/arc/icons/5fc7a97f63.webp` 这类） |
| 体积连续性 | 武器/护甲基线 27 + 23 例的载荷字节数与改动前一致（585 KB / 467 KB） |

**同步了 1 条语料断言（不是为绿而改）**：`scripts/run_corpus_all_rows.py:1523` 原本钉 `"icon_url" not in dup_first`（组级图标曾被投影掉）。现在改钉"组级必须有、实例级仍然没有、perk 行仍是 `{name, slot}`"，注释里写了体积账：`duplicates` 20 KB 闸下 **13.5 → 13.965 KB**。

**另有 1 条既有测试同步了形状（也不是为绿而改）**：`tests/test_personal_migrated_read_features.py` 原本断言选取率快照的 perk 行**带** `icon_url`；按第 4 项拍板统一成不带，顺带加钉 `popular_combinations[].perks[]` 也不带。

**基线夹具 `tests/baselines/**` 未刷**（拍板第 6 项按"不动"处理）：它们是武器/护甲的冻结快照，本轮没有改到那两类出口；活动出口的对照改用上面那次逐字段 diff（并且它证明的是"只加不减"）。


---

## 八、活动 / 副本的图标：**缺口已补**（2026-10-04 第二轮）

**用户明确要求"活动/副本也带上"（突袭、地牢、PvP 场次），第一轮清点表里没有这一类 —— 确实是漏了。**

**两条道是这么分的**（共用一个出口函数 `manifest_lookup.get_icon_url()`，内部按参数分道）：

| 道 | 查什么 | 取哪个字段 | 谁在用 |
| --- | --- | --- | --- |
| **物品道**（`item_hash=`） | `DestinyInventoryItemDefinition` | `displayProperties.icon` | 武器、护甲、模组、perk、碎片、商品 |
| **活动道**（`activity_hash=`） | `DestinyActivityDefinition` | `pgcrImage` →（占位时退）`displayProperties.icon` | 突袭、地牢、PvP 场次、里程碑/夜幕活动行 |

活动道里那两条来源是**优先级**不是二选一：上游给 707 条活动塞了通用占位横幅
（`/img/theme/destiny/bgs/pgcrs/placeholder.jpg`，**PvP 活动全在坑里**），占位与
`missing_icon_d2.png` 两个哨兵都要往下退 —— 退不到就给空串，**不拿别的副本的图顶上**。

### 覆盖到的出口

| 出口 | 加的字段 |
| --- | --- |
| `activity_assistant(intent="history")` | 每行 `activity_hash` + `icon_url`（与名字**同一个 hash**：`referenceId` 优先、退回 `directorActivityHash`） |
| `activity_assistant(intent="pgcr")` | 顶层 `activity_hash` + `icon_url` |
| `activity_assistant(intent="aggregate")` | 每行 `icon_url`（`activity_hash` 本来就有） |
| `activity_assistant(intent="raid_report")` | 每行 `activity_hash` + `icon_url`（一个副本挂多个难度 hash → 取第一个**查得到图**的，**hash 与图同源**） |
| `activity_assistant(intent="pvp_weapons")` | 新增 `activities[]`：本次分析里打过的活动（`activity_hash`/`name`/`icon_url`/`matches`） |
| `world_assistant(intent="rotations")` | 里程碑 `activities[]` 与夜幕行各加 `icon_url` |
| `activity_assistant(intent="weapon_history")` | 物品道改走同一个出口（顺手删掉一处 `get_item_info` + 自己拼 URL） |

**真机验证**（三个出口各 `curl` 一次，见 §七 的验证表）。

---

## 九、下一阶段：模型侧 HTML 渲染 skill

**设想**：模型输出时**自己渲染 HTML** 在对话框里（豆包已经可以做到）。**渲染格式照老 web 写**（`~/项目/Destiny_MCP` 的 `webui/api/destiny_render_blocks.py` —— 那里已经有 `_icon()` / `_perk()` 这类渲染块构造器，是现成模板）。

### 与图标覆盖的配套关系

> **没有 `icon_url`，HTML 渲染就只有文字和色块。**（就是用户截图里 Starside 那条的症状。）

**顺序：先补齐 `icon_url`（本轮，已做）→ 再写渲染 skill。**

### skill 要准备的三样

| # | 要什么 | 现状 |
| --- | --- | --- |
| 1 | **老 web 的渲染格式** | **已找到**（`webui/api/destiny_render_blocks.py`），可直接作为模板 |
| 2 | **HTML 渲染的约定** | **待定**：允许哪些标签/内联样式、图片尺寸与降级（图挂了显示什么）、列表/卡片布局 |
| 3 | **可用于渲染的字段表** | **待列**：`icon_url` 是基础；还有名字、类型、稀有度、能量、perk 列表……**不列清楚，模型会瞎猜** |

### 仓库规矩（做的时候必须遵守）

- 新 skill 放 `skills/` 下 → **必须跑 `scripts/install_skill.py`**（不跑，宿主读的还是旧版）
- 要**登记进契约与文档索引**（`tests/test_skill_contracts.py` + `AGENTS.md` 文档索引会**双向核对**，漏了会红）

---

## 十、7 项拍板 —— 结果（2026-10-04 第二轮，全部落地）

| # | 事项 | 拍板与落地 |
| --- | --- | --- |
| **1** | **Starside 社区资料给不给图** | **给相对路径**：`![](icons/<hash>.webp)` 不再抹掉，补成 `assets/<主题>/icons/<hash>.webp`（`services/starside_icons.py`）。理由写在那个模块开头：图标是本地文件（`missing on disk: 0`），而 `starside.work` 是第三方热链（`redistribution_license: not_established`）。**主题名从 `index.json` 反查**：同一 hash 出现在多个主题下时取字典序最小者（实测这 90 个多主题 key 的**字节完全相同**，所以不影响渲染）。那句过期注释（"那份资源没随归档给我们"）已改成真实原因 |
| **2** | 定义级 perk 池带不带图标 | **不带**。带 = **+17 KB/把（图标 13.2 + 描述 4.0，P6 实测）**，出处 `docs/plans/WEAPON_FORMAT_PLAN.md:360`。台账理由已按这个口径重写（原来写的是"P6 体积口径"这种不点数的说法） |
| **3** | `duplicates` 的 perk 行 | **不带**。每实例 1.81 KB 里 perk 占 **1.64 KB（91%）**，`limit=5` 有 **20 KB 闸**（`docs/adr/021…:15`、`RESPONSE_PROJECTION_PLAN.md:12`）。**正面钉住**：`test_duplicates_perk_rows_stay_lean_by_volume` |
| **4** | `popularity` 两条路不一致 | **统一成不带**：`weapon_popularity_service._enrich_entry` 不再给 `icon_url`（连带 `popular_combinations[].perks[]`）。**守门**：`test_popularity_paths_agree_on_perk_rows` 比两条路的键集合 |
| **5** | 其余台账 | **接受**：25 条，每条都在 `_EXEMPT` 里写明"是什么、凭什么不给图"。台账顶部单列了**两类不在扫描面里的**（定义级池 / duplicates 的 perk 行）与它们的正面守门 |
| **6** | `tests/baselines/**` 要不要刷进本轮新键 | **没做**（仍是冻结快照）：它们只覆盖武器/护甲响应，与活动道无关；相关测试本来就绿 |
| **7** | 活动 / 副本图标 | **已补**（见 §八） |

---

## 十一、副作用与工位卫生

- **`data/dim_wishlists.json`**：跑真机时生成，未跟踪。`.gitignore` 已补 `data/dim_wishlists.json`（原来只盖住了包内那份 `destiny_mcp/data/dim_wishlists.json`）。
- 工作区（第一轮）：41 改 + 6 新；第二轮又动了活动道 / Starside / popularity / 守门 / 文档，**按主题拆成多条提交**。
- 本轮操作中出现过两次失误（一个 shell 注入函数参数写错，在仓库根留下 4 个 0 字节怪名文件；同一次导致注入未自动恢复），**均已在核对绝对路径后复原并逐字节核对**。


---

## 十二、这一轮的通用教训（与 §六 守门同样重要）

**给调用方看的字段，会在某条出口上悄悄没了——而且守门抓不到"没被扫到的出口"。**

所以 §六 的守门里最值钱的不是前四条，而是**第六条注入**：它在**一个全新模块里新加一个出口**，照样被抓住。**防未来比抓现在重要。**

同族的教训（本仓已有记录）：`ls-tree` 证明"文件在提交里"、不证明"树能跑"；`.venv` 的 editable 映射会让"临时树验证"作弊；`ruff` 抓不到"从一个模块导入一个它根本没有的名字"。
