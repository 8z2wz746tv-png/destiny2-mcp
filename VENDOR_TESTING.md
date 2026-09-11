# 商人专项测试集（`world_assistant(intent="vendor")`）

这份只测一件事：**问商人**。每条都给「怎么说 → 该调什么 → 期望看到什么 → 什么样算不合格」。

先说三句最重要的：

1. **看结构，别对商品名较真。** 商人是每日/每周刷新的，商品名、价格、数量、等级、在不在，全都会变。所以"期望"栏里写的是**字段和它们之间的关系**（例如 `truncated=true` 时商品数必须等于 `limit`），不是"应该看到某某枪"。
2. **新开一个任务再测。** 工具的参数和行为是宿主**连接时**读的，旧会话里的进程还是老代码。
3. **让 Agent 把 `error.code` 和 `mode` 原样念出来。** 这两个字符串是断言，不能凭印象。

开工前跑一次：

```bash
.venv/bin/python scripts/verify_mcp.py
```

必须看到 `MCP_HANDSHAKE=ok`、`PARAMETER_GUARD=ok`、`MCP_TOOL_COUNT=8`。**`PARAMETER_GUARD=ok` 就是"你连的确实是新代码"的证明**；看不到它说明还在跟旧进程说话，重启宿主再来。

---

## 一、路由总表

| 用户这么说 | 该走的路由 | 关键参数 | 响应形态 |
| --- | --- | --- | --- |
| 有哪些商人 / 商人现在都卖什么 | `world_assistant(intent="vendor")` | 不传 `vendor_name` | `mode="menu"` |
| 班西今天有什么好东西 | `world_assistant(intent="vendor")` | `vendor_name="班西-44"` | 先 `menu`（若名字有歧义）或直接 `mode="detail"` |
| 萨瓦拉那里有什么 | 同上 | `vendor_name="萨瓦拉"` | `mode="detail"` |
| 苏拉那边…… | 同上 | `vendor_name="苏拉"`（片段就行） | `mode="detail"` |
| 这个商人页面 hash 是 2484291326，看看 | 同上 | `vendor_name="2484291326"` | `mode="detail"` |
| 那把枪值不值得刷 | `world_assistant(intent="vendor")` → 再读 `farming_list` | `vendor_name=<商人>` | `mode="detail"` + `farming_list` |
| 某件武器本身的详情 | `weapon_assistant(intent="analyze", weapon_name=…)` | 不是商人入口 | 不要用 `vendor` 查单件武器 |

**只有 `vendor` 和 `community` 两个 intent 读 `vendor_name`**；`world_assistant` 的其它 intent 传它会得到 `ignored_parameter`。

---

## 二、稳定断言（每次、每台机器都必须成立）

这几条与刷新无关，可以直接当硬指标：

1. 响应里 **`data.vendors.mode`** 只有两种取值：`"menu"` 或 `"detail"`。
2. `mode="menu"` 时：**每件商品的列表是空的**（`vendors[].sale_items == []`），并且有 `question` 提示"要查哪个"。
3. `returned_vendors == len(vendors)`，`returned_weapons` 同理（不能自相矛盾）。
4. `truncated == (total > returned)`：说截断就必须真截断，真截断就必须说。
   - **响应级**（`data.vendors.truncated`）只在菜单态有意义（商人列表被 `limit` 裁过）；
   - **商人级**（`vendors[].truncated`）只在详情态有意义（商品被裁过）；菜单态每个商人都写 `false`，
     因为菜单本来就**不展开商品**（此时 `total_items > 0` 是正常的，不算违反）。
5. 每件商品都有 `item_hash`（非 0）——它是串到武器详情的钥匙；`category_index` 要么指向
   同一个商人 `categories[].index` 里**确实列出**的分类，要么为 `null`，**不允许指向被过滤掉的分类**。
6. `can_be_sold=false` ⇒ `failure_reasons` **非空且不含空字符串**。
7. `purchasable_items ≤ total_items`；`returned_vendors ≤ total_vendors`；`total_items + hidden_items`
   = 上游真实行数（装饰性条目单独计数，不静默吞掉）。
