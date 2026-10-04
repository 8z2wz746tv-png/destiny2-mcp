# 图标 URL 全覆盖 + 模型侧 HTML 渲染（开发档案）

状态：**五轮都已实现并验证**（第一轮：物品/装备/perk 的 `icon_url` 覆盖；第二轮：活动道 + Starside 相对路径 + `popularity` 统一；第三轮：渲染 skill；第四轮：豆包三个真实场景暴露的缺口，见 §十三；**第五轮：副本对比行视图 + 守门的形状/词表双缺口**，见 §十四）。§十 的 7 项拍板**已落地**（第 6 项按"不刷基线夹具"处理）；§十四 的 duplicates 实例行**是待拍板项**（体积表已量，见 14.2）。
最后更新：2026-10-05

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

## 九、模型侧 HTML 渲染 skill（**已交付**，2026-10-04 第三轮）

**设想**：模型输出时**自己渲染 HTML** 在对话框里（豆包已经可以做到）。**渲染格式照老 web 写**（`~/项目/Destiny_MCP` 的 `webui/api/destiny_render_blocks.py` —— 块构造器，另有 `webui/src/features/chat/DestinyBlocks.tsx` + `styles/app.css` 是同一套块的**样式与降级**出处）。

### 与图标覆盖的配套关系

> **没有 `icon_url`，HTML 渲染就只有文字和色块。**（就是用户截图里 Starside 那条的症状。）

**顺序：先补齐 `icon_url`（第一/二轮，已做）→ 再写渲染 skill（本轮）。**

### 交付物

| # | 要什么 | 落在哪 |
| --- | --- | --- |
| 1 | **渲染块与格式** | `skills/destiny2-render/references/blocks.md`（块骨架 + 每块字段表 + 降级） |
| 2 | **HTML 渲染的约定** | `skills/destiny2-render/references/html-conventions.md`（标签/内联样式白名单、图标尺寸与降级、转义、布局、社区相对路径怎么挂） |
| 3 | **字段表（可复跑核对）** | 同 `blocks.md` 的字段表，由 `scripts/verify_render_fields.py` 打真机逐条解析 |

### 最后一公里：宿主交付包装（2026-10-05 实测补上，第三轮漏的就是这块）

**故障**：skill 把"渲染哪些块、用哪些字段、什么 HTML 合法、图挂了怎么办"都教了，**唯独没写怎么把这个
HTML 交给宿主**。结果模型照 skill 做，用户在豆包里看到的是**一大坨 `<div style=…>` 源码** —— 前面全对，卡在最后一公里。

**实测（豆包客户端）**：同一个模型、同一套卡片数据，只换代码块的起始行 ——

| 起始行 | 结果 |
| --- | --- |
| ```` ```html type="renderer" ```` | 渲染成可视化卡片 |
| 普通的 ```` ```html ```` | 原样当代码文本显示 |
| 直接贴 HTML 字符串 | 同样不渲染 |

**结论：包装是宿主特有的**，不是"HTML 就该这么包" —— `type="renderer"` 是豆包认的标记，
不是 HTML 规范的属性；换个没确认过的宿主照抄，可能只是一个它不认识的标记。落地：

- `html-conventions.md` **§零**（新增）：分档表（豆包 = ```` ```html type="renderer" ````；其他宿主**先确认**，
  或先拿一小块试一次看是渲染还是吐源码）+ 反面（普通 ```` ```html ```` / 裸 HTML 在豆包里不渲染）。
- `SKILL.md` **§1** 的三档表："能渲染 HTML"那一档接上这条并给出豆包的值；另加一段说明它是宿主特有。
- `blocks.md` 的 **5 段 HTML 骨架示例起始行全部改对**（示例写错比不写示例更糟：模型会照抄）。
- **守门**：`tests/test_skill_contracts.py` 三条 —— 三档表接上包装协议、两个入口口径一致
  （都写"宿主特有"+ 反面）、skill 里每段 HTML 示例只允许用正确的起始行。

#### 第四轮修正（2026-10-05）：从"豆包的值 + 其他先确认"改成**换宿主的机制**

