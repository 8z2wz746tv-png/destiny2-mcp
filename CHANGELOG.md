# Changelog

按日期倒序。版本号来自 `pyproject.toml`，tag 用 `v<版本>`。

## 0.1.10 — 2026-09-14

**按另一台机器的实机复盘（Windows / 另一个 agent）修「否定结论的表达」**。那次事故的链条是：
模板要求「玻璃拱顶 ×4」→ 工具标 `unresolved`、调用方标「❓待自查」→ 最后却回了「这套能直接玩」，
而同一份响应里明明写着 `execution_supported=false` / `execution_eligible=false`。
根因不是缺数据，是**否定结论没有被推到调用方一定会看到的地方**。

- **summary 现在带可执行性判定**：社区详情返回「已读取社区配装：X；**不可直接执行**（社区模板不是服务器签发的 ExecutableBuild）」，
  并把 `execution_eligible=false` + 首要 blocker + 「要装备必须走 find → canonical_build → 确认」
  放进**第一条 warning**。summary 是 agent 唯一一定会引用的字段，判定不能再只躺在 payload 里。
- **活动名 → 套装名解析**：社区模板按活动称呼套装（玻璃拱顶），Manifest 与玩家物品用套装名
  （埃希恩记忆）。已核对的映射自动解析并说明来路（`resolved_via`/`alias_from`/`resolved_name`）；
  没登记的给 `unresolved_reason=name_not_matched_candidates_available` + `set_name_candidates`
  相似候选，让调用方去问用户，而不是只剩「查不到」。
- **「这套我有几件」能直接读**：套装行新增 `owned_count` / `owned_distinct_slot_count` /
  `missing_slot_count` / `wildcard_count`（以前只有列表，没人去数——复盘里就是「❓待自查」）。
  实机复验（同一套配装）：`玻璃拱顶 → 埃希恩记忆`，需 4 件、持有 20 件覆盖 5 个部位、缺 0。
- **「没校验」与「没有」彻底分开**：所有 `not_account_checked` 行带 `unverifiable_reason` 枚举
  （`mod_unlock_state_not_available` / `artifact_*` / `subclass_unlock_state_not_available` /
  `stat_feasibility_not_checked` / `free_text_not_checkable` …）；
  `unresolved` 行带 `unresolved_reason`；武器行补
  `alternate_perk_options_reason=instance_socket_options_not_read`，并把警告改成明说
  「`owned_no_current_roll_match` 是『当前选中的 Perk 不符』，**不是**『这把枪不行』」。
- **参数说明补「哪些 intent 不读它」**：`character` 那栏列出会返回 `ignored_parameter` 的 intent；
  `component` 那栏写明「碎片不是一类 component，要用 `intent=fragments`」
  （复盘里这两处各浪费了一次调用）。

**仍未做（写在明处）**：`alternate_perk_options_checked` 目前恒为 `false` + 说明原因 ——
真正的「可切换但未选中」比对需要武器详情载荷带上实例可换项（组件 310），
属于武器面的改动（有自己的基线与体积上限），留作下一步。

## 0.1.9 — 2026-09-14

**干净安装冒烟（从 GitHub 装 v0.1.8 到全新 venv）抓出来的两处一致性问题。**

- **包内 `__version__` 停在 0.1.0**：不管发到哪个版本，`destiny_mcp.__version__` 一直写着 0.1.0。
  现在改成"源码树读 `pyproject.toml`、安装后读发行版元数据"，`tests/test_package_version.py`
  把"两处一致"钉住，避免再漂。
- **注册模板的客户端超时 180 秒余量太薄**：无解诊断实测 80–165 秒（还要叠加调谐补齐那一趟），
  慢一次就会被客户端掐断、用户看到的不是阶梯而是超时。模板与文档统一改成 **300000 ms**。

冒烟结果（可复现步骤见 `scripts/`）：从 `git+https://…@v0.1.9` 装进全新 venv → 8 个工具握手成功 →
复用 tokens/manifest 后真机读取正常；技能安装器在临时 `DSH_HOME` 里正确写出指针块与 MCP 注册文件。

## 0.1.8 — 2026-09-14

**真机写入实测抓出来的两个发布阻断问题**（用户要求"全部实测一遍"，这次实测值回票价）。