8. `categories[].kind="submenu"` ⇒ 一定有 `target_vendor_hash`；`target_available=false` 时必须有一条 `next_actions` 说明"本次没返回"。
9. 不点名查商人 **不该** 逐个商人去拉 Perk socket（表现为慢+一堆 API 调用）。
10. 任何"查不到"都必须是**说得清的空**：要么 `warnings` 给相近名字，要么明说"本周期不在/本次没返回"，**不允许**静默空数组。
11. 详情被截断时，商品按**分类顺序**给出（同分类内按上游序号）：前缀覆盖的是靠前的分类，
    不是上游字典顺序的随机切片。想知道"剩下的在哪"，看各分类的 `item_count` 即可
    （`sum(item_count)` 等于 `total_items`；某个分类 `item_count=0` 就是今天那个 tab 没货）。

---

## 三、分组语料

### A. 菜单态（不点名）

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| A1 | 现在有哪些商人？ | `mode="menu"`；每个商人一行：`vendor_hash`、`name`、`identifier`、`rank`、`purchasable_items/total_items`、`categories` 数量；`question` 提示选一个；`next_actions` 里带 2–3 个可直接粘的调用 | 一次返回上千件商品；或只回一句"有很多商人" |
| A2 | 有哪些商人？（带 `limit=1`） | `returned_vendors=1`、`total_vendors` 远大于 1、`truncated=true` | 忽略了 limit；或者说"只有 1 个商人" |
| A3 | 商人一共多少个？ | 用 `total_vendors` 回答（实测 229 左右），并说明这只是有货架的商人 | 拿 `returned_vendors` 当总数 |
| A4 | 商人菜单里有名字显示不出来吗？ | 有：个别页面 manifest 没给名字，会回退成 `identifier`（如 `EVERVERSE_ARCHIVE`）；只有连 identifier 都没有时才允许退成 `#hash` | 名字栏是空白；或明明有 identifier 却只显示纯数字 hash |

### B. 详情态（点名一个商人）

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| B1 | 班西今天有什么？ | `mode="detail"`；`total_items`/`purchasable_items`；`categories[].kind` 至少能看到 `rewards`（等级奖励）与 `submenu`（子页面）；`rank.name`（如「枪匠等级」）与 `level/level_cap` | 只给商品清单，没有分类和等级 |
| B2 | 萨瓦拉那里卖什么？ | 同上；`rank` 大概率是「先锋等级」 | 分类信息缺失，或把「等级奖励」当成"另一个商人" |
| B3 | 泰斯·艾夫瑞斯（银币商店）有什么？ | `total_items` 上百件；`mode="detail"`；因为货架很大，`limit` 没给时应自动截断并 `truncated=true`；`next_actions` 里给出一串 `EVERVERSE_*` 子页面调用 | 一次性倒出上百件；或只给一点点却说"就这些" |
| B4 | 点进去看它的子页面 | 顺着 `next_actions` 再查一次，得到那个子页面自己的货架（`mode="detail"`、`name` 可能是 `EVERVERSE_*`） | 把子页面货架并进主商人一起返回 |
| B5 | 指定角色呢？ | `character="猎人"` 可用（`vendor` 认 `character`）；换角色后商品可能不同 | 传了 `character` 被拒（它属于 `vendor` 的参数） |

### C. 名字怎么解析

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| C1 | 苏拉 | 按**名字片段**命中「苏拉娅·霍桑」，`mode="detail"` | 说"没有这个商人" |
| C2 | 直接给它 hash：`2484291326` | `mode="detail"`，名字是「武器聚焦」（`identifier="GUNSMITH_WEAPON_FOCUSING"`） | 把 hash 当名字查不到 |
| C3 | 老九 / 仄 / Xur | 三个别名都指同一个商人 | 只认其中一个写法 |
| C4 | 传承装备那个页面卖什么 | `mode="menu"` + `question` 说明匹配到多个（实测 6 个：通用武器/熔炉/铁旗/先锋/智谋/试炼），候选各自带 hash | **自己挑一个**就给答案 |
| C5 | 随便编个名字：`这个名字不存在xyz` | `vendors=[]` 但**不是空白**：`warnings` 给出相近名字，`next_actions` 给出可用调用 | 静默返回空数组；或编一个商人出来 |
| C6 | 问一个"该在但现在不在"的商人（例如周三问仄） | `warnings` 明说本周期不在／本次没返回，`vendors=[]` | 空数组或一句"查询失败" |

