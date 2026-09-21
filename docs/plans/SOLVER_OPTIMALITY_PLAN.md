# 求解器"最优性与极限"改造计划

一句话：**现在的求解器算得准，但目标函数只奖励"刚好达标"**；这份计划把它改成"先证明能不能，
再按优先级把能拿的都拿到"，并且把"这套装备到底能到哪"如实报出来。

对照物（外部两家都读过了，证据在各自小节里）：DIM 的 armor 求解器、`MIGO-OvO/d2-armor-solver`
（v3.x，MIT）。两家在同一个问题上给了一致的答案，这份计划按他们的口径 + 我们现有的机器来排。

> 本文记**取舍与实测证据**；最终定下来的对外口径另写一条 ADR（落地时取当时的最大号 +1，
> 见 `docs/adr/README.md`），别把两件事混进同一个文件。

---

## 一、现象（全部是本次会话的真机数据）

用户报："属性不是最优解，而且不够极限。"

拿"赫沃斯托夫星火术士"那套（术士，异域 星火协议，套装 埃希恩记忆 4 件）当样本：

| 项 | 值 |
| --- | --- |
| 社区模板要求 | 生命 0 ｜ 近战 70 ｜ **手雷 100～200** ｜ **超能 80～100** ｜ **职业 70～100** ｜ **武器 100～200** |
| 那次求解输入的硬目标 | weapons 100 / grenade 100 / class 70 / melee 70 / super 80，`priority_stats=["grenade","weapons"]` |
| 求解器自报 | `completion_rate: 1.0`、`requires_tuning: false`、`tuning_changes: []` |
| 实际装出来的六维 | 武器 107 / 生命 7 / 职业 71 / **手雷 120** / **超能 110** / 近战 71 |

三件事一眼可见：

1. **每项都趴在底线上**：手雷 120 落在 100~200 的两成位置，武器 107（7%）、职业 71（3%）、近战 71（刚过 1 点）。
2. **超能 110 超过了模板上限 100 十点**，而我们全程没有报过任何"违规"——因为我们**根本没有"上限"这个概念**。
3. **免费的额度没用**：五件护甲各有一个 0 能量的调谐槽，谁都没动过。

### 那条"至少能多拿 10 点手雷"的算术证据

不动装备、不动模组，只把头盔和腿的调谐从 `+超能/−生命值` 换成 `+手雷/−生命值`
（插件存在、0 能量、`plugCategoryIdentifier` 就是调谐槽那一类）：

- 手雷 120 → **130**；超能 110 → 100（要求只要 80）；其余五项一字不变；
- **仍然满足那次请求的全部硬目标** —— 也就是说求解器交的解，在它自己的目标下就不是最优的。

调谐插件表（plug set 1155052024）实测共 32 条：`平衡调整(+1×6)` 1 条 + 30 条 `+X/−Y`（各 +5/−5）。
五件全换理论上限是 +25 手雷，但会撞到"职业刚好 71→66 < 70"这类约束，所以真实可拿的是十几点量级，
需要求解器自己算，不能靠人眼估。

---

## 二、根因（四条，逐条给代码位置）

### 1. 非优先属性的天花板被压成了底线

`destiny_mcp/build/solver.py:224-233`：

```python
if priority_indices:
    desired_max = [
        MAX_STAT if index in priority_indices else desired_min[index]
        for index in range(6)
    ]
```

只有 `priority_stats` 里点过名的属性拿到 `MAX_STAT`，其余**上限就等于下限**。这个 `desired_max`
被 `build/process_utils.py:413-430` 用来算 `max_added_stats`，于是那些属性的"还能加多少"恒为 0
——**剩余能量在它们身上一分都花不出去**。这是"刚好 120/107"的机械原因。

注意 `process_utils.py:401` 的注释写着 `desired_max_stats: Maximum stat targets (0 = ignored)`：
**机器本来就有"上限"这个东西**，是我们喂错了值。

### 2. 评分反过来奖励"贴底线"

`destiny_mcp/build/scorer.py:52-66`：

- 超出目标的部分：**每点罚 0.5**（且只对非优先属性算）；
- 六维总分：**每点只加 0.1**。

净效果就是"越贴近下限分越高"。而 `services/build_results.py:130` 的
`completion_rate = 达标条数 / 目标条数`，候选排序又以它打头 —— 一层层下来，
"多堆 10 点手雷"在整套评分里是**负收益**。