**① 写入失败被报成成功（真 bug）**
`ArmorModService.apply()` 拿到的是老约定形状 `{"ErrorCode": …, "Message": …}` —— Bungie 把
**错误也放在 200 响应的信封里**。以前这里不看内容直接 `success: True`：实测换调谐时账号
**一个字节没变**，工具却回"已把槽 11 换成 +手雷 / -职业"。现在必须核对 `ErrorCode == 1`，
否则抛 `TransferError` 并把 Bungie 原文带出来；权限类错误（`AccessNotPermittedByApplicationScope`）
会点名 `AdvancedWriteActions`，不再被读成"稍后重试"。回归测试写进 `tests/test_equip_mod.py`
（假客户端改成 Bungie 的真实信封形状 —— 以前回 `{"success": True}`，正好把这个 bug 遮住了）。

**② 调谐根本写不进去（实测结论，改设计）**
- 免费插槽接口（`InsertSocketPlugFree`）对调谐回 `This action can only be done in-game.`（ErrorCode 1663）；
- 付费接口（`InsertSocketPlug`）要 Bungie 应用的 `AdvancedWriteActions` 权限，当前授权没有 → 403。

所以 0.1.6/0.1.7 里"`canonical_build` 已带上调谐插件、确认即可执行"是**做不到的承诺**，这版改掉：
`equip_mod` 对调谐直接给方案（`writable=false` / `written=false` + 「只能在游戏内改」的 warning，
`confirmed=true` 也不写账号）；`canonical_build.items[].mods` 不再包含调谐插件；
`tuning_changes` 明确是**给玩家的手动清单**，`tuning_note` 也照此改写。
（这也解释了为什么"改完调谐才达标"的方案必须把清单交给玩家：六维达标以玩家手动改完为前提。）

**③ 顺带**：README 补了「写入权限」「调谐只能游戏内改」「无解诊断 80–165 秒 → 客户端超时建议 300 秒」；
语料护甲章节加了两行（调谐只给方案不写、付费写入如实报权限），共 26 行。

## 0.1.7 — 2026-09-14

**池子上限这条边界（实测发现并修掉）**：求解器只保留排名前 200 套（`RETURNED_ARMOR_SETS`，
DIM 的原始设计），而放宽那一趟的池子是按**放宽后的目标**排名的 —— 可救的方案可能排在
200 名之外，于是"明明能补却说补不上"。实测（猎人 118 件，武器150+生命103）：
池 200 时救回 **0** 套，放到 1500 时救回 **16** 套。修法有两步：
`solve()` 加了一个**可选**参数 `returned_sets`（不传就是老行为），放宽那一趟传 1500；
池子变大后不能每套都精确复核（每套约 0.1 秒，1500 套要三分钟），所以按
"护甲 + 调谐额度之后还差多少"排序，**只复核最值得的前 40 套**（复核仍是唯一裁判）。
修完实测：武器150+生命103 从 0 候选变成 **5 套候选、每套只改 1 件调谐**（47 秒）；
武器150+生命106 变成 5 套、2 件调谐（36 秒）。

**已知限制（写在明处）**：救援覆盖的是"放宽解排名前 1500 套里、且复核能过"的方案；
再往后的套仍然看不到（要彻底解决得像 DIM 那样把调谐变体放进主循环，本实现的组合上限
扛不住那个展开量）。挑候选用的是算术估计，理论上可能把可救的套排在 40 名之外。

## 0.1.6 — 2026-09-14

**调谐（Tuning）真的进求解器了**（P7）：以前"差 5 点"只能给一句人工提示，现在
`find`/`recommend` 会**真的去改调谐再算一遍**，能补上就直接给带方案的候选。

- **怎么做的**（两趟 + 精确复核）：按原目标解一次，达标就原样返回（基线里那些绿方案一个字没改）；
  没达标才用"调谐额度"把目标放宽复解一遍，然后对候选**逐套精确复核**——用真实目标重新分配
  属性模组、逐项比对六维，过了复核才算数。这样不动求解器里已经验证过的精确数学。
- **对外字段**：`builds[].tuning_changes`（逐件 `from`/`to`/`delta`，中文名 + hash）、
  `requires_tuning`、`tuning_note`，以及响应级 `tuning` 汇总；`canonical_build.items[].mods`
  里已经带上要装的调谐插件，`equip_build` 直接就能执行（调谐能量为 0，走免费插槽接口）。
- **`equip_mod` 支持调谐**：报全名（如 `"+武器 / -生命值"`）即可换调谐，方案里给
  `kind="tuning"`、`stat_bonus`（含 −5 那一侧）与 `energy`（不变）；
  只说"手雷调谐"这种**没讲减哪一项**的说法会报错并列出全部 5 个选项，不替用户猜。
