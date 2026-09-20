# 全面语料（八工具面 · 逐 intent 与字段级）

这是 `docs/testing/TESTING_CORPUS.md` 的**补充**，不替代它：主语料写「该看到什么」，这份把其中
「发布前把所有 intent 跑一遍」那条变成**可执行的全量回归**，并补上其余六个工具面的字段级断言。
武器与护甲章节的字段级断言仍由 `run_corpus_weapon_rows.py` / `run_corpus_armor_rows.py` 管，这里不重复。

## 怎么跑

```bash
.venv/bin/python scripts/run_corpus_all_rows.py --report /tmp/corpus_all.json   # 三组全跑
.venv/bin/python scripts/run_corpus_all_rows.py --group sweep                    # 只跑全 intent 体检
.venv/bin/python scripts/run_corpus_all_rows.py --group rows                     # 只跑字段级
.venv/bin/python scripts/run_corpus_all_rows.py --skip-slow                      # 跳过求解类（几十秒级）
.venv/bin/python scripts/run_corpus_all_rows.py --only 商人                      # 汇总/报告只看含关键词的行
```

退出码 0 = 没有 FAIL（WARN/INFO/SKIP 不算失败）；1 = 有 FAIL。
**跑之前**先 `pytest -q` 与 `scripts/verify_mcp.py`，并且新开任务/重启宿主，别用旧连接。

**不做任何写入**：写入类 intent 一律 `confirmed=false`，并且会断言响应里没有 `written=true` 这类落盘标记。
要真跑写入仍按主语料第十五章的手动流程走。

## 三组各管什么

| 组 | 覆盖 | 量级 | 什么时候跑 |
| --- | --- | --- | --- |
| `sweep` | 八个工具面**全部 intent**（含中文别名），给最简但有意义的参数，只验「是不是干净信封」 | 110 个 intent | 发布前；改了 intent 分派/参数守卫后 |
| `rows` | 其余六个工具面的字段级契约 + 跨切面（默认条数、翻页、中英同义、错误信封） | 约 70 行 | 改了任一工具面后 |
| `mcp` | 真 stdio 握手：工具数、intent 枚举覆盖、schema 层拒收、旧工具面确已剥离 | 9 行 | 改了工具签名 / schema / 工具面后 |

`sweep` 的失败线（任一命中即 FAIL）：

1. 抛异常（打印异常类型与原文）；
2. 超时（默认单次 90s，慢 intent 420s，可调）；
3. `ok` 不是布尔，或 `ok=false` 但 `error.code` 为空；
4. `ok=true` 但 `data` 为 null 或没有 `summary`；
5. `warnings` 不是 list；
6. 响应里出现 `Traceback (most recent call last)` 原文；
7. 写入类 intent 出现 `written=true` 之类的落盘标记（这一条是账号安全线）。

`EXPECT_CODE` 里登记的两条（榜单的上游故障）失配只报 **WARN**：那记的是上游现状，
变了要人看一眼，不是我们的回归失败。

## rows：逐工具面断言

### player_assistant

| 行 | 断言 |
| --- | --- |
| profile 结构 | 有 `display_name`/`membership_id`/`membership_type` 与角色列表，每个角色带 `light`；只读 |
| search 精确名 | 返回的 `membership_id` 与 profile 里那个一致（不能搜出别人） |
| find 前缀 | 候选带 `confidence`/`playtime_hours`/`last_played`/`membership_id`，`has_more` 在；超过 20s 报 WARN（实测约 23s，见「已知问题」） |
| find 空结果 | 要么干净失败码，要么空列表 + warning；**不许**把空候选说成「没这个人」 |

### inventory_assistant

| 行 | 断言 |
| --- | --- |
| summary | `data.inventory` 区分角色背包与仓库，只给数量概况 |
| summary + `item_type="手炮"` | `config_error`（按具体类型看要用 `intent="type"` + `type_name`） |
| get 翻页四件套 | `total_items`/`returned_items`/`truncated`/`next_offset` 都在，`returned_items == limit` |
| offset 翻页 | 两页实例 ID 不重叠、不跳号 |
| offset 超界 | 空页 + `truncated=false`，不是报错也不是倒出全量 |
| type | `total`/`returned`/`truncated`/`weapon_count` 都在，行内**不带** `sockets`（要看 perk 走 `weapon_assistant`） |
| duplicates | 每组内 `item_hash` 唯一（同名不同版本不许混），带 `pagination`/`scan` |
| search 命中 | 给 `item_instance_id` 与 `location` |
| search 未命中 | 说「没找到」，不反推「全账号没有」 |
| item 缺实例 ID | `invalid_arguments`，不是裸抛 |
| item 传 null | 干净信封（`AttributeError` 算 FAIL） |

