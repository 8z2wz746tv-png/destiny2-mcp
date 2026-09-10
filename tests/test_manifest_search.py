"""Manifest 名称索引与搜索的特征测试。

这是 `manifest.py` 按域拆分前的安全网：先钉住当前的索引结构与三层匹配优先级，
再把这几个方法整块搬走。断言的是现状。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from destiny_mcp.exceptions import ManifestError
from destiny_mcp.manifest import ITEM_ALIASES, ManifestManager
from destiny_mcp.utils.hash_utils import to_signed

UNSIGNED_HIGH_HASH = 4000000000
SIGNED_HIGH_HASH = to_signed(UNSIGNED_HIGH_HASH)


def _item_data(
    name: str,
    *,
    item_type: int = 3,
    tier: int = 5,
    display_type: str = "",
    icon: str = "",
    class_type: int = -1,
    ammo_type: int = 1,
    bucket: int = 1498876634,
) -> dict:
    return {
        "displayProperties": {"name": name, "icon": icon},
        "itemType": item_type,
        "itemTypeDisplayName": display_type,
        "inventory": {"tierType": tier, "bucketTypeHash": bucket},
        "classType": class_type,
        "defaultDamageType": 1,
        "equippingBlock": {"ammoType": ammo_type},
    }


def _connection(items: list[tuple[int, dict]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE DestinyInventoryItemDefinition (id INTEGER PRIMARY KEY, json TEXT)"
    )
    for item_hash, data in items:
        conn.execute(
            "INSERT INTO DestinyInventoryItemDefinition (id, json) VALUES (?, ?)",
            (item_hash, json.dumps(data, ensure_ascii=False)),
        )
    return conn


def _manager(
    english: list[tuple[int, dict]] | None = None,
    chinese: list[tuple[int, dict]] | None = None,
) -> ManifestManager:
    """直接喂索引，跳过下载与建库。"""
    manager = ManifestManager()
    if english:
        manager._build_name_index(_connection(english), language="en")
    if chinese:
        manager._build_name_index(_connection(chinese), language="zh-chs")
    return manager


# ── _build_name_index ────────────────────────────────────────────────


def test_name_index_keys_on_lowercased_name_and_skips_nameless_items() -> None:
    manager = _manager(english=[
        (100, _item_data("The Recluse", display_type="冲锋枪")),
        (101, _item_data("")),
    ])

    assert "the recluse" in manager._name_index
    assert len(manager._name_index["the recluse"]) == 1
    # 无名条目不进索引
    assert sum(len(v) for v in manager._name_index.values()) == 1


def test_name_index_keeps_a_list_so_duplicate_names_survive() -> None:
    """同名不同 hash 的装备必须都留着，否则搜索会静默漏掉一把。"""
    manager = _manager(english=[
        (100, _item_data("猎手的誓言")),
        (101, _item_data("猎手的誓言")),
    ])

    assert len(manager._name_index["猎手的誓言"]) == 2
    assert len(manager.search("猎手的誓言")) == 2


def test_english_pass_records_fields_the_chinese_pass_reuses() -> None:
    manager = _manager(english=[
        (100, _item_data("The Recluse", display_type="Submachine Gun")),
    ])

    assert manager._english_name_by_hash[100] == "The Recluse"
    assert manager._english_type_display_by_hash[100] == "Submachine Gun"
    # 有符号形式也记一份，Bungie 两种都会返回
    assert manager._english_name_by_hash[to_signed(100)] == "The Recluse"


def test_chinese_pass_wins_the_hash_index_but_keeps_both_name_keys() -> None:
    """中文后加载：hash 索引被中文覆盖，但中英两个名字都能搜到。"""
    manager = _manager(
        english=[(100, _item_data("The Recluse", display_type="Submachine Gun"))],
        chinese=[(100, _item_data("隐士", display_type="微型冲锋枪"))],
    )

    assert manager._hash_index[100]["name"] == "隐士"
    assert manager._hash_index[100]["language"] == "zh-chs"
    assert manager._hash_index[100]["nameEn"] == "The Recluse"
    assert {item["name"] for item in manager._name_index["the recluse"]} == {"The Recluse"}
    assert {item["name"] for item in manager._name_index["隐士"]} == {"隐士"}


def test_entry_carries_the_fields_search_results_depend_on() -> None:
    manager = _manager(english=[
        (100, _item_data("测试枪", item_type=3, tier=6, display_type="手炮", icon="/img/x.png")),
    ])

    entry = manager._name_index["测试枪"][0]
    assert entry["itemHash"] == 100
    assert entry["itemType"] == 3
    assert entry["tier"] == 6
    assert entry["itemTypeNameDisplay"] == "手炮"
    assert entry["icon"].endswith("/img/x.png")
    assert entry["language"] == "en"


# ── search ───────────────────────────────────────────────────────────


def test_search_requires_a_loaded_index() -> None:
    with pytest.raises(ManifestError, match="Manifest not loaded"):
        ManifestManager().search("anything")


def test_search_returns_nothing_for_a_blank_query() -> None:
    manager = _manager(english=[(100, _item_data("千语"))])

    assert manager.search("") == []
    assert manager.search("   ") == []


def test_search_orders_exact_then_prefix_then_substring() -> None:
    manager = _manager(english=[
        (1, _item_data("上古千语")),      # substring
        (2, _item_data("千语")),          # exact
        (3, _item_data("千语·变体")),     # prefix
    ])

    names = [item["name"] for item in manager.search("千语")]

    assert names == ["千语", "千语·变体", "上古千语"]


def test_search_sorts_within_a_tier_by_tier_then_name() -> None:
    """同一层里金装（tier 高）排前面，再按名字。"""
    manager = _manager(english=[
        (1, _item_data("甲千语", tier=5)),
        (2, _item_data("乙千语", tier=6)),
    ])

    names = [item["name"] for item in manager.search("千语")]

    assert names[0] == "乙千语"


def test_search_does_not_repeat_an_item_across_tiers() -> None:
    manager = _manager(english=[(1, _item_data("千语"))])

    assert len(manager.search("千语")) == 1


def test_search_treats_a_non_positive_limit_as_no_limit() -> None:
    manager = _manager(english=[
        (index, _item_data(f"千语{index}")) for index in range(5)
    ])

    assert len(manager.search("千语", limit=2)) == 2
    assert len(manager.search("千语", limit=0)) == 5


def test_search_expands_community_aliases() -> None:
    """社区昵称应能搜到官方名，具体别名取自数据，避免写死。"""
    alias, targets = next(iter(ITEM_ALIASES.items()))
    official = targets[0]
    manager = _manager(english=[(1, _item_data(official))])

    by_alias = [item["name"] for item in manager.search(alias)]
    by_official = [item["name"] for item in manager.search(official)]

    assert by_alias == [official]
    assert by_official == [official]


def test_canonical_entry_prefers_the_hash_index_and_backfills_english() -> None:
    manager = _manager(
        english=[(100, _item_data("The Recluse", display_type="Submachine Gun"))],
        chinese=[(100, _item_data("隐士", display_type="微型冲锋枪"))],
    )

    hit = manager.search("The Recluse")[0]

    assert hit["name"] == "隐士"
    assert hit["nameEn"] == "The Recluse"
    assert hit["itemTypeNameDisplayEn"] == "Submachine Gun"


# ── search_fuzzy ─────────────────────────────────────────────────────


def test_search_fuzzy_requires_a_loaded_index() -> None:
    with pytest.raises(ManifestError, match="Manifest not loaded"):
        ManifestManager().search_fuzzy("anything")


def test_search_fuzzy_matches_a_one_character_typo_and_reports_a_score() -> None:
    manager = _manager(english=[(1, _item_data("暮光壁垒"))])

    hits = manager.search_fuzzy("暮光壁磊")

    assert [item["name"] for item in hits] == ["暮光壁垒"]
    assert hits[0]["match_score"] >= 0.65


def test_search_fuzzy_drops_hits_below_the_score_floor() -> None:
    manager = _manager(english=[(1, _item_data("暮光壁垒"))])

    assert manager.search_fuzzy("完全不相干的东西") == []
    assert manager.search_fuzzy("完全不相干的东西", min_score=0.0) != []


def test_search_fuzzy_filters_by_type_tier_and_class() -> None:
    manager = _manager(english=[
        (1, _item_data("暮光壁垒", item_type=3, tier=5, class_type=-1)),
        (2, _item_data("暮光壁垒", item_type=2, tier=6, class_type=1)),
        (3, _item_data("暮光壁垒", item_type=2, tier=6, class_type=2)),
    ])

    assert len(manager.search_fuzzy("暮光壁垒", item_type=2)) == 2
    assert len(manager.search_fuzzy("暮光壁垒", tier=6)) == 2
    # classType -1 是通用件，任何职业都算合格
    assert len(manager.search_fuzzy("暮光壁垒", class_type=1)) == 2
    assert len(manager.search_fuzzy("暮光壁垒", class_type=3)) == 1


def test_search_fuzzy_keeps_the_best_score_per_hash() -> None:
    manager = _manager(english=[(1, _item_data("暮光壁垒"))])

    hits = manager.search_fuzzy("暮光壁垒")

    assert len(hits) == 1
    assert hits[0]["match_score"] == 1.0


# ── search_by_type_name ──────────────────────────────────────────────


def test_search_by_type_name_matches_display_or_internal_type() -> None:
    manager = _manager(english=[
        (1, _item_data("甲", item_type=3, display_type="手炮")),
        (2, _item_data("乙", item_type=3, display_type="自动步枪")),
    ])

    assert [item["name"] for item in manager.search_by_type_name("手炮")] == ["甲"]
    assert [item["name"] for item in manager.search_by_type_name("")] == []


def test_search_by_type_name_deduplicates_and_honours_limit() -> None:
    manager = _manager(
        english=[(1, _item_data("甲", display_type="手炮"))],
        chinese=[(1, _item_data("甲中", display_type="手炮"))],
    )

    assert len(manager.search_by_type_name("手炮")) == 1
    assert len(manager.search_by_type_name("手炮", limit=0)) == 1


# ── get_item_name ────────────────────────────────────────────────────


def test_get_item_name_falls_back_to_a_marked_hash() -> None:
    assert ManifestManager().get_item_name(12345) == "#12345"

    manager = _manager(english=[(100, _item_data("隐士"))])
    assert manager.get_item_name(100) == "隐士"
    assert manager.get_item_name(999) == "#999"


def test_get_item_name_accepts_the_unsigned_form_of_a_signed_hash() -> None:
    """Manifest 存有符号 32 位 hash，Bungie API 返回无符号。"""
    manager = _manager(english=[
        (SIGNED_HIGH_HASH, _item_data("高位装备")),
    ])

    assert manager.get_item_name(UNSIGNED_HIGH_HASH) == "高位装备"
    assert manager.get_item_name(SIGNED_HIGH_HASH) == "高位装备"
