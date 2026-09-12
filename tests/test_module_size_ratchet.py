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
    "destiny_mcp/tools/assistants.py": 1352,
    "destiny_mcp/build/farm_target.py": 1296,
    "destiny_mcp/bungie_client.py": 1219,
    "destiny_mcp/services/build_service.py": 1114,
    # 794 → 795：P3 收拢组件号，多一行 `from . import profile_components`；
    # 三处裸组件字面量换成命名集合没有增行，这一行就是净增量。
    "destiny_mcp/services/loadout_equipment_service.py": 795,
    "destiny_mcp/services/starside_service.py": 769,
    # P4 新增：武器分支载荷与形状工厂。定在上限处是为了让"再加一个 intent"
    # 必须先回答"是搬出去还是抬上限"，而不是悄悄长胖。
    # P5：本地资料挂载 + 固定/随机话术 + 覆盖表，各分支都要交代自己带哪些块（444 → 454）。
    # 再往上就该把"覆盖表 + 挂载"抽出去，而不是继续在这里加 intent。
    "destiny_mcp/tools/_weapon_branches.py": 454,
    "destiny_mcp/services/weapon_payload.py": 349,
    # P5 新增：本地资料汇总（愿单/选取率/清单/社区 → 每个 plug 的 recommended）
    "destiny_mcp/services/weapon_local_data.py": 403,
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