外部对照：`d2-armor-solver` 的 `docs/priority-rules-audit.md` 自查出同一族的病
（一个高优先级 `≤100` 的属性取 80 时反被扣分，结果"规则说别超过 100，排序却把它往 100 推"），
它的结论原话是 **"达标与贴近输入值不是一回事"**，并明确要求"统一搜索评分、下界剪枝、
库存/升级比较器与展示排序，**不要仅调大数权重**"。

### 3. 调谐没进搜索域

`services/build_tuning.py` 的调谐只在**目标没达成**时才被当成补救手段动用（`requires_tuning`），
所以这次的 `requires_tuning=false` + `tuning_changes=[]` 是"设计如此"而不是巧合。
DIM 的 `mappers.ts:135-183`（`buildTuningVariants`）和 d2-armor-solver 的
`docs/complete-optimization.md`（"Every eligible piece can leave its Tuning socket empty"）
都把调谐（含"空着"）当作搜索域的一部分。

### 4. 两套排序口径并存（同一个事实写了两遍）

- 搜索内部：`solver.py:49-63` 的 `_ranking_metric` = **优先级字典序在前、总和在后**；
- 最终展示：`scorer.py` 的加权分 + `build_results.py` 的 `completion_rate` 打头。

两者对同一个问题给不同答案。这正是 d2-armor-solver 那份审计的结论
（"只更改一个 UI 排序函数不能统一搜索、剪枝、候选保留与最终展示"）。

### 附带发现：已有的"能到哪"机器全都在无解路径里

- `build/analyzer.py` 的 `_max_possible_stats`：逐属性上限（"把点全堆这一项"）；
- `tools/_armor_ladder.py`：`ceiling`、`single_stat_ceiling`、`shortfall`、`trials`、`tuning_first`；
- `build/process_utils.py:452` 的 `update_max_stats`：求解过程中已经算出可达值，但**没进响应**。

也就是说"报可达区间"这件事**不需要新的数学**，主要是把它从"只有无解时才跑"升格成
"有解时也给"，并按 DIM / d2-armor-solver 的既有纪律配一句免责
（**逐项可达 ≠ 同时可达**，我们语料第 382/396 行已经钉了这句话，别丢）。

---

## 三、口径决定（要拍板的五件事，都带我的推荐）

### 决定 1：目标有三种语义，不许再混

| 语义 | 含义 | 现状 |
| --- | --- | --- |
| **下限** | 硬约束，必须满足 | 有（`*_target`） |
| **上限** | 超了算违规（会被排到后面并标注），但**不阻止出解** | **完全没有** |
| **偏好** | 都达标之后，按这个顺序尽量高 | 有名字（`priority_stats`），但被 `desired_max` 的写法废掉了 |

**推荐**：三个都保留、各司其职。区间 `100~200` = 下限 100 + 上限 200；"尽量高"用偏好顺序表达。
**被否**：把区间上限当硬约束（模板超了不该"无解"，游戏里超上限也没有惩罚）；
把"只要给了区间就自动冲到上限"当默认（DIM 和 d2-armor-solver 都不这么做，用户没要就别猜）。

### 决定 2：排序口径统一成一份，逐字段字典序，删掉浪费惩罚

按 d2-armor-solver 的 9 元组（`src/core/stat-ranking.mjs`）：

```
[有没有任何显式规则没满足, 高优先未达标个数, 高优先缺口, 中…, 低…, 普通…]
```

要点：① 第一位是**布尔**，不是跨层加权；② 同层**先比未达标个数、再比缺口**；
③ 字典序逐字段比 → **低优先级的富余不能补贴高优先级的缺失**。
之后才比刷取成本 / 稳定性。

**推荐**：照抄这个结构，并且**搜索、剪枝下界、候选保留、展示四处共用同一个比较器**。
**被否**：继续用加权分再调权重（d2-armor-solver 试过，明确写了"不要仅调大数权重"）。

### 决定 3：阶段分离 —— 预算用尽永远不能产生"无解"

`feasibility（穷尽，不许被 top_n/超时截断）→ ranking（预算内排序取前 N）→ refinement（剩余时间精修）`，
响应里带 `coverage.complete`。d2-armor-solver 原文：**"no limit can create an infeasibility proof"**、
**"截断不能证明不可行"**。

**推荐**：照做。我们已经有 `analyze` 在没有验证过的情况下断言过"没有合法组合"的前科
（0.1.13 修的），这条守门是防它复发。