### weapon_assistant（0.1.11 / 0.1.12 新增口径）

| 行 | 断言 |
| --- | --- |
| `↑` 与 `name_plain` | 逐副本：带 `↑` 的已装 perk 同时给 `name_plain`；本账号当前没有已选中的强化 perk 时记 INFO（不假失败） |
| `filter_rolls` 认规范名 | 用不带箭头的名字能筛到带 `↑` 的副本 |

### weapon_assistant（`intent="patterns"`：锻造图样）

| 行 | 断言 |
| --- | --- |
| 计数自洽 | `counts` 四档相加 == `total` == 183，且 `catalog_total` == 183（图鉴「模式和催化」的条数） |
| 未开始 ≠ 0 | `status="未开始"` 的行 `progress` 必须是 `null`（账号里没有这条记录，不是 0/5） |
| 来源带出处 | `sources.available=true`、`page.updated_at` 有值、`page.trust="untrusted_reference"` |
| 变体指回基础版 | 问「惩戒措施（失时）」返回基础版「惩戒措施」的图样，摘要里写明"变体" |
| 名字对不上 | 摘要说清"图鉴共 183 条"，`patterns.items` 为空，不编"没有来源" |
| 别名等价 | `patterns`/`pattern`/`craft`/`锻造`/`图样`/`图样进度` 同参返回逐字节相同的 `data`（`aliases` 组） |
| 默认条数 | 不传 `limit` 返回 20 条（`cross` 组的默认条数体检） |

### build_assistant

| 行 | 断言 |
| --- | --- |
| community 搜索 | `archive_available=true`、`network_fallback_used=false`，每条有 `build_id` 且 `executable=false` |
| community_build | summary 含「**不可直接执行**」，warning 里 `execution_supported=false`，`data` **不再带** `results`/`next_offset` |
| 三层 roll | 带 `required_perks` 的行的 `owned_instances[]` 必须有 `selectable_plug_status`；`not_read` 必须给 `alternate_perk_options_reason`；`available` 必须同时给 `perks_current_match`/`perks_available_to_switch`/`perks_unavailable` |
| armor_mods（属性词） | `match.kind="stat"` + 模组列表 |
| armor_mods（蒙中词，如「速度」） | `match.kind="keyword"` + warning 说明这些模组不加该属性 |
| armor_mods（乱填） | `invalid_argument_error` 并列词表 |
| set_bonus（真套装名） | 用账号里真实 T5 护甲的 `identity.set.name` 往返，给 2/4 件两档 |
| set_bonus（不存在） | `definition_not_found_error` |
| exotic_armor 形状 | 必须与 `intent="item"` 用同一套 `name_en`/`slot_display`/`rarity_tier`/`gear_tier` snake_case 身份块 |
| equip_build（手拼候选） | 被拒且不落盘 |
| recommend（小目标） | 候选给齐五个部位 + 实例 ID |
| analyze（超规模） | 立刻返回 `precision ∈ {exact, not_computed}`，不是干等超时 |
| farm_target | 只反推待刷件，不当成已拥有 |

### loadout_assistant

| 行 | 断言 |
| --- | --- |
| list 默认 | 最多 5 套 + 四个分页字段 + 每套带 `build_template` |
| list 翻页 | `offset` 两页不重叠 |
| get 传 `loadout_id` | `ignored_parameter`（要 Agent 自己从全部里挑） |
| search_identifiers | 按 `kind` 给标识（含 hash） |
| search_identifiers 乱填 | `invalid_argument_error` 列词表 |
| save 缺名字 | `invalid_arguments` |
| save 不给确认 | `confirmation_required`，账号未动 |
| update_official_identifiers 空 | `invalid_arguments` |

### subclass_assistant

| 行 | 断言 |
| --- | --- |
| get | 给当前子职业 + 各插槽当前项与可换项（`socket_type`/`is_active`/`available`） |
| options 缺 `character` | `subclass_error` 并列出合法职业（`hunter/warlock/titan`） |
| 报错文案 | 不许出现「。。」这种重复标点 |
| options 完整 | 给该元素该部件的全部可选项 + `count` |
| fragments 元素写法 | `void/虚空`、`strand/缚丝/编织` 五种写法条数一致 |
| fragment_details 不存在 | `definition_not_found_error`，不编效果 |
| artifact | 能回答「我现在用哪个神器」：`current_artifact.name` + `tiers[].mods` |
| artifact 带 `character` | 多出 `character_artifact`：`equipped` 是**身上那件**、`available` 是背包里能换的（实采：三角色分别装 s26/s21/s25，与目录里的 s27 不同） |
| artifact_mod | 用 artifact 给的正数 hash 能查到详情 |
| equip_artifact_mod | 不给确认 → `confirmation_required` |
| equip_artifact | 不给名字 → `missing_artifact_name`；名字不在这个角色身上 → `invalid_argument_error` 并列出他身上有几件；不给确认 → `confirmation_required` |
| modify 换子职业 | `changes={"subclass":"烈日"}`（或 `火术`／官方名 `破晓`）→ 真机换上另一件子职业物品后回读一致；已是目标则不动；猎人说「火术」→ 报错不硬切 |
| equip_loadout 子职业不一致 | 先换上保存的子职业再配槽（0.3.0 起；以前直接失败） |
| community | 走本地技能资料 |

