"""Manifest 通用定义查询与命名解析的特征测试。

覆盖 `get_definition` 及其四个包装、`iter_definitions`、
`search_definitions_by_name`、`find_collectible_by_item_hash`。
重点是几种「查不到」的返回值差异：有的给占位串，有的给空串。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

VENDOR_TABLE = "DestinyVendorDefinition"
MILESTONE_TABLE = "DestinyMilestoneDefinition"
ACTIVITY_TABLE = "DestinyActivityDefinition"
ACTIVITY_TYPE_TABLE = "DestinyActivityTypeDefinition"
COLLECTIBLE_TABLE = "DestinyCollectibleDefinition"

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


def _named(name: str | None, description: str = "") -> str:
    display: dict[str, str] = {}
    if name is not None:
        display["name"] = name
    if description:
        display["description"] = description
    return json.dumps({"displayProperties": display}, ensure_ascii=False)


def _manager(**tables: list[tuple[int, str]]) -> ManifestManager:
    manager = ManifestManager()
    manager._conn = _connection(dict(tables))
    return manager


# ── get_definition ───────────────────────────────────────────────────


def test_get_definition_needs_a_connection() -> None:
    assert ManifestManager().get_definition(VENDOR_TABLE, 1) is None


def test_get_definition_refuses_tables_outside_the_whitelist(caplog) -> None:
    manager = _manager()

    with caplog.at_level(logging.WARNING):
        result = manager.get_definition("DestinySecretTable", 1)

    assert result is None
    assert "unknown table" in caplog.text


def test_get_definition_accepts_both_hash_signs() -> None:
    manager = _manager(**{VENDOR_TABLE: [(SIGNED_HIGH_HASH, _named("班西"))]})

    assert manager.get_definition(VENDOR_TABLE, UNSIGNED_HIGH_HASH) is not None
    assert manager.get_definition(VENDOR_TABLE, SIGNED_HIGH_HASH) is not None
    assert manager.get_definition(VENDOR_TABLE, 999) is None


# ── 命名包装 ─────────────────────────────────────────────────────────


def test_vendor_and_milestone_definitions_use_their_own_tables() -> None:
    manager = _manager(
        **{
            VENDOR_TABLE: [(1, _named("班西-44"))],
            MILESTONE_TABLE: [(2, _named("本周日暮"))],
        }
    )

    assert manager.get_vendor_definition(1)["displayProperties"]["name"] == "班西-44"
    assert manager.get_milestone_definition(2)["displayProperties"]["name"] == "本周日暮"
    # 表串了就该查不到
    assert manager.get_vendor_definition(2) is None
    assert manager.get_milestone_definition(1) is None


def test_name_lookups_mark_unknown_hashes() -> None:
    manager = _manager(**{
        VENDOR_TABLE: [(1, _named("班西-44"))],
        MILESTONE_TABLE: [(2, _named("本周日暮"))],
        ACTIVITY_TABLE: [(3, _named("玻璃拱顶"))],
        ACTIVITY_TYPE_TABLE: [(4, _named("突袭"))],
    })

    assert manager.get_vendor_name(1) == "班西-44"
    assert manager.get_milestone_name(2) == "本周日暮"
    assert manager.get_activity_name(3) == "玻璃拱顶"
    assert manager.get_activity_type_name(4) == "突袭"

    assert manager.get_vendor_name(999) == "Vendor(999)"
    assert manager.get_milestone_name(999) == "Milestone(999)"
    assert manager.get_activity_name(999) == "Activity(999)"
    # 活动类型没有占位串，查不到就是空串
    assert manager.get_activity_type_name(999) == ""


def test_name_lookups_mark_a_definition_without_display_properties() -> None:
    manager = _manager(**{VENDOR_TABLE: [(1, json.dumps({}))]})

    assert manager.get_vendor_name(1) == "Vendor(1)"


def test_an_empty_name_is_returned_as_is_not_replaced_by_the_marker() -> None:
    """现状：占位串只在 name 这个键不存在时生效；键存在但为空串会原样返回。"""
    manager = _manager(
        **{
            VENDOR_TABLE: [(1, _named(""))],
            ACTIVITY_TYPE_TABLE: [(2, _named(""))],
        }
    )

    assert manager.get_vendor_name(1) == ""
    assert manager.get_activity_type_name(2) == ""


# ── iter_definitions ─────────────────────────────────────────────────


def test_iter_definitions_refuses_unknown_tables_and_missing_connections() -> None:
    assert _manager().iter_definitions("DestinySecretTable") == []
    assert ManifestManager().iter_definitions(VENDOR_TABLE) == []


def test_iter_definitions_clamps_limit_to_at_least_one_row() -> None:
    manager = _manager(**{VENDOR_TABLE: [(1, _named("甲")), (2, _named("乙"))]})

    assert len(manager.iter_definitions(VENDOR_TABLE, limit=0)) == 1
    assert len(manager.iter_definitions(VENDOR_TABLE, limit=1)) == 1
    assert len(manager.iter_definitions(VENDOR_TABLE, limit=100)) == 2


def test_iter_definitions_defaults_the_hash_from_the_row_id() -> None:
    manager = _manager(**{
        VENDOR_TABLE: [
            (1, json.dumps({"displayProperties": {"name": "甲"}})),
            (2, json.dumps({"hash": 999, "displayProperties": {"name": "乙"}})),
        ]
    })

    rows = manager.iter_definitions(VENDOR_TABLE)

    assert {row["hash"] for row in rows} == {1, 999}


def test_iter_definitions_skips_rows_with_broken_json() -> None:
    manager = _manager(**{VENDOR_TABLE: [(1, "{not json"), (2, _named("好的"))]})

    assert [row["hash"] for row in manager.iter_definitions(VENDOR_TABLE)] == [2]


def test_iter_definitions_prefers_the_chinese_connection() -> None:
    manager = _manager(**{VENDOR_TABLE: [(1, _named("english"))]})
    manager._zh_conn = _connection({VENDOR_TABLE: [(1, _named("中文"))]})

    assert manager.iter_definitions(VENDOR_TABLE)[0]["displayProperties"]["name"] == "中文"


# ── search_definitions_by_name ───────────────────────────────────────


def test_search_definitions_matches_name_or_description_case_insensitively() -> None:
    manager = _manager(**{
        VENDOR_TABLE: [
            (1, _named("Banshee-44", "Gunsmith")),
            (2, _named("萨瓦拉", "泰坦先锋")),
            (3, _named("浪客", "")),
        ]
    })

    assert [row["hash"] for row in manager.search_definitions_by_name(VENDOR_TABLE, "banshee")] == [1]
    assert [row["hash"] for row in manager.search_definitions_by_name(VENDOR_TABLE, "枪匠")] == []
    assert [row["hash"] for row in manager.search_definitions_by_name(VENDOR_TABLE, "先锋")] == [2]


def test_search_definitions_with_a_blank_query_returns_everything_up_to_the_limit() -> None:
    manager = _manager(**{
        VENDOR_TABLE: [(index, _named(f"商人{index}")) for index in range(1, 6)]
    })

    assert len(manager.search_definitions_by_name(VENDOR_TABLE, "")) == 5
    assert len(manager.search_definitions_by_name(VENDOR_TABLE, "", limit=2)) == 2


def test_search_definitions_returns_nothing_for_an_unknown_table() -> None:
    assert _manager().search_definitions_by_name("DestinySecretTable", "任意") == []


# ── find_collectible_by_item_hash ────────────────────────────────────


def test_find_collectible_returns_the_first_matching_entry() -> None:
    manager = _manager(**{
        COLLECTIBLE_TABLE: [
            (1, json.dumps({"itemHash": 500, "displayProperties": {"name": "第一个"}})),
            (2, json.dumps({"itemHash": 500, "displayProperties": {"name": "第二个"}})),
            (3, json.dumps({"itemHash": 600})),
        ]
    })

    found = manager.find_collectible_by_item_hash(500)

    assert found is not None
    assert found["displayProperties"]["name"] == "第一个"


def test_find_collectible_compares_hashes_as_integers() -> None:
    """定义里存的可能是字符串形式的 hash。"""
    manager = _manager(**{
        COLLECTIBLE_TABLE: [(1, json.dumps({"itemHash": "500"}))],
    })

    assert manager.find_collectible_by_item_hash(500) is not None


def test_find_collectible_is_none_without_a_match_or_a_connection() -> None:
    manager = _manager(**{COLLECTIBLE_TABLE: [(1, json.dumps({"itemHash": 500}))]})

    assert manager.find_collectible_by_item_hash(999) is None
    assert ManifestManager().find_collectible_by_item_hash(500) is None
