# 图标 URL 全覆盖 + 模型侧 HTML 渲染（开发档案）

状态：**图标覆盖已实现并验证，未提交**（HEAD `dff38c7` + 工作区 41 改 / 6 新）
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
- `icon_url(value)` —— 把 `displayProperties.icon` 的相对路径变成绝对地址；已是绝对地址则原样放过
- `is_bungie_icon(url)` —— 白名单判据（照老 web 消费侧口径）

`manifest_data.BUNGIE_BASE_URL` 改成**再导出**同一份字面量。

**12 个构造点全部收敛**（`manifest_search.py:72`、`inventory_analysis_service._cdn_url`（删除）、`manifest_query_service._absolute_icon_url`（删除）与 4 处内联、`weapon_profile:338`、`fragment_service:268`、`activity_service:686`、`pvp_weapon_service:315`、`build/models.py:634`、`loadout_service:139`）。现在**全仓只有 `utils/icons.py` 含该字面量**（守门扫）。

### 体量：先抽代码，上限只降不抬

新增 3 个小模块 + 2 处搬家：`services/inventory_lookup.py`（`locate_instance`）、`services/pattern_records.py`（`cell/merge_cell`）、`services/rotation_tables.py`（`lost_sector_block`）、`_tuning_rows` → `build_results.tuning_rows`、`crafting_sources_block` → `tools/_farming.py`。

上限随之**收紧**：`build_projection 137→120`、`inventory_service 783→762`、`pattern_service 488→465`、`rotation_service 313→300`、`_patterns_branches 215→200`。**一处理都没抬。**

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

`tests/test_icon_url_output.py`，5 条：

1. `test_no_second_icon_url_constructor` —— 谁再自己拼 Bungie 图标 URL 就红（判据：f-string / `+` 拼接里含 `bungie.net` 字面量，且绑给 `icon*` 名字 / `icon*` 字典键 / `icon*` 关键字实参）
2. `test_the_icon_source_is_the_only_place_with_the_origin_literal` —— 源站字面量只许在 `utils/icons.py`
3. `test_identity_rows_carry_icon_url` —— **三种**身份行（字典字面量 / 键清单常量 / `Model(item_hash=…, name=…)` 构造调用）必须带 `icon_url`，否则进 29 条 `_EXEMPT` 台账
4. `test_icon_exemption_ledger_is_not_stale` —— 台账条目过期也判红
5. `test_recorded_baselines_use_openable_bungie_icon_urls` —— 基线里每个非空 `icon_url` 必须是 `is_bungie_icon()` 认可的地址

### 注入矩阵（6 种全咬红，`touch` + `PYTHONDONTWRITEBYTECODE=1`，恢复后 sha256/`cmp` 逐字节通过）

| 注入 | 结果 |
| --- | --- |
| 身份行缺图（`armor_payload.socket_rows` 字典） | 红：点名 `armor_payload.py:278 socket_rows` |
| 构造调用缺图（`build_results` 的 `LoadoutItem`） | 红：点名 `build_results.py:269` ← **这条当初漏了，补上判据后才咬住** |
| 自己拼 URL 绑给 `icon_url` | 红：`loadout_service.py:705` |
| 第二处源站字面量 | 红：`['weapon_profile.py', 'utils/icons.py']` |
| 台账条目过期 | 红：点名 `build_results()→LoadoutItem` |
| **全新模块里的新出口** | 红：`_probe_new_exit.py:8 new_weapon_rows` ← **证明能抓"以后新加的漏网出口"** |

### 守门的已知边界（别当成"图标全查过了"）

- 名字/hash 键表是**封闭词表**（**不含裸 `hash`**——那会把套装层级/记录/档位表全冲进来）
- 只扫 `destiny_mcp/**`（`legacy/`、`tests/` 不在内）
- **不追运行期拼装**

---

## 七、验证

| 项 | 结果 |
| --- | --- |
| 开发机 `pytest -q` | **1995 passed**（改前 1990） |
| **干净树**（`git archive` 导出、`env -i`、干净 HOME、**无 `.env`**、`PYTHONPATH` 钉住临时树） | `import destiny_mcp.server` OK + **1977 passed / 18 skipped**（18 条要本地 `manifest/*.sqlite3`，全新克隆本来就没有） |
| 语料 runner `scripts/run_corpus_all_rows.py` | **264 PASS / 3 INFO / 1 SKIP / 0 FAIL**（4 次调用层失败全是上游：排行榜空 ×3、`stats period=season` 是文档化的接口限制） |
| **受控基线 diff** | `git worktree` 出 `dff38c7` 跑"改前"、**同账号紧接着**跑"改后"：武器面 **6** 条新路径、护甲面 **7** 条新路径，**两侧都没有"无理由消失"的字段** → 纯加法。仅易变项（`execution_id`、`god_roll.source_detail` 的集合序）变化，后者是既有的非确定性 |
| 真机 URL 形状 | 星狐座 → `.../icons/6495d6a04cc9e7c0515b27b26ad8be60.jpg`，**与用户实测逐字一致** |
| 真机出口取样 | 9 个新出口逐个 `curl`：**全部 HTTP 200 + `image/jpeg\|png`**，`打开失败: []` |

