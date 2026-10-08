# ADR-029: 挪装备撞上目标格满时自动腾一件（判据照 DIM）

- Status: accepted
- Date: 2026-10-06
- Decision By: maintainer（口径由用户拍板："所有挪动装备遇到这样的条件都直接执行这个"）
- Scope: `services/make_room.py`（新）、`services/build_service.py`、`services/loadout_exact_flow.py`、
  `services/loadout_equipment_service.py`、`tools/_write_failure_hints.py`、`tools/_armor_branches.py`、
  `build/execution_feasibility.py`、`build/execution_diagnosis.py`

## Context

上游 `TransferItem` 在目标格满时回 HTTP 500 `DestinyNoRoomInDestination`
（`There are no item slots available to transfer this item.`）。我们现在的处理是**把问题丢回给用户**：

- `build/execution_diagnosis.py` 把它写成"术士的臂铠格已经满了 …… 那些件照样参与求解"，
  出路是"**先在游戏里腾出一格**"；
- `tools/_write_failure_hints.py` 撞到它时只说"目标位置空间不足：先清出位置，或换一个目标角色/仓库"。

2026-10-06 真机上连着撞了两次：求解出来的候选明明能装，却因为"术士臂铠格 10/10"被
`requires_preparation` 拦下；当天做完基线演练，也是**手动**先 `move` 腾格才继续得了 ——
这一步没有任何判断，纯粹是工具该做而没做的事。

**What changed**：读了 DIM（本机 `~/项目/DestinyItemManager/DIM`，v8.143.0）的真代码，
确认这件事有一套成熟判据，不是"随便挑一件"：

- 挂机收：某格"满到只剩 N 格"（用户设置 `inventoryClearSpaces`）→ 挑一件进**仓库**
  （`src/app/farming/actions.ts:166-222`）；
- 手动搬/装备时目标满：先试**别的角色**（从最久没玩开始）→ 都不行才进仓库
  （`src/app/inventory/item-move-service.ts:655-742`）；
- 挑哪一件：候选 = `!equipped && !notransfer`，按 `sortMoveAsideCandidatesForStore`
  （`item-move-service.ts:1138-1212`）排序取第一件 —— 不穿着的 → 同部位 → 不在游戏内配装槽 →
  角色用不了的 → 标签顺序（`dim-item-info.ts:61-71`）→ 稀有度低 → 主属性/光等低。

## Decision

**任何"挪动装备"的写入撞上目标格满时，自动腾出一件（或几件），而不是报错让用户自己处理。**
落地成一份新服务 `services/make_room.py`，**所有入口共用它**（`equip_build`、`equip_loadout`、
`inventory move/equip/equip_many`），判据与顺序的唯一出处也在这个模块。

硬口径（比 DIM 更保守的地方都写明）：

1. **只在已经 `confirmed=true` 的写入里自动腾。** 只读调用（`find`/`list`/`get`）绝不写账号；
   对未确认的调用只**预告**"确认后会腾哪几件"。
2. **只搬仓库**，不跨角色。仓库也满 → 如实失败，**不递归挤仓库**（DIM 会递归，我们不做）。
3. **绝不腾**（任一命中即跳过该件）：正穿着的；**锁定的**（DIM 这段没按锁过滤，我们按锁当"用户在意"）；
   本次配装计划里要装的；当前被某个游戏内官方配装槽引用的；`notransfer` 的。
4. **单次最多腾 3 件**；需要更多 → 如实拒绝并说明差几件。
5. **全程可见**：预览里预告要腾哪几件 → 结果里逐件记 `action="make_room"` 的 step
   （**复用现有 `steps` 结构，不新增响应字段**）→ 摘要点明"已自动腾出 X"。
6. **腾出去的件进恢复点**：沿用 `equip_with_recovery` 的机制，失败回滚时放回原处。
7. **不另写一套"格满"判断**：复用 `build/execution_feasibility.py` 已有的结论
   （`Armor.execution_blocker` 那句"术士的臂铠格已经满了"就是它写的）。

被否掉的选项：

- **照 DIM 跨角色腾**：多一条路径、多一种"东西去哪了"的可能；仓库够大之前不做（P3 再议）。
- **递归从仓库再挤一件出来**（DIM 的做法）：容易连环搬动、失败面大，收益小。
- **只报错让用户自己腾**（现状）：2026-10-06 两次真机都卡在这，明确否掉。
- **在只读调用里也自动腾**：等于绕过"写入要用户确认"，否掉。

## Consequences

- **工具开始替用户做"搬哪件"的判断** —— 这是新的信任面：判据错一件就可能把用户在意的装备挪走。
  所以"绝不腾"五条与"可见 + 可还原"是这条决定的一部分，不是可选项。
- **没有标签系统**：DIM 的标签顺序（归档/灌注/垃圾/最爱）我们拿不到，排序里只能靠
  稀有度 / 光等 / 大师化 / 巧匠 / 是否在官方槽。用户如果有 DIM 标签习惯，这里会有差异。
- **仓库满了仍然要人出手**：不递归，所以仓库满时行为与现在一样（如实失败）。
- **改这些地方要同时改**：`docs/plans/AUTO_MAKE_ROOM_PLAN.md`（阶段与验收）、
  `docs/testing/TESTING_CORPUS.md`（`steps` 多一种 action）、
  `skills/destiny2-mcp/references/routing.md`（"格满了会怎样"）。
- 三个参数（锁是否参选、单次上限、是否跨角色）**按上面的默认值执行**；要改就改这一条 ADR 的
  §Decision 与 `make_room.py` 的常量，别在调用方各写一份。
