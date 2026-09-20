# 架构决策记录（ADR）

这里只记**决定了什么、为什么**，以及被否掉的选项和代价。进度、实测数据、逐次取舍不在这里 ——
那是 `docs/plans/` 的事（分工见 `AGENTS.md` 的「决策记录（ADR）」段）。

守门测试：`tests/test_agent_docs.py`（编号连续无重复、每条都有 Status/Date、本清单与实际文件双向一致）。

## 索引

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [ADR-001](001-canonical-build-strategy.md) | Canonical build strategy：配装分三层类型，只有 `ExecutableBuild` 能执行 | accepted |
| [ADR-002](002-armor-mods-require-in-game.md) | 护甲模组只能游戏内装：工具只列清单，且不因此回滚已换上的装备 | accepted |
| [ADR-003](003-measurement-beats-docs.md) | 实测优先于文档：冲突以真机结果为准并留痕，官方快照不进仓库 | accepted |
| [ADR-004](004-component-numbers-are-named.md) | 组件号只能来自 `profile_components.py`；读 305 必须带清单类组件 | accepted |
| [ADR-005](005-career-numbers-follow-in-game-counters.md) | 生涯数字以游戏内计数器为准、统计接口只作明细；三档三来源不许混 | accepted |
| [ADR-006](006-pvp-weapon-board-is-a-window.md) | PvP 武器榜只能是"最近 N 场"：上游没有生涯口径，只能逐场 PGCR 聚合，窗口与"只统计自己那一行"必须自证 | accepted |
| [ADR-007](007-weapon-lists-carry-list-rows.md) | 「我有哪些武器」给列表行（不带插槽池/可换项，默认 10 件 + 翻页），单件明细走 `compare` | accepted |
| [ADR-008](008-legacy-tool-face-archived.md) | 历史工具面整块剥离到 `legacy/`（不进包/测试/lint），工具面固定 8 个聚合工具；配装导入一并放弃 | accepted |
| [ADR-009](009-pattern-progress-lives-in-records.md) | 锻造武器模式（红框）的进度只在 profile 记录组件（900）；目录按"记录名 == 可锻造武器名"取 183 条（不是 `is_craftable` 的 219 件） | accepted |

## 编号规矩

- 文件名 `NNN-kebab-title.md`（现状即此形），标题行写 `# ADR-NNN: 标题`；`NNN` 是三位零填充。
- 编号**连续、不复用**：新决定取当前最大号 +1；旧决定被推翻时**保留原文件**并把 `Status` 改成
  `superseded by ADR-NNN`，不要删文件、不要留空号。
- **本项目从 `ADR-001` 重新计数**：前身项目的编号（005/007/008…）不带过来，这里的编号只属于本仓库；
  代码与文档里写 `ADR-NNN` 就必须真有那一篇 —— `tests/test_agent_docs.py` 会扫，指向不存在的 ADR 直接红
  （以前 `models/loadout.py`、`build/models.py` 引用过从未落盘的编号，就是靠这条规矩清掉的）。
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
