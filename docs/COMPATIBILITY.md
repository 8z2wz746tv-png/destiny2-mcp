# 兼容面规矩（别名与历史工具面）

这个文件回答一个问题：**已经存在但"看起来多余"的入口，哪些必须留、哪些可以删、删之前要先做什么。**
写法是「实测 + 决定」，不是「感觉」：下面每一组别名都在本机真账号上跑过——同一组参数下
两两返回**逐字节相同**的 `data`（脚本见 `scripts/run_corpus_all_rows.py` 的同类做法，
`tests/test_intent_aliases.py` 负责防止以后偷偷跑偏）。

## 三类，三种待遇

| 类别 | 例子 | 待遇 |
| --- | --- | --- |
| **永久别名**（中文说法） | `概况` = `summary`、`重复武器` = `duplicates`、`角色`/`档案` = `profile` | **不许删**。中文用户会这么说，删了等于砍功能；它们必须永远和 canonical 走同一段代码 |
| **待删别名**（英文近义） | `search_catalog`/`all_weapons`/`global`/`search_all` = `catalog`；`selection_rates`/`perk_selection`/`selection`/`usage_rates` = `popularity` | **保留到 0.2.0**。现在只登记不宣传；`skills/destiny2-mcp/references/routing.md` 只写 canonical。删之前先看一圈真实调用日志 |
| **历史工具面**（67 个旧工具，**已剥离**，见 ADR-008） | `get_inventory`、`search_items`、`import_build_from_*` … | 2026-09-20 整块移到仓库根目录 `legacy/`：不进包、不参与测试与 lint，只作查阅（见 `legacy/README.md`）。原来的口径是「默认屏蔽、不保证契约、不单独修 bug」——这个口径下必然腐烂（复核时已经有两个工具在裸抛 `KeyError` / 把有数据说成「未找到」），而 62/67 在 8 个聚合工具里都有对应。没有对应的三个：`raw_api_call`、`get_item_definition`（按设计不再提供）与**配装导入**（整个功能已决定不要，README 与技能文档里的宣传同步删掉） |

## 未发布：武器类型列表改列表行、默认 10 件、可翻页（破坏性，见 ADR-007）

`weapon_assistant(intent="type")`（"我手炮都有哪些"）以前每件发**完整模板**
（`{weapon, sockets, options, stats, perks_complete, notes}`），20 件就是 **18.9 万字符**，
其中 **2/3** 是插槽池与可换项 —— 而列表只是"有哪些"。现在改成既定的**列表行**
（`services/weapon_payload.py` 的 `LIST_ROW_KEYS`，与 `catalog` 的 `matched[]` 同一套写法）：

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 行形状 | `{weapon, sockets, options, stats, perks_complete, notes}` | **摊平**的身份字段 + 副本字段（`instance_id`/`location`/`power`/`is_equipped`/`locked`/`tracked`）+ `stats` + `notes` |
| 插槽池与可换项 | 每件都给（定义级 `sockets` + 实例级 `options`） | **不给**：要看某一件的部件用 `compare(weapon_name, item_instance_id)`（一次调用拿全） |
| 每件还会读的组件 | 305（已装 plug）+ 310（能换什么） | **不读**（profile 10.16 MB → 1.86 MB，实测 3.79s → 0.63s） |
| 逐件的本地资料 | `farming`/`popularity`/`community`/`sources` 四块（精简版） | **不带**：整张列表的清单在顶层 `farming_list`，单把的结论用 `info`/`analyze` |
| 默认条数 | 20 件 | **10 件**（响应里 `total`/`returned`/`truncated` 照旧） |
| 翻页 | 无（只能靠调大 `limit`） | `offset` + 响应里的 `next_offset`；最后一页 `truncated=false` |
| `perks_complete` | 有 | 不给了（它说的是"插槽解析完整性"，而列表行没有插槽） |

真机实测（2026-09-20，同一账号）：`type 手炮` 默认调用 **387,598 → 41,537 字符**（9.3×），
热调用 0.78s → 0.30s。**信息没丢**：插件明细在 `compare` 里一件不少，`stats` 每行照给，
清单评级在 `farming_list.results[]` 里按名字对。

## 未发布：`stats` 的口径与行形状（破坏性，见 ADR-005）

