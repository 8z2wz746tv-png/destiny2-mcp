# 计划：装备写入路径（求解 → 确认 → 写入 → 回读）这一轮修了什么

状态：**已落地（代码 + 单测）**；**修复之后的真机复验：2026-10-04 取得一轮**（逐条与口径见 §八；
这一轮仍然没拿到的列在 §六）。

这份文档是**补记**（2026-10-03）。那一轮开工时没有计划文档 —— 审计顺着
[ADR-024](../adr/024-recheck-premises-before-writing.md) 结尾那句「见 plan/提交说明」找过来，
两头都不存在：`docs/plans/` 下没有这一轮的文件，而提交说明按仓库规矩只有一行标题。
内容全部来自这六条提交、ADR-022/023/024 与各测试文件 docstring 里的**真机原文**；
凡是没有原文可引的地方一律写「未取得」。这是仓库里第一次把「验过什么 / 没验成什么」写下来 ——
此前这条规矩只活在 `AGENTS.md` 里，一个实例都没有。

## 一、证据等级怎么读（这份文档的规矩）

| 档 | 含义 |
| --- | --- |
| **真机（故障）** | 有真机原文留在仓库里（回执句、上游 `ErrorCode`/`ErrorStatus`）。它证明的是**问题存在**，不是修复有效 |
| **真机（修复后）** | 有「修好之后又跑了一次真机、结果是 X」的记录。**2026-10-03 那一轮一条都没有**（下一版补上了，见 §八） |
| **只有单测** | 修复由单测钉住（给得出文件名与断言要点），没有真机复跑记录 |
| **未取得** | 以上都没有。**不等于「没做」**，等于「仓库里没有证据」—— 没拿到证据就写未取得，不许拿「调用成功」顶替「功能正确」 |

**「真机（修复后）」这一档是 2026-10-04 第一次填上的**（§八）：在这之前本文档全篇的「未取得」
都是按上面这四档读的，没有一处是"没做"。另外注意：**同一档里的证据强度也不一样**
（回执原文 > 段计时 > 单个数字），§八逐条标了口径。

## 二、这一轮修了什么（逐条）

六条提交：`a98bb42`（ADR-022 + ADR-023）→ `5e9ec68`（6 组）→ `a59b9f1`（ADR-024）→
`5d82b5f`（文档）→ `9b38da2`（碎片）→ `69c1dfe`（预检不再污染写入趟）。

