"""`ManifestManager.get_activity_mode_name` 的特征测试（真 sqlite，不用替身）。

为什么必须用真连接：这个方法以前**全仓没有一条测试真的调用它** —— 单测处处用
`MagicMock`（`get_activity_mode_name.side_effect = {...}`），于是"索引建不起来"这种
故障会一路静默：全站模式名退化成 `模式43`，而 1596 条测试全绿。

真机教训（2026-09-18）：把索引打断后跑相关 52 条单测，**没有一条变红**。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from destiny_mcp.manifest import ManifestManager

TABLE = "DestinyActivityModeDefinition"


def _mode_row(mode_type: int, name: str) -> str:
    return json.dumps(
        {"modeType": mode_type, "displayProperties": {"name": name}}, ensure_ascii=False
    )


def _connection(rows: list[tuple[int, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, json TEXT)")
    for item_hash, raw in rows:
        conn.execute(f"INSERT INTO {TABLE} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _manager(rows: list[tuple[int, str]], *, zh: bool = True) -> ManifestManager:
    manager = ManifestManager()
    if zh:
        manager._zh_conn = _connection(rows)
    else:
        manager._conn = _connection(rows)
    return manager


def test_mode_names_come_from_the_manifest() -> None:
    manager = _manager([
        (1, _mode_row(5, "熔炉竞技场")),
        (2, _mode_row(43, "铁旗占领模式")),
        (3, _mode_row(84, "奥斯里斯试炼")),
    ])

    assert manager.get_activity_mode_name(5) == "熔炉竞技场"
    assert manager.get_activity_mode_name(43) == "铁旗占领模式"
    assert manager.get_activity_mode_name(84) == "奥斯里斯试炼"


def test_unknown_mode_type_is_empty_not_invented() -> None:
    """查不到就是 `""`（调用方降级成 `模式<号>`），不编一个名字。"""
    manager = _manager([(1, _mode_row(5, "熔炉竞技场"))])
    assert manager.get_activity_mode_name(9999) == ""


def test_without_any_connection_it_degrades_loudly(caplog) -> None:
    """没有连接 → 返回 `""`，并且**必须留下 warning**（以前只有 debug，界面会突然全是"模式43"）。"""
    manager = ManifestManager()

    with caplog.at_level(logging.WARNING):
        assert manager.get_activity_mode_name(5) == ""

    assert any("模式名索引为空" in record.getMessage() for record in caplog.records)


def test_missing_table_degrades_loudly_and_does_not_raise(caplog) -> None:
    """表不存在（表名改错/Manifest 版本不对）→ 不抛异常，但要留下 warning。"""
    manager = ManifestManager()
    manager._zh_conn = sqlite3.connect(":memory:")
    manager._zh_conn.row_factory = sqlite3.Row

    with caplog.at_level(logging.WARNING):
        assert manager.get_activity_mode_name(43) == ""

    assert any("模式名索引为空" in record.getMessage() for record in caplog.records)


def test_row_without_row_factory_does_not_blank_the_whole_index(caplog) -> None:
    """连接忘了设 `row_factory` 时 `row["json"]` 抛 `KeyError`。

    以前那个 `KeyError` 会冒到调用方（把整条 history 带崩）；现在只跳过这一行。
    """
    manager = ManifestManager()
    conn = sqlite3.connect(":memory:")          # 故意不设 row_factory
    conn.execute(f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, json TEXT)")
    conn.execute(f"INSERT INTO {TABLE} (id, json) VALUES (1, ?)", (_mode_row(5, "熔炉竞技场"),))
    manager._zh_conn = conn

    with caplog.at_level(logging.WARNING):
        assert manager.get_activity_mode_name(5) == ""

    assert any("模式名索引为空" in record.getMessage() for record in caplog.records)


def test_broken_rows_do_not_break_the_working_ones() -> None:
    """坏行（非法 JSON / 合法 JSON 但不是 dict / modeType 不是数字）只跳过自己。

    "合法 JSON 但不是 dict"那条以前抛 `AttributeError`（`.get` 不存在），
    `except (JSONDecodeError, TypeError)` 接不住 → 每次调用都带崩整条 history。
    """
    manager = _manager([
        (1, "{ 不是 JSON"),
        (2, '["not", "a", "dict"]'),
        (3, json.dumps({"displayProperties": {"name": "没有 modeType"}})),
        (4, json.dumps({"modeType": "5", "displayProperties": {"name": "modeType 是字符串"}})),
        (5, json.dumps({"modeType": 5, "displayProperties": {"name": "   "}})),
        (6, _mode_row(43, "铁旗占领模式")),
    ])

    assert manager.get_activity_mode_name(43) == "铁旗占领模式"
    assert manager.get_activity_mode_name(5) == ""


def test_zh_wins_and_en_fills_the_gaps() -> None:
    """zh 先建、en 只补缺：中文缺哪个模式就用英文兜底那个。"""
    manager = ManifestManager()
    manager._zh_conn = _connection([(1, _mode_row(5, "熔炉竞技场"))])
    manager._conn = _connection([(1, _mode_row(5, "Crucible")), (2, _mode_row(84, "Trials of Osiris"))])

    assert manager.get_activity_mode_name(5) == "熔炉竞技场"
    assert manager.get_activity_mode_name(84) == "Trials of Osiris"
