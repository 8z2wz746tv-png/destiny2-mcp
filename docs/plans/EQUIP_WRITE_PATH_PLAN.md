# 计划：装备写入路径（求解 → 确认 → 写入 → 回读）这一轮修了什么

状态：**已落地（代码 + 单测）**；**修复之后的真机复验：未取得**（逐条见 §二 与 §六）。

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
| **真机（修复后）** | 有「修好之后又跑了一次真机、结果是 X」的记录。本轮**一条都没有** |
| **只有单测** | 修复由单测钉住（给得出文件名与断言要点），没有真机复跑记录 |
| **未取得** | 以上都没有。**不等于「没做」**，等于「仓库里没有证据」—— 没拿到证据就写未取得，不许拿「调用成功」顶替「功能正确」 |

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

一句话总结证据面：**故障侧全是真机原文，修复侧全是单测**。

**一个间接但真实的证据**（不要读成"逐条复验通过"）：`a98bb42` 之后，2026-10-03 又跑过一次真机
（`tests/test_equip_orchestration.py` 的 docstring 原话是「真机证据（2026-10-03 **复测**）」），
它报出来的是**另一批**毛病 —— 预检次序、子职业被吞、回滚不比对、`save` 快照漏件、1676 提前
return —— 那批原文里**没有**再出现 `DestinyNoRoomInDestination` / `1641`；第 3 轮又抓到碎片
（`9b38da2`）。能确证的只有这些：**修完之后真机确实又跑过，且没再报出同一条**。
**各条修复各自有没有真机复验 —— 未取得。**

## 三、验收点（这一轮算不算完）

| 验收 | 口径 | 状态 |
| --- | --- | --- |
| 单测 | `pytest -q` 全绿。审计补记时实测（六条提交落地后）：**1958 passed / 59.91 秒**；审计新增 1 条守门后收集 **1959** 条 —— 比 `AGENTS.md` 里那行 1652 条新（文档里的数字本来就会漂） | **已取得** |
| 语料行 | `docs/testing/TESTING_CORPUS.md` 段内更新：0 候选要说「装不上」、一次读回、标量宿主的 `execution_id` 回传、调谐不由 207 判 | **已取得**（文档层） |
| 语料真机跑 | `scripts/run_corpus_all_rows.py` 在 `a98bb42` 里加了行（+24），**是否真机跑过并逐行吻合：未取得** | **未取得** |
| 真机复跑 | 修完之后再走一遍「find → 确认 → 写入 → 回读」，拿到 `steps[].verify` 的核对结论 | **部分**：只有 §二 末段那条间接证据（复测没再报同一条）；逐条复验**未取得** |
| 注入验证 | 每条新守门都要「注入违规 → 确认变红 → 逐字节恢复」。逐条记录见 §五 | **部分**（1/3 与 3/3 两种读法，见 §五） |

## 四、还没做的

1. **失败后的 `next_actions` 还是固定一句**：`tools/_armor_branches.equip_build` 失败时一律给
   「重新求解并确认配装」。对 `stale_inventory_snapshot` / `exact_item_missing` 是对的（确实要重解），
   对 `execution_precondition_failed` **不是最优下一步** —— 理想是按码给「腾一格 / 先顶下冲突的金装」。
   ADR-024 的 Consequences 里记着这条，这一轮没动工具层（出路只能靠 `message` 与 `blockers` 带出去）。
2. **`find` 与 `confirm` 之间的账号变化**已由 ADR-024 接手，但**只覆盖那两条执行前提**：
   其余现场变化仍按指纹口径判（例如"在游戏里换一颗不改六维的功能模组"会作废候选，见 ADR-024）。
3. **真机复验**（§六 全部）。

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

- **修复之后的真机复跑（逐条）**：整条链路（`find` → `confirmed=true` → 写入 → 回读核对）在
  每一条修复之后是否走通过 —— **未取得**。仓库里没有回执、没有日志、没有基线 diff；提交说明
  只有一行标题。唯一能确证的是 §二 末段那条：`a98bb42` 之后真机又跑过（复测），报的是另一批
  毛病、没再报同一条。这一条最要紧：ADR-022/023/024 与 6 组修复的**故障**都是真机抓的，
  **验收**却只有单测。
- **`intent="mods"` 的真机调用**：单测 271 行钉住形状与覆盖，但真机上是否真的用它替代了
  逐件 `intent="item"`（省下那 70 秒）—— **未取得**。
- **1641 / `DestinyNoRoomInDestination` 复检分支的真机命中**：`tests/test_build_execution_feasibility.py`
  (c) 段三条全是替身快照；真机上"确认那一刻现场已变"是否真被拦住 —— **未取得**。
- **碎片禁用槽（socket 14）修复后的真机复跑**：**未取得**。
- **语料真机全量**（`scripts/run_corpus_all_rows.py`，约 4–5 分钟）：**未取得**。

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
