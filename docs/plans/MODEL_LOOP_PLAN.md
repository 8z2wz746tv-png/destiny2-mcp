# 模型反复（宿主端循环）治理方案

目标：**让别的 AI 少走弯路** —— 把"模型必然会犯、而工具能兜住"的失败，从"整通拒掉 → 模型重来"
改成"照常办 + 明确告知"，并把"哪个宿主在反复"变成可测的数字。

不是文档问题：`skills/` 与工具描述已经把该说的都说了，实测仍然 25% 的调用失败。**根因在工具侧的
交互形状**，所以这份方案只改工具，不指望模型变得更聪明。

## 一、证据（全审计实测，2026-10-09 量）

- 审计 **5407 次调用 / 1405 次失败 = 25%**。
- 失败代价：**≈1288 分钟**（失败调用自身的服务端时间 173 分钟 + "失败之后模型那一段" **1115 分钟，
  占 87%**）。中位 5 秒、长尾几分钟 —— 与"整链 87% 时间不在服务端手里"同一结论。
- 失败构成（按码）：

  | 码 | 次数 | 有 `next_actions` 吗 |
  | --- | --- | --- |
  | `invalid_arguments` | 298 | ✗ |
  | `ignored_parameter` | 247（183 + 64） | 部分 |
  | `invalid_argument_error` | 159 | ✗ |
  | `confirmation_required` | 176（136 + 40） | 大半 ✗ |
  | `exotic_confirmation_required` | 80 | ✗ |
  | `config_error` / `a_p_i_error` / `build_validation_error` / `manifest_error` | 55 / 53 / 42 / 28 | ✗ |

  **参数形态三类合计 704 次 = 全部失败的 50%**；confirmation 类 216 次 = 15%。
- 四个真实案例（审计原文）：
  1. `inventory_assistant(intent="summary")` 多带 `item_name` → **整通拒掉**，共 **106 次**；
     `get` + `item_instance_id` **25 次**；`weapon_assistant(type)` + `weapon_name` **10 次**。
  2. `stale_inventory_snapshot` 的 `next_actions` 给的是
     `{"intent": "recommend", "character": "warlock"}` —— **换了词、且丢掉金装/套装/属性目标**，
     模型照做会求出**另一套配装**（15 次）。
  3. 审计**没有客户端标识**：2026-10-09 排查"这批调用是谁发的"时，只能按时间段猜，猜错两次。
  4. `confirmation_required` 176 次：模型漏 `confirmed=true`，而回执没有"把这次调用补上 `confirmed`
     原样重发"的信息。

## 二、四件事与验收口径

| 编号 | 做什么 | 现在的现象 | 验收（可量） |
| --- | --- | --- | --- |
| **C** | 审计记录 MCP 客户端名 | 分不出宿主，只能猜 | 每条审计带 `client`（名 + 版本）；能按宿主出报表 |
| **B** | 失败都带"照发就行"的调用 | 850+ 条失败无 `next_actions`；`stale` 那条还会换目标 | 每个 `error` 的 `next_actions[0]` 都能**原样回放**（参数与原调用一致）；守门逐类断言 |
| **A** | 多传参数不再整通拒 | 247 次`ignored_parameter` 整通拒 | 该计数归零；改出**顶格 `warnings`**（不是静默）；保留"参数归属"表 |
| **D** | 失败率常驻报表 | 疼了才查，修完会反弹 | 一条命令出「失败率 + 前十错误码 × 宿主 + 环比」，只读 |

## 三、逐条设计

### C —— 审计记录客户端名（先做，让后面可验收）

- 落点：`destiny_mcp/server.py`（工具装配处）→ `destiny_mcp/audit.py`（落盘字段）。
- MCP 会话自报家门：`ctx.session.client_params.clientInfo.{name,version}`（SDK 的 `ServerSession`
  有 `client_params`，`InitializeRequestParams` 带 `clientInfo`，已核实）。取不到就写 `""`，
  **不编名字**（缺值给空，不给 `"unknown"` 之类看着像结论的东西）。
- 审计新增字段 `client`（形如 `"doubao-connector/1.2"`），**旧条目没有该字段是合法的**（读取侧按缺失处理）。
- 守门：`tests/test_audit_client.py` —— 造一次带 `clientInfo` 的握手断言字段落盘；不带时写空。
- 风险：低（只加字段）。**注意**：`audit.py` 贴着体量上限，加字段前先看闸。

### B —— 每条失败都带"照发就行"的调用

- 落点：`destiny_mcp/tools/_responses.py`（信封）+ 新增 `destiny_mcp/tools/_replay_actions.py`
  （**按错误码生成可回放调用**的唯一出处），`_write_failure_hints.py` 保持"写入类关键词话术"不动。
- 规则（三条，写进 docstring）：
  1. **参数与原调用一致**：`next_actions[0].arguments` = 触发失败那次调用的参数 `+ 必要修正`
     （如 `confirmed=true`）；不许换 intent、不许丢参数。
  2. **`stale_inventory_snapshot` 这类"重来一次"**：回带**求解时的原始查询参数**（金装名、套装、
     属性目标、`top_n`）—— 现在给的是 `intent="recommend"`，会把用户的目标换掉，属于**正确性问题**。
  3. **每条 `error` 至少一条** `next_actions`；实在没有可回放的调用时，给一句**可执行的中文**（不是"重试"）。
- 守门：`tests/test_replay_actions.py` —— 逐个错误码断言 `next_actions[0]` 的结构与参数；
  再加一条**全量扫描**：`_responses.error(...)` 的所有调用点都传了 `next_actions`（禁止裸失败）。