### inventory_assistant 的装备编排（0.4.0）

| 行 | 断言 |
| --- | --- |
| equip 不带确认 | `confirmation_required`，`candidates[0].steps` 里是「先顶下、再装」两步，**零写入** |
| equip 带确认 | `ok:true` + `verified:true` + `equipped_now` 说清受影响部位现在装着什么 |
| equip 撞金装冲突 | `equip_blocked`，消息说清全身只能一件异域 + 下一步（不是 `equip_failed`） |
| equip 已在身上 | `ok:true`，无事可做、不写 |


### activity_assistant

| 行 | 断言 |
| --- | --- |
| history | 按 `count` 给记录，每条有 `instance_id`/`start_time`/`mode` |
| history 传 `maxtop` | `ignored_parameter` 并指向 `count` |
| pgcr 真 ID | 给结算内容，不是空壳 |
| pgcr 缺 ID / 非数字 | `invalid_argument_error`（不是裸抛 404） |
| pgcr 数字但不存在 | `upstream_not_found_error` 信封 |
| career / historical_stats | 账号级三档（`scope=account`、`source=GetHistoricalStatsForAccount`）；pvp 熔炉生涯击败 `existing`/`deleted`/`account_total` = 50,622 / 28,242 / **78,864**，且 `account_total == existing + deleted`（已删角色不许算两遍） |
| career 与游戏内数字核对 | **生涯数字与游戏内一致**：`data.game_counters` 里 `811894228` 的 `progress = 124,495`（`source=profile.metrics`），warnings 里写出与统计接口账号级（78,864）的差 **45,631** 及"拆不出来"的原因 |
| stats 传 `character=hunter` | `scope=character` + 角色名，行里**没有** `account_total`（单角色不许冒充生涯） |
| stats 传 `mode=trials` / `period=season` | 试炼按模式给得出（`aggregation=computed`、`upstream_modes=84`、`upstream_group=trials_of_osiris`，K/D 有值）；`period=season` → `a_p_i_error` + "unavailable"（上游没有赛季周期，不降级成生涯） |
| weapon_history | 给常用武器与击杀数；必带 `scope=all_modes`（**不是 PvP 榜**，话术直说） |
| pvp_weapons | **纯 PvP 武器榜**（最近 N 场逐场 PGCR 聚合）：必带 `scope=pvp_recent`、`source=pgcr_aggregation`、`window{newest,oldest,matches_requested,matches_planned,matches_analyzed,matches_failed,matches_without_your_row,history_page_size_per_character}`（`matches_requested` 是调用方要的原值，`matches_planned` 是钳制后实际用的）、`mode_tally`（子模式 + Manifest 官方中文名）、`characters`（含 `page_full`）、`failed_matches{total,returned,truncated,items}`（失败详情的全量自证）；武器行给 `kills`/`precision_kills`/`precision_rate`/`matches_with_kills`/`kill_share`/`kills_per_match`。`mode=gambit` 时 `mode_group.is_pvp_only=false`（智谋是 PvPvE）。真机默认 10 场冷启约 35 秒、命中缓存约 14 秒（其中大半是进程启动 + Manifest 加载）|
| aggregate | 给账号累计排行；`mode` **不是**它读的参数（按模式问它会拿到 `ignored_parameter`） |
| clan_leaderboards | 缺 `group_id` → `invalid_argument_error`；伪 ID → `upstream_not_found_error` |
| leaderboards | 记现状（上游故障），INFO 不判失败；不许编排名 |
| community | 走本地资料 |

### world_assistant

