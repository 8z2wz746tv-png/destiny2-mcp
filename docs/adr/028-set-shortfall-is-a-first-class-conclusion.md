# ADR-028: **约束凑不齐**是一等结论 —— 套装覆盖率优先于面向属性的话术

- Status: accepted
- Date: 2026-10-06
- Decision By: maintainer
- Scope: `build/set_feasibility.py`、`build/models.py`（`BuildAnalysis.infeasible_by`）、
  `services/build_analysis_guards.py`、`tools/_empty_message.py`、`tools/_armor_ladder.py`、
  `tools/_build_flow.py`

## Context

求解器只在每个组合上验 `set_count + wildcard >= set_bonus_count`，**验不过就丢**；
丢到 0 候选之后，"为什么"那套话术是**面向属性**写的：

> 各项目标单看都在单项上限之内，所以配不出来的原因不在「某一项堆不上去」，
> 而在同一套护甲要同时满足这些目标（还可能被金装、优先级与组合规模限制）

**真机 2026-10-06（泰坦）**：用户要「移民号陨落」4 件套。他**能穿**的这套只有
**3 个部位**（头盔 1 / 腿甲 2 / 臂铠 4；胸甲 0、职业护甲 0 —— 那 23 件里其余的是术士的）。
**4 件套要 4 个不同部位，所以这套配装在数学上不存在** —— 而工具回的是上面那句正确的废话，
连"套装"两个字都没提；`reason` 里那句"还可能被金装、优先级与组合规模限制"把唯一真正的原因
含糊带过了。

**信息其实全在手边**：`Armor.set_bonus`（这件属于哪套）、部位、以及"这个职业能不能穿"
（快照本来就按职业过滤过）—— 只差**数一遍**。

## Decision

**约束级的"不可能"和"执行前提"一样，要作为**一等结论**给出来**，而不是让面向属性的话术去套：

1. `build/set_feasibility.set_bonus_shortfall` 数一遍覆盖率：
   **每个部位最多穿一件**（万能插槽 `has_set_bonus_mod_socket` 也只占一个部位），
   所以"有这套件的部位数"就是**这次能凑到的上界**；
2. `上界 < 要求的件数` → 判定为**数学上不可能**，写成一句能执行的结论
   （哪几个部位有、缺哪几个、这是数出来的上限），落进 `BuildAnalysis.infeasible_by`，
   并**取代** `reason` —— 属性层有数也是白搭（一套都出不来）；
3. `上界 >= 要求的件数` → **一律不下结论**（那是属性/金装/优先级的事，交给 ladder）。
   缺数据也不下结论 —— 与 `execution_feasibility` 同一条纪律；
4. 出口三处都认它：`analyze`（`analysis.reason` + `infeasible_by`）、`recommend` 与 `find`
   的 0 候选摘要（`tools/_empty_message.py`）、阶梯表（`data.ladder.infeasible_by`）。

被否掉的方案：

- **让 ladder 去猜**（把"套装"塞进"组合规模限制"那句里）：那是含糊，不是结论；
  真机上它就把用户引向了"先腾格子"（而腾完仍然 0）。
- **穷举证明**（跑完 440 万组合再说"没有"）：慢 76 倍的空间换一句话，而覆盖率一遍就能数出来，
  且是**充要的上界**。
- **把件数当件数**：同一部位有 3 件这套**只算 1 件**（一个部位只能穿一件）——
  这是最容易写错的一处，守门里专门钉了一条。

## Consequences

- 真机同一组参数现在给的是："这套**凑不出 4 件**：这个职业身上只有 3 个部位有它
  （头盔、腿甲、臂铠），缺 胸甲、职业护甲。每个部位只能穿一件，所以这是**数出来的上限**"；
- **响应新增 `BuildAnalysis.infeasible_by` 与 `data.ladder.infeasible_by`**
  （与 `blocked_by` 井水不犯河水：那条是执行前提、腾格就能解；这条是约束不可能、得换约束）；
- 两处早退（超规模 / 套装凑不齐）合到 `services/build_analysis_guards.early_analysis`，
  `build_service` 的体量没有增长（787 == 上限）；
- 守门：`tests/test_set_feasibility.py`（覆盖率上界 / 刚好够不下结论 / 万能插槽算数 /
  没要求不说 / 同部位多件不撑大上界）+ `tests/test_build_execution_feasibility.py`
  里两条出口断言（`find` 与 `recommend` 的摘要要先说它）。
