"""活动模式词表的守门测试 —— 词、数值、PvP 归属，以及"只有单数 `mode=` 有效"这条实测。

数值来源：本地 Manifest `DestinyActivityModeDefinition.modeType`（2026-09-18 实测 75 个模式
类型，无重复无重名）。这里把**用到的**那几个钉成常量：Manifest 更新后数值若变了，
先来这里确认再改，别让口径悄悄漂移。真正的"跟 Manifest 对不对得上"由真机语料
（`scripts/run_corpus_pvp_rows.py`）回答 —— 单测不许依赖本机 manifest 库。
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.data import activity_modes, pvp_counters
from destiny_mcp.services.activity_service import ActivityService

# 2026-09-18 真机 + 本地 Manifest 实测。
EXPECTED_MODE_TYPES = {
    "story": 2,
    "strike": 3,
    "raid": 4,
    "crucible": 5,
    "patrol": 6,
    "allpve": 7,
    "iron_banner": 19,
    "nightfall": 46,
    "grandmaster": 47,
    "gambit": 63,
    "competitive": 69,
    "dungeon": 82,
    "trials": 84,
    "lostsector": 87,
}


def test_mode_types_are_pinned() -> None:
    assert activity_modes.MODE_TYPE == EXPECTED_MODE_TYPES


def test_pvp_means_category_two_and_gambit_is_not_in_it() -> None:
    """纯 PvP 就是 category=2；智谋是 PvPvE（3），别名再像也不算。"""
    assert set(activity_modes.pvp_keys()) == {
        "crucible", "iron_banner", "competitive", "trials",
    }
    assert activity_modes.is_pvp("crucible") and activity_modes.is_pvp("trials")
    assert not activity_modes.is_pvp("gambit"), "智谋是 PvPvE，不能被当成纯 PvP"
    assert not activity_modes.is_pvp("raid")
    # 真机实测：mode=5（熔炉伞形）拉到的 250 场里没有一场智谋 —— 与 category 判定一致。
    assert activity_modes.MODES["gambit"]["category"] == activity_modes.CATEGORY_PVPVE


def test_words_cover_chinese_labels_english_keys_and_the_umbrella_aliases() -> None:
    for word, expected in {
        "铁旗": "iron_banner",
        "试炼": "trials",
        "奥斯里斯试炼": "trials",
        "大师日落": "grandmaster",
        "pvp": "crucible",
        "allpvp": "crucible",
        "熔炉竞技场": "crucible",
        "智谋": "gambit",
        "TRIALS": "trials",          # 大小写不敏感
        "  突袭  ": "raid",          # 前后空格不敏感
    }.items():
        assert activity_modes.resolve(word) == expected, word
    assert activity_modes.resolve("") is None
    assert activity_modes.resolve("不存在的模式") is None


def test_onslaught_is_not_silently_mapped_to_competitive() -> None:
    """"猛攻"必须报不认识 —— 旧表把它映射成 69（多人竞技PvP），那是错答。

    Manifest 里没有 Onslaught 这个模式（86 只叫 `Offensive`／攻势，无法确认就是猛攻），
    按"缺值给 None、不编"的规矩：宁可报错，也不返回一堆竞技场次。
    """
    assert activity_modes.resolve("猛攻") is None
    assert activity_modes.resolve("onslaught") is None


def test_no_second_mode_table_reappears() -> None:
    """模式词/数值/中文名都只有一处：不许再抄一张表（抄过的那两张都烂了）。

    比第一版严在三处（第一版只认"行首就是被禁名 + 有 `=`"，换个写法就漏）：

    1. 用 AST 找**任何**形式的被禁名绑定（赋值、类型注解、字典键都算）；
    2. 扫描范围含 `tests/` 与 `scripts/` —— 在那里再抄一份表同样会烂；
    3. 另加一条中文标签的粗筛：PvP 模式名的**标签**不许出现在 `destiny_mcp/`
       （`data/activity_modes.py` 只放别名，标签一律去 Manifest 取）。
    """
    root = Path(__file__).resolve().parents[1]
    banned = {"ACTIVITY_MODES", "MODE_NAMES", "MODE_LABELS_ZH", "MODE_ACTIVITY_TYPES"}
    self_path = Path(__file__).resolve()
    offenders: list[str] = []

    for path in sorted([*root.glob("destiny_mcp/**/*.py"), *root.glob("tests/**/*.py"),
                        *root.glob("scripts/**/*.py")]):
        if "__pycache__" in path.as_posix() or path == self_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [_target_name(target) for target in node.targets]
            elif isinstance(node, ast.AnnAssign):
                names = [_target_name(node.target)]
            elif isinstance(node, ast.Dict):
                names = [
                    key.value for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
            for name in names:
                if name in banned:
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}: 绑定 {name}")

    # 中文标签粗筛：这些是"标签"（不是别名），只该来自 Manifest。
    # 只认**值正好等于标签**的字符串常量 —— 注释与 docstring 里引用这些名字是证据、
    # 不是表（第一版按子串扫，把注释里的"69 = 多人竞技PvP"也判红了）。
    labels = {"铁旗占领模式", "占领模式：快速游戏", "多人竞技PvP", "铁旗区域占领"}
    for path in sorted(root.glob("destiny_mcp/**/*.py")):
        if "__pycache__" in path.as_posix() or path.name == "activity_modes.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in labels:
                offenders.append(
                    f"{path.relative_to(root)}:{node.lineno}: 硬编码模式标签 {node.value!r}"
                )

    assert offenders == [], (
        "模式词/标签只能有 data/activity_modes.py + Manifest 两处出处：\n"
        + "\n".join(offenders)
    )


def _target_name(node: ast.expr) -> str:
    """赋值目标的名字：`X = ...` / `X: T = ...` / `obj.X = ...` 都取出末段名字。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _target_name(node.value)
    return ""


def test_counter_families_are_mode_words() -> None:
    """计数器那张表的家族词必须都是模式词（否则 counters 的 mode= 会筛出空清单）。"""
    families = [mode for mode in pvp_counters.filter_modes()]
    unknown = [mode for mode in families if mode not in activity_modes.MODES]
    assert unknown == []
    # `other` 是"没分类"的落点，故意不是模式词，也不出现在筛选项里。
    assert "other" in pvp_counters.MODES and "other" not in families


@pytest.mark.asyncio
async def test_history_uses_the_singular_mode_parameter_with_the_umbrella_value() -> None:
    """上游只有**单数** `mode=` 有效，复数的 `modes=` 会被静默忽略（实测返回 PvE 场次）。

    这条既是防呆（不许把 `modes` 写进 history 的 params），也是伞形过滤的证据：
    `mode=pvp` 必须翻成 5（熔炉竞技场），不是 9（Reserved9，`modes=9` 直接 500）。
    """
    bungie = AsyncMock()
    manifest = MagicMock()
    resolver = AsyncMock()
    resolver.resolve_player.return_value = {
        "membership_id": "4611686018000000001", "membership_type": 3,
    }
    resolver.get_profile.return_value = {
        "characters": {"data": {"2305843009000000001": {"classType": 0}}}
    }
    bungie.get_activity_history.return_value = {"ErrorCode": 1, "Response": {"activities": []}}
    service = ActivityService(bungie, manifest, resolver)

    await service.get_activity_history("TestGuardian#1234", mode="pvp")

    params = bungie.get_activity_history.await_args.kwargs["params"]
    assert params["mode"] == "5", params
    assert "modes" not in params, "复数参数会被上游静默忽略，等于没筛"