| 行 | 断言 |
| --- | --- |
| weekly | 给 `reset_time` + 分类计数 + 重点活动 |
| weekly_full 传 `limit` | `ignored_parameter`（它不读 limit） |
| vendor 菜单 | `mode="menu"`、`total_vendors` 有值、`question`/`next_actions` 都在、`vendors[].sale_items` 全空 |
| vendor 详情 | `mode="detail"`、有 `rank.name`/等级、分类 `kind ∈ {rewards,sale,submenu}`、商品数受 `limit` 约束 |
| vendor hash 往返 | 菜单首条的 hash 直查回来是同一条 |
| vendor 编名字 | 空菜单 + 下一步，**不是**错误信封 |
| search_collectible_nodes | 给 `node_hash`/`name`（节点号 ≠ 收藏品号） |
| collectible_node（真节点号） | 给已获得/未获得计数与条目 |
| collectible_node（收藏品号） | `invalid_argument_error`，消息说清两者区别 |
| collectible_item 的 hash | `item_hash` 与其它面统一为**非负**（无符号） |
| community | 跨分类搜本地资料 |

### 跨切面（cross）

| 行 | 断言 |
| --- | --- |
| 默认条数 | 背包清单 100、按类型列武器 20、武器目录 50、重复武器 10、配装 5、商人菜单 15、碎片 10、活动 20——逐个真跑，实测条数 ≤ 上限 |
| rarity 中英 | `异域` 与 `exotic` 结果数量一致 |
| character 中英 | `猎人` 与 `hunter` 读到同一套配置 |
| weapon_type 中英 | 中文分类名必须可靠；英文名只记录现状（INFO） |
| 失败信封 | 抽查四类失败：`ok=false` + `error.code` + `error.message`，`warnings` 是 list |
| 体积与耗时 | 汇总打印最慢 8 次调用与最大 8 个响应（用来盯"悄悄变胖/变慢"） |

### MCP 协议层（mcp）

| 行 | 断言 |
| --- | --- |
| 工具面 | `tools/list` 恰好 8 个聚合工具，名字一致 |
| intent 枚举 | 每个工具的 schema `intent.enum` 覆盖 `_requests.py` 里 Literal 的全部取值（含中文别名） |
| 参数拦截 | 传给不读它的 intent → `ignored_parameter` |
| 未声明参数 | `extra_forbidden`（协议级 `isError`） |
| 乱填 intent | `literal_error`（协议级） |
| `item_instance_id=null` | 走 schema 拒绝或干净信封，**不许**裸抛 `AttributeError` |
| 槽位越界 21 | `less_than_equal`（协议级） |
| 真握手读账号 | `player_assistant(intent="profile")` 得到 `ok=true` |
| 旧工具面已剥离 | 连旧的 `DESTINY_MCP_TOOL_PROFILE=full` + `ENABLE_LEGACY_TOOLS=1` 一起塞进去，也只该有 8 个工具、且没有 `get_inventory` |

## `sweep` 实测基线（119 个 intent，最简参）

最近一次全跑（2026-09-20，0.5.0 + 锻造图样）：**119 条 PASS + 1 条汇总 INFO，0 FAIL / 0 WARN**。
92 个 intent 用最简参就返回 `ok=true`；27 个返回**干净的错误码**（这本身就是对的：缺参/越界要在工具层
或服务层说清，不许裸抛）：

| 返回码 | 个数 | 哪些 intent（最简参下） |
| --- | --- | --- |
| `confirmation_required` | 6 | `inventory:move`、`loadout:{delete,equip_loadout}`、`subclass:{modify,equip_artifact_mod,equip_artifact}` |
| `invalid_arguments`（工具层） | 12 | `inventory:{transfer,equip,equip_many,equip_items,pull_postmaster,lock,track_quest,quest_tracking}`、`loadout:{save,snapshot_official,update_official_identifiers,clear_official}` |
| `invalid_argument_error`（服务层） | 2 | `inventory:item`（空实例 ID）、`inventory:equip_mod`（缺 `item_instance_id`，要先指定哪一件） |
| `invalid_canonical_build` | 1 | `build:equip_build`（手拼候选会被拒） |
| `ignored_parameter` | 3 | `activity:{aggregate,activity_aggregate,activity_stats}`（`mode` 不是它们读的参数） |
| `a_p_i_error` | 2 | `activity:{leaderboards,leaderboard}`（上游故障，见已知问题） |
| `upstream_not_found_error` | 1 | `activity:clan_leaderboards`（伪 group_id） |

（`missing_artifact_mod_hash` / `missing_artifact_name` 两条在旧基线里出现过：那时 `sweep` 给
`artifact_mod` / `equip_artifact` 传的是 0 / 空名字。现在这两个 intent 会带上真机取到的
神器名与模组 hash，于是走到写入确认那一步，返回 `confirmation_required`。）

三条容易踩的：