- 风险：参数回带可能带上敏感/无用字段 → 只回带**该工具 schema 里声明过的**参数。

### A —— 多传参数不再整通拒（照做 + 大声说）

- 现状：`server.py:411` 对"该 intent 不读的参数"直接回 `ignored_parameter` 并**不调用服务层**。
- 改成：**照常执行** + 在 `warnings` 里顶格写：*"`item_name` 这次没用上；按名字找东西用
  `intent="search"`"*（保留原有的指路话术，只是不再中断）。
- **与既有口径的差别**（这也是要写 ADR 的原因）：原口径是"没认领的参数必须拒收"，本意是
  **禁止静默忽略**；新口径是"**照做 + 明确警告**"，仍然不静默 —— 但把一次白跑的往返省掉。
  参数归属表（`_param_contracts.py`）与"认领了必须真读"不变。
- 落点：`server.py`（拒收分支改警告）+ `tests/test_ignored_parameters.py`（改断言：从"必须拒收"
  改成"必须执行 + 必须出现在 warnings 里"）+ ADR-031。
- 风险：**误判风险**：模型把参数传给错的 intent（例如 `type` + `weapon_name`）时，新口径会**照
  intent 执行** —— 结果可能与模型本意不同。缓解：`warnings` 里同时写"你可能想用 `intent="search"`"，
  并在回执 `summary` 里点一句。这条风险在接受范围内（现状是白跑一轮、模型照样要重发）。

### D —— 失败率常驻报表

- 落点：`scripts/audit_failure_report.py`（只读；复用 `scripts/audit_chain_timing.py` 的读法）。
- 输出：总调用数 / 失败率 / 前十错误码（含**环比上一周期**）/ **按宿主**（依赖 C）/ 每个错误码的
  一个**真实样例**（工具、intent、参数、原文前 120 字）。
- 验收：一条命令、10 秒内出结果；`--json` 供将来接 CI。**只读**，绝不写账号。
- 守门：`tests/test_audit_failure_report.py`（用固定的小审计夹具断言统计口径）。

## 四、阶段与依赖

1. **C**（半小时）：只加字段。做完后 A/B/D 的效果都能按宿主验收。
2. **B**（主要工作量）：先 `stale`（正确性问题）、再 `confirmation` 类、再其余；逐个错误码上守门。
3. **A**：改口径 + ADR-031 + 改守门；改完跑一次真机（让豆包那侧自然撞到）。
4. **D**：收尾，出第一份基线报表（现在的 25% 就是基线）。

每条做完都：`pytest` 全量 + ruff + **注入验证**（去掉新行为必须变红）+（涉及行为的）真机一轮。

### 进度（2026-10-09）

| 编号 | 状态 | 证据 |
| --- | --- | --- |
| **C** | ✅ 完成、真机验过 | 审计出现 `client='dsh-mcp-client/0.0.1'`；守门 `tests/test_audit_client.py`（注入 58 红） |
| **B** | ✅ 完成（前半 `confirmation_required` + 后半 `stale`） | 前半真机：`next_actions[0]` = 补 `confirmed=true` 的完整调用；后半：候选里存下求解参数（`candidate_search_args.py`），失败时原样回带"用原来的条件重新求解"。守门 `tests/test_replay_actions.py`（注入 59/60/63 红） |
| **A** | ✅ 完成、真机验过 | 真机 `summary` + `item_name` → `ok: true` 且 `warnings[0]` 点名；`tests/test_ignored_parameters.py` 翻向 407 条（注入 61 红） |
| **D** | ✅ 完成 | `scripts/audit_failure_report.py`（按宿主 + 前十错误码 + 环比 + 真实样例）；守门 `tests/test_audit_failure_report.py`（注入 62 红） |

**第一份基线**（`--days 2`，2026-10-09）：250 次调用 / 失败 68（27.2%）；按宿主 247 条"未记录" + 3 条
`dsh-mcp-client`（C 刚上线）。注意这一窗口里包含**本轮自己的验证风暴**（大量故意失败的调用），
不要直接当成"真实使用"的失败率 —— 下一个窗口才是可比基线。

**还没做**：无（四件事全部落地）。

**真机上的一个意外收获**（2026-10-09，B 后半验证时撞到）：故意"先求解、再挪一件、再 `equip_build`"**没有**触发
`stale_inventory_snapshot` —— 写前准备动过账号后会把候选基线推到现在（P5 那条修的），**把这次失效吸收了**，
回执直接是"已装备，回读核对通过"。也就是说豆包那 3 次 `stale` 重试现在会直接消失，后半的"原条件回放"是
**兜底**（给写完仍失效、或不在写前准备路径上的失败用），只在单测/注入上验过。

## 五、明确不做

- **不指望模型读文档**：skill / 工具描述该写的继续写，但不作为治理手段。
- **不改模型的思考时间**（整链 87% 在这里，不是工具能动的）。
- **不放宽上游错误**：`a_p_i_error` / `config_error` 一类如实报，不重试、不掩饰。
- **不做"自动重试同一调用"**：失败重试必须由调用方决定（写账号的调用尤其）—— 除了已经落地的
  "格满/金装冲突"这类**工具自己知道自己做对**的场景。

## 六、待拍板

- **A 的口径**：是否同意把"没认领的参数必须**拒收**"改成"**照做 + 明确警告**"（ADR-031）。
  本次用户已表示"要修就全修"，落地时按同意处理；若日后要回退，回退点是 ADR-031 与
  `tests/test_ignored_parameters.py` 的断言。
