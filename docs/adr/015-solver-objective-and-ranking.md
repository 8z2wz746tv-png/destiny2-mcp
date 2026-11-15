# ADR-015: 配装求解的目标与排序口径：上限是软的、调谐是局部的、可达是保守下界

- Status: accepted
- Date: 2026-09-22
- Decision By: maintainer
- Scope: `build/ranking.py`、`build/solver.py`、`build/tuning.py`、`build/constraints.py`、`services/build_*`、`tools/_armor_ladder.py`

## Context

用户报的原始症状是两句：「计算准确，但**属性不是最优解**」「**属性不够极限**」。
查下来不是数值算错，而是**目标函数与排序口径**的问题（根因与代码位置见
`docs/plans/SOLVER_OPTIMALITY_PLAN.md`）：候选排序有三套口径打架（求解器堆的优先级字典序、
`scorer.score` 的加权总分里"超出目标每点倒扣 0.5"、`find_build` 末尾 `completion_rate` 打头），
于是"多堆 10 点手雷"在评分里是负收益 —— 求解器只交及格卷。另外求解器**不认上限**
（想说"手雷别超过 100"没有表达方式），调谐额度（护甲上那些 +5/−5 的小格子）在有解时**没人去吃**。

对照物（DIM 的护甲六维计算器、开源的 `MIGO-OvO/d2-armor-solver`）给出的关键教训：
min/max 在那边是**筛选器不是目标**；排序是"按规则逐层比"；调谐属于搜索域的一部分；
"表爆 = 未知，绝不等于不可达"；逐项边际可达 ≠ 同时可达；局部补全 ≠ 全局最优。

**What changed**：这是把上面几条落成我们自己的口径（P0–P4 的实跑数据在计划文档里），
不是修正一个 bug。

## Decision

1. **上限（`stat_caps`）是软约束**：超了照样出解，但排到没超的方案后面、在结果的
   `max_violations` 里逐项标注（`{stat,label,actual,max}`），并且求解器不再往那一项堆模组。
   **不许**因为超上限就剪枝、也不许静默按 100 截断；不传 = 不限。上限键不认识、超 0–200、
   或上限低于下限 → 报错（不静默忽略）。
2. **排序只有一把尺子**：`build/ranking.goodness_key`（越大越好）——
   布尔违规位 → 每个优先项各一层（先"达没达"、再"差多少"）→ 普通层（先"没达标的个数"、
   再"总缺口"）→ 优先级值 → 封顶总和。求解器堆、剪枝用的乐观键、候选保留、展示排序**四处共用**。
   `score` 的含义降级为**封顶后的六维总和**（展示用，**不是**排序键）；
   `completion_rate` 只作展示。
3. **"要顶到极限"用提高下限 + 优先级表达**，不新增参数；求解器必须真的把免费额度吃干净
   （`build/tuning.local_tuning_improvement`：单件调谐邻域贪心，**每一步都过
   `validate_fixed_process_items`**）。克制边界：只报**净改动**；用户没有任何目标与优先级时
   **不动他的调谐**；贪心是**局部最优不是全局最优**，碰步数上限要如实标出来
   （`TuningPlan.exhausted`）。
4. **`reachable` 是保守下界**：逐项"已验证能到多少"，且**逐项可达 ≠ 同时达到** ——
   两句都写进 `reachable_note`。要精确的"同时上限"就走"把下限写成那个数再解一次"，
   不为此新增一条昂贵的枚举路径。
5. **没算完就不许下结论**：0 候选时必须自证枚举完了（`data.search = {exhaustive, combos,
   truncated_by}`）；没搜完时 `verdict.satisfiable = null` + "这次没搜完"，
   **不许**把预算/配额截断写成"不可行"。超时同理："没算完 ≠ 配不出来"。

## Consequences

- **破坏性**：候选顺序与 `score` 数值都会变（`score` 从"1000+ 的加权分"变成"200~1200 的总和"），
  已登记 `docs/COMPATIBILITY.md`。把 `score` 当阈值用的调用方要改。
- 有解时也会返回调谐改动（`tuning_changes` / `requires_tuning`），而调谐写入有条件
  （见 ADR-014），所以候选的达标六维**包含"要你自己动手改调谐"那部分**，话术必须说清。
- 局部贪心让"有解"这一路变慢（真机 +8s 量级），但它换来的是"不再交及格卷"；
  "严格解为空"那一趟（放宽目标复解 + 逐套精确复核）**没有动**，它现在是最大的单项成本，
  **不许**在没有基准数据的情况下动它（计划文档待办里有指向）。
- 上限是软的这件事意味着：**调用方不能把 `max_violations` 当硬失败**，只能当"这套不理想"。
- 这套口径的守门是四条测试文件（`test_build_ranking.py`、`test_build_stat_caps.py`、
  `test_build_budget_honesty.py`、`test_build_local_tuning.py`）+ 真机基线
  `tests/baselines/armor_responses/`（`tests/test_armor_baseline.py`）。
  改排序、改上限语义、改调谐策略都会踩到它们，**先看它们守什么**。