`activity_assistant(intent="stats"/"career"/"historical_stats")` 的三个别名仍走同一段分派，
但**默认口径与行形状变了**，旧键一个不留：

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 默认范围 | 第一个角色（`GetHistoricalStats`） | **账号级**（`GetHistoricalStatsForAccount`），`scope="account"` |
| 行里的数字 | `value` + `display`（单角色） | 账号级行给 `existing` / `deleted` / `account_total` 三档（**没有 `value`**）；单角色行照旧给 `value`，但要显式传 `character=` |
| 口径标签 | 无 | `source` / `scope` / `mode`（null 或块）/ `period` / `aggregation` / `tiers` |
| 合并语义 | 无 | 每行 `aggregate ∈ {sum, max, min, derived, none}`（比值类不许相加） |
| `mode=` / `period=` | 传给 `stats` 会被 `ignored_parameter` 拒绝 | `stats` 认它们：`mode` 用对照表词表（crucible/trials/iron_banner/competitive/gambit/raid 或官方中文标签），`period` 只认 `career`；`season`/`act` 上游没有 → `a_p_i_error` + `unavailable` 并指向 `counters` |
| `weapon_history` 载荷 | 无范围声明 | 必带 `scope="all_modes"`（全模式 PvE+PvP，**不是 PvP 榜**） |

调用方要改的只有一件事：**想要单角色数字就显式传 `character=`**；想要账号生涯直接用默认即可。
别名表不变（`career`/`historical_stats` 仍是 `stats` 的永久别名）。

## 未发布：活动模式词表合一（`mode=` 的词与数值，破坏性）

`history` / `stats` / `counters` 三处的模式词以前来自**两张各自手写的表**，其中若干取值实测是错的。
现在只有一处出处：`destiny_mcp/data/activity_modes.py`（数值取自 Manifest 的 `modeType`，
中文名运行时从 zh Manifest 取）。**改的是词表本身，不是别名待遇**：

| 词 | 以前 | 现在 |
| --- | --- | --- |
| `allpvp` | 9（`modes=9` 直接 HTTP 500） | **5**（熔炉竞技场伞形，覆盖铁旗/试炼/竞技/占领/死斗…） |
| `猛攻` / `onslaught` | 69 = 多人竞技PvP（**错答**：返回一堆竞技场次） | **不认识**，报 `config_error` 并列出可用词 |
| `大师日落` / `grandmaster` | 46（计分日落） | **47**（计分巅峰日落） |
| `stats` 认的模式词 | 只有 6 个 PvP/突袭词 | 全部模式词（story/strike/raid/crucible/patrol/allpve/iron_banner/nightfall/grandmaster/gambit/competitive/dungeon/trials/lostsector） |
| 历史列表的 `mode_name` | 手写 8 条表，43/44/73/89/91 显示"模式43" | Manifest 官方中文名（"铁旗占领模式"） |
| `counters` 的 `mode_label` 与 `labels.modes` | 手写标签表 | 同上，Manifest 官方名 |

智谋（`gambit`，63）的 `activityModeCategory=3`（**PvPvE**）：它不属于"纯 PvP"，
筛 PvP 时不会被带上（真机实测 `mode=5` 拉到的场次里没有智谋）。

## 未发布：`count` / `limit` / `maxtop` 传 0 或负数的行为

以前只有文档承诺"传 0 或负数等于没指定"，代码只判 `None`，于是 0 会被服务层的
`max(1, …)` 变成 **1**（要 1 条/1 场）。现在 0 与负数**确实**等同"没指定"，按该入口的默认值走：

| 入口 | 传 `0` 以前 | 传 `0` 现在 |
| --- | --- | --- |
| `activity_assistant` 的 `count`（history/counters/community…） | 1 条 | 该 intent 的默认条数（20） |
| `activity_assistant(intent="pvp_weapons")` 的 `count` | 1 场 | 10 场（本 intent 默认） |
| `activity_assistant` 的 `maxtop`（排行榜） | 0（上游收到 0） | 10 |
| 各工具的 `limit` | 部分入口 1 条、部分入口走默认 | 一律走该入口默认 |
| `top_n` / `slot_number` / `max_replacements` | schema 直接拒收（`ge=1`） | 不变（仍然拒收） |

## 0.3.0 的新能力与删除的行为