### D. 分类与子页面（`categories`）

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| D1 | 这个商人的货架分了哪几类？ | 每类的 `index`/`name`/`identifier`/`kind`/`item_count`；`kind` ∈ `sale`/`rewards`/`submenu` | 只给一个扁平商品列表 |
| D2 | 「等级奖励」是什么？ | `kind="rewards"`、`identifier` 形如 `category.rank_rewards_seasonal`；说明它是**普通分类**，不是另一个商人 | 说"等级奖励是单独的模块/查不到" |
| D3 | 「聚焦破译」点进去是什么？ | `kind="submenu"` + `target_vendor_hash`；工具给的是**下一步调用**，不是已合并的货架 | 声称这个分类不存在；或把两页货架混在一起 |
| D4 | 帮助按钮（如「先锋行动」「枪匠」「霍桑」「仄」）算货架吗？ | 不算：装饰性 tab 不出现在 `categories` 里，挂在它下面的那条占位条目也**不进 `sale_items`、不计入 `total_items`**，只体现在 `hidden_items` 计数里 | 把帮助按钮列成一类商品；或商品里的 `category_index` 指向一个没列出来的分类 |
| D5 | `target_available=false` 的子页面 | `next_actions` 里有一条明说"本次没有返回，暂时看不到它的货架" | 假装能查；或默默省略 |

### E. 能不能买 & 值不值得刷

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| E1 | 这里面哪些现在能买？ | 以 `can_be_sold` 为准；每件不可买的都带 `failure_reasons`（实测例子：`["需要等级4"]`、`["需要等级10"]`） | 全部 `can_be_sold=true`（说明读了上游不存在的字段） |
| E2 | 为什么这件买不了？ | 原样引用 `failure_reasons`；文案取自上游 `failureStrings` | 只说"买不了"不给原因 |
| E3 | 我已经有这件了吗？ | 看 `owned`；它只表示"账号里有"，不等于"不用再买" | 把 `owned=true` 当成"买不了" |
| E4 | 这些东西里哪些值得刷？ | 读 `data.farming_list`：`matched_count`、`results`（带清单名与评级）、`unmatched` | 用模型自己的印象编"值得刷"；或把 `unmatched` 说成"都不值得刷" |
| E5 | `farming_list.unmatched` 里应该是什么？ | **只应该是商品名**；出现商人名（「指挥官萨瓦拉」）、分类名（「等级奖励」）、声望名（「先锋等级」）、Perk 名（「狂暴 [PvP]」）都算不合格 | unmatched 里混进非商品名 |
| E6 | `farming_list.available=false` | 照实说明本地清单不可用（带 `error`），官方库存数据仍然有效 | 把清单不可用说成"没数据" |

### F. 截断与 `limit`

