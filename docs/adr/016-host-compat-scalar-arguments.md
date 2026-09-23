# ADR-016: 结构化参数额外收一种文本写法；`equip_build` 也能只收 `execution_id`

- Status: accepted
- Date: 2026-09-23
- Decision By: maintainer
- Scope: `destiny_mcp/utils/arg_text.py`、`destiny_mcp/tools/_coerce.py`、`destiny_mcp/tools/_param_docs.py`、`destiny_mcp/tools/_armor_branches.py`、`destiny_mcp/services/build_candidates.py`、`skills/destiny2-mcp/references/routing.md`

## Context

用户 2026-09-23 在豆包上实测：同一个服务端、同一份参数，官方 SDK 能通、宿主 connector 不通。
证据（连接器文档 + 用户复现脚本 + 本地审计）：

- `list[str]` 参数送不到：`weapon_assistant(intent="catalog")` 回显 `required_perks=[[]]`；
- `dict` 参数整块消失：`subclass_assistant(intent="modify")` 回 `invalid_arguments`，消息里连
  `changes` 这个名字都没出现；
- 排除服务端问题：同一台服务器用官方 SDK 调同一组参数正常。

也就是说，connector 只序列化标量，**参数根本没到服务端** —— 我们的校验没错，是这条路走不通。
受影响的是 perk 筛选、批量装备、子职业修改、配装硬约束，以及整条"确认后装备"
（`canonical_build` 是结构体，几 KB、五件护甲逐件模组）。

约束：仓库的规矩是"禁止兼容、不做补丁"——不许为了迁就宿主放弃校验（放宽到 `Any`），
也不许让模型自己把几 KB 的 JSON 转义一遍（出错率比收益高）。宿主怎么序列化不在我们控制内。

## Decision

**一、结构化参数额外收一种文本写法，原生形态不变。**

列表写 `"A,B"`、映射写 `"k=v,k2=v2"`；分隔符只收中文名字里不会出现的几个
（半角/全角逗号、顿号、半角/全角分号、换行），键值之间收 `=`、`:`、`：`。
三处硬口径：

- **规则唯一出处** `destiny_mcp/utils/arg_text.py`（分隔符、键值写法、错误话术）；
  `tools/_coerce.py` 只登记"哪个参数是什么类型"，不许在别处再写一份 `split(",")`
  （历史上 `weapon_roll_filter_service._split_terms` 就自己写过一份，只有半角逗号）。
- **只解析一次**：在 `@handle_tool_error` 之内、模型校验与参数归属检查之前还原，
  下游看到的都是解析后的值。空串按"没传"（与 `null` 同一条哨兵规则），
  写法不对回 `invalid_arguments` + 可照抄的例子，**不许静默丢掉一半条件**。
- **两种形态在 schema 里并列**（`array`/`object` + `string`），宿主看得见自己能用哪种；
  登记的每个参数都必须真的开出 `string` 分支，签名里"想收文本却没登记"的则判红。

被否掉的方案：让宿主改（不在控制内）；schema 放宽到 `Any`（等于放弃参数校验）；
给 `canonical_build` 开一条"字符串化 JSON"通道（把几 KB 的转义工作丢给模型）。

**二、`equip_build` 收 `canonical_build` 或 `execution_id`（二选一）。**

装备的凭据本来就是"服务端签发"，不是"调用方回传的内容"：服务端把签发的那份存在进程内，
执行前按 ID 取回并核对玩家绑定、10 分钟 TTL、内容一致、一次性。
所以只给 ID 就够了，而且**内容根本不经过调用方**——比回传整块 JSON 更不可能被篡改。
配套两处新增字段（都不是替换）：候选行顶层的派生 `execution_id`（标量与
`canonical_build.execution_id` 同源，不存第二份）与确认回显里的 `execution_id`。

候选暂存随之从 `services/build_service.py`（贴着体积上限）拆到
`services/build_candidates.py`；`resolve()` 用
`ok`/`unknown`/`expired`/`player_mismatch` 状态表达失败，而不是合并成一个 `None`。

## Consequences

- 每个新增的结构化参数都要回答"文本写法是什么"并登记，否则 schema 守门会红
  （`tests/test_connector_scalar_params.py`）。这是有意的摩擦。
- 文本形态只在工具层成立：进程内直调服务层仍只认 list/dict（模型不经过工具层，
  也就没有"参数写错却以为传对了"的空间）。
- `canonical_build` **不**收字符串化 JSON：要装备就两条路 —— 整块回传，或给 ID。
  两套做法做同一件事会立刻分叉（谁为准、怎么校验），所以只留一条替代路径。
- 旧宿主不受影响：`list`/`dict`/整块 `canonical_build` 全是一等公民，没有任何旧键被删。
- 代价：`_coerce` 与 `arg_text` 成为新的守门面，改分隔符要同时改文档、schema 说明与语料。