- **`transfer` 与 `move` 虽然在同一份 intent 列表里，参数名不一样**：`move` 读 `destination`（vault 或角色名，可配 `equip=true`），`transfer` 读 `to_character`（按实例转移）。拿 `destination` 调 `transfer` 会得到 `invalid_arguments`（消息会说清缺 `to_character`）。
- **`missing_artifact_mod_hash`** 这个码只在这里出现（`artifact_mod_hash` 传 0 或缺省），主语料第十二章 C 的码表里没有它，按本表补。
- **`equip_mod` 的码是服务层的 `invalid_argument_error`**（缺 `item_instance_id` 时）而不是工具层的 `invalid_arguments`：它需要先知道改哪一件，这条按「服务层实体参数」归类。

## 信封统一（第二批）：`data` 里不再有第二个状态信封

之前有些读取意图把 `{success, message}` 又塞在 `data` 里（顶层信封已经有 `ok`/`summary`），
键名也混着 camelCase。0.1.14 起：

- **`data` 与其子块里不再有 `success`/`message`**（写入类的领域结果 `data.result.*` 例外——
  那是"这次操作的结果"，不是状态信封）；
- **我们自己的键一律 snake_case**：`fragments[].name_en`、`options[].name_en`、
  金装候选行的 `name_en`（原 `nameEn`）；
- 清掉的位置：`artifact`/`artifact_mod`、`activity` 的 `weapon_history`/`aggregate`/`leaderboards`/
  `clan_leaderboards`、`collectible_item`/`collectible_node`/`search_collectible_nodes`、
  `loadout` 的 `search_identifiers`。

**守卫**：`sweep` 组现在对每个 `ok=true` 的响应检查这两条（110 个 intent 全都过一遍），
违规会直接 FAIL 并打印路径（`data.message`、`data.artifact.success`…）。

**待办（已知例外）**：活动统计里的键沿用 Bungie 自己的 `statId`
（`activitiesEntered`/`killsDeathsRatio`…），武器历史的逐项 `values` 同样——
它们是上游标识符，不在"我们的键"范围里；要统一得单独排期（改成 `{key, name, value}` 行式）。
规则里按路径放行（`values`/`pve`/`pvp` 块），别的 camelCase 一概不允许。

## 性能诊断：`weapon_assistant(intent="catalog")` 为什么这么慢

结论：**慢在"给每把武器的每个插槽选项都取了 Perk 描述"**，而不是 Manifest 本身慢。
方法：`cProfile` 跑一次 `catalog + perk_name=萤火虫`（本机负载高，绝对秒数偏大，但占比是负载无关的）。

| 观测 | 数值 |
| --- | --- |
| 总耗时 | 163.7s（其中 `filter_catalog` 158.7s） |
| `_catalog_perk_details` 调用 | **2,208 次**（= 候选武器数，每把一次） |
| `_plug_option`（选项展开） | **1,901,007 次**（约 860 个选项/把） |
| Manifest `_query_json` | 1,910,011 次 → 92.5s |
| `sqlite3.execute` | **7,620,483 次 → 77.3s** |
| 其中 `get_sandbox_perk_description` | 1,901,007 次 → 89.3s（**最大头**） |
| 实际返回 | 50 行（`matched_count=100`） |

根因在 `services/weapon_roll_filter_service._catalog_perk_details`：为了让"命中的 perk 明细"
带上描述与图标（`include_descriptions=True, include_icons=True`），它把**所有候选武器**的
**所有插槽**（枪管/弹匣/特性 + 模组/大师杰作/纪念物/装饰…约 860 项/把）都展开了一遍，
而筛选阶段其实**只需要 perk 名字**。

修的方向（未改代码）：

1. 筛选阶段只要名字：走 `socket_list(..., include_descriptions=False, include_icons=False)`，
   并且只展开可滚栏（barrel/magazine/trait），别把模组/装饰也算进来；
2. 命中之后再为**这一页要返回的**武器构造完整明细（50 把 × 860 项 ≈ 4.3 万次查询，秒级）；
3. 顺带把 5 个别名（`search_catalog`/`all_weapons`/`global`/`search_all`）共用的这段路径一次修好——
   它们现在各付一次这份开销（见 `docs/COMPATIBILITY.md` 的待删别名）。

预期：从"分钟级"回到"秒级"；改完要用 `run_corpus_weapon_rows.py` + 武器基线 diff 复核
（响应内容必须一个字不变，只是不算那些用不到的字段）。

## 这份语料没覆盖的（别当成"测过了"）

