# ADR-021: 默认出口只给「行 + 引用」，`execution_id` 成为配装候选的唯一引用

- Status: accepted
- Date: 2026-09-25
- Decision By: maintainer
- Scope: `destiny_mcp/tools/_build_flow.py`、`destiny_mcp/services/inventory_analysis_service.py`、`destiny_mcp/services/build_candidates.py`、`destiny_mcp/services/weapon_analysis_projection.py`

## Context

**What changed**：`find`/`recommend` 的默认响应一次把每套候选的**求解器内部模型**（`build`：
`tuning_option_hashes` / `base_roll_stats` / `archetype_*` / `armor3_roll_verified` /
`roll_parse_error` / `icon_url` …）与**可执行载荷**（`canonical_build`）整包发给模型。
真机实测（2026-09-25，同账号同参数）：`find` 21.5 KB / 2 套 —— `build` 6.74 KB/套（63%），
`canonical_build` 2.01 KB/套（19%）；后者才是"原样回传"的凭据，前者模型根本用不上。
`duplicates` 同样：`limit=5` 一次 50.9 KB，每实例 1.81 KB 里 **1.64 KB（91%）是 perk 对象**
（`plug_hash` + `icon_url` + `plug_category`）。

被否掉的选项：

- **全局 `exclude_none`/`exclude_unset`**：会把 `false`/`0` 这类本身是业务信息的状态抹掉
  （`is_equipped`、`locked`、`energy_used_by_other_mods`），而且"缺值给 None、不编 0"是本项目既有口径。
- **把投影集中到一个 `_compact.py`**：各域"哪些字段重要"是不同的知识，集中必然重复且互相打架；
  仓库已有 `*_payload.py` / `loadout_rows` / `describe_candidate` 的按域做法，这轮沿用。
- **删掉信封里的空 `candidates`/`next_actions`/`warnings`**：实测 54 B/次 = 语料总字节的 **0.49%**，
  而这三个键是语料 sweep 组对 110 个 intent 逐个断言的冻结信封 —— 不值。
- **加 `detail="full"` 开关 / 新增 `get_build_detail` intent**：两条路都要维护，且"哪条是默认"会长期含糊；
  详情入口本来就有（确认信封）。
- **单字母键 / 二维数组**：省的是几十字节，赔的是模型对 `n`/`p`/`s` 的理解成本；
  仓库在活动统计上已经验证过"语义键行式"可维护。

## Decision

1. **`find` / `recommend` 默认只给候选行**：`execution_id`、`score`、`completion_rate`、六维、
   金装、套装、`requires_tuning`、`tuning_changes`（只留中文名与六维净变化）、以及这套是哪五件
   （名字、部位、实例 ID、光等、能量、六维、调谐名）。**`build` 与 `canonical_build` 不再出现在默认响应里。**
   行只写一次口径说明（`builds_note`）+ 一条 `next_actions`，不逐行重复提示。
2. **`duplicates` 把 perk 压成判定依据**：每实例 `instance_id`/`location`/`power`/`equipped`/
   `perks_complete` + `perks[] = {name, slot}`；组级与 perk 级的 `icon_url`、`plug_hash` 不进默认响应。
   不引入"好坏结论"字段 —— 现在没有这个数据，不编。
3. **`execution_id` 升为正式引用**：TTL 10 → **30 分钟**（"给玩家看 → 等回话"常超 10 分钟），
   保持玩家绑定与"一次确认只用一次"。候选行自带五件身份，所以过期后仍能对玩家说清刚才那套是什么。
4. **`equip_build` 不新增 intent、不加开关**：详情与执行都走
   `equip_build(execution_id, confirmed=false)` → 逐件 `items_preview` + `canonical_build`（**不消费候选**）
   → 玩家同意 → `confirmed=true`。确认信封里仍给整块 `canonical_build`，所以"原样回传"那条路没断。

5. **`weapon_assistant(intent="analyze")` 同样只给"值得看的"**：定义级插槽池默认只给本地愿单
   有结论的选项（一栏一个都没有时退回前 6 项并说明），副本的"可换项"明细（每件 9 KB）默认不带 —— 
   那是 `compare` 的活。完整池子走 `perk_pool`。

## Consequences

- 真机（2026-09-25，同参数）：`find` **21.5 → 8.6 KB**（2 套）、`recommend` **43.1 → 7.3 KB**、
  `duplicates` **50.9 → 15.8 KB**（5 组）、`weapon analyze` **68.6 → 34.7 KB**；`equip_build` 用行里的 `execution_id` 先看不确认（5 件预览 +
  `canonical_build`）再确认写入，整链通过。**顺带纠正一条假文档**：旧语料说"`recommend` 的候选会被拒"，
  实测两个 intent 的 `execution_id` 都能装。
- **破坏性**：默认响应里 `build` / `canonical_build` 消失（登记在 `docs/COMPATIBILITY.md`），
  语料与 `TESTING_CORPUS_FULL.md` 的行按新读法改写。
- 代价：`_build_flow.py`（行工厂，366 行）与 `inventory_analysis_service.py`（`duplicate_rows`，617 行）
  各自登记了体量上限；`assistants.py` 不再增长（导入提到模块级）。
