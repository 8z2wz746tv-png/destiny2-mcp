# ADR-027: 求解**不再剔掉**带执行前提的件 —— 先算属性层，把前提降级成方案上的标注

- Status: accepted
- Date: 2026-10-06
- Decision By: maintainer
- Scope: `build/solver.py`、`build/analyzer.py`、`build/models.py`（`BuildResult.requires_preparation`）、
  `services/build_preparation.py`、`services/build_service.py`、`services/build_projection.py`、
  `tools/_build_flow.py`、`tools/_preparation_note.py`
- 修订：[ADR-022](022-candidates-must-be-equippable.md) 的 **Decision 第 1 条**
  （"求解器只从没有 `execution_blocker` 的件里挑"）。ADR-022 的其余部分仍然有效。

## Context

ADR-022 的决定是：仓库件遇上满格、或异域与身上那件冲突时，**在求解阶段就把这件的候选资格判死** ——
理由是 2026-10-03 真机三次里前两次一个字节都没写进去，时间全花在"求解 → 预览 → 尝试 → 失败 → 回滚"。

那条决定解决的是"**临到写入才说不行**"，但它连**属性层结论**也一起弄没了：

- 件进不了候选池 → 规模闸门数的是剔过件的空间 → `analyze` 在 `build_service` 里**看到前提就整段短路**
  （`return BuildAnalysis(reason="；".join(blocked_by) + …)`）；
- 于是用户拿到的是"格子满了"这一句，**问不出"差多少"**。

**真机反例（2026-10-06，泰坦）**：头盔 / 胸甲 / 职业护甲三格都是 10/10 满，仓库里分别还有
10 / 14 / 20 件；用户要的是"移民号陨落 4 件套"——**那 4 件恰恰要靠仓库里的件才凑得齐**。
工具给的是一份写得很清楚的"三条前提"回执，`ceiling`/`single_stat` 全空：**属性层压根没算**。

用户的原话点破了取舍：*"按理说应该都是假如算完之后所有护甲确认之后才会转移到角色身上"* ——
**搬运本来就在确认之后**（`loadout_equipment_service._equip_local_unlocked` 的 Step 1），
所以"搬不进来"是**执行期的条件**，不该在求解期充当**资格**。

## Decision

**求解器不再按 `execution_blocker` 过滤件**；两条前提从"资格"降级成"条件"，用三处承接：

1. **求解 / 闸门**：`solver.solve` 与 `analyzer.estimate_combinations` 都取**全量**件
   （两侧必须数同一个空间，这是 `build/execution_feasibility` 的原有要求）；
2. **每套方案带 `requires_preparation`**：`BuildResult.requires_preparation`，内容就是
   **方案用到的那几件**上的原句（出处仍是 `execution_feasibility`，不另写判据）；
   候选行与 `find` 的 `warnings` 都带出去（`tools/_preparation_note.py`）；
3. **确认那一刻的复检照旧拦**：`build_execution_guard.recheck_confirmed_build` 拿**新鲜快照**
   逐件复检，前提没解决就 `execution_precondition_failed` + 同一句出路 ——
   **上游一次都不会被撞到**。这条是 ADR-022 留下的，且是这次敢放开的前提。

**话术随身份一起改**：`ladder_evidence.BLOCKED_NOTE` 不再说"属性层这次没有单独评估"
（现在**算过了**），改说"这些件要先准备，它们**已经算进**上面的结论"。

被否掉的方案：

- **保持剔除（ADR-022 原样）**：代价就是上面那条真机反例 —— 用户连"差多少"都问不出来，
  而"格子满"恰恰是**用户自己动手就能解决**的一件事，把它说成"配不出来"是错的归因；
- **只在 0 候选时补一段"理想区间"**（两套池子）：同一个问题两个答案，迟早被读混；
- **工具自动腾格（先腾后占）**：ADR-022 已经否过一次（要动账号两次、还要决定腾哪一件）。
  这次也**不做** —— 腾哪一件是玩家的取舍（分解不可逆、转移会改变仓库），
  方案里说清"要先准备什么"、由玩家自己做，比工具替他决定更安全；
- **事后过滤失败候选**：ADR-022 否掉的理由仍然成立，这次没有回头走那条路。

## Consequences

- **属性层结论回来了**：`ceiling`/`shortfall`/`single_stat_ceiling`/`precision` 都按**含那些件**的
  全量算，用户能直接看到"差多少"；
- **候选里会出现"现在还装不上"的方案** —— 所以 `requires_preparation` **必须在行上**，
  摘要与 `warnings` 里也要有一份；漏掉它就是回到 ADR-022 要避免的那种白跑；
- **代价**：求解空间变大（泰坦那一例：`combos` 从剔过件的数字涨到 57750），
  规模闸门更容易撞截断 —— 截断时照旧如实说 `truncated_by`；
- **守门**：`tests/test_build_execution_feasibility.py` 的六条按新口径重写
  （仓库件照常进池并被选中 / 冲突异域解得出来 / 闸门数全量 / 候选带 `requires_preparation` /
  `analyze` 不再短路）；三条"确认那一刻复检"的用例**一条没改** —— 它们是这次放开的保险。
- 改这条决定要同时改：`solver` 与 `analyzer` 的取件、`build_preparation` 的投影、
  `_preparation_note` 的那句话，以及上面那六条守门。
