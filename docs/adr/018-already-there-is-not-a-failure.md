# ADR-018: 「已经装着」与「这一位装不上」都不许把整条配装打成失败

- Status: accepted
- Date: 2026-09-23
- Decision By: maintainer
- Scope: `destiny_mcp/services/loadout_mod_sockets.py`、`destiny_mcp/services/loadout_equipment_service.py`、`destiny_mcp/services/loadout_subclass_sockets.py`、`destiny_mcp/services/armor_mod_service.py`、`destiny_mcp/tools/_armor_branches.py`

## Context

为性能计划第七项记基线时，真机跑了三次 `equip_build`，**三次都失败**（335 / 398 / 309 秒），
每次都走完整回退（~215 秒：5 件搬回仓库、装回原护甲、逐颗恢复模组、回读核对），
而账号从头到尾都是好的。三个根因叠在一起：

1. **1679 `DestinySocketAlreadyHasPlug` 被当成失败。** 上游把它包在 **HTTP 500**
   （"The request to modify an item failed. Refresh the item and try again."）里回，
   `equip_build` 的模组循环只看 `ErrorCode != 1`；客户端还会为这个 500 退避重试四次 ——
   真机实测每颗白花约 10 秒。`equip_mod` 那条路早就把它当"已是目标状态"，两条路不一致。
2. **子职业插槽必然重写。** `execution_subclass` 就是按"当前装备的子职业"读出来的，
   于是每次 `equip_build` 都把同样的 plug 再写一遍 → 每一颗都回 1679 →
   在这个 bug 修掉之前，**每一次装备都会失败并回退**。
3. **预检里"这一位装不上"的一颗模组直接抛错。** `_prepare_mod_operations` 撞到组件 207
   清单里没有的模组就 `raise TransferError` → 整条配装失败 + 回退 ~4 分钟。
   这与 ADR-013 已经定下的口径（"这一位装不上就如实报，装备照换"）是相反的：
   写入阶段早就把这类模组记成 `mod_blocked` 而**不回退**，预检阶段却把它升级成了致命错误。

## Decision

**两条判据，写入路径统一遵守：**

- `plug_already_installed(result)`（单一出处，`services/loadout_mod_sockets.py`）：
  1679 = 想要的状态**已经成立**，算成功；回执写"已经装着 X，未改动"。
  `equip_build` 的模组循环、子职业 plug 循环与 `equip_mod` 三条路共用它。
- 预检发现"这一位装不上"的模组 → 标成 `blocked`（`ModOperation` 带 `reason`），
  **跳过这一颗、不回退整条配装**，回执里给出插入条件；摘要在有 blocked 时改成
  "配装已装备，但有 N 颗模组装不上"，原因进 `warnings`。

另外两条配套（同一件事的另一半）：

- 预检与子职业写入**先比对现状**：已经装着的那颗**不调用上游** ——
  那次注定 1679 的写入连同它的四次重试（约 10 秒/颗）直接省掉；
- `steps` 里写**模组名字**而不是 hash：它是调用方唯一的写后证据，
  写 hash 会逼调用方再逐件查护甲（真机实测一次链路因此多 5 次往返）。

被否掉的方案：把预检的 `TransferError` 换成 warning 后继续写（写下去必然再撞 1676，
等于拿一次注定失败的写入换一次报错）；只修 `equip_build` 不修子职业那条路
（那样每次装备仍然必然失败 —— 这是当时最贵的那个 bug）。

## Consequences

- 一次装备从"335 秒 + 失败 + 回退 + 调用方重试"变成"**64.7 秒成功**"（真机同日实测）。
- 代价：`ok=true` 不再等于"所有模组都装上了"。调用方必须看 `steps` 里的 `mod_blocked`
  与 `warnings` 才能说全 —— 这是有意的：**宁可把"哪一颗没装上"说清楚，也不回退**。
  守门在 `tests/test_equip_build_candidate_id.py`（有 blocked 时摘要与 warnings 必须露出来）
  与 `tests/test_armor_mod_unlock.py`（预检标 blocked、1679 算成功、已装的不排写入）。
- `ModOperation` 成为预检与写入之间的契约（`action/plug_hash/socket_index/reason`），
  新增一种 action 必须同时定义"要不要写、要不要回退"。
