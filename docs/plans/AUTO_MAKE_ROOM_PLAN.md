# 自动腾格（make room）方案

**目标**：任何"挪动装备"的操作撞上**目标格满**（上游 `DestinyNoRoomInDestination`）时，
**自动腾出一件**（判据照 DIM），而不是把问题丢回给用户让他进游戏手点。用户原话
（2026-10-06）："所有挪动装备遇到这样的条件都直接执行这个"。

**为什么现在做**：2026-10-06 真机上连着撞了两次 —— 求解出来的候选明明能装，
却因为"术士臂铠格 10/10"被 `requires_preparation` 拦下，话术是"**先在游戏里腾出一格**"
（`build/execution_diagnosis.py`）。同一天我们还试过手动腾格（`move`）才把演练做完 ——
**这一步没有任何判断，纯粹是工具该做而没做的事**。

**DIM 的做法**（本机源码 `~/项目/DestinyItemManager/DIM`，v8.143.0，2026-10-06 读）：

- 挂机收（farming）：某格"满到只剩 N 格"（N = 用户设置 `inventoryClearSpaces`）→ 挑一件**进仓库**
  （`src/app/farming/actions.ts:166-222`）。
- 手动搬/装备时目标满：先试**别的角色**（从最久没玩开始）→ 都不行才进仓库
  （`src/app/inventory/item-move-service.ts:655-742`）。
- **挑哪一件**：候选 = `!equipped && !notransfer`，再按
  `sortMoveAsideCandidatesForStore` 排序取第一件（`item-move-service.ts:1138-1212`）：
  ① 不穿着的优先 ② 同部位优先 ③ 不在游戏内配装槽里的优先 ④ 角色用不了的优先
  ⑤ 标签顺序 `归档→灌注→没标→垃圾→保留→最爱`（`dim-item-info.ts:61-71`）
  ⑥ 稀有度低优先 ⑦ 主属性/光等低优先；另有"刚手动搬过的尽量别动"。

## 一、口径（先定下来，写进 ADR-029）

1. **只在已经 `confirmed=true` 的写入里自动腾**。只读调用（`find`/`list`/`get`）绝不偷偷写账号；
   对未确认的调用，仍然只**预告**"确认后会腾哪几件"。
2. **只搬仓库**（不跨角色）。比 DIM 少一条路：仓库是唯一目标，行为可预期、可还原；
   仓库也满 → **如实失败**，不递归挤仓库（DIM 会递归，我们不做）。
3. **绝不腾**（五条，任何一条命中就跳过它）：
   - 正穿着的；
   - **锁定的**（比 DIM 保守：DIM 这段代码里没按锁过滤，我们按锁当"用户在意"）；
   - 本次配装计划里要装的（别把马上要穿的件腾走）；
   - 当前正在某个**游戏内官方配装槽**里使用/引用的（读得到，见 `loadout_official_identifiers`）；
   - `notransfer`（任务/不可转移）。
4. **单次最多腾 3 件**；需要更多 → 如实拒绝并说明差几件。
5. **全程可见**：预演里预告要腾哪几件 → 结果里逐件记 `action="make_room"` 的 step
   （复用现有 `steps` 结构，**不新增响应字段**）→ 摘要里点明"已自动腾出 X"。
6. **腾出去的件记进恢复点**：沿用 `equip_with_recovery` 的恢复机制，失败回滚时把它们放回原处。

## 二、挑件算法（我们的版本）

候选 = 目标格（bucket）里、且不在"绝不腾"五条里的件。排序（**从最可能被腾到最不可能**）：

| 序 | 判据 | 出处 |
|---|---|---|
| 1 | 稀有度低优先 | DIM ⑤⑥ |
| 2 | 光等/主属性低优先 | DIM ⑦ |
| 3 | 非大师化优先 | 我们补（DIM 无此字段） |
| 4 | 非巧匠（artifice）优先 | 我们补 |
| 5 | 不在官方配装槽里的优先 | DIM ③ |
| 6 | 名字稳定排序（同名多副本按实例 ID） | 保证确定性 |

**与 DIM 的差异（都写进 ADR）**：没有标签系统（跳过 DIM ⑤）；不跨角色（少一条路）；
按锁过滤（更保守）；有单次上限；不递归挤仓库。

