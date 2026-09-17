# ADR-005: 生涯数字以游戏内计数器为准，统计接口只作明细，三档三来源不许混

- Status: accepted
- Date: 2026-09-18
- Decision By: maintainer
- Scope: `destiny_mcp/activity_stats.py`、`services/activity_service.py`、`services/activity_counters_service.py`、
  `bungie_stats.py`、`tools/_stats_branches.py`、`tools/_counters_branches.py`、`data/pvp_counters.py`

## Context

用户拿游戏内（与第三方机器人）的"熔炉生涯击败"跟我们的输出对照，发现差了几万。查下来不是算错，
而是**同一个概念有三个口径不同的数字**，我们此前只给了最小的那一个，还没标它是谁：

| 来源 | 熔炉生涯击败 | 范围 |
| --- | --- | --- |
| `profile.metrics`（组件 1100，`811894228`） | **124,495** | 游戏内那个计数器，自 S1 起累计、含已删角色 |
| `GetHistoricalStatsForAccount`（账号级 `mergedAllCharacters`） | **78,864** | 上游还列举得出的角色（现存 50,622 + 已删 28,242） |
| `GetHistoricalStats`（按角色） | 17,703 / 22,279 / … | 单个角色 |

**What changed**（相对 0.4.7 及此前）：

1. **发现统计接口分三块**：`mergedAllCharacters` **已经包含**已删角色（8 条角色之和 78,864
   = 现存 50,622 + 已删 28,242），`mergedDeletedCharacters` 是其中那 5 条的**明细**。
   本计划早期版本写的 107,106 就是把这 28,242 又加了一遍（等于把已删角色算两遍）。
2. **`stats` 此前默认给"第一个角色"的数字**（17,703），既不是生涯、也不是账号级，且没有标注。
3. **统计接口没有赛季周期**：`periodType` 实测只有 None/Daily/AllTime/Activity，传 3（Activity）
   在按角色端点上直接 500；`modes=` 只在**按角色**端点上生效，账号级端点会静默忽略它。
4. 组件 1100 读取会抖动（整块 `metrics` 缺失，重试后恢复），已在 0.4.7 之后接上并带重试。

## Decision

1. **生涯/赛季的"官方数字"以游戏内计数器（`profile.metrics`）为准**；统计接口只作明细
   （K/D、效率、胜率、按模式、按角色）。两个来源**同时给、各带 `source`**，
   差值写进 `warnings` 并说明"这不是我们能拆出来的部分"。
2. **`stats` 默认账号级三档**：每行给 `existing`（现存角色，我们按合并语义重算）/
   `deleted`（上游 `mergedDeletedCharacters` 明细）/ `account_total`（上游 `mergedAllCharacters`，
   **已含已删角色**）。`account_total` 不是前两者之外的第三个数，也**不允许**把已删角色再加一遍。
   单角色只在显式传 `character=` 时给，并标 `scope="character"` + 角色名。
3. **合并语义逐项声明**：`aggregate ∈ {sum, max, min, derived, none}`。比值类（K/D、效率、
   胜率、KDA）**不许相加**，只能按公式从分量重算（公式与真机核对见 `activity_stats.DERIVED_STATS`）；
   比不出的（均值、枚举）给 `null`，不编。payload 顶层的 `aggregation` 说明三个档位是上游合并的
   （`upstream_merged`）还是我们自己合的（`computed`）。
4. **上游没有的口径如实报 `unavailable`**：`period="season"/"act"` 直接报取不到并指向
   `counters`，**不降级成生涯**、也不去试会 500 的 `periodType=3`。
5. **按模式的账号级统计 = 逐角色取 + 自己合**（`modes` 只在按角色端点上生效），
   标 `aggregation="computed"`；模式数值只从 `data/pvp_counters.MODE_ACTIVITY_TYPES` 取
   （`ACTIVITY_MODES` 里的 `allpvp=9` 是错的，实测 500）。
6. **`weapon_history` 标 `scope="all_modes"`**：上游 `GetUniqueWeaponHistory` 没有模式参数，
   它是"这个角色的全模式武器击杀"，**不是 PvP 榜**；真要 PvP 武器榜只能拿 PGCR 聚合最近 N 场
   （另做，且必须标"最近 N 场"）。

**被否掉的选项**：

- **只用计数器**：它只有累计值，没有 K/D、胜率、按模式明细 —— 那些只能统计接口给；
- **只用统计接口**：它的生涯数字与游戏内显示不一致（用户看到的就是这个差），且没有赛季；
- **把两个数相加或相减"凑"出游戏内数字**：差值来自上游不再列举的旧角色，方向与数量都不可验证；
- **`period="season"` 试一次 `periodType=3`**：实测 500，属于"重试会 500 的参数"，不是探索；
- **账号级按模式直接用 `modes=`**：上游静默忽略，会安静地拿回全模式数字（最危险的一种错）。

## Consequences

- `stats` 的载荷变重（131 行 × 三档 + 标签），且**破坏性**改了默认口径与行形状
  （账号级行没有 `value`，旧键一个不留）—— 已在 `docs/COMPATIBILITY.md` 登记。
- 每次 `stats` 多读一次组件 1100（可选数据）：读不到只降级成 `warnings` + `counters_unavailable`，
  统计接口的结果不受影响；
- 账号级按模式要 N+1 次请求（1 次账号级拿角色清单 + 每角色 1 次），已并发；
- 口径散落在五处（本 ADR、`docs/reference/bungie_api.md`、`docs/plans/PVP_STATS_PLAN.md`、
  `tests/`、`docs/testing/TESTING_CORPUS_FULL.md`），改任何一处都要同时改其余几处 ——
  `scripts/verify_career_stats.py` 是这条口径的真机验收脚本（8 条断言）。