### 决定 4：不给偏好时怎么办

**推荐**：不猜 —— 只保证达标，**但响应里必须给出每属性的可达区间**，让用户一句话就能要"顶"。
理由：没要求就不优化（本仓库既有原则），同时把"能到哪"摆在眼前，用户不会再觉得工具"不够极限"。

### 决定 5：调谐进搜索，但分两条路走

全枚举调谐域是 32 种/件（含"空着"），五件就是 32⁵ —— 术士本身已经要 ~190s，不能硬上。

**推荐**：
- **便宜路径（默认走）**：达标之后按偏好顺序，逐件把调谐换成 `+偏好/−代价`、把剩余能量吃满，**不枚举**；
- **昂贵路径（显式要"顶到极限"或要证明某条线可达时才走）**：枚举调谐域（含空着）做可达性判定。

**被否**：默认全枚举（术士会撞预算）；把调谐留给用户自己进游戏改（那正是现在的问题）。

---

## 四、阶段计划

每阶段独立可交付；**P0 不做完不许动 P1 之后的任何一步**（"声称之前先量"）。

### P0 先量：基线 + 可复现基准

- **交付**
  - `scripts/benchmark_build_solver.py`：对"当前 checkout 或一个隔离的 git ref"跑同一批用例，
    逐行输出 JSON（子进程跑，父进程聚合），不写对方的目录。
  - `docs/benchmarks/build-solver-<label>.json`：落盘的结果（含 node/硬件信息）。
  - `scripts/capture_weapon_baseline.py` 增加 `build` 面（或单开 `capture_build_baseline.py`）
    + `tests/baselines/build_responses/` + `tests/test_build_baseline.py`：求解器响应目前**没有**
    基线 diff 闸门（护甲/武器都有），P1 之后要动排序，没有对照物等于摸黑。
- **指标**：`wallMs`、`status`、`candidateCount`、`targetsMet/targetsTotal`、`finalStats`、
  `coverageComplete`、工作量计数（评估次数 / 访问状态数；现在没有就先加内部计数器）。
- **口径**：配对**交替顺序**跑三次取中位；无解时"首次解耗时"记 `null` **不是 0**；
  只给**确定性工作量**对比，秒数只作参考；文档里明文写"哪些历史数字不作为证据"。
- **验收**：一条命令能复现"手雷 120 / 武器 107 / 超能 110"和术士 `find` 的耗时区间。
- **风险**：加计数器本身可能拖慢热路径 → 计数器默认关，基准时打开。

### P1 诚实性：阶段分离 + `coverage.complete`

- **改**：`build/solver.py`（feasibility pass 与有界排序分开）、`services/build_service.py`、
  `services/build_results.py`、`tools/_build_flow.py`、`destiny_mcp/error_codes.py`（预算用尽要有自己的码）。
- **守门**：`tests/test_build_budget_honesty.py` —— 注入极小预算，断言响应是"没搜完"而
  **不是** `satisfiable=false`；注入超组合规模，断言不是"无解"。
- **验收**：语料第 382/396 行的 `verdict.satisfiable=false` 只在真穷尽时出现；
  `analyze` 的 `precision="not_computed"` 语义不退化。

### P2 目标语义：上限 + 可达区间

- **改**：`tools/_param_contracts.py` / `_requests.py` / `_param_docs.py`（新参数 `*_max` 进参数表、
  进 intent 契约）、`build/models.py`（`BuildConstraints`）、`build/solver.py`、
  `build/process_utils.py`、`tools/_armor_ladder.py`、`services/build_results.py`、
  `skills/destiny2-mcp/references/routing.md`、语料、`docs/COMPATIBILITY.md`。
- **有解时也给可达区间**：复用 `analyzer._max_possible_stats` 与 `_armor_ladder.ceiling`，
  带 `single_stat_ceiling` 与"**逐项可达 ≠ 同时可达**"的既有话术（语料已钉）。
- **守门**：`tests/test_build_target_ranges.py` —— ① 超上限算违规且有明确标注；
  ② 上限进剪枝界（能被剪掉的组合不许全枚举）；③ 不传 `*_max` = 不限，不许悄悄按 100 截断
  （200 才是游戏上限，`build/constants.py` 的 `MAX_STAT`）。
- **验收**：把模板六项**原样**传进去，能报出"超能 110 超过上限 100"；不再需要人工把区间拍成下限。

### P3 排序口径统一