- **Agent 侧路由**（用户话术 → 该调哪个 intent）：由 `tests/agent_behavior_cases.yaml` + `test_skill_contracts.py` 守。
- **真实写入**（`confirmed=true`）：默认不做，按主语料第十五章手动跑。
- **`sweep` 是进程内调用**，绕过 FastMCP 的 schema 层；schema 才有的行为（`extra_forbidden`/`literal_error`/
  `less_than_equal`）只在 `mcp` 组覆盖。
- **长时间运行的行为**（后台刷新、token 过期、跨进程并发）：不在语料里。
- **非 macOS 路径**（Windows 的 ACL、控制台编码）：只有 CI/真机能验。

## 已知问题（首轮实跑发现，0.1.13 已全部修掉）

首轮实跑抓到的 7 条，按「先定位根因、再修」记在这里；每条都给了复现话术与代码位置，
回归锁在 `tests/test_corpus_full_regressions.py`（12 条）。修完复跑：`rows` 组 0 FAIL、全量三组 0 FAIL。
（`--known` 仍保留：以后再有登记在案的已知问题，可以用它只看新问题。）

| # | 现象（可复现话术） | 根因 | 定性 |
| --- | --- | --- | --- |
| 1 | `build_assistant(intent="analyze", character="hunter", health_target=100)` 的 `data.analysis.reason` 是 `No valid armor combination found (constraints may conflict with exotic + stat requirements)`，但**同一组约束** `intent="recommend"` 给出 `completion_rate=1.0` 的候选，且 `max_possible.health=134 ≥ 100` | `destiny_mcp/build/analyzer.py:147-153`：只要没有任何**单项**目标超过单项上限，就落进 else 写死这句；它没有验证过"有没有合法组合" | **P1**（0.1.13 已修：文案改成如实说明「都在单项上限内、单看上限解释不了」并指路 `recommend`/`find`，两处 reason 与刷取建议一并中文化；单测直接喂小快照给 analyzer 钉住） |
| 2 | `build_assistant(intent="exotic_armor", exotic_name="星火协议", character="warlock")` 返回 `armor.{classType,nameEn,flavorText,itemTypeDisplayName,tierType,intrinsicPerks}`；而 `inventory_assistant(intent="item")` 返回统一的 `identity.{name_en,slot_display,rarity_tier,gear_tier,item_type_display}` | `destiny_mcp/tools/_armor_branches.py:106` 直接透传 `manifest_query_svc.get_exotic_armor_details()` 的旧形状，没接 P3 的统一身份块 | **P2**（0.1.13 已修：走 `armor_payload.armor_definition_payload`，与 `intent="item"` 同一身份块；定义级不编 T 级） |
| 3 | `world_assistant(intent="collectible_item", item_name="无感")` 的 `items[].item_hash` 里出现 `-2064629060 / -1315203219 / -801484551 / -597710170`（同一次里还有正值） | `destiny_mcp/services/collection_service.py:122` 与 `:283` 直接取 Manifest 原始 `itemHash`，没走其它面统一用的 `utils.hash_utils.to_unsigned` | **P2**（0.1.13 已修：对外统一 `to_unsigned`，读状态时两种键都试） |
| 4 | `subclass_assistant(intent="artifact")`（不带 `artifact_name`）的 `data.artifact` 只有 `artifacts[]`，**没有** `current_artifact`；随便带一个名字才会出现 `current_artifact` | artifact 分支只在按名字查时才附当前神器 | **P2**（0.1.13 已修：不带名字也返回 `current_artifact`） |
| 5 | `subclass_assistant(intent="options", element="void", component="grenade")` 报错结尾是「…或中文职业名。。」 | `services/fragment_service.py:167` 传入的句子已带「。」，`exceptions.py:107` 的 `SubclassError` 又拼一个 | **P3**（0.1.13 已修：`SubclassError`/`APIError` 包装按需补句号） |
| 6 | `inventory_assistant(intent="search", item_name="绝对不存在的物品名")` → `ok=true`、`summary="已搜索物品。"`、`result.items=[]`；语料第十二章要求「未命中说『没找到』」 | 搜索分支 summary 是固定文案，不看命中数 | **P3**（0.1.13 已修：0 命中时 summary 写「没找到叫「X」的物品。」，文案抽到 `_formatters`） |
| 7 | 进程内 `inventory_assistant(intent="item", item_instance_id=None)` → `AttributeError: 'NoneType' object has no attribute 'strip'`（`_armor_branches.armor_item` 的 `if not item_instance_id.strip()`） | 缺 None 防御 | **P3**（0.1.13 已修：判断补 `or ""`） |
| 8 | `inventory_assistant(intent="equip_mod", mod_name="韧性模组")` 报「没找到护甲模组 '韧性模组'」，而 `生命值模组` 正常 | 词表漂移：`armor_mod_service` 的旧名映射把「韧性」换成「生命」，而游戏里的模组叫「生命值模组」（Manifest 官方名）。同一份六维词表当时散在 9 个文件里，值已经不一致（「生命」vs「生命值」） | **P2**（本轮 ② 已修：词表收进 `destiny_mcp/vocabulary.py`，旧名映射改指 `生命值`；`tests/test_vocabulary.py` 钉住旧名必须指向存在的规范名） |