| # | 说什么 | 期望输出 | 不合格的表现 |
| --- | --- | --- | --- |
| F1 | 班西的货架，只看 3 件 | `limit=3` → 该商人 `sale_items` 恰好 3 件，`truncated=true`，`total_items` 是真实总数 | 3 件但不标 `truncated` |
| F2 | 商人详情最多给几件？（不给 limit） | 走默认 40 件（菜单默认 15 个商人） | 一次全给或只给 1 件 |
| F3 | `limit=250` 这种超大值 | 被夹到上限（250），并照实标 `truncated` | 报错或无视上限 |
| F4 | 按类型查武器：手炮，先给五把 | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` → 5 把 + `total_weapons=124` 左右 + `truncated=true` | 给 5 把却说这是全部 |
| F5 | **`limit=12`**（曾经的坑） | 和别的数字一样生效：菜单 12 个商人、详情 12 件商品；对照 `11→11 / 12→12 / 13→13` 必须单调 | 12 被吞掉（详情变默认 40、菜单变默认 15）；三连测下来非单调 |
| F6 | `limit=0` 或负数 | 等于"没指定"，按模式默认（菜单 15 / 详情 40），**不报错**；`limit=null`（不传）同理 | 报错；或返回 0 条 |
| F7 | 截断时给的是哪几件？ | 按分类顺序的前 N 件：例如 `EVERVERSE_FOCUSED`（分类 0 有 1 件、分类 1–5 各 5 件）传 `limit=5` → 覆盖分类 0 和 1；**不应**出现"前 5 件只落在分类 0 和 2、其它分类像空的" | 按上游字典顺序乱切，让人误以为某些分类没货 |

### G. 参数守卫与错误路由

| # | 调用 | 期望输出 | 备注 |
| --- | --- | --- | --- |
| G1 | `world_assistant(intent="vendor", item_name="遗产")` | `ok=false` + `error.code="ignored_parameter"`，消息里列出真正认领 `item_name` 的 intent，提示"vendor 返回整个货架，不按物品过滤"，并给结构化 `next_actions`（改走 `inventory_assistant(intent="search")`） | 卖单件武器查询要用 `inventory_assistant(intent="search")`；所有 `ignored_parameter` 都应带 `next_actions`，只给文字不给调用算不合格 |
| G2 | `world_assistant(intent="vendor", query="遗产")` | 同上（`query` 只给搜节点和社区） | 别把 query 当商品过滤 |
| G3 | `world_assistant(intent="weekly", limit=3)` | 允许（`weekly` 认 `limit`） | 对照 G4 |
| G4 | `world_assistant(intent="weekly_full", limit=3)` | `ok=false` + `ignored_parameter`（`weekly_full` 不读 limit） | 这是**故意**的，不是 bug |
| G5 | `world_assistant(intent="weekly", player_name="x#1")` | `ok=false` + `ignored_parameter`（周常与账号无关） | 商人才需要 `player_name` |
| G6 | `weapon_assistant(intent="type", weapon_type="手炮", vendor_name="班西-44")` | **协议级报错**：`isError=true`，文本形如 `Error executing tool weapon_assistant: 1 validation error…`。原因：`vendor_name` **根本不是 `weapon_assistant` 的参数** | 属于已知取舍（schema 层拒绝，见第六节），不是商人功能的问题 |
| G7 | `world_assistant(intent="community", vendor_name="班西-44")` | `ok=true`：`community` 也认领 `vendor_name`，但它在这里是**搜索词**，不是商人查询 | 想让 Agent 查商人却给了 `community`，会搜出一堆文本 |

### H. 反例：出现这些就是答错了

| 场景 | 明确不该出现 |
| --- | --- |
| 用户没点名商人 | 直接倾倒上千件商品 / 逐商人拉 Perk 导致超时 |
| 商人名字有歧义 | 自己挑一个给答案（必须先给候选） |
| 子页面（聚焦破译等） | 把子页面货架并进主商人，或声称它不存在 |
| 商人本周不在 | 编商品、拿旧数据顶替、或只说"查询失败"不解释 |
| 商品买不了 | 只给 `can_be_sold=false` 不给原因 |
| 货架被截断 | 不说 `truncated`，让 40 件看起来像全部 |
| 刷取清单没收录 | 把 `unmatched` 说成"不值得刷"（没收录 ≠ 不值得） |
| 商人数据获取失败 | 给缓存或记忆里的商品清单（🔐 账号相关的答案必须来自本次查询） |

---

## 四、会变的 vs 不会变的（免得下一轮误报）

| 会变（别当断言） | 不会变（可以当断言） |
| --- | --- |
| 商品名、价格、数量、`tier` | 字段名与 `mode`/`kind` 的取值 |
| `total_items`、`purchasable_items`、菜单前几名 | `truncated == (total > returned)`、`returned == len(...)` |
| `rank.level`、进度数字、`level_cap` 的具体数值 | `level_cap` 要么是 `null`（= 无上限，上游发的是 -1），要么 `level ≤ level_cap` |
| 商人是否在（仄只在周五 17:00–周二 17:00 UTC 在；聚焦页可能整周没货） | "不在/没返回"必须被明说 |
| 哪个子页面本次有返回 | `kind="submenu"` 必须有 `target_vendor_hash`；截断前缀按分类顺序取 |
| 具体商人 hash 是否出现（实测 229 个有货架的商人） | 别名/hash/片段三种写法都能定位到同一个 hash |

---

## 五、本次实测参考值（2026-09-12，**仅作对照，不要当断言**）

| 商人 | `vendor_name` | 实测 |
| --- | --- | --- |
| 指挥官萨瓦拉 | `69482069` / `萨瓦拉` | 14 件，可买 6；分类：等级奖励(rewards,7)、护甲、周常：先锋武器奖励(submenu→153857624 在线)、聚焦破译、传承聚焦破译(submenu→3444362755 在线)；`rank.name="先锋等级"` |
| 班西-44 | `672118013` / `班西-44` | 10 件，可买 3；分类：等级奖励(rewards,6)、杂项、传承聚焦破译(submenu→908529654 **本次没返回**)、聚焦破译(submenu→2484291326 在线)；`rank.name="枪匠等级"` |
| 泰斯·艾夫瑞斯 | `3361454721` | 224 件，可买 127；`next_actions` 给出一串 `EVERVERSE_*` 子页面 |
| 武器聚焦 | `2484291326` | 37 件（班西「聚焦破译」的目标页） |
| 传承装备 | 名字有歧义 | 6 个同名页面 |
| 全商人数 | 不点名 | `total_vendors=229`，菜单默认给 15 个，响应约 **17 KB**（修复前一次 1.85 MB） |
| 详情响应大小 | 点名 | 约 **3–7 KB**；`type` + `limit=5` 约 22 KB（修复前 515–651 KB） |
| 最大响应 | 泰斯 + `limit=250` | 224 件全量约 **178 KB / 5200 行**（`truncated=false`）；这是上限级场景，超过它说明 `limit` 没被夹住 |

---

## 六、已知问题（测到这些不算新 bug）

| 现象 | 真实原因（已核实） | 状态 |
| --- | --- | --- |
| `weapon_assistant` 传 `vendor_name` → 原始 pydantic 报错、`isError=true` | 该参数**不属于**这个工具的参数表，校验发生在工具函数之前，属于协议级拒绝（不是业务失败） | 取舍中：保持协议级，或统一包一层 `ok=false` 信封 |
| 泰斯主页面的分类名是「分类0」 | Bungie 的 manifest 对那一类确实没给名字/identifier，只能回退成"分类+序号" | 数据如此；序号与 `category_index` 对得上 |
| 「等级奖励」分类里有商品但 `can_be_sold=false` | 等级不够，`failure_reasons` 会写「需要等级N」 | 正常，照实说等级要求 |
| 仄（Xur）周三问不到 | 他只在周五 17:00 – 周二 17:00 UTC 在塔里，代码按时间判断后明说不在此周期 | 正常 |
| `farming_list` 里 `matched_count=0` | 本地清单是**武器**清单，材料／货币／护甲不会命中 | 正常；`unmatched ≠ 不值得刷` |
| 某件商品 `item_type` 是空串 | 该条目在上游没有类型（Bungie 枚举里那个成员就叫 `None`），显示空串而不是字符串 `"None"` | 已修 |
| ~~`rank.level_cap=-1` 被当成上限~~ | 已修：上游对无上限的声誉体系（内欧姆那等级、王座世界等级、总合部、派克组、智能失效保险）发 -1，现在归一成 `null` | **已修** |
| 「智能失效保险」的 `rank.name` 和商人同名，像是回退 | 不是回退：manifest 里那条声望定义的 `displayProperties.name` 本身就写作「智能失效保险」（已直接查定义核实） | 上游数据如此，不是缺陷 |
| 装饰性条目混进 `sale_items`、`category_index` 越界 | 已修：装饰性 tab 下的条目不再算商品，改计入 `hidden_items` | **已修** |
| ~~显式 `limit=12` 被当成"没指定"~~ | 已修：`world_assistant.limit` 默认值改成 `null`（schema 里也是 `default: null`），11/12/13 现在都照常生效 | **已修** |
| ~~`weekly_full` + `limit=12` 被静默放过~~ | 上一条的副作用：12 曾被当成"没传"，所以不读 `limit` 的 `weekly_full` 也就没报错。现在会正常返回 `ignored_parameter` | **已修** |
| 聚焦类子页面"分类 26 个槽位、明细只有 5 件" | 不是丢数据：上游 `categories` 声明的是槽位，实际有货的行看 `total_items`/`sale_items`；`limit` 小的时候明细按分类顺序取前缀（见 F7） | 口径已写进断言 11 |

---

## 七、机器可读子集

`tests/agent_behavior_cases.yaml` 里有对应的路由用例（`vendor_read`、`vendor_menu_then_detail`、`vendor_specific_by_name`、`vendor_ambiguous_name`、`weapon_type_truncated`），由 `tests/test_skill_contracts.py` 校验"期望的路由必须真的存在"。改动路由或新增语料时，两处要一起改。

字段级的行为断言在测试里，不靠人肉核对：

```bash
.venv/bin/python -m pytest tests/test_vendor_menu.py tests/test_vendor_inventory_modes.py -q
```

几条"环境不具备、没法现场构造"的用例，自动化里已经有对应断言，不必等条件：

| 现场难测的用例 | 自动化位置 |
| --- | --- |
| C6 商人本周期不在 | `test_vendor_inventory_modes.py::test_xur_absence_is_reported_instead_of_returning_nothing`（把可用性打桩成 false） |
| E6 `farming_list.available=false` | `test_farming_list_lookup.py`（服务缺失 / 抛 `DestinyMCPError` 两条路径） |
| F3 `limit` 超大值的夹取 | `test_vendor_menu.py::test_limit_clamping_uses_mode_defaults`（`clamp_limit(1000)` → 250 上限） |