| # | 修了什么 | 落点 | 故障的真机证据（原文） | 修复后的验收 |
| --- | --- | --- | --- | --- |
| 1 | 两条执行前提（格满搬不进来 / 与正穿着的金装冲突）在**求解阶段**就判死，求解器不再签发注定装不上的候选；0 候选说清是哪条约束卡的（ADR-022） | `build/execution_feasibility.py`、`build/execution_diagnosis.py`、`build/solver.py`、`build/analyzer.py`、`tools/_build_flow.py` | **真机（故障）**：2026-10-03「社区配装 → 一次性装备」三次里**前两次一个字节都没写进去** —— ① 求解器挑了件在**仓库**里的臂铠、角色臂铠格 **10/10 满** → 上游 500 `DestinyNoRoomInDestination: There are no item slots available to transfer this item.`；② 要换异域头盔而身上穿着异域胸甲（星火协议）→ `equipStatus=1641 DestinyItemUniqueEquipRestricted`。两次都全量回滚、**0 颗模组落地** | **只有单测**：`tests/test_build_execution_feasibility.py`（(a)(b) 段 + 工具面 4 条）、`docs/testing/TESTING_CORPUS.md` 的「0 候选」行 |
| 2 | 独立回读要有**一次调用拿全**的入口：`inventory_assistant(intent="mods")`（ADR-023） | `services/inventory_service.py`、`services/armor_payload.py`、`tools/_armor_branches.py` | **真机（成本）**：逐件 `intent="item"` 是 N+1，语料实测 **13.9 秒/件**（五件 ≈ 70 秒）；而组件 305 一次覆盖账号全部物品的已装插槽（实测 3.47 MB / 0.85 秒） | **只有单测**：`tests/test_equipped_armor_mods.py`（与 `item` 同形状、一次覆盖五件、分派不串台） |
| 3 | 写入编排的次序纪律（6 组）：预检**在任何写入之前**、模组被挡不许吞掉子职业那一步、没确认不许回滚、回滚**先比对再写**、快照要覆盖被改动过的那件 | `services/loadout_mod_preflight.py`、`services/loadout_exact_flow.py`、`services/loadout_verify.py`、`services/loadout_restore_locations.py`、`services/loadout_recovery.py` | **真机（故障）**：预检排在搬运+批量装备**之后** → 注定失败的批次已经把装备换好了，只能整条回滚（实测一次 **3.5 分钟**）；1 颗模组被上游 **1676** 挡住时那个分支**提前 return** → `subclass`（Step 3）整段没跑，回执却说「装备已经换上」；`save` 只拍了当时穿着的 5 件；回滚逐颗重写模组、连「这个槽已经装着它」都不比一下 → 上游 1679 `DestinySocketAlreadyHasPlug` + 客户端退避，**每颗白花约 10 秒** | **只有单测**：`tests/test_equip_orchestration.py`、`tests/test_loadout_rollback.py`、`tests/test_loadout_armor_snapshot.py`、`tests/test_exact_build_execution.py` |
| 4 | 写账号之前按**当时的现场**复检那两条执行前提（判据仍是同一份，不重解）；候选指纹只收「写入前复算得出来的求解输入」，补入**已装功能模组的能量占用**（ADR-024） | `services/build_execution_guard.py`、`build/snapshot_version.py`、`build/models.py`、`services/build_service.py` | **真机（故障）**：同上那两次 0 颗模组落地（`find` 与 `confirm` 之间的账号变化，指纹刻意不覆盖）；另 2026-09-22 真机：换一颗不改六维的功能模组后按**过期能量预算**腾模组 → 12/11 预检失败、整批回滚 | **只有单测**：`tests/test_build_execution_feasibility.py`(c) 段三条、`tests/test_snapshot_version_fingerprint.py` 三条；注入记录见 §五 |
| 5 | 碎片只排进**启用中**的槽（禁用槽不再被当成第 6 格） | `services/fragment_sockets.py`、`services/build_fragments.py`、`services/loadout_subclass_sockets.py` | **真机（故障）**：2026-10-03 棱镜术士（槽 9–14 共用碎片池 `3916244727`）社区配装的碎片里 **4 颗**被判「与插槽不兼容」（有符号/无符号没归一）；第 3 轮把第 6 颗碎片排进**被禁用**的槽 14 → 上游 500 `DestinySocketActionNotAllowed`（`request.plug.socketIndex: The requested socket is disabled.`） | **只有单测**：`tests/test_build_fragments.py`、`tests/test_loadout_mod_planning.py` |
| 6 | 预检不再污染写入趟：一颗模组只出一条结论；非异域中间件挑得到（键缺席 ≠ 判不了） | `services/equip_planner.py`、`services/loadout_mod_sockets.py`、`services/loadout_functional_mods.py` | **真机（故障）**：2026-10-03 回执里**15 对矛盾**（同一颗模组既有 `action=mod` 成功行、又有 `action=mod_blocked` 失败行，槽号是内部哨兵 `-1`）；「光芒领主护腿」的功能模组按瞬时 12/11 被判装不下、**白丢一颗**；背包里 **8 件**非异域胸甲全被跳过，回执却说「没有可用的非异域胸部护甲」 | **只有单测**：`tests/test_mod_conclusion_steps.py`、`tests/test_equip_planner.py`、`tests/test_loadout_mod_planning.py` |

一句话总结证据面（**这是 2026-10-03 那一轮的读数**）：**故障侧全是真机原文，修复侧全是单测**。
2026-10-04 又跑了一轮真机，把修复侧能跑的都跑了（§八）——所以这句现在只对 §六 里剩下的那几条成立。

