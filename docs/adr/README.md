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
| [ADR-010](010-rotations-are-mostly-hand-maintained.md) | 周常轮换只有突袭/地牢（里程碑）与夜幕/宗师（组件 204）是官方数据，其余靠自维护周期表 + 锚点；没锚点的（遗失区域）只给候选 | accepted |
| [ADR-011](011-exotic-exclusivity-follows-unique-label.md) | 异域互斥按 Manifest 的 `uniqueLabel` 判（不是"全身一件"），槽位按 `equipmentSlotTypeHash` 取；判据必须同时作用于 `equip` 与 `equip_many` | accepted |
| [ADR-012](012-free-socket-writes-are-a-format-bug.md) | 免费插槽写入（护甲模组/子职业/Perk）走 API 是**线上格式写错了**（字段名 `itemId`、hash 要无符号），不是"应用没权限"；**推翻 ADR-002** | accepted |
| [ADR-013](013-armor-mod-insertability-is-per-character.md) | 护甲模组能不能插，看角色级可插入清单（组件 207 `characterPlugSets`）而不是 Manifest 的 plug set；1676 是"插入条件没满足"，不许说成"去游戏里装" | accepted |
| [ADR-014](014-tuning-writes-need-ownership.md) | 调谐能用免费接口换，判据是**这件护甲允许的清单**（组件 310；不在里面的回 1675，1679 不是失败）；写入已开放为正式能力；`平衡调整` 是**最低三项各 +1**，不是六维各 +1 | accepted |
| [ADR-015](015-solver-objective-and-ranking.md) | 配装求解口径：上限是**软**的（排名 + 标注，不剪枝）、排序只有 `goodness_key` 一把尺子、调谐吃干净但是**局部**最优、`reachable` 是**保守下界**、没算完不许说不可行 | accepted |
| [ADR-016](016-host-compat-scalar-arguments.md) | 只发标量的宿主（豆包 connector）：结构化参数额外收文本写法（规则唯一出处 `utils/arg_text.py`），`equip_build` 也收 `execution_id` 代替整块 `canonical_build`；不许放宽 schema、不许给 `canonical_build` 开 JSON 字符串通道 | accepted |
| [ADR-017](017-exact-exotic-name-skips-a-round.md) | 金装名字**唯一精确匹配**时直接求解（响应 `query.exotic_resolution` 交代用的哪件），模糊/多件仍然必须停下来确认；`resolve_exotic_armor` 的 `status` 语义成为对外契约 | accepted |
| [ADR-018](018-already-there-is-not-a-failure.md) | 1679「这个槽已经装着它」= 状态已成立（三条写入路径共用一条判据）；预检里「这一位装不上」的模组标 `blocked` 并跳过，**不许**把整条配装打成失败 + 回退 | accepted |
| [ADR-019](019-disambiguation-is-not-a-failure.md) | 「同名多件，先选一件」回 `item_disambiguation_required`（没写也没失败），不是 `move_failed`；候选只在信封里发一份，`next_actions` 告诉调用方拿 `question` 去问 | accepted |
| [ADR-020](020-functional-mods-are-copied.md) | 社区配装的**功能模组照抄**（抗性/搜寻/回收这类流派取向，不进求解器）；求解器只让出它们占的能量，插不进/装不下就跳过并点名 | accepted |

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
