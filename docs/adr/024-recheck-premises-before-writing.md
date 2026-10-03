# ADR-024: 执行前复检：确认那一刻按当时的现场再判一次执行前提；指纹只管"写入前复算得出来的求解输入"

- Status: accepted
- Date: 2026-10-03
- Decision By: maintainer
- Scope: `destiny_mcp/build/execution_feasibility.py`、`destiny_mcp/build/execution_diagnosis.py`、`destiny_mcp/build/snapshot_version.py`、`destiny_mcp/build/models.py`、`destiny_mcp/services/build_execution_guard.py`、`destiny_mcp/services/build_service.py`

## Context

ADR-022 把两条执行前提（格满搬不进来 / 与角色正穿着的金装冲突）**判死在求解阶段**，但它的
Consequences 里写着一条明确的缺口：**"`find` 与 `confirm` 之间账号状态变化仍会在写入时撞上游错误"**。
真机代价是实测过两次的：那一格被填满、或换上了另一件金装之后，写入路径只比"库存指纹"就放行，
最后拿到的是上游 500 `DestinyNoRoomInDestination` / `equipStatus=1641` —— 整批回滚、**0 颗模组落地**。

指纹挡不住这两条，是**设计使然**（`build/snapshot_version.py`）：`snapshot.execution`（各格占用、
当前穿着的金装、`Armor.execution_blocker`）刻意不进指纹，否则"刚求解完、捡到一件护甲"就会被告知
"库存变了、请重新求解"。于是"指纹一样"这件事，对这两条前提**不构成任何证据**；而判据本身还有
"读不到就不下结论"的一档（桶定义缺失、认不出唯一角色时一件都不拦），那种候选**从来没被这条前提判过**。

同一个模块里还有第二件事：求解用的能量预算取 `max(已装部位模组, 照抄模板的预留额度)`，而指纹当时
只认六维与能量上限。于是在游戏里换一颗**不改六维**的功能模组（弹药搜寻者这种）不会作废候选，
`equip_build` 按**过期的能量预算**腾模组（多清一格，或属性模组装不下 → 12/11 预检失败并回滚）。

**What changed**：写入路径补上复检（ADR-022 承诺、但此前无人兑现的那一步）；指纹口径明确为
"只收影响求解、且写入那一刻**复算得出来**的字段"，并把"已装功能模组的能量占用"补进去。

## Decision

**1. 写账号之前，按当时的现场复检两条执行前提。** 判据不新增第二份：读的就是求解那一刻写在件上的
`Armor.execution_blocker`（唯一出处 `build/execution_feasibility.py`，连同"哪条约束 + 出路"整句），
只是换一份**刚重取**的 `get_armor_snapshot` 现场重读一遍。**不许**在这里重新求解一次
（那是白花几十秒的另一条路），**不许**静默继续，也**不许**静默返空。

**2. 复检排在库存指纹比对之前。** 理由两条：① 这两条前提刻意不进指纹，"指纹一样"证明不了它们还
成立；② 两个信号同时出现时，报出来的必须是**可执行**的那条 —— "腾一格 / 先顶下冲突的金装"，
而不是泛泛的"库存变了、请重新求解"（重新求解只会再得到同一个结论，用户在两个消息之间打转）。

**3. 拒绝要说清"哪条约束 + 出路"。** 落在响应的 `blockers`（逐件一条中文），与 `find` / `analyze`
的 0 候选诊断同一套措辞；拒绝码 `execution_precondition_failed`（与 `stale_inventory_snapshot`
那族并列的服务层候选拒绝码）。

**4. 指纹只收"写入前复算得出来的求解输入"。** 这一轮新增的只有 `installed_mod_energy`
（**已装**部位模组占掉的能量，账号事实）。**不收**求解请求里的预留额度（写入那一刻没有它 ——
收了会让每一套带功能模组的社区配装在确认时被自己判成"库存变了"），也不收其余执行现场（第 1 条
已经负责复检它们）。取舍写进 `build/snapshot_version.py` 与 `Armor.installed_mod_energy` 的注释。

被否掉的方案：

- **把执行现场整体塞进指纹**（连各格占用、当前金装一起）：代价是"捡到一件护甲 / 换件金装就作废
  手里的候选"，用户刚花几十秒求解完就得重跑 —— 比复检更差的体验，而且这两条本来就有明确出路；
- **拿 `find` 那份快照当复检现场**：那正是要避免的"用求解时的现场证明当时的前提还成立"；
- **在写入层加一段"先腾再搬"的重试**（把格子先腾出来再搬仓库件）：要动账号两次、还得挑腾哪一件，
  而"搬不进来"这件事在求解阶段就能避开（ADR-022 已否过一次）；
- **只靠上游错误码事后解释 500/1641**：上游给的是整批回滚，写进去的字节数已经归零 —— 那不是判，
  是事后止损。

## Consequences

- `find` 与 `confirm` 之间的账号变化不再撞上游：命中就如实拒绝（`blockers` 里是哪一件、哪条约束、
  出路是什么）。代价是**多一次 profile 读取**（写入路径本来就要重取一次，复检搭在同一份快照上，
  零额外请求）与**多一个拒绝分支**。
- 在游戏里换一颗不改六维的功能模组会**作废手里的候选**（要求重新求解）—— 这是有意的：能量预算是
  求解输入，写入那一刻无法复算。同能量的两颗模组互换不作废（指纹只认占用，不认模组清单）。
- 判据/出路与叙述各归一处：`execution_feasibility.py`（判据 + 每条约束的出路，写在件上）、
  `execution_diagnosis.py`（格级汇总与"指定的金装"点名）、`services/build_execution_guard.py`
  （写之前那四步的顺序）。以后再动这两条前提，先改判据那一处。
- **已知未做**：`tools/_armor_branches.equip_build` 失败时的 `next_actions` 仍是固定的
  "重新求解并确认配装"，对 `execution_precondition_failed` 不是最优下一步（理想是按码给
  "腾一格 / 先顶下金装"）。这一轮没动工具层，出路靠 `message` 与 `blockers` 带出去。
- 守门在 `tests/test_build_execution_feasibility.py`（(c) 段：指纹相同只能靠复检、格满、换金装）
  与 `tests/test_snapshot_version_fingerprint.py`（换一颗功能模组作废候选；预留额度与其余执行现场
  不进指纹）。三条注入都验证过会变红（见 plan/提交说明）。
