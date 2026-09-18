# ADR-006: PvP 武器榜只能是"最近 N 场"（上游给不了生涯口径）

- Status: accepted
- Date: 2026-09-18
- Decision By: maintainer
- Scope: `destiny_mcp/services/pvp_weapon_service.py`、`activity_assistant(intent="pvp_weapons")`、
  `destiny_mcp/data/activity_modes.py`、`skills/destiny2-mcp/references/routing.md`

## Context

用户要"纯 PvP 武器榜"（哪些枪在 PvP 里杀得多）。我们已有的 `weapon_history` 走
`GetUniqueWeaponHistory`，载荷里必须写 `scope="all_modes"` —— 因为**上游这个端点没有模式参数**，
返回的是全模式 PvE+PvP 合计，榜首通常是刷本用的枪。这在 PvP 语境下是错答。

**What changed** —— 三条"看起来能绕过去"的路，2026-09-18 全部实测排除：

| 方案 | 实测结果 |
| --- | --- |
| 给 `GetUniqueWeaponHistory` 加模式参数 | 端点没有该参数，加了也没用 |
| 用 `GetHistoricalStats*` 的武器聚合 | 聚合里没有模式维度，拆不出 PvP |
| 用游戏内计数器（profile 组件 1100） | 只有"击败对手数"这类总量，**没有按武器拆** |

同时摸清了唯一可行路径的两个关键事实：

- `GetActivityHistory` 的**单数** `mode=` 接受伞形模式：`mode=5`（熔炉竞技场）一次返回 250 场
  且**全是 PvP**（子模式 43/44/71/73/84/89/91…），`mode=84` 全是试炼、`mode=19` 返回子模式 43、
  `mode=63` 是智谋（`activityModeCategory=3`，PvPvE，**不在** `mode=5` 里）；
  **复数 `modes=` 被静默忽略**（返回 PvE 场次），只有单数有效。
- PGCR 的武器数据在 `entries[].extended.weapons[]`（`referenceId` +
  `uniqueWeaponKills` / `uniqueWeaponPrecisionKills`），**按玩家分行**；
  取 `entries[0]` 会把别人的枪算到用户头上（第一版探针真机踩到）。
- 成本实测（同一批 8 场）：串行 0.54 场/秒、并发 3 → 0.98 场/秒、并发 6 → 0.89 场/秒；
  真机整轮：10 场冷启 35 秒、命中缓存 14 秒（其中约 10–12 秒是进程启动 + Manifest 加载）。

被否掉的方案：**按角色分页翻到"生涯"**（每页 250 场，124,495 次击败对应的场次数以千计，
逐场 PGCR 不可能跑完，而且中途失败会给出一个"看起来是生涯、其实是残片"的数）；
**给用户一个"估计的生涯 PvP 武器榜"**（那就是编数字）。

## Decision

1. 新入口 `activity_assistant(intent="pvp_weapons")`，参数 `player_name` / `character` /
   `mode`（`pvp` 默认｜`trials`｜`iron_banner`｜`competitive`｜`gambit`）/ `count`（分析多少场，
   默认 10、上限 100）。
2. **口径必须在载荷里自证**：`scope="pvp_recent"`、`source="pgcr_aggregation"`、
   `window{newest,oldest,matches_requested,matches_analyzed,matches_failed}`、
   `mode_tally`（每场回报的子模式 + 官方中文名）、`characters`。
   时间窗**不许省**：真机上"最近 250 场"对某些角色跨两年多。
3. **只统计自己那一行**：按 `characterId` 精确匹配 PGCR 的 entry，匹配不到就跳过该场
   （宁少一场，也不猜）。
4. 失败如实：单场失败进 `failed_matches` + warning 并继续；一场都没取到**报错**，
   不返回空榜单（"没打成"和"没打过"是两件事）；历史失败只丢那个角色。
5. PGCR 落盘缓存（`~/.destiny_mcp/cache/pgcr/<instanceId>.json`，结算不可变）：
   不算账号写入，不需要 `confirmed`；缓存坏掉只等于没缓存。
6. `pvp_weapons` 与 `weapon_history` **并存、各自标口径**：前者是窗口、后者是全模式，
   两个来源不互相覆盖（`scope` 不同，调用方一眼能分）。

## Consequences

- 用户拿到的永远不是"生涯 PvP 武器榜"：想覆盖更长时间只能加大 `count`（上限 100 场，
  冷启约 1 场/秒），或者自己去游戏里查。这是上游限制，不是实现取舍。
- 首跑有真实等待（10 场冷启 35 秒，含 Manifest 加载），所以默认值取 10 而不是更多；
  缓存让重跑和换模式重查变便宜（14 秒，其中大部分是启动开销）。
- 新增两个文件与一处 intent 面，得同时维护：`data/activity_modes.py`（模式词，守门
  `tests/test_activity_modes.py`）、`services/pvp_weapon_service.py`（守门
  `tests/test_pvp_weapons.py`）、`tools/_weapon_usage_branches.py`（两个口径的分派）。
- 子模式名依赖 Manifest 的 `DestinyActivityModeDefinition`：Manifest 更新后若出现新模式，
  榜单会显示它的官方名（查不到才降级成 `模式<号>`），不需要改代码。
