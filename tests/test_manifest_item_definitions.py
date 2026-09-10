"""Manifest 物品定义查询的特征测试。

覆盖 hash 索引查询、中英连接的优先与回退、有符号/无符号 hash、定义缓存的键，
以及坏 JSON 行不能把查询炸掉。拆分 manifest.py 前先钉住这些行为。
"""

from __future__ import annotations

import json
import sqlite3

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

UNSIGNED_HIGH_HASH = 4000000000
SIGNED_HIGH_HASH = to_signed(UNSIGNED_HIGH_HASH)

ITEM_TABLE = "DestinyInventoryItemDefinition"
PERK_TABLE = "DestinySandboxPerkDefinition"


def _table(name: str, rows: list[tuple[int, str]]) -> sqlite3.Connection:
    """建一个只有 id/json 两列的表；json 以原始字符串写入，便于塞坏数据。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, json TEXT)")
    for item_hash, raw in rows:
        conn.execute(f"INSERT INTO {name} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _definition(description: str = "", name: str = "物品", **extra) -> str:
    return json.dumps(
        {
            "displayProperties": {"name": name, "description": description},
            "itemType": 3,
            **extra,
        },
        ensure_ascii=False,
    )


def _manager(
    *,
    english: list[tuple[int, str]] | None = None,
    chinese: list[tuple[int, str]] | None = None,
    table: str = ITEM_TABLE,
) -> ManifestManager:
    manager = ManifestManager()
    manager._conn = _table(table, english or [])
    manager._zh_conn = _table(table, chinese or []) if chinese is not None else None
    return manager


# ── get_item_info ────────────────────────────────────────────────────


def test_get_item_info_reads_the_name_index_by_either_hash_form() -> None:
    manager = ManifestManager()
    manager._build_name_index(
        _table(ITEM_TABLE, [(SIGNED_HIGH_HASH, _definition(name="高位装备"))]),
        language="en",
    )

    assert manager.get_item_info(SIGNED_HIGH_HASH)["name"] == "高位装备"
    assert manager.get_item_info(UNSIGNED_HIGH_HASH)["name"] == "高位装备"
    assert manager.get_item_info(123456) is None


# ── get_item_description ─────────────────────────────────────────────


def test_get_item_description_returns_the_localized_text() -> None:
    manager = _manager(english=[(100, _definition(description="一段说明"))])

    assert manager.get_item_description(100) == "一段说明"


def test_get_item_description_is_blank_for_missing_or_malformed_fields() -> None:
    manager = _manager(english=[
        (100, json.dumps({"displayProperties": "not-a-dict"})),
        (101, json.dumps({"displayProperties": {"description": 42}})),
        (102, json.dumps({})),
    ])

    assert manager.get_item_description(999) == ""
    assert manager.get_item_description(100) == ""
    assert manager.get_item_description(101) == ""
    assert manager.get_item_description(102) == ""


# ── _query_json ──────────────────────────────────────────────────────


def test_query_json_refuses_tables_outside_the_whitelist() -> None:
    manager = _manager(english=[(100, _definition())])

    assert manager._query_json("DestinySecretTable", 100) is None


def test_query_json_prefers_the_chinese_connection() -> None:
    manager = _manager(
        english=[(100, _definition(description="english"))],
        chinese=[(100, _definition(description="中文"))],
    )

    assert manager._query_json(ITEM_TABLE, 100)["displayProperties"]["description"] == "中文"


def test_query_json_falls_back_to_english_when_chinese_lacks_the_row() -> None:
    manager = _manager(
        english=[(100, _definition(description="english"))],
        chinese=[(200, _definition(description="其他"))],
    )

    assert manager._query_json(ITEM_TABLE, 100)["displayProperties"]["description"] == "english"


def test_query_json_accepts_the_unsigned_form_of_a_signed_row() -> None:
    manager = _manager(english=[(SIGNED_HIGH_HASH, _definition(name="高位装备"))])

    assert manager._query_json(ITEM_TABLE, UNSIGNED_HIGH_HASH)["displayProperties"]["name"] == "高位装备"


def test_query_json_survives_a_row_with_broken_json() -> None:
    """坏 JSON 只该让这一行落空，不能把查询抛出去。"""
    manager = _manager(english=[(100, "{not json")])

    assert manager._query_json(ITEM_TABLE, 100) is None


def test_query_json_uses_the_table_it_was_given() -> None:
    manager = _manager(english=[(100, _definition())], table=PERK_TABLE)

    assert manager._query_json(PERK_TABLE, 100) is not None
    # 表存在但查的是另一张白名单内的表，应当查不到
    assert manager._query_json(ITEM_TABLE, 100) is None


# ── _query_json_from_conn ────────────────────────────────────────────


def test_query_json_from_conn_needs_a_connection_and_a_known_table() -> None:
    manager = _manager(english=[(100, _definition())])

    assert manager._query_json_from_conn(ITEM_TABLE, 100, None) is None
    assert manager._query_json_from_conn("DestinySecretTable", 100, manager._conn) is None
    assert manager._query_json_from_conn(ITEM_TABLE, 100, manager._conn) is not None


def test_query_json_from_conn_only_reads_the_given_connection() -> None:
    manager = _manager(
        english=[(100, _definition(description="english"))],
        chinese=[(100, _definition(description="中文"))],
    )

    english = manager._query_json_from_conn(ITEM_TABLE, 100, manager._conn)
    chinese = manager._query_json_from_conn(ITEM_TABLE, 100, manager._zh_conn)

    assert english["displayProperties"]["description"] == "english"
    assert chinese["displayProperties"]["description"] == "中文"


# ── get_item_definition ──────────────────────────────────────────────


def test_get_item_definition_caches_under_both_hash_forms() -> None:
    manager = _manager(english=[(SIGNED_HIGH_HASH, _definition(name="高位装备"))])

    first = manager.get_item_definition(UNSIGNED_HIGH_HASH)

    assert first is not None
    # 断掉连接后仍能命中缓存，说明两个键都写进去了
    manager._conn = None
    manager._zh_conn = None
    assert manager.get_item_definition(UNSIGNED_HIGH_HASH) is first
    assert manager.get_item_definition(SIGNED_HIGH_HASH) is first


def test_get_item_definition_is_none_when_the_row_is_absent() -> None:
    manager = _manager(english=[(100, _definition())])

    assert manager.get_item_definition(999) is None


# ── get_item_definition_by_name ──────────────────────────────────────


def test_get_item_definition_by_name_searches_then_loads() -> None:
    manager = _manager(english=[(100, _definition(name="千语"))])
    manager._build_name_index(manager._conn, language="en")

    definition = manager.get_item_definition_by_name("千语")

    assert definition is not None
    assert definition["displayProperties"]["name"] == "千语"


def test_get_item_definition_by_name_is_none_when_the_name_is_unknown() -> None:
    manager = _manager(english=[(100, _definition(name="千语"))])
    manager._build_name_index(manager._conn, language="en")

    assert manager.get_item_definition_by_name("不存在的名字") is None
