"""护甲模组筛选：关键词的语言必须两种都认。

工具面其它地方用的是英文键（`weapons_target`、`priority_stats` 的
`weapons/health/class_stat/…`），而模组名称和描述是中文。以前只按中文子串匹配，
传英文会**安静地返回 0 条** —— 看起来像"游戏里没有加武器的模组"。
这条路径此前没有任何测试。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.manifest import ManifestManager

ITEM_TABLE = "DestinyInventoryItemDefinition"
GENERAL_MOD_CATEGORY = 2487827355
HELMET_MOD_CATEGORY = 2912171003

STAT_WEAPONS = 2996146975
STAT_HEALTH = 392767087
STAT_MELEE = 4244567218
STAT_GRENADE = 1735777505


def _connection(rows: list[tuple[int, dict]]) -> sqlite3.Connection:
    """LIKE '%"itemType":19%' 依赖紧凑序列化。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE {ITEM_TABLE} (id INTEGER PRIMARY KEY, json TEXT)")
    for item_hash, data in rows:
        conn.execute(
            f"INSERT INTO {ITEM_TABLE} (id, json) VALUES (?, ?)",
            (item_hash, json.dumps(data, separators=(",", ":"), ensure_ascii=False)),
        )
    return conn


def _mod(
    name: str,
    *,
    category: int = GENERAL_MOD_CATEGORY,
    stat_hash: int = 0,
    value: int = 10,
    description: str = "",
) -> dict:
    stats = [{"statTypeHash": stat_hash, "value": value}] if stat_hash else []
    return {
        "itemType": 19,
        "displayProperties": {"name": name, "description": description},
        "plug": {"plugCategoryHash": category, "energyCost": {"energyCost": 1}},
        "investmentStats": stats,
        "perks": [],
    }


def _manager() -> ManifestManager:
    manager = ManifestManager()
    manager._zh_conn = _connection([
        (1, _mod("武器模组", stat_hash=STAT_WEAPONS)),
        (2, _mod("小型生命值模组", stat_hash=STAT_HEALTH)),
        (3, _mod("近战模组", stat_hash=STAT_MELEE)),
        (4, _mod("手雷模组", stat_hash=STAT_GRENADE)),
        (5, _mod("头盔模组", category=HELMET_MOD_CATEGORY)),
    ])
    return manager


def _names(mods: list[dict]) -> set[str]:
    return {mod["name"] for mod in mods}


def test_stat_filter_accepts_english_and_chinese() -> None:
    """同一个筛选词，两种语言必须给出同一批模组。"""
    manager = _manager()

    for english, chinese in (
        ("weapons", "武器"),
        ("health", "生命"),
        ("melee", "近战"),
        ("力量", "力量"),
        ("grenade", "手雷"),
    ):
        assert _names(manager.get_armor_mods(stat=english)) == _names(
            manager.get_armor_mods(stat=chinese)
        ), f"{english} 与 {chinese} 结果不一致"


def test_stat_filter_matches_the_right_mods() -> None:
    manager = _manager()

    assert _names(manager.get_armor_mods(stat="weapons")) == {"武器模组"}
    assert _names(manager.get_armor_mods(stat="health")) == {"小型生命值模组"}
    # "力量"在游戏里就是近战属性
    assert _names(manager.get_armor_mods(stat="力量")) == {"近战模组"}


def test_category_words_still_match_the_description() -> None:
    """中文关键词直接匹配描述文本的行为不能因为别名表而丢失。"""
    manager = ManifestManager()
    manager._zh_conn = _connection([
        (1, _mod("某模组", description="+10 恢复 ▲")),
    ])

    assert _names(manager.get_armor_mods(stat="恢复")) == {"某模组"}
    assert _names(manager.get_armor_mods(stat="recovery")) == {"某模组"}


def test_unknown_stat_filter_is_loud() -> None:
    """不认识的筛选词要报错并列出词表。

    安静返回 0 条会被读成"游戏里没有加这个属性的模组"，而真相是"这个词我不认识"。
    """
    manager = _manager()

    with pytest.raises(InvalidArgumentError, match="不认识的属性筛选词"):
        manager.get_armor_mods(stat="这不存在的属性")
    with pytest.raises(InvalidArgumentError, match="不认识的属性筛选词"):
        manager.get_armor_mods(stat="武器伤害")


def test_unknown_slot_and_category_are_loud() -> None:
    manager = _manager()

    with pytest.raises(InvalidArgumentError, match="不支持的部位"):
        manager.get_armor_mods(slot="不存在的部位")
    with pytest.raises(InvalidArgumentError, match="不支持的模组类别"):
        manager.get_armor_mods(category="不存在的类别")


def test_slot_filter_accepts_chinese_slot_names() -> None:
    manager = _manager()

    assert "头盔模组" in _names(manager.get_armor_mods(slot="helmet"))
    assert _names(manager.get_armor_mods(slot="helmet")) == _names(
        manager.get_armor_mods(slot="头盔")
    )


