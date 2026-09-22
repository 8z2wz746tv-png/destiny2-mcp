# Changelog

按日期倒序。版本号来自 `pyproject.toml`，tag 用 `v<版本>`。

## 未发布

**新增：`weapon_assistant(intent="analyze")` 带 Starside 社区块（作者推荐 + 实测数值 + 帧表）** —— 2026-09-23：

- 数据来自站点作者给的归档（5,760 物品 / 5,200 perk 的社区层），经 `scripts/import_starside_entities.py`
  转成按 hash 的紧凑文件（4,483 + 2,676 条、2.8 MB）；**hash 逐条校验、全有或全无**，
  真机审计 `scripts/audit_starside_entities.py` 可复核。
- `analyze` 的 `data.starside`：Aegis（S–F 总榜 + 枪管/弹匣/起源/3、4 号位 + 理由）与
  LGpig（分场景 T0… 榜 + 实测 dps/总伤/换弹 dps + 备注）**各自成字段、不合并**；机制说明走标记解析器
  （41 token 单一出处：`{num|…}`/`∞` 解包、`{unsure|…}` 保留、`{pvp|…}` 与 PvE 分开、查不到的名字
  原样保留并记进 `unresolved_names`）；帧级 DPS 表附适用条件；缺什么写进 `gaps`。
- 出处四件套（`source`/`snapshot_at`/`authors`/`unofficial`）随块一起出去；没评过给
  `available=false` + `reason`。
- 语料：`scripts/run_corpus_starside_entities.py` 8 行真机（账号 500 把武器 100% 命中，
  Aegis 189 / LGpig 72 把，326 把能 join 帧表，本季神器模组覆盖 60%）。


**性能（重要）：两条回归 + 一个 300 秒超时，都修了** —— P5/P6：

- **内核慢 7.7 倍**（纯枚举那条用例 12.4s → 95.0s）：那把统一后的排序键被**按组合**调用 286 万次，
  每次都从 pydantic 约束上重建常量向量（`max_vector()` 一个就被调 857 万次、占 36 秒）。
  新增 `RankingVectors`（纯元组）在 `solve()` 里只算一次往下传 → **95.0s → 35.8s**。
- **服务侧多出 60–120 秒**：内核一次交回整池 200 套，局部调谐逐套跑贪心，而用户只看 top_n 套。
  加**候补窗口**（前 `max(2 × top_n, 10)` 套）→ 端到端 **156.8→45.6s**、**130.3→14.5s**，
  top 六维**一字未变**。
- **特定形状上内核炸到 355 秒（300 秒预算直接超时）**：请求**没有 `priority_stats`** 时排序键
  区分度低，而乐观剪枝的上界把模组余量按"每项各加一遍"算（6 项 = 6 倍）→ 几乎每个组合都进了
  最贵的校验。修法：余量按排序键顺序**贪心分配**（该预算下最紧的合法上界）→ **355s → 66.3s**，
  top 方案一字未变。守门是一条**保守性属性测试**（乐观键必须是任何可达向量的上界）——
  它当场抓到了第一版分配的 bug（预算在"总和"里被扣了两次，上界算小 = 悄悄丢解）。
- 顺手：`could_insert` 的边界从 `>=` 改成 `>`（`insert` 本来就是严格大于，打平的候选
  永远进不了堆，`>=` 只让它们白跑最贵的校验；结果完全等价）；`MAX_STAT` 收进 `build/constants.py`。
- **还没解决**：严格解为空时那条"放宽目标复解 + 逐套精确复核"仍是最大单项成本（实测 140–333s，
  同一份代码三次差一倍以上，成本抖、结果不抖），列在计划文档待办第 1 条。

**修复：0 候选时 `reachable` 报出一排 0** —— P6：

- 那些数来自"逐套候选推上限"，**一套都没验证过时它们全是 0**，报出去读起来像"你哪项都到不了"，
  事实是"这次没量过"。规则按"缺值不编 0"落到形状工厂（`SearchDiagnostics.to_dict`：全 0 = 没量过），
  守门两档都钉（空表 / 全 0 表）。

**修复：调谐的允许清单比对没归 hash 符号（清单里有的被判成"装不到这件上"）** —— 2026-09-22：

- 组件 310 给的是**无符号** hash，而 `manifest.search` 给的是**有符号** —— 同一个插件两种写法。
  新加的写入判据当时写成裸 `in`，于是**清单里明明有的**调谐被判 `writable=false`，还附了一句
  1675 的理由。真机复现：光芒领主面具 `6917530188460608169` 的 `+超能 / -生命值`
  （310 = `4026414261` / 搜索 = `-268553035`）被判"装不到这件上"；改成两边过 `to_unsigned`
  后同一条请求 `writable=true`，并且**真的写进去了**（`ErrorCode=1`，装回也成功）。
- 收口成单一出处 `build.models.tuning_is_allowed`（清单与目标 hash 都归一），源头
  `tuning_options_from_reusable` 也统一返回无符号，写入兜底与进计划过滤都走它。
- 守门三条，各自注入验证过：helper 退化成成员判断 → 红；判据退回裸 `in` → 红；
  源头不再归一 → 红。（写第一条时夹具的假 Manifest 只认有符号，310 桩按无符号查不到类别 →
  清单被当成"读不到"、判据短路成"可写" —— 测试**假通过**，已修夹具：两种写法都认。）

**新增能力：代写调谐（`equip_build` / `equip_mod` 现在会写调谐）** —— 2026-09-22：

- 判据只有一条：**这颗在这件护甲允许的清单里**（组件 310 `ItemReusablePlugs`，每件不一样）。
  在 → 写；不在 → 明确不写并解释（上游回 **1675**，那句读作"这颗装不到这件上"，不是"你没材料"）。
- 调谐**并进 `canonical_build.items[].mods`**：执行器对 `mods` 是"按插件类别找插槽"，而调谐类别
  本来就在模组类别集合里 —— 走同一条写入/回读/失败处理，不新增字段、不开第二条路径。
- 真机验证：清单内的调谐写入 `ErrorCode=1`、回读插槽与六维一致、**不花材料**；四件护甲的调谐
  就是这么写成功的。`equip_mod` 对调谐的 `writable` 从"恒 false"改成按清单判。
- 话术：`tuning_changes` / `tuning_note` 从"要你进游戏手动改"改成"**确认后一起改**；改不了会说明
  是哪一件、为什么"。ADR-014 追加修订（原"暂不代写"作废），见 `docs/plans/TUNING_WRITE_PLAN.md`。
- **"写不进去不回退"原来不成立**：执行器把"上游拒绝某一颗"（记 `steps.mod_blocked`，不回退）
  与"硬失败"（回退）分两档，而 1675 **不在**"挡住"名单里 —— 一颗写不进去的调谐会让整条配装被
  回退。已把 1675 加进挡住名单，并补上那条一直没有测试盖住的分支（不回退、也不许报成功）。
- 判据**只剩 310 一条**：验收时量到 `unlock_state`（组件 305 / 角色级 plug set）对调谐不可信 ——
  调谐槽里正装着的那颗都不在清单里，而一颗被判 `false` 的调谐写入**照样成功**（真机：至高碎片槽 11
  `+武器 / -超能` → `ErrorCode=1`，回读 超能 25→20、近战 0→5，再换回也成功）。原判据会把能装的
  调谐报成"游戏里同样装不上"，已删除；`unlock_state` 对调谐改报 `null`（不把已知错误的值当判据）。
- 守门：清单外的调谐不许进计划、`writable` 只看清单、**305 说不行的调谐仍可写**、`confirmed=false`
  不写、执行端按类别能写调谐槽；注入验证：分别关掉"清单过滤"、"清单判据"、装回 305 闸门
  → 对应三条各自变红。

**修复：调谐"见底的项是白给"只该夹在比较层（口径定案）** —— P5：

- 用户在游戏里确认："属性到 0 之后，就算是负数也没有数值上的影响" —— 组件 304 报的 `生命 −5`
  是**记录值**，效果上夹在 0。所以把 −5 打在见底的项上**是白给**的。
- 正确的分法是把两件事分开：**存储层不夹**（`base`/`stats_with`/`Armor.stats` 跟 304 一致，
  否则反推会凭空长点数 —— 那正是上一条修的 bug）、**比较层夹到 0**
  （`delta_of` 与求解器看到的六维，见底的项再扣不掉点）。
- 守门：`test_dumping_a_stat_that_is_already_zero_is_free`（白给回来了）、
  "换调谐不许改基础值"（把换完的件再解析一次，base 必须一模一样 —— 比"总和相等"更准）、
  局部贪心幂等性。注入验证：把夹 0 塞回存储层 → 两条变红。
- 审计不受影响（468 件仍全过，它比的是 304 与我们的解析，两者都是记录值）。

**修复：求解器把"整件护甲的能量"都当成了可装属性模组的额度** —— P5：

- 症状（用户落地候选[1] 时撞上）：光芒领主手套容量 11、**手臂模组已占 9**，换 `武器模组`（3 点）
  要 `12/11` 装不下 —— 而求解器给每件都排了 +10 武器模组。
- 根因：`armor_to_process_item` 直接把 `energy_capacity` 当可用额度；DIM 那套"活动模组排列"
  （`activity_mod_permutations`）本该扣掉部位模组的能量，但我们的调用点永远传空列表，从没启用。
- 修法：解析层新增 `Armor.energy_used_by_other_mods`（已装的 `enhancements.v2_*` 里非 `v2_general`
  的部分），`armor_to_process_item` 用它算可用额度。一般插槽那颗不算（会被替换、能量还回来）。
- 验证：修正后求解器的候选与账号上真实装出来的那套**逐项一致**（武器135 生命23 职业38
  手雷140 超能125 近战28）；并且能正确指出"换用仓库那件手套副本可到武器140"。
- 守门：能量账两条（11−9=2、数据对不上时夹 0），注入验证过。

**新增：护甲口径审计脚本 + 修掉它挖出的两个模型 bug** —— P5：

- `scripts/audit_armor_model.py`（只读、退出码可当机器检查）：把游戏自己的组件（304 属性 /
  310 调谐清单 / 305 插槽）摊开，用**独立算术路径**重算期望值，逐件和我们模型对账六件事。
  为什么要它：前面三个口径 bug 都是"看输出"看不出来的，必须审输入。
- **全账号 468 件：六项全部对得上** ✓（另有 60 件非 T5、54 件无调谐槽，按设计不适用），
  并拿到 4 件"304 里有负数"的证据（`生命−5`/`近战−4`/`近战−5`）。
- 审计挖出并修掉：① **大师加成是"档位"不是"有/无"** —— 插件声明"六维各 +N"，实际只给
  **非词条那三项**各 +N；我们以前固定用 `gear_tier`(=5) 当加成，于是**只升到 1/3/4 档的一大批件
  永远反推不出词条**；② `is_masterworked = gear_tier == 5` 把 0 档的 T5 件当成满大师。
  两者现在都从**插件声明的档位**读（`masterwork_level`）。
- 守门：`tests/test_armor_masterwork.py` 三条真机金标准（3 档必须按 +3、5 档按 +5、
  0 档按 0，套错档位必须对不上），注入验证过。

**修复（重要）：每件护甲能装哪些调谐是逐件的 —— 求解器一直在开装不上的选项** —— P5：

- 用户指出"这个 +5/−5 在护甲上不是通用的，每件的调谐对象不一样"（他拿两件**同名**的光芒领主手套
  举例：一件只能加职业、另一件只能加近战）。真机核实：**组件 310 `ItemReusablePlugs`** 里，
  传说件各给 6 颗（5 颗"某一个属性 +5" + 平衡调整）、**金装 31 颗（任意属性）**；
  而 Manifest 的调谐 plug set 是**全局 32 颗**（三件同名手套指向同一个集合）。
- 我们的护甲快照以前**不请求 310**，于是 `piece_tuning` 拿全局目录当选项 —— 上一版方案里
  "五件统一换 `+武器/−近战`"，其中**三件根本装不上**。
- 修法：新增 `profile_components.BUILD_ARMOR`（= `ARMOR_SNAPSHOT` + 310），
  `get_armor_snapshot` 默认带它（定位就是"求解器的数据输入"），社区模板核对显式退出
  （代价实测 4.11 MB → 8.90 MB）；`Armor.tuning_option_hashes` 承载这份清单，
  `piece_tuning` 按它过滤选项，**读不到清单 = 不可动 + 说明**（缺数据 ≠ 允许，只留"撤掉"）。
- 守门：两件同名手套的**真机金标准**（A 只出 `+职业/−X`、B 只出 `+近战/−X`）+ "读不到 310 不可动"；
  两条都做了注入验证。ADR-014 里"只能换成你已经拥有的那一颗"一并纠正为"**只能换成这件允许的**"。
- 复算（武器优先、手雷其次、套装不变）：候选[0] 武器145 手雷111、候选[1] 武器140 手雷140 ——
  上一版的 160/155 里含"每件都能加 5 武器"这个不存在的假设。

**修复（重要）：调谐的"夹 0"让求解器凭空长属性 —— 用户看方案时发现的** —— P5：

- 症状：方案给一件臂铠记了 生命5/手雷5/超能5，而它的组件 304 明明是 生命−5/手雷0/超能0。
- 根因两步：① `PieceTuning.base_with()` 把 `base + delta` **夹在 0**（注释理由是"游戏里属性不会
  变成负数"）；② 反推基础值 `_invert_tuning()` 又做 `observed − delta`，把夹掉的那 5 点当成
  本来就有（`0 − (−5) = +5`）。贪心对同一件走几步就累加，真机实测**多出 15 点**
  （其中 5 点手雷、5 点超能落在用户关心的项上）。
- **"游戏里属性夹在 0"这条假设本身是错的**：组件 304 如实报负数，所以模型改成**等于游戏报的数**
  （不夹）。同时删掉 `plan_tuning` 里"任何一项都不许变成负数"的硬规则 —— 游戏允许把 −5 打在
  基础值为 0 的项上（304 就是证据），那条规则会把"牺牲一项没用的属性去补目标"整类走法判死。
