"""Starside 实体数据（作者给的导出，转成我们入库的紧凑文件）的守门。

这些测试**只用仓库里已提交的文件**，不碰真机、不碰 Manifest —— 干净 HOME 下也要过
（跟 Manifest 对 hash 的那部分在 `scripts/audit_starside_entities.py`，那是真机审计）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from destiny_mcp.services.starside_entities import StarsideEntities
from destiny_mcp.utils.hash_utils import to_unsigned

ENTITIES = Path(__file__).parents[1] / "data" / "starside" / "entities"


@pytest.fixture(scope="module")
def payloads() -> tuple[dict, dict]:
    items = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))
    perks = json.loads((ENTITIES / "perks.json").read_text(encoding="utf-8"))
    return items, perks


def test_keys_are_unsigned_and_meta_is_intact(payloads) -> None:
    """键一律无符号；来源元数据必须齐全（社区资料没有出处就不许出去）。"""
    items, perks = payloads
    for name, body in (("items", items["items"]), ("perks", perks["perks"])):
        for key in body:
            assert key.isdigit(), f"{name} 的键必须是无符号整数字符串：{key}"
            assert str(to_unsigned(int(key))) == key, f"{name} 的键没归一：{key}"
    meta = items["_meta"]
    assert meta["source"] == "starside.work"
    assert meta["unofficial"] is True
    assert meta["snapshot_at"] and meta["authors"] == ["Aegis", "LGpig"]
    assert meta["source_files"] and all(f["sha256"] for f in meta["source_files"])


def test_meta_counts_and_coverage_match_the_data(payloads) -> None:
    """覆盖表是 ratchet：数据缩水必须是一次显式修改，不能被顺手带过。"""
    items, perks = payloads
    meta = items["_meta"]
    assert meta["counts"]["items"] == len(items["items"])
    assert meta["counts"]["perks"] == len(perks["perks"])
    assert meta["counts"]["items_with_author"] == sum(
        1 for e in items["items"].values() if (e.get("zh") or {}).get("site_authors")
    )
    for field, expected in meta["coverage"]["items"].items():
        actual = sum(
            1
            for e in items["items"].values()
            if e.get(field) or (e.get("derived") or {}).get(field) or (e.get("zh") or {}).get(field)
        )
        assert actual == expected, f"items.{field} 覆盖数对不上：{actual} != {expected}"
    for field, expected in meta["coverage"]["perks"].items():
        actual = sum(
            1 for e in perks["perks"].values() if e.get(field) or (e.get("zh") or {}).get(field)
        )
        assert actual == expected, f"perks.{field} 覆盖数对不上：{actual} != {expected}"


def test_references_are_well_formed_and_counts_reconcile(payloads) -> None:
    """引用必须**格式合法**，且"文件内可解析的边数"要对账。

    注意一个刻意的设计：我们只收"有社区字段"的物品（5,760 → 4,483），所以会有边指向本文件里
    没有条目的物品 —— 那些靠**我们自己的 Manifest** 解释（真机审计
    `scripts/audit_starside_entities.py` 验的就是"每条引用都能在 Manifest 里解出"）。
    导入前实测原始导出是 6,134/6,134 全在文件内，过滤后降到 5,212：这个数字写进 `_meta`，
    缩水必须是一次显式修改。
    """
    items, perks = payloads
    item_ids, perk_ids = set(items["items"]), set(perks["perks"])

    for key, entry in perks["perks"].items():
        for target in entry.get("onItems") or []:
            assert int(target) > 0 and str(to_unsigned(int(target))) == str(to_unsigned(int(target)))
    for key, entry in items["items"].items():
        if entry.get("sameAs"):
            assert str(to_unsigned(int(entry["sameAs"]))) in item_ids, f"{key} 的 sameAs 该在文件里"
        for column in entry.get("site_perkColumns") or []:
            for perk_hash in column:
                assert str(to_unsigned(int(perk_hash))) in perk_ids | item_ids, f"{key} 的栏位 perk 越界"
        for row in entry.get("enhanced") or []:
            for perk_hash in row.get("by") or []:
                assert str(to_unsigned(int(perk_hash))) in perk_ids | item_ids, f"{key} 的 enhanced.by 越界"

    counts = items["_meta"]["counts"]
    assert counts["onItems_edges"] == sum(len(e.get("onItems") or []) for e in perks["perks"].values())
    assert counts["onItems_edges_in_file"] == sum(
        1
        for e in perks["perks"].values()
        for h in (e.get("onItems") or [])
        if str(to_unsigned(int(h))) in item_ids
    )
    assert counts["sameAs_in_file"] == sum(1 for e in items["items"].values() if e.get("sameAs"))


def test_lookups_accept_both_hash_spellings(payloads) -> None:
    """有符号/无符号两种写法都要查得到 —— 今天真机踩过两次的坑。"""
    items, _ = payloads
    entity = StarsideEntities()
    big = next(k for k in items["items"] if int(k) > 2**31)  # 有符号写法是负数的那种
    unsigned, signed = int(big), int(big) - 2**32
    assert entity.item(unsigned) is not None
    assert entity.item(signed) == entity.item(unsigned)
    small = next(k for k in items["items"] if int(k) < 2**31)
    assert entity.item(int(small)) == entity.item(str(int(small)))


def test_queries_are_silent_when_data_or_entry_is_missing(tmp_path) -> None:
    """查不到就给 None/空元组（可选资料降级不炸主流程），并把原因留在 load_problem。"""
    empty = StarsideEntities(root=tmp_path / "nope")
    assert empty.available() is False
    assert "社区实体数据缺失" in empty.load_problem
    assert empty.item(123) is None and empty.perk(123) is None
    assert empty.items_with_perk(123) == () and empty.frame_stats_for_weapon(123) == ()
    assert empty.attribution()["available"] is False
    assert empty.attribution()["unofficial"] is True

    loaded = StarsideEntities()
    assert loaded.available() is True
    assert loaded.load_problem == ""
    assert loaded.item(999_999_999_999) is None
    assert loaded.items_with_perk(999_999_999_999) == ()


def test_real_links_work_end_to_end(payloads) -> None:
    """三条真实链路：perk 反查、套装关联、武器→框架→帧表（hash join）。"""
    items, _ = payloads
    entity = StarsideEntities()

    perk_key = next(k for k, v in payloads[1]["perks"].items() if v.get("onItems"))
    assert entity.items_with_perk(perk_key), "perk 反查应当有结果"

    set_key = next(k for k, v in payloads[1]["perks"].items() if v.get("onSets"))
    assert entity.sets_of_perk(set_key), "套装关联应当有结果"

    weapon = next(
        k
        for k, v in items["items"].items()
        if (v.get("derived") or {}).get("archetype") and entity.frame_stats_for_weapon(k)
    )
    rows = entity.frame_stats_for_weapon(weapon)
    assert rows and "base_damage" in rows[0], "武器 → 框架 → 帧表这条 join 断了"
    assert entity.frame_stats_for_weapon(999_999_999_999) == ()

    mod = next(k for k, v in items["items"].items() if v.get("site_artifact"))
    assert entity.artifact_of(mod) == to_unsigned(int(items["items"][mod]["site_artifact"]))
