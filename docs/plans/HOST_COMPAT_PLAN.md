# 宿主兼容：只发标量的宿主怎么把参数送进来

**状态**：已落地（0.7.0）
**实机来源**：2026-09-23 用户实测（豆包 connector，Windows 机器 + `MCP_TRANSPORT=sse`）
**相关**：`docs/plans/EQUIP_FLOW_PLAN.md`（候选 → 确认 → 回读）、`docs/COMPATIBILITY.md`

## 一、问题：宿主只序列化标量

用户在豆包上连同一个服务端、同一份参数，官方 SDK 能通、connector 不通。实测到的现象：

| 现象 | 证据 |
| --- | --- |
| `list[str]` 参数被丢掉或变成嵌套空数组 | `weapon_assistant(intent="catalog")` 回显 `required_perks=[[]]` |
| `dict` 参数整块消失 | `subclass_assistant(intent="modify")` 回 `invalid_arguments`，消息里连 `changes` 都不提 |
| 同一服务端 + 官方 SDK 正常 | 排除服务端问题，是 connector 的序列化边界 |

不是我们的校验错了，是这条调用路径**根本没把参数送到**。豆包那台机器上因此用不了：
perk 筛选、批量装备、子职业修改、配装硬约束（`priority_stats`/`fragment_names`/`stat_caps`），
以及整条"确认后装备"（`canonical_build` 是结构体）。

## 二、决定：同一件事收两种形态，不是一个兼容层

被否掉的三个选项：

1. **让宿主改**——不在我们控制内；文档写了也不保证下游读。
2. **放宽 schema 到 `Any`**——等于放弃校验，`invalid_arguments` 也就没意义了。
3. **给结构化参数加一个"字符串化 JSON"通道**——整块 `canonical_build`（几 KB、五件护甲逐件模组）
   让模型自己转义一遍，出错率比收益高。

采用的做法：

- 结构化参数**多收一种文本写法**（列表 `A,B`、映射 `k=v,k2=v2`），schema 里 `array`/`object`
  与 `string` 并列 —— 宿主看得见自己能用哪种。原生形态仍是一等公民，两种形态走同一段解析。
- 解析规则唯一出处 `destiny_mcp/utils/arg_text.py`（分隔符、键值写法、错误话术）；
  工具层 `destiny_mcp/tools/_coerce.py` 只登记"哪个参数是什么类型"，并在
  `@handle_tool_error` 之内、模型校验与参数归属检查之前**还原一次** ——
  下游（分组校验、归属拦截、函数体、服务层）看到的都是解析后的值。
- 认的分隔符：半角/全角逗号、顿号、半角/全角分号、换行；键值之间认 `=`、`:`、`：`。
  空串按"没传"（与 `null` 同一条哨兵规则），写法不对回 `invalid_arguments` + 可照抄的例子。

历史遗留：`weapon_roll_filter_service._split_terms` 自己写过一份 `split(",")`，只有半角逗号 ——
同一个参数"换个写法就少筛一半"。现在它走同一份规则。

## 三、`canonical_build`：用 `execution_id` 代替整块结构体

装备配装的凭据本来就是"服务端签发"，不是"调用方回传的内容"：服务端把签发的那份存在
进程内（`services/build_candidates.py`），执行前用 ID 取回来核对玩家绑定、TTL 与一致性。
所以只给 ID 就够了，而且**内容根本不经过调用方**——比回传整块 JSON 更不可能被篡改。

- `build_assistant(intent="equip_build")` 现在收 `canonical_build` **或** `execution_id`（二选一）；
- 确认回显里带 `execution_id`（标量），宿主照抄就能再发一次；
- 候选行顶层也单独给一份 `execution_id`（`BuildResult` 的派生字段，不存第二份，不会分叉），
  只发标量的宿主不用钻进嵌套结构里取；
- 服务端校验不变：10 分钟 TTL、绑定玩家、一次确认只能用一次、内容一致才执行。

顺带把候选暂存从 `build_service.py` 拆到 `services/build_candidates.py`：那里贴着体积上限，
而"候选怎么存、什么时候过期"与求解流程无关；`resolve()` 用状态
（`ok`/`unknown`/`expired`/`player_mismatch`）而不是 `None` 表达失败，工具层才能给出对得上的话术。

## 四、守门与验收

| 守门 | 钉住什么 |
| --- | --- |
| `tests/test_connector_scalar_params.py::test_text_form_parameters_publish_a_string_branch` | 登记过的参数必须在 schema 里真的开出 `string` 分支（拿 `create_server().list_tools()` 核对） |
| 同文件 `::test_text_capable_parameters_are_registered` | 反方向：签名里"既收 list/dict 又收 str"的参数必须登记，不然就是悄悄少一个入口 |
| 同文件 `::test_the_nested_build_has_a_scalar_substitute` | `execution_id` 必须是标量参数 |
| 同文件 `::test_the_service_layer_uses_the_same_separator_rule` | 分隔符只有一份规则 |
| `tests/test_equip_build_candidate_id.py` | 只给 ID：确认回显带 ID 且零写入；确认后装的是服务端取回的那份；取不到候选指向重新求解 |
| `tests/test_ignored_parameters.py` | 参数归属双向核对（`execution_id` 只被 `equip_build` 认领） |
| `scripts/run_corpus_armor_rows.py` ⑯b | 真机：只给 `execution_id` 也能走确认，回显同一个 ID + 五件预览 |

两条 schema 守门都做过注入验证（把 `| str` 去掉、把登记项删掉，都会变红）。

## 五、没做的

- `canonical_build` **不**收字符串化的 JSON：那样等于把几 KB 的转义工作丢给模型，
  而且与 `execution_id` 是同一件事的两种做法。要装备就两条路：整块回传，或给 ID。
- 其他宿主（Claude Code、Codex、DSH）本来就是结构化参数，不受影响；
  文本形态是**并列**的第二种写法，不是它们的默认路径。
