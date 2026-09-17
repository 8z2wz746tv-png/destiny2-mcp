# 架构决策记录（ADR）

这里只记**决定了什么、为什么**，以及被否掉的选项和代价。进度、实测数据、逐次取舍不在这里 ——
那是 `docs/plans/` 的事（分工见 `AGENTS.md` 的「决策记录（ADR）」段）。

守门测试：`tests/test_agent_docs.py`（编号连续无重复、每条都有 Status/Date、本清单与实际文件双向一致）。

## 索引

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [ADR-007](007-canonical-build-strategy.md) | Canonical build strategy：配装分三层类型，只有 `ExecutableBuild` 能执行 | accepted |

## 编号规矩

- 文件名 `NNN-kebab-title.md`（现状即此形），标题行写 `# ADR-NNN: 标题`；`NNN` 是三位零填充。
- 编号**连续、不复用**：新决定取当前最大号 +1；旧决定被推翻时**保留原文件**并把 `Status` 改成
  `superseded by ADR-NNN`，不要删文件、不要留空号。
- 现存正文从 `ADR-007` 起：代码里还引用着更早的 `ADR-005`、`ADR-008`，但那些决定的正文从未落盘
  —— 这是历史欠账，**不补写、不复用编号**，新决定一律往后排。
- 一条 ADR 只做一个决定。两件事写两条，互相在正文里引用。

## 什么时候必须写一条

写一条的成本很低，漏一条的代价是下一个人把推翻过的决定又提一遍。以下四种情况必须写：

1. **改对外契约**：响应形状、工具面与 intent 取值、错误码语义、skill 文档承诺的口径。
2. **破坏性变更**：删旧键、删别名、改默认值这类会让既有调用方行为变化的事。
3. **推翻既有决定**：改写一条已存在的 ADR（新写一条并在正文里说明取代了谁）。
4. **上游限制类的结论**：不是我们选的，但会被反复问到 —— 例如"护甲模组必须游戏内装"、
   "Manifest 里没有字段 X"。写下来，免得每次重新试一遍。

## 模板

照 `TEMPLATE.md` 抄：标题行 + 元信息（`Status` / `Date` / `Decision By`，需要时加 `Scope`），
然后 `## Context`（含 **What changed**）、`## Decision`、`## Consequences`。

- `Status` 取值：`accepted` / `superseded by ADR-NNN` / `rejected`（记被否掉的方案时用）。
- `Date` 写决定日期 `YYYY-MM-DD`；回顾补记的写补记日期，并在正文里说明是补记。
- `Decision By` 写拍板的人或角色（个人项目可写 `maintainer`）；不要留空。
