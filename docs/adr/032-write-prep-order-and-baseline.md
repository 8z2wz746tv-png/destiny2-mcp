# ADR-032: 写前把关在先，基线的"推进"只吸收我们自己的动作

- Status: accepted
- Date: 2026-10-09
- Decision By: maintainer
- Scope: `services/make_room.prepare_build_write`、`services/build_service.equip_build`、`services/inventory_service.get_armor_snapshot`、`build/snapshot_version.py`（新增 `substance_version`）、`build/execution_feasibility`（接线）
- 取代/修订：**ADR-024**（复检次序）、**ADR-025** 的"旧候选写不进变过的账号"（重申并重新做到）、**ADR-029 #6**（腾格进恢复点的承诺，改口径）、并**逐条点明** ADR-029/030 与 **ADR-022 / 024 / 025 / 027** 中被推翻的那几项

## Context

2026-10-09 的一次外部审查（opus5.5）指出：当天落地的"写前准备 + 基线推进"把 ADR 里明文否掉的方案做了回来，
并且**削弱了 `stale_inventory_snapshot` 这道防线**。我逐行核过，成立，而且比审查说的更严重：

1. `prepare_build_write` 在**每一轮复检之前**把 `build.snapshot_version` 覆盖成**当前**快照的版本
   （`make_room.py:418-428`）→ 复检里那句"指纹比对"变成**自己跟自己比** → 在这条路径上等于失效。
2. `recheck_confirmed_build` 的第一关（执行前提，`Armor.execution_blocker`）**是死代码**：
   `annotate()` 全仓**从未被调用**，那个字段恒为 `""`（`get_armor_snapshot` 也没接线）。
3. `build_service` 在写失败后**无条件**推进基线（`:761-762`）→ 求解到确认之间玩家在游戏里做的任何改动，
   只要这次碰巧触发了腾格或顶下，就会被一并"认可"。真机现象：**先求解 → 挪一件 → `equip_build` 照样装上**
   （我当天还把它记成了"收获"，那是把"问题不再报出来"当成了"问题解决了"）。
4. 写前准备在复检**之前**就改了账号（审查 #2），腾格也不在恢复点里（审查 #3，ADR-029 #6 的承诺未兑现）。

被否掉的方案在这几天被复活，却没有 ADR 说明推翻了谁 —— 违反 `docs/adr/README.md` 的第 3 条规矩。

## Decision

1. **先只读把关，再动账号**。`prepare_build_write` 第一步：
   `snapshot_version(当前现场) == build.snapshot_version`，对不上就报 `stale_inventory_snapshot`，
   **一个字节都不写**（腾格、顶下都不执行）。上游同步窗口用轮询吸收（不是变更）。
2. **指纹分两半**（`build/snapshot_version.py`）：
   - `snapshot_version`（含"放哪儿 / 穿没穿"）= 求解 ↔ 执行的一致性契约，**只在写前**用；
   - `substance_version`（`stats` / `energy_capacity` / `installed_mod_energy` / 件与件数）=
     我们**自己写完**之后允许变的只有"放哪儿 / 穿没穿"，物质层必须**逐字节一致**；
     不一致 = 别人动过这套里的件 → 报 `stale`。
3. **不再"无条件推进基线"**：推进只发生在"写前指纹对得上，且写完物质层没变"之后。
   写失败**不推进**（`build_baseline.rebaseline_note` 整块删除）：同一个 `execution_id` 重试会拿到
   **诚实的 stale**，模型按 `next_actions` 用原来的条件重解（那已是一句可照抄的调用，见 `_replay_actions`）。
4. **把执行现场接上**：`get_armor_snapshot` 末尾调用 `execution_feasibility.annotate`（写在这里而不是
   `from_profile`：那一层反向依赖会成环）。于是复检第一关与 `models` 的"砍掉多少件"不再是死代码。
5. **腾格/顶下不进恢复点**（改 ADR-029 #6 的口径，不装作已兑现）：它们的结果逐条记在 `steps`
   （`make_room` / `downgrade`）里、摘要里说明"腾了什么 / 顶下了谁"。理由：回滚一次腾格通常会让
   目标格重新变满，下次尝试还得再腾一遍；而"失败后账号被我们动过"这件事**已经写进回执**，不是静默的。
   代价如实记在这里：**失败之后用户账号可能与执行前不同**（只是可见、可解释）。