**一个间接但真实的证据**（不要读成"逐条复验通过"）：`a98bb42` 之后，2026-10-03 又跑过一次真机
（`tests/test_equip_orchestration.py` 的 docstring 原话是「真机证据（2026-10-03 **复测**）」），
它报出来的是**另一批**毛病 —— 预检次序、子职业被吞、回滚不比对、`save` 快照漏件、1676 提前
return —— 那批原文里**没有**再出现 `DestinyNoRoomInDestination` / `1641`；第 3 轮又抓到碎片
（`9b38da2`）。能确证的只有这些：**修完之后真机确实又跑过，且没再报出同一条**。
**各条修复各自有没有真机复验 —— 2026-10-03 当时未取得；2026-10-04 补上了一部分（§八），
剩下的见 §六。**

## 三、验收点（这一轮算不算完）

| 验收 | 口径 | 状态 |
| --- | --- | --- |
| 单测 | `pytest -q` 全绿。审计补记时实测（六条提交落地后）：**1958 passed / 59.91 秒**；审计新增 1 条守门后收集 **1959** 条 —— 比 `AGENTS.md` 里那行 1652 条新（文档里的数字本来就会漂） | **已取得** |
| 语料行 | `docs/testing/TESTING_CORPUS.md` 段内更新：0 候选要说「装不上」、一次读回、标量宿主的 `execution_id` 回传、调谐不由 207 判 | **已取得**（文档层） |
| 语料真机跑 | `scripts/run_corpus_all_rows.py` 在 `a98bb42` 里加了行（+24），**是否真机跑过并逐行吻合：未取得** | **未取得** |
| 真机复跑 | 修完之后再走一遍「find → 确认 → 写入 → 回读」，拿到 `steps[].verify` 的核对结论 | **已取得（2026-10-04）**：写入回执、回读结论、逐槽还原、段计时都在 §八。**但「写段 0 颗被拒 + `verify` 通过」这个形状仍未取得**（§六 第 2 条） |
| 注入验证 | 每条新守门都要「注入违规 → 确认变红 → 逐字节恢复」。逐条记录见 §五 | **部分**（1/3 与 3/3 两种读法，见 §五） |

## 四、还没做的

1. **失败后的 `next_actions` 还是固定一句**：`tools/_armor_branches.equip_build` 失败时一律给
   「重新求解并确认配装」。对 `stale_inventory_snapshot` / `exact_item_missing` 是对的（确实要重解），
   对 `execution_precondition_failed` **不是最优下一步** —— 理想是按码给「腾一格 / 先顶下冲突的金装」。
   ADR-024 的 Consequences 里记着这条，这一轮没动工具层（出路只能靠 `message` 与 `blockers` 带出去）。
2. **`find` 与 `confirm` 之间的账号变化**已由 ADR-024 接手，但**只覆盖那两条执行前提**：
   其余现场变化仍按指纹口径判（例如"在游戏里换一颗不改六维的功能模组"会作废候选，见 ADR-024）。
3. **真机复验**：2026-10-04 跑了一轮（§八），把上面这两条之外能跑的都跑了；
   剩下的在 §六，动这条链路之前先补掉。

## 五、注入验证：哪几条有记录可核对

ADR-024 结尾说「三条注入都验证过会变红（见 plan/提交说明）」。那句话原来指不到任何地方；
能核对到的记录只有测试 docstring 里写下的注入点与预期红点，逐条如下。

| 守门 | 注入做法（docstring 原文） | 预期 | 仓库里有没有这条记录 |
| --- | --- | --- | --- |
| `test_build_execution_feasibility.py` (a) 段 | 把 `solver` 里那句 `if not armor.execution_blocker` 去掉 | 本用例红 | **有**（写在用例 docstring 里） |
| `test_build_execution_feasibility.py` (c) 段第 1 条 | 去掉 `build_execution_guard.recheck_confirmed_build` 里 `refusals` 那段判断 | 本用例红（会一路走到 `equip_with_recovery` —— 真机上就是上游 500 + 整批回滚、0 颗模组落地） | **有** |
| `test_build_execution_feasibility.py` (c) 段第 2、3 条 | —— | —— | **未取得**（只有测试本身，没有注入记录） |
| `test_snapshot_version_fingerprint.py` 三条 | ① 把指纹里那行 `installed_mod_energy` 去掉；② 把它换成 `energy_used_by_other_mods`；③ 把 `model_dump()` 整个快照写进指纹 | 各自红 | **有**（三条都写在 docstring 里） |