- 新增 `PieceTuning.movable`（机器可读）与 `note` 同进同出：反推不出基础六维的件不许动、
  任何改法都返回观测值（以前那条兜底会把当前调谐再加一遍）；服务层两处 `tunable` 按它过滤。
- 真机复核：候选从 手雷115/超能105 修正为 **手雷110/超能100**（= 五件 304 之和）；
  另一条候选 武器155 手雷140 同样逐项等于 304 之和。
- 守门：真机数字当金标准的"只能按调谐 delta 变"、幂等性哨兵（第二趟不许再长点）、
  `movable`/`note` 一致性；四条编码旧口径的测试按新口径重写。**注入验证：恢复夹 0 → 4 条红**。
- 一处文档作废：`docs/plans/ARMOR_FORMAT_PLAN.md` 第 10 条"−5 打在 0 上是白给"（那是推理，不是实测）。

**修复（重要）：`平衡调整` 的模型一直是错的 —— 真机对账咬出来的** —— P5：

- Manifest 的 `investmentStats` 把 `平衡调整` 写成"六维各 +1"，`build/tuning.py` 就照这个
  建模；可 `build/armor_rules.py` 的模板模型（连文档串）写的是"**只给最低的三项各 +1**"。
  同一个 plug 两套模型打架，而求解器搜索用的是**错的那份**。
- 真机证据（`scripts/verify_tuning_write.py --apply --to 3122197216`）：光芒领主面具未调谐基值
  武器30/生命5/职业5/手雷30/超能25/近战5，装上平衡调整后是 武器30/生命6/职业6/手雷30/超能25/近战6。
  于是：以前六维每项多算 1（总账最多虚高 6 点），换成方向型调谐时基础值也会反推错。
- 修法：规则抽成 `armor_rules.balanced_tuning_bonus` 单一出处（"恰好三项并列最低"，否则一项不加，
  且**不适用时返回等长全 0，不返回空元组**）；目录里那份 `delta` 改成全 0 占位，真实增量由
  `PieceTuning.choice_delta` 按件现算；`is_noop` 只认空插槽；反推基值改用不动点校验。
- **破坏性提示**：候选的六维与顺序可能随之变化（这次是变准）。

**修复：局部调谐贪心跑满步数上限就停，等于没吃干净** —— P5：

- P4 那条"局部最优"不变量在模型修完当场变红：第 13 步本来还能再涨。查下去是 `max_steps=12`
  **跑满就停**（不是"没有改进了"停）。实测收敛点：上限 12 → key 405（残留 2 处可改进）、
  16 → 421、**20 → 427（残留 0）**，再往上不变。默认上限按实测调到 **24**。
- 新增 `TuningPlan.exhausted`：碰上限就是"没吃干净"，`tuning_note` 里会多一句
  "这轮局部优化是碰到步数上限停的……不代表已经最优"。守门：不变量断言 `exhausted is False`，
  外加一条"上限压到 1 必须翻 True"的可注入测试。

**修复：调谐"只能在游戏内改"这句彻底作废（真机一次往返问清）** —— P5：

- 真机结果：换成**你已经拥有**的调谐（身上别的护甲正装着的那颗）→ 免费接口 `ErrorCode=1`、
  回读插槽真变、换回也成功；换成**你没有**的一颗 → **1675**
  `DestinyCannotAffordMaterialRequirements`（换调谐要材料），**账号一个字节没变**。
- 三条边角事实：角色的可插入清单（组件 207）**根本不覆盖调谐槽**（正装着的那颗也不在里面，
  所以它不能当判据）；原样重插回 1679 `DestinySocketAlreadyHasPlug` **不是失败**；
  免费接口能寻址这个槽。
- 全仓话术跟着改（`_build_flow.py`、`build_results.py`、`armor_mod_service.py`、
  `loadout_mod_sockets.py`、README、`routing.md`、`ARMOR_FORMAT_PLAN.md`、`TESTING_CORPUS.md`）：
  结论仍是"不代你改调谐"，但**理由**从"上游不允许"换成"本项目还没开这条路 + 换调谐要材料"。
- 新增真机脚本 `scripts/verify_tuning_write.py`（`--apply` 才写、`--to` 指定目标、
  `--include-empty` 试撤掉），口径见 ADR-014。

**文档：配装求解的目标与排序口径写成 ADR-015** —— P5：

- 上限是**软**的（排名 + `max_violations` 标注，不剪枝）、排序只有 `goodness_key` 一把尺子、
  调谐吃干净但只是**局部**最优、`reachable` 是**保守下界**、没算完不许说"不可行"。
- 这几条散在 P0–P4 的记录与代码注释里，现在有一处能指的地方。

**变化：有解时也把"免费额度"吃干净（调谐进搜索的便宜那半）** —— P4：

- 以前"刚好达标"就交卷：达标之后剩下的调谐额度（护甲上那 5 个 +5/−5 的小格子）**没人去动**，
  于是答案常年是"够用但不够极限"。现在候选池出来后过一趟**单件调谐邻域贪心**
  （`build/tuning.local_tuning_improvement`）：每件试它的调谐选项、每一步都用
  `validate_fixed_process_items` 按真实目标**重新排序属性模组再比六维** —— 与补救路径同一判据，
  不拍近似值。选项先按"可能变好"过滤（提升优先项 / 压下超上限那项 / 抬高封顶总和）。
- **真机验收**（术士 + 星火协议 + 埃希恩记忆 4 件，手雷下限 100、超能上限 100）：
  手雷 **120 → 145**、超能 110 → **95**（顺便不超上限了），端到端 9.2s → 17.6s。
  验收线本来是"手雷 ≥ 130"；145 正好等于此前手算的"免费额度上限 +25"——这 25 点在调谐槽里
  躺了一整轮。**`reachable` 仍是保守下界**（计划决定 2/3），因为"要顶到 X"只要把下限写成 X 就有答案。
- 两条克制的边界：① 对外只报**净改动**（贪心会对同一件走好几步，第一版真机报了 12 条、实际只涉及 5 件）；
  ② **用户什么都没要求时不动他的调谐** —— 调谐免费、但"改 5 件"要他自己点，为 +6 封顶总和换 5 次点击不值。
  这两条都是真机跑出来才发现的，各配了一条守门。
- **没解决**：严格解为空时仍走"放宽目标复解 + 逐套精确复核"，那个猎人样板端到端仍是 **292s**
  （其中枚举只有 0.76s）。它是现在**最大的单项成本**，单列进计划文档的待办。

**重构（破坏性）：候选排序口径统一成一套逐字段字典序** —— P3：

- 以前有**三套**排序口径打架：求解器堆里的 `_ranking_metric`（优先级字典序）、
  `scorer.score` 的加权总分（**超出目标每点倒扣 0.5**、六维总和只加 0.1）、
  `find_build` 末尾 `completion_rate` 打头的排序。净效果是"多堆 10 点手雷在评分里是负收益"，
  求解器只交及格卷 —— 这正是"不够极限"的机械原因之一。
- 现在唯一口径是 `build/ranking.goodness_key`：**布尔违规位 → 每个优先项各一层（先达没达、
  再差多少）→ 普通层（先未达标个数、再总缺口）→ 优先级值 → 封顶总和**。
  求解器堆、剪枝用的乐观键、候选保留、展示排序四处共用它。
- `score` 的含义改成**封顶后的六维总和**（超上限部分不计），**不再是排序键**；
  `completion_rate` 降级为展示字段。**破坏性**：候选顺序与 `score` 数值都会变，已登记兼容面。
- 顺手把 `ProcessArmorSet` 的三个排序字段（`enabled_stats_total`/`stat_mix`/`stats_total`）
  收成一个 `rank_key`；P2 为"上限沉底"加的 `caps_ok` 特判也删了（上限已并进规则维度）。
- 守门：`tests/test_build_ranking.py`（12 条，其中 4 条注入验证过会红），含 d2-armor-solver
  自查里的现成反例（武器高优先时 `[武器100/生命80]` 必须赢 `[武器90/生命100]`）。

**新增能力：属性上限（`stat_caps`）与"每项能到多少"（`data.reachable`）** —— P2：

- `build_assistant` 的求解类 intent 多了 `stat_caps`：表达"手雷别超过 100"这种**上限**。
  语义定死为**软约束**：超了照样出解，但在结果的 `max_violations` 里逐项标注
  （`{stat,label,actual,max}`）、排到没超上限的方案后面，同时求解器不再往那一项堆模组。
  不传 = 不限；不认识的键 / 超 0-200 / 上限低于下限一律报错，**不静默忽略**。
- 有解时也报"每项已验证能到多少"（`data.reachable` + `reachable_note`）。真机实测：
  赫沃斯托夫那套模板（手雷 100~200、超能 80~100 …）原样传进去，能报出「超能 105/110 超过上限 100」，
  而把手雷下限抬到 130 时它给出**超能正好 100、不超上限**的那套。
- 诚实口径：`reachable` 是**保守下界**（模组按 5/10 加减，这一步只验证"要求到这么多"时还装得下，
  实际可能更高 —— 手雷报 120 而 130 确实能到），且**逐项可达 ≠ 同时达到**；两句都写进了
  `reachable_note`，守门钉住。
- 顺手清掉一段**死计算**：`solver.stat_ranges` 一直在算、形状是 `[[MAX_STAT, 0]]*6`
  （`[0]` 那个"下限"永远是假值）却从未被读过。现在只留上限并真的返回。
- 守门：`tests/test_build_stat_caps.py`（13 条，其中 5 条注入验证过会红）。

**修复（重要）：0 候选不再是一句空口"没解"** —— P1 阶段分离，见 `docs/plans/SOLVER_OPTIMALITY_PLAN.md`：

- 空结果现在**自证枚举完了**：`data.search = {exhaustive, combos, truncated_by}`，摘要写
  「枚举完了，没有任何一套能满足这些下限（枚举了 N 套组合）」；真机上 6,283,200 套的用例已验。
- **"没搜完"与"不可行"在类型上分开**：新增 `build/process_types.SearchCoverage`，
  `ProcessResult.complete` 默认 True（五层枚举无配额），将来加预算的人必须显式置 False。
  `ladder.verdict.satisfiable` 因此多了 `null` —— 没搜完时不许断言不可行
  （对齐 d2-armor-solver 的 "no limit can create an infeasibility proof"）。
- 超时话术从英文改成中文，并明说「**没算完**，不代表配不出来」。
- 顺带收掉一处"一个事实写三处"：`STAT_NAMES → 请求字段名` 的映射（`class_stat` 对应 `class_target`）
  以前在 `tools/_armor_ladder.py` 抄了一份、`build_service` 的日志又按 `STAT_NAMES` 拼字符串，
  于是 `class_stat_target` 直接 `AttributeError`（真机路径上会炸）。现在单一出处是
  `build/constants.REQUEST_TARGET_FIELDS`，`BuildRequest.target_vector()` 也走它。
- 守门：`tests/test_build_budget_honesty.py`（7 条，其中 4 条注入验证过会红），
  含"堆截断不许把唯一满足下限的那套挤掉"（3125 组合 / 堆容量 200 的构造用例）。

**新增工具：护甲求解器基准** `scripts/benchmark_build_solver.py`（P0，见 `docs/plans/SOLVER_OPTIMALITY_PLAN.md`）：

- 固定用例 × 3 次取中位，每条用例同时量**端到端**（`BuildService.find_build`，含 worker 进程开销）
  与**内核**（直接 `solve()`，给 `combos` 这个确定性工作量）；`--source-root` 指向另一个 checkout 做
  paired 对照（实测 `sys.path` 前置能压过 editable 安装的 finder），`--compare a.json b.json` 出对照表。
- 默认用例集按**实测成本**拆分：`hunter_priority_only`(18s)、`titan_meets_minimum`(7.8s) 默认跑；
  `hunter_priority_set`(283s)、`hunter_infeasible`(28s)、术士两条进 `--heavy`。
- 落盘 `docs/benchmarks/build-solver-p0-*.json`。
- 顺带给 `scripts/capture_weapon_baseline.py` 补 `--only`（**补录新用例必须用它**：整面重录会把已有的
  "改动前"对照覆盖成今天的值），并加了三条钉目标语义的护甲面用例 `build_priority_targets` /
  `build_priority_only` / `build_range_targets`。

**实测（P0 先量，全部写进计划文档）**：同一个人把目标从 手雷≥100 抬到 ≥130，
求解器**能**给出 手雷130（其余下限仍达标），但端到端从 9.2s 涨到 140s —— 内核工作量一模一样
（3,769,920 组合、5.7s），差的是"严格解为空 → 调谐补救"那条路（同一请求分段实测：内核 0.27s /
`solve_with_tuning` 212.66s）。所以"属性不够极限"的真因是**没人要求过** + **要价太贵**，不是算法够不着。

**修复（重要）：护甲上"剩下的模组槽"本来就是能通过 API 写的，"装不了"是第三件事** ——
部位模组（头盔/手臂/胸甲/腿甲/职业物品）在 API 面前和属性模组一样，被拒的是**这一位还没解锁**的
那几颗（见 ADR-013）：

- 真机验证：6 类插槽各做一次"同类别、能量不增的一换一 → 回读 → 换回"，**6/6 全部成功**
  （`scripts/verify_armor_mod_sockets.py --apply`）。所以这不是字段名/有符号 hash 那类格式 bug。
- 真因：能不能插由**这一位角色**决定，清单在组件 207 `characterPlugSets`（随 305 一起回来），
  不是 Manifest 的 `reusablePlugItems`。实测头盔 plug set Manifest 有 61 颗、这一位只有 21 颗；
  手臂 25/56；一般模组 9/24。被拒的那几颗发的是 **1676 `DestinyFailedPlugInsertionRules`**，
  它们 Manifest 的 `insertionRules` 里都写着「必须在赛季神器中选择」。
