# ADR-017: 金装名字「唯一精确匹配」时直接求解，不再多问一轮确认

- Status: accepted
- Date: 2026-09-23
- Decision By: maintainer（用户明确拍板：这一轮往返可以合并）
- Scope: `destiny_mcp/tools/_build_confirmation.py`（`resolve_exotic`）、`destiny_mcp/services/build_service.py::resolve_exotic_armor` 的 `status` 语义、`skills/destiny2-mcp/references/routing.md`

## Context

旧口径是「指定金装首次查询必须返回候选并等玩家确认」（写在语料与技能文档里）。它的来源是
一次真实事故：调用方按模糊名字自行挑了一件金装就开解，玩家看到的是**另一件**的配装。

但这条口径被套在了**所有**金装请求上，包括名字唯一命中的那种。真机审计
（`~/.destiny_mcp/audit/`，2026-09-16 ~ 09-22）显示这多出来的一轮是实打实的成本：

```
14:07:27  find  0.0s   ← 只做了解析，回一句"请确认"，然后停住
14:07:48  find 21.2s   ← 同一组参数再来一次，这次才真求解
```

同一晚重复了七八次，每次都多一次完整往返（模型读候选 → 生成下一次调用 ≈ 20–40 秒）。
`resolve_exotic_armor` 其实**已经**把两种情形分开返回了：

- `status="exact"`：名字与官方中文名或英文名**逐字相同**，`matches` 只有一条；
- `status="confirmation_required"`：模糊命中或命中多件，`matches` 可能多条。

也就是说"要不要挑"这件事，解析器早就知道，只是调用方一律按"要挑"处理了。

## Decision

**只有真的存在选择时才停下来问。**

- `status="exact"` → 直接用那件求解，并在响应里交代清楚：
  `query.exotic_resolution = {status: "exact_match", name, name_en, item_hash, note}`，
  同时 `query.exotic_name` 是解析后的**规范名**（不是玩家口语的原文）。写入仍然要
  `confirmed=true`，与其它写入同一条门槛。
- `status="confirmation_required"` → **一个字都不改**：仍然返回
  `exotic_confirmation_required` + 每个候选的 `arguments`（含 `confirmed_exotic_hash` 与
  HMAC 凭据），仍然不启动求解。模糊名字依旧不许自行选定。

被否掉的方案：把确认整块删掉（就回到那次事故了）；只在"名字完全等于官方中文名"才算精确
（英文名精确匹配也是无歧义的，没必要多一轮）。

## Consequences

- 常见路径少一轮往返（真机实测每次 20–40 秒的模型回合 + 一次 0 秒的空调用）；
  名字打错、只记得一半的路径**代价不变**，仍然停在确认上。
- 调用方要读 `query.exotic_resolution` 才知道"有没有被自动选定"——旧调用方忽略它也不会
  出错（它只是多一个键），但会把"自动选定"当成"我传的名字就用上了"，两者此时是同一件，
  所以不会给出错答案。
- 解析器 `status` 的语义从此是**对外契约**的一部分：新增状态必须同时定义"要不要先确认"，
  否则这一条会被绕开。守门在 `tests/test_exact_build_execution.py`
  （精确 → 真跑求解；模糊 → 必须停）。
