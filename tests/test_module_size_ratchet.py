"""防止上帝类重新长回来。

这些模块已经是各自领域里最大的一个，继续往里加就是原来那种堆积。
这里的数字是「当前长度」，即上限：拆分后要跟着调低，确需放宽时改这里的数字，
改动本身会在 diff 里显式暴露出来，不会被顺手带过。

判断标准不是行数本身，而是新增代码该不该落在这个文件里。行数只是让这件事
变成一次有意识的决定。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]

CEILINGS = {
    # 性能"顺带缓存"：目录查询结果缓存登记进"重载要清"的清单，清缓存那段也改成同一张
    # 清单（少一个"新加缓存忘了清"的漏）→ 净增 0 行，262 不动。
    # 性能六项之外的又一次"顺带缓存"：护甲模组那次**全表扫**的结果也登记进同一张清单
    # （真机 5.8 秒/次，护甲快照每次都要），仍然净增 0 行 —— 清缓存那一项并进上一行，
    # 类 docstring 压掉一行冗长的话。缓存本体与理由写在 manifest_armor。
    "destiny_mcp/manifest.py":           262,
    # 参数说明改用 `from . import _param_docs as fields`，新增别名不再让 import 块
    # 长胖；给全部参数补说明时反而比上次短了 5 行。
    # +9：vendor 分支改走菜单/详情两种形态，并把 limit/next_actions/warnings 接出去；
    # 分类、排名、截断规则在 services/vendor_menu.py，刷取清单挂载在 tools/_farming.py，
    # 把后者整个搬出去净减约 46 行。
    # +5：world_assistant 的 limit 默认值从 12 改成 None（显式传 12 曾被当成"没传"）
    # 并说明各 intent 的默认条数从哪来。
    # +24：同一条规则推广到全部 18 个"有含义的默认值"参数（limit/locked/tracked/
    # slot_number/kind/baseline/top_n/count/maxtop/…），每个参数一行"None 就补默认值"。
    # 判断点没有增加，只是把默认值从签名搬进函数体；规则与验收在
    # tests/test_parameter_sentinels.py。
    # +6：popularity 组先确认武器存在（避免"打错名字"被答成"暂无快照"），
    # loadout 列表补 total/returned 以便自证全量。
    # +16：库存类型查询补 total/returned（自证全量）；community 指定 build id 时
    # 用新 payload 收掉搜索分页结构（不改服务返回的字典，避免改到调用方对象）。
    # P4：武器分支搬去 tools/_weapon_branches.py，只剩认参数与分发 —— 上限跟着收紧
    # （收紧后旧上限就不再是"可以慢慢长回去"的许可）。
    # +7：find 分支恢复可用 + 按类型列武器的默认上限 20。
    # +6：armor_mods 分支改取 get_armor_mods_filtered 并把 match/warning 带出去
    # （判断逻辑在 services/manifest_armor.py，这里只是多接一层返回值）。
    # PvP 武器榜：榜单三兄弟搬去 `_leaderboard_branches.py`、武器两个口径搬去
    # `_weapon_usage_branches.py`（分支响应按域放 `_*_branches.py` 是既定分工），
    # 这里净减 2 行 → 上限跟着收紧，旧上限不再是"可以慢慢长回去"的许可。
    # 条数类参数的哨兵规则收敛到 `_helpers.positive_or_default`：`if limit is None:`
    # 的壳去掉后 1401 → 1399（那些壳本身就让 0 走不进规则里）。
    # 性能六项：搜人分支搬去 `tools/_player_branches.py`（它有错误映射、两条话术与
    # 响应封装，本来就该按域放 `_*_branches.py`）→ 1407 → 1364，上限跟着收紧。
    # 1364 → 1319：equip_build 分支搬去 `tools/_armor_branches.py`（它旁边就是
    # equip_preview），并把"标量宿主也能装备"的两种回传形态收在那里。
    # 1319 → 1263：金装解析/确认整段搬去 `tools/_build_confirmation.py`（与确认凭据同一件事），
    # 且"唯一精确匹配不再多问一轮"（ADR-017）。上限跟着收紧到当前长度。
    # 1263 → 1217：`loadout_assistant` 的清单/详情分支搬去 `tools/_loadout_branches.py`
    # （list 只给清单行、get 才给完整模板），`_dump` 也搬去 `_responses.dump` 共用。
    # 1217 → 1206：写入类信封（原本地 `_action_response`）与配装写入的确认载荷也各归各家
    # —— 前者去 `_responses.action_response`，后者去 `_loadout_branches.confirm_write`。
    # 1206 → 1208（0.7.9）：`build_assistant` 多一个入参 `functional_mods`（照抄社区配装的功能模组）
    # —— 签名一行 + 两处分派各多一个实参。真正的解析与执行都不在这儿。
    # 1208 → 1218（0.7.14）：`duplicates` 的下一步与判据（先看整栏 options 再定级）+ 默认条数 5。
    "destiny_mcp/tools/assistants.py": 1218,
    "destiny_mcp/build/farm_target.py": 1296,
    # +44：上游 HTTP 错误统一映射（以前只有 503 被翻译，4xx 裸抛到 MCP 客户端）。
    # P1：错误映射整段搬去 `bungie_errors.py`，活动统计端点搬去 `bungie_stats.py`（客户端只留
    # 门面方法）→ 1263 → 1164，上限跟着收紧：腾出来的位置已经用掉了（账号级 + 按模式统计）。
    # 2026-10-06：官方配装槽那一族搬去 `bungie_loadouts.py`（只有门面留下），1164 → 1094，
    # 上限跟着**下调**到 1120 —— 这次腾出来的位置留一点给门面签名，不预支给别的域。
    "destiny_mcp/bungie_client.py": 1120,
    # 2026-10-06 新增：官方配装槽端点族（读/应用/快照/改标识/清空）+ 那一族的真机结论。
    # 登记即上限：里面大半是"为什么只能这么做"的注释，代码本身很短。
    # 同日 192 → 180：把四动作表与标识表**搬去 `docs/reference/bungie_api.md` 第十七节**
    # （那是唯一出处），模块里只留指针 —— 抄第二份就会两边走样，位置也腾出来了。
    # 同日 180 → 170：`equip_loadout` 改成走 `_post_action`（信封归一只此一处），
    # 自己那套 try/except 与三个 import 一起删掉 —— 位置跟着收紧。
    "destiny_mcp/bungie_loadouts.py": 170,
    # P7：结果翻译层（ProcessArmorSet → BuildResult/canonical_build + 目标统计）
    # 整个搬到 services/build_results.py，1114 → 906，上限跟着收紧 ——
    # 调谐（tuning）的对外字段也落在那边的翻译层里，不再往这里堆。
    # 902 → 901：函数内那行冗余的 `from ..manifest import …`（顶层已经导入了同样两个
    # 名字）换成组件号单一出处 `from . import profile_components` —— 修 F821 的同时不涨行数。
    # 901 → 875：候选暂存（execution_id → 签发方案、TTL、玩家绑定、用完即焚）
    # 整段搬去 `services/build_candidates.py` —— 它与求解流程无关，而且"只给候选 ID
    # 也能装备"要在这里按 ID 取回签发的那份。上限跟着收紧。
    # 875 → 880：`analyze_build` 改走"六项单项上限并发探测"（另起 `_probe_compute`，
    # 档位 4，只读）；为此把 `_canonical_subclass`（25 行）搬去 `services/build_results.py`
    # —— 净增 5 行，上限跟着定在当前长度。
    # 880 → 889（0.7.9）：照抄来的功能模组是**求解的一项输入**（它占掉的能量必须先扣掉），
    # 工具层 → 快照的透传（含标量写法归一）就地加在这里。要再压请先拆碎片属性那两块
    # （`_get_fragment_stats_by_names` 42 行 + `_replace_fragment_config` 58 行）。
    # 889 → 833（2026-10-03）：按上一行的指引拆了 `_replace_fragment_config`
    # （→ `services/build_fragments.py`）—— 碎片 hash 两套值域没归一的修复要在那里写清
    # 为什么，这里已经没有位置；那块本来也不属于求解流程。上限跟着收紧。
    # 833 → 808（① 执行前提，2026-10-03）：执行前提（格子满搬不进来 / 与角色正穿着的金装
    # 冲突）的原因要接进 find 的诊断与 analyze 的结论；腾位置把 `_snapshot_version`
    # 拆去 `build/snapshot_version.py`（它是"求解 ↔ 执行"的一致性契约，不是这个类的内部细节）。
    # 808 → 788（② 执行前复检）：写账号前最后那段只读闸（重取现场 → 执行前提复检 →
    # 指纹比对 → 实例核对）整段搬去 `services/build_execution_guard.py`，`equip_build`
    # 只留一次调用 —— 那段本来就是"用户确认 → 真写账号"之间独立的四步，混在签发流程里
    # 读不出顺序。上限跟着收紧，腾出来的位置已经用掉了（一行注释 + 调用点）。
    # 788 → 787：`equip_build` 那一族候选拒绝码收进 `ErrorCode`（+1 行 import），同一趟把
    # `equip_by_score` 的 logger 实参收成一行（-2）—— 净减 1，上限跟着收紧。别把这行抬回来：
    # 它多出来的是"码的第二个出处"，不是新功能。
    "destiny_mcp/services/build_service.py": 787,
    # 2026-10-03 新增（从 build_service 拆出）：碎片替换的兼容预检。拆出来的直接原因：
    # 上面那块贴在 889 没有位置放"为什么"，而它是真机事故（碎片有符号/无符号没归一，
    # 5 颗里 4 颗被判"与插槽不兼容"）的修复落点。登记即上限。
    # 82 → 95（① 禁用槽）：差异报告（"能写几颗/收到几颗、哪些槽空着/被禁用"）
    # 整段搬去 `fragment_sockets.py`，这里只剩替换本身。
    "destiny_mcp/services/build_fragments.py": 95,
    # 2026-10-03 新增（从 build_fragments 拆出，那边贴着 82）：碎片槽现场的**判据 + 报错清单**。
    # 抽出来的直接原因有二：那边没有位置放"哪些槽算碎片槽"的判据链（真机 socket 14 被禁用、
    # 第 6 颗碎片排进 14 → 上游 500 DestinySocketActionNotAllowed）；而"数几颗"与"报哪些槽"
    # 必须是同一份判据 —— 各写一份就会漂移（清单里混进超能/星象槽，条数却只有碎片那一档）。
    # 113 = 92 + ② 差异报告的两档措辞（"禁用"与"状态未知"必须分开说，混起来就是把
    # 已知当成未知）+ 报错清单与数数共用 `is_assignable_fragment_slot`（注入一次
    # "清单不看现场状态"就复现了漂移：能写 5 颗、清单列 6 个槽）。
    "destiny_mcp/services/fragment_sockets.py": 113,
    # 794 → 795：P3 收拢组件号，多一行 `from . import profile_components`；
    # 三处裸组件字面量换成命名集合没有增行，这一行就是净增量。
    # 抽走 _capture_recovery_state（→ loadout_recovery.py）后下调：上限只能降不能升
    # 520 → 519：`equip_loadout` 补上回读核对（同一条纪律，与 equip_with_recovery 一致）。
    # 519 → 521（0.7.9）：模组预检的入口条件要认"只有照抄模组"的件（+1），以及多继承一个
    # `FunctionalModMixin`（+1）。决策逻辑本身在 `loadout_functional_mods.py`。
    # 521 → 469 → 354（2026-10-03）：两段搬走 —— 回读核对（`_verify_loadout`）去
    # `loadout_verify.py`（它同时被 Step 4 与 `equip_with_recovery` 用，而"核对"与"执行"是两件事）；
    # 精确执行那三个方法（应用 → 核对 → 判回滚）去 `loadout_exact_flow.py`（"要不要回滚"是
    # 一个判断，散在两处就会出现真机那两类事故）。模组预检前置、话术修正、以及
    # `equip_loadout` 改走同一条恢复路径、以及三条话术修正（腾能量点名换下的那颗、
    # 清能量失败也带上游原文、子职业原因写进回执）都落在这 370 里。
    # 上限**跟着收紧**，腾出来的位置才是真腾出来。
    # 370 → 364（2026-10-03 回读那一趟的单一出处）：Step 4 从"自己开窗口 + 自己措辞"改成
    # 两次调用 —— 窗口在 `write_readback`、判据在 `loadout_matches`、结论与话术在
    # `loadout_verify.readback_verdict`。真机 audit 123435：外层自己又读了一整轮（约 78 秒），
    # 与内侧读的是同一份状态、同一个函数，结论不可能变。
    "destiny_mcp/services/loadout_equipment_service.py": 364,
    "destiny_mcp/services/starside_service.py": 769,
    # 新增登记（PvP 武器榜）：不登记就等于没有闸 —— 仓库的规矩是"要在这里加代码
    # 得先做一次有意识的决定"，而不是等它长成下一个上帝模块。当前 529 行即上限。
    # 拆分后登记：PGCR 缓存整段抽去 `services/pgcr_cache.py`（541 → 461），
    # 上限按拆分后的长度收紧 —— 缓存与聚合本来就是两个问题。
    # 性能六项：PGCR 数值解析与时间窗计算搬去 `services/pgcr_values.py`（与榜单逻辑无关）
    # → 468 → 434，上限跟着收紧。
    # 434 → 429（2026-10-04）：两张身份表（按模式、按活动）搬去 `pvp_match_tally.py`，
    # 榜单本体这边只留一行调用 —— 腾出的位置放"活动行"这个新块。
    "destiny_mcp/services/pvp_weapon_service.py": 429,
    # 2026-10-04 新增：PvP 榜的两张身份表（模式名 + 活动名/图）。登记即上限：
    # 它们要跟响应一起冻结，所以"再加一张表"得先回答"放这儿还是另起一处"。
    "destiny_mcp/services/pvp_match_tally.py": 58,
    "destiny_mcp/services/pgcr_values.py": 49,
    "destiny_mcp/services/pgcr_cache.py": 106,
    # P4 新增：武器分支载荷与形状工厂。定在上限处是为了让"再加一个 intent"
    # 必须先回答"是搬出去还是抬上限"，而不是悄悄长胖。
    # P5：本地资料挂载 + 固定/随机话术 + 覆盖表，各分支都要交代自己带哪些块（444 → 454）。
    # 再往上就该把"覆盖表 + 挂载"抽出去，而不是继续在这里加 intent。
    # 性能第四项：`inventory_assistant(intent="type")` 的载荷搬去 `_inventory_branches.py`
    # ——它本来就不是武器 intent（不读账号武器服务），一直占着这个文件的位置；
    # `type` 改成列表行后上限跟着收紧到 412。
    # 412 → 435（0.7.13）：`compare` 多一条分支 —— 不带 `item_instance_id` 时给行视图，
    # 带 ID 时保持单副本明细（同一 intent 的两种读法，分支必须在这层）。
    "destiny_mcp/tools/_weapon_branches.py": 435,
    # 性能第四项新增登记：从 `weapon_payload.py`（上帝模块，加了闸）拆出的属性值形状。
    # 登记即上限：以后再往里加东西要先回答"是搬出去还是抬上限"。
    "destiny_mcp/services/weapon_stats_payload.py": 92,
    # 同上：`inventory_assistant` 的分支载荷（从 `_weapon_branches.py` 搬出来）。
    "destiny_mcp/tools/_inventory_branches.py": 76,
    # P6：体积口径（定义级不带描述/图标）写在工厂里，349 → 363。
    # +2（4a）：gear_tier=0 → null 时给一句说明。
    # 0.1.12：`with_equipped`（定义级 sockets 标"现在装的是哪个"）搬去
    # `weapon_profile.py` —— 那里才是插槽助手的老家，也顺便给"强化版名标 ↑"腾出空间。
    # 性能第四项：属性值形状拆去 `weapon_stats_payload.py`、删掉两个 0 引用的空壳
    # （`roll_summary`/`instance_extras`），腾出位置放 `list_row` → 上限 334 → 297。
    # 297 → 310（0.7.14）：身份块带 `name_variants`（同名多版本如实报，P0 修复）。
    "destiny_mcp/services/weapon_payload.py": 313,
    # P5 新增：本地资料汇总（愿单/选取率/清单/社区 → 每个 plug 的 recommended）
    # 403 → 425（2026-09-28，真机「千码凝视」同名两版）：`_cross_check` 不再对"同名多版本"
    # 断言 `in_manifest_pool=false`。+22 的去处逐块可数：docstring 写清"为什么不能据此说滚不到"
    # （+8）、`name_variant_count` 判歧义（+3）、逐颗从一次性 dict 改成可变 dict 并补
    # `in_manifest_pool=null` / `perk_pool_check` / `perk_pool_check_reason`（+9）、`note` 补
    # null 的语义（+2）。这就是那次修复的全部，没有顺带重构。
    # 425 → 427 是**错的**：那 +2 出自 0be0c1b（突袭报表，没碰过这个文件），而且当时这次修复
    # 还只在工作区里 —— 它是凭余量写的数，不是量出来的。上限按实际长度收回 425。
    "destiny_mcp/services/weapon_local_data.py": 425,
    # 锻造图样查询新增登记（登记即上限）：目录（展示树 + 记录 + 目标需求）+ 账号进度
    # （组件 900 的**档案级与角色级两份**）+ 来源装配 + 变体的塑形配置都在这里。
    # 463 = 427 + 角色级记录合并（真机事故：32 条角色级记录被漏读）+ read 块收敛。
    # 488 = 463 + 稀有度筛选与 by_tier 汇总（真机事故：只读第一页把 16 把金枪报成 2 把）。
    # 再往里加就该拆"目录/账号状态"，而不是抬上限。
    # 465（2026-10-04 图标出口）：图样行补 `icon_url` 时把"组件 900 一格怎么并"
    # （`cell`/`merge_cell`，纯合并规则、不碰账号与 Manifest）拆去
    # `services/pattern_records.py` —— 上限跟着收紧，不是抬。
    "destiny_mcp/services/pattern_service.py": 465,
    # 同上：Starside「锻造武器来源」的名字索引（归一化 / 表格 / 正文兜底 / 出处）。
    # 名字归一化只此一处，别再抄到 pattern_service 或工具层。
    "destiny_mcp/services/starside_crafting_sources.py": 186,
    # 周常轮换新增登记（登记即上限）：周期表 + 锚点 + 六类轮换的取值规则（纯计算）。
    # 官方接口只给突袭/地牢（里程碑）与夜幕/宗师（组件 204），其余靠这张表 —— 见 ADR-010。
    # 遗失区域那 27 个地点按目的地分组写在这里（用户截图的「World Lost Sector」专家列表）。
    "destiny_mcp/data/rotations.py": 166,
    # 同上：把官方那半（里程碑 + 组件 204）与表那半拼起来，并给每行标 source。
    # 300（2026-10-04 图标出口）：周常奖励行补 `icon_url`，同时把"自维护表那半"的
    # 遗失区域块拆去 `services/rotation_tables.py`（不碰账号、不碰 Bungie）。
    # 300 → 285：周期表本身（`_tables_block`）搬去 `rotation_tables.tables_block()` ——
    # 那一块只读 `data/rotations.py`，与"取数"无关；腾出的位置正好放活动行的图标。
    # 285 → 265（2026-10-05 第六轮）：自维护表那半的**每一行**（`_schedule_rows`）也搬去
    # `rotation_tables.schedule_rows(now)`（同上：只读表、不碰账号），腾出的位置放
    # **轮换行头**的 `activity_hash`/`icon_url`（行头与 `activities[]` 取同一条活动行）。
    "destiny_mcp/services/rotation_service.py": 265,
    # 遗失区域块拆去 `services/rotation_tables.py`（不碰账号、不碰 Bungie）。
    # 61 = 遗失区域块 + 周期表本身（2026-10-04 从服务搬来）：两块都是"表的只读渲染"。
    # 61 → 107（2026-10-05）：自维护表那半的**行**（`schedule_rows`）也并进来 ——
    # 这一块与"表怎么算"是同一件事，分在两个文件里反而要把 `data/rotations.py` 的用法读两遍。
    "destiny_mcp/services/rotation_tables.py": 107,
    # 同上：`intent="rotations"` 的载荷与话术（口径分离 / 未锚点说明 / {var:} 提醒）。
    "destiny_mcp/tools/_rotation_branches.py": 111,
    # 只发标量的宿主（豆包 connector）把结构化参数写成文本时的还原：登记表 + 一次性还原。
    # 分隔符规则不在这儿，在 `destiny_mcp/utils/arg_text.py`（工具层与服务层共用一份）。
    "destiny_mcp/tools/_coerce.py": 118,
    # 候选暂存（从 build_service 拆出）：登记即上限 —— 再往里加东西先回答"是不是该拆状态与话术"。
    # 101 → 126：`get_build_candidate` 的判定与话术（expired/unknown 两种下一步）搬了进来，
    # 与暂存同一处；换出来的是 build_service 的行数（那边要放"阶梯试解走并发档"）。
    # 126 → 125（错误码收口）：两个候选拒绝码改用 `ErrorCode`（+1 行 import），
    # `describe_candidate` 的签名收成一行（-2）—— 上限跟着收紧。
    "destiny_mcp/services/build_candidates.py": 125,
    # 2026-10-06 新增（ADR-025）：候选 ID 的**取回与四个状态的话术**（expired / consumed /
    # unknown / player_mismatch）从 `build_candidates` 与 `equip_build` 两处收拢到这里 ——
    # 这两处以前各写一份，措辞已经漂了（真机把"用过了"报成了"不认识"）。
    "destiny_mcp/services/candidate_messages.py": 66,
    # 2026-10-06 新增：无解阶梯的**口径**（纯文字与判据之外的分类）。抽出来是因为
    # `tools/_armor_ladder.py` 正好贴着 506 上限。
    # 62 → 120（同日第二轮）：它接着吸收了 `verdict` 与 `single_stat_ceiling` 两段口径，
    # 并补上"执行前提砍光"专用的那句话 —— 换出来的是 tools 那边的行数（519 → 502）。
    "destiny_mcp/build/ladder_evidence.py": 120,
    # 同上：`intent="patterns"` 的载荷与话术（总览 / 单把 / 变体 / 术语对照 /「未开始」措辞）。
    # 211 是加上"玩家说红框、游戏说模式"的术语块与变体话术（含强化插槽）之后的长度；
    # 215 = 211 + 载荷里的 by_tier 汇总。
    # 200（2026-10-04 图标出口）：图样行补 `icon_url`，同时把来源覆盖块
    # （`crafting_sources_block`）搬去 `tools/_farming.py`（那边本来就是刷取/来源清单的家）。
    "destiny_mcp/tools/_patterns_branches.py": 200,
    # —— 2026-09-24 补登记：这几块都是**拆出来的产物**（assistants / loadout_equipment_service
    # 的上限一路下调，靠的就是把代码挪进它们）。拆出来的模块不登记，等于给上限开了后门：
    # 往 `_loadout_branches.py` 堆代码时 `assistants.py` 仍然"达标"，总量却在涨
    # （这一轮它就从 101 涨到 151）。登记即上限，再往里加先回答"是不是该拆"。
    # `loadout_assistant` 的清单/详情/确认三个分支（list 只给清单行、get 才给完整模板）。
    "destiny_mcp/tools/_loadout_branches.py": 151,
    # 金装解析/确认/一次性凭据（从 assistants 拆出，ADR-017）。
    "destiny_mcp/tools/_build_confirmation.py": 230,
    # 护甲阶梯试解（从 _armor_branches 拆出）：探针并发 + 逐步收窄的话术。
    "destiny_mcp/tools/_armor_ladder.py": 506,
    # 响应信封的唯一住处（ok/error/confirmation/disambiguation/failure + `dump`）。
    # 186 → 180：失败话术表拆去 `_write_failure_hints.py`（形状与措辞分开）。
    "destiny_mcp/tools/_responses.py": 180,
    # 上游原文关键词 → 下一步话术（会随实测加条目，所以单独登记，别挤回信封模块）。
    "destiny_mcp/tools/_write_failure_hints.py": 43,
    # 执行前状态快照与回滚（从 loadout_equipment_service 拆出）。
    # 385 → 317：三块搬走 —— 回滚核对（`_verify_restored_items`）去 `loadout_verify.py`
    # （它和 `verify_loadout` 是同一件事：拿一份 profile 逐槽比，且共用"哪些实例在身上"）；
    # 位置还原去 `loadout_restore_locations.py`（"这一件装着什么"与"这一件在哪"是两件事）。
    # 上限跟着收紧。
    "destiny_mcp/services/loadout_recovery.py": 317,
    # 模组插槽读写（三条写入路径共用 `plug_already_installed`）。
    # 562 → 518：能量腾挪那段搬去 `loadout_energy_budget.py`（预算是一道算术 + 挑选规则，
    # 与"这颗该进哪个槽"是两件事），腾出来的位置给照抄模组的调用点。
    # 518 → 519：这次把"预检两趟"（`_mod_write_snapshot` / `_mod_preflight`）整段搬去
    # `loadout_mod_preflight.py`（守门与规划语义相反，混一处最容易让规划复用守门的过期快照），
    # 净剩 2 行：多继承一个 `ModPreflightMixin` 与那一行 import。
    "destiny_mcp/services/loadout_mod_sockets.py": 519,
    # 2026-10-03 新增（从 loadout_equipment_service 拆出）：回读核对 —— 写入之后账号上到底是
    # 不是要的那套。单独成模块的直接原因有二：那边贴着 521 没有位置；而"核对"与"执行"本来
    # 就是两件事（`equip_with_recovery` 的外层与 `_equip_local_unlocked` 的 Step 4 共用同一份判据，
    # 各自写一份就会各自漂移）。注册即上限。
    # 159 → 153：判据（逐槽比 / 集合比 / 子职业那一半）搬去 `loadout_matches.py`（见下），
    # 这里留下两个**入口**（装备这一趟、回滚那一趟）与装备这一趟的结论话术
    # `readback_verdict`（窗口仍在 `write_readback`）；腾出来的位置放了"核对自己炸了"那两行
    # 留痕（回执 + 服务器日志）。上限跟着收紧。
    "destiny_mcp/services/loadout_verify.py": 153,
    # 2026-10-03 新增（从 loadout_verify 拆出，那边贴着 159）：**判据本身** —— 组件 305 报的
    # 插槽与"要的那一套"逐项比（逐槽位 + 集合 + 子职业本体/技能/星象/碎片）。
    # 拆出来的直接原因是 2026-10-03 真机那 147.8 秒的根因就落在这里：hash 两边值域没归一，
    # 比对**恒为 False**（社区模板给有符号的 `回天掌法` = -1847517590，账号上是 2447449706），
    # 于是"装好了"被报成"对不上"。判据与入口分开之后，`tests/test_hash_domains.py` 的横切
    # 扫描也认得出这里的归一写法（那三条 `to_unsigned(...) != to_unsigned(...)`）。
    # 注册即上限。
    "destiny_mcp/services/loadout_matches.py": 109,
    # 2026-10-03 新增（① 预检判死的模组也要让回读提前收手）：`loadout_equipment_service` 贴着
    # 364 没有位置，而"写不成的模组算不算、分哪两类、回读还核不核"本来就是**一个判断**：
    # 判据（`OPERATION_KINDS` 那张表）、两本账、以及它们各自的话术（聚合步骤 / 回执尾句）
    # 都只有这一处。登记即上限。
    "destiny_mcp/services/loadout_blocked_mods.py": 117,
    # 2026-10-03 新增（从 loadout_mod_sockets 拆出，那边贴着 518）：模组写入的守门那一趟。
    # 单独成模块的原因：守门（换装**前**的现场）与规划（换装后的现场）语义相反，混在一处
    # 最容易发生的就是"规划顺手复用了守门那份过期快照"。注册即上限。
    "destiny_mcp/services/loadout_mod_preflight.py": 86,
    # 2026-10-03 新增（从 loadout_equipment_service 拆出，那边贴着上限）：`equip_build` 的
    # 精确执行 —— 应用 → 回读核对 → **判要不要回滚**。"要不要回滚"是一个判断：它以前散在
    # `_equip_local_unlocked` 的返回分支与它自己的外层之间，于是出了真机那两类事故
    # （模组被挡就提前 return，子职业整段没跑；外侧只读一次就把"没确认"判成失败，整条白回滚）。
    # 197 = 184 + `equip_loadout` 也走这一条（同一动作两种后果，只因为入口不同）。
    # 197 → 175：外层那次"自己再读一遍"整段删掉（它是 2026-10-03 那 147.8 秒里的第二轮，
    # 与内侧同一判据、同一窗口，结论不可能变），只留"读内侧的结论 + 判要不要回滚"；
    # 省下的位置换成了那三条不许回滚的**真实控制流**说明（第三种"没确认"已经不在那条路上）。
    # 上限跟着收紧。
    "destiny_mcp/services/loadout_exact_flow.py": 175,
    # 2026-10-03 新增（从 loadout_recovery 拆出）：回滚的位置那一半（放回原位 + 原先穿着的
    # 再穿回去）。与"这一件装着什么"分开的理由见模块 docstring。注册即上限。
    "destiny_mcp/services/loadout_restore_locations.py": 89,
    # 0.7.10：`find`/`recommend` 的候选行投影（默认出口只给行 + execution_id）。
    # 366 → 284（① 执行前提）：0 候选要把"装不上"与"配不出来"分开说，这里已经满了 ——
    # 候选行投影（118 行）搬去 `services/build_projection.py`（形状工厂该在的层，
    # 与 `weapon_analysis_projection` 同一分工），上限跟着收紧。
    "destiny_mcp/tools/_build_flow.py": 284,
    # 0.7.11：武器分析的投影（池子只给愿单有结论的项、副本去掉可换项）。
    # 108 → 166（0.7.13）：同名多副本的**行视图**（`compare_rows`）也搬进来 —— 它和 analyze
    # 的投影是同一件事（把武器域的结果投影成"行 + 判定依据"），放一起比再开一个文件清楚。
    # 173 → 184（0.7.14）：roll 定义栏固定也照列（`fixed` 标记）——藏掉会被读成"没有这一栏"。
    # 184 → 179（2026-10-05）：副本行视图补 `icon_url` 时这里已经 184/184 顶死，
    # 按"超限先抽代码"把两条投影**共用**的 perk 选项行形状
    # （`option_row`/`recommended`/`OPTION_KEYS`）抽去 `services/weapon_option_rows.py`，
    # 上限跟着收紧（不是抬）。同一件事的另一半在上面 L290 的注释里：`compare_rows` 本就该留在这里。
    "destiny_mcp/services/weapon_analysis_projection.py": 179,
    # 2026-10-05 新增：perk 选项行的**唯一形状**（`analyze` 的插槽行与 `compare` 的副本行共用）。
    # 抽出来的直接原因是上面那条（184 顶死），根本原因是"同一个形状写两份会漂"——
    # 两处对模型是同一个问题（"这一栏能换成什么"）。登记即上限。
    "destiny_mcp/services/weapon_option_rows.py": 44,
    # 0.7.10：重复武器的行视图（`duplicate_rows`；perk 从对象压成 {name, slot}）。
    # 0.7.12：按**类别**把插槽拆成 perks / mods / masterwork（"空模组插槽"不再是 perk）。
    "destiny_mcp/services/inventory_analysis_service.py": 654,
    # 0.7.9 新增：社区配装的功能模组（名字 → 部位/版本/能量；不进求解器，只算它占多少能量）。
    "destiny_mcp/build/functional_mods.py": 159,
    # 2026-09-28 新增：职业金装（相对主义/唯我主义/坚忍克己）roll 到的两个异域特性。
    # 单独成模块的原因：armor_payload 贴着上限，而这读的是"插槽里装着哪颗异域 plug"，
    # 与"六维怎么算"是两件事。登记即上限：再往里加东西先回答"是不是该拆"。
    "destiny_mcp/services/armor_class_item.py": 195,
    # 社区模板的"异域护甲=两个特性名"这一支接进 starside_matching（2026-09-28）。
    # 860：接上"按组件 305 真核对职业金装特性"（2026-09-28，原为 not_checked）。
    "destiny_mcp/services/starside_matching.py": 860,
    # 2026-10-03 登记（② 一次读回已装备护甲的插槽）：这个文件已经是"账号读取"里最大的一块，
    # 再加读取入口先回答"是拆出去还是复用"（读回那一段本身只有 70 行，形状工厂在 armor_payload）。
    # 762（2026-10-04 图标出口）：五件护甲补 `icon_url`，同时把"在 profile 里定位一件实例"
    # （`locate_instance`，纯字典遍历、无服务状态）拆去 `services/inventory_lookup.py`。
    "destiny_mcp/services/inventory_service.py": 762,
    # 2026-10-03 登记（②）：护甲只读分支（`item` 单件 + `mods` 多件插槽）。
    "destiny_mcp/tools/_armor_branches.py": 589,
    # 2026-10-03 新增（从 `_build_flow` 拆出，① 执行前提腾位置）：`find`/`recommend` 的
    # 候选行投影。登记即上限 —— 再往里加东西先回答"是不是该拆"。
    # 120（2026-10-04 图标出口）：候选行补 `icon_url`（默认出口少了它，UI 那五件只能放色块），
    # 同时把 `tuning_rows` 搬去 `services/build_results.py`（与 `tuning_note` 同一件事）。
    "destiny_mcp/services/build_projection.py": 120,
    # 2026-10-03 新增（从 `build_service` 拆出）：求解输入指纹（`CanonicalBuild.snapshot_version`
    # 的重算口径）。登记即上限。
    "destiny_mcp/build/snapshot_version.py": 61,
    # 2026-10-03 新增（①）：求解阶段就要判死的两条执行前提（仓库件遇上满格、与角色正穿着的
    # 金装冲突）。单独成模块是因为它同时被求解器（滤件）、分析器（说话术）与规模闸门（数件数）
    # 读，放进任何一边都会变成两处判据。登记即上限。
    # 257 → 230（② 执行前复检）：判据与"这条约束的出路"留在这里（出路是判据的反面，
    # 写进件上的 `execution_blocker` 就是完整一句"哪条约束 + 怎么办"，确认那一刻的复检
    # 直接原句拿走）；**叙述**那半边（0 候选怎么解释、是哪个格满的）搬去
    # `build/execution_diagnosis.py`。上限跟着收紧。
    "destiny_mcp/build/execution_feasibility.py": 230,
    # 2026-10-03 新增（② 执行前复检，从 execution_feasibility 拆出）：执行前提的**叙述** ——
    # 格级汇总（"哪个格满了、仓库里几件搬不进来"）与"指定的金装跟身上那件冲突"的点名。
    # 拆出来的原因：判据那边贴着 257，而这几句会随真机反馈改（改措辞不该动判据那一侧）；
    # 件级的那句话不在这里 —— 确认复检直接引用 `execution_blocker` 原句，不重复叙述。
    "destiny_mcp/build/execution_diagnosis.py": 95,
    # 2026-10-03 新增（② 执行前复检，从 build_service 拆出）：写账号前最后一段只读闸。
    # 单独成模块的直接原因：那边贴着 808 没位置，而"重取现场 → 执行前提复检 → 指纹比对 →
    # 实例核对"是一段完整的四步，顺序本身就是结论（缺一条就是"以为在装备、实际撞上游 500"，
    # 真机两次各 0 颗模组落地）。登记即上限。
    # 101 → 99：三个候选拒绝码收进 `error_codes.ErrorCode`（局部常量与那行注释去掉、换成
    # 一行 import）—— 上限跟着收紧：腾出来的位置不该留着。
    "destiny_mcp/services/build_execution_guard.py": 99,
    # 2026-09-28 新增（从 loadout_mod_sockets / armor_mod_service 抽出）：调谐能不能写的判据。
    # 抽出来的直接原因：同一判据被两条写入路径各写一份，只改了一边 → equip_build 拿组件 207
    # 判调谐，把 3 颗能装的调谐误拦（连上游都没试）。登记即上限。
    "destiny_mcp/build/tuning_writes.py": 66,
    # 0.7.9 新增：照抄模组在执行时的现场决策（挑版本、插不进/装不下就跳过并点名）。
    "destiny_mcp/services/loadout_functional_mods.py": 127,
    # 0.7.9 新增（从 loadout_mod_sockets 拆出）：模组的能量预算与"拿谁去腾能量"。
    "destiny_mcp/services/loadout_energy_budget.py": 85,
    # 2026-10 新增（从 loadout_mod_sockets 拆出，那边贴着 518）：1676 的话术 —— 把 Manifest 的
    # 插入条件分成"候选"与"已被账号守护者等级证伪"，外加读守护者等级这一处（组件 100）。
    # 抽出来的直接原因：把条件清单念成原因会让用户**早就满足**的那条背锅（等级 11 被告知要 3 级）。
    "destiny_mcp/services/insertion_rule_diagnosis.py": 98,
    # 配装服务本体：清单/详情/存档/预览/官方槽都在这。真机走查连加了两块（`describe_save`
    # 与抽出的 `_read_equipment`），登记在 1030 就是"下次先想清楚放哪儿"。
    # 1030 → 986（2026-10-05 第六轮）：**清单行**整块搬去 `services/loadout_rows.py` ——
    # 清单行（每套一行 + 图块）与完整模板是两件事，清单行那边还要放"金装/子职业的图"。
    "destiny_mcp/services/loadout_service.py": 984,
    # 2026-10-06 新增（从 loadout_service 拆出）：官方槽**三个标识**怎么写才对 ——
    # 里面存着一条真机踩出来的上级事实（SnapshotLoadout / UpdateLoadoutIdentifiers
    # 三个标识必须都给，少一个就是 HTTP 500 DestinyInvalidRequest），
    # 所以它不该混回服务本体，也别被别处的体积增长挤掉。
    "destiny_mcp/services/loadout_official_identifiers.py": 100,
    # 2026-10-05 新增（从 loadout_service 拆出）：`intent="list"` 的清单行形状 ——
    # 只留"挑一套"要用的字段 + `visuals`（金装那一件、子职业那一行的图）。
    # 图**只从这份配装自己的 `build_template` 里取**（账号数据，不做名字→Manifest 解析）。
    "destiny_mcp/services/loadout_rows.py": 112,
    # 突袭报表新增登记（登记即上限）：表本体（副本 → 官方计数器 hash）+ 取数组装 + 分支话术。
    # 三块分开登记的理由：它们的上限不该互相借用 —— 表会随新副本长，服务与话术不该跟着长。
    # 表里每一行都有 `tests/test_raid_report.py` 对着 Manifest 核（hash 存在、描述里含副本名、
    # 且不是"本周/本赛季"变体），所以加行是"有意识的决定"，不是顺手抄一条。
    "destiny_mcp/data/raids.py": 163,
    # 269 → 268（2026-10-04）：活动道给每行加了 `activity_hash`/`icon_url`，同时把
    # `_int_or_none`/`_progress` 删掉 —— 组件 1100 的读法只有一处
    # （`activity_counters_service.metric_progress`），两份对字符串的判定本来就不同。
    "destiny_mcp/services/raid_report_service.py": 268,
    # 逐场索引（P4）：存从 PGCR 抠出来的原始事实 + 按副本分块扫描。两者同处一模块是因为
    # 扫描写的就是这里的格式，分开会让"字段名"有两个出处。
    "destiny_mcp/services/raid_runs.py": 319,
    "destiny_mcp/tools/_raid_report_branches.py": 129,
    "destiny_mcp/tools/_history_branches.py": 42,
    # 跨值域 hash 比较的静态守门（tests/ 里唯一登记的一条：它不是业务模块，但里面有
    # 半个静态分析器 —— 台账、判据、注入样本都在里面，长起来同样是"下一个上帝模块"的苗头，
    # 所以按同样的规矩登记在**当前长度**：再加规则先回答"是拆出去还是抬上限"）。
    # 1338 → 1328（2026-10-03）：回读核对那三条台账条目删掉（它们记的不是"判不准"而是
    # 恒为 False 的真 bug），改成 `test_normalization_is_positively_recognized` 里两条
    # 正面钉住的归一比较（台账跟着收紧，别让它继续挂着一个已经不存在的问题）。
    "tests/test_hash_domains.py": 1328,
}


def test_god_modules_do_not_grow() -> None:
    oversized = {}
    for relative, ceiling in CEILINGS.items():
        path = ROOT / relative
        assert path.is_file(), f"{relative} 不存在了；清单需要同步更新"
        size = len(path.read_text(encoding="utf-8").splitlines())
        if size > ceiling:
            oversized[relative] = f"{size} > {ceiling}"

    assert not oversized, (
        "这些模块超过了上限，先把新代码放到别处或拆分已有代码；"
        f"确有必要再显式提高上限：{oversized}"
    )


def test_ceiling_list_stays_meaningful() -> None:
    """上限不能留得离实际太远，否则这道闸就失效了。"""
    slack = {
        relative: ceiling - len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        for relative, ceiling in CEILINGS.items()
    }

    assert all(value >= 0 for value in slack.values())
    assert all(value <= 40 for value in slack.values()), slack