- 以前的行为：把这颗当能装 → 用户确认完才失败；同名有"已解锁/未解锁"两档时按属性加成挑，
  可能正好挑到没解锁那档；`equip_loadout` 还把 1676 说成"Bungie 不允许 API 改护甲模组，
  请游戏内手动装"——**游戏里同样装不上**。
- 现在：`plan` 先按可插入清单挑（同名优先已解锁那档），挑不到就 `writable=false` 并带上 Manifest
  的插入条件；配装预检在**写之前**报同样的话；`apply` 与配装的写入失败按 1676/403/1663
  **三种原因分开说**，1676 说的是"插入条件没满足"。上游没给这份清单时 `unlock_state=null`
  且照旧可写（**没数据 ≠ 不允许**）。
- 顺带修一个显示 bug：`ArmorModService._slot_key` 用**有符号** bucket hash 查表，而 Manifest 存的是
  无符号值 → 头盔与臂铠的槽名一直是空串，确认请求印成「光芒领主面具（，540，T5）」（真机日志可见）。
  改走 `armor_payload._ARMOR_SLOT_HASHES` 那张表，不再手抄第二份。
- 调谐的理由也换了：不再是"上游只允许游戏内改"（那句出自 ADR-012 推翻的 1663），
  而是"本项目没验证过"。实测免费接口能寻址调谐槽（原样重插回 **1679 `DestinySocketAlreadyHasPlug`**）。
- 又一处有符号 hash 坑：`manifest.search` 给**有符号** hash、profile 给**无符号**，第一版直接比
  → 凡是负数 hash 的模组（`复原`、`特殊终结技`……）全被判成「没解锁」。现在
  `insertable_plugs` 统一收成无符号，`plug_is_insertable` 与 `_find_mod_socket` 两边都转
  （顺带修掉"这个槽已经装着它了"认不出来、会错装到别的同类槽）。
- 目标已经是槽里现值时（上游 1679 `DestinySocketAlreadyHasPlug`）当**无操作成功**报
  （`already_installed=true`），不再抛成失败。
- 守门：`tests/test_armor_mod_unlock.py`（24 条，其中 7 条注入验证过会变红）。

**修复（重要）：护甲模组与子职业插槽本来就该能通过 API 写** —— 之前"个人应用没有
`AdvancedWriteActions`、只能游戏内手动装"的结论是**归因错误**（见 ADR-012，推翻 ADR-002）。
真因是两个线上格式错误，都在免费插槽接口那条路上：

- **字段名**：免费接口 `InsertSocketPlugFree` 的物品字段叫 **`itemId`**，付费接口才叫
  `itemInstanceId`；`aiobungie` 把付费那套抄进了免费方法，于是 `itemId` 缺失、上游取不到物品，
  固定回 1663 `DestinyItemActionForbidden` + `{'request.itemInstanceId': "The item being
  socketed doesn't have sockets."}`。那句"物品没有插槽"其实是**字段没绑上**。现在自己拼 body。
- **有符号 hash**：护甲模组方案里的 plug hash 是**有符号**的（实测 `武器模组` = `-111671246`），
  直接发负数上游回 `InvalidPostBody`；两个接口现在都过 `to_unsigned()`。
- 官方原文：该接口**不需要** `AdvancedWriteActions`，对第三方开放，只要 `MoveEquipDestinyItems`。
- 真机验证：术士 5 颗属性模组全部通过 API 写入成功，装完六维与求解器预测**逐项零差值**
  （武器 107 / 生命 7 / 职业 71 / 手雷 120 / 近战 71 / 超能 110）；子职业手雷与碎片也写通了。
- 顺带纠正：`socketArrayType` 只有 `Default=0` / `Intrinsic=1`，此前注释写的"1 = reusable"是错的。
- 守门：`tests/test_bungie_client_actions.py` 两条新测试钉住字段名与无符号转换。
- **没变的**：付费插槽接口（调谐/强化类）仍然做不到——它要 AWA 三段流程，本项目没实现；
  现在如实说"我们没实现"，不再说成"应用没有权限"。

**新增能力：周常轮换表** `world_assistant(intent="rotations")`（别名 `轮换`/`周常轮换`/`这周`）：

- **官方那半**：本周特色突袭/地牢来自 `/Destiny2/Milestones/`（带官方起止时间）；本周夜幕/宗师的
  **打击 + 词缀 + 掉落**来自 profile 组件 204 的 `characterActivities.availableActivities[]`
  （实测三个角色一致；词缀与奖励就在这条记录上）—— 这两类标 `source="official"`。
- **自维护表那半**（`data/rotations.py`）：上维挑战（6 周循环）、异域任务轮换（7 周循环）、
  泉源（每天 攻击/防御 交替）—— 标 `source="schedule"` + `verified_at`/`verified_against`，
  锚点来自游戏内轮换页截图（逐条对上：本周 辛梅里安卫戍营 / 凯尔之陨 / 泉源防御）。
- **遗失区域**：官方一个接口都不给（里程碑没有、组件 204 的 294 条可用活动里没有、
  Manifest 那个 42 条「遗失区域」清单是"打过哪些"而不是"今天轮到哪个"），顺序表也还没核对过
  → 只给 31 个候选地点 + 核对办法，**不给"今天是谁"**。
- 空词缀 hash 跳过、奖励数量 0 原样给、上游没给打击名就留空并说明、`{var:...}` 不插值。
- 口径与实测见 ADR-010 与 `docs/plans/ROTATION_PLAN.md`；守门 `tests/test_rotations.py`（16 条）。


**新增能力：锻造武器模式查询** `weapon_assistant(intent="patterns")`
（别名 `pattern`/`craft`/`锻造`/`锻造武器`/`图样`/`图样进度`/`模式进度`/`红框`/`红框进度`）：
游戏里「收藏品 → 模式和催化」那一页的 **183 条武器模式**，给每条的进度（游戏里那条「模式进度 4/5」）、
还差几个红框萃取、需求萃取次数、武器类型/槽位/稀有度，以及 Starside「锻造武器来源」的掉落来源。
只读、不改账号。

- **用玩家的词答话**：玩家说「红框」（深视共振武器），游戏官方中文说「模式」、英文是 `pattern`、
  第三方工具常译作「图样」—— 响应里带 `terms` 对照表，摘要用「红框 / 未解锁」这类玩家口径。
- 进度取自 profile **组件 900**（`profileRecords`）：组件 800 里图样解锁状态一条都没有、
  组件 1300 只回答"能塑形哪些 perk"（实测见 `docs/reference/bungie_api.md` 第十四节）。
  首次调用约 3 s（1.44 MB），5 分钟 TTL 内复用提取后的状态，之后约 0.4 s。
- 图样总数按 **183 条**算（记录名 == 可锻造武器名，精确相等；实测 183/183 命中），
  **不是** `is_craftable` 的 219 件。多出的 36 件 `（专家）/（失时）/（痛苦）`变体不单列图样，
  而且**塑形配置不同**：只有框架/枪管/弹夹可塑形（三四号特性固定）、自己不带深视插槽
  （红框只掉基础版）—— 这些数字都从 Manifest 现算，单独问变体名会连它的塑形配置一起给出。
- `未开始` = 账号里没有这条模式记录（`progress` 与 `remaining` 都是 `null`），**不是 0/5**；
  `counts` 里额外给 `not_unlocked`（= 进行中 + 还没开始，玩家口径的"没解锁"）。
- 来源是本地社区资料（Starside 页面 2026.8.30，183 条里命中 172 条），带 `trust`/`updated_at`；
  "这份清单没有这一行"不会说成"这把武器没有来源"。
- **修复（2026-09-20，用户二次指出）**：默认一页 20 条，调用方只读第一页时把「金枪图样」报成 2 把
  （实际 **16 把**）。现在 `patterns` 支持 `rarity`（异域/金枪、传说、稀有；词表收拢到
  `vocabulary.RARITY_ALIASES`，`inventory_assistant` 那份私有表删掉），并在 `counts` 旁给 `by_tier`
  汇总；要"全部金枪图样"一次调用即可拿全。
- **修复（2026-09-20，用户截图驱动）**：模式记录分两个作用域 —— 183 条里 151 条档案级
  （`profileRecords`）、**32 条角色级**（`characterRecords`）。第一版只读了档案级，把那 32 把
  已解锁的武器报成「未开始」（149/183）；现在两个作用域都读、同一记录号取进度最靠前的角色，
  本账号 **181/183**。「未开始」只有在两处都没有记录时才给。
- 新增守门：`tests/test_pattern_query.py`、`tests/test_crafting_sources.py`；语料新增
  `patterns` 行（计数自洽、"未开始 ≠ 0"、变体塑形配置、术语对照、来源出处）与别名等价行。

## 0.5.0 — 2026-09-20

**这是一次破坏性发布**：工具面固定为 8 个聚合工具（历史工具面剥离到 `legacy/`）、
`weapon_assistant(intent="type")` 改列表行（默认 10 件 + 翻页）、`activity_assistant(intent="stats")`
改账号级三档、活动模式词表合一。升级前请对照 `docs/COMPATIBILITY.md`。

**历史工具面整块剥离（见 ADR-008）** —— 67 个低层工具（`get_inventory`、`search_weapons_by_type` …）
与**配装导入整块**（`build_import/` 领域包 + 服务 + 工具 + 两个提示词 + 它的测试）移到仓库根目录 `legacy/`：
不进包、不参与测试与 lint，只作查阅（`legacy/README.md` 写了为什么不维护、怎么复活）。

- `full`/`expert` profile 与 `DESTINY_MCP_ENABLE_LEGACY_TOOLS` 开关一起删除：`create_server()` 不再收参数，
  `/health` 不再报 `tool_profile`；语料 `mcp` 组加了反向断言（把旧的 profile/开关塞进去也只该有 8 个工具）。
- **配装导入功能不再提供**（决定不要，不做"搬成新 intent"的迁移）：README 功能列表与技能文档里的
  相关宣传同步删掉；58 处引用（测试、语料、安装脚本、技能、DSH 配置）已清理。
- 为什么这么做：那 67 个工具的既定口径是"默认屏蔽、不保证契约、不单独修 bug"，复核时已经有两个
  烂在里面（`analyze_weapon` 裸抛 `KeyError`、`get_historical_stats` 有数据说成"未找到"）；
  而 62/67 在 8 个聚合工具里都有对应，留着它们只是每次改公共层都要替一堆没守卫的工具做决定。

**性能六项（按 `docs/plans/PERFORMANCE_PLAN.md` 的顺序落地）**：

- **武器目录改成"两趟走"：先按名字判定、只为返回的那一页构明细**。
  以前对全库 2208 把枪都构一遍带描述+图标的 perk 明细，真机 **120–135 秒**、内部 12 万次查询，
  而返回只有几十条。现在轻量匹配（只取名字）+ 命中页才展开：
  `catalog 手炮+爆破专家` **120s+ → 1.7 秒**，命中数仍是 46 条（内容不变，武器语料 17/17 复核）。
  没有做"命中够一页就早退"——那会把 `total` 从"全库命中多少"变成"至少这么多"，是内容变化。
- **抽代码（体积上限只降不升）**：搜人分支 → `tools/_player_branches.py`（`assistants.py` 1407 → 1364，
  上限 1399 → 1364）；PGCR 数值解析与时间窗 → `services/pgcr_values.py`（`pvp_weapon_service.py`
  468 → 434，上限 461 → 434）。
- **三个角色的历史并发拉**（`history` 与 `pvp_weapons` 的历史那一步）：以前串行等 3 次上游往返。
  单角色失败仍只丢那个角色（`return_exceptions` 保住原降级语义），拼装顺序不变。
  诚实备注：真机复测时单次历史只要 0.3s（上游当时很快），所以这次只省了不到 1 秒；
  上游慢的时候（1.2s/次）才省得多 —— 收益随上游波动，不是固定值。
- **`stats` 的两个来源并行**：统计接口与游戏内计数器以前一前一后各打一次，现在同时去拿
  （守门用"各睡 0.2s"钉住真的并发）。真机复测 3.4–6.5s —— 上游本身波动就有 1.1–4.6s/次，
  所以这条省的是"较小的那一跳"，别指望它把 5 秒变 3 秒。
- **武器类型列表改"列表行"，默认 10 件、可翻页（破坏性）**：`我手炮都有哪些` 以前每件发完整模板
  （`{weapon, sockets, options, stats, perks_complete, notes}`），真机 **387,598 字符**里 2/3 是
  插槽池与可换项。现在一行一件的列表行（身份摊平 + 位置/光等 + `stats` + `notes`），
  **387,598 → 41,537 字符（9.3×）**；顺带列表视图**不再请求 305/310**（profile 10.16 MB → 1.86 MB，
  实测 3.79s → 0.63s），也不再逐把读本地资料（整张列表的清单仍在顶层 `farming_list`）。
  默认 20 → **10 件**，新增 `offset`/`next_offset` 翻页（最后一页 `truncated=false`）。
  要看某一件的插槽与可换部件：`weapon_assistant(intent="compare", weapon_name=…, item_instance_id=…)`。
  明细见 `docs/COMPATIBILITY.md`；基线已同步重录（新增 `type_list_default` 用例）。
- **顺带的两处缓存（计划里的"顺带一项"）**：① 目录查询 `list_weapon_catalog` 的结果按 (类型, 名字)
  记忆化 —— 这一步要遍历全量索引（真机 **1.06s**），而"我手炮都有哪些""哪些手炮能出某个 perk"
  会反复问同一个类型；`limit` 不进缓存键，重载 Manifest 时与其它内存索引一起清空。
  ② `include_profile=true` 的候选档案加 5 分钟 TTL（同一前缀改字再搜不必把同一批人重拉，
  真机单人 1.1–4.6s）；只缓存成功结果，失败照旧降级、下次重试。
  计划里写的"目录筛选结果指纹缓存"没有单独做：真正贵的是那一次索引遍历，缓存在它那一层
  同时利好 `catalog` 与 `type`，也避开了"账号侧结果过期"的风险（`filter_rolls` 不缓存）。
