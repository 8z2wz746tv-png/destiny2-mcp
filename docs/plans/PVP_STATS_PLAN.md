# 计划：生涯/赛季战绩的分档与标注（PvP 数据摸透后的开发计划）

状态：**待评审**（未开工）。起因：用户拿游戏内/机器人数字与我们的输出对照，发现"生涯击败"差了几万。

## 一、实采证据（2026-09-17，本机真机，只读）

| 事实 | 证据 |
| --- | --- |
| 游戏内生涯计数器的真身是 **profile 组件 1100（Metrics）** 里的一条 metric | `811894228` = `Opponents Defeated`，描述原文 *"The total number of opponents defeated in Crucible matches. Tracks from Season 1 onward."*，`progress = 124495`，与游戏内、第三方机器人显示的数字**一字不差** |
| 该组件在**本账号**有 **402 条**计数器 | 原始响应 66 KB；数值最大者 `79,712,994`（PvE 累计），Crucible 那条 124,495 |
| 我们**从未读过组件 1100** | `profile_components.py` 里没有 METRICS；所有统计都走 stats 接口 |
| stats 接口的生涯数字**分三档**，且**不含**上述计数器口径 | 账号级 `mergedAllCharacters.results.allPvP.allTime.opponentsDefeated = 78,864`（= 现存 3 角色 50,622 + 已删 5 角色 28,242）；`mergedDeletedCharacters = 28,242` |
| 我们此前报的是**单角色** | 第一个角色 `23…5779` 的 `opponentsDefeated = 17,703 kills / 12,496 deaths`（正是那份报告的来源） |
| 跨平台：4 个平台账号，非主号读不到独立数据 | Steam(3) 主号；Xbox(1) 空号（2 场 PvE）、PSN(2) 零角色、Epic(6) 直接 404；`isCrossSavePrimary` 都指向主号 |
| 组件 1100 **读取不稳定** | 同一 URL 连续多次请求，会出现**整块 metrics 缺失**的响应（0 条），重试后恢复 402 条 —— 必须重试 + 不许把"空"当"0" |
| 游戏内"赛季"数字（KDA 2.02 / 赛季击败 3,522 / 多人竞技级别 4,645）不在上面任何一处 | 属**赛季口径**，需要 stats 接口的 periodType 或对应 metric，尚未实现 |
| `weapon_history` 是**全模式**（PvE+PvP）且按角色 | 榜首"挽歌 10,055 杀"是刷本数据，却被当成 PvP 结论使用 |

## 二、目标

一句话：**问什么口径，就给什么口径的数字，并且每个数字自带"来源 + 范围 + 模式"三个标签** —— 再也不出现"单角色数字冒充生涯""全模式数据冒充 PvP"。

## 三、数据来源盘点（各管什么）

| 来源 | 管什么 | 我们的现状 |
| --- | --- | --- |
| profile 组件 **1100 Metrics** | **游戏内计数器**（生涯/赛季的"官方数字"） | ❌ 未接 |
| profile 组件 **900 Records** | 成就/凯旋目标（含同一计数器的另一出口，实测也有 124,495） | ❌ 未接 |
| `GetHistoricalStatsForAccount` | 账号级统计（可拆现存/已删角色）、按模式 | ❌ 未接（只接了按角色的） |
| `GetHistoricalStats`（按角色） | 单角色、按模式明细 | ✅ 已接（但没标"单角色"） |
| `GetUniqueWeaponHistory` | 武器使用（**全模式**） | ✅ 已接（没标全模式） |
| PGCR 逐场 | 最近 N 场的真实 PvP 明细（含武器击杀） | 仅单场查询 |

## 四、分阶段

### P0 接上"游戏内计数器"（本体，收益最大）

- `profile_components` 新增 `METRICS = [1100]`（并登记调用点钉桩）；Manifest 白名单加 `DestinyMetricDefinition`，提供"hash → 名称/描述"解析；
- 读取实现**必须重试**（实测有空响应），拿到空时**如实报 `unavailable`**，不许当 0；
- 输出一条"生涯计数器"清单：`{metric_hash, name, description, progress, completion_value}`，
  至少覆盖 PvP 相关（`Opponents Defeated` 等），全量 402 条按数值降序可选给。

