"""Offline contracts for the optional Starside knowledge layer."""

from __future__ import annotations

from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.exceptions import ConfigError
from destiny_mcp.services.starside_builds import parse_build
from destiny_mcp.services.starside_matching import match_inventory, validate_build
from destiny_mcp.services.starside_service import StarsideService
from destiny_mcp.tools.assistants import build_assistant
from destiny_mcp.tools.assistants import (
    weapon_assistant,
    subclass_assistant,
    activity_assistant,
    world_assistant,
)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _archive(
    root: Path, *, blocks: list[str] | None = None, second_page: bool = False
) -> Path:
    blocks = blocks or []
    pages = [
        ("weapons", "weapon-perks/index.html", "records/weapon-perks/index.json", []),
        ("builds", "builds/a/index.html", "records/builds/a/index.json", blocks),
    ]
    if second_page:
        pages.append(
            (
                "builds",
                "builds/b/index.html",
                "records/builds/b/index.json",
                [blocks[0].replace("Build A", "Build B")],
            )
        )
    categories: dict[str, list[dict]] = {}
    for category, url, record_path, source_blocks in pages:
        html_hash = sha256(url.encode()).hexdigest()
        record = {
            "url": "https://starside.work/" + url,
            "title": url,
            "description": "sample",
            "category": category,
            "updated_at": "2026.9.9",
            "text": "辉耀炽热 sample page",
            "source_blocks": source_blocks,
            "tables": [
                {
                    "caption": "DPS",
                    "rows": [
                        [
                            {
                                "tag": "th",
                                "text": "值",
                                "html": "值",
                                "attrs": {"rowspan": "2"},
                            },
                            {
                                "tag": "td",
                                "text": "10 [5] ?",
                                "html": '10 <span class="pvp">[5]</span> <span class="unsure">?</span>',
                                "attrs": {"colspan": "2"},
                            },
                        ]
                    ],
                }
            ],
            "external_links": ["https://docs.example/guide"],
            "archive": {"fetched_at": "2026-09-09T00:00:00Z", "sha256": html_hash},
        }
        _write(root / record_path, record)
        categories.setdefault(category, []).append(
            {
                "url": record["url"],
                "record": record_path,
                "source_blocks": len(source_blocks),
            }
        )
    _write(
        root / "exports/starsideIndex.json",
        [
            {
                "u": "weapon-perks/index.html",
                "a": "perk-1",
                "l": "Perk",
                "n": "辉耀炽热",
                "x": "普通摘要",
            },
        ],
    )
    _write(
        root / "exports/starsideDesc.json",
        {
            "weapon-perks\t辉耀炽热\tPerk": '<p>爆炸 <span class="pvp">[5]</span> <span class="enh">+10</span> <span class="unsure">?</span></p><script>ignore()</script>',
        },
    )
    _write(
        root / "index.json",
        {
            "schema_version": 2,
            "provider": "starside",
            "status": "complete",
            "updated_at": "2026-09-09T01:00:00Z",
            "categories": categories,
            "failures": [],
            "pending_urls": [],
            "public_exports": [
                "exports/starsideIndex.json",
                "exports/starsideDesc.json",
            ],
            "crawler": {"pending": 0, "pages_saved": len(pages)},
        },
    )
    return root


BUILD = """# Build A
推荐人：作者
更新：2026.9.9
类别：meta
## 审核意见
类别：不应覆盖
## 职业
职业：猎人
超能：测试超能
## 武器
传说武器：测试武器 | 测试 Perk
## 护甲
异域护甲：测试金装
套装：甲套 2 件 × 甲套 4 件 × 乙套 2 件
头盔：测试模组、测试模组
## 六维
六维：生命 ~ ｜ 近战 70 ｜ 手雷 70+ ｜ 超能 80～100 ｜ 武器 300
## 注解
测试武器可按条件替换
"""


