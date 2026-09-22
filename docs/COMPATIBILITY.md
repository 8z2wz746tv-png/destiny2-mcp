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

## 未发布：候选排序口径统一（**破坏性**，见计划文档决定 2）

求解器内部堆、展示排序、`score` 以前是**三套口径**，现在只有一套
（`build/ranking.goodness_key`：布尔违规位 → 每个优先项一层「先达没达、再差多少」→
普通层「先个数、再缺口」→ 优先级值 → 封顶总和）。**候选顺序会变**。

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 排序键 | 求解器堆：优先级字典序；展示：`completion_rate` 打头 → 优先级 → 加权总分 | 三处**共用**同一把键 |
| `score` 的含义 | 加权总分（达标率×1000 + 六维总和×0.1 − **超出目标每点×0.5**） | **封顶后的六维总和**（超上限部分不计）；**不再是排序键** |
| 「多堆 10 点手雷」 | 在评分里是负收益（浪费惩罚） | 不影响达标判定；达标之后按优先级"越高越好" |
| 超上限 | 无概念 | 与「没达下限」同级：掉到不违规的方案后面 |
| `completion_rate` | 排序第一键 | 只作展示 |

`BuildResult.score` 的**数值会整体变化**（量级从"1000+ 的加权分"变成"200~1200 的总和"）。
只把它当"越大越好"读的调用方不受影响；把绝对值当阈值的要改。

## 未发布：有解时默认会动调谐（P4，行为变化）

以前只有"没达标"时才去试调谐（补救路径）；现在**有解也会**在候选池上做一次单件调谐的邻域贪心
（`build/tuning.local_tuning_improvement`，每一步都过权威复核）。看得见的变化：

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 六维 | 刚好达标就交卷 | 达标之后按优先级继续吃调谐额度（真机：手雷 120 → 145） |
| `tuning_changes` / `requires_tuning` | 只有补救路径会给 | 有解也可能给（**要用户自己动手改**，`canonical_build` 仍不含调谐插件） |
| 没有目标也没有优先级时 | 无 | **不动**（不为了封顶总和 +6 让用户改 5 件） |
| 耗时 | — | 池子上多一趟：真机小池 +8s（9.2s→17.6s）；补救路径那类**没变**（仍是分钟级，见计划 P4 记录） |

## 未发布：`build_assistant` 新增 `stat_caps`（属性上限）与 `data.reachable`（可达区间）

求解类 intent（`recommend`/`find`/`analyze`/`farm_target`）新增参数 `stat_caps`：
属性**上限**映射，键与 `priority_stats` 同一套词。`find` 的有解响应新增 `data.reachable` 与
`data.reachable_note`（只在算得出时出现）。

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 表达"别超过 100" | 没有办法 —— 模板里的区间上半截被丢掉，超了也没人报 | `stat_caps={"super_stat": 100}`；超了在 `builds[].max_violations` 里逐项标注并排到后面，**但不阻止出解** |
| 模组分配 | 只受下限与优先级支配 | 到了上限就不再往那一项堆（`desired_max` 吃 caps） |
| 每项能到多少 | 只在"无解"的阶梯里给，且是逐项最大值 | 有解时也给 `data.reachable`（**保守下界**）+ `reachable_note` 两句免责 |
| 上限输入错误 | —— | 不认识的键、超 0-200、上限低于下限都是 `build_validation_error`，**不静默忽略** |

`stat_caps` 没认领的 intent 传它会被 `ignored_parameter` 拒收（走既有的参数归属表）。

## 未发布：0 候选时新增 `search` 自证、`ladder.verdict.satisfiable` 可能是 `null`（见 ADR-013 之后的 P1）

`build_assistant(intent="find"|"recommend")` 在 **0 候选**或**没搜完**时多一个 `data.search`
（`{exhaustive, combos, truncated_by}`）；`ladder.verdict.satisfiable` 多了 `null` 取值。

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 0 候选的 `summary` | 「找到 0 个候选配装。」 | 「找到 0 个候选配装：枚举完了，没有任何一套能满足这些下限（枚举了 6,283,200 套组合）。」 |
| 空结果的可信度 | 只能自己相信"求解器跑完了" | `data.search.exhaustive=true` + `combos`（确定性工作量）自证 |
| `ladder.verdict.satisfiable` | 恒为 `false` | **可能是 `null`** —— 表示"这次没搜完"，不是"配不出来"；只有 `exhaustive=true` 才是 `false` |
| 超时错误话术 | 英文："Build computation exceeded the 300s budget; …" | 中文，并明说「**没算完**，不代表配不出来」+ 收窄建议 + `DESTINY_BUILD_TIMEOUT_SECONDS` |

`search` 只在"0 候选"或"没搜完"时出现 —— 有候选的正常响应**逐字节不变**（护甲基线不用改）。

## 未发布：换模组的响应多出"能不能插"的字段，配装步骤名从 `mod_in_game` 改成 `mod_blocked`（见 ADR-013）