## 三、接在哪（四个入口 + 一个兜底）

| 入口 | 现状 | 接法 |
|---|---|---|
| `build_assistant(intent="equip_build")` | `recheck_confirmed_build` 用 `execution_blocker` 拒绝（"先在游戏里腾"） | 预检里发现"格满"→ 确认后先腾 → 再执行原计划 |
| `inventory_assistant(intent="move")` | 直接吃上游 500 | 撞到 `NoRoomInDestination` → 腾一件 → **重试一次** |
| `inventory_assistant(intent="equip"/"equip_many")` | 同上 | 同上 |
| `loadout_assistant(intent="equip_loadout")` | 走 `equip_with_recovery` | 在恢复点捕获后、应用前腾格 |
| **兜底**：`tools/_write_failure_hints` 的 `NoRoomInDestination` 分支 | 现在只说"先清出位置" | 改成"已尝试腾格：腾了 X／腾不出来因为…" |

**复用已有的判断**：`build/execution_feasibility.py` 已经在算"哪些格满了、仓库里有多少件搬不进来"
（`Armor.execution_blocker` 那句"术士的臂铠格已经满了"就是它写的）。**不另写一套满格判断**。

## 四、落地步骤

- **P0（不接线，可单独验）**：新模块 `services/make_room.py` —— 纯挑选函数
  `pick_move_aside(items, bucket, *, reserved_ids, official_ids, limit)` + 搬运执行。
  单测覆盖：五条"绝不腾"各一条、排序六条各一条、上限、仓库满。
- **P1（今天撞的那条）**：接 `equip_build`；预览里预告；结果里记 `make_room` steps；
  改 `execution_diagnosis` 的话术（"确认后会自动腾出 X"）。
- **P2**：接 `inventory move/equip/equip_many` 与 `equip_loadout`；兜底重试一次。
- **P3**：文档与技能同步（`TESTING_CORPUS.md` 的 steps 说明、`routing.md` 的"格满了会怎样"）。
- ~~**P2**~~ / ~~**P3**~~ / **P4**（补做）**已完成**（2026-10-06）：
  · P2 落地为 `equip_build` 撞上游 `NoRoomInDestination` 时按回执点名的件腾一格并**重试一次**（`services/make_room.py::equip_with_make_room_retry`）；
  · P4 把同一套编排接到**会搬运**的另外两条入口：`move` 与 `equip_loadout`（`services/transfer_service.py::make_room_in_bucket` + `_transfer_item_with_make_room`）。**`equip`/`equip_many` 不需要接** —— 它们走上游 `EquipItem`、不搬运，撞不到这条错（实测）；
  · 真机验证过：`equip_build`、`move`、`equip_loadout` 三条都跑通（原文见 CHANGELOG 与 ADR-029 补记）。
  · 途中抓到 4 个只有真机才暴露的缺陷并全部修掉：挑中仓库件（搬到仓库 = 没腾）、腾动让候选指纹过期、写入同步窗口（回读到的还是旧状态）、一次格满按默认 3 腾了三件、以及仓库件`bucket_type`/`slot` 报的是 `Vault (General)`/空串。
- **P5（计划外补做）**：`equip_loadout` 批量装备**之前**顶下冲突金装 —— 见 `docs/adr/030-dequip-conflicting-exotics-before-batch.md`。

## 五、验收

- **守门**：每个新判据一条单测；注入验证（去掉"绝不腾"的任一条 → 必须变红并逐字节还原）。
- **真机**：制造"臂铠格 10/10 + 候选在仓库"的现场（2026-10-06 有现成现场），
  走 `equip_build` 一次过；对照演练前后的 `move` 台账，确认腾的是**按判据该腾的那件**。
- **不回归**：全量 `pytest`；`equip_loadout` 的"格满 → 自动腾 → 装成 → 回读通过"；
  失败回滚时腾出去的件**回到原处**（回读核对）。

## 六、待拍板

1. **锁定的件**：我建议**绝不腾**（比 DIM 保守）。你也可以选"照 DIM，锁不影响"。
2. **上限 3 件**：够不够？超过就如实拒绝。
3. **只搬仓库**：还是也像 DIM 一样先试**别的角色**（多一条路，能少占仓库）？