### P1 stats 三档并存 + 标注

- `stats`/`career` 默认改为**账号级**（`mergedAllCharacters.results.<group>.allTime`），
  payload 分三块：`stats.existing`（现存角色）/ `stats.deleted`（已删角色）/ `stats.total`（账号级）；
- 显式传 `character=` 才给单角色，且必须标 `scope: "character"` + 角色名；
- 求和类字段与取最大值类字段（`longestKillSpree`/`bestSingleGameKills`）分开处理，标 `aggregate: "sum" | "max"`。

### P2 游戏计数器与统计接口的关系写进契约

同一件事会出现两个数（124,495 vs 107,106），**两个都给，各自带来源**：

```
game_counters:  [{source: "profile.metrics", metric_hash: 811894228, name: "Opponents Defeated", progress: 124495}]
stats.account:  [{source: "GetHistoricalStatsForAccount", scope: "account", existing: 50622, deleted: 28242, total: 107106}]
```

并在 `warnings` 里说明差异原因（计数器从 S1 起累计，含统计接口已不再列举的旧角色）。

### P3 按模式与周期统计（试炼 / 铁旗 / 竞技 / 智谋 / 熔炉；生涯 vs 赛季）

**问题**：用户要"按模式分开看"，而三条口径不同的计数器**同名叫「已击败对手」**
（生涯 124,495 / 试炼 10,696 / 赛季 3,522），光看名字分不出来。

**已实采的两条分类依据**：

| 依据 | 能分什么 | 分不了什么 |
| --- | --- | --- |
| Manifest 层级链（`DestinyMetricDefinition.parentNodeHashes` → `DestinyPresentationNodeDefinition`） | 模式**家族**：熔炉竞技场 / 奥斯里斯试炼 / 智谋 各自成链 | 生涯 vs 赛季（两者同挂"熔炉竞技场"）；铁旗、竞技等级等子模式（也挂在"熔炉竞技场"下） |
| 计数器自己的**名字 + 描述** | 子模式与周期：「已击败铁旗对手数」直接点名；生涯那条描述写 "Tracks from Season 1 onward" | 靠人读，必须落成表 |

**做法（三步，每步可独立验收）**：

1. **建对照表（单一出处）**：`destiny_mcp/data/pvp_counters.py`（或同类低层模块）
   `{metric_hash: {mode, period, label_zh}}`，`mode ∈ {crucible, trials, iron_banner,
   competitive, gambit, …}`、`period ∈ {career, season, act}`；**只收录我们要暴露的那批**
   （先 20–40 条：熔炉生涯/赛季、试炼、铁旗、竞技等级、智谋、突袭…），每条都带实测证据
   （hash + 名字 + 描述 + 采集日期）；
   - 守门 `tests/test_pvp_counters_table.py`：① 每个 hash 的 `mode/period` 唯一且取值在枚举内；
     ② 表与 `counters` 输出的标签一致；③ **注入一次重复 hash / 非法 mode → 红**。
2. **`counters` 输出带标签**：每条加 `mode` / `period`（表里没有的给 `mode: "other"`,
   `period: null`，**不猜**）；支持按 `mode` 过滤（`counters(mode="trials")`）；
   - 真机验收：`mode="trials"` 只返回试炼那批，且「已击败对手」= 10,696；
   - `mode="iron_banner"` → 「已击败铁旗对手数」= 1,737。
3. **细节数据走 stats 接口的 `modes` + `periodType`**（计数器只有"累计值"，没有 K/D、胜率）：
   - 我们的 `get_historical_stats` 目前**这两个参数都没传**，所以只拿到 `allPvP` 一坨；
   - **第一步先核实参数取值**（`activityModeType` 的 Trials/IronBanner/Competitive 数值、
     `periodType` 的 AllTime/Season 枚举）——用官方文档 + 一次真机调用对照，写进
     `docs/reference/bungie_api.md`，**不许照印象填**；
   - 实现后 `stats` 支持 `mode=` 与 `period=`，payload 带 `mode`/`period` 标签；
   - 真机验收：试炼本赛季的 K/D 与游戏内（或机器人）显示一致；拿不到就报 `unavailable`。

