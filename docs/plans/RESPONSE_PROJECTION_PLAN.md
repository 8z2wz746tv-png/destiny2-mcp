# 响应投影收口（Build 候选 + 重复武器）

一轮只做一件事：把**两个最大响应的默认出口**从"整包"改成"清单行 + 服务端引用"，
其余一律冻结。不是新架构——`Row-Form`（ADR-007）/ `*_payload.py` / `loadout_rows` /
`describe_candidate` 已经是仓库的既有做法，这轮只是把两个漏网的出口纳进同一套。

## 一、实测基线（2026-09-25，本机真机，同账号同参数）

| 出口 | 参数 | 现在 | 主要构成 |
| --- | --- | --- | --- |
| `build_assistant(find)` | hunter / 金装「快速装弹松身裤」/ 武器150·职业100·手雷70·近战70·超能80 | **21.5 KB**（2 套候选）→ 0.7.10 实测 **8.6 KB** | 每套 `build` **6.74 KB**（`build.items[]` 5 件 × 1.23 KB，装的是求解器内部字段）+ `canonical_build` 2.01 KB（19%）+ 其余诊断 ~0.5 KB |
| `inventory_assistant(duplicates)` | `limit=5` | **50.9 KB**（5 组）→ 0.7.10 实测 **15.8 KB** | 每组 `instances` 12.6 KB；每实例 1.81 KB，其中 **`perks` 1.64 KB（91%）** |
| `loadout_assistant(list)` | 已有闸 | 1.9 KB / 2 行 | （0.7.6 已完成：121 KB → 2.8 KB） |
| `weapon_assistant(analyze)` | `weapon_name="遗产"` | **68.6 KB** → 0.7.11 实测 **34.7 KB** | `inventory` 34.2 KB（含 `instances` 30.5 KB）+ `sockets` 21.1 KB + `weapon` 6.5 KB + `starside` 2.9 KB + `god_roll` 2.1 KB + `stats` 1.5 KB；信封 186 B |

两条结论：**① `canonical_build` 不是大头（19%），只砍它做不到 5 KB** —— 必须连
`build.items[]` 一起投影；**② duplicates 的脂肪在 perk 对象里**，不是在实例行上。
`weapon analyze` 的脂肪在"自己那些副本的逐件明细 + 插槽池"（81%），记着，下一轮再决定动不动。

## 二、这轮改什么

1. **`find` / `recommend` 默认只给候选行**：
   `execution_id` / `score` / 六维 / 金装 / 套装 / 是否要改调谐 / 这套的 5 件极简身份
   （`name`、`slot`、`item_instance_id`、`power`、`energy`、六维、调谐名、金装与套装名）。
   丢掉求解器内部字段：`tuning_option_hashes`、`base_roll_stats`、`archetype_*`、
   `armor3_roll_verified`、`roll_parse_error`、`icon_url`、`source_*`、`slot_key` 等。
   默认响应里**不再有** `build` 与 `canonical_build`（详情与执行都走 `execution_id`）。
2. **`execution_id` 升为正式引用协议**：TTL 10 分钟 → **30 分钟**；保持"玩家绑定 + 一次确认"；
   候选行自带五件身份，所以过期后仍能对玩家说清"刚才那套是哪五件"。
3. **不新增 intent、不加开关**：详情入口就是现有确认信封 ——
   `equip_build(execution_id, confirmed=false)` 给逐件 `items_preview`（**不消费候选**），
   玩家同意后 `confirmed=true` 执行。不加 `get_build_detail`、不加 `detail="full"`、
   不加 `find(compact=…)`。
4. **`duplicates` 把 perk 对象投影成判定依据**：每实例保留
   `instance_id` / `location` / `power` / `equipped` / `perks_complete` + **perk 名字与好坏结论**
   （PvE/PvP 结论），丢掉描述/图标/hash/插槽明细。这个 intent 的用途是"挑一把留"，
   不能压成库存索引。
5. **体积闸进语料**（同账号同参数为基线）：`find` ≤ 8 KB、`duplicates` ≤ 20 KB
   （`loadout list` < 20 KB 已存在）。

## 三、冻结（这轮一个字不碰）

信封五键、Pydantic Domain Model、`_compact.py`（不新建）、Weapon 全部投影、
`icon_url` 全局策略、二维/单字母 schema、YAML/Markdown、新 detail intent、SQL 层、缓存策略。

## 四、验收

- 字节：上面两条闸 + 记录前后数字（写回本文档）。
- 语义：`find` / `recommend` / `duplicates` / `equip_build(confirmed=false)` /
  `equip_build(confirmed=true)` 行为不变；`execution_id` 全程可回指服务端那份完整
  `CanonicalBuild`（确认信封里仍会给 `canonical_build`，原样回传那条路不断）。
- 真机：跑一遍语料（260 行）+ 两条写入链（`equip_build` 先看不确认 → 确认后写）。
- 版本：0.7.10；`docs/COMPATIBILITY.md` 登记（`build` / `canonical_build` 从默认响应消失 = 破坏性）。

## 五、落地结果（0.7.10，2026-09-25）

| 出口 | 前 | 后 | 闸 |
| --- | --- | --- | --- |
| `find` | 21.5 KB / 2 套 | **8.6 KB / 2 套** | 每套 ≤ 5 KB 且总量 ≤ 15 KB |
| `recommend` | 43.1 KB / 5 套（语料） | **7.3 KB / 2 套** | 同上 |
| `duplicates` | 50.9 KB / 5 组 | **15.8 KB / 5 组** | ≤ 20 KB |

真机写入链同时验过：`find` 候选行里的 `execution_id` → `equip_build(confirmed=false)` 给 5 件预览
与 `canonical_build` → 同一个 `execution_id` + `confirmed=true` 执行成功；`recommend` 的
`execution_id` 也照样能装（旧文档说会被拒，已纠正）。

## 六、0.7.11：武器分析（P0）与写入链分段（P1，只量）

`analyze` 投影：池子 21.1 → **6.6 KB**（只给愿单有结论的项）、副本明细 34.2 → **14.8 KB**
（去掉整块可换项），总量 68.6 → **34.7 KB**；语料闸 ≤ 45 KB。`weapon`/`stats`/`god_roll`/`starside`
合计 13 KB 是答案本身，不再动。

写入链分段（`scripts/benchmark_equip_chain.py --write`，幂等那次的实测）：

| 段 | 耗时 | 说明 |
| --- | --- | --- |
| `find` | 18.6s | 求解（与载荷无关） |
| 确认预览 | 0.8s | |
| 护甲快照 | 12.0s | 进写入链前的一次 profile 读 |
| 回滚快照 | 3.2s | 执行前状态 |
| 逐件搬运 ×5 | 11.1s | 每件之间还各插一次 0.4–0.5s 的 `profile` 抓取 |
| 批量装备 | 4.3s | |
| 模组预检 | ~0s | 缓存命中 |
| 回读核对 ×5–8 | 8.0s | 同步窗口重试 |
| **合计** | **43.9s**（幂等）/ 93–134s（真要写） | 全链路约 16 次小 `profile` 抓取（0.3–0.8s/次） |

真实用量（2026-09-25 当天 13 次调用）：服务端合计 **14s**，中位调用间隔 6.9s —— 查询类使用里
体感几乎全在模型回合；**写入链才是服务端占大头的那一半**（审计 28 条装备链：墙钟 p50 623s、
服务端 p50 258.6s、间隔 p50 286.9s = 46%）。下一个性能目标因此是**减少串行往返**
（缓存 profile 抓取、合并回读、跳过快照），不是继续压载荷 —— 但这要单独一轮 + 真机验，本轮只量。
