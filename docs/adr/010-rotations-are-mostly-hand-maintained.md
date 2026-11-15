# ADR-010: 周常轮换只在两处是官方数据，其余靠自维护周期表 + 锚点

- Status: accepted
- Date: 2026-09-21
- Decision By: maintainer
- Scope: `destiny_mcp/data/rotations.py`、`destiny_mcp/services/rotation_service.py`、
  `destiny_mcp/tools/_rotation_branches.py`、`world_assistant(intent="rotations")`

## Context

用户要"本周轮换"（特色突袭/地牢、夜幕/宗师、上维挑战、异域任务、泉源、遗失区域）。实测（2026-09-21）：

| 项 | 实测 |
| --- | --- |
| `/Destiny2/Milestones/` | 本周 **12 条**（09-15T17:00Z → 09-22T17:00Z）：10 条突袭/地牢 + 周常公会记忆水晶 + 净化；`activities[].activityHash` 与起止时间都有，**但 `challenges` 全空、`phaseHash` 全 null** |
| 组件 **204** `characterActivities.availableActivities[]` | 本周 **294 条**可用活动，其中日落/宗师 4 条：`切除: 宗师`（10 条词缀 + 掉落 `故我在`/`故我在催化`/`上维碎片`）与 `日落: 高级/专家/大师`（各自词缀与奖励）；**三个角色完全一致** |
| 遗失区域（专家） | **常驻列表**：游戏内「World Lost Sector」页按目的地列出 **27 个地点**（用户截图与 Manifest 逐条对上：有「专家」变体的地点正好 27 个；`空坦克` 只有传说/大师、`消息，第一/二/三部分` 一条难度变体都没有，都排除） |
| 遗失区域（传说/大师） | 里程碑里没有；组件 204 的 294 条里**一条都没有**；Manifest 那个「遗失区域」清单（hash `3142056444`，42 条，在**角色级**组件 202）是"打过/解锁了哪些"（本账号 42/42），**不是**"今天轮到哪个" |
| 上维挑战 / 异域任务 / 泉源 | 候选活动/名字在 Manifest 里齐，但**没有任何接口给"这周/今天是哪个"** |
| 社区工具 | Braytech 自己排表（它前端甚至把 `rotationLostSectors` 做成用户可填参数），官方没有一个机器可读的轮换源 |
| 游戏状态 | **已停更**：周期不再变化，锚点核对一次即长期有效 |

## Decision

1. **官方优先**：特色突袭/地牢走 `/Destiny2/Milestones/`；夜幕/宗师的打击、词缀与掉落走组件 204
   （`modifierHashes` + `visibleRewards`）。这两类在响应里标 `source="official"`。
2. **其余用自维护周期表**（`data/rotations.py`）：上维挑战 6 周、异域任务 7 周、泉源每日交替、
   遗失区域 31 个地点的顺序。每张表必须带 `anchor_week_start_utc`/`anchor_index` 与
   `verified_at`/`verified_against`（怎么核的），响应里标 `source="schedule"` 并把这些元数据一起给出去。
3. **没核对过的不猜**：遗失区域顺序表尚无锚点 → 只给候选名单 + 核对办法，**不给"今天是谁"**；
   上游没给打击名（名字是泛化的 `日落: 宗师`）时留空并说明，不拿难度当打击名。
4. 细节按上游原样：空 `modifierHash` 跳过、奖励 `quantity=0` 原样给、`{var:...}` 不插值。

被否掉的选项：

- **只做官方那半**：用户点名的六类里只有两类有官方数据，砍掉一半等于没做；
- **抓 Braytech/light.gg 的表当数据源**：那是别人的产物，引用可以、抓取代跑不合规矩；
- **从组件 202 的「遗失区域」清单推今天是谁**：它记的是完成状态（42/42），推不出轮换；
- **按 Manifest 的 `index` 猜顺序**：没有任何证据表明它等于游戏内轮换顺序。

## Consequences

- 周期表是**人工维护**的：游戏停更后基本一次性，但任何人改表都必须写 `verified_at`/`verified_against`；
  将来若上游给泛化名字或新增轮换，答案会自己显示"上游没给"而不是编。
- 响应里**必然并存两种口径**（official / schedule）：任何新入口都要标清楚，不许把表算的写成官方。
- 遗失区域要能答"今天是谁"，只差一次游戏内核对（地点名→补锚点）；在那之前它只以候选形态出现。
- 改这条决定要同时改：`data/rotations.py`、`services/rotation_service.py`、
  `tools/_rotation_branches.py`、`tests/test_rotations.py`、`docs/plans/ROTATION_PLAN.md`、
  `skills/destiny2-mcp/references/routing.md`。
