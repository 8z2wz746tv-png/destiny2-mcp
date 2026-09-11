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
