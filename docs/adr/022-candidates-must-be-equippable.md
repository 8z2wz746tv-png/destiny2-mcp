# ADR-022: 求解器不许产出**注定装不上**的候选：执行前提在求解阶段判死

- Status: accepted
- Date: 2026-10-03
- Decision By: maintainer
- Scope: `destiny_mcp/build/execution_feasibility.py`、`destiny_mcp/build/models.py`、`destiny_mcp/build/solver.py`、`destiny_mcp/build/analyzer.py`、`destiny_mcp/build/process_types.py`、`destiny_mcp/services/build_service.py`、`destiny_mcp/tools/_build_flow.py`

## Context

2026-10-03 真机跑「社区配装 → 一次性装备」，三次里**前两次一个字节都没写进去**
（时间全花在"求解 → 预览 → 尝试 → 失败 → 回滚"上）：

1. **格满**：求解器挑了一件**在仓库里**的臂铠，而该角色臂铠格 **10/10 满** → 上游 500
   `DestinyNoRoomInDestination: There are no item slots available to transfer this item.`
   链路在写第一颗模组之前中止，**0 颗模组落地**；
2. **金装冲突**：同一批里要换上异域头盔，而角色身上还穿着异域胸甲（星火协议）→
   `equipStatus=1641 DestinyItemUniqueEquipRestricted` → 全量回滚，**0 颗模组落地**。

两条都是**求解阶段就能判死**的确定性事实，不是运气：

- 搬运**必须先于**装备（`loadout_transfer_step`：批量装备要求东西已经在角色身上），
  所以格子满时仓库件根本进不来；
- 批量装备是**一次上游调用**，先装哪一件由它决定（实测那次先算了异域头盔，
  而胸甲还穿着），所以"另一个部位还穿着同类异域"不能赌。

**What changed**：这两条判据要的账号事实其实**早就在手上**，只是没人读 ——
`Armor.source_location`（vault vs 角色）、已装备那几件的 `is_exotic`、
以及 Manifest 的 `DestinyInventoryBucketDefinition.itemCount` 减去该角色该桶的件数
（`used` 含正装备那件，与 `equip_planner` 同口径）。全部来自**同一次 profile 读取**，零额外请求。

## Decision

**两条执行前提在 `InventorySnapshot.from_profile` 末尾判一次，结论落在件上**
（`Armor.execution_blocker`，出处 `build/execution_feasibility.py`）：

1. 件在**仓库**里 + 角色对应格**确知已满** → 这一件这次装不上；
2. 件是**异域** + 角色正穿着**另一部位**的异域护甲 → 这一件这次装不上
   （同部位替换不算冲突）。

配套三条，都是"一个事实只写一次"的落地：

- **求解器只从没有 `execution_blocker` 的件里挑**（`solver.solve` 建候选表时过滤）；
  规模闸门（`analyzer.estimate_combinations`）与求解器数**同一个空间**；
- **0 候选必须说清是哪条约束卡的**：`SearchDiagnostics.blocked_by`（进 `find` 的
  `data.search.blockers` + `warnings`）、`BuildAnalysis.blocked_by`（`recommend`/`analyze`），
  话术由 `analyzer.execution_blockers` 生成 —— **不许静默返回空**，
  也不许把"装不上"念成"属性配不出来"（那会把下一步指到"降目标/反推待刷"上）；
- **读不到就不下结论**：桶定义缺失、或没按职业过滤（认不出唯一目标角色）时，
  一条都不判、一件都不拦（缺数据 ≠ 装不上）。反方向也钉住：判出来了就一定要拦
  （多算顶多让用户白清一格，少算会去撞上游）。

被否掉的方案：

- **"事后过滤失败候选"**（求解照旧、写入前再筛）：用户已经看过、确认过那份方案，
  临到写入才说"这套不行"就是那两次白跑本身；
- **在写入路径里加"先腾后占"的搬运编排**（把格子先腾出来再搬）：要动账号两次、
  还要处理腾哪一件，而"仓库件搬不进来"这件事本身在求解阶段就能避开；
- **一次批量装备里靠顺序绕过 1641**：批量调用的处理顺序不在我们手里（真机实测先算了异域头盔），
  赌顺序等于赌运气。

## Consequences

- `find`/`recommend` 返回的候选**一定是能装的**（格满时自动改用身上/背包里已有的件）；
  代价是候选空间变小 —— 真机里"臂铠格 10/10"会一次性砍掉该部位大半件数，
  用户可能觉得"怎么突然配不出来了"，所以 `blockers` 必须原样发出去（话术里给数字与出路）。
- `data.search.blockers` / `analysis.blocked_by` 是对外契约的新键（**只在非空时出现**，
  没被砍就不占响应）；`tests/test_build_execution_feasibility.py` 钉住这两个场景、
  "读不到不拦"、闸门同空间与话术口径。
- 阈值/顺序的边界写死在 `execution_feasibility` 里：以后要放宽（例如允许"先腾再搬"），
  改的是那里的判据 + `solver` 的过滤，而不是在写入路径里补一段重试。
- **不覆盖的**：`find` 与 `confirm` 之间账号状态变化（那一格被填满、换上了另一件金装）
  仍会在写入时撞上游错误。`snapshot_version` 刻意不含执行现场（见 `build/snapshot_version.py`：
  含了会让"捡到一件护甲"就作废手里的候选）。
  **这一条已由 [ADR-024](024-recheck-premises-before-writing.md) 接手**：写入前按当时的现场
  复检这两条前提（判据仍是这里的那一份），不再让上游 500/1641 来当判据。
