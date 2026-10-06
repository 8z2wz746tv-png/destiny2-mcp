# ADR-025: 候选在**写成功之后**才焚烧，被拦下的执行不消耗它

- Status: accepted
- Date: 2026-10-06
- Decision By: maintainer
- Scope: `services/build_candidates.py`、`services/candidate_messages.py`、
  `services/build_service.py`（`equip_build`）、`services/build_execution_guard.py` 这条链

## Context

`execution_id` 是 `find` / `recommend` 签发、**留下服务端**的那份方案，`equip_build`
只认服务端自己那份（不认调用方回传的内容）。签发之后它有两个独立的闸门：

1. **写之前的执行前提复检**（`services/build_execution_guard`）：重取现场 → 执行前提
   （格子满、与正穿着的金装冲突 1641）→ `snapshot_version` 指纹 → 实例核对；
2. 写入本身（搬运 → 装备 → 插模组 → 回读，失败会回滚）。

**What changed** —— 原来的做法写在 `BuildCandidateStore.consume` 的 docstring 里：
*"一次确认只能执行一次：执行前就烧掉，失败也不还。"* 即候选在**闸门 1 之前**就被消费掉，
理由是防重放的直觉：万一第一次其实写进去了、只是响应丢了，第二次就该被挡住。

2026-10-06 真机踩到它的代价（用户报的直接卡点）：

- `equip_build` 因为"指定的金装与当前穿着的那件冲突"被**闸门 1** 拦下 ——
  **账号一个字节没改**；
- 调用方按提示去处理冲突（先用一件非异域的顶下那件金装），拿**同一个** `execution_id`
  重试 → `unknown_execution_id`；
- 于是整条候选作废，只能重新求解 + 重新让用户确认一遍。

同一天还量到：被闸门 1 拦下与"候选失效"是**两件完全不同的事**，但错误码当时分不出来 ——
`unknown_execution_id` 的话术是"已失效或不属于当前玩家，请重新求解"，把调用方指向了
重解，而正确的下一步是"改完前提直接重试"。

## Decision

**`consume()` 只在写入成功（`result.success`）之后调用**；被闸门 1 拦下、或写入失败的执行
**不消耗候选**。

配套三件必须同时成立，否则分不出来：

1. `BuildCandidateStore` 焚烧时**留墓碑**（ID → (时刻, 属主)），不是抹掉 ——
   这样 `resolve()` 能返回第四个状态 `consumed`，与 `expired`、`unknown`、
   `player_mismatch` 分开；墓碑按同一条 TTL 清理（留久了是内存泄漏，
   而且那么老的 ID 报"过期"更有用）。属主先判：别人的 ID 一律 `player_mismatch`，
   不泄露"这个 ID 存在过"。
2. 新增错误码 `ErrorCode.USED_EXECUTION_ID`（`used_execution_id`）——
   "用过了，要再装一次请重新求解并确认" ≠ "已失效，请重新求解"。
3. 四个状态的话术**只有一处**：`services/candidate_messages.py`
   （`CANDIDATE_FAILURES` + `candidate_failure()` + `describe_candidate()`）——
   以前这套话术在 `build_candidates` 与 `equip_build` 各写一份，措辞已经开始漂。

**被否掉的方案**：

- *"延长 TTL / 让调用方手动续签"*：治不了病。真机上候选失效与时间无关，
  是**被消费**了；而且"重签"仍然要走一遍求解 + 用户确认。
- *"闸门 1 拦下时自动替调用方处理冲突（顶下金装再重试）"*：越权。
  顶下那件金装是**改玩家的配装**，属于要用户拍板的动作；闸门 1 的职责只是拒绝与说明。
- *"干脆不焚烧（永久可重放）"*：丢掉防重放。成功那次仍然要烧。

## Consequences

- **重放保护没有削弱，但形状变了**：以前靠"提前烧"，现在靠两层 ——
  ① 成功才烧；② 写之前**每次都**重读现场并比对 `snapshot_version`
  （`build_execution_guard`），所以一个旧候选写不进一个变了的账号。
  代价是"成功"的定义要准：`equip_with_recovery` 返回 `success=True` 才算成功
  （回滚过的算失败，候选留着 —— 那种情况下账号已经回到执行前的样子）。
- **调用方看到的错误码多了一个**：`used_execution_id`。任何按"unknown/expired 两分"
  写分支的地方都要加上第三支（本仓已改：`candidate_messages.CANDIDATE_FAILURES`
  是唯一出处）。
- **`equip_build` 的重试语义变宽了**：同一个 `execution_id` 现在可以在"没写成功"之后
  反复用。这不等于"可以拿旧候选写新状态"—— 闸门 1 每次都会重读现场。
- 要改这条决定，得同时改：`build_candidates`（焚烧时机 + 墓碑）、
  `candidate_messages`（四个状态的话术与错误码）、`build_service.equip_build`
  （调用点）、以及 `tests/test_exact_build_execution.py` 的
  `test_a_refused_execution_does_not_burn_the_candidate`（核心守门，注入验证过）。
