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

### P3 赛季口径 + 武器榜

- 赛季：stats 接口的 periodType（或对应 metric）→ 给"本赛季 KDA / 击败 / 竞技等级"；
- `weapon_history` 加 `scope: "all_modes"` + 话术直说"不是 PvP 榜"；
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