- **修复：刷取清单回查时把属性名/perk 名当成了武器名**。列表行里嵌着 `stats[].name`（"伤害""射程"）
  与 `matched_perk_details[].name`，通用的名字收集会把它们一起送进 `farming_list` 回查，
  于是 `unmatched` 里出现"萤火虫""稳定性"这种条目 —— 看着像"这些武器不在清单里"，其实问错了对象
  （真机实测 `catalog` 与 `type` 都有）。现在列表行只送行自己的 `name`（`tools/_farming.item_names`），
  实测 `catalog` 的 `unmatched` 从 `["玉兔","萤火虫","黑桃A"]` 变成 `["玉兔","黑桃A"]`。
- **修复：目录命中的 perk 明细不再把强化版发两遍**。池子里 `萤火虫` 与 `萤火虫↑` 是两条 plug，
  但"能滚出萤火虫"只有一件事，强化版 hash 已经在基础版的 `enhanced_plug_hash` 里；
  两条都发会把 `catalog_perk` 从 55.9k 撑到 77.1k 字符（真机实测），也让第 2 项的"内容零变化"
  不成立。按规范名去重后，`catalog_perk` 与改动前**逐字节一致**（纯提速）。
- **模糊搜人不再逐个拉别人档案**：默认只给候选的名字/ID/平台（真机 **22.6 秒 → 1.1 秒**，
  档案读取 0 次）；要按"游玩时长/最近游玩/凯旋分"排序时传 `include_profile=true`，
  这时**一次扇出**并发拉（实测并发 10 → 2.8–4.2s，而并发 5 出现 4.3–14.5s 的抖动：
  分批等于把"某个人档案慢"的风险翻倍）。并发值写死在 `_ENRICH_CONCURRENCY` 并附实测。

**PVP 战绩 P1/P2/P3b/P3-2：生涯数字分三档、两个来源并列、按模式与周期说话**（口径决定见
`docs/adr/005-career-numbers-follow-in-game-counters.md`，实测证据见
`docs/reference/bungie_api.md` 第十一/十二节，真机验收 `scripts/verify_career_stats.py` 8/8）：

- **`stats`/`career` 默认改成账号级三档**（破坏性）：每一行给 `existing`（现存角色）/
  `deleted`（已删角色明细）/ `account_total`（账号级合计，**已含已删角色**）—— 真机
  50,622 / 28,242 / **78,864**。账号级行**没有** `value`，单角色只在显式传 `character=` 时给
  并标 `scope="character"` + 角色名。此前默认给的是"第一个角色"的 17,703，既不是生涯也没标注。
- **纠错**：`mergedAllCharacters` 已含已删角色，`account_total` = `existing + deleted`；
  早期文档里的 107,106 是把 `mergedDeletedCharacters` 又加了一遍，已在本 CHANGELOG 与
  `docs/reference/bungie_api.md` 一并改正。
- **合并语义逐项声明**：`aggregate ∈ {sum, max, min, derived, none}`（本次真机核对抓出
  `remainingTimeAfterQuitSeconds` 是**可加**的，不是"取最小"）；比值类按公式重算，
  比不出的给 `null`。`killsDeathsAssists` 的中文名从"击杀+助攻"改成 **KDA**（它是
  `(击杀+助攻/2)/死亡` 的指数，不是那个和）。
- **两个来源并列（P2）**：`stats` 的 `data.game_counters` 给游戏内计数器
  （`811894228` = **124,495**，`source=profile.metrics`），warnings 写出与统计接口账号级
  （78,864）的差 **45,631** 及"拆不出来"的原因；计数器读不到只降级（`counters_unavailable`
  + warning），不影响统计结果。
- **`weapon_history` 标 `scope="all_modes"`（P3b）**：话术直说"这是全模式 PvE+PvP 武器击杀，
  不是 PvP 榜"。
- **`stats` 支持 `mode=`/`period=`（P3-2）**：模式数值取自 Manifest 的 `modeType`
  （`modes=` **只在按角色端点上生效**，账号级 + 模式 = 逐角色取 + 自己合，标
  `aggregation="computed"`；交叉验证：`mode=crucible` 自行合并的 60 项与上游账号级 `allPvP`
  逐项一致）；`period=season/act` 上游没有 → 如实报 `unavailable` 并指向 `counters`，
  不降级成生涯、不试会 500 的 `periodType=3`。
- **抽代码**：上游 HTTP 错误映射整段搬到 `bungie_errors.py`，活动统计端点搬到
  `bungie_stats.py`（`bungie_client` 上限 1263 → 1200 以下，只降不升）。

**玩家名改回游戏内 ID，并补上活动服务漏掉的导入**（社区反馈："显示的是我的 Steam 名"）：

- **口径**：`displayName` 是**平台名**（同一账号四个平台各不相同：`OneTop丶Husky` /
  `SecHusky` / `early_moccasin0` / `此人以嫖到广东`），游戏内 ID 是
  `bungieGlobalDisplayName` + `#code`（`OneTop丶Husky#6641`，四个平台一致）。旧代码写成
  `displayName or bungieGlobalDisplayName`，顺序反了。
- **单一出处**：新增 `utils/player_names.py`（`bungie_display_name` 走游戏内 ID、
  `bungie_display_name_of_player` 拆 PGCR 的 `destinyUserInfo`），profile / history / PGCR /
  排行榜四条路径统一走它；`tests/test_player_display_name.py` 除行为用例外还**扫描全仓**，
  禁止再出现 `get("displayName"` 拼玩家名。
- **修复**：`activity_service` 漏了 `player_names` 的导入 —— 名字改了，PGCR 与排行榜两条路径
  会直接 `NameError`（真机 PGCR 复现）。真机复核：PGCR 六个参与者全部是 `名字#code` 形式；
  排行榜接口对该账号上游返回空，按上游故障如实报（不编排名）。

**活动模式词表合一（破坏性）：三处手写表并成一处，模式名改从 Manifest 取** ——
同一个"模式"概念以前在 `activity_service.ACTIVITY_MODES`、`activity_service.MODE_NAMES`、
`pvp_counters.MODE_ACTIVITY_TYPES` 三处各写一遍，其中两张实测是错的：

- `allpvp = 9`（伞形其实是 **5**；`modes=9` 直接 HTTP 500）；`猛攻/onslaught = 69`
  （69 是**多人竞技PvP**，问"猛攻"会拿到一堆竞技场次）；`grandmaster = 46`（46 是计分日落，
  47 才是计分巅峰日落）；`MODE_NAMES` 只有 8 条，真机跑出来的子模式 43/44/73/89/91
  全不在里面，输出"模式43"。
- 现在唯一出处是 `data/activity_modes.py`（词 → `modeType` + `activityModeCategory`），
  **中文名不落地**：运行时从 zh Manifest 的 `DestinyActivityModeDefinition` 取
  （新方法 `ManifestManager.get_activity_mode_name`，懒加载索引，查不到退回 `模式<号>`）。
- 顺带的分工修正：**智谋（63）的 category=3（PvPvE），不是纯 PvP** —— 真机实测
  `mode=5` 拉的 250 场里没有一场智谋；"猛攻"直接删掉别名（Manifest 里没有这个模式，
  86 只叫 `Offensive`/攻势，确认不了就不猜）。
- 行为变化：真机 `history(mode="pvp")` 现在返回 PvP 场次且标"铁旗占领模式"；
  `stats` 认的模式词从 6 个扩到全部；`counters` 的 `labels.modes` 给官方中文名。
  影响面见 `docs/COMPATIBILITY.md`。

**纯 PvP 武器榜（新 intent `pvp_weapons`）：逐场结算聚合"最近 N 场"**（口径与实测见
`docs/plans/PVP_WEAPON_BOARD_PLAN.md`，决定与被否掉的方案见 ADR-006）：

- **上游给不了生涯口径的 PvP 武器榜**：`GetUniqueWeaponHistory` 没有模式参数、
  统计接口的武器聚合没有模式维度、计数器没有按武器拆 —— 三条路都实测排除，
  所以答案是"最近 N 场 PGCR 聚合"，`scope="pvp_recent"`、`source="pgcr_aggregation"`。
- `activity_assistant(intent="pvp_weapons")`：`mode` 取 `pvp`（默认，熔炉伞形 5）/
  `trials` / `iron_banner` / `competitive` / `gambit`；`count` 是**分析多少场**
  （默认 10、上限 100）；`character` 可只算一个角色。
- **窗口与角色必须自证**：`window{newest,oldest,matches_requested,matches_analyzed,matches_failed}`、
  `mode_tally`（每场回报的子模式 + 官方中文名）、`characters`。真机上"最近 250 场"对某些角色
  跨两年多，只说"最近 N 场"会被读成"最近几周"。子模式名来自 Manifest（伞形过滤、回报具体模式）。
- **只统计自己那一行**：PGCR 的 `extended.weapons` 按玩家分行，必须按 `characterId`
  精确匹配（取 `entries[0]` 会把别人的枪算到你头上 —— 真机探针踩过，守门测试钉住）。
- **失败如实**：单场失败进 `failed_matches` + warning 并继续；一场都没取到**报错**，
  不返回空榜单；历史失败只丢那个角色。
- **PGCR 落盘缓存**（`~/.destiny_mcp/cache/pgcr/<instanceId>.json`，结算不可变）：
  真机 10 场冷启 35.2 秒 → 命中缓存 14.2 秒（含约 10–12 秒进程启动 + Manifest 加载）。
  缓存不算账号写入，不需要 `confirmed`。
- 顺带：`assistants.py` 把榜单三兄弟与武器两个口径分别搬进 `_leaderboard_branches.py`、
  `_weapon_usage_branches.py`，体量上限 1403 → 1401（只降不升）。

**PvP 武器榜审查修正（同日补，都是自查与独立审查抓出来的）**：

- **PvE 模式词必须报错**：`pvp_weapons` 原来认全部 14 个模式词，`mode="raid"` 会被接受、
  回包却自称 `scope="pvp_recent"`（真机：392 杀的突袭枪登顶"PvP 武器榜"）。现在只认
  PvP 家族（category=2）+ 智谋，`mode_group.available_modes` 与报错话术都从同一个常量派生。
- **"找不到你那一行"不再静默**：`_own_weapons` 的 membership 回退以前只写在 docstring 里、
  实现没有 —— 上游不给 `characterId` 时整场击杀消失而响应仍 `ok=true`。现在补齐回退，
  两条路都不中则记 `window.matches_without_your_row` + warning。
- **`count` 不再被静默改写**：`window.matches_requested` 保留调用方要的值（原样），
  实际用多少场另给 `matches_planned`，两者不同带 warning（以前问 500 场会看到"你要了 100 场"）。
- **榜单分支搬运不许改请求**：搬 `leaderboards`/`clan_leaderboards` 时 `mode` 从 `None`
  变成 `""`，客户端只过滤 `None`，于是 URL 多了一个空的 `modes=`（httpx 实测会发出去）。
  已还原成 `None`，并加守门测试钉住"没给 mode 就不许出现这个参数"。
- **Manifest 模式名两处加固**：坏行（合法 JSON 但不是 dict / 没设 `row_factory`）以前会
  抛 `AttributeError`/`KeyError` 把整条 history 带崩，现在只跳过该行；索引为空时由 `debug`
  升到 `logger.warning`，榜单词条也带 warning（新增真 sqlite 测试 7 条，此前全仓没有一条
  测试真的调用过 `get_activity_mode_name`）。
- **PGCR 缓存有了保留策略**：`MAX_CACHE_FILES = 2000`（≈100 MB）按 mtime 轮换，
  目录改由独立的 `DESTINY_CACHE_PATH` 指定（默认 `~/.destiny_mcp/cache`）。
- 补守门：工具层默认值（不传 `count` → 10 场，这条路径此前从没被走过）、信封形状、
  职业名标注、`character=` 过滤、榜单分支等价性、模式名真 sqlite 行为。

**PvP 武器榜载荷收口（同日补，趁未发版改干净）**：

- `has_more_history` → **`page_full`**：`>= 250` 只能说明"这一页被填满了"，不保证"确实还有更多"
  （原字段名承诺了它不知道的事）。
- `failed_matches` 从截断列表改成自证全量的对象：`{total, returned, truncated, items}`
  —— 以前 `len(failed_matches)` 最多 10，与 `window.matches_failed` 数字对不上也没说明。
- `PGCR 缓存整段抽去 `services/pgcr_cache.py``（541 → 461 行），两个新文件都登记进体积闸
  （`pvp_weapon_service.py` 461、`pgcr_cache.py` 106，只降不升）；缓存行为不变，
  目录仍是 `~/.destiny_mcp/cache/pgcr`。
- 补守门：模式词表扫描改成 AST 版并覆盖 `tests/`、`scripts/`，另加"中文模式标签不许硬编码"
  粗筛（第一版只认行首赋值语句，换个写法就漏）；`assistants.py` 到分支的**位置传参顺序**
  由工具层测试钉死（CI 不跑 mypy/ruff，签名错位以前没有任何网）。

**条数类参数的哨兵规则落地（同日补）**：

- **`count`/`limit`/`maxtop` 的 0 与负数一律算「没指定」**：`_param_docs.Limit` 一直承诺
  「传 0 或负数等于没指定」，但代码只判 `None` —— 传 0 会走到服务层的 `max(1, …)`，
  静默变成「要 1 条/1 场」。现在收敛到 `tools/_helpers.positive_or_default`，六处
  （背包/武器/配装/子职业/世界/活动）共用一条规则；`top_n`/`slot_number`/`max_replacements`
  **不在**这条规则里（它们的 schema 是 `ge=1`，0 由 schema 直接拒收）。