**用户可见（P3 完成后）**：

| 问法 | 看到 |
| --- | --- |
| "我试炼打得怎么样" | 试炼生涯击败 10,696（计数器）+ 试炼 K/D / 胜率（stats，标 `mode=trials, period=season`） |
| "铁旗呢" | 「已击败铁旗对手数」1,737、铁旗等级 949（计数器，`mode=iron_banner`） |
| "我这赛季熔炉" | 赛季击败 3,522（`period=season`）+ 赛季 KDA（stats） |
| "智谋呢" | 储存萤光 11,264、击败入侵者 608、胜场 423（`mode=gambit`） |

**风险**：① 401 条里绝大多数是 PvE 计数器，**默认只给 PvP 那批**（表里没有的不猜模式）；
② `periodType`/`modes` 的取值必须先核实，猜错会静默拿错数据（违反"不许静默降级"）；
③ 上游可能新增/改名计数器 → 由守门测试发现而不是靠人巡检。

### P3b 武器榜的口径标注

- `weapon_history` 加 `scope: "all_modes"` + 话术直说"这不是 PvP 榜"；
- 真要做 **PvP 武器榜**：用 PGCR 逐场聚合最近 N 场（**明确标"最近 N 场"**，不是生涯）。

### P4 收口

- **新增一条 ADR（编号在落地时按当时的下一篇取）**：生涯数字以**游戏计数器（Metrics）为准**；统计接口只作明细；三档/三种来源不许混；
- `docs/reference/bungie_api.md` 增「Metrics vs Stats」一节（含 811894228 与 402 条、读取抖动）；
- 守门：三档数字的真机断言、payload 必带 `source/scope/mode`、`weapon_history` 必带 `all_modes`；
- 语料行：`docs/testing/TESTING_CORPUS_FULL.md` 增"生涯数字与游戏内一致"的核对行。

## 五、用户提问后能看到什么（**这就是最终要交付的样子**）

| 用户这么问 | 现在会看到（问题） | 改完后看到 |
| --- | --- | --- |
| "总结我的 PvP 数据" | 单角色数字（17,703 击败） | **游戏计数器：熔炉生涯击败 124,495（来源：profile.metrics，S1 起累计）**；并附统计接口口径：账号级 107,106（现存 50,622 + 已删 28,242） |
| "我什么枪比较猛" | PvE 数据冒充 PvP（挽歌 1 万杀） | **明确标注**："这是**全模式**（PvE+PvP）武器使用，不是 PvP 榜"；若要 PvP 榜则给"最近 N 场 PGCR 聚合"并标明范围 |
| "我这赛季 PvP 打得怎么样" | 无（或给生涯） | 本赛季 KDA / 击败 / 竞技等级，标 `period: "season"` |
| "我术士的生涯战绩" | 可能给成第一个角色 | 术士单角色 + `scope: "character"` + 角色名，且**同时**给账号级作对照 |
| "我试炼的战绩" | 生涯（混模式） | 试炼专属（模式过滤）+ 标明模式 |
| "为什么游戏里显示的数字和你给的不一样" | 无法解释 | 直接给出差异说明（计数器 vs 统计接口），带两个来源的实测值 |

## 六、风险与取舍

- **Metrics 读取抖动**：必须重试 + `unavailable` 标记；连续失败时降级到"只用 stats 并说明计数器没读到"，**不许静默给 0**；
- **计数器定义从 S1 起累计**：与我们能拆解的明细**天然不等**，文档与话术都要写死这一点；
- **402 条计数器全给会很长**：默认只给 PvP/玩家常问的那几条，全量按需；
- **PGCR 深度有限**：Bungie 只保留最近若干场，PvP 武器榜只能是"最近 N 场"。