### 逐条点名：本 ADR 与 ADR-029/030 推翻了什么

| 被推翻的条款 | 原文 | 现在的口径 |
| --- | --- | --- |
| ADR-022 | "候选一定是能装的"（求解期就把装不上的件剔掉） | 保留：求解期不剔；**执行期**按当时现场判（本 ADR 第 4 条把这条路接上） |
| ADR-024 | "复检排在指纹比对之前、按当时现场判" | 保留，但**改次序**：写前先按指纹把关（本 ADR 第 1 条），准备动作之后按物质层与执行现场再判 |
| ADR-025 | "一个旧候选写不进一个变了的账号" | **重申**（被"无条件推进"破坏过，第 1/2/3 条把它重新做到） |
| ADR-027 | "腾哪一件是玩家的取舍，工具不替他决定" | **推翻**（ADR-029 + ADR-030）：工具会腾/会顶下，但**只在已确认的写入里**、结果逐条可见 |
| ADR-029 #6 | "腾出去的件进恢复点，失败回滚时放回原处" | **改口径**（本 ADR 第 5 条）：不回滚，改为"逐条记录 + 摘要说明" |
| ADR-030 的适用范围 | 只写 `equip_loadout` | 扩大到 `equip_build`（两条路共用 `_dequip_conflicting_exotics`） |

## Consequences

- **代价**：写失败后同一个 `execution_id` 不再能直接重试（会 `stale`）。用户多一步"重解" ——
  但因为 `next_actions` 现在带回**原来的条件**，那一步是一句照抄的调用，不是重新描述需求。
- **收益**：恢复"确认的是一套、写的就是那一套"；执行前提第一次真的生效（格满/金装冲突按**当时**现场判）。
- 守门：`tests/test_make_room.py::test_pre_write_gate_refuses_before_touching_the_account`（注入 64：
  去掉指纹比对必红）、`tests/test_exact_build_execution.py::test_a_failed_write_does_not_absorb_changes_into_the_baseline`。
- 还没做（如实记）：腾格/顶下的**回滚**（本 ADR 明确选择了不回滚 + 可见记录）；
  以及审查里的 #10（`notransfer` 不腾的规则代码里也没有）—— 需要先定"补规则还是改文档"。

## 落地时的一次口径修正（2026-10-09，试出来的）

第一版把关写的是"写前比**整份** `snapshot_version`，对不上就拒"。当场被一条既有测试打回：

> `test_equip_build_makes_room_when_the_bucket_filled_up_after_find`
> —— 用户求解完回游戏里捡了件、把臂铠格填满，工具的职责是**替他腾一件**（ADR-029），不是让他重新求解。

所以口径收窄成：**"放哪儿 / 穿没穿"是工具的业务**（腾格、顶下本来就要改它）；
**"这件是什么"（属性/能量/已装模组/件与件数）才是契约**。落地形态：
- 写前取一份基准快照（不比整份指纹）；
- 我们自己写完，拿 `substance_version(写前) vs substance_version(写后)` 比 ——
  一样 → 推进基线继续；不一样 → `stale`（"别人动过这套里的件"）；
- `execution_feasibility.annotate` 接上后，复检第一关按**当时现场**判格满/金装冲突（这才是 ADR-024 的原意）。

**未完成（交接）**：`tests/test_build_execution_feasibility.py` 里 3 条仍然红 ——
`test_equip_build_makes_room_when_the_bucket_filled_up_after_find`（夹具按"第 1 次读=满"钉死了读序，
现在基准读占了第 1 次）、`test_equip_build_still_refuses_when_nothing_can_be_moved`、
`test_equip_build_refuses_when_the_worn_exotic_changed`（期望的是格满那条消息，现在先报**金装冲突** ——
这恰好证明死关卡活了）。改法：夹具的"满格"要由**是否真的搬过**驱动、件池恒定（不能靠"件数变少"表达"腾走"）。
