# 纯 PvP 武器榜计划（PGCR 窗口聚合）

一句话：**生涯口径的 PvP 武器榜上游给不了**，能给的是"最近 N 场 PvP 的武器击杀榜"——
所以这份计划的核心不是算法，是**口径怎么标才不骗人**。

真机实测日期：2026-09-18（账号 `OneTop丶Husky#6641`，Warlock `…046315` / Titan `…355779` / Hunter `…174687`）。

## 一、为什么不能直接要（实测）

| 想这么做 | 实测结果 |
| --- | --- |
| 给 `GetUniqueWeaponHistory` 加模式参数 | **端点没有模式参数**（现有代码注释记录了这条）→ 只能全模式 PvE+PvP，榜首多半是刷本枪 |
| 从统计接口拆 PvP 武器击杀 | `GetHistoricalStats*` 的武器聚合同样没有模式维度 → 拆不出来 |
| 用计数器（组件 1100）算 | 计数器只有"击败对手数"这类总量，**没有按武器拆** → 拆不出来 |

结论：**唯一的数据来源是逐场 PGCR 聚合**，因此天然带窗口（最近 N 场），不是生涯。

## 二、上游能给的（实测）

### 1. 活动历史支持按 PvP 伞形模式过滤 —— 参数是**单数** `mode`

| 调用 | 实测返回 |
| --- | --- |
| `mode=5`（Crucible 伞形） | 250 场**全是 PvP**，子模式分布 `{43:97, 73:64, 44:32, 84:27, 91:12, 71:6, 89:4, 31:4, 48:2, 81:2}`（Warlock） |
| `mode=84` | 5/5 场都是 84（奥斯里斯试炼） |
| `mode=19` | 返回子模式 **43**（铁旗占领）——伞形过滤、回报具体模式 |
| `mode=69` | 返回子模式 **37**（生存模式，竞技） |
| `mode=63` | 5/5 场都是 63（智谋，**不在** `mode=5` 里） |
| `modes=5`（**复数**） | **被静默忽略**：返回的是 PvE 场次 → 必须用单数，代码里要防呆 |

### 2. 能翻页，历史很深

Titan：`page=0` 250 场（2023-09-08 → 2025-12-08）、`page=1` 250 场（2023-05-22 → 2023-09-08）、
`page=2` 250 场（2022-11-27 → 2023-05-22），三页无重叠 → 单角色至少 750 场可拉。

### 3. PGCR 里有武器级数据，但**是按玩家分行给的**

`entries[].extended.weapons[]`，每条的 `values`：

```
uniqueWeaponKills            16.0
uniqueWeaponPrecisionKills   11.0
uniqueWeaponKillsPrecisionKills 0.6875
```

`referenceId` 是武器 hash。**坑**：`extended.weapons` 属于 `entries[i]`，
第一版探针取 `entries[0]` 就把别人的武器算到自己头上了 —— 必须按
`characterId`/membership 找到自己那一行（写进守门测试）。

### 4. 成本（同一批 8 场，真机实测）

| 并发 | 吞吐 | 单场大小 |
| --- | --- | --- |
| 串行 | 0.54 场/秒（1.8s/场） | 46–52 KB |
| **并发 3** | **0.98 场/秒** | 同上 |
| 并发 6 | 0.89 场/秒（再高没收益，像撞限流） | 同上 |

→ 取并发 3。**25 场 ≈ 25–30 秒，50 场 ≈ 50 秒**：这是要给用户看的时间成本，
必须配缓存（PGCR 不可变，缓存命中后是 0 网络）。

### 5. 中文模式名有官方出处，不用手写表

zh Manifest `DestinyActivityModeDefinition.displayProperties.name`：
`5 熔炉竞技场 / 43 铁旗占领模式 / 69 多人竞技PvP / 73 占领模式：快速游戏 /
84 奥斯里斯试炼 / 37 生存模式 / 94 赛雀联赛`。

**顺带抓出的 bug**：`services/activity_service.py:52` 的手写 `MODE_NAMES` 有错
——`69` 写成"猛攻"（官方是**多人竞技PvP**）、`9` 写成"全PvP"（9 不是模式，伞形是 5），
而且 43/44/73/91 这些真机跑出来的子模式根本没有，会显示"模式43"。本次一并改成走 Manifest。

## 三、打算怎么做

### 入口与参数

`activity_assistant(intent="weapon_history", mode="pvp", matches=25)`