`inventory_assistant(intent="equip_mod")` 的方案新增 `to.unlock_state`（`true`/`false`/`null`）、
`to.conditions` 与 `alternatives[].unlock_state`；目标已经是槽里现值的请求现在回
`success=true` + `already_installed=true`（上游 1679），摘要改成「已经装着它，这次没有改动」；`writable=false` 的 `writable_reason` 改成
"没解锁 + Manifest 的插入条件"。配装执行里"模组被上游拒绝"那一步的 `action` 从
**`mod_in_game` 改名成 `mod_blocked`**，`detail` 里带具体原因（1676 插入条件 / 403 AWA / 1663 含糊话术）。

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 调谐方案的 `writable_reason` | "Bungie 的插槽接口实测回 1663…第三方写不进去" | "本项目**还没验证过**调谐写入"（1663 那句出自 ADR-012 推翻的字段名 bug，不再引用） |
| 没解锁的模组 | 当能装：给确认请求，确认后上游回 1676，被说成"请游戏内手动装" | 直接 `writable=false` + `written=false`，reason 给出 Manifest 的插入条件；`confirmed=true` 也不写 |
| 同名多版本 | 按属性加成 / 能量挑 | **先按这一位是否已解锁**挑，其余列在 `alternatives[].unlock_state` |
| 上游没给可插入清单 | （以前根本没读这份清单） | `unlock_state=null` 且照旧可写 —— **没数据 ≠ 不允许** |
| 配装步骤 `action` | `mod_in_game`（"Bungie 不允许 API 装，请游戏内手动装"） | `mod_blocked`（"上游拒绝写入，原因见 detail；这些条件游戏里同样要先解决"） |
| 目标已是槽里现值 | 上游回 1679 被当失败抛出 | `success=true` + `already_installed=true` + 摘要「已经装着它，这次没有改动」 |

`writable` / `written` 两个键的语义没变（`false` 就是"这次不写账号"），
所以只判断"写没写"的调用方不用改；把 `writable_reason` 当固定话术转述的调用方要跟着更新。

## 未发布：`equip` 的异域互斥改成"同类"判据，计划里出现武器槽位（见 ADR-011）

`equip` 的异域互斥判据从"全身只能穿一件异域"改成 Manifest 自己的 `equippingBlock.uniqueLabel`：
异域**武器**一件 + 异域**护甲**一件，两类**互不冲突**。

| 变了什么 | 以前 | 现在 |
| --- | --- | --- |
| 装异域武器、身上穿着异域护甲 | 计划里多一步"顶下异域护甲"（**错**，执行了也解决不了问题） | 不冲突，直接装 |
| 装异域武器、身上穿着另一把异域武器 | **零冲突识别**，真执行撞 500 `UniqueEquipRestricted` | 给出"先用**同槽位**的非异域顶下"的步骤 |
| 失败话术 `UniqueEquipRestricted` | "全身只能穿一件异域**护甲**…挑一件非异域的同部位护甲" | "同类的异域只能穿一件（异域武器一件 + 异域护甲一件）…挑一件非异域的同部位装备" |

**`slot` 取值**：护甲那半的键一个没动（`helmet`/`gauntlets`/`chest`/`legs`/`class_item`），
但 `EquipPlan.target_slot` / `EquipPlanStep.slot` / `EquipPlanBlock.slot` 现在**也可能是武器槽位**
（`kinetic`/`energy`/`power`）—— 武器目标以前这里给的是**空串**（认不出部位），现在给真实槽位。
把 `slot` 当"只会是护甲槽"读的调用方要放宽；只看护甲的调用方行为不变。

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
| 周常轮换表 | `world_assistant(intent="rotations")` | 新 intent（只读）：本周特色突袭/地牢（官方里程碑）、本周夜幕/宗师**哪个打击 + 词缀 + 掉落**（角色活动组件 204）、上维挑战/异域任务/泉源（自维护周期表 + 锚点）。每行带 `source=official\|schedule`；**遗失区域顺序未核对，只给候选**（见 ADR-010） |
| 图样按稀有度筛 | `weapon_assistant(intent="patterns", rarity="异域")` | 新参数（只被 `patterns` 读）：异域/金枪、传说/紫枪、稀有；稀有度词表收拢到 `vocabulary.RARITY_ALIASES`（`inventory_assistant` 原来私藏一份）。同时 `counts` 旁多了 `by_tier` 汇总——真机上出现过"只读第一页把 16 把金枪报成 2 把" |
| 锻造武器模式查询 | `weapon_assistant(intent="patterns")` | 新 intent（只读）：图鉴「模式和催化」183 条武器模式的进度（组件 900，就是游戏里那条「模式进度 4/5」）、还差几个红框萃取、需求次数、掉落来源。**新能力，非破坏性**：没动任何既有 intent 的键；两个"可锻造"口径并存（模式 183 条 vs `is_craftable` 219 件，后者含 36 件变体，变体可塑形栏位更少、且不带深视插槽），见 ADR-009 |

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
| | `patterns` | `pattern`、`craft`、`锻造`、`锻造武器`、`图样`、`图样进度`、`模式进度`、`红框`、`红框进度`（永久中文说法：玩家说「红框」「锻造武器」，游戏官方中文叫「模式」；`pattern`/`craft` 是英文近义，只登记不宣传） |
| `loadout_assistant` | `list` | `get` |
| `subclass_assistant` | `get` | `subclass` |
| `activity_assistant` | `stats` | `career`、`historical_stats` |
| | `weapon_history` | `weapons`、`weapon_usage`、`weapon_leaderboard` |
| | `aggregate` | `activity_aggregate`、`activity_stats` |
| | `leaderboards` | `leaderboard` |
| `world_assistant` | `rotations` | `轮换`、`周常轮换`、`这周`（永久中文说法） |
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
