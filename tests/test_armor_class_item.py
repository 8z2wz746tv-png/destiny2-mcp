"""职业金装两个 roll 特性的读取（`services/armor_class_item.py`）。

两层守门，缺一不可：

1. **替身层**（任何机器都能跑）：夹具里的 `plugCategoryHash` 用的是**2026-09-28 从真机
   Manifest 实测的值**（至纯光能之灵 = 1476923953、曲腹蛛之灵 = 3751917164→有符号
   3751917994，两者都是 1744546145）。所以常量写错一位这条就会红 —— 我第一版正是写成了
   1944746145。
2. **真 Manifest 层**（装了 Manifest 才跑，没有就 skip）：直接拿库里的定义反查，
   上游改了类别号能被抓到。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from destiny_mcp.services import armor_class_item as mod

# 2026-09-28 实测（真机 Manifest）：
#   1476923953 = 至纯光能之灵（超能30/近战25）
#   3751917994（无符号）= 曲腹蛛之灵（纯机制，无属性）
PERK_CATEGORY = 1744546145
PURE_LUMINANCE = 1476923953
CYRTARACHNE = -543049302  # 3751917994 的有符号写法（同一颗曲腹蛛之灵）
SUPER_STAT = 144602215
MELEE_STAT = 4244567218


def _plug(name: str, category: int, stats: list[tuple[int, int]] | None = None) -> dict:
    return {
        "displayProperties": {"name": name},
        "plug": {"plugCategoryHash": category, "plugCategoryIdentifier": "intrinsics"},
        "investmentStats": [
            {"statTypeHash": h, "value": v} for h, v in (stats or [])
        ],
    }


def _table(defs: dict[int, dict]):
    def lookup(plug_hash: int):
        return defs.get(plug_hash)
    return lookup


def _stats_of(defn):
    out = {}
    for entry in (defn or {}).get("investmentStats") or []:
        out[entry["statTypeHash"]] = entry["value"]
    return out


def test_reads_both_rolled_perks_in_socket_order() -> None:
    """两颗特性都读出来，按槽序；带属性的给出属性，纯机制给空 dict。"""
    defs = {
        PURE_LUMINANCE: _plug("至纯光能之灵", PERK_CATEGORY, [(SUPER_STAT, 30), (MELEE_STAT, 25)]),
        CYRTARACHNE: _plug("曲腹蛛之灵", PERK_CATEGORY),
    }
    rows = [
        {"plug_hash": PURE_LUMINANCE, "name": "至纯光能之灵"},
        {"plug_hash": CYRTARACHNE, "name": "曲腹蛛之灵"},
    ]

    out = mod.class_item_perks(rows, _table(defs), _stats_of)

    assert [p["name"] for p in out] == ["至纯光能之灵", "曲腹蛛之灵"]
    assert out[0]["stats"] == {SUPER_STAT: 30, MELEE_STAT: 25}
    assert out[1]["stats"] == {}, "纯机制特性没有属性 —— 给空 dict，不编 0"


def test_ignores_everything_that_is_not_a_class_item_perk() -> None:
    """词条 / 大师 / 调谐 / 皮肤都不能被当成特性读进来。

    这条守的是"判据用 `plugCategoryHash` 而不是名字"：`intrinsics` 这个**标识符**下有 1004 条
    （皮肤 247 条一类），而这一族只有 36 颗共用一个 hash。
    """
    defs = {
        111: _plug("楷模典范", 3000000001),
        222: _plug("升级护甲", 3000000002),
        333: _plug("平衡调整", 3000000003, [(SUPER_STAT, 1)]),
        444: _plug("某皮肤", 3000000004),
    }
    rows = [
        {"plug_hash": 111, "name": "楷模典范"},
        {"plug_hash": 222, "name": "升级护甲"},
        {"plug_hash": 333, "name": "平衡调整"},
        {"plug_hash": 444, "name": "某皮肤"},
        {"plug_hash": 0, "name": "空槽"},
        {"plug_hash": 999, "name": "查不到定义"},
    ]

    assert mod.class_item_perks(rows, _table(defs), _stats_of) == []


def test_empty_socket_list_is_not_an_error() -> None:
    """不是职业金装（没有特性槽）时给空列表 —— 缺数据不是错误。"""
    assert mod.class_item_perks([], _table({}), _stats_of) == []


def test_constant_matches_the_manifest_when_it_is_installed() -> None:
    """真 Manifest 反查：类别号对不对、那两颗特性还在不在。

    没装 Manifest 就跳过（单测要能在干净 HOME 下跑）—— 上面替身层那条仍然有效。
    """
    db = Path(__file__).resolve().parents[1] / "manifest" / "destiny_manifest_zh.sqlite3"
    if not db.is_file():
        pytest.skip("本机没装 Manifest；替身层已覆盖常量本身")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for plug_hash, expect_name in ((PURE_LUMINANCE, "至纯光能之灵"), (CYRTARACHNE, "曲腹蛛之灵")):
            row = conn.execute(
                "select json from DestinyInventoryItemDefinition where id=?", (plug_hash,)
            ).fetchone()
            assert row is not None, f"{expect_name} 在 Manifest 里查不到了（hash 或写法变了？）"
            text = row[0]
            assert f'"plugCategoryHash":{PERK_CATEGORY}' in text.replace(" ", ""), (
                f"{expect_name} 的 plugCategoryHash 不再是 {PERK_CATEGORY} —— "
                "上游改了类别号，`CLASS_ITEM_PERK_CATEGORY_HASH` 要跟着改"
            )
    finally:
        conn.close()


# ── 按名字认特性 + 翻回金装名（社区模板那一支）──────────────────────────────


class _FakeManifest:
    """只实现 `search` + `get_item_definition`；`search` 给的是 `itemHash` 键（真形状）。"""

    def __init__(self, table: dict[str, tuple[int, int]], *, with_definition: bool = True) -> None:
        self._table = table
        # 真"没有能力"的替身：根本没有这个方法（`tests/test_starside_integration.py` 的
        # `_Aliased` 就是这样，真机踩到过）。
        if with_definition:
            self.get_item_definition = self._get_item_definition

    def search(self, name, *, limit=20):
        entry = self._table.get(name)
        if entry is None:
            return []
        return [{"itemHash": entry[0], "name": name}]

    def _get_item_definition(self, h):
        for name, (item_hash, category) in self._table.items():
            if item_hash == h:
                return {
                    "displayProperties": {"name": name},
                    "plug": {"plugCategoryHash": category},
                }
        return None


PERK_TABLE = {
    "至纯光能之灵": (PURE_LUMINANCE, PERK_CATEGORY),
    "曲腹蛛之灵": (CYRTARACHNE, PERK_CATEGORY),
    "快速装弹松身裤": (1793346751, 99999999),
}


def test_splits_the_community_separator_forms() -> None:
    """社区那 14 套写「A、B」；半角逗号与加号也认，空白丢掉。"""
    assert mod.split_perk_names("至纯光能之灵、曲腹蛛之灵") == ["至纯光能之灵", "曲腹蛛之灵"]
    assert mod.split_perk_names(" 至纯光能之灵 ,  曲腹蛛之灵 ") == ["至纯光能之灵", "曲腹蛛之灵"]
    assert mod.split_perk_names("单个金装名") == ["单个金装名"]


def test_two_perks_resolve_to_the_class_item_name() -> None:
    """两个特性 → 翻回金装名，并带上已解析的特性（hash 归一成无符号）。"""
    out = mod.class_item_for_perks(
        _FakeManifest(PERK_TABLE), ["至纯光能之灵", "曲腹蛛之灵"], "hunter"
    )
    assert out is not None
    armor_name, perks = out
    assert armor_name == "相对主义"
    assert [p["name"] for p in perks] == ["至纯光能之灵", "曲腹蛛之灵"]
    assert perks[1]["item_hash"] == 3751917994, "必须是无符号，与 payload/sockets 的写法一致"


def test_plain_armor_name_is_not_a_perk_combo() -> None:
    """普通金装名（76 套的写法）不能被当成特性，否则会把松身裤解析成职业金。"""
    m = _FakeManifest(PERK_TABLE)
    assert mod.class_item_for_perks(m, ["快速装弹松身裤"], "hunter") is None
    assert mod.class_item_for_perks(m, ["至纯光能之灵", "不存在的之灵"], "hunter") is None
    # 只点名一颗特性是合法的（组合里只列一颗），按职业翻成对应的那件金装
    only_one = mod.class_item_for_perks(m, ["至纯光能之灵"], "warlock")
    assert only_one is not None and only_one[0] == "唯我主义"
    assert mod.class_item_for_perks(m, ["至纯光能之灵"], "unknown-class") is None


def test_missing_definition_capability_is_not_an_error() -> None:
    """替身没有 `get_item_definition` 时返回 None，不许抛 AttributeError。

    真机踩过：社区比对的替身只实现 `search`，硬依赖 `get_item_definition` 会让
    `tests/test_starside_integration.py` 整片红（22 条）。缺数据 ≠ 报错。
    """
    m = _FakeManifest(PERK_TABLE, with_definition=False)
    assert mod.perk_plug(m, "至纯光能之灵") is None
    assert mod.class_item_for_perks(m, ["至纯光能之灵"], "hunter") is None


# ── 核对"这件 roll 得对不对"（组件 305 的已装插槽）──────────────────────────


def _sockets(perks: list[tuple[int, str]]) -> list[dict]:
    """`class_item_perks_of` 的输出形状（它已经只留职业金特性）。"""
    return [{"name": n, "plug_hash": h} for h, n in perks]


REQUIRED = [{"item_hash": PURE_LUMINANCE, "name": "至纯光能之灵"},
            {"item_hash": 3751917994, "name": "曲腹蛛之灵"}]


def test_verified_when_a_copy_has_both_required_perks() -> None:
    """有副本同时满足两颗 → verified，并指出是哪一个副本。"""
    socks = {
        "wrong": _sockets([(1476923952, "刺客之灵"), (1476923956, "合成感受器之灵")]),
        "right": _sockets([(PURE_LUMINANCE, "至纯光能之灵"), (3751917994, "曲腹蛛之灵")]),
    }
    result, best = mod.classify_required_perks(REQUIRED, socks, ["wrong", "right"])
    assert result["status"] == "verified"
    assert best == {"instance_id": "right", "perks": ["至纯光能之灵", "曲腹蛛之灵"]}


def test_owned_wrong_perks_lists_what_was_actually_rolled() -> None:
    """读到了但都不满足 → owned_wrong_perks，并把各副本实际滚到的列出来（供人决定）。"""
    socks = {
        "a": _sockets([(1476923952, "刺客之灵"), (1476923956, "合成感受器之灵")]),
        "b": _sockets([(PURE_LUMINANCE, "至纯光能之灵"), (1476923956, "合成感受器之灵")]),
    }
    result, best = mod.classify_required_perks(REQUIRED, socks, ["a", "b"])
    assert result["status"] == "owned_wrong_perks"
    assert best is None
    assert [r["instance_id"] for r in result["rolled"]] == ["a", "b"]
    assert result["rolled"][0]["perks"] == ["刺客之灵", "合成感受器之灵"]


def test_missing_socket_data_is_unknown_not_a_failure() -> None:
    """组件 305 里没有这件 → **unknown**，绝不能降级成"不满足"。

    这条是项目老规矩（"没探成" ≠ "试过没有"）：读不到就说读不到。
    """
    result, best = mod.classify_required_perks(REQUIRED, {}, ["a"])
    assert result["status"] == "unknown"
    assert result["reason"] == "socket_component_missing_for_candidates"
    assert best is None


def test_signed_and_unsigned_hashes_compare_equal() -> None:
    """两条来路的写法不同（无符号 3751917994 / 有符号 -543049302），核对时不能假阴。"""
    socks = {"a": _sockets([(PURE_LUMINANCE, "至纯光能之灵"), (-543049302, "曲腹蛛之灵")])}
    result, best = mod.classify_required_perks(REQUIRED, socks, ["a"])
    assert result["status"] == "verified", "有符号/无符号是同一个 hash，不该判成不满足"
    assert best is not None and best["instance_id"] == "a"