- **阶梯口径改实**：`tuning_first` 现在是"已经试过调谐"之后的结论，并新增
  `solver_attempted` 与三种 lever（额度够但让不出来 / 额度不够 / 只看属性模组）；
  新增 `verdict`：`satisfiable=false` = 「原样」那一档实采 0 候选，并说明 `ceiling`
  是各次探测**逐项**取的最大值、不等于同一套能同时达到。

**实机验证**（118 件护甲的猎人，只读）：

- 调谐额度实测：每个部位取最强的一件，五项合计 25 点（含"撤掉反向调谐"的 +10 情况，
  所以单件上限是 10 不是 5）；
- 一个真实救援案例：`武器150 + 生命103` 严格解 0 候选，放宽复解后给出 4 套达标方案，
  逐件列出调谐改动（如「光泽胄盔 +职业/−近战 → +生命值/−超能」）；
- 一个真实"补不上"案例：`武器150 + 生命106` 给出 `verdict.satisfiable=false`——
  手算边界一致（护甲 110/52 + 模组 50 + 调谐最多 33，凑不出 150/106）。

**对比 DIM（读源码后的结论）**：DIM 是在主循环里展开调谐变体（非金装逐件展开成多个
ProcessItem，金装因为一套只能有一件、改成在主循环里换 variant），并用"牺牲哪一项"
（dump stat）把变体数压到个位数。这个 Python 实现扛不住那种展开量（组合上限 2000 万），
所以采用同样思路的收敛版：**零和语义 + 牺牲价值最低那一项**，但把"能不能达标"交给
精确复核裁决，另外加一个复核驱动的局部搜索兜底（最多改 5 件，每步都过复核）。

**过程中的实机 bug（都是先算错、再被抓出来的）**：调谐额度把整个仓库相加（118 件 →
"每项能补 563 点"）；规划基线把求解器已配的模组又加一遍（同一笔模组算两次）；
剪枝用"每件最多 +5"，漏掉"撤掉反向调谐 = +10"与模组预算，把可行组合整支砍掉；
`plan_tuning` 把 −5 打在本就为 0 的项上当成有代价（游戏里属性夹在 0，白给）。

## 0.1.5 — 2026-09-13

**L2 冒烟集全跑（25 条 ⭐ 行）抓出来的两个问题。**

- **我自己的回归（P1）**：0.1.4 重构 `analyze` 的提前返回时，把
  `precision="not_computed"` 那一行一起删掉了 —— 结果超规模时 `reason` 说的是
  "没算"，`precision` 却报 `exact` 且 `max_possible={}`，调用方会读成"你什么都达不到"。
  已补回，并新增 `tests/test_build_size_guard.py`（4 条）把这条口径钉死：
  超限必须 `precision="not_computed"` + 空 `max_possible`，两条路（analyze / find）同一句说明。
- **语料自己写错了路径（文档 bug）**：武器 T 级那条原来写 `analyze` 的 `weapon.gear_tier`，
  实测 `analyze` 的 T 级在 `data.inventory.instances[].weapon.gear_tier`（逐副本），
  `type` 才是 `weapons.items[].weapon.gear_tier`；而且 `gear_tier_note` 并非处处都有
  （`owned.instances[]` 里有，`analyze` 的副本块只有 `gear_tier` 本身）。语料行按实测改写，
  并给武器逐行脚本加了第 17 行锁住这三条路径（真机 `type.gear_tier=5`、遗产各副本 `[None, 5]`）。

冒烟集结果：**26/26 PASS**（25 条 ⭐ + 工具面核对），覆盖 8 个工具、写入拦截、
参数误用指路、schema 层拒绝、社区资料不可信提示、护甲四条新行。

## 0.1.4 — 2026-09-13

**修 0.1.3 实测跑出来的 5 个问题**（都是护甲那轮改动暴露的）：

- **`rarity` 中文值被静默忽略**：参数说明写着"传说/异域"可用，但代码只映射英文，取不到就
  直接跳过过滤 —— 问"我有哪些异域腿甲"会把传说件一起端回来（实测 `异域`=26 件、`exotic`=8 件）。
  现在中英同结果，**不认识的稀有度直接报错并列出可用取值**（照 `item_type` 的封闭词表做法）。
- **`recommend`/`find` 组合规模超限会跑到客户端超时**：术士同参数 `analyze` 秒回
  "2.43 亿组合超上限、未计算"，而 `recommend` 会真的去枚举，客户端拿到
  `-32001 Request timed out`。现在两道门共用同一个估算与同一句收窄建议，
  超限立刻返回 `not_computed` + 四条可操作建议（**不是**"无解"）。