def test_build_parser_preserves_semantics_and_does_not_overwrite_from_review() -> None:
    build = parse_build(BUILD, build_id="a", source={"title": "fallback"})

    assert build["category"] == "meta"
    assert build["review_notes"] == ["类别：不应覆盖"]
    assert build["armor"]["mods"]["helmet"] == ["测试模组", "测试模组"]
    assert build["armor"]["set_requirements"] == [
        {"name": "甲套", "count": 4},
        {"name": "乙套", "count": 2},
    ]
    assert build["stat_targets"]["health"]["kind"] == "unspecified"
    assert build["stat_targets"]["melee"]["kind"] == "target"
    assert build["stat_targets"]["grenade"]["kind"] == "minimum"
    assert build["stat_targets"]["super"] == {
        "raw": "80～100",
        "kind": "range",
        "min": 80,
        "max": 100,
    }
    assert any(part.strip() == "武器 300" for part in build["unparsed"])
    assert build["notes"] == ["测试武器可按条件替换"]


def test_archive_search_detail_pagination_and_provenance(tmp_path: Path) -> None:
    service = StarsideService(None, _archive(tmp_path, blocks=[BUILD]))

    search = service.search_knowledge("辉耀炽热", category="weapons", limit=1)
    assert search["archive_available"] is True
    assert search["page_count"] == 2
    assert search["build_count"] == 1
    assert search["matched_count"] == 3
    assert search["next_offset"] == 1
    result = search["results"][0]
    assert result["kind"] == "description"
    assert "[pvp][5][/pvp]" in result["snippet"]
    assert "[enh]+10[/enh]" in result["snippet"]
    assert "[unsure]?[/unsure]" in result["snippet"]
    assert "ignore" not in result["snippet"]
    assert result["source"]["snapshot_id"] == search["snapshot_id"]
    assert result["source"]["game_version_verified"] is False

    detail = service.get_knowledge(result["knowledge_id"])
    assert detail["text"] == result["snippet"]
    tables = service.get_knowledge(result["page_id"], section="tables")
    assert tables["rows"][0]["cells"][0]["attrs"] == {"rowspan": "2"}
    assert "[pvp][5][/pvp]" in tables["rows"][0]["cells"][1]["text"]
    assert tables["inline_semantics_preserved"] is True
    links = service.get_knowledge(result["page_id"], section="links")
    assert links["links"] == [
        {"url": "https://docs.example/guide", "body_archived": False}
    ]


def test_archive_reloads_after_install_and_rejects_partial_index(
    tmp_path: Path,
) -> None:
    service = StarsideService(None, tmp_path)
    assert service.search_knowledge()["archive_available"] is False

    _archive(tmp_path)
    assert service.search_knowledge()["archive_available"] is True
    index_path = tmp_path / "index.json"
    index = json.loads(index_path.read_text())
    index["failures"] = ["failed"]
    _write(index_path, index)
    with pytest.raises(ConfigError, match="不完整"):
        service.search_knowledge()


def test_build_id_lookup_is_not_limited_by_search_page_and_returns_copies(
    tmp_path: Path,
) -> None:
    service = StarsideService(
        _Manifest(), _archive(tmp_path, blocks=[BUILD], second_page=True)
    )
    first = service.search_builds(limit=1)
    assert first["matched_count"] == 2
    assert first["next_offset"] == 1
    second_id = service.search_builds(limit=1, offset=1)["results"][0]["build_id"]
    build = service.get_build(second_id)
    assert build["title"] == "Build B"
    build["title"] = "mutated"
    assert service.get_build(second_id)["title"] == "Build B"


class _Manifest:
    entries = {
        "测试武器": [
            {
                "itemHash": -1,
                "name": "测试武器",
                "nameEn": "Test Weapon",
                "itemType": 3,
                "tier": 5,
                "classType": -1,
            }
        ],
        "测试 Perk": [
            {
                "itemHash": 9,
                "name": "测试 Perk",
                "nameEn": "Test Perk",
                "itemType": 19,
                "tier": 5,
                "classType": -1,
            }
        ],
        "测试金装": [
            {
                "itemHash": 10,
                "name": "测试金装",
                "nameEn": "Test Exotic",
                "itemType": 2,
                "tier": 6,
                "classType": 1,
            }
        ],
        "测试模组": [
            {
                "itemHash": 11,
                "name": "测试模组",
                "nameEn": "Test Mod",
                "itemType": 19,
                "tier": 5,
                "classType": -1,
            }
        ],
        "测试超能": [
            {
                "itemHash": 12,
                "name": "测试超能",
                "nameEn": "Test Super",
                "itemType": 19,
                "tier": 5,
                "classType": 1,
            }
        ],
    }

    def search(self, name: str, *, limit: int = 0) -> list[dict]:
        return self.entries.get(name, [])

    def get_all_set_bonuses(self) -> dict:
        return {20: {"set_name": "甲套"}, 21: {"set_name": "乙套"}}