> #1 的复现条件：组合规模要落在 `DESTINY_BUILD_MAX_COMBINATIONS` 阈值内，`analyze` 才会走精确分支并写出那句
> `reason`（超阈值时走中文的 `not_computed` 分支，不作可行性断言）。修的时候顺手补一条单测：把小快照直接喂给
> `destiny_mcp/build/analyzer.py` 里算失败原因的那个函数，断言「没有任何目标超过单项上限时，reason 不得声称配不出来」。

## 文档/口径漂移（已按本轮实测更正）

| 位置 | 原来写的 | 实测 |
| --- | --- | --- |
| `docs/testing/TESTING_CORPUS.md` 第十二章 A「默认条数」 | 「subclass 10」 | `subclass_assistant` **只有 `community` 读 `limit`**；`fragments`/`options` 传 `limit` 直接 `ignored_parameter`，void 碎片返回全量 19 条（已更正） |
| `docs/testing/TESTING_CORPUS.md` 已知问题「leaderboards」 | 恒 `ok=false` | **间歇**：本轮 sweep 里成功过 1 次，随后连测 3 次都失败（已更正为"常失败"） |
| `docs/testing/TESTING_CORPUS.md` 第四章实测耗时 | `analyze` 泰坦 ~15s | hunter `analyze`（health 100）29.8s、`recommend` 20.3s（同一台机器、热缓存） |
| `docs/testing/TESTING_CORPUS.md` 第五章 loadout「每套 ≈ 11 KB」 | 5 套 ≈ 55 KB | 实测默认 5 套 = **71.4 KB**（约 14.3 KB/套） |
| `docs/testing/TESTING_CORPUS.md` 第十二章 C「工具层前置校验 → `invalid_arguments`」 | 未列 `item` | `item` 缺 `item_instance_id` 走服务层 → `invalid_argument_error`（消息清楚，属正常分层，补进表即可） |

## 本轮实跑记录

**2026-09-15，macOS，真机账号 + 本地 Manifest + Starside 快照；`pytest -q` 1427 通过。**

```bash
.venv/bin/python scripts/run_corpus_all_rows.py --report /tmp/corpus_final.json
```

| 组 | 行数 | 结果 |
| --- | --- | --- |
| `sweep`（110 个 intent + 1 条汇总） | 111 | PASS 110、INFO 1；**0 异常 / 0 超时 / 0 空成功 / 0 意外写入** |
| `rows`（字段级 + 跨切面） | 83 | PASS 75、FAIL 5、WARN 1、SKIP 1、INFO 1 |
| `mcp`（协议层） | 11 | PASS 11 |
| **合计** | **205** | FAIL 5、WARN 1、INFO 2、SKIP 1、PASS 196 |

最慢 8 次：`build_assistant(analyze)` 28.5s、`weapon_assistant(catalog/all_weapons/global/search_catalog/search_all)` 26.3–26.9s（**同一件事的五个别名，各付一次 26 秒**）、`player_assistant(find)` 26.0s、`build_assistant(community_build)` 9.0s。
最大 8 个响应：`weapon_assistant(type)` 184.2 KB（20 件，9.2 KB/件，与语料的 9.7 KB/件一致）、`inventory_assistant(duplicates)` 85.4 KB（一页 10 组）、`loadout_assistant(list/get)` 71.4 KB（5 套，14.3 KB/套）、`build_assistant(community_build)` 58.2 KB、`inventory_assistant(get)` 52.6 KB。

FAIL 5 条＝「已知问题」表的 #2/#3/#4/#5/#6；#1（analyze 自相矛盾）本轮没触发，因为该账号当前的组合规模超过闸门、`analyze` 走的是中文 `not_computed` 分支（那条分支不作可行性断言，不算违规）——要看它得让组合规模落在阈值内；#7（`item` 传 null）只记 WARN：真 MCP 路径被 schema 拦住，只有进程内可达。

那一行 `SKIP`（`subclass:artifact_mod / 写入拦截`）是发现阶段还没修造成的：不带 `artifact_name` 时响应里没有
`current_artifact`，取不到模组 hash（这正是已知问题 #4）。修完单独复跑这两行已 PASS：模组「反屏障手炮」`4217417017`
→ `artifact_mod` `ok=true`；`equip_artifact_mod` → `confirmation_required`。