- `equip_mod` 的 `alternatives[].stat_bonus_hashes` 口径不一致（原始 hash，还把非六维的
  "费用"属性算进去）→ 改成与 `to.stat_bonus` 同一套六维可读键。
- `with_slot_keys` 会把展示字段塞进 `canonical_build.items[]`（目前 pydantic 容忍，但违背
  "canonical 只放可执行内容"）→ 递归时跳过 `canonical_build` 子树，并加断言。
- `intent="item"` 的 `next_actions` 还写着"换模组后续阶段提供" → 改成指向 `equip_mod` 的正确用法。

顺带修了差异闸门自己的一个小 bug：`--allowlist` 传相对路径时，报错分支会
`relative_to` 崩掉，把真正的字段消失吞成一条 traceback。

语料：护甲章节 18 → 20 行（新增稀有度中英一致/乱填报错、组合规模超限两行），
冒烟 ⭐ 25 条；`diff_weapon_baseline.py` 的路径处理加注释说明。

## 0.1.3 — 2026-09-13

**护甲：统一格式 + 换单个模组 + 无解时的六维阶梯。**

以前护甲在六个地方出没、字段各不相同，而且**换不了单个模组**（默认工具面里没有写入入口，
legacy `apply_mod` 又不确认直接改账号）。这一版按 `ARMOR_FORMAT_PLAN.md` 的 P0–P6 做完：

- **统一载荷**（`ArmorPayload`）：`identity`（`slot`/`slot_display`/`gear_tier`/`archetype`/套装）+
  `instance`（光等/位置/能量/大师/调谐）+ **三层属性** `roll`/`base`/`final` + 插槽清单。
  列表保持轻量（只加 `slot`/`slot_display`/`gear_tier`/`armor_system`，`bucket_type` 保留），
  要看插槽走新 intent。
- **新 intent `inventory_assistant(intent="item")`**：单件护甲的完整载荷。词条本体槽与
  `intrinsics` 标 `editable=false`。
- **新 intent `inventory_assistant(intent="equip_mod")`**：换一个模组。`confirmed=false` 给
  「哪件护甲、哪个槽、从什么换成什么、能量怎么变」的确认请求，确认后才写；校验实例在不在该角色身上、
  该槽收不收这个模组、能量够不够。**legacy `apply_mod` 收编**到同一条确认路。
- **无解时的 `ladder`**：`shortfall`（差多少）、`ceiling`（同一套约束下**同时**能达到的上限，
  实采）、`single_stat_ceiling`（单项上限，两者不能混）、`trials`（逐级放松试了哪些档）、
  `suggestion`（最小可行降档，**只是提议**，不自动降目标）。
- **装备确认逐件预览**：`candidates[0].items_preview` 给五件的光等/能量/现有模组/将要装的模组。
- 实机勘测修掉的两个真问题：老护甲也带 `gearTier: 0`（按字段分族会误判成 3.0）；
  异域护甲的固定属性分布在 `intrinsics` 里（不算进 `roll` 会把大师等级算成 30）。
- 文档：`routing.md` 补护甲统一键口径、`find` vs `recommend` 分工（**要装备走 `find`**）、
  `ladder` 读法；`TESTING_CORPUS.md` 新增「十六、护甲」章与逐行脚本
  `scripts/run_corpus_armor_rows.py`（12 行）；护甲基线 19 例（`--surface armor`）。

## 0.1.2 — 2026-09-13

**装给正在跟你说话的那个 Agent。**

以前的 `install_skill.py` 会把技能铺给**本机探测到的每一个宿主**（Codex、Claude、Cursor…），
对一个新用户来说这是错的：他是在某个 Agent 里提问，只想让**那个** Agent 用上这套 MCP。

- 默认只装当前宿主，靠环境变量认：`DSH_HOME`/`DSH_SHELL` → dsh、`CLAUDECODE` → claude、
  `CODEX_*` → codex；认不出来才退回旧行为（已存在的都装）。`--all` 保留全装，
  `--host <name>` 显式指定，`--list` 会标出"← 当前"。
- 新增 DeepSeek Harness 宿主：技能根 `~/.dsh/skills/`（DSH 会**热发现**，装完不用重启），
  全局指令文件 `~/.dsh/AGENTS.md`。
- 新增 `--mcp`：注册 MCP 服务器。DSH 直接幂等写入 `$DSH_HOME/profiles/web/cordis.patch.yml`
  （带 `destiny2-mcp:mcp-begin/end` 标记，先备份、重复跑只更新），工具以 `mcp__destiny__*`
  出现；Claude Code / Codex 只**打印**可以直接粘的 `claude mcp add` / `codex mcp add` 命令，
  不替用户改它们的配置。
