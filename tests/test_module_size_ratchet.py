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
    "destiny_mcp/tools/assistants.py": 1208,
    "destiny_mcp/build/farm_target.py": 1296,
    # +44：上游 HTTP 错误统一映射（以前只有 503 被翻译，4xx 裸抛到 MCP 客户端）。
    # P1：错误映射整段搬去 `bungie_errors.py`，活动统计端点搬去 `bungie_stats.py`（客户端只留
    # 门面方法）→ 1263 → 1164，上限跟着收紧：腾出来的位置已经用掉了（账号级 + 按模式统计）。
    "destiny_mcp/bungie_client.py": 1164,
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
    "destiny_mcp/services/build_service.py": 889,
    # 794 → 795：P3 收拢组件号，多一行 `from . import profile_components`；
    # 三处裸组件字面量换成命名集合没有增行，这一行就是净增量。
    # 抽走 _capture_recovery_state（→ loadout_recovery.py）后下调：上限只能降不能升
    # 520 → 519：`equip_loadout` 补上回读核对（同一条纪律，与 equip_exact 一致）。
    # 519 → 521（0.7.9）：模组预检的入口条件要认"只有照抄模组"的件（+1），以及多继承一个
    # `FunctionalModMixin`（+1）。决策逻辑本身在 `loadout_functional_mods.py`。
    "destiny_mcp/services/loadout_equipment_service.py": 521,
    "destiny_mcp/services/starside_service.py": 769,
    # 新增登记（PvP 武器榜）：不登记就等于没有闸 —— 仓库的规矩是"要在这里加代码
    # 得先做一次有意识的决定"，而不是等它长成下一个上帝模块。当前 529 行即上限。
    # 拆分后登记：PGCR 缓存整段抽去 `services/pgcr_cache.py`（541 → 461），
    # 上限按拆分后的长度收紧 —— 缓存与聚合本来就是两个问题。
    # 性能六项：PGCR 数值解析与时间窗计算搬去 `services/pgcr_values.py`（与榜单逻辑无关）
    # → 468 → 434，上限跟着收紧。
    "destiny_mcp/services/pvp_weapon_service.py": 434,
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
    "destiny_mcp/services/weapon_payload.py": 297,
    # P5 新增：本地资料汇总（愿单/选取率/清单/社区 → 每个 plug 的 recommended）
    "destiny_mcp/services/weapon_local_data.py": 403,
    # 锻造图样查询新增登记（登记即上限）：目录（展示树 + 记录 + 目标需求）+ 账号进度
    # （组件 900 的**档案级与角色级两份**）+ 来源装配 + 变体的塑形配置都在这里。
    # 463 = 427 + 角色级记录合并（真机事故：32 条角色级记录被漏读）+ read 块收敛。
    # 488 = 463 + 稀有度筛选与 by_tier 汇总（真机事故：只读第一页把 16 把金枪报成 2 把）。
    # 再往里加就该拆"目录/账号状态"，而不是抬上限。
    "destiny_mcp/services/pattern_service.py": 488,
    # 同上：Starside「锻造武器来源」的名字索引（归一化 / 表格 / 正文兜底 / 出处）。
    # 名字归一化只此一处，别再抄到 pattern_service 或工具层。
    "destiny_mcp/services/starside_crafting_sources.py": 186,
    # 周常轮换新增登记（登记即上限）：周期表 + 锚点 + 六类轮换的取值规则（纯计算）。
    # 官方接口只给突袭/地牢（里程碑）与夜幕/宗师（组件 204），其余靠这张表 —— 见 ADR-010。
    # 遗失区域那 27 个地点按目的地分组写在这里（用户截图的「World Lost Sector」专家列表）。
    "destiny_mcp/data/rotations.py": 166,
    # 同上：把官方那半（里程碑 + 组件 204）与表那半拼起来，并给每行标 source。
    "destiny_mcp/services/rotation_service.py": 313,
    # 同上：`intent="rotations"` 的载荷与话术（口径分离 / 未锚点说明 / {var:} 提醒）。
    "destiny_mcp/tools/_rotation_branches.py": 111,
    # 只发标量的宿主（豆包 connector）把结构化参数写成文本时的还原：登记表 + 一次性还原。
    # 分隔符规则不在这儿，在 `destiny_mcp/utils/arg_text.py`（工具层与服务层共用一份）。
    "destiny_mcp/tools/_coerce.py": 118,
    # 候选暂存（从 build_service 拆出）：登记即上限 —— 再往里加东西先回答"是不是该拆状态与话术"。
    # 101 → 126：`get_build_candidate` 的判定与话术（expired/unknown 两种下一步）搬了进来，
    # 与暂存同一处；换出来的是 build_service 的行数（那边要放"阶梯试解走并发档"）。
    "destiny_mcp/services/build_candidates.py": 126,
    # 同上：`intent="patterns"` 的载荷与话术（总览 / 单把 / 变体 / 术语对照 /「未开始」措辞）。
    # 211 是加上"玩家说红框、游戏说模式"的术语块与变体话术（含强化插槽）之后的长度；
    # 215 = 211 + 载荷里的 by_tier 汇总。
    "destiny_mcp/tools/_patterns_branches.py": 215,
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
    "destiny_mcp/services/loadout_recovery.py": 385,
    # 模组插槽读写/预检（三条写入路径共用 `plug_already_installed`）。
    # 562 → 518：能量腾挪那段搬去 `loadout_energy_budget.py`（预算是一道算术 + 挑选规则，
    # 与"这颗该进哪个槽"是两件事），腾出来的位置给照抄模组的调用点。
    "destiny_mcp/services/loadout_mod_sockets.py": 518,
    # 0.7.10：`find`/`recommend` 的候选行投影（默认出口只给行 + execution_id）。
    "destiny_mcp/tools/_build_flow.py": 366,
    # 0.7.11：武器分析的投影（池子只给愿单有结论的项、副本去掉可换项）。
    # 108 → 166（0.7.13）：同名多副本的**行视图**（`compare_rows`）也搬进来 —— 它和 analyze
    # 的投影是同一件事（把武器域的结果投影成"行 + 判定依据"），放一起比再开一个文件清楚。
    "destiny_mcp/services/weapon_analysis_projection.py": 173,
    # 0.7.10：重复武器的行视图（`duplicate_rows`；perk 从对象压成 {name, slot}）。
    # 0.7.12：按**类别**把插槽拆成 perks / mods / masterwork（"空模组插槽"不再是 perk）。
    "destiny_mcp/services/inventory_analysis_service.py": 654,
    # 0.7.9 新增：社区配装的功能模组（名字 → 部位/版本/能量；不进求解器，只算它占多少能量）。
    "destiny_mcp/build/functional_mods.py": 159,
    # 0.7.9 新增：照抄模组在执行时的现场决策（挑版本、插不进/装不下就跳过并点名）。
    "destiny_mcp/services/loadout_functional_mods.py": 127,
    # 0.7.9 新增（从 loadout_mod_sockets 拆出）：模组的能量预算与"拿谁去腾能量"。
    "destiny_mcp/services/loadout_energy_budget.py": 85,
    # 配装服务本体：清单/详情/存档/预览/官方槽都在这。真机走查连加了两块（`describe_save`
    # 与抽出的 `_read_equipment`），登记在 1030 就是"下次先想清楚放哪儿"。
    "destiny_mcp/services/loadout_service.py": 1030,
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
