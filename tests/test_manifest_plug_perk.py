"""Manifest 的 Plug / Perk 查询特征测试。

覆盖 `get_plug_set_plugs`、`get_sandbox_perk_description`、`get_plug_category_identifier`：
缓存、富化字段、hash 符号回退，以及前两个走中文优先而第三个只读英文连接这个不一致。
"""

from __future__ import annotations

import json
import sqlite3

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

ITEM_TABLE = "DestinyInventoryItemDefinition"
PLUG_SET_TABLE = "DestinyPlugSetDefinition"
PERK_TABLE = "DestinySandboxPerkDefinition"

UNSIGNED_HIGH_HASH = 4000000000
SIGNED_HIGH_HASH = to_signed(UNSIGNED_HIGH_HASH)


def _connection(tables: dict[str, list[tuple[int, str]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, rows in tables.items():
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, json TEXT)")
        for item_hash, raw in rows:
            conn.execute(f"INSERT INTO {table} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _plug_item(name: str = "枪管", category: str = "frames") -> str:
    return json.dumps(
        {"displayProperties": {"name": name}, "plug": {"plugCategoryIdentifier": category}},
        ensure_ascii=False,
    )


def _plugin_set(plug_hashes: list[int | None]) -> str:
    return json.dumps(
        {"reusablePlugItems": [{"plugItemHash": h} for h in plug_hashes]},
        ensure_ascii=False,
    )


# ── get_plug_set_plugs ───────────────────────────────────────────────


def test_plug_set_is_none_when_the_row_is_absent() -> None:
    manager = ManifestManager()
    manager._conn = _connection({PLUG_SET_TABLE: [(1, _plugin_set([10]))]})

    assert manager.get_plug_set_plugs(999) is None


def test_plug_set_enriches_each_plug_from_its_item_definition() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PLUG_SET_TABLE: [(1, _plugin_set([10, 11]))],
        ITEM_TABLE: [
            (10, _plug_item("枪管 A", "barrels")),
            (11, _plug_item("弹匣 B", "magazines")),
        ],
    })

    plugs = manager.get_plug_set_plugs(1)

    assert plugs == [
        {"plugItemHash": 10, "name": "枪管 A", "plugCategoryIdentifier": "barrels"},
        {"plugItemHash": 11, "name": "弹匣 B", "plugCategoryIdentifier": "magazines"},
    ]


def test_plug_set_skips_entries_without_a_plug_hash() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PLUG_SET_TABLE: [(1, _plugin_set([10, None]))],
        ITEM_TABLE: [(10, _plug_item())],
    })

    assert [plug["plugItemHash"] for plug in manager.get_plug_set_plugs(1)] == [10]


def test_plug_set_keeps_a_plug_whose_definition_is_missing() -> None:
    """定义查不到时仍返回该 plug，只是名字和类别为空——不能整条丢掉。"""
    manager = ManifestManager()
    manager._conn = _connection({PLUG_SET_TABLE: [(1, _plugin_set([10]))]})

    assert manager.get_plug_set_plugs(1) == [
        {"plugItemHash": 10, "name": "", "plugCategoryIdentifier": ""}
    ]


def test_plug_set_returns_an_empty_list_for_an_empty_pool() -> None:
    """条目存在但池子为空 → 空列表；条目不存在 → None。两者不同。"""
    manager = ManifestManager()
    manager._conn = _connection({PLUG_SET_TABLE: [(1, _plugin_set([]))]})

    assert manager.get_plug_set_plugs(1) == []


def test_plug_set_result_is_cached() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PLUG_SET_TABLE: [(1, _plugin_set([10]))],
        ITEM_TABLE: [(10, _plug_item())],
    })

    first = manager.get_plug_set_plugs(1)
    manager._conn = None

    assert manager.get_plug_set_plugs(1) is first


# ── get_sandbox_perk_description ─────────────────────────────────────


def test_sandbox_perk_returns_name_and_description() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PERK_TABLE: [(
            10,
            json.dumps({"displayProperties": {"name": "辉耀炽热", "description": "造成灼烧"}},
                       ensure_ascii=False),
        )]
    })

    assert manager.get_sandbox_perk_description(10) == {
        "name": "辉耀炽热",
        "description": "造成灼烧",
    }


def test_sandbox_perk_is_none_when_absent() -> None:
    manager = ManifestManager()
    manager._conn = _connection({PERK_TABLE: []})

    assert manager.get_sandbox_perk_description(10) is None


def test_sandbox_perk_treats_an_empty_definition_as_absent() -> None:
    """现状：`if not data` 把空字典也算查不到，所以返回 None 而不是空字段。"""
    manager = ManifestManager()
    manager._conn = _connection({PERK_TABLE: [(10, json.dumps({}))]})

    assert manager.get_sandbox_perk_description(10) is None


def test_sandbox_perk_uses_blank_strings_for_missing_fields() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PERK_TABLE: [(10, json.dumps({"displayProperties": {"name": "只有名字"}}))]
    })

    assert manager.get_sandbox_perk_description(10) == {"name": "只有名字", "description": ""}


def test_sandbox_perk_result_is_cached() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        PERK_TABLE: [(10, json.dumps({"displayProperties": {"name": "甲"}}))]
    })

    first = manager.get_sandbox_perk_description(10)
    manager._conn = None

    assert manager.get_sandbox_perk_description(10) is first


# ── get_plug_category_identifier ─────────────────────────────────────


def test_plug_category_identifier_reads_the_plug_section() -> None:
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(10, _plug_item(category="intrinsics"))]})

    assert manager.get_plug_category_identifier(10) == "intrinsics"


def test_plug_category_identifier_is_none_without_a_connection() -> None:
    manager = ManifestManager()
    manager._zh_conn = _connection({ITEM_TABLE: [(10, _plug_item(category="intrinsics"))]})

    assert manager.get_plug_category_identifier(10) is None


def test_plug_category_identifier_ignores_the_chinese_connection() -> None:
    """现状：这个方法只读 self._conn，不像 _query_json 那样中文优先。

    所以中文库里换了类别标识也不会被读到。这里记录现状。
    """
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(10, _plug_item(category="english"))]})
    manager._zh_conn = _connection({ITEM_TABLE: [(10, _plug_item(category="chinese"))]})

    assert manager.get_plug_category_identifier(10) == "english"


def test_plug_category_identifier_accepts_the_unsigned_form() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [(SIGNED_HIGH_HASH, _plug_item(category="intrinsics"))]
    })

    assert manager.get_plug_category_identifier(UNSIGNED_HIGH_HASH) == "intrinsics"


def test_plug_category_identifier_is_none_for_a_non_plug_entry() -> None:
    manager = ManifestManager()
    manager._conn = _connection({
        ITEM_TABLE: [(10, json.dumps({"displayProperties": {"name": "不是 plug"}}))]
    })

    assert manager.get_plug_category_identifier(10) is None


def test_plug_category_identifier_survives_broken_json() -> None:
    manager = ManifestManager()
    manager._conn = _connection({ITEM_TABLE: [(10, "{not json")]})

    assert manager.get_plug_category_identifier(10) is None
