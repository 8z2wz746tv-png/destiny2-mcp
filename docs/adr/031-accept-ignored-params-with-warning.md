# ADR-031: 没认领的参数照做并警告，不再整通拒收

- Status: accepted
- Date: 2026-10-09
- Decision By: maintainer
- Scope: `server.py`（参数守卫分支）、`tools/_param_contracts.py`（参数归属表不变）、`tools/_responses.py`（回放动作）、`tests/test_ignored_parameters.py`（断言方向反转）

## Context

调用方把参数传给"当前 intent 不读"的字段时，服务端**整通拒掉**并回 `ignored_parameter`，
不调用服务层。这条口径的本意是**禁止静默忽略**（"描述里承诺的每个值都必须能被代码解析"那一族）。

代价由审计量出来了（2026-10-09，全量 5407 次调用）：

- 这类拒收 **247 次**（占全部失败的 18%），而拒掉的参数**全都是无害的多余参数**：
  - `inventory_assistant(intent="summary")` 多带 `item_name` —— **106 次**
  - `inventory_assistant(intent="get")` 多带 `item_instance_id` —— **25 次**
  - `weapon_assistant(intent="type")` 多带 `weapon_name` —— **10 次**
- 每一次拒收 = 用户多等一个模型往返（实测中位约 40 秒 —— 整链 87% 的时间花在调用之间）。
- 全部 1405 次失败合计烧掉约 21.5 小时，其中 87% 是"失败之后模型那一段"。

**What changed**：这条口径在 2026-10-09 之前没有 ADR 记载（只在 `server.py` 的分支与守门测试里），
本 ADR 是对既有实现口径的**明确修改**，不是补记。

## Decision

**没认领的参数不再中断调用**：照 `intent` 正常执行，并在回执 `warnings` 里顶格说明
"这个参数这次没用上 + 该用哪个 intent"。

三条不变：

1. **参数归属表不变**：`tools/_param_contracts.py` 仍是唯一出处，认领了必须真读。
2. **仍然不静默**：忽略必须出现在 `warnings` 里（守门断言其存在），且 `summary` 里点一句。
3. **传了值却解析不了照样失败**：词表/枚举不认识的值仍然报错（那不是"多传"，是"传错"）。

被否掉的方案：

- **保持整通拒收**（现状）：白跑一个模型往返；实测这类参数 100% 无害。
- **静默忽略**（不写 warnings）：违反"不许静默降级"，且模型永远学不会正确用法。
- **按参数纠偏 intent**（例如看到 `weapon_name` 就转成 `search`）：工具替调用方猜意图，
  猜错会给出**看起来对、其实不是他要的**结果 —— 比白跑一轮更糟。

## Consequences

- `ignored_parameter` 这个错误码**保留**（schema 层拒收、真正非法的传参仍会用它），但
  "该 intent 不读的字段"不再产生它。
- 回执体积略增（`warnings` 一条），可忽略。
- 风险：模型把参数传给错的 intent 时，工具会**照那个 intent 执行**，结果可能与模型本意不同。
  缓解是 warnings + summary 双向提示；接受这个风险，因为现状是白跑一轮后模型照样要重发。
- 回退点：本 ADR + `tests/test_ignored_parameters.py` 的断言方向。