- `mode` 不传 → **行为与现在完全一致**（`scope="all_modes"`，`GetUniqueWeaponHistory`），形状不变；
- `mode="pvp"`（伞形 5）/ `"trials"`(84) / `"iron_banner"`(19) / `"competitive"`(69) / `"gambit"`(63)
  → 走新的 PGCR 窗口聚合，`scope="pvp_recent"`、`source="pgcr_aggregation"`；
- `matches` 默认 25、上限 100（成本见上）；`character` 可限定单角色。

不新开 intent：用户问的是同一件事（"我的武器榜"），两个来源各自在 `scope`/`source` 里自报家门，
与 `stats` 并列"计数器 + 统计接口"两个来源的做法一致。

### 数据流

1. 每个角色拉一次 `GetActivityHistory(mode=<伞形>, count=250, page=0..P-1)`；
2. 合并、按 `instanceId` 去重、按 `period` 倒序，取**全局最近 N 场**（不是每角色各 N 场）；
3. 并发 3 拉 PGCR（先查缓存），按自己的 entry 聚合武器击杀；
4. 汇总成榜单。

### 载荷

```
data.weapons[]      name / item_hash / icon_url / kills / precision_kills /
                    precision_rate / matches_used / kill_share / kills_per_match
data.scope          "pvp_recent"
data.source         "pgcr_aggregation"
data.mode_group     "pvp"（中文名 + 伞形 modeType）
data.matches_analyzed / matches_requested
data.window         {"newest": ..., "oldest": ...}   ← 必给
data.mode_tally     [{"mode": 73, "name": "占领模式：快速游戏", "matches": 12}]
data.characters     参与统计的角色与各自场次
warnings            "这是最近 N 场、不是生涯"；窗口跨年时直说年份
```

**为什么 `window` 必给**：真机上"最近 250 场"对 Hunter 是 2023-09-08 → 2025-12-08，
跨了两年多。只说"最近 N 场"会让人以为是最近几周 —— 得把起止日期摆出来。

### 缓存

`~/.destiny_mcp/cache/pgcr/<instance_id>.json`（PGCR 不可变，永久有效）；
只读路径上的缓存写入不算账号写入，不需要 `confirmed`。

### 失败与降级

- 某角色历史拉不到 → 只丢这个角色 + `warnings`，其余照算；
- 单场 PGCR 失败 → 记 `failed_matches`，`matches_analyzed` 如实减，不假装满额；
- 成功场次为 0 → `ok=false` + 中文说明"这个模式最近没有比赛记录"；
- 上游 5xx/限流 → 按上游故障报，不循环重试。

## 四、分阶段

| 阶段 | 内容 |
| --- | --- |
| P1 | 接线：`mode=pvp` 单角色/账号级窗口聚合 + 并发 3 + 缓存 + 标签与 warnings |
| P2 | 翻页取更多场次（`page`）、`matches` 上限与时间成本提示 |
| P3 | 模式细分（trials/iron_banner/competitive/gambit）+ `mode_tally` |
| P4 | 顺手修 `MODE_NAMES`：删手写表，模式名走 zh Manifest（连带修 history 的 mode_name 错标） |
| P5 | 守门测试 + 真机语料行 + 文档/ADR |

## 五、守门与验收

`tests/test_pvp_weapon_board.py`：

1. 上游武器字段清单当夹具（`uniqueWeaponKills` 等拼错即红）；
2. **必须取自己那一行**：造两行 entry 的假 PGCR，验证不会把别人的武器算进来；
3. `mode=` 单数 / `modes=` 复数：钉住"复数被静默忽略"，代码里禁止复数误用；
4. 缓存命中不重复请求（替身计数）；
5. `window` 与 `matches_analyzed` 必给，且 `scope != "all_modes"`；
6. 手写 `MODE_NAMES` 已删（扫描测试），模式名只从 Manifest 取。

真机语料加进 `scripts/run_corpus_pvp_rows.py`：`mode=pvp` 榜单非空且 `window` 在合理范围、
`mode=trials` 全是试炼、`modes=` 复数不再被代码使用。

ADR：`docs/adr/006-pvp-weapon-board-is-a-window.md` —— 记录"生涯 PvP 武器榜上游不可能，
只能给窗口口径"，连同被否掉的三个方案（统计接口、计数器、给 `GetUniqueWeaponHistory` 加参数）。

## 六、要你拍板的两点

1. **入口**：`intent="weapon_history"` 加 `mode=`（本计划）还是新开 `intent="pvp_weapons"`？
2. **默认场次**：25 场（约 25–30 秒，首跑）够不够，还是默认更小（10 场 ≈ 10 秒）+ 让用户说"再多分析点"？