def test_no_filter_returns_every_mod() -> None:
    manager = _manager()

    assert _names(manager.get_armor_mods()) == {
        "武器模组", "小型生命值模组", "近战模组", "手雷模组", "头盔模组",
    }


def test_missing_connection_is_empty_not_an_error() -> None:
    assert ManifestManager().get_armor_mods(stat="weapons") == []


def test_keyword_hit_outside_the_vocabulary_is_marked_as_fuzzy() -> None:
    """词表外的词靠描述蒙中时，必须说清这不是按属性筛的。

    真机案例：`速度` 返回 57 条弹药类模组，`stat_bonus` 全空，调用方却只会看到
    "ok=true + 57 条"，很容易当成"这些是加速度的模组"。
    """
    manager = ManifestManager()
    manager._zh_conn = _connection([
        (1, _mod("弹药生成", description="提高换弹速度")),
        (2, _mod("武器模组", stat_hash=STAT_WEAPONS)),
    ])

    picked = manager.get_armor_mods_filtered(stat="速度")

    assert _names(picked["mods"]) == {"弹药生成"}
    assert picked["match"]["kind"] == "keyword"
    assert picked["match"]["matched"] == 1
    assert picked["match"]["with_stat_bonus"] == 0
    assert "不在属性词表里" in picked["match"]["warning"]


def test_vocabulary_word_is_marked_as_stat_match() -> None:
    manager = _manager()

    picked = manager.get_armor_mods_filtered(stat="武器")

    assert picked["match"]["kind"] == "stat"
    assert "warning" not in picked["match"]
    assert _names(picked["mods"]) == {"武器模组"}


def test_vocabulary_word_with_zero_hits_explains_itself() -> None:
    """词表认这个词、但这批数据里一条都没有：不能沉默返回 0 条。"""
    manager = _manager()

    picked = manager.get_armor_mods_filtered(stat="敏捷")

    assert picked["mods"] == []
    assert picked["match"]["kind"] == "stat"
    assert "本地数据里没有" in picked["match"]["warning"]


def test_blank_stat_is_not_a_filter() -> None:
    """空格不该被当成筛选词（以前 `" "` 会命中全部模组）。"""
    manager = _manager()

    picked = manager.get_armor_mods_filtered(stat="   ")

    assert picked["match"]["kind"] == "all"
    assert len(picked["mods"]) == 5


# ── 记忆化：这一步是**全表扫**（真机 ~5.8 秒/次，每次护甲快照都问一遍） ──────────


def test_armor_mod_scan_is_memoized_and_copies_out(monkeypatch) -> None:
    """重复问同一 (部位, 类别) 不许再扫全表；发出去的必须是拷贝。

    真机实测（2026-09-22）：护甲快照构建 7.3 秒里有 **5.8 秒**花在这次全表扫上
    （`SELECT ... WHERE json LIKE '%"itemType":19%'` → 一万多行逐行 `json.loads`），
    而求解、复核、装备预检各会问一遍。
    """
    manager = _manager()
    scans = {"n": 0}
    original = ManifestManager._scan_armor_mods

    def counting(self, *args, **kwargs):
        scans["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ManifestManager, "_scan_armor_mods", counting)

    first = manager.get_armor_mods(category="all")
    assert scans["n"] == 1
    again = manager.get_armor_mods(category="all")
    assert scans["n"] == 1, "第二次不该再扫全表"
    assert [m["name"] for m in again] == [m["name"] for m in first]

    # 不同键各扫一次（部位/类别都会换 target_hashes）
    manager.get_armor_mods(slot="helmet", category="all")
    assert scans["n"] == 2

    # 出参是浅拷贝：调用方（`_armor_branches` 会给模组补 writable/理由）改不动缓存
    first.clear()
    assert len(manager.get_armor_mods(category="all")) == len(again), "缓存不许被调用方清空"
    cached_row = manager.get_armor_mods(category="all")[0]
    cached_row["writable"] = False
    assert "writable" not in manager.get_armor_mods(category="all")[0], "就地改会污染缓存"


def test_reloading_the_manifest_drops_the_armor_mod_cache(tmp_path, monkeypatch) -> None:
    """重载必须重扫：缓存在 `_load_from_file` 那张派生缓存清单里（新加的别漏）。"""
    import sqlite3

    manager = _manager()
    manager.get_armor_mods(category="all")
    assert manager._armor_mod_cache, "先要有缓存才谈得上失效"

    db = tmp_path / "destiny_manifest.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(f"CREATE TABLE {ITEM_TABLE} (id INTEGER PRIMARY KEY, json TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(type(manager), "manifest_path", property(lambda self: db))
    monkeypatch.setattr(
        type(manager), "manifest_path_zh", property(lambda self: tmp_path / "zh.sqlite3")
    )
    manager._load_from_file()

    assert manager._armor_mod_cache == {}, "重载后旧结果不许留下来"