- **改**：新增 `build/ranking.py`（9 元组比较器 + 缺口定义），`build/scorer.py` 收敛成它的调用方
  或整段删掉；`build/solver.py`、`services/build_results.py`、`build/farm_target.py` 全部换成同一处。
- **守门**：`tests/test_build_ranking.py` —— ① 跨路径差分：同一个候选在
  solver 内部、`build_results`、`farm_target` 三处的相对位次一致；
  ② 注入"低优先富余补贴高优先缺失" → 必须红；③ d2-armor-solver 审计里的现成反例
  （武器高优先时 `[武器100/生命80]` 必须赢 `[武器90/生命100]`）。
- **验收**：`completion_rate` 不再参与候选排序（降级为展示字段），"浪费惩罚"从评分里消失。
- **风险**：**这是破坏性变更**（候选顺序会变）→ 语料 diff + `docs/COMPATIBILITY.md` + CHANGELOG，
  并且必须 P0 的基准在手才动。

### P4 把免费的花干净

- **便宜路径**（默认）：达标后按偏好顺序吃满调谐与剩余能量。
- **昂贵路径**（`顶到极限` / 可达性证明）：枚举调谐域（含"空着"）。
- **`TUNING_DOMAIN_ID` 进缓存/证明身份**：d2-armor-solver 的 `tuning-domain.mjs` 就是这个教训
  —— "调谐合法域一变，旧缓存里基于**更小**域得出的否定结论绝不能复用"。我们现在没有否定缓存，
  但 P1 之后会有"没搜完/不可行"的判定，先把身份位留出来。
- **守门**：`tests/test_build_no_free_lunch.py` —— 对返回的每个解做**可检测的不变量**检查：
  不存在"单件调谐替换即可提升偏好属性、且不破坏任何硬约束"的情况。
- **验收**：同样输入下那套配装手雷 ≥ 130；模板六项原样传入时能继续往上顶到可达上限。

### P5 响应 · 话术 · 文档

- 新字段（`*_max`、`reachable`、`coverage.complete`、`violations`）登记 `docs/COMPATIBILITY.md`；
- 写一条 ADR 记最终口径；`README.md`、`skills/destiny2-mcp/references/routing.md`、
  `docs/testing/TESTING_CORPUS.md` 的 build 章、`CHANGELOG.md` 跟上；
- 工具层话术：把"目标没达成"与"上限被超过"分开说；`analyze` 的 `precision` 语义保持不变。

### P6 真机验证

- `scripts/verify_build_ceiling.py`（只读，仿 `verify_armor_mod_sockets.py` 的写法）：
  给一组约束，打印每属性可达区间 + 一个"顶到 X"的解，并逐件回读现实装出来的六维。
- 真机跑两遍：① 那套配装原样重解，看手雷能不能到 130+；② 模板六项原样传入，
  看超能上限 100 会不会被报违规。
- **验收**：改动前后的六维对照表 + 耗时对照表都进 `docs/benchmarks/`。

---

## 五、明确不做的事

对照两家读完之后的取舍（**不做也是决定**，写在这里免得被反复提）：

1. **worker / 多进程并行**：d2-armor-solver 自己的实测是反例 —— 14 个 worker 让 388ms → 1056ms、
   内存 ×16、主线程延迟 29ms → 417ms，所以他们定成 Fast/Balanced 强制单分片、只有 Deep 才 4 片。
   我们求解是秒级到百秒级、候选集小，多进程 clone 库存 + 全库复核只会更贵。**提速先做界与顺序。**
2. **整库数学等价类压缩**：只在 1300 件大库且要先解决"搜索顺序即契约"才划算。
   只借其中一条：**否定结果由等价类共享**。
3. **完整宏等价（plan-equivalence）**：那是"库存规划器"的需求。只借一条：
   `精工/调谐按六维贡献向量比较，不按数量比较`（不同件的精工三属性不同）。
4. **400 万条的密集 adjustment 表**：只适合"精确点目标 + 固定五件"的常数级查询。
5. **一次给 200 套解 / DIM 式 UI**：我们是 MCP 工具，用 `top_n` + **可达区间**表达，
   不做并排对比界面。
6. **不改 `equip_build` 的"确认后写入"纪律**：不管排序怎么变，写入仍需用户明确确认。

---

## 六、风险与回滚