所以 ADR-024 说的「三条」有两种读法：按 (c) 段读是 **1/3 有记录**，按指纹那个文件读是 **3/3 有记录**。
两种读法都在上面，读者可自行核对；**本轮没有重做这三条注入**（未取得 = 没有新证据）。

唯一一条**本轮亲手验过**的注入，是这次审计新增的那道守门（§七）：把
`build_execution_guard` 里的 `ErrorCode.EXACT_ITEM_MISSING` 改回裸串
`"code": "exact_item_missing"` → `tests/test_error_codes.py` 立刻红、点名
`destiny_mcp/services/build_execution_guard.py:96`，其余 5 条照旧绿；恢复后文件 sha256
与注入前逐字节一致（`c748254db1f9…`），再跑 6 passed。

## 六、没验成的部分（如实列）

**这一版是 2026-10-04 的读数**：那天跑了一轮真机（结果见 §八），把 2026-10-03 那版清单里的
`intent="mods"` 与碎片 socket 14 两条覆盖掉了，**剩下的**是下面这些。老规矩：没验成就写没验成，
不许拿「调用成功」顶替「功能正确」。

1. **`benchmark_equip_chain.py --write` 的端到端真机（含自动还原）**：**未取得**。真机那一趟
   （2026-10-04 02:03）自动还原只做到"回读核对通过"，**还留下 1 处位置缺口** —— 为腾能量搬走的
   `光芒领主手套` 停在 `warlock` 上，回执自己在【已知缺口】里点名要手动搬回 `vault`。
   **"自动还原真机成功"只有 `--offline` 替身证据**（替身里 `restore.ok=true`，但 `steps` 是空的，
   那不是真机证据）。
2. **benchmark 写段走出成功路径（0 颗被拒）时的端到端 `verify`**：**未取得** —— 这一趟写段有
   1 颗模组被上游拒，`verify` 一跳没做（原文见 §8.1）。口径要说清：`equip_build` 那条路
   （MCP 工具面）走出过 `success: true` + 回读通过（`164250`/`164539`，见 §8.1），
   **benchmark 这条链路没有同等的一趟**。
3. **`clear`（腾能量被拒 → 回读照跑）**：**形状造不出来** —— 真机上腾能量没被拒过，只有单测。
4. **预检判死 `M>0`**：只有单测。真机那几趟属性模组三颗全插得进，没有一颗在预检就被判死。
5. **`_read_sockets` 的最坏情形（每件 8 次重试）**：**未取得**。真机 5 件全是一次命中，
   重试那条路没跑到。
6. **benchmark 第 5 件（位置基线前移）**：**只标了盲区、没修** —— 存快照那一下搬走的件仍然看不见，
   就是第 1 条那处位置缺口的根因。
7. （2026-10-03 那版留下、这轮仍未取得的）**1641 / `DestinyNoRoomInDestination` 复检分支的真机命中**：
   `tests/test_build_execution_feasibility.py` (c) 段三条全是替身快照；真机上"确认那一刻现场已变"
   是否真被拦住 —— **未取得**。
8. （同上）**语料真机全量**（`scripts/run_corpus_all_rows.py`，约 4–5 分钟）：**未取得**。

## 七、补记：这一轮之后审计挖出的三个规范缺口（2026-10-03）

同一天做的一轮静态审计，修的都是"规范缺口"、不改行为，一起记在这里：

1. **候选拒绝码有一整族在 `error_codes.py` 之外**（`stale_inventory_snapshot` 甚至不在枚举里，
   却在响应里出现）：三个模块 12 个码收进 `ErrorCode`（**值一个字没改** —— 调用方与语料按值匹配），
   并把 `tests/test_error_codes.py` 的守门扩到**结构化 payload 里的 `"code"` 字面量**
   （服务层不走 `error_response()`，原来那条按函数名扫的判据看不见它）。