**同步了 1 条语料断言（不是为绿而改）**：`scripts/run_corpus_all_rows.py:1523` 原本钉 `"icon_url" not in dup_first`（组级图标曾被投影掉）。现在改钉"组级必须有、实例级仍然没有、perk 行仍是 `{name, slot}`"，注释里写了体积账：`duplicates` 20 KB 闸下 **13.5 → 13.965 KB**。

**基线夹具 `tests/baselines/**` 未覆盖**（它们是冻结的对照快照，相关测试本来就绿）——要不要把本轮新键刷进夹具，**待拍板**。

---

## 八、⚠️ 已知缺口：活动 / 副本的图标

**用户明确要求"活动/副本也带上"（突袭、地牢、PvP 场次），但清点表里没有这一类。**

两种可能：做了没说，或者**漏了**。

**技术上必须走另一条道**：

| 道 | 查什么 | 例子 |
| --- | --- | --- |
| **物品道** | `hash → item definition → displayProperties.icon` | 武器、护甲、模组、perk、碎片、商品 |
| **活动道** | `hash → activity definition → pgcrImage / activityIcon` | 突袭、地牢、PvP 场次 |

**共用一个出口函数、内部走两条道**——硬塞进一条的结果是"物品都对、活动全裂图"，**而且裂得很难查**。

**补的时候要单独验**：突袭 / 地牢 / PvP 各取一个出口，`curl` 一次。

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

## 十、待拍板（含建议）

| # | 事项 | 建议 |
| --- | --- | --- |
| **1** | **Starside 社区资料给不给图** | **给，但给本地归档相对路径**（`assets/<topic>/icons/<hash>.webp`），**不给第三方外链**。理由：归档里有 **3767 个图标的绝对外链**（`data/starside/index.json`），**文件本地全在**（45 MB，`missing on disk: 0`）；但那是 `starside.work` 的外链、`redistribution_license: not_established`，热链 + 归因是授权问题。给相对路径就同时满足"按 hash 绑定"和"不越界"。<br>**另**：现在抹掉它们的 `services/starside_markup.py:82-84` 的注释写"那份资源没随归档给我们"——**这句已经不成立**，无论如何该改 |
| **2** | 定义级 perk 池带不带图标 | **不带**。带 = **+13.2 KB/把**（P6 量过，三处测试钉着 `DEFINITION_ONLY_ABSENT`）。它是"**可能 roll 到什么**"、不是"你有这件"，**渲染价值低**；实例级 options **已经有图** |
| **3** | `duplicates` 的 perk 行 | **不带**（现压成 `{name, slot}`，每实例 1.64 KB、占 91%；补图会破 20 KB 闸） |
| **4** | `_popularity_summary` 的 perk 行没图，而**同一 `popularity` intent 的另一条路有** | **统一成不带**（按 #2 的口径）。**这是真不一致，不是有意为之**，已写进台账理由 |
| **5** | 其余 25 条台账 | **接受**（每条写了理由、逐条可核） |
| **6** | `tests/baselines/**` 要不要刷进本轮新键 | **待定**（夹具是冻结快照） |
| **7** | 活动 / 副本图标 | **补**（见 §八） |

---

## 十一、副作用与工位卫生

- **`data/dim_wishlists.json`**：跑真机时生成，**未跟踪且 `.gitignore` 没覆盖该路径**（现有规则只覆盖 `destiny_mcp/data/dim_wishlists.json`）。它是 DIM 愿单缓存的正常产物 → **建议 `.gitignore` 补一行**。
- 工作区：41 改 + 6 新，**未提交**；`skills/**` 与未跟踪的 `showreel/` 未碰。
- 本轮操作中出现过两次失误（一个 shell 注入函数参数写错，在仓库根留下 4 个 0 字节怪名文件；同一次导致注入未自动恢复），**均已在核对绝对路径后复原并逐字节核对**。

---

## 十二、这一轮的通用教训（与 §六 守门同样重要）

**给调用方看的字段，会在某条出口上悄悄没了——而且守门抓不到"没被扫到的出口"。**

所以 §六 的守门里最值钱的不是前四条，而是**第六条注入**：它在**一个全新模块里新加一个出口**，照样被抓住。**防未来比抓现在重要。**

同族的教训（本仓已有记录）：`ls-tree` 证明"文件在提交里"、不证明"树能跑"；`.venv` 的 editable 映射会让"临时树验证"作弊；`ruff` 抓不到"从一个模块导入一个它根本没有的名字"。