- **新守门 `tests/test_parameter_sentinels.py`**（14 条）：这个文件名在体积闸注释里被引用已久，
  但文件根本不存在；现在既有助手单测，也有工具层断言（不传与传 0 必须走同一个默认值）。

**CI 加静态检查 ruff，并修掉它抓出的一个真 bug（同日补）**：

- **CI 现在跑 `python -m ruff check`**（在 pytest 之前）：它抓「未定义的 name / 未使用的 import」
  这类问题，比测试更早。真机验证：`build_service` 用了 `profile_components` 却从未导入 ——
  **修复前该方法真机报 `NameError: name 'profile_components' is not defined`**（build 取账号护甲
  那条路对用户就是崩的），修复后正常返回子职业与碎片；`activity_service` 缺 `player_names`
  导入也是同一类。
- 顺带清掉全仓 36 个存量告警（21 个自动修复；其余是死变量、测试里未导入的 `Any`、
  重复定义，以及 5 处刻意摆放在常量之后的「单一出处」import）。
- **mypy 不进 CI**：存量 411 个错（70 个文件），一次修不完。路线写进 `ci.yml` 注释：
  先把某个模块修到干净，再加进那行注释里的文件列表，覆盖够了再换全仓；现在它是本地工具，
  不是门槛。

**全量语料实跑抓出的两处问题（同日补）**：

- **`data` 里不许再带 `message`**：`weapon_history` 四个别名与新的 `pvp_weapons` 都把
  `message` 留在了 data 里，信封规则（`envelope_violations`）判违规 —— 全量语料里
  5 条 FAIL 全是这一个根因（老路径一直带着它，没人跑到过）。现在工具层统一把
  `message`/`warnings` 拆到信封（`_split_envelope`），data 只留数据；语料里那行原本
  断言的是 `data.message`（等于把违规形状钉死了），改成查顶层 `summary`。
- **全量语料 runner 自己写坏了一行**：`run_corpus_all_rows.py` 的 career 三档那一行，
  布尔条件中间混进一个字符串，把 `check()` 的位置参数从 4 个撑成 5 个 —— 表现是
  **跑到第 40 行才 `TypeError` 崩掉**，前面几十行结果全废（这一行从来没被跑到过）。
  已修，并新增 `tests/test_corpus_script_health.py`：AST 扫每个 `run_corpus_*.py` 的
  `check()` 位置参数个数 + 语法可解析（守门自己也验证过会咬人）。

**回归报告复核后修的五个问题（同日补）** —— 都是"报告打了 ✅、其实是错答"的那几条：

- **搜索结果的玩家名少了编码**（`OneTop丶Husky#`）：真凶是 `bungie_client.search_player`
  自己拼 `f"{p['displayName']}#{p.get('displayNameCode', '')}"` —— `displayName` 是**平台名**，
  而 `displayNameCode` 这个字段**根本不存在**（真名 `bungieGlobalDisplayNameCode`），
  于是每个候选都带一个尾随空 `#`、四个平台分不出谁是谁。同类写法在
  `player_service._identity_of` 还有一处（空 code 会拼出 `名字#`）—— 两处都改走唯一出处
  `utils/player_names.bungie_display_name`。守门三条：空 code 的行为断言、禁止自己拼 `名字#code`、
  平台名扫描扩到 `["displayName"]` 与"和 `#` 同时出现的 `displayNameCode`"（请求体里的同名字段不算）。
- **「玉兔」按名找不到武器**（`perk_pool`/`info`/`analyze` 全报 `manifest_error`）：
  `search("玉兔")` 命中 **54 条**，前 53 条是同名的 `itemType=20` 条目，真武器排第 7 位，
  而按名找武器只在**前 5 / 前 10 条**里挑 `itemType==3`。现在 `search()` 支持 `item_type`
  过滤（切片之前筛），`weapon_profile.find_weapon` 用它；`perk_service` 里那份重复实现删掉。
- **counters 的 `query` 英文词静默返回 0**：`query` 是名称/描述的**子串**匹配，真机
  `query="crucible"` → 0 条、`query="熔炉"` → 13 条。现在 0 命中会带一条提示
  （"这是子串匹配，按模式用 mode=、按周期用 period="），文档同步纠正 ——
  那份报告把它读成了"query 参数不被支持"。
- **按模式的生涯统计不附游戏内计数器**：`stats(mode="trials")` 给击败 1,474 / 胜场 105，
  游戏内计数器是 **10,696 / 826**（差 7 倍），而按模式调用以前既不附计数器也没有提示，
  用户只会看到那个小数（报告还把它当成"试炼生涯"报了出去）。现在配对表按模式展开
  （crucible + trials），按模式调用会附同模式的生涯计数器 + 一条"两个来源都真实、别相加"的
  warning；读不到只降级（`counters_unavailable` + warning）。
- **收藏品节点拿 0 当"没有"**：`search_collectible_nodes` 说「动能武器（50 件物品）」，
  `collectible_node` 却回 `counts 全 0`、items 空 —— 那 50 条是 `records`（条目），
  组件 800 里没有它们的收藏状态。现在响应带 `declared{records,collectibles,presentation_nodes}`、
  `child_nodes`（可继续下钻）与 `empty_reason`（说清 0 只能读成"这个口径下没有可查的收藏品"）。

**PVP 战绩 P0：接上"游戏内计数器"（profile 组件 1100）** —— 以前我们报的生涯数字全部来自统计接口，
游戏里显示的那个数我们根本没读过。这一版把组件 1100 接上，并把它和统计接口**分成两个来源**
（口径与实测见 `docs/reference/bungie_api.md` 第十一节）：

- `profile_components.METRICS = [1100]`（组件号的单一出处）；
- Manifest 白名单加 `DestinyMetricDefinition`，`get_metric_definition(hash)` 解析名称/描述
  （查不到降级成 `#hash`，不编名字）；
- `services/activity_counters_service.py` 读计数器：**带重试**（真机实测会整块缺失），
  重试后仍为空就返回 `unavailable`，**不把空当 0**；数值降序、`query` 可按名称/描述筛、
  `count` 限条数，每行自带 `source: "profile.metrics"`；
- 新入口 `activity_assistant(intent="counters")`（参数 `player_name`、`query`、`count`；
  `character` 对它无意义 —— 计数器是账号级的，传了会被 `ignored_parameter` 拒绝）。

真机核对：`811894228 = Opponents Defeated` 的 `progress = 124495`，与游戏内显示一致
（同一组件本账号共 402 条计数器）；统计接口账号级同项当时记为 107,106 —— **P1 复核后
更正为 78,864**（107,106 是把已删角色算了两遍，见上面的「未发布（2026-09-18）」）。
两个数都给、各自标来源。

## 0.4.7 — 2026-09-16

**登录不再被 scope 卡死**：`AdvancedWriteActions` 是 Bungie **按应用审批**的 scope，
应用没被授予时授权页直接回 `invalid_scope` —— 真机上连登录都做不成（0.4.6 刚把这条 scope
写进授权 URL，就撞上了）。现在：

- `_auth_url(..., scope=...)` 支持不带 scope；`main()` 先按"带 scope"试一次，
  授权页回 `invalid_scope` 时**打印说明并自动退回"不带 scope"再登一次**，
  功能上只少了「带消耗/不可逆的插槽写入」那几条路；
- 说明里直接给出申请入口（<https://www.bungie.net/en/Application>）：
  想要 API 也能装护甲模组，就得给应用申请 `AdvancedWriteActions`，批准后重新登录。

## 0.4.6 — 2026-09-16

**用户指出"DIM 能操作模组"，一查果然是我们错了。** 两处：

1. **接口选错了**：`_insert_armor_mod` 按"能量消耗 > 0"去选付费接口 `InsertSocketPlug`，
   但 Bungie 的 "free" 指的是**没有材料消耗** —— 官方文档明确 `InsertSocketPlugFree` 覆盖
   "Perks, **Armor Mods**, Shaders, Ornaments"（<https://bungie-net.github.io/>）。
   护甲模组消耗的是能量、不是材料，本来就该走 free。现在**先走 free**，只有上游回
   1663 `DestinyItemActionForbidden`「只能游戏内做」时才退回付费接口；
   403「scope 不够」不再盲目重试（那不是"这个 plug 不免费"）。
2. **OAuth 从来没申请 scope**：授权 URL 只有 `client_id`/`response_type`/`state`/`redirect_uri`，
   一个 `scope` 都没带 —— 所以令牌里没有 `AdvancedWriteActions`，写入被拒成
   403 `AccessNotPermittedByApplicationScope`（DIM 能做，正是因为 DIM 申请了这个 scope）。
   现在授权 URL 带上 `scope=AdvancedWriteActions`；**生效需要用户重新登录一次**
   （`.venv/bin/destiny-mcp-oauth --no-open --timeout 900`），并且 Bungie 开发者后台里
   这个应用要允许该 scope。

守门测试按新决定重写：free 优先、只有 1663 才退回付费、403 不重试付费接口。

真机进度不变：**五件护甲已全部换上**（武器 106 / 生命 30 / 职业 40 / 手雷 165 / 近战 44 / 超能 125，
差的正是那 5 颗属性模组）；重新登录拿到 scope 后再跑一次，模组应当能通过 free 接口装上。

## 0.4.5 — 2026-09-16

真机配装测试打通到"装备全部换上"这一步，靠的是把上游原因如实带出来。

### 关键发现：护甲模组**没法通过 API 装**（Bungie 策略，不是我们的 bug）

把模组步骤失败时的上游原文带出来之后，真因一目了然：

| 操作 | 上游返回 |
| --- | --- |
| 装属性模组（`InsertSocketPlugFree`） | **403** `Access not permitted by application scope`（装护甲模组要 `AdvancedWriteActions` scope，本应用没有） |
| 为腾能量卸掉一颗模组 | **500** `This action can only be done in-game.`（卸/换模组只能在游戏内做） |

以前这两句都被丢掉，只写"模组 4183296050 → '铁能面罩'"，于是看上去像我们的插槽查找又错了。

### 修复

- `_equip_local_unlocked` 的模组步骤失败时带上游原文；识别上面两种"策略限制"后
  **不再当成硬失败**：记下来、继续走完剩下的写入，最后返回
  `mod_in_game` 步骤 + 明确话术（"装备已经换上；这几颗模组请在游戏里手动装"）；
- `_apply_exact_with_recovery` 见到 `mod_in_game` 时**不回滚装备** —— 装备是好的，
  回滚等于把用户要的东西又脱下来；
- `_capture_recovery_state` / `_restore_exact_state` / `_verify_restored_items`
  全部搬进 `services/loadout_recovery.py`（`RecoveryStateMixin`）：
  `loadout_equipment_service.py` 795 → **508 行**，`tests/test_module_size_ratchet.py`
  的上限随之下调到 **520**（上限只降不升）；`tests/test_profile_components.py`
  的调用点钉桩跟着搬家。

### 真机结果（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器）

- 求解 ✅ → 转移 ✅ → **批量装备 ✅ 五件全部换上（且不再回滚）**；
- 五件装备的 `final` 六维求和（**未含计划里的属性模组**）：
  武器 **106** / 生命 30 / 职业 40 / 手雷 **165** / 近战 44 / 超能 **125**；
- 求解器预测（**含**属性模组）：武器 106 / 生命 30 / 职业 30 / 手雷 180 / 超能 125 ——
  差的正是 5 颗属性模组（2× 武器 +10、3× 手雷 +10），**需要用户在游戏里手动装**（Bungie 策略）；
- 全量测试 **1526 passed**。

## 0.4.4 — 2026-09-16

接着真机配装测试往下打：这一版把"模组装不上"的真正原因挖出来了，是**组件请求**的问题。

### 修复

- **只请求 305（插槽）时上游一个插槽都不返回** —— 真机实测：`get_profile(..., [305])`
  返回 **0 件**带插槽；带上清单类组件（102/200/201/205/300）才有 **1627 件**。
  模组插槽读取正是只写了 `[305]`，于是拿到的是一串空数据，接着每件护甲都报
  "找不到模组 X 的唯一兼容插槽"（前几轮一直以为是插槽匹配逻辑的问题）。
  现在统一走 `profile_components.INVENTORY_SOCKETS`，并加了守门测试：
  **要 305 就必须带清单类组件**（裸组件号一律拒收）；
- **插槽缓存里的"空列表"被当成了"这件的插槽是空的"**：装备刚被搬过来时快照里还没有它，
  旧代码只判"键在不在缓存里" → 每个槽都被跳过。现在空列表会触发重读；
- **两处同步窗口重试**（与写入后回读同一个坑）：① 读某件实例的插槽（`write_readback` 重试到非空）；
  ② `equip_items` 的"物品必须在目标角色背包里"预检 —— 刚搬完立刻批量装备必失败
  （报"请先用 move_item 转移"，可转移明明刚成功）；
- **批量装备失败不再吞掉原因**：以前只写"批量装备 N 件物品"，现在带上游原文
  （就是靠这句才看到真正的原因是"物品还没在目标角色背包里"）。

### 结构

- `_capture_recovery_state`（131 行）抽成 `services/loadout_recovery.py`（`RecoveryStateMixin`），
  `loadout_equipment_service.py` 795 → 671 行；`tests/test_module_size_ratchet.py` 的上限
  **随之下调到 680**（上限只降不升）。

### 真机进度（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器）

- 求解 ✅（5 套候选 100% 达标，预测六维 武器106 / 手雷180 / 超能125）；
- 转移 5 件 ✅ → **批量装备 5 件 ✅**（这一版之前必失败）；
- 卡在**"为属性模组腾出能量"的写入**：`mod_clear` 那一步失败（原因同样被丢掉了，
  下一步要补上原文再修）；
- **账号每次都完整回到执行前状态**（逐件核对：五件护甲的属性与位置、仓库里那件都在原位）。

## 0.4.3 — 2026-09-16

真机做了一次完整配装测试（术士 / 星火协议 / 180 手雷 + 100 超能 + 100 武器），
这一版修的是那次测试暴露出来的东西。

