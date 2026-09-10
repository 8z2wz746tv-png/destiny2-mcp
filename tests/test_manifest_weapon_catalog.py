"""Manifest 武器目录与条目列举的特征测试。

覆盖 `get_exotic_armor_by_class`、`list_weapon_catalog`、`_weapon_type_matches`、
`list_items`：过滤维度、按 hash 还是按名字去重、排序键，以及 limit 的截断时机。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from destiny_mcp.exceptions import ManifestError
from destiny_mcp.manifest import ManifestManager

ITEM_TABLE = "DestinyInventoryItemDefinition"


def _connection(rows: list[tuple[int, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE {ITEM_TABLE} (id INTEGER PRIMARY KEY, json TEXT)")
    for item_hash, raw in rows:
        conn.execute(f"INSERT INTO {ITEM_TABLE} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _raw(
    name: str,
    *,
    item_type: int = 3,
    tier: int = 5,
    display_type: str = "",
    class_type: int = -1,
    damage_type: int = 1,
    ammo_type: int = 1,
) -> str:
    return json.dumps(
        {
            "displayProperties": {"name": name, "icon": ""},
            "itemType": item_type,
            "itemTypeDisplayName": display_type,
            "inventory": {"tierType": tier, "bucketTypeHash": 1},
            "classType": class_type,
            "defaultDamageType": damage_type,
            "equippingBlock": {"ammoType": ammo_type},
        },
        ensure_ascii=False,
    )


def _manager(
    english: list[tuple[int, str]] | None = None,
    chinese: list[tuple[int, str]] | None = None,
) -> ManifestManager:
    manager = ManifestManager()
    if english is not None:
        manager._build_name_index(_connection(english), language="en")
    if chinese is not None:
        manager._build_name_index(_connection(chinese), language="zh-chs")
    return manager


# ── _weapon_type_matches ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "display_type", "expected"),
    [
        ("手炮", "手炮", True),
        ("手炮", "重型手炮", True),      # 查询是显示名的子串
        ("hand cannon", "hand cannon", True),
        ("hand cannon", "Hand Cannon", True),
        ("火箭筒", "火箭发射器", True),   # 别名组
        ("rocket launcher", "火箭筒", True),
        ("手炮", "自动步枪", False),
        ("手炮", "", False),
        # 现状：空查询因为 `"" in candidate` 成立而返回 True。
        # 调用方（list_weapon_catalog）用 `if type_query` 挡住了空值，所以没暴露。
        ("", "手炮", True),
    ],
)
def test_weapon_type_matching_rules(query: str, display_type: str, expected: bool) -> None:
    assert ManifestManager._weapon_type_matches(query, display_type) is expected


# ── get_exotic_armor_by_class ────────────────────────────────────────


def test_exotic_armor_filters_by_type_tier_and_class() -> None:
    manager = _manager(english=[
        (1, _raw("猎人金头", item_type=2, tier=6, class_type=1)),
        (2, _raw("猎人紫头", item_type=2, tier=5, class_type=1)),   # 非异域
        (3, _raw("术士金头", item_type=2, tier=6, class_type=2)),   # 别的职业
        (4, _raw("猎人金枪", item_type=3, tier=6, class_type=1)),   # 不是护甲
    ])

    assert [item["name"] for item in manager.get_exotic_armor_by_class("hunter")] == ["猎人金头"]
    assert [item["name"] for item in manager.get_exotic_armor_by_class("术士")] == ["术士金头"]
    assert manager.get_exotic_armor_by_class("nobody") == []


def test_exotic_armor_deduplicates_by_name_and_sorts() -> None:
    manager = _manager(english=[
        (1, _raw("乙金头", item_type=2, tier=6, class_type=1)),
        (2, _raw("甲金头", item_type=2, tier=6, class_type=1)),
        (3, _raw("甲金头", item_type=2, tier=6, class_type=1)),  # 同名不同 hash
    ])

    names = [item["name"] for item in manager.get_exotic_armor_by_class("hunter")]

    assert names == ["乙金头", "甲金头"]


# ── list_weapon_catalog ──────────────────────────────────────────────


def test_weapon_catalog_requires_a_loaded_index() -> None:
    with pytest.raises(ManifestError, match="Manifest not loaded"):
        ManifestManager().list_weapon_catalog()


def test_weapon_catalog_only_returns_weapons() -> None:
    manager = _manager(english=[
        (1, _raw("一把枪", item_type=3)),
        (2, _raw("一件甲", item_type=2)),
    ])

    assert [item["name"] for item in manager.list_weapon_catalog()] == ["一把枪"]


def test_weapon_catalog_sorts_by_tier_then_name_and_honours_limit() -> None:
    manager = _manager(english=[
        (1, _raw("乙紫枪", tier=5)),
        (2, _raw("甲金枪", tier=6)),
        (3, _raw("甲紫枪", tier=5)),
    ])

    assert [item["name"] for item in manager.list_weapon_catalog()] == ["甲金枪", "乙紫枪", "甲紫枪"]
    assert len(manager.list_weapon_catalog(limit=2)) == 2
    assert len(manager.list_weapon_catalog(limit=0)) == 3


def test_weapon_catalog_filters_by_type_and_name_across_languages() -> None:
    manager = _manager(
        english=[(1, _raw("Fatebringer", display_type="Hand Cannon"))],
        chinese=[(1, _raw("命运使者", display_type="手炮"))],
    )

    assert len(manager.list_weapon_catalog("手炮")) == 1
    assert len(manager.list_weapon_catalog("hand cannon")) == 1
    assert manager.list_weapon_catalog("自动步枪") == []
    # 中英名字都能作为名字过滤条件
    assert len(manager.list_weapon_catalog(weapon_name="命运")) == 1
    assert len(manager.list_weapon_catalog(weapon_name="fatebringer")) == 1


def test_weapon_catalog_deduplicates_by_hash_across_both_signs() -> None:
    """同一个 hash 只应出现一次，且中英两条记录不能算两把枪。"""
    manager = _manager(
        english=[(1, _raw("Fatebringer"))],
        chinese=[(1, _raw("命运使者"))],
    )

    assert len(manager.list_weapon_catalog()) == 1


# ── list_items ───────────────────────────────────────────────────────


def test_list_items_maps_filters_in_both_languages() -> None:
    manager = _manager(english=[
        (1, _raw("烈日手枪", item_type=3, tier=6, damage_type=2, ammo_type=1)),
        (2, _raw("虚空手枪", item_type=3, tier=5, damage_type=4, ammo_type=2)),
        (3, _raw("猎人护甲", item_type=2, tier=6, class_type=1)),
    ])

    assert [i["name"] for i in manager.list_items(item_type="weapon")] == ["烈日手枪", "虚空手枪"]
    assert [i["name"] for i in manager.list_items(item_type="武器")] == ["烈日手枪", "虚空手枪"]
    assert [i["name"] for i in manager.list_items(tier="exotic")] == ["烈日手枪", "猎人护甲"]
    # 伤害/弹药只过滤武器，护甲会原样通过
    assert [i["name"] for i in manager.list_items(damage_type="solar")] == ["烈日手枪", "猎人护甲"]
    assert [i["name"] for i in manager.list_items(ammo_type="special")] == ["猎人护甲", "虚空手枪"]


def test_list_items_applies_class_filter_only_to_armor() -> None:
    manager = _manager(english=[
        (1, _raw("猎人护甲", item_type=2, class_type=1)),
        (2, _raw("术士护甲", item_type=2, class_type=2)),
        (3, _raw("一把枪", item_type=3, class_type=-1)),
    ])

    names = [item["name"] for item in manager.list_items(class_name="hunter")]

    # 武器不受职业过滤影响
    assert names == ["一把枪", "猎人护甲"]


def test_list_items_lets_non_weapons_through_damage_and_ammo_filters() -> None:
    """现状：这两个条件写成「是武器且属性不符才排除」，所以护甲一律通过。"""
    manager = _manager(english=[
        (1, _raw("猎人护甲", item_type=2, damage_type=9, ammo_type=9)),
        (2, _raw("烈日手枪", item_type=3, damage_type=2, ammo_type=1)),
    ])

    assert [i["name"] for i in manager.list_items(damage_type="solar")] == ["烈日手枪", "猎人护甲"]
    assert [i["name"] for i in manager.list_items(ammo_type="primary")] == ["烈日手枪", "猎人护甲"]


def test_list_items_deduplicates_by_name_not_by_hash() -> None:
    """与武器目录不同：这里同名不同 hash 只留一件。"""
    manager = _manager(english=[
        (1, _raw("同名枪")),
        (2, _raw("同名枪")),
    ])

    assert len(manager.list_items(item_type="weapon")) == 1


def test_list_items_limit_semantics_differ_from_the_weapon_catalog() -> None:
    """现状（不是理想设计），两点都和 list_weapon_catalog 不一致：

    1. limit 在排序之前按 hash 索引顺序截断，所以名字排序本应最前的「丙」会被丢掉；
    2. limit=0 不是「不限量」——`len(results) >= limit` 在第一条之后就 break，
       实际等价于 limit=1；而 list_weapon_catalog 的 limit=0 才是不限量。

    记录现状；将来若要统一语义，这些断言会失败并提醒改动者。
    """
    manager = _manager(english=[
        (100, _raw("甲")),
        (101, _raw("乙")),
        (102, _raw("丙")),
    ])

    assert [item["name"] for item in manager.list_items(limit=2)] == ["乙", "甲"]
    assert [item["name"] for item in manager.list_items(limit=0)] == ["甲"]
    assert len(manager.list_items(limit=100)) == 3


def test_list_items_skips_nameless_entries() -> None:
    manager = _manager(english=[(1, _raw("")), (2, _raw("有名"))])

    assert [item["name"] for item in manager.list_items(limit=100)] == ["有名"]


def test_list_items_returns_nothing_when_a_filter_matches_no_entry() -> None:
    manager = _manager(english=[(1, _raw("一把枪"))])

    assert manager.list_items(item_type="子职业") == []