def test_validation_does_not_convert_bare_or_range_stats_to_solver_minimums() -> None:
    build = parse_build(BUILD, build_id="a", source={"title": "A"})
    result = validate_build(_Manifest(), build)

    args = result["solver_handoff"]["arguments"]
    assert args["grenade_target"] == 70
    assert "melee_target" not in args
    assert "super_target" not in args
    assert result["execution_supported"] is False
    assert result["template_parse_complete"] is False
    assert (
        any(item["name"] == "乙套" for item in result["unresolved_requirements"])
        is False
    )


async def test_inventory_matching_distinguishes_missing_unknown_and_current_roll() -> (
    None
):
    build = parse_build(BUILD, build_id="a", source={"title": "A"})
    inventory = SimpleNamespace(
        items=[
            SimpleNamespace(
                item_hash=2**32 - 1,
                item_type="Weapon",
                item_instance_id="w1",
                name="测试武器",
                location="vault",
            ),
            SimpleNamespace(
                item_hash=10,
                item_type="Armor",
                item_instance_id="a1",
                name="测试金装",
                location="hunter",
            ),
        ]
    )
    inventory_service = SimpleNamespace(
        get_inventory=AsyncMock(return_value=inventory),
        get_armor_snapshot=AsyncMock(side_effect=ConfigError("partial")),
    )
    weapon_details = SimpleNamespace(
        weapons=[
            SimpleNamespace(
                instance_id="w1",
                item_hash=2**32 - 1,
                perks_complete=True,
                sockets=[SimpleNamespace(plug_name="测试 Perk", plug_hash=9)],
            )
        ]
    )
    detail_service = SimpleNamespace(
        get_weapon_details_by_type=AsyncMock(return_value=weapon_details)
    )

    result = await match_inventory(
        _Manifest(), "player", build, inventory_service, detail_service
    )

    weapon = next(row for row in result["requirements"] if row["kind"] == "weapon")
    exotic = next(
        row for row in result["requirements"] if row["kind"] == "exotic_armor"
    )
    assert weapon["inventory_status"] == "current_roll_matched"
    assert weapon["alternate_perk_options_checked"] is False
    assert exotic["inventory_status"] == "owned"
    assert result["known_missing_count"] == 0
    assert result["unknown_or_unchecked_count"] > 0
    assert result["full_build_verified"] is False
    assert result["execution_eligible"] is False


async def test_weapon_without_required_perks_does_not_read_sockets() -> None:
    block = BUILD.replace("测试武器 | 测试 Perk", "测试武器")
    build = parse_build(block, build_id="a", source={"title": "A"})
    inventory = SimpleNamespace(items=[])
    inventory_service = SimpleNamespace(
        get_inventory=AsyncMock(return_value=inventory),
        get_armor_snapshot=AsyncMock(side_effect=ConfigError("partial")),
    )
    detail_service = SimpleNamespace(get_weapon_details_by_type=AsyncMock())

    result = await match_inventory(
        _Manifest(), "player", build, inventory_service, detail_service
    )

    detail_service.get_weapon_details_by_type.assert_not_awaited()
    missing = next(
        row for row in result["known_missing_requirements"] if row["kind"] == "weapon"
    )
    assert missing["name"] == "测试武器"