### 修复（真机复现 → 已修）

- **`equip_build` 直接崩**：`LoadoutEquipmentService` 用了不存在的 `self._ARMOR_SLOTS`
  （那只是 `loadout_service` 的模块级常量，而这个类并不继承它）→
  `AttributeError: 'LoadoutEquipmentService' object has no attribute '_ARMOR_SLOTS'`，
  调用方只看到一句空错误。现在改用**单一出处** `build/constants.ARMOR_SLOT_MAP`
  （经 `item_parser.armor_slot_from_bucket` 归一），这条路径以前没有任何测试覆盖；
- **上游超时被吞成空消息**：Bungie 请求超时时 `TimeoutError()` 没有 message，
  `handle_tool_error` 直接重抛，客户端只看到 `Error executing tool …: `（空的）——
  分不清"网络慢"和"参数错"。现在统一翻译成 `a_p_i_error` +
  "访问 Bungie 超时（网络慢或上游没响应），这不是参数问题：稍后重试即可"，并加了守门测试；
- **三份"桶 hash → 护甲槽位"表收口成一份**：`loadout_service._ARMOR_SLOTS`、
  `equip_planner.ARMOR_BUCKET_BY_SLOT`、`item_parser` 里那段两步查找原本是同一个事实的三份拷贝
  （这次崩溃就是其中一份漂了），现在都从 `build/constants.ARMOR_SLOT_MAP` 派生。

### 尚未修复（已知，真机复现）

**护甲模组插槽预检失败**：`equip_build` 执行到"装模组"这一步时，
对多件护甲报 `找不到模组 <hash> 在 '<装备名>' 上的唯一兼容插槽`，
回滚时同样装不回原模组，于是整条配装以
`配装 '已确认的精确配装' 执行失败，且自动恢复不完整` 结束。
实测影响：**账号最终状态与执行前一致**（逐件核对五件护甲的属性与位置都没变），
所以是"没装成"，不是"装坏了"。修复方向与神器模组同源：
按**该件定义里的插槽与 plug set**（配合组件 310 的 `reusablePlugs`）找候选，
不要假定槽位/plug set 的固定结构。

## 0.4.2 — 2026-09-16

装备编排的三处收口（0.4.0/0.4.1 之后自查出来的）：

- **容量来源统一到 Manifest 桶定义**：规划器原先读 `itemComponents.buckets.data` —— 真机上
  **没有这个组件**，读出来永远是空，"背包满"这条预检等于静默失效。现在两处（规划器的
  `load_plan_request` 与执行侧）都只认 `DestinyInventoryBucketDefinition.itemCount`，
  `used` 含正装备那件；
- **装配收成一处**：`transfer_service.plan_equip_item` 改为委托 `equip_planner.load_plan_request`
  （读哪些组件、容量怎么算、`equipped_keys` 怎么来，只在那边说一次），删掉重复实现；
- **槽位在仓库物品上也有值了**：`InventoryItem.slot` 按 bucketHash 认，而**仓库里的护甲**
  bucket 是仓库格、认不出部位（`slot` 为空）—— 装仓库里的护甲正是最需要编排的场景。
  现在缺失时退回定义的 `itemTypeDisplayName`（`armor_payload.slot_key_from_display`），
  回滚因此知道动了哪个部位；
- 守门：新增"每一步都必须带 `slot`"与"桶定义缺失就不下结论"两条；容量那条测试按新口径重写
  （旧测试断言的是不存在的组件）。

真机核对（只读）：`load_plan_request` + `plan_equip` 在真账号上给出
`[downgrade chest=圣贤保护者法袍 顶下星火协议, equip gauntlets=逃逸艺术家]`，
容量 chest/gauntlets/helmet = 10/10、legs/class_item = 9/10。

## 0.4.1 — 2026-09-16

0.4.0 的装备编排补上**失败回滚**：执行到第 N 步失败时，把**已经改动过**的部位换回动手前那件，
再报"停在第几步、账号现在什么样"。

- 回滚只动**真正变过**的部位（失败那一步通常没落地，把它算进去会白写一次 `EquipItem`，
  而每次写入都要等上游同步窗口）；回滚本身失败也照实说 `rolled_back: false` + 哪个部位没换回，
  不许假装干净；
- 计划步骤新增结构化 `slot` 字段（回滚要靠它知道动了哪个部位，不能从 `why` 的中文里猜）；
- 守门 `tests/test_equip_execute.py`（4 条）：顺序（先顶下再装）、失败回滚、回滚失败如实上报、
  上游说成功但回读没看到 → `unverified` 而不是成功。

## 0.4.0 — 2026-09-16

`equip` 从"直通原语"变成**冲突感知编排**：以前装一件金装会撞 `DestinyItemUniqueEquipRestricted` 500、
装仓库里的会撞 404，用户当时摸索了 10 轮才把星火协议穿上；现在同样的链子是**1 次计划 + 1 次确认**。

### `equip` 两段式（行为变更）

- 不带 `confirmed` → `confirmation_required`，`candidates[0]` 里带 `steps[]`
  （每步 `action/item/from_location/to_location/why/replaces`）与 `blockers[]`，**零写入**；
- `confirmed=true` → 才执行「先顶下、再装目标」，逐步 `EquipItem`，写完**回读核对**
  （`verified`/`unverified`；上游 profile 有同步窗口，走 `services/write_readback.py` 重试）；
- 失败说清**停在第几步**（`stopped_at`/`steps_done`）与**账号现在什么状态**（`equipped_now`），
  不允许部分成功报成功；
- `equip` 因此进了 `SELF_GUARDED_WRITE_INTENTS`，分派在 `tools/_equip_branches.py`。

### 四种预检（只读，先做）

`services/equip_planner.py`（纯函数）+ `models/equip_plan.py`：
`item_missing`（实例不在账号上）/ `item_equipped`（正装备着，上游禁 move）/
`exotic_conflict`（另一个槽的异域挡住，且背包里挑不到非异域顶下）/ `inventory_full`（给数字）；
另有"仓库里的物品要先搬"作为计划里的 `move` 步骤。
`EquipPlanRequest` 的入参语义写死在 docstring：装备位 + 背包**合并**列表，`equipped_keys` 按
组件 300 的 `instances.data[实例].isEquipped` 填（205 的条目没有这个字段）。

### 修复

- 新增 `ManifestManager.get_bucket_definition`（`DestinyInventoryBucketDefinition`）：
  背包容量 `itemCount` 的**唯一出处**，内部 `to_signed()` 回退（头盔/臂铠的桶 hash 超过 int32，直查查不到）；
- 容量 `used` **含正装备那件**（与 DIM 同口径）。真机上 warlock 的头盔/臂铠/胸甲都是 10/10，
  旧口径会假报"还能放一件"，然后去撞上游 `NoRoomInDestination`；
- 写入失败话术补两条：`UniqueEquipRestricted` → "全身只能一件异域，先穿一件非异域的同部位顶下它"；
  `ItemNotFound`/"not found in the character's inventory" → "`EquipItem` 只接受在该角色身上的实例"。

### 新增错误码

`equip_blocked`（上游铁律挡住预检：金装冲突/背包满/正装备着/实例不在账号上，**不是写入失败**）。

### 守门

- `tests/test_equip_planner.py`（14 条：四种预检、计划顺序、挑不到中间件、空 `slot_display` 不留空括号）；
- `tests/test_equip_plan_path.py`（5 条：无确认零写入且带计划、确认后按序执行、blocked 走 `equip_blocked`、
  已在身上不写、执行失败保留停点）。

### 真机验证（账号已完全还原）

`equip` 逃逸艺术家 → 计划两步（顶下星火协议、装逃逸艺术家）→ 确认执行 `verified:true` →
换回星火协议与光芒领主手套 → 最终五部位与操作前**完全一致**。

## 0.3.0 — 2026-09-16

补上两个"想做但做不到"的能力：**换子职业元素**与**换神器**；顺带修掉神器模组的槽位假设。

### 换子职业（新能力）

用户报的原始故障：术士从棱镜切烈日，`modify` 只在**当前**子职业上插 plug，
`loadout_subclass_sockets` 又"子职业不一致就拒绝"，于是怎么都换不过去。

- `subclass_assistant(intent="modify")` 新增变更键 `changes={"subclass": …}`：
  先按别名表解析（`烈日/火/火术/棱镜术士/solar/…`）→ 在该角色的子职业物品里按元素或
  **Manifest 官方名**（破晓/枪手/炎阳…）精确匹配 → `EquipItem` → 回读核对 → **然后**才改插槽
  （槽索引属于具体那件物品，顺序反了就是错的）；
- 已经是目标子职业时**不写**（幂等）；找不到就列出这个角色实际有哪些（官方名 + 元素）；
  职业叫法冲突（猎人说"火术"）**报错不硬来**；不模糊匹配、不查拼音；
- 元素别名进 `destiny_mcp/vocabulary.py`（口语词根 `火/电/冰` + 职业尾缀表），
  官方名不写进表 —— 名字的权威来源是 Manifest；
- `equip_loadout` 的子职业不一致由"直接失败"改为**先换上再配**（用户拍板：顺手换）。

### 换神器（新能力）

- `subclass_assistant(intent="equip_artifact")`（写入，走确认信封）：把该角色背包里的另一件
  神器换上，名字精确匹配，回读核对；
- `subclass_assistant(intent="artifact", character=…)` 附带**角色身上那件**与背包里能换的
  （目录里的"当前神器"按赛季算，与身上那件可以完全不同）；
- 事实校正：神器**不可转移**（`transferStatus` 背包=2、装备位=3），所以只能换同角色背包里的；
  神器有三个 hash 家族（玩家实例族 / 赛季定义族 / `DestinyArtifactDefinition`），
  **写入只能用实例的 hash**；真机实测 `EquipItem` 接受神器实例（推翻"只能装最新赛季"的旧说法）。

### 修复

- **写入后回读要重试**：真机实测 `EquipItem` 返回成功、立刻回读仍是旧值（一次约 3 秒可见、
  另一次 10 秒内仍旧）。新增 `services/write_readback.py`（8 次 × 1.5 秒），
  超出窗口时报 `unverified`（**没确认**）而不是"没换成" —— 上游已经返回成功了，那是两件事；
- **神器的"装没装"只在组件 300 的 `instances.data[实例].isEquipped` 上**（205 的条目没有这个
  字段），而且它对所有装备都为真，必须先在神器桶实例里挑；
- **元素不在子职业物品定义里**，只写在 plug 的 `plugCategoryIdentifier` 第二段；
  `_CATEGORY_PATTERN` 抓的是第三段（槽类型），两者不能混用。

### 守门

- `tests/test_subclass_switch.py`（9 条）、`tests/test_artifact_switch.py`（9 条）、
  `tests/test_write_readback.py`（3 条）；
- 组件表新增 `profile_components.SUBCLASS`（含 201：看不见背包就换不了子职业），
  `tests/test_profile_components.py` 钉住集合与调用点数量；
- 分派契约允许分派层拆到 `tools/_*_branches.py`（这次神器一族搬进 `_subclass_branches.py`，
  顺手把 `assistants.py` 压回体量上限内）。

## 0.2.0 — 2026-09-15

**破坏性变更**：活动统计改成行式（见下）。另外这一版收了上一批架构与守门工作。

### 活动统计改为行式（破坏性）

旧实现手写 8 个键、只取 `displayValue`：真机抓下来上游 `allPvE` 有 **65 项**（`allPvP` 66 项），
实际只拿到 6 项，而且 `precisionkills` 拼错（上游是 `precisionKills`）导致「精准击杀」静默消失，
原始数值与场均（`pga`）全被丢掉——想做"PvE 折合多少小时"这类计算只能去解析 `"42d 11h"`。

- 新增 `destiny_mcp/activity_stats.py`：上游 `statId` → 行式统计
  `{stat_id, upstream_id, name, group, value, display, unit?, per_game?}`，
  与武器属性 `{stat_hash, name, value, display}` 同一套写法；
- **全量给项**：65/66 项一项不丢，没登记中文名的也出行（`name` 留空、`group="other"`）；
- **数值回归**：`value` 给原始数（整数还原 int）、`display` 给人读串、时长标 `unit: "seconds"`、
  场均（`pga`）有就给、没有就不给字段（**不编 0**）；
- **中文名 + 分组**：66 个键全部有中文名，按 `core/activities/objectives/averages/weapons/other` 排序；
- 接线：`stats`/`career`/`historical_stats`、`weapon_history`、`aggregate`、
  `leaderboards`/`clan_leaderboards`（排行榜不再透传上游原始载荷，改自有条目形状）；
- **旧键一个不留**（不双写、不别名）：`data.stats.pve.activitiesEntered` 这类写法在 0.2.0 起不存在；
- 守卫：`tests/baselines/activity_stat_keys.json` 存真机抓的上游键清单，
  `tests/test_activity_stats.py` 据此断言「每个键都有中文名」「行数等于键数」「缺值给 None」——
  上游新增统计项会让测试红，逼着做决定，不再静默少项。

### 架构与守门（本版同批）

- **CI**：`.github/workflows/ci.yml`（Python 3.12/3.13：`pytest` + 无凭据构造工具面冒烟）；
- **错误码单一出处**：新增 `destiny_mcp/error_codes.py`（`ErrorCode` 枚举 +
  `code_for_exception()` + `write_failed()`），43 处裸字符串全部替换；
  `tests/test_error_codes.py` 禁止裸字符串、并钉住"异常类名即契约"；
- **词表归一**：新增 `destiny_mcp/vocabulary.py`（六维/职业/位置/元素/旧属性名），
  此前同一份词表散在 9 个文件、值已经漂了——顺带修掉真 bug：
  旧名「韧性模组」被映射成不存在的「生命模组」（游戏里叫「生命值模组」），真机报「没找到护甲模组」；