上面那份落地把**豆包那条写成了中心**，对"其他宿主"只有一句"**先确认，别猜**" —— **治不了换宿主**：
要适配不同的 agent（WorkBuddy / Codex 的格式可能各不相同，要**按环境自己切换**），可模型不知道
该**怎么**确认、确认完**怎么记**。所以包装协议**只活在 skill 里**（不加 MCP `instructions`、
不动 `install_skill.py` 的指针块），并改成机制。上面那条"其他宿主先确认"由这一轮取代：

- `html-conventions.md` **§零**拆成四段：
  - **0.1 宿主表**（宿主 / 交付包装 / 依据）：豆包 = ```` ```html type="renderer" ````（**实测** 2026-10-05）；
    **WorkBuddy / Codex / 未知宿主 = "未知 —— 先探测"**（没有实测就如实写，**不许编**）。
  - **0.2 探测**（本节重点）：发**最小一块**（一个 `<div>` 加一行字，**不带图、不带表格、不带账号数据**）
    → 看是**渲染成卡片**还是**原样吐源码** → 定下来再发正式块。**别先渲染一整张卡**（猜错就是一大坨源码）。
  - **0.3 切换规矩**：**不许把某个宿主的标记带到没验过的宿主**（`type="renderer"` 不是 HTML 规范属性）；
    **换了环境重走一遍探测**（不在这个环境验过就当作未知）；结论要能复用（同一环境覆盖后面所有块、
    跨环境**记回 0.1 那张表**）。
  - **0.4 扩展位**：新宿主怎么加进来（探测 → **加一行** → 写依据 + 日期），改一行就行、不用重写这一节。
- `SKILL.md` **§1** 的三档表接上这条机制（查表 → 没验过的先探测 → 记回表）；`blocks.md` 的 5 段骨架
  **保持豆包那一行**，但正文写清"这是**豆包环境**的写法，换宿主按 §零 重来、别带过去"。
- **守门改成断言机制**（`tests/test_skill_contracts.py`）：宿主表存在且**至少一行实测**、没验过的行
  必须如实写"未知 + 先探测"（依据里不许出现实测日期、不许把豆包的标记写成它的值）、**探测步骤**与
  **"不许跨宿主套用标记"** 必须在、**扩展位**（"加一行"）必须在。**示例允许的围栏从表里实测行现推** ——
  以后给 WorkBuddy 加一行实测，示例就能用它的围栏，守门不会撞红。旧守门断言的是"skill 里必须出现
  `type="renderer"`"，**那是把豆包的值写成唯一答案**：加 WorkBuddy 的格式反而会把守门撞红，
  守门开始阻碍扩展。

### 与老 web 的差异（都是被迫的，写清了理由）

- **样式内联**：老 web 有自己的应用与样式表（`var(--…)`、`onError` 回调）；对话宿主不一定支持外链 CSS/`<style>`/脚本。所以 class 全部展开成内联字面值，图片降级改成"`<img>` 自带底色与尺寸"（图挂了是一个深色方块，不跳版）。
- **`icon_url` 为空串**（本轮口径）→ 渲染同尺寸占位块，**不写 `<img src="">`**（老 web 的 `ItemIcon` 也是这个分支）。
- **Starside 的 `assets/…` 相对路径不进 `<img src>`**：归档在服务器的 `<DATA_PATH>/starside/`，能挂静态目录的宿主自己拼挂载点；挂不了的（豆包这类）删掉图片片段留文字 —— 服务端刻意不给 `starside.work` 热链。

### 仓库规矩（做的时候必须遵守）

- 新 skill 放 `skills/` 下 → **必须跑 `scripts/install_skill.py`**；它现在按 `SKILL_NAMES` 装多份，**新目录要加进 `EXTRA_SKILLS`**（漏了不会报错但装不进去，`tests/test_skill_install.py` 会判红）。
- 登记：`tests/test_skill_contracts.py`（渲染 skill 里每条 `tool(intent=…, 参数=…)` 都要真实存在、参考文档都要从 SKILL.md 指得到）。

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

---

## 十三、第四轮（2026-10-05）：豆包三个真实场景暴露的缺口

**触发**：用户在豆包里的三张实拍截图 —— ①社区配装列表（55 套，先显示前 5）渲染成**纯文字行**；
②单套配装详情文字为主、**没有图**；③当前装备 5 件 + 子职业，模型在回复里**自己写明**
「本次响应未带图标字段，按规范走文本行」（它遵守了 skill，是 MCP 侧没给字段）。

### 13.1 场景 3 的出口：**先排掉"跑的是旧进程"这个混淆项**

MCP 是长驻进程。**第一次复现时两个出口都没有 `icon_url`，但工作区源码里两个都有** ——
这不能直接判成"代码缺口"：当时 DSH 那个子进程 **13:48** 起、而这两个源文件 **17:36 / 18:53** 才改
（`ps -eo pid,lstart` + `stat -f %Sm`）。所以按仓库既有纪律，用一个**新起的**进程读当前字节：

- 直接调服务层（`.venv/bin/python` 起新进程）：`get_equipped_armor_mods` 五件**全带图**、
  `get_subclass` 的 `plugs[]/available[]` **全带图**；
- 再走一次**真 stdio 握手**（新进程 + `mcp` 客户端）取整包：

| 出口（复现命令） | 响应里的字段路径 | 结论 |
| --- | --- | --- |
| `inventory_assistant(intent="mods", character="hunter")` | `data.equipped_armor.characters[].items[].icon_url` | **69 个非空、0 行缺图** —— 是旧进程，不是代码缺口 |
| `subclass_assistant(intent="get", character="hunter")` | `data.subclass.plugs[].icon_url`、`plugs[].available[].icon_url` | 181 个非空，**但 `data.subclass` 自己那一行没有** |

**所以场景 3 的真缺口是一条**：`SubclassConfig`（`subclass_name` + `subclass_hash`）**没有 `icon_url`**。
子职业是一件真物品（实测 `4282591831`「棱镜猎人」→ `fab506e62fa4f188bfe2fb6d56b39614.png`），
而卡片第一行"这是哪个子职业"只能放色块。**零解析风险**：hash 现成，走既有出口函数。

复跑方式（不依赖我这次会话）：
`.venv/bin/python /tmp/survey_probe.py A_subclass_hunter`（真 stdio 握手 + 字段路径扫描）。

### 13.2 同类出口逐个核（"账号里的装备/武器身份"）

每个出口都取**真实响应**、按"有名字键或有身份 hash 键的行必须带 `icon_url`"扫一遍：

| 出口 | 结果 |
| --- | --- |
| `inventory_assistant(intent="mods")` | ✅ 已有（69 个） |
| `subclass_assistant(intent="get")` | ❌ **补**：`data.subclass.icon_url`（`models/subclass.py`、`services/subclass_service.py`） |
| `inventory_assistant(intent="item")` | ❌ **补**：`data.armor.identity.archetype.icon_url`（词条原型是真插件，实测 `2230428468` 有图）；`data.armor.instance.tuning.icon_url` |
| `inventory_assistant(intent="item")` 的 `armor.identity.set` / `set.tiers[]` | **不给**：套装是 `DestinyEquipableItemSetDefinition`，实测 `741162535` 写着 `hasIcon: false`（不是漏了，是没有这张图） |
| `loadout_assistant(intent="get")` 的 `items[]` / `weapons[]` / `class.plugs[]` | ✅ 已有 |
| `loadout_assistant(intent="get")` 的 `build_template.class` | ❌ **补**：`icon_url`（`subclass_item_hash` 就在同一行，两条构造路径都补） |
| 神器三件套（`artifact` / `artifact_mod` / `switch_artifact`） | ❌ **补**：`_parse_artifact` 的神器行、`get_artifact_mod_details` 的主行、`_artifact_instances` 的清单行（`from`/`to`/`available` 都从它取） |
| `armor_mods` 的社区注记块 / 职业金装双栏 | ❌ **补**：同一颗模组/同一颗"之灵"在别处有图、这里没有 |
| `equip_preview` 的模组行 | ❌ **补**：两处字典字面量 |

外部依赖（社区配装）见 §13.3。

### 13.3 场景 1/2：社区配装模板里**只有名字，没有 hash**

读了 `starside_builds.parse_build`（**只产出名字**：`weapons[].name`、`armor.exotic`、
`armor.set_requirements[].name`、`class.{super,aspects,fragments,…}`、`artifact.name`）
并用真实响应核过一遍。所以出图必须先做一次**名字 → Manifest 定义**的解析，
而"解析"正是这条链上唯一会出错的一步。

**红线（用户明确的价值判断，也是这一轮最硬的约束）**：
> **玩家看图比看名快 —— 很多时候是看了图标才想起名字。**

错图比没图坏得多：看图记名字的人会**记错**。所以 `services/starside_build_icons.py` 只有三条规则，
每条都对应一个**本机实测反例**（不是设想）：

| # | 规则 | 实测反例 |
| --- | --- | --- |
| 1 | 只用**精确名**解析（沿用 `starside_matching._exact_definitions`），相似度匹配一个不用 | 模板里的「贪婪之握」是**活动**名 → Manifest 精确名 **0 命中** → 不出图（正是用户截图里模型自己指出的那条） |
| 2 | 同名多版本必须**图一致**才出图 | 「重型弹药搜寻者」3 个 hash 里 **2 种图**（`2867719094` vs `644105`/`554409585`） |
| 3 | 裸标签不许直接解析，**必须带类型过滤** | 模板写「分支: 棱镜」，而 `棱镜` 这个精确名命中的是一件**武器皮肤**（`3373357626`，`itemType=19`、typeDisp=武器皮肤）—— 拿它的图当"子职业"就是一张错图。带 `item_type=16` + 「标签+职业名」（`棱镜`+`猎人`→`棱镜猎人`→`4282591831`）才落对 |

解析不到的项给**空串**（不是省略）：`icon_url: ""` 的含义是"解析不到或图不一致"，
渲染侧按 skill 画同尺寸占位块 —— 这与"缺值给空串、不编一个假地址"是同一条口径。
**套装行恒为空串**（Manifest 里就没有这张图）。

**两档形状**（用户口径："列表简单显示，问详情再全部显示"）：

| 档 | 出口 | 给的字段 |
| --- | --- | --- |
| **简版** | `data.results[].visuals`（`community` 列表） | 只有 护甲（`armor.exotic` / `armor.set`）/ 武器（`weapons[]`）/ 子职业（`subclass`）三块，每块 `{name, icon_url}` |
| **全量** | `data.selected_build.visuals` + `validation.requirements[].icon_url` | 详情里**每一行**都有图：武器 / 武器 perk / 异域护甲 / 套装 / 护甲模组 / 神器 / 神器模组 / 子职业组件；`perk_resolutions[]` 与 `required_class_item_perks[]` 也补 |

**详情那一跳不是按名字猜**：`requirements[]` 本来就带 `definitions[].item_hash`（`validate_build`
解析出来的），所以是"拿已经解析出来的 hash 取图"，仍然过"图一致"那道闸。

### 13.4 载荷实测（真机，同一账号）

`build_assistant(intent="community", query="猎人")` 命中 **62** 套（`top_n` 上限 20，
所以"55 套"那档是**翻页取全 62 行后按前 55 行算的**，不是均值外推）：

| 规模 | 基线 | + `visuals` | 合计 | 增幅 |
| --- | --- | --- | --- | --- |
| 前 5 套（默认 `top_n=5`） | 9,621 B | 2,485 B | **12,106 B** | **+25.8%** |
| 前 55 套 | 108,632 B | 31,201 B | **139,833 B** | **+28.7%** |
| 全部 62 套 | 122,616 B | 35,642 B | **158,258 B** | **+29.1%** |

每套均值 **575 B**；`visuals` 里的项 **225 有图 / 136 空串**（62% 出得了图 ——
剩下那 38% 就是规则 1–3 拦下来的"宁可不出图"）。详情整包 68,078 → **72,424 B（+6.4%）**。

### 13.5 守门：补的是**形状**盲区，不只是词表盲区

第四轮查出来的盲区有**两个**，第二个是注入验证抓出来的：

1. **词表**（原来不含 `subclass_name`/`subclass_hash`，也不含裸 `hash`）。
   裸 `hash` 当年被排除的理由是"套装层级/记录/档位表到处都是，会把台账冲成噪声" ——
   这一轮把它**实测**了一遍：收进来全仓只多 **21** 行，**没有一行**是套装层级/记录/档位表，
   全是 `{hash, name}` 形状的物品身份行。假设不成立，于是连它一起收紧
   （12 行补图、9 行进台账，台账每行写清"是什么、凭什么不给图"）。
2. **形状**（原来只扫三种：字典字面量 / 键清单常量 / 构造调用）。
   **`SubclassConfig` 是 pydantic 类体** —— 把新补的 `icon_url` 删掉后守门**仍然全绿**。
   所以补上第四种形状：**类体里同时声明了名字键与身份 hash 键字段的模型类**。
   加上之后只多出 4 行（`ArmorArchetype` / `ProcessItem` / `TuningChoice` / `PieceTuning`），
   全是求解器内部模型，逐条登记。

### 13.6 注入矩阵（6 条，全咬红；`touch` + `PYTHONDONTWRITEBYTECODE=1`，恢复后 sha256 逐字节一致）

| 注入 | 结果 |
| --- | --- |
| 场景 3：`SubclassConfig` 去掉 `icon_url` | 红：点名 `models/subclass.py`（**第一版这里是绿的 —— 形状盲区，补了判据才咬住**） |
| 裸 hash：神器行去掉 `icon_url` | 红：点名 `manifest_artifacts.py:244 _parse_artifact` |
| 台账过期：给豁免的 `_declared_children` 补上图 | 红：点名 `collection_service.py::_declared_children` |
| 红线：图不一致时挑第一张 | 红：`assert '…/icons/a.png' == ''` |
| 红线：子职业去掉 `item_type=16` | 红：**第一版绿的**（测试自己把过滤写进了调用）→ 改成走模块自己的 `_subclass_row` 才咬住 |
| 红线：套装行拿别的图顶上 | 红：**第一版绿的**（只钉了 `requirements` 那条路）→ 改成两条路都钉才咬住 |

**三条"第一次是绿的"全部是注入打偏**（判据没覆盖那个形状 / 测试没走那条路），
不是"守门没问题"—— 这正是仓库里那条纪律的现场复现。

---

## 十四、第五轮（2026-10-05）：副本对比行视图、守门的形状/词表双缺口

**触发**：用户两张新实拍截图 —— ①`weapon_assistant(intent="compare")` 的**副本行视图**
（8 把同一把枪，每把一行：现装 4 项 + 可切换项）**整包一张图都没有**；②`duplicates` 的
**实例行**没有图（组级有）。

### 14.1 副本对比行视图补 `icon_url`（已做）

| 位置 | 加了什么 |
| --- | --- |
| `services/weapon_analysis_projection.py` `compare_rows` 的卡头（`_WEAPON_ROW_KEYS`） | `item_hash` + `icon_url` |
| 同函数每一条副本行 | `item_hash` + `icon_url` |

**两个值取自同一份身份块**（`instances[].weapon`，即 `weapon_payload.weapon_block` 按**这件副本
自己的 hash** 查出来的物品道结果）—— 所以"同一 hash 出两种图"在形状上不可能：图不是在这里按 hash
重算的，而是照抄那条已经走 `manifest_lookup.get_icon_url()` 的结果（单一出处）。

**真机实测**（`weapon_assistant(intent="compare", weapon_name="M-17“快嘴”")`，5 个副本）：

| 项 | 改动前 | 改动后 |
| --- | --- | --- |
| 整包 `icon_url` 出现次数 | **0** | **6**（卡头 1 + 每行 1） |
| 行里缺图 | 5/5 | **0/5** |
| 卡头 `icon_url` | 无这个键 | `…/icons/bac8c1358e1243f387aeede8d4c7b9bb.jpg` |
| 同一 `item_hash` 出多种图 | —— | **无** |
| 载荷 | 9,008 B | **9,788 B**（+780 B，+8.7%） |
| 图能打开 | —— | `curl` **HTTP 200 + image/jpeg（4,228 B）** |

**同一轮的副产物**：`patterns` 的歧义候选行（`data.candidates[]`）投影时**把 `icon_url` 丢了** ——
同一批 `rows` 在 `_row` 里本来就有图。它是"拿真实响应逆推"抓到的第一个实例，修法是把投影搬进
`services/pattern_records.candidate_rows()`（`pattern_service.py` 贴着 465 行上限，净增 0 行）。

### 14.2 `duplicates` 的实例行：**先算体积，等拍板（这一轮没动）**

`limit` 上限实测是 **25**（`limit=50` 被拒：`duplicates limit 必须是 1 到 25 的整数`）。
两种口径都给：**闸口径** = `json.dumps(ensure_ascii=False)`（ADR-021 / 语料 20 KB 闸就是这个口径，
`run_corpus_all_rows.py:1519`）；**线上口径** = 服务端实际发的那段文本（实测 `indent=2`，约 1.9 倍）。
表里实测那列与模拟值**逐字节相等**（26,755 = 26,755），所以模拟是可信的。

| limit | 组 | 实例 | 现状（闸口径） | B 每件给图 | ΔB | C 组级+首件 | ΔC | 现状（线上） | B（线上） | ΔB（线上） |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **5（默认）** | 5 | 23 | 13,965 B | **16,380 B** | **+2,415（+17.3%）** | 14,490 B | +525 | 26,755 B | 29,446 B | +2,691 |
| 20 | 20 | 77 | 42,899 B | 50,984 B | +8,085 | 44,999 B | +2,100 | 85,627 B | 94,636 B | +9,009 |
| 25（上限） | 25 | 92 | 51,199 B | 60,859 B | +9,660 | 53,824 B | +2,625 | 102,235 B | 112,999 B | +10,764 |

- **20 KB 闸只在默认档（`limit=5`）成立**，而且它是**闸口径**；`limit=20/25` 现在就已经是闸的
  2.1×/2.6×（那两档不是默认出口）。
- 每件实例约 **105 B**（闸口径）/ **117 B**（线上口径）。
- **方案 A′（推荐，0 字节）**：不加字段 —— 同一组里每一把的图**本来就是同一张**
  （分组判据是 `exact_item_hash_and_distinct_instance_id`），渲染每把时复用组级 `icon_url` 即可。
  已写进渲染 skill 的字段表（`skills/destiny2-render/references/blocks.md`）。
- **方案 B**：每件给 `icon_url` → 默认档 13,965 → **16,380 B，仍在 20 KB 闸内**（+17.3%）；
  `limit=20/25` 时 +8.1/+9.7 KB。**没有自己改闸、没有改 ADR-021**，等用户拍板。
- **方案 C**：组级 + 首件 → +525 B（默认档）。只解决"组里第一把有图"，对"每把一行都要图"没用。

### 14.3 守门扩面：**形状**与**词表**各补一次（这一轮的重点）

用户口径是"很多场景都还没有 url，逐个补就是打地鼠"，所以先补覆盖面。**七种形状**（前四种是
前几轮的，后三种是这一轮）：

| # | 形状 | 何时加的 |
| --- | --- | --- |
| ① | 字典字面量（名字键 + 身份 hash 键） | 第一轮 |
| ② | **命名**的键清单常量 | 第一轮 |
| ③ | `Model(name=…, item_hash=…)` 构造调用 | 第一轮 |
| ④ | 模型类体里的字段声明（pydantic 少一个字段） | 第四轮 |
| ⑤ | **逐次装配**：`x.update(…)` + `x["k"] = v` | **本轮**（用户点名的盲区） |
| ⑥ | **内联**键清单：`{k: row[k] for k in ("name", …)}` | **本轮**（逆推发现的） |
| ⑦ | 只有 `item_hash` 的字典字面量（每行一把的行视图） | **本轮**（用户实拍那张图的形状） |

**词表也缺一次**（第 5 种形状的注入验证逼出来的，见 14.4）：`subclass_item_hash` 根本不在
`_HASH_KEYS` 里 —— `loadout_service` 那一行写了 `icon_url` 却没人管，**不是形状漏了，是键认不出**。
按"先量后收"补进"物品/组件身份"那一族（`subclass_item_hash`/`plug_item_hash`/`new_plug_hash`/
`enhanced_plug_hash`/`perk_hash`/`super_hash`/`grenade_hash`/`melee_hash`/`class_ability_hash`/
`movement_hash`）：全仓**只多 0 行**。同族里**故意不收**的（`name_hash`/`icon_hash`/`color_hash`
是配装外观标识，`vendor_hash`/`progression_hash`/`milestone_hash`/`metric_hash`/`node_hash`/
`stat_hash`/`record_hash`/`tier_hash`/`with_hash` 不是物品）：实测收进来多 **20** 行、20 条台账
—— 那是噪声不是覆盖。

**第 5 种形状的实现口径**（两半，判据不同，都写进 docstring）：

- `update()` 调用**按每一次调用判**：`class_data` 有两条**互斥分支**各 update 一次，
  按目标聚合的话"其中一条漏了图"会被另一条盖住 —— 注入验证会假绿，而运行期那一支真的没有图；
- `x["k"] = v` **按 (函数, 目标) 聚合**：单看一条语句永远只有一个键，不聚合一条都判不了。

**台账新增 7 条**（全部逐条核过、都不是出口）：3 条取数中间行（`weapon_compare_service` 的
`weapon_instances` 收集、`snapshot_version` 的指纹输入、`get_pvp_weapon_board` 的 `totals` 暂存）
+ 2 条"图由另一个具名工厂给"的装配（`weapon_popularity_service._weapon_identity`、`_enrich_entry`）。
**台账总数 25 → 32。**

### 14.4 注入矩阵（11 条，全部咬红；`touch` + `PYTHONDONTWRITEBYTECODE=1`，恢复后 sha256 逐字节一致）

| 注入（打在哪） | 结果 |
| --- | --- |
| ④ `SubclassConfig` 去掉 `icon_url`（上一轮，一起重跑） | 红：点名 `models/subclass.py:32` |
| 词表：神器行去掉 `icon_url`（上一轮） | 红：点名 `manifest_artifacts.py:244` |
| 台账过期：给旧的豁免行补图（上一轮） | 红：点名 `collection_service.py::_declared_children` |
| 红线三条（图不一致挑第一张 / 子职业去类型过滤 / 套装拿别的图） | 三条都红 |
| **⑤ update(kwargs)**：去掉 `class_data.update(…, icon_url=…)` 里的图 | 红：`loadout_service.py:399 _build_template()::class_data.update ['subclass_item_hash']` |
| **⑤ update(dict)**：另一条分支去掉图 | 红：`loadout_service.py:413`（同 kind） |
| **⑤ 下标装配**：副本行改成逐键赋值且不带图 | 红：`weapon_analysis_projection.py:148 compare_rows()::row ['item_hash']` |
| **⑥ 内联键清单**：卡头改成内联元组且不带图 | 红：`weapon_analysis_projection.py:165 compare_rows ['item_hash', 'name']` |
| **⑦ 只有 item_hash**：副本行去掉 `icon_url` | 红：`weapon_analysis_projection.py:147 compare_rows ['item_hash']` |

**⚠️ 第一次跑有 3 条是绿的 —— 全部是"打偏"，不是"守门没问题"**（正是仓库里那条纪律的现场复现）：

| 第一次绿的 | 真因 | 修法 |
| --- | --- | --- |
| update(kwargs) | 判据只认"名字键 + hash 键"，而那次 update 只有 `subclass_item_hash`（**且它不在词表里**） | 扩词表（14.3）+ 装配形状改判"任何身份 hash 键" |
| update(dict) | 同上 | 同上 |
| 下标装配 | 判据要求名字键在**同一组**里，而名字是更早那条 `class_data = {"name": …}` 给的 | 装配形状改判"任何身份 hash 键" |

### 14.5 `AGENTS.md` 那条 `PYTHONPATH` 规矩：**实测反例修正**（已改）

原文写"在临时树里跑之前先钉住 `PYTHONPATH=<临时树>`"。实测：**经 stdio 起 MCP 子进程时它会被丢掉**
（`mcp.client.stdio.get_default_environment()` 只转发白名单，POSIX 是 `HOME/LOGNAME/PATH/SHELL/TERM/USER`），
症状是**基线 diff 假绿** —— "改动前"的 capture 里已经带着新字段。改法：写清适用范围（直接
`python -c`/`pytest` 成立）+ 经 stdio 时必须在 harness 里显式 `env={**os.environ}` + 注明这是
**实测反例**改出来的（"规矩也会错"的一类）。

### 14.6 语料 `265 PASS / 0 SKIP` vs 基线 `264 PASS / 1 SKIP`：**定位到那一行**

翻掉的那一行是 **`build：set_bonus 真实套装名往返`**（`run_corpus_all_rows.py:1295` 的 `else` 分支
`record(..., "SKIP", "账号里没取到护甲套装名")`）。**不是代码改动，是账号数据变了**：

- 这一行的输入是现场取的：`inventory_assistant(intent="get", armor_slot="legs", limit=8)` 里
  **第一件有 `gear_tier` 的护甲**（`:315`）→ 再读 `intent="item"` 的 `identity.set.name`；
- 基线那次录到的是 `6917530195336950066`「噬星者之鳞」—— **异域**腿甲，异域没有
  `equipableItemSetHash` → `identity.set` 整个不在 → `set_name` 空 → SKIP（基线 JSON 里
  `live.armor_instance_id` / `live.armor_identity` 都留着，可复核）；
- 现在列表头一件成了 `6917530202732949212`「众神辉煌腿铠」（传说、套装 `众神辉煌`，
  两个档位 `准备充足`/`沿线推进`）—— 那件的实例 ID 比基线那件**大**（更新获得），
  而基线那件现在排在**第 2 位**，仍在账号里 → 说明是**新拿到一件**，不是代码改了顺序。
- 所以：**SKIP → PASS 是"真跑并通过"**（好事），但它反映的是账号状态，不是本轮改动。

### 14.7 本轮验证

| 项 | 结果 |
| --- | --- |
| `ruff check`（`destiny_mcp tests scripts skills`） | 全过（仓库根的 `ruff check .` 只报未跟踪的 `showreel/`，那个目录没碰） |
| 开发机全量 `pytest -q` | **2017 passed**（基线 2016 + 本轮新增 1 条 `test_ambiguous_candidates_carry_the_icon`） |
| **干净树**（`git archive HEAD`、`env -i`、干净 HOME、**无 `.env`**、`PYTHONPATH` 钉住临时树） | `import destiny_mcp.server` OK + **1999 passed / 18 skipped**（= 2017 − 18 条要本地 `manifest/*.sqlite3` 的，**没有新增跳过**） |
| 语料 runner | **266 PASS / 3 INFO / 0 FAIL / 0 SKIP**（269 行 = 基线 268 + 本轮新增的 compare 行视图那条；新那条的明细：`武器=M-17“快嘴” 把数=5 卡头=43f387aeede8d4c7b9bb.jpg 缺图=[] 同 hash 多图={}`） |
| **受控基线 diff（`env={**os.environ}` 两侧同源）** | 武器面 27 例：**`compare` +4 条路径**（`instances[].icon_url`/`instances[].item_hash`/`weapon.icon_url`/`weapon.item_hash`），**消失路径 0**；护甲面 23 例：只有 `execution_id`/`next_actions[]` 这类既有非确定性值变化，**消失路径 0** |
| 假绿对照（任务 4 的现场证据） | 同一棵树、同一条命令：**不带 `env`** 的旧脚本 → "改动前" capture 里 `icon_url` 出现 **3** 次、行里有 `icon_url`+`item_hash`（跑的是工作区新代码）；**带 `env={**os.environ}`** → `icon_url` **0** 次、行是旧键集合 |
| 渲染字段表（`scripts/verify_render_fields.py`） | **377 条解析成功 / MISSING 0**（含本轮新写进 skill 的 4 条），EMPTY 2、条件字段这次没有 4 |
| 注入矩阵 | 11 条全部咬红并逐字节恢复（在**最终提交的字节**上重跑过一遍，见 14.4） |
| 真机出口 | `compare` 行视图：`icon_url` 0 → **6** 次、9,008 → **9,788 B**、行里缺图 5/5 → **0/5**、同一 hash 无多图；图 `curl` **200 + image/jpeg（4,228 B）** |

**本轮没做的（如实记）**：`duplicates` 实例行**没有加字段**（14.2 的体积账与方案等拍板）；
`loadout_assistant(intent="list")` 的行（整包 0 个 `icon_url`）、`rotations` 的轮换行头、
`vendor` 的 `icon`（实测读的是定义里**不存在**的字段，永远空串 —— 供应商定义给的是
`largeIcon`/`originalIcon`）这三处是"逆推扫出来、但这轮没动"的候选，需要先拍板"这一行该给哪张图"。