async def test_set_ownership_counts_distinct_armor_slots() -> None:
    block = BUILD.replace(" × 乙套 2 件", "").replace(
        "测试武器 | 测试 Perk", "测试武器"
    )
    build = parse_build(block, build_id="a", source={"title": "A"})

    def armor(instance_id):
        return SimpleNamespace(
            item_instance_id=instance_id,
            set_bonus_hash=20,
            has_set_bonus_mod_socket=False,
        )

    snapshot = SimpleNamespace(
        get_slot=lambda collection: {
            "helmets": [armor("h1"), armor("h2")],
            "gauntlets": [armor("g1")],
            "chests": [armor("c1")],
            "legs": [],
            "class_items": [],
        }[collection]
    )
    inventory_service = SimpleNamespace(
        get_inventory=AsyncMock(return_value=SimpleNamespace(items=[])),
        get_armor_snapshot=AsyncMock(return_value=snapshot),
    )
    detail_service = SimpleNamespace(get_weapon_details_by_type=AsyncMock())

    result = await match_inventory(
        _Manifest(), "player", build, inventory_service, detail_service
    )

    armor_set = next(
        row for row in result["requirements"] if row["kind"] == "armor_set"
    )
    assert armor_set["required_count"] == 4
    assert len(armor_set["owned_candidates"]) == 4
    assert armor_set["potential_distinct_slot_count"] == 3
    assert armor_set["inventory_status"] == "insufficient_slots"
    assert armor_set["compatible_plan_checked"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 1),
        ("status", "in_progress"),
        ("failures", ["bad"]),
        ("pending_urls", ["https://starside.work/missing.html"]),
        ("categories", {}),
        ("public_exports", []),
    ],
)
def test_invalid_archive_is_not_empty_success(tmp_path, field, value):
    _archive(tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    index[field] = value
    _write(tmp_path / "index.json", index)
    with pytest.raises(ConfigError):
        StarsideService(None, tmp_path).search_knowledge()


def test_missing_indexed_page_invalidates_cached_archive(tmp_path):
    service = StarsideService(None, _archive(tmp_path))
    assert service.search_knowledge()["archive_available"]
    (tmp_path / "records/weapon-perks/index.json").unlink()
    with pytest.raises(ConfigError):
        service.search_knowledge()


def test_modified_record_changes_snapshot_identity(tmp_path):
    service = StarsideService(None, _archive(tmp_path))
    first = service.search_knowledge()
    path = tmp_path / "records/weapon-perks/index.json"
    record = json.loads(path.read_text())
    record["text"] += " revised"
    _write(path, record)
    second = service.search_knowledge()
    assert first["snapshot_id"] != second["snapshot_id"]


def test_table_pagination_keeps_rowspan_context_and_heading(tmp_path):
    _archive(tmp_path)
    path = tmp_path / "records/weapon-perks/index.json"
    record = json.loads(path.read_text())
    table = record["tables"][0]
    table["heading"] = "Conditional DPS"
    table["rows"].append([{"tag": "td", "text": "second row"}])
    _write(path, record)
    service = StarsideService(None, tmp_path)
    page = service.get_knowledge(
        "page:weapon-perks/index.html", section="tables", offset=1, limit=1
    )
    row = page["rows"][0]
    assert row["heading"] == "Conditional DPS"
    assert row["preceding_spanning_rows"][0]["row_index"] == 0
    assert row["preceding_spanning_rows"][0]["cells"][0]["attrs"]["rowspan"] == "2"


def test_records_newer_than_completed_index_are_rejected(tmp_path):
    _archive(tmp_path)
    path = tmp_path / "records/weapon-perks/index.json"
    record = json.loads(path.read_text())
    record["archive"]["fetched_at"] = "2027-01-01T00:00:00Z"
    _write(path, record)
    with pytest.raises(ConfigError):
        StarsideService(None, tmp_path).search_knowledge()


def test_record_path_cannot_escape_archive(tmp_path):
    _archive(tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    index["categories"]["weapons"][0]["record"] = "../outside.json"
    _write(tmp_path / "index.json", index)
    with pytest.raises(ConfigError, match="越界"):
        StarsideService(None, tmp_path).search_knowledge()


def test_all_blocks_are_independently_addressable(tmp_path):
    service = StarsideService(
        _Manifest(),
        _archive(tmp_path, blocks=[BUILD, BUILD.replace("Build A", "Variant")]),
    )
    ids = [item["build_id"] for item in service.search_builds()["results"]]
    assert len(set(ids)) == 2
    assert [service.get_build(build_id)["title"] for build_id in ids] == [
        "Build A",
        "Variant",
    ]


def test_text_detail_can_be_read_to_end_without_truncation(tmp_path):
    _archive(tmp_path)
    path = tmp_path / "records/weapon-perks/index.json"
    record = json.loads(path.read_text())
    record["text"] = "x" * 12500
    _write(path, record)
    service = StarsideService(None, tmp_path)
    first = service.get_knowledge("page:weapon-perks/index.html")
    second = service.get_knowledge(
        "page:weapon-perks/index.html", offset=first["next_offset"]
    )
    third = service.get_knowledge(
        "page:weapon-perks/index.html", offset=second["next_offset"]
    )
    assert first["text"] + second["text"] + third["text"] == record["text"]
    assert third["next_offset"] is None


@pytest.mark.parametrize("query", ["测试武器（烈日）", "测试", "未知武器"])
async def test_fuzzy_or_unknown_weapon_names_are_not_reported_missing(query):
    manifest = _Manifest()
    manifest.search = lambda *args, **kwargs: _Manifest.entries["测试武器"]
    build = parse_build(
        f"# A\n## 职业\n职业：猎人\n## 武器\n传说武器：{query}",
        build_id="a",
        source={"title": "A"},
    )
    result = await match_inventory(
        manifest,
        "player",
        build,
        SimpleNamespace(
            get_inventory=AsyncMock(return_value=SimpleNamespace(items=[]))
        ),
        None,
    )
    assert result["known_missing_count"] == 0
    assert result["requirements"][0]["inventory_status"] == "unknown_definition"


@pytest.mark.parametrize(
    "perk_hash,complete,expected",
    [
        (9, True, "current_roll_matched"),
        (99, True, "owned_no_current_roll_match"),
        (9, False, "unknown_current_roll"),
    ],
)
async def test_current_roll_is_distinct_from_selectable_perks(
    perk_hash, complete, expected
):
    build = parse_build(
        "# A\n## 职业\n职业：猎人\n## 武器\n传说武器：测试武器 | 测试 Perk",
        build_id="a",
        source={"title": "A"},
    )
    inventory = SimpleNamespace(
        items=[
            SimpleNamespace(
                item_hash=2**32 - 1,
                item_type="Weapon",
                item_instance_id="1",
                name="测试武器",
                location="vault",
            )
        ]
    )
    details = SimpleNamespace(
        weapons=[
            SimpleNamespace(
                instance_id="1",
                item_hash=2**32 - 1,
                perks_complete=complete,
                sockets=[SimpleNamespace(plug_name="Test Perk", plug_hash=perk_hash)],
            )
        ]
    )
    result = await match_inventory(
        _Manifest(),
        "player",
        build,
        SimpleNamespace(get_inventory=AsyncMock(return_value=inventory)),
        SimpleNamespace(get_weapon_details_by_type=AsyncMock(return_value=details)),
    )
    assert result["requirements"][0]["inventory_status"] == expected
    assert result["known_missing_count"] == 0
    assert result["full_build_verified"] is False


async def test_community_id_cannot_be_used_as_an_execution_plan():
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={}))
    response = await build_assistant(
        intent="equip_build",
        community_build_id="any",
        confirmed=True,
        ctx=ctx,
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "community_template_not_executable"


@pytest.mark.parametrize(
    "wrong_field,value", [("tier", 5), ("classType", 2), ("itemType", 3)]
)
def test_exotic_resolution_rejects_wrong_tier_class_or_type(wrong_field, value):
    manifest = _Manifest()
    manifest.entries = deepcopy(manifest.entries)
    manifest.entries["测试金装"][0][wrong_field] = value
    build = parse_build(BUILD, build_id="a", source={"title": "A"})
    rows = validate_build(manifest, build)["requirements"]
    exotic = next(row for row in rows if row["kind"] == "exotic_armor")
    assert exotic["status"] == "unresolved"


def test_general_class_plugs_resolve_without_claiming_unlocks():
    manifest = _Manifest()
    manifest.entries = deepcopy(manifest.entries)
    manifest.entries["测试超能"][0]["classType"] = 3
    build = parse_build(BUILD, build_id="a", source={"title": "A"})
    rows = validate_build(manifest, build)["requirements"]
    component = next(row for row in rows if row["kind"] == "subclass_component")
    assert component["status"] == "resolved"


async def test_unknown_perk_is_unresolved_even_when_weapon_is_missing():
    build = parse_build(
        "# A\n## 职业\n职业：猎人\n## 武器\n传说武器：测试武器 | 不认识的 Perk",
        build_id="a",
        source={"title": "A"},
    )
    result = await match_inventory(
        _Manifest(),
        "player",
        build,
        SimpleNamespace(
            get_inventory=AsyncMock(return_value=SimpleNamespace(items=[]))
        ),
        SimpleNamespace(
            get_weapon_details_by_type=AsyncMock(
                return_value=SimpleNamespace(weapons=[])
            )
        ),
    )
    assert result["known_missing_count"] == 1
    assert result["unknown_or_unchecked_count"] == 1
    assert result["coverage_complete"] is False


async def test_explicit_build_id_bypasses_search_filters_and_account_failure_keeps_template(
    tmp_path,
):
    service = StarsideService(
        _Manifest(), _archive(tmp_path, blocks=[BUILD], second_page=True)
    )
    inventory = SimpleNamespace(
        get_inventory=AsyncMock(side_effect=ConfigError("partial inventory"))
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={
                "starside_svc": service,
                "inventory_svc": inventory,
                "weapon_detail_svc": None,
            }
        )
    )
    response = await build_assistant(
        intent="community",
        community_build_id="builds/b/index.html#build-1",
        query="no search hits",
        top_n=1,
        ctx=ctx,
    )
    assert response["ok"] is True
    assert response["data"]["selected_build"]["title"] == "Build B"
    assert (
        response["data"]["selected_build"]["inventory_match"]["inventory_status"]
        == "unavailable"
    )
    assert (
        response["data"]["selected_build"]["inventory_match"]["coverage_complete"]
        is False
    )


