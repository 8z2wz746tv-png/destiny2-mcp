"""Manifest 英文名、按插件类别/条目类型查找、本地化定义的特征测试。

这四个方法各自绑定一个连接，互不相同的回退规则是重点：
`get_english_name` 与 `get_localized_definition` 都**只**看自己那一侧，
不像 `_query_json` 那样中文优先再回退。
"""

from __future__ import annotations

import json
import sqlite3

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

ITEM_TABLE = "DestinyInventoryItemDefinition"
PERK_TABLE = "DestinySandboxPerkDefinition"

UNSIGNED_HIGH_HASH = 4000000000
SIGNED_HIGH_HASH = to_signed(UNSIGNED_HIGH_HASH)


def _compact(data: dict) -> str:
    """LIKE '%"itemType":N%' 依赖紧凑序列化。"""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _connection(tables: dict[str, list[tuple[int, str]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, rows in tables.items():
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, json TEXT)")
        for item_hash, raw in rows:
            conn.execute(f"INSERT INTO {table} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _item(name: str = "物品", *, item_type: int = 3, plug_category: str = "") -> str:
    data: dict = {
        "displayProperties": {"name": name},
        "itemType": item_type,
    }
    if plug_category:
        data["plug"] = {"plugCategoryIdentifier": plug_category}
    return _compact(data)


# ── get_english_name ─────────────────────────────────────────────────


def test_english_name_reads_only_the_english_connection() -> None:
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(1, _item("English"))]})
    manager._zh_conn = _connection({ITEM_TABLE: [(1, _item("中文"))]})

    assert manager.get_english_name(1) == "English"


def test_english_name_is_blank_without_the_english_connection() -> None:
    """只有中文连接时返回空串——它不会回退，与 _query_json 不同。"""
    manager = ManifestManager()
    manager._zh_conn = _connection({ITEM_TABLE: [(1, _item("中文"))]})

    assert manager.get_english_name(1) == ""


def test_english_name_accepts_both_hash_signs_and_marks_nothing() -> None:
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(SIGNED_HIGH_HASH, _item("高位装备"))]})

    assert manager.get_english_name(UNSIGNED_HIGH_HASH) == "高位装备"
    assert manager.get_english_name(999) == ""


def test_english_name_survives_broken_json_and_missing_fields() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [
            (1, "{not json"),
            (2, _compact({"itemType": 3})),
        ]
    })

    assert manager.get_english_name(1) == ""
    assert manager.get_english_name(2) == ""


# ── find_items_by_plug_category ──────────────────────────────────────


def test_find_items_by_plug_category_matches_in_python_not_just_in_sql() -> None:
    """SQL 只做粗筛，真正的判定是 Python 里的子串检查。"""
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [
            (1, _item("枪管甲", plug_category="barrels")),
            (2, _item("弹匣乙", plug_category="magazines")),
            # JSON 里出现了关键词，但 plugCategoryIdentifier 并不匹配
            (3, _compact({"displayProperties": {"name": "barrels 出现在描述里"},
                          "itemType": 3, "plug": {"plugCategoryIdentifier": "frames"}})),
        ]
    })

    names = [row["name"] for row in manager.find_items_by_plug_category("barrels")]

    assert names == ["枪管甲"]


def test_find_items_by_plug_category_returns_the_documented_shape() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [(1, _item("枪管甲", plug_category="barrels"))]
    })

    row = manager.find_items_by_plug_category("barrels")[0]

    assert row["hash"] == 1
    assert row["name"] == "枪管甲"
    assert row["plugCategoryIdentifier"] == "barrels"
    assert row["displayProperties"] == {"name": "枪管甲"}


def test_find_items_by_plug_category_prefers_the_chinese_connection() -> None:
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(1, _item("english", plug_category="barrels"))]})
    manager._zh_conn = _connection({ITEM_TABLE: [(1, _item("中文", plug_category="barrels"))]})

    assert manager.find_items_by_plug_category("barrels")[0]["name"] == "中文"


def test_find_items_by_plug_category_is_empty_without_a_connection() -> None:
    assert ManifestManager().find_items_by_plug_category("barrels") == []


def test_find_items_by_plug_category_skips_broken_json() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [(1, "{not json"), (2, _item("好的", plug_category="barrels"))]
    })

    assert [row["name"] for row in manager.find_items_by_plug_category("barrels")] == ["好的"]


# ── find_items_by_type ───────────────────────────────────────────────


def test_find_items_by_type_filters_on_compact_item_type_json() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [
            (1, _item("武器甲", item_type=3)),
            (2, _item("护甲乙", item_type=2)),
        ]
    })

    assert [row["displayProperties"]["name"] for row in manager.find_items_by_type(3)] == ["武器甲"]


def test_find_items_by_type_applies_the_extra_substring_filter() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [
            (1, _compact({"displayProperties": {"name": "带标记"}, "itemType": 3, "tag": "MARK"})),
            (2, _compact({"displayProperties": {"name": "不带标记"}, "itemType": 3})),
        ]
    })

    rows = manager.find_items_by_type(3, extra_json_like="MARK")

    assert [row["displayProperties"]["name"] for row in rows] == ["带标记"]


def test_find_items_by_type_returns_raw_definitions() -> None:
    """不像 find_items_by_plug_category 那样补 hash 字段。"""
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(1, _item("武器甲", item_type=3))]})

    row = manager.find_items_by_type(3)[0]

    assert "hash" not in row
    assert row["itemType"] == 3


def test_find_items_by_type_is_empty_without_a_connection_or_matches() -> None:
    assert ManifestManager().find_items_by_type(3) == []

    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(1, _item("护甲", item_type=2))]})
    assert manager.find_items_by_type(3) == []


# ── get_localized_definition ─────────────────────────────────────────


def test_localized_definition_needs_the_chinese_connection() -> None:
    """现状：只读中文库，没有中文库就返回 None，不回退英文。"""
    manager = ManifestManager()
    manager._conn = _connection({PERK_TABLE: [(1, _item("english"))]})

    assert manager.get_localized_definition(PERK_TABLE, 1) is None


def test_localized_definition_ignores_the_english_connection() -> None:
    manager = ManifestManager()
    manager._conn = _connection({PERK_TABLE: [(1, _item("english"))]})
    manager._zh_conn = _connection({PERK_TABLE: [(1, _item("中文"))]})

    found = manager.get_localized_definition(PERK_TABLE, 1)

    assert found["displayProperties"]["name"] == "中文"


def test_localized_definition_refuses_tables_outside_the_whitelist() -> None:
    manager = ManifestManager()
    manager._zh_conn = _connection({PERK_TABLE: [(1, _item("中文"))]})

    assert manager.get_localized_definition("DestinySecretTable", 1) is None


def test_localized_definition_accepts_both_hash_signs_and_survives_bad_json() -> None:
    manager = ManifestManager()
    manager._zh_conn = _connection({
        PERK_TABLE: [
            (SIGNED_HIGH_HASH, _item("高位")),
            (2, "{not json"),
        ]
    })

    assert manager.get_localized_definition(PERK_TABLE, UNSIGNED_HIGH_HASH) is not None
    assert manager.get_localized_definition(PERK_TABLE, 2) is None
    assert manager.get_localized_definition(PERK_TABLE, 999) is None