2. **`weapon_local_data.py` 的体量上限注释给不出它的数字**：403 → 427 里的 +22 是
   `_cross_check` 那次「同名多版本」修复（逐块可数），另外 +2 出自一个没碰过这个文件的提交、
   没有出处 —— 上限按实际长度收回 425。同一趟把这次收口改到的另外三个文件的上限也按实际长度
   收紧了（`build_execution_guard` 101 → 99、`build_service` 788 → 787、`build_candidates` 126 → 125），
   数字与理由都写在 `tests/test_module_size_ratchet.py` 的注释里。
3. **ADR-024 的指路悬空**（就是本文档存在的直接原因）：现在指向 §五。

## 八、2026-10-04 真机轮：修复侧的验收（补上 §一 那一档）

这一轮把 §二 那 6 组修复里**能真机跑的**跑了一遍。**先把口径写在前面**：

- **两次形状不完全一致**（旧那一趟是 **16 颗**模组被上游挡，新这趟只有 **1 颗**），所以凡是拿旧
  回执比时长的，都是**同类对比、不是受控 A/B** —— 同形状前后各跑一次才算 A/B，本轮没做。
- 上游那一侧受时段影响（同一端点单次 2.3~8.6 秒不等），所以下面只写实测读数，不写"提升了百分之几"。
- 数字的出处：回执原文在 `~/.destiny_mcp/audit/20261003/`（文件名是 **UTC** 时间戳 —— 那一天跨到
  10-04 凌晨，所以"今天"的几条也在这个目录里）；脚本那几趟的段表与明细在同名 `bench_*` 的
  JSON/log 里（跑完时在 `/tmp`，**临时产物、不进 git**）。也就是说：**这些数字的长期载体就是本文档**，
  要复核得重跑那一趟。

### 8.1 回读路径（已真机验证）

- **有模组被上游拒绝时，`verify` 一次都不读**（`steps[].verify` 原文）：
  > 这次没有回读核对：计划要写的模组里有 1 颗被上游拒绝、0 颗在组件 207 预检就被判死
  > （见 steps.mod_blocked）——它们都不在账号上，核对注定不通过，所以一次都没读；装备与子职业
  > 那一半这次没有独立证据（写入步骤报的是成功，但别把'没核对'当成'没装上'）。

  同一趟的 `steps.mod_blocked` 给出上游 **1676** 的原文（`DestinyFailedPlugInsertionRules`）。
- **整份回执里「可能是同步窗口」出现 0 次** —— 旧话术已经消失（旧那一趟出现 1 次）。
  0 次的意思是：**不再拿"窗口"当解释**，没核对就直说没核对。
- **干净形状对照**：`verify` 照跑、`success: true`（两趟：96.3 秒 / 59.5 秒）。
- **耗时**：同类有 blocked 形状 **130.9s → 59.1s（−71.8s）**；干净形状 59~96s，无回归。
- 顺带结掉 2026-10-03 那版 §六 里的 `intent="mods"` 一条：**真机上确实在用它做独立回读** ——
  一次调用就拿回 5 件的全部插槽（真机调用 2.7~3.0 秒/次；旧的逐件 `intent="item"` 是 13.9 秒/件）。
  但别把它跟下面那个 70.13s 混起来：**70.13s 是"被拒的模组不可能对上、还在窗口里空等 8 轮"
  （8 次 `verify_loadout` ≈ 59.6s + 窗口余量），不是逐件读取的成本** —— 两笔账数字像、根因不同，
  混起来就会把"跳过无望的回读"记成"省下了 N+1"。

### 8.2 写入路径（已真机验证）

一趟真实 `equip_build(confirmed=true)`（2026-10-04 02:03，`/tmp/bench_write_final.json`）：

- **11 颗模组写成功 / 1 颗被上游 1676 拒 / 4 个碎片成功**；`rollback_*` **0 条**。
- **`subclass` 12 条全 `success`**，**没有 `DestinySocketActionNotAllowed`**，**socket 14 未被碰**
  （它是那个 `is_active=false` 的禁用碎片槽；写入前后两次独立读子职业，它都还是"空碎片插槽"）。
