# ADR-023: 独立回读要有**一次调用拿全**的入口（`inventory_assistant(intent="mods")`）

- Status: accepted
- Date: 2026-10-03
- Decision By: maintainer
- Scope: `destiny_mcp/services/inventory_service.py`、`destiny_mcp/services/armor_payload.py`、`destiny_mcp/tools/_armor_branches.py`、`destiny_mcp/tools/_requests.py`、`destiny_mcp/tools/_param_contracts.py`、`skills/destiny2-mcp/references/routing.md`

## Context

写入路径的 `steps[].verify` 是**写入流程内部的自证**。要拿第二条证据核对"现在到底装着什么"
（测试协议里的"独立回读"），以前只能逐件 `inventory_assistant(intent="item")` ——
那是 N+1：每件都重读一次整份 profile（语料实测 **13.9 秒/次**），核五件 ≈ **70 秒**。

而同一份响应里本来就有全部答案：组件 305 `ItemSockets` 一次覆盖**账号里全部物品**的已装插槽
（实测 3.47 MB / 0.85 秒），"哪五件在装备位上"在同一次响应的组件 205 里。
`intent="item"` 之所以贵，不是因为插槽贵，而是因为它给的是**一件的完整载荷**
（能量、三层属性、词条反推、能不能调谐）——那是另一个问题。

## Decision

新增只读 intent **`inventory_assistant(intent="mods")`**：一次 profile 读取
（组件 `INVENTORY_SOCKETS`）返回**每一位角色**身上五件护甲的插槽行；`character` 可选，
给了就只读那一位（不给就三位都读，各带职业标签）。

- 每件给 `{slot, slot_key, name, item_instance_id, item_hash, is_exotic, power, energy,
  mods[]}`，`mods[]` 与 `intent="item"` 的 `armor.sockets` **同一形状**
  （同一个形状工厂 `armor_payload.socket_rows`：`index/kind/name/plug_hash/energy_cost/empty`）——
  两边形状一旦分叉，"核对"就要两套读法，迟早给出不同答案；
- `mods` 与 `item` 不是别名：一个读**多件的插槽**、一个读**单件的完整载荷**，
  共用一段只读分派（`_armor_branches.armor_read`）但读的东西不同，所以登记进
  `tests/test_intent_aliases.py` 的 `STANDALONE`；
- 它**只读**：不判断"该不该"、不写入、也不替代写入路径的 `verify`。
  某位角色读回的护甲不是 5 件时在 `warnings` 里点名（某个部位没装备或读不到）。

被否掉的方案：

- **扩展 `intent="item"` 收多个实例 ID**：`item` 的语义是"一件的完整载荷"，
  而"不给 ID"目前是有守门测试的明确报错（缺参数）；改语义会同时弄浑两条读法。
- **让 `intent="item"` 不带 ID 时返回身上五件**：`character` 不在它的参数归属里
  （参数归属表是硬契约），要么静默读全部角色、要么放宽归属，两条都更糟。

## Consequences

- 核对五件从 **5 次调用 / ≈70 秒** 变成 **1 次调用**（组分 305 实测 0.85 秒）。
- 工具面多一个 intent：`_requests.InventoryIntent`、参数归属（`character` 归 `mods`）、
  `routing.md` 的索引与参数表、行为语料（`tests/agent_behavior_cases.yaml`、
  `docs/testing/TESTING_CORPUS.md`/`_FULL.md`）与语料 runner 必须同步 ——
  `tests/test_skill_contracts.py` 与 `tests/test_ignored_parameters.py` 会把漏掉的一处直接判红。
- 载荷按"每件十几个槽行"计（真实护甲 12 槽左右）：核对要看的是**槽里装着哪颗**，
  不是六维 —— 要属性/词条仍走 `intent="item"`。
- 守门：`tests/test_equipped_armor_mods.py`（一次调用、覆盖五件、与 `item` 同形状、分派不串台）。