async def test_corrupt_optional_archive_does_not_break_official_perk_response(tmp_path):
    _archive(tmp_path)
    _write(tmp_path / "index.json", {})
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={
                "starside_svc": StarsideService(None, tmp_path),
                "manifest_query_svc": SimpleNamespace(
                    get_perk_description=lambda name: {"name": name}
                ),
            }
        )
    )
    response = await weapon_assistant(
        intent="perk_description", perk_name="test", ctx=ctx
    )
    assert response["ok"] is True
    assert response["data"]["perk"] == {"name": "test"}
    assert response["data"]["community_references"]["archive_available"] is False


@pytest.mark.parametrize(
    "tool,kwargs,category",
    [
        (weapon_assistant, {"perk_name": "test"}, "weapons"),
        (subclass_assistant, {"query": "test"}, "subclass"),
        (activity_assistant, {"query": "test"}, "activities"),
        (world_assistant, {"query": "test", "community_category": "armor"}, "armor"),
    ],
)
async def test_all_knowledge_routes_are_read_only(tool, kwargs, category):
    calls = []

    def search(query, **options):
        calls.append((query, options))
        return {"results": []}

    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={
                "starside_svc": SimpleNamespace(search_knowledge=search),
            }
        )
    )
    response = await tool(intent="community", offset=3, ctx=ctx, **kwargs)
    assert response["ok"] is True
    assert calls[0][0] == "test"
    assert calls[0][1]["category"] == category
    assert calls[0][1]["offset"] == 3


async def test_assistant_does_not_auto_select_when_limit_hides_more_results() -> None:
    starside = SimpleNamespace(
        search_builds=lambda **kwargs: {
            "matched_count": 2,
            "archive_available": True,
            "results": [{"build_id": "first"}],
        },
        get_build=AsyncMock(),
        match_build_inventory=AsyncMock(),
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"starside_svc": starside})
    )

    response = await build_assistant(intent="community", top_n=1, ctx=ctx)

    assert response["ok"] is True
    assert "selected_build" not in response["data"]
    starside.get_build.assert_not_awaited()
    starside.match_build_inventory.assert_not_awaited()