- **`插槽 -1` 0 处** —— §二 #6 那种内部哨兵不再出现在 steps 里（旧那一趟有 15 处）。
- **属性模组（六维 + 调谐）进了 steps，且回读证实落盘**：`164539` 的写步骤里有 `生命值模组`
  与调谐 `平衡调整`，`164250` 里有 `+生命值 / -近战` 这类六维模组与 `平衡调整`；
  两趟的 `verify` 都是「已回读核对：装备实例、模组与子职业配置都对得上」。
- **逐槽还原**：5 件实例 ID 与快照一致、**每槽 `plug_hash` 与能量一致**（回读那份
  `inventory_assistant(intent="mods")` 是逐槽给 `plug_hash` + `energy_cost`、逐件给
  `energy.capacity/used` 的，5 件逐格比下来 0 差异）；**全账号 482 件位置逐件相同**
  （写前基线 vs 还原后现场，482 件的 `location` 与 `mods` 都 0 差异；`counts_by_location`
  也逐项相同：hunter 45 / postmaster 11 / titan 47 / vault 331 / warlock 48）。
  （口径：这一比是**拿写前基线 vs 还原后现场**，覆盖全账号 482 件；它证明的是"最终位置回到了
  基线"，**不是**"benchmark 的自动还原自己搬回来的" —— 那句仍然未取得，见 §六 第 1 条。）

### 8.3 分段实测（benchmark，墙钟归属）

`scripts/benchmark_equip_chain.py` 的段计时（**嵌套，不能相加**；修前读数取自同一脚本早一趟的段表）：

| 段 | 本轮 | 修前 |
| --- | --- | --- |
| `find` | 12.2s | —— |
| 确认回显 | 0.8s | —— |
| 真写（`equip_with_recovery` 总） | 74.0s | —— |
| └ 写一颗模组 ×12 | 42.5s（均值 3.54s） | —— |
| 回读核对（`readback_verdict` 总） | **2.0s** | **70.13s** |
| Step0 模组预检（`_mod_preflight`） | **0.0s** | **6.74s** |
| HTTP 合计 | 155.6s / 115 次 | —— |

- **写一颗模组 ×12 = 42.5s（均值 3.54s）已经贴住上游地板**：12 次 `InsertSocketPlugFree` 单次
  2.3~8.6 秒，客户端这一侧没有可省的余量了 —— 再谈"写得更快"只能改计划（少写几颗），不能改实现。
- 后两行是**两处修复的直接对照**（那一版提交的标题就写着「免掉 70.1 秒与 6.7 秒」）：
  有模组被拒时不再空等回读窗口、模组预检复用快照而不是逐件重规划。
- **新量出来的两条账**（都是"跑腿"，不是上游慢；两条各自出自哪一趟都写在括号里）：
  1. **43% 的请求是跑腿**（分段那一趟：81 次 HTTP）：**21 次 `resolve_player` + 14 次 `profile[200]`**
     —— 解析器没有缓存，同一份 membership/角色 ID 被反复问；
  2. **`find` 的 15.5s 里 8.0s（52%）是 15 次重复的 `GetMembershipsForCurrentUser`**（只读那一趟），
     根因是 `.env` 里没配 `DESTINY_DEFAULT_PLAYER`：工具层在"调用方没给 `player_name`"时会回落到
     "当前登录者"，而**那条路没有缓存**（同一趟 15 次往返，合计 8.04 秒）。

### 8.4 `socket_diff_rows` 的判据口径（写明的）

还原核对的逐槽 diff **现在只判快照记过的槽**（`expected` 的键），不按并集比：
`read_armor_mod_sockets` 收的是"可写模组槽"（通用/部位/调谐），而 `inventory_assistant(intent="mods")`
把**着色器/大师杰作/原型/词条/皮肤/固有能力**也一起列出来。按并集比 = 每一次都把那批槽报成
"快照=空 现场=有"的**假红**（真机 2026-10-04 第一趟五件全 ✗，而账号逐件等于快照）。
现在那部分**单独占一行**说「本次**不判**」、不进 `gaps`。
**为什么这么定**：假红比不报更糟 —— 它会教读的人忽略这份核对。
