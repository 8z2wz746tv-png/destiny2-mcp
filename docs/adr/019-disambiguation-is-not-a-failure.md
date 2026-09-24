# ADR-019: 「同名多件，先选一件」不是失败

- Status: accepted
- Date: 2026-09-24
- Decision By: maintainer
- Scope: `destiny_mcp/error_codes.py`、`destiny_mcp/tools/_responses.py`、`destiny_mcp/tools/assistants.py`

## Context

**What changed**：真机做改账号调用走查时，让 `inventory_assistant(intent="move")` 只报名字去搬一件
有 8 个同名副本的护甲（`千码凝视`）。回来的信封是：

```json
{"ok": false, "error": {"code": "move_failed", "message": "找到 8 件匹配 '千码凝视' 的物品，请选择具体实例。"},
 "candidates": [ ...8 件... ]}
```

三处问题叠在一条回执里：

1. **码说错了责任**。`move_failed` 是「写入失败族」（`write_failed(intent)`），而这一次**什么都没写**：
   服务层在 `len(candidates) != 1` 时提前返回，缺的只是"玩家选哪一件"。调用方按码判断，
   会把这条读成"搬失败了"，然后**重试同一个调用** —— 再撞一次同样的回执。
2. **同一份候选发了两遍**：信封 `candidates` 与 `data.result.candidates` 各一份（`_action_response`
   既把候选塞进信封，又把整个领域结果放进 `data.result`）。8 件候选 ≈ 2.4 KB 里有近一半是重复。
3. **`next_actions` 是空的**：可用的下一步（"把 `question` 原样给玩家、等他回编号"）当时只写在
   `models/transfer.py` 的字段说明和 `legacy/` 的旧工具 docstring 里 —— 工具面看不到。

被否掉的选项：

- **保持 `move_failed`，只加一句说明**：码是调用方唯一稳定的判断依据，靠 message 补救等于没有契约。
- **把候选从信封里挪到 `data.result`（或反过来）**：`candidates` 是跨工具的统一字段（确认信封、
  缺件清单都用它），收敛到信封才叫"一处"；`data.result` 保留 `question`/`needs_disambiguation`
  这些领域语义，去掉重复的那份清单。
- **给「要你选一件」也回 `ok: true`**：账号没被改动、对话也还没结束，报成功会让模型直接收尾。

## Decision

1. 新增字面量码 `ErrorCode.ITEM_DISAMBIGUATION_REQUIRED = "item_disambiguation_required"`；
   `recoverable=true`，`candidates` 放候选，`data.result.question` 原样展示给玩家。
2. 统一信封 `_responses.action_response()` 里判定：`payload["needs_disambiguation"]` 为真走
   `disambiguation_response()`，否则才是 `write_failed(intent)`。
3. **候选清单只在信封里发一份**：`action_response` 把 `data.result` 里的 `candidates` 摘出来
   （同一次 dump 出来的副本，摘掉不丢信息）。
4. `next_actions` 由工具层给出："把 `data.result.question` 原样展示（编号已列好），拿到编号后带
   `item_instance_id` 重发同一个 intent；在这次调用返回前账号没有被改动。"

## Consequences

- 调用方按 `item_disambiguation_required` 就知道"没写、等你选"，不会再重试同一个调用；
  `move_failed` 从此只表示真的写失败了。
- 载荷变小：真机 8 件候选的信封 2.4 KB（此前同样的候选发两份）。
- 这是**对外契约变化**：按 `move_failed` 判断"歧义"的调用方要改判据（本项目只有测试这么做，
  已在 `tests/test_assistant_action_results.py` 与 `docs/testing/TESTING_CORPUS.md` 的码表同步）。
- 同一族的后续判断沿用这条口径：**"需要你补一个选择"不是失败**，写入失败族只留给真失败。