**0.1.13 修完后的复跑（同一份语料，同一台机器）**

- `--group rows --skip-slow`：**0 FAIL**（87 PASS / 7 SKIP，SKIP 是求解类与两处慢组；含新增的旧六维名与别名等价行）。
- **全量三组（发布前体检）：206 行，PASS 204 + INFO 2，0 FAIL / 0 WARN / 0 SKIP，退出码 0。**
  最慢仍是 `build(analyze)` 28.9s 与 `weapon(catalog 及其四个别名)` 26–27s；最大响应仍是
  `weapon(type)` 184.2 KB、`loadout(list)` 71.4 KB、`inventory(duplicates)` 85.4 KB。
- `pytest -q` 1439 通过（新增 12 条回归锁）。

**2026-09-20 复跑（性能六项之后，同一台机器）**

- `weapon_assistant(type)` 这个"最大响应"已经不在榜首：默认 10 件的列表行是 **23.2 KB**
  （2.3 KB/件），而它改之前是 **184.2 KB / 20 件**。同一份响应的线上文本 387,598 → 41,537 字符。
- 只读的武器面重新录了基线（`tests/baselines/weapon_responses`，新增 `type_list_default` 用例），
  `scripts/diff_weapon_baseline.py` 对改前快照 **0 个无理由消失**；其余用例耗时与体积照旧。
- 口径提醒：本文件里的 KB 是**解码后的紧凑 JSON 字符数**；线上文本带 `\uXXXX` 转义，
  中文多的时候大约是它的 2 倍（两个数都对，别混着比）。

**2026-09-20 复跑（0.5.0 发布 + 锻造图样之后，同一台机器）**

```bash
.venv/bin/python scripts/run_corpus_all_rows.py --report /tmp/corpus_patterns.json
```

| 组 | 行数 | 结果 |
| --- | --- | --- |
| `sweep`（119 个 intent + 1 条汇总） | 120 | PASS 119、INFO 1 |
| `rows`（字段级 + 跨切面） | 95 | PASS 95（含 5 条新的 `patterns` 断言） |
| `aliases`（别名等价） | 17 | PASS 17（含 `patterns` 六个别名） |
| `mcp`（协议层） | 10 | PASS 10 |
| **合计** | **242** | **PASS 240、INFO 2、0 FAIL / 0 WARN / 0 SKIP**，退出码 0 |

- 新增的 `patterns` 行全绿：计数自洽 183、`未开始` 的 `progress` 为 null、来源带页面与
  `trust`、变体指回基础版、名字对不上时说清图鉴条数、六个别名 `data` 逐字节相同、默认 20 条。
- 最慢 8 次：`build(analyze)` 33.8s、`player(profile)` 9.4s、`activity(pgcr)` 9.3s、
  `activity(clan_leaderboards)` 7.3s / 7.1s、`build(community_build)` 7.0s、`build(recommend)` 6.4s / 5.8s；
  `weapon(patterns)` 首次 2.8s（之后 5 分钟 TTL 内 0.3–0.6s）。
- 最大 8 个响应仍由库存与配装占着：`inventory(duplicates)` 85.4 KB、`loadout(list/get)` 71.4 KB、
  `inventory(重复武器)` 63.6 KB；`patterns` 默认一页 **5.9 KB**。
- `pytest -q` **1626 通过**（新增 `tests/test_pattern_query.py` 28 条、`tests/test_crafting_sources.py` 12 条）。

**这一轮里语料自身的 bug（都已修，记在这里免得下次重犯）**

1. **`sweep` 一开始没把 `intent` 传下去**：110 条「体检」全落在各工具的默认 intent 上，等于同一个默认调用跑了 110 遍（还因此把 `weapon_assistant` 的 `query` 参数误判成 bug）。修好后才是真正的 110 个 intent。
2. 断言路径写错 7 处：`inventory.type` 传了它不读的 `limit`、`search` 的键是 `result.items`（不是 `instances`）、`set_bonus` 的 2/4 件效果在 `perks[].required_count`（不是 `tiers`）、`recommend` 在 `data.recommendation.results[].build.items[]`、`analyze` 在 `data.analysis`、`search_identifiers` 在 `data.results.<kind>`、`loadout.save` 必须先给 `character` 才走到确认门。
3. 碎片别名行把「void 组」和「strand 组」当成一组比（19 ≠ 16 是两个元素本身不同）。
4. 旧工具面那一行现在是**反向断言**：旧的 profile/开关都不该再变出工具（历史工具面已剥离到 `legacy/`；哪天又变回 77 个，说明有人把它挂回来了）。