- **结论路径不许静默降级**：护甲阶梯的探测失败不再被记成"试过没有解"（改 `ok: null` + `not_probed` +
  `reason`），调谐额度拿不到会带 `tuning_unavailable_reason`，参数归属名单读不出会 ERROR 日志 + warning；
  `tests/test_conclusion_paths.py` 扫描结论模块的 `except` 必须留痕；
- **信封统一第二批**：`data` 及其子块不再有 `success`/`message`（写入领域结果除外），
  自有键一律 snake_case（`fragments[].name_en`、金装候选 `name_en`）；`sweep` 组新增两条守卫，
  110 个 intent 全量检查；
- **兼容面定规矩**：`docs/COMPATIBILITY.md` 登记 21 组**实测等价**的别名与 69 个历史工具的去留，
  `tests/test_intent_aliases.py` 防"别名偷偷跑偏"；
- **分层守门**：`tests/test_architecture_layers.py`（依赖只能向下、禁止环、`svc` key 必须在
  `ServiceContext` 里声明）+ `utils/item_parser.py` 挪到 `services/`。

## 0.1.13 — 2026-09-15

新增一份全面语料：`docs/testing/TESTING_CORPUS_FULL.md` + `scripts/run_corpus_all_rows.py`，把八个工具面
**全部 110 个 intent** 的信封体检、其余六个工具面的字段级契约和 MCP 协议层都变成可执行断言。
第一轮真机跑（205 行）抓出 7 个问题，这次一并修掉：

- **`analyze` 不再替求解器下结论**（P1）：`build/analyzer.py` 的兜底文案以前写死
  `No valid armor combination found`，但它只算了单项上限，从来没验证过有没有合法组合 ——
  实机出现过 analyze 说配不出来、**同一组约束** `recommend` 给出 `completion_rate=1.0` 的方案，
  而且 `max_possible.health=134 ≥ 目标 100`。现在如实说明「各项目标都在单项上限内，单看上限
  解释不了」并指路 `recommend`/`find`；两处 reason 与刷取建议也一起中文化（以前是英文）。
- **`exotic_armor` 与 `intent="item"` 统一键集**（P2）：`get_exotic_armor_details` /
  `get_exotic_armor_list` 以前直吐 Manifest 原始键（`nameEn`/`flavorText`/`classType`/`tierType`/
  `intrinsicPerks`），同一件护甲两条路径两套键集。现在走同一个身份块工厂
  （`armor_payload.armor_definition_payload`）：`identity.{name, name_en, slot, slot_display,
  item_type_display, rarity_tier, class_type, class_display, …}` + `intrinsic_perks`。
  定义级没有实例，`gear_tier`/`armor_system` 给 `null` 并附说明，不编 T 级。
- **收藏品 hash 统一无符号**（P2）：`collectible_item` / `collectible_node` 以前直接吐 Manifest
  原值，同一份响应里有正有负（实测 `-2064629060`、`-1315203219` 等，负数拿去别的面按 hash 查
  必然查不中）。现在对外统一 `to_unsigned`，读收藏状态时无符号与有符号两种键都试。
- **`artifact` 不带名字也给当前神器**（P2）：`get_seasonal_artifact("")` 现在也返回
  `current_artifact`，「我现在用哪个神器」不用先知道神器名字。
- **报错不再叠句号**（P3）：`SubclassError` / `APIError` 的包装遇到已经带句号的整句不再拼第二个
  句号（实测出现「…或中文职业名。。」）。
- **背包搜索未命中说「没找到」**（P3）：0 命中时 summary 从「已搜索物品。」改成
  「没找到叫「X」的物品。」；文案抽到 `_formatters.inventory_search_summary`，
  `tools/assistants.py` 的行数上限同步收紧到 1403。
- **`intent="item"` 传 null 不再裸抛**（P3）：`armor_item` 的实例 ID 判断补 `or ""`。
  真 MCP 路径本来就被 schema 层拦住（`string_type`），这是进程内的防御性缺口。

语料第一轮结果：sweep 110 个 intent 全部干净（无异常、无超时、无「ok=true 但 data 空」、
无写入漏网），mcp 组 11 行全过；上面 7 条都是 rows 组抓出来的。回归锁见
`tests/test_corpus_full_regressions.py`（12 条），另外更正了主语料里两处被实测证伪的口径
（「subclass 默认 10 条」其实只有 `community` 读 `limit`；`leaderboards` 不是恒失败而是间歇）。

## 0.1.12 — 2026-09-14

强化版 perk 的展示名后面加 `↑`，普通版保持原字符串不变。

- 约定：`高爆载荷` 是普通版，`高爆载荷↑` 是强化版。适用位置包括
  `weapons[].sockets[].options[].name`、`equipped.name`、实例可换项，以及社区核对里的
  `perks_current_match` / `perks_available_to_switch`。带箭头的项同时给 `name_plain`
  （Manifest 规范名），按名字查表或比对时用它。
- 内部按名字索引的地方（刷取清单 `farm_index`、愿单、`filter_rolls` 的匹配）改用规范名，
  避免箭头影响匹配；`weapon_local_data` 直接读选项上的 `name_plain`。
- 修正 `analyze` 的 `differences` 口径：它以前按名字比对副本，一把普通版加一把强化版会显示成
  双向缺失；现在名字能区分版本，差异才有意义。
- 结构调整：`with_equipped` 从 `weapon_payload.py`（已顶到 365 行上限）移到
  `weapon_profile.py`，该文件上限同步收紧到 334，用搬移而不是抬上限来腾空间。

实机复验（术士邮政长里的「无感」）：特性 1 显示 `现在=快速命中↑ / 可换=[快速命中↑, 即兴弹药↑, 集体爆破↑]`，
特性 2 显示 `现在=高爆载荷↑ / 可换=[高爆载荷↑, 肾上腺素成瘾↑, 盒式呼吸法↑]`；社区核对里
`巅峰捕食者` 的一把为 `perks_current_match=['爆炸光能↑','爆炸协议']`，其余副本是普通版。

同时重录了武器面基线：它从 0.1.6 前后就没再录过，累积了若干版本的漂移（hash 统一为无符号、
`farming_list` 挂载、`name_en` 字段等）。核对过 `catalog_*` / `filter_rolls_*` 的命中集合变化，
是有符号与无符号的同一批物品（例：-1205549507 与 3089417789），不是筛选逻辑变化；
diff 报出的消失项全部是早期登记过的（P4 精简身份块）。

## 0.1.11 — 2026-09-14

把「可切换但未装备的 Perk」真正查出来。0.1.10 只做到如实说明没有查。

- 武器详情新增可选开关 `include_selectable_plugs`（默认关闭，其他调用方载荷不变），打开后给每栏
  挂 `selectable_plug_hashes`（组件 310 的 hash，不展开为带名字的 options）。社区配装匹配走这条路，
  因此每个副本能报三层：`perks_current_match`（当前装备）、`perks_available_to_switch`（可换栏可达）、
  `perks_unavailable`（两者都没有）；行状态依次为 `current_roll_matched` →
  `owned_alternate_roll_available` → `owned_no_current_roll_match`。
- 「没读」与「读了没有」分开：`selectable_plug_status` 取 `available` / `none` / `not_read`，
  只有 `available` 时「换也换不到」才是结论，否则仍是 `alternate_perk_options_checked=false` 加原因。
- 两条边界写进字段：判定范围是 `any_selectable_socket`，不校验栏位是否与模板一致；锻造件的组件 310
  只列当前选中项（`alternate_perk_options_caveat`），未命中不等于换不到。
- 实现过程中修掉一个方向相反的坑：组件 310 的真实形状是 `{"plugs": {"<槽>": [...]}}`，第一版直接遍历
  外层，`_instance_plug_hashes` 收到的是 Mapping，静默返回空集，于是「有 4 栏可换」被报成「读了但没有
  可换项」。实机复验时才发现，`selectable_plug_status` 就是为区分这种情况加的。

实机复验（复盘里的同一套配装）：`笛卡尔坐标` 两把的 `重建/洪涝/老兵睿智` 确证换也换不到
（`perks_unavailable`，`selectable_plug_status=available`）；`巅峰捕食者` 一把的
`爆炸光能`、`爆炸协议` 当前装备，`爆破专家` 换也换不到。

## 0.1.10 — 2026-09-14

按另一台机器的实机复盘（Windows，另一个 agent）修正否定结论的表达。那次事故的链条是：模板要求
「玻璃拱顶 ×4」，工具标 `unresolved`，调用方标「待自查」，最后却回了「这套能直接玩」，
而同一份响应里已经写着 `execution_supported=false` / `execution_eligible=false`。
问题不在数据，是否定结论没有出现在调用方一定会读到的位置。

- summary 带可执行性判定：社区详情返回「已读取社区配装：X；不可直接执行（社区模板不是服务器签发的
  ExecutableBuild）」，并把 `execution_eligible=false`、首要 blocker、以及「要装备必须走
  find → canonical_build → 确认」放进第一条 warning。
- 活动名到套装名的解析：社区模板按活动称呼套装（玻璃拱顶），Manifest 与玩家物品用套装名
  （埃希恩记忆）。已核对的映射会自动解析并说明来路（`resolved_via` / `alias_from` / `resolved_name`）；
  没有登记的返回 `unresolved_reason=name_not_matched_candidates_available` 和 `set_name_candidates`
  相似候选，调用方可以据此向用户确认，而不是只得到「查不到」。
- 套装持有数量可以直接读：套装行新增 `owned_count`、`owned_distinct_slot_count`、
  `missing_slot_count`、`wildcard_count`。实机复验同一套配装：玻璃拱顶解析为埃希恩记忆，需 4 件，
  持有 20 件覆盖 5 个部位，缺 0。
- 「没校验」与「没有」分开：所有 `not_account_checked` 行带 `unverifiable_reason` 枚举
  （`mod_unlock_state_not_available`、`artifact_*`、`subclass_unlock_state_not_available`、
  `stat_feasibility_not_checked`、`free_text_not_checkable` 等）；`unresolved` 行带
  `unresolved_reason`；武器行补 `alternate_perk_options_reason=instance_socket_options_not_read`，
  警告文字改为说明 `owned_no_current_roll_match` 只表示当前选中的 Perk 不符。
- 参数说明补上各 intent 读取范围：`character` 一栏列出会返回 `ignored_parameter` 的 intent；
  `component` 一栏写明碎片不属于 component，应使用 `intent=fragments`。

当时仍未做的：真正的「可切换但未选中」比对需要武器详情载荷带上实例可换项（组件 310），
属于武器面改动，有自己的基线与体积上限，留到 0.1.11 完成。

## 0.1.9 — 2026-09-14

干净安装冒烟（从 GitHub 安装 v0.1.8 到全新 venv）发现两处一致性问题。

- 包内 `__version__` 停在 0.1.0：无论发布到哪个版本，`destiny_mcp.__version__` 一直写着 0.1.0。
  现在改为源码树读 `pyproject.toml`、安装后读发行版元数据，`tests/test_package_version.py`
  校验两处一致。
- 注册模板里的客户端超时 180 秒余量偏小：无解诊断实测 80–165 秒，还要叠加调谐补齐那一趟，
  慢一次就会被客户端中断，用户看到的是超时而不是阶梯。模板与文档统一改为 300000 ms。

冒烟结果：从 `git+https://…@v0.1.9` 装进全新 venv，8 个工具握手成功；复用 tokens 与 manifest 后
真机读取正常；技能安装器在临时 `DSH_HOME` 下正确写出指针块与 MCP 注册文件。

## 0.1.8 — 2026-09-14

真机写入实测发现两个发布阻断问题。

写入失败被报成成功。`ArmorModService.apply()` 收到的形状是 `{"ErrorCode": …, "Message": …}`，
Bungie 把错误也放在 200 响应的信封里，而这里以前不看内容直接返回 `success: True`。
实测换调谐时账号一个字节都没变，工具却回了「已把槽 11 换成 +手雷 / -职业」。现在必须核对
`ErrorCode == 1`，否则抛 `TransferError` 并带出 Bungie 原文；权限类错误
（`AccessNotPermittedByApplicationScope`）会点名 `AdvancedWriteActions`。回归测试写进
`tests/test_equip_mod.py`，假客户端改成 Bungie 的真实信封形状（以前回 `{"success": True}`，
正好遮住了这个 bug）。

调谐无法通过 API 写入，据此改了设计：免费插槽接口（`InsertSocketPlugFree`）对调谐返回
`This action can only be done in-game.`（ErrorCode 1663）；付费接口（`InsertSocketPlug`）需要
Bungie 应用的 `AdvancedWriteActions` 权限，当前授权没有，返回 403。因此 0.1.6 与 0.1.7 里
「`canonical_build` 已带上调谐插件、确认即可执行」是做不到的承诺。本版改为：`equip_mod` 对调谐
只给方案（`writable=false`、`written=false`，附「只能在游戏内改」的 warning，`confirmed=true`
也不写账号）；`canonical_build.items[].mods` 不再包含调谐插件；`tuning_changes` 明确为给玩家的
手动清单，`tuning_note` 同步改写。

其他：README 补充写入权限、调谐只能游戏内修改、无解诊断 80–165 秒与客户端超时建议；
语料护甲章节新增两行（调谐只给方案、付费写入如实报权限），共 26 行。

## 0.1.7 — 2026-09-14

修掉调谐救援的候选池上限。求解器只保留排名前 200 套（`RETURNED_ARMOR_SETS`，沿用 DIM 的设计），
而放宽目标那一趟的池子按放宽后的目标排名，可救的方案可能排在 200 名之外，表现为明明能补却报补不上。
实测（猎人 118 件护甲，目标武器 150 加生命 103）：池上限 200 时救回 0 套，放到 1500 时救回 16 套。