| 风险 | 触发点 | 处置 |
| --- | --- | --- |
| 术士求解已经 ~190s，加调谐枚举会更慢 | P4 昂贵路径 | 昂贵路径必须显式开关 + 走同一套预算；预算用尽报"没搜完"（P1 保证） |
| 排序变更是破坏性变更 | P3 | P0 基准 + 基线 diff + COMPATIBILITY/CHANGELOG；必要时按阶段出 tag |
| `build/farm_target.py` 已在 1296 行上限 | P3/P4 要动它 | 先抽代码出模块，**上限只许降不许抬**（`tests/test_module_size_ratchet.py`） |
| 新模块要登记层号 | 新增 `build/ranking.py` 等 | `tests/test_architecture_layers.py` 的 `_LAYERS` 里登记（`build/` = 第 2 层） |
| "没算就说没解"的老毛病复发 | P1/P2 | 预算/规模导致的不确定一律走 `unverifiable_reason` / `precision="not_computed"`，守门注入验证 |
| 排序启发式过度设计 | P3 | d2-armor-solver 把花哨的启发式实现后又**删掉**（极端用例降、普通用例回退）；先做简单版本并量 |

---

## 七、开放问题（等你拍板）

1. **上限算硬约束还是软约束**？（我推荐：算违规、会被排到后面并标注，但不阻止出解。）
2. **默认要不要"尽量高"**？（我推荐：不猜，只保证达标 + 报可达区间；要顶就显式说。）
3. **"顶到极限"用什么输入表达**？（我推荐复用 `priority_stats` 表达顺序，配 `*_max` 表达边界，
   不新增一个"maximize"参数名。）
4. **接受 P3 的候选顺序变化吗**？（破坏性变更，会改现有 `find`/`recommend` 的候选排名。）
5. **术士求解的时间预算**：现在默认 300s，实测 ~190s。要不要在 P4 之后把默认预算显式调低，
   让"顶到极限"变成一个用户主动选的慢操作？

---

## 附：外部对照物的关键证据（便于复核）

**DIM**（`DestinyItemManager/DIM`，`gh api` 读源码）
- `process-worker/set-tracker.ts:23-37` `isWorse`：clamp 后总和降序 → 优先级字典序（`encodeStatMix`，:157）
  → 不 clamp 总和 → 光等。**min/max 只是过滤器，不是目标函数**（总和才是第一键）。
- `process-utils.ts:529` `greedyPickStatMods`：先达标，再按优先级**二分搜索还能加多少**；
  `updateMaxStats`(:170) 算每属性可达上限。
- `mappers.ts:135-183` `buildTuningVariants`：调谐是搜索空间的一部分，含"牺牲哪个属性"的选择。
- `loadout/known-values.ts:38-48`：`MAX_STAT = 200`（Armor 3.0），溢出不计入也不转移（`process.ts:894-899`）。

**d2-armor-solver**（`MIGO-OvO/d2-armor-solver`，v3.x，MIT）
- `src/core/search-session.mjs:3-12` 三档 profile "Profiles change effort only"；
  `:77-78` **存在性阶段豁免预算**；`docs/staged-search.md:18` "no limit can create an infeasibility proof"。
- `src/core/stat-ranking.mjs`：9 元组逐字段比较；`statRuleGap` **区间内缺口为 0**；
  `docs/priority-rules-audit.md` 是公开自查，含"低优先富余不能补贴高优先缺失"与那个现成反例。
- `src/core/residual-bounds.mjs`：三个互补的界（精确总和 / support 投影 / 模 5 残差），
  **表爆 → `null` = 未知，绝不等于"不可达"**；`canReach` 用 min/max 拆成正负项求最大投影。
- `src/core/reachability.mjs`：逐属性可达区间；纪律"**marginal 区间可分别并，绝不能拼成联合 witness**"。
- `docs/complete-optimization.md`：调谐（含空着）进搜索域；`TUNING_DOMAIN_ID` 变必须 bump 缓存身份；
  "截断不能证明不可行"、"局部完成不是全局最优的证明"。
- `src/core/armor-model.mjs:487-515`：T5 传说本体只有 **48 种形状**（12 原型 × 4 第三属性），
  形态 `30/25/20 + 其余三项各 5`，**精工恒等于词条之外那三项** —— 解释了我们那套里 30/0/5/30/30/5 这类分布。
- 性能实测（`docs/benchmarks/*.json`）：14 worker 388→1056ms、内存 ×16、主线程延迟 29→417ms；
  所以他们 `profileLimit = deep ? 4 : 1`。