- README / AGENTS.md / 安装 Skill：把"装给提问的 Agent"写成显式规则，并补上 DSH 的注册步骤。

首次安装的耗时预期也写进了 README：`pip install -e .` 约 5 分钟（下依赖，无进度条）、
预构建 Manifest 685 MB 约 5 分钟。

## 0.1.1 — 2026-09-13

**修 P0：干净环境装出来起不来。**

- 根因：`pyproject.toml` 只写了 `mcp[cli]>=1.27.2`，从零安装解析到 **mcp 2.2.0**；
  2.x 把 `mcp.server.fastmcp` 改名成 `MCPServer`，服务在 import 阶段就炸，
  自检报 `VERIFY_FAILED=MCPError: Connection closed`。开发机装着 1.x，本地测不出来。
- 改成 `mcp[cli]>=1.27.2,<2`，并新增 `tests/test_dependency_bounds.py`
  （上界 + lock 钉 1.x + 代码确实用 v1 API，三条一起才算完整约束）。
- 验证方式：从 GitHub 克隆到干净目录 → `python -m venv .venv` → `pip install -e .`
  → 下载预构建 Manifest → OAuth 登录 → `verify_mcp.py` 全绿（8 工具、
  `BUNGIE_PROFILE_CHECK=ok`）→ 12 项真机冒烟全部符合文档。此时装到的是 mcp 1.30.0。

`v0.1.0` 的 tag 停留在修复前，请用 `v0.1.1`。

## 0.1.0 — 2026-09-13

第一次公开快照：本地运行的 Destiny 2 MCP 服务器，通过 8 个面向自然语言的聚合工具
（`player_assistant` / `inventory_assistant` / `weapon_assistant` / `build_assistant` /
`loadout_assistant` / `subclass_assistant` / `activity_assistant` / `world_assistant`，
共 108 个 intent）读取 Bungie 账号、Manifest 定义与本地社区资料。

**能做什么**

- 只读为主：角色概况、模糊找人、仓库/背包检索、武器词条与 god roll 标注、护甲词条反推、
  商人货架、单场结算、收藏品解锁状态、官方配装与本地配装读取。
- 写入类（转移、装备、改模组、存配装）一律先返回确认请求，`confirmed=false` 时**不触碰账号**；
  装备配装还要求传回服务端签发的 `canonical_build`，自拼 hash 会被拒。
- 证据分层：账号数据、Manifest 定义、社区资料三类来源在响应里分开放，缺数据就说缺数据，
  不把"没扫完"答成"你没有"。

**安装与运行**

- Python 3.12+；`pip install -e .`；自带 OAuth 登录助手与 MCP 自检脚本。
- 首次启动需要约 717 MB Manifest（可先取 `manifest-data-v1` 预构建库）。
- 只暴露 8 个聚合工具；69 个历史工具要 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 才出现。

**这一版为公开发布做的准备**

- 修正 `README` 推荐的启动方式：`python -m destiny_mcp.server` 会让配装求解的子进程起不来，
  改为 `python -m destiny_mcp`。
- 自检脚本不再误报：`DESTINY_OAUTH_REDIRECT_URI` 未写进 `.env` 时按运行时默认值判定；
  缺 Manifest 时自动把超时放宽到 1800 秒并打印原因；venv 路径与 token 权限检查按平台分支
  （Windows 不再必失败）。
- 登录助手缺 `openssl` 时给人话提示并指向 `--manual`，不再裸抛 traceback。
- `equip_build` 传错 `canonical_build` 时改成中文说明（缺哪些字段、该先跑哪个 intent），
  不再直接甩 pydantic 的英文堆栈。
- 护甲模组筛选补 `match` 口径：词表外的词只在名字/描述里蒙中时标 `kind="keyword"` 并给 warning，
  词表内 0 条时说明"本地数据里没有"，不再让 `速度` 这类词安静返回一堆无关模组。
- 补 `LICENSE`（MIT）与 `pyproject` 的 license 元数据；README 增加「前置条件与已知限制」，
  写明平台支持、审计日志落盘位置（`~/.destiny_mcp/audit/`，明文、不上传）、
  跑测试需要 `pip install -e ".[dev]"`。
- 删除长期失效的 `Dockerfile`（引用了不存在的 `src/`）；补 `tests/conftest.py`，
  干净克隆（没有 `.env`）也能跑全量测试。

**已知限制**：见 README「前置条件与已知限制」与 `TESTING_CORPUS.md` 的「已知问题」。