修法两步：`solve()` 增加可选参数 `returned_sets`（不传即旧行为），放宽那一趟传 1500；
池子变大后无法逐套精确复核（每套约 0.1 秒，1500 套约三分钟），改为按「护甲加调谐额度之后还差多少」
排序，只复核最值得的前 40 套，复核仍是唯一裁判。修完实测：武器 150 加生命 103 从 0 候选变为
5 套候选、每套只改 1 件调谐（47 秒）；武器 150 加生命 106 为 5 套、2 件调谐（36 秒）。

已知限制：救援覆盖放宽解排名前 1500 套且复核能过的方案，再往后的套仍然看不到；
挑候选用的是算术估计，理论上可能把可救的套排在 40 名之外。

## 0.1.6 — 2026-09-14

调谐进入求解器。此前目标差 5 点只能给人工提示，现在 `find` 与 `recommend` 会实际尝试更换调谐再算一遍，
能补上就直接返回带方案的候选。

- 做法是两趟加精确复核：按原目标解一次，达标就原样返回（基线里的绿方案不变）；没达标才按调谐额度
  放宽目标复解，然后对候选逐套精确复核，用真实目标重新分配属性模组并逐项比对六维，通过复核才算数。
- 对外字段：`builds[].tuning_changes`（逐件 from/to/delta，中文名加 hash）、`requires_tuning`、
  `tuning_note`，以及响应级 `tuning` 汇总。
- `equip_mod` 支持调谐：报全名（如 `+武器 / -生命值`）即可，方案里给 `kind="tuning"`、
  `stat_bonus`（含 −5 一侧）与 `energy`；只说「手雷调谐」这类未指明减少项的说法会报错并列出 5 个选项。
- 阶梯口径改实：`tuning_first` 变成「已经试过调谐」之后的结论，新增 `solver_attempted` 与三种 lever
  （额度够但无法让步、额度不够、只看属性模组）；新增 `verdict`，`satisfiable=false` 表示按原始优先级
  实采 0 候选，并说明 `ceiling` 是各次探测逐项取的最大值、不等于同一套能同时达到。

实机验证（118 件护甲的猎人，只读）：调谐额度实测为每个部位取最强的一件、五项合计 25 点，
含撤掉反向调谐的 +10 情况，所以单件上限是 10 而不是 5；`武器 150 加生命 103` 严格解为 0 候选，
放宽复解后给出 4 套达标方案并逐件列出调谐改动；`武器 150 加生命 106` 返回
`verdict.satisfiable=false`，与手算边界一致（护甲 110/52 加模组 50 加调谐最多 33，凑不出 150/106）。

对比 DIM（读源码后的结论）：DIM 在主循环里展开调谐变体，非金装逐件展开成多个 ProcessItem，
金装因为一套只能有一件而改成在主循环里换 variant，并用 dump stat 把变体数压到个位数。
本项目的组合上限是 2000 万，扛不住那种展开量，所以采用同样思路的收敛版：零和语义加牺牲价值最低的一项，
能否达标交给精确复核裁决，另加一个复核驱动的局部搜索兜底（最多改 5 件，每步都过复核）。

过程中修掉的实机问题：调谐额度把整个仓库相加（118 件得出每项能补 563 点）；规划基线把求解器已配的
模组又加一遍；剪枝按每件最多 +5 计算，漏掉撤掉反向调谐等于 +10 与模组预算，把可行组合整支砍掉；
`plan_tuning` 把 −5 打在本就为 0 的属性上当成有代价（游戏里属性下限为 0，属于白给）。

## 0.1.5 — 2026-09-13

L2 冒烟集全跑（25 条 ⭐ 行）发现两个问题。

- 0.1.4 重构 `analyze` 提前返回时，把 `precision="not_computed"` 一并删掉了：超规模时 `reason`
  说明没算，`precision` 却报 `exact` 且 `max_possible={}`，调用方会读成什么都达不到。已补回，
  并新增 `tests/test_build_size_guard.py`（4 条）固定该口径：超限必须
  `precision="not_computed"` 加空 `max_possible`，analyze 与 find 两条路同一句说明。
- 语料写错了路径：武器 T 级那条原写 `analyze` 的 `weapon.gear_tier`，实测 `analyze` 的 T 级在
  `data.inventory.instances[].weapon.gear_tier`（逐副本），`type` 才是
  `weapons.items[].weapon.gear_tier`；`gear_tier_note` 也并非处处都有（`owned.instances[]` 里有，
  `analyze` 的副本块只有 `gear_tier`）。语料行按实测改写，武器逐行脚本新增第 17 行锁住这三条路径。

冒烟集结果 26/26 PASS（25 条 ⭐ 加工具面核对），覆盖 8 个工具、写入拦截、参数误用指路、
schema 层拒绝、社区资料不可信提示、护甲四条新行。

## 0.1.4 — 2026-09-13

修 0.1.3 实测跑出来的 5 个问题。

- `rarity` 中文值被静默忽略：参数说明写着传说/异域可用，代码只映射英文，取不到就跳过过滤，
  问有哪些异域腿甲会把传说件一起返回（实测 `异域` 26 件、`exotic` 8 件）。现在中英结果一致，
  不认识的稀有度直接报错并列出可用取值。
- `recommend` 与 `find` 在组合规模超限时会跑到客户端超时：术士同参数下 `analyze` 秒回
  「2.43 亿组合超上限、未计算」，而 `recommend` 会真的枚举，客户端拿到
  `-32001 Request timed out`。现在两条路共用同一套估算与同一句收窄建议，超限立即返回
  `not_computed` 和四条可操作建议。
- `equip_mod` 的 `alternatives[].stat_bonus_hashes` 口径不一致（原始 hash，还把非六维的费用属性算进去），
  改成与 `to.stat_bonus` 同一套六维可读键。
- `with_slot_keys` 会把展示字段塞进 `canonical_build.items[]`，改为递归时跳过 `canonical_build` 子树，
  并加断言。
- `intent="item"` 的 `next_actions` 还写着「换模组后续阶段提供」，改为指向 `equip_mod` 的正确用法。

另外修了差异闸门自身的问题：`--allowlist` 传相对路径时，报错分支的 `relative_to` 会崩，
把真正的字段消失吞成一条 traceback。语料护甲章节从 18 行扩到 20 行，冒烟 ⭐ 25 条。

## 0.1.3 — 2026-09-13

护甲部分统一格式、支持更换单个模组、无解时给出六维阶梯。

此前护甲在六个地方出现且字段各不相同，也无法更换单个模组（默认工具面没有写入入口，
legacy `apply_mod` 又不经确认直接改账号）。本版按 `docs/plans/ARMOR_FORMAT_PLAN.md` 的 P0–P6 完成：

- 统一载荷 `ArmorPayload`：`identity`（`slot`/`slot_display`/`gear_tier`/`archetype`/套装）加
  `instance`（光等、位置、能量、大师、调谐）加三层属性 `roll`/`base`/`final` 加插槽清单。
  列表保持轻量（只加 `slot`/`slot_display`/`gear_tier`/`armor_system`，`bucket_type` 保留）。
- 新增 `inventory_assistant(intent="item")`：单件护甲的完整载荷，词条本体槽与 `intrinsics`
  标 `editable=false`。
- 新增 `inventory_assistant(intent="equip_mod")`：更换一个模组。`confirmed=false` 时返回
  「哪件护甲、哪个槽、从什么换成什么、能量怎么变」的确认请求，确认后才写；校验实例是否在该角色身上、
  该槽是否接受该模组、能量是否足够。legacy `apply_mod` 收编到同一条确认路径。
- 无解时返回 `ladder`：`shortfall`（差多少）、`ceiling`（同一套约束下同时能达到的上限，实采）、
  `single_stat_ceiling`（单项上限，两者不能混用）、`trials`（逐级放松试了哪些档）、
  `suggestion`（最小可行降档，只是提议，不自动降目标）。
- 装备确认逐件预览：`candidates[0].items_preview` 给出五件的光等、能量、现有模组与将要装的模组。
- 实机勘测修掉两个问题：老护甲也带 `gearTier: 0`（按字段分族会误判为 3.0）；异域护甲的固定属性
  分布在 `intrinsics` 里（不算进 `roll` 会把大师等级算成 30）。
- 文档：`routing.md` 补护甲统一键口径与 `find`/`recommend` 分工（要装备走 `find`）、`ladder` 读法；
  `docs/testing/TESTING_CORPUS.md` 新增「十六、护甲」章与逐行脚本 `scripts/run_corpus_armor_rows.py`（12 行）；
  护甲基线 19 例（`--surface armor`）。

## 0.1.2 — 2026-09-13

技能只装给正在对话的那个 Agent。

此前 `install_skill.py` 会把技能铺给本机探测到的每一个宿主（Codex、Claude、Cursor 等），
对新用户来说是错的：他在某个 Agent 里提问，只需要那个 Agent 用上这套 MCP。

- 默认只装当前宿主，靠环境变量识别：`DSH_HOME`/`DSH_SHELL` 对应 dsh、`CLAUDECODE` 对应 claude、
  `CODEX_*` 对应 codex；识别不出来才退回旧行为。`--all` 保留全装，`--host <name>` 显式指定，
  `--list` 会标出当前宿主。
- 新增 DeepSeek Harness 宿主支持：技能根 `~/.dsh/skills/`（DSH 热发现，装完不用重启），
  全局指令文件 `~/.dsh/AGENTS.md`。
- 新增 `--mcp`：注册 MCP 服务器。DSH 直接幂等写入 `$DSH_HOME/profiles/web/cordis.patch.yml`
  （带 `destiny2-mcp:mcp-begin/end` 标记，先备份、重复运行只更新），工具以 `mcp__destiny__*` 出现；
  Claude Code 与 Codex 只打印可直接粘贴的 `claude mcp add` / `codex mcp add` 命令，不改它们的配置。
- README、AGENTS.md、安装 Skill 把「装给提问的 Agent」写成显式规则，并补上 DSH 的注册步骤。

首次安装的耗时预期也写进 README：`pip install -e .` 约 5 分钟（下载依赖，无进度条），
预构建 Manifest 685 MB 约 5 分钟。

## 0.1.1 — 2026-09-13

修 P0：干净环境安装后无法启动。

- 原因：`pyproject.toml` 只写了 `mcp[cli]>=1.27.2`，从零安装会解析到 mcp 2.2.0；2.x 把
  `mcp.server.fastmcp` 改名为 `MCPServer`，服务在 import 阶段即失败，自检报
  `VERIFY_FAILED=MCPError: Connection closed`。开发机装着 1.x，本地测不出来。
- 改为 `mcp[cli]>=1.27.2,<2`，并新增 `tests/test_dependency_bounds.py`（上界、lock 钉住 1.x、
  代码确实使用 v1 API，三条一起才算完整约束）。
- 验证方式：从 GitHub 克隆到干净目录，建立 venv 并 `pip install -e .`，下载预构建 Manifest，
  完成 OAuth 登录，`verify_mcp.py` 全绿（8 工具、`BUNGIE_PROFILE_CHECK=ok`），12 项真机冒烟
  全部符合文档。此时安装到的是 mcp 1.30.0。

`v0.1.0` 的 tag 停留在修复前，请使用 `v0.1.1`。

## 0.1.0 — 2026-09-13

第一次公开快照：本地运行的 Destiny 2 MCP 服务器，通过 8 个面向自然语言的聚合工具
（`player_assistant` / `inventory_assistant` / `weapon_assistant` / `build_assistant` /
`loadout_assistant` / `subclass_assistant` / `activity_assistant` / `world_assistant`，
共 108 个 intent）读取 Bungie 账号、Manifest 定义与本地社区资料。

能做什么

- 以只读为主：角色概况、模糊找人、仓库与背包检索、武器词条与 god roll 标注、护甲词条反推、
  商人货架、单场结算、收藏品解锁状态、官方配装与本地配装读取。
- 写入类操作（转移、装备、改模组、存配装）一律先返回确认请求，`confirmed=false` 时不触碰账号；
  装备配装还要求传回服务端签发的 `canonical_build`，自拼 hash 会被拒绝。
- 证据分层：账号数据、Manifest 定义、社区资料三类来源在响应里分开放，缺数据就说缺数据，
  不把没扫完答成你没有。

安装与运行

- Python 3.12+；`pip install -e .`；自带 OAuth 登录助手与 MCP 自检脚本。
- 首次启动需要约 717 MB Manifest（可先取 `manifest-data-v1` 预构建库）。
- 只暴露 8 个聚合工具；69 个历史工具需要 `DESTINY_MCP_ENABLE_LEGACY_TOOLS=1` 才出现。

为公开发布做的准备

- 修正 README 推荐的启动方式：`python -m destiny_mcp.server` 会让配装求解的子进程起不来，
  改为 `python -m destiny_mcp`。
- 自检脚本不再误报：`DESTINY_OAUTH_REDIRECT_URI` 未写进 `.env` 时按运行时默认值判定；
  缺 Manifest 时自动把超时放宽到 1800 秒并打印原因；venv 路径与 token 权限检查按平台分支
  （Windows 不再必然失败）。
- 登录助手缺 `openssl` 时给出提示并指向 `--manual`，不再直接抛出 traceback。
- `equip_build` 传错 `canonical_build` 时改为中文说明（缺哪些字段、应先运行哪个 intent）。
- 护甲模组筛选补 `match` 口径：词表外的词只在名字或描述里命中时标 `kind="keyword"` 并给 warning；
  词表内 0 条时说明本地数据里没有。
- 补 `LICENSE`（MIT）与 `pyproject` 的 license 元数据；README 增加「前置条件与已知限制」，
  写明平台支持、审计日志落盘位置（`~/.destiny_mcp/audit/`，明文、不上传）、
  跑测试需要 `pip install -e ".[dev]"`。
- 删除长期失效的 `Dockerfile`（引用了不存在的 `src/`）；补 `tests/conftest.py`，
  干净克隆（没有 `.env`）也能跑全量测试。

已知限制见 README「前置条件与已知限制」与 `docs/testing/TESTING_CORPUS.md` 的「已知问题」。
