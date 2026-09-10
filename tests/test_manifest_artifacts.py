"""Manifest 赛季神器查询的特征测试。

覆盖 `get_all_artifacts`、`get_artifact_by_name`、`get_current_artifact`、
`get_artifact_by_hash`、`_parse_artifact`、`get_artifact_mod_details`。

注意两处现状：神器列表靠 JSON 子串匹配（依赖紧凑序列化），以及按 hash 查询时
只试有符号形式；都已在下面显式记录。
"""

from __future__ import annotations

import json
import sqlite3

from destiny_mcp.manifest import ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

ITEM_TABLE = "DestinyInventoryItemDefinition"
ARTIFACT_TABLE = "DestinyArtifactDefinition"
PERK_TABLE = "DestinySandboxPerkDefinition"

ARTIFACT_BUCKET = 1506418338


def _compact(data: dict) -> str:
    """紧凑 JSON —— LIKE '%itemType":28%' 依赖 `":` 之间没有空格。"""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _connection(tables: dict[str, list[tuple[int, str]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, rows in tables.items():
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, json TEXT)")
        for item_hash, raw in rows:
            conn.execute(f"INSERT INTO {table} (id, json) VALUES (?, ?)", (item_hash, raw))
    return conn


def _artifact_item(name: str, description: str = "", bucket: int = ARTIFACT_BUCKET) -> str:
    return _compact({
        "displayProperties": {"name": name, "description": description},
        "itemType": 28,
        "inventory": {"bucketTypeHash": bucket},
    })


def _artifact_definition(
    name: str,
    description: str = "",
    *,
    tiers: list[dict] | None = None,
) -> str:
    return _compact({
        "displayProperties": {"name": name, "description": description},
        "tiers": tiers or [],
    })


def _manager(
    *,
    items: list[tuple[int, str]] | None = None,
    artifacts: list[tuple[int, str]] | None = None,
    perks: list[tuple[int, str]] | None = None,
    english_only: bool = False,
) -> ManifestManager:
    tables: dict[str, list[tuple[int, str]]] = {}
    if items is not None:
        tables[ITEM_TABLE] = items
    if artifacts is not None:
        tables[ARTIFACT_TABLE] = artifacts
    if perks is not None:
        tables[PERK_TABLE] = perks
    manager = ManifestManager()
    if english_only:
        manager._conn = _connection(tables)
    else:
        manager._zh_conn = _connection(tables)
    return manager


# ── get_all_artifacts ────────────────────────────────────────────────


def test_all_artifacts_is_empty_without_a_connection() -> None:
    assert ManifestManager().get_all_artifacts() == []


def test_all_artifacts_filters_by_bucket_and_item_type() -> None:
    manager = _manager(items=[
        (1, _artifact_item("神器甲")),
        (2, _artifact_item("别的装备", bucket=999)),          # 不是神器桶
        (3, _compact({"displayProperties": {"name": "枪"}, "itemType": 3,
                      "inventory": {"bucketTypeHash": ARTIFACT_BUCKET}})),
    ])

    assert [item["name"] for item in manager.get_all_artifacts()] == ["神器甲"]


def test_all_artifacts_skips_nameless_and_duplicate_entries() -> None:
    manager = _manager(items=[
        (1, _artifact_item("")),
        (2, _artifact_item("神器甲")),
        (3, _artifact_item("神器甲")),
    ])

    assert [item["name"] for item in manager.get_all_artifacts()] == ["神器甲"]


def test_all_artifacts_truncates_the_description_to_100_characters() -> None:
    manager = _manager(items=[(1, _artifact_item("神器甲", "字" * 150))])

    assert len(manager.get_all_artifacts()[0]["description"]) == 100


def test_all_artifacts_sorts_by_name_and_prefers_the_chinese_connection() -> None:
    english = _manager(
        items=[(1, _artifact_item("english"))], english_only=True
    )
    english._zh_conn = _connection({ITEM_TABLE: [(1, _artifact_item("中文"))]})

    assert [item["name"] for item in english.get_all_artifacts()] == ["中文"]


# ── get_artifact_by_name ─────────────────────────────────────────────


def test_artifact_by_name_is_none_without_a_connection() -> None:
    assert ManifestManager().get_artifact_by_name("任意") is None


def test_artifact_by_name_prefers_an_exact_match_over_a_substring() -> None:
    """先整表扫一遍精确匹配，再扫一遍模糊匹配，所以顺序不影响结果。"""
    manager = _manager(items=[
        (1, _artifact_item("好奇之器·测试版")),
        (2, _artifact_item("好奇之器")),
    ])

    found = manager.get_artifact_by_name("好奇之器")

    assert found is not None
    assert found["hash"] == 2
    assert found["name"] == "好奇之器"


def test_artifact_by_name_matches_a_substring_in_either_direction() -> None:
    manager = _manager(items=[(1, _artifact_item("好奇之器"))])

    assert manager.get_artifact_by_name("好奇")["name"] == "好奇之器"
    assert manager.get_artifact_by_name("好奇之器·外传")["name"] == "好奇之器"


def test_artifact_by_name_returns_the_full_description() -> None:
    """列表接口截断到 100 字，按名查询不截断。"""
    manager = _manager(items=[(1, _artifact_item("神器甲", "字" * 150))])

    assert len(manager.get_artifact_by_name("神器甲")["description"]) == 150


def test_artifact_by_name_is_none_when_nothing_matches() -> None:
    manager = _manager(items=[(1, _artifact_item("神器甲"))])

    assert manager.get_artifact_by_name("不相干") is None


# ── get_current_artifact / get_artifact_by_hash ──────────────────────


def test_current_artifact_is_none_without_a_connection_or_rows() -> None:
    assert ManifestManager().get_current_artifact() is None
    assert _manager(artifacts=[]).get_current_artifact() is None


def test_current_artifact_picks_the_highest_id() -> None:
    manager = _manager(artifacts=[
        (100, _artifact_definition("旧神器")),
        (300, _artifact_definition("新神器")),
        (200, _artifact_definition("中间神器")),
    ])

    assert manager.get_current_artifact()["name"] == "新神器"


def test_artifact_by_hash_only_tries_the_signed_form() -> None:
    """现状：查询用 to_signed(hash) 作唯一条件，不像别处两个符号都试。

    所以只有以有符号形式存的行才查得到。
    """
    signed_id = to_signed(4000000000)
    manager = _manager(artifacts=[(signed_id, _artifact_definition("高位神器"))])

    assert manager.get_artifact_by_hash(4000000000)["name"] == "高位神器"
    assert manager.get_artifact_by_hash(signed_id)["name"] == "高位神器"


def test_artifact_by_hash_is_none_for_an_unknown_or_unsigned_only_row() -> None:
    manager = _manager(artifacts=[(4000000000, _artifact_definition("无符号存的行"))])

    assert manager.get_artifact_by_hash(4000000000) is None
    assert manager.get_artifact_by_hash(123) is None


# ── _parse_artifact ──────────────────────────────────────────────────


def _tier(tier_hash: int, title: str, points: int, mod_hashes: list[int]) -> dict:
    return {
        "tierHash": tier_hash,
        "displayTitle": title,
        "minimumUnlockPointsUsedRequirement": points,
        "items": [{"itemHash": h} for h in mod_hashes],
    }


def test_parse_artifact_reads_tier_fields_and_resolves_mods() -> None:
    mod_unsigned = 3000000000
    manager = _manager(
        items=[(to_signed(mod_unsigned), _compact({
            "displayProperties": {"name": "模组甲", "description": "效果说明"},
            "itemType": 19,
        }))],
    )

    parsed = manager._parse_artifact(42, json.loads(_artifact_definition(
        "好奇之器", "神器说明", tiers=[_tier(7, "1阶", 0, [mod_unsigned])]
    )))

    assert parsed["hash"] == 42
    assert parsed["name"] == "好奇之器"
    assert parsed["tiers"] == [{
        "tier_hash": 7,
        "display_title": "1阶",
        "min_unlock_points": 0,
        "mods": [{"name": "模组甲", "hash": mod_unsigned, "description": "效果说明"}],
    }]


def test_parse_artifact_drops_mods_whose_definition_is_missing() -> None:
    """模组解析不到定义时整条丢掉，而不是留个空壳。"""
    parsed = ManifestManager()._parse_artifact(42, json.loads(_artifact_definition(
        "好奇之器", tiers=[_tier(7, "1阶", 0, [111, 222])]
    )))

    assert parsed["tiers"][0]["mods"] == []


def test_parse_artifact_falls_back_to_perk_descriptions_for_mods() -> None:
    mod_unsigned = 3000000000
    manager = _manager(
        items=[(to_signed(mod_unsigned), _compact({
            "displayProperties": {"name": "模组甲"},
            "itemType": 19,
            "perks": [{"perkHash": 555}],
        }))],
        perks=[(555, _compact({"displayProperties": {"description": "来自 Perk 的说明"}}))],
    )

    parsed = manager._parse_artifact(42, json.loads(_artifact_definition(
        "好奇之器", tiers=[_tier(7, "1阶", 0, [mod_unsigned])]
    )))

    assert parsed["tiers"][0]["mods"][0]["description"] == "来自 Perk 的说明"


def test_parse_artifact_tolerates_a_definition_without_tiers() -> None:
    parsed = ManifestManager()._parse_artifact(42, {"displayProperties": {"name": "空神器"}})

    assert parsed == {"name": "空神器", "description": "", "hash": 42, "tiers": []}


# ── get_artifact_mod_details ─────────────────────────────────────────


def test_artifact_mod_details_is_none_without_a_definition() -> None:
    assert ManifestManager().get_artifact_mod_details(123) is None


def test_artifact_mod_details_collects_perks() -> None:
    manager = _manager(
        items=[(100, _compact({
            "displayProperties": {"name": "模组甲", "description": "主说明"},
            "itemType": 19,
            "perks": [{"perkHash": 555}, {"perkHash": 556}],
        }))],
        perks=[
            (555, _compact({"displayProperties": {"name": "Perk 甲", "description": "说明甲"}})),
            (556, _compact({"displayProperties": {"name": "Perk 乙", "description": "说明乙"}})),
        ],
    )

    details = manager.get_artifact_mod_details(100)

    assert details == {
        "name": "模组甲",
        "hash": 100,
        "description": "主说明",
        "perks": [
            {"name": "Perk 甲", "description": "说明甲"},
            {"name": "Perk 乙", "description": "说明乙"},
        ],
    }


def test_artifact_mod_details_falls_back_to_joined_perk_descriptions() -> None:
    manager = _manager(
        items=[(100, _compact({
            "displayProperties": {"name": "模组甲"},
            "itemType": 19,
            "perks": [{"perkHash": 555}, {"perkHash": 556}],
        }))],
        perks=[
            (555, _compact({"displayProperties": {"description": "说明甲"}})),
            (556, _compact({"displayProperties": {"description": "说明乙"}})),
        ],
    )

    assert manager.get_artifact_mod_details(100)["description"] == "说明甲; 说明乙"


def test_artifact_mod_details_keeps_an_empty_description_without_perks() -> None:
    manager = _manager(items=[(100, _compact({
        "displayProperties": {"name": "模组甲"}, "itemType": 19,
    }))])

    details = manager.get_artifact_mod_details(100)

    assert details["description"] == ""
    assert details["perks"] == []