新能力**不是别名**，登记在这里是为了让"以前做不到、现在能做了"有据可查：

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 换子职业元素 | `subclass_assistant(intent="modify", changes={"subclass": …})` | 新的变更键（旧键一个没动）；元素别名进 `vocabulary.ELEMENT_ALIASES` + 职业尾缀表 |
| 换神器 | `subclass_assistant(intent="equip_artifact")` | 新 intent（写入，走确认信封）；名字精确匹配，不模糊 |
| 纯 PvP 武器榜 | `activity_assistant(intent="pvp_weapons")` | 新 intent（`scope="pvp_recent"`、`source="pgcr_aggregation"`，逐场 PGCR 聚合最近 N 场）；**不是** `weapon_history` 的别名，两者口径不同、并存。**只认 PvP 家族 + 智谋**：`mode="raid"` 这类 PvE 词报 `invalid_argument_error`（要全模式武器击杀用 `weapon_history`） |
| 角色身上的神器 | `subclass_assistant(intent="artifact", character=…)` | `artifact` 原来的返回一个键没少，多附 `character_artifact` |

**删除的行为（不留兼容分支）**：`equip_loadout` 遇到"保存的子职业与当前不一致"以前直接失败并返回
「当前子职业与保存/确认的子职业不一致。」；0.3.0 起改成**先换上再配**（用户 2026-09-15 拍板），
那句话术不再出现。旧行为没有开关、没有回退路径。

## 别名总表（实测等价）

| 工具 | canonical | 别名 |
| --- | --- | --- |
| `player_assistant` | `profile` | `get_profile`、`角色`、`档案` |
| | `search` | `search_player` |
| | `find` | `find_players`、`fuzzy` |
| `inventory_assistant` | `summary` | `summarize`、`概况` |
| | `duplicates` | `duplicate_weapons`、`find_duplicates`、`重复武器` |
| | `get` | `inventory`、`list` |
| | `search` | `find_item` |
| | `type` | `search_type` |
| | `equip_many` | `equip_items` |
| | `track_quest` | `quest_tracking` |
| `weapon_assistant` | `catalog` | `search_catalog`、`all_weapons`、`global`、`search_all` |
| | `compare` | `compare_duplicates` |
| | `perk_pool` | `perks` |
| | `popularity` | `selection_rates`、`perk_selection`、`selection`、`usage_rates` |
| `loadout_assistant` | `list` | `get` |
| `subclass_assistant` | `get` | `subclass` |
| `activity_assistant` | `stats` | `career`、`historical_stats` |
| | `weapon_history` | `weapons`、`weapon_usage`、`weapon_leaderboard` |
| | `aggregate` | `activity_aggregate`、`activity_stats` |
| | `leaderboards` | `leaderboard` |
| `build_assistant` | `community` | `starside` |

## 三个"看着像别名、其实不是"的坑

- **`move` ≠ `transfer`**：都在 intent 列表里，但参数名不同——`move` 读 `destination`（vault 或角色名，可配 `equip=true`），`transfer` 读 `to_character`（按实例转移）。拿 `destination` 调 `transfer` 会得到 `invalid_arguments`。
- **`community_build` ≠ `community`**：只在**没给** `community_build_id` 时两者返回相同（都走搜索）；给了 id 之后 `community_build` 会读整套模板并核对库存，这是另一件事。
- **`equip` / `equip_mod` / `item` / `lock` / `pull_postmaster`** 各自是独立行为，不是任何东西的别名。

独立行为（不是任何东西的别名）也逐条登记在 `tests/test_intent_aliases.py` 的 `STANDALONE` 里：
`pgcr`、`filter_rolls`、`save`、`modify`、`vendor`、`weekly` 这类各有各的分派，新增 intent 时
要么进别名表、要么进 `STANDALONE`，两个都不进测试就红。

## 加新入口的规矩

1. **别名不许有自己的分支代码**：必须落进 canonical 的那段 handler；`tests/test_intent_aliases.py` 会检查每个别名组在同一处分派里。
2. **新别名要登记在本文件**，并写清它是"永久（中文说法）"还是"待删（英文近义）"。
3. **canonical 只写在 `routing.md` 与工具说明里**；别名出现在 schema 的 enum 里就够了，不要在文档里教用户用别名。
4. **删别名前**：先把它从 `_requests.py` 的 Literal 里去掉、跑一遍 `pytest` 与
   `scripts/run_corpus_all_rows.py --group sweep`（110 个取值逐个真机跑），确认没有测试或语料还在用它。
