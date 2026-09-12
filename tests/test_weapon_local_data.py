"""P5：本地资料挂载 —— 覆盖表、`options[].recommended` 汇总、以及 fail-soft。

三件事必须钉住：
1. **覆盖表**（计划 §3.4）：哪个 intent 带 farming/popularity/community/sources，
   不该带的要写明"这个 intent 不查"，而不是悄悄少一个键；
2. **每个 plug 的四路结论就地汇总**（愿单/选取率/清单/社区），调用方不用按名字跨四段拼；
3. 本地资料坏掉时官方数据照常返回，且**照实说**资料不可用（语料里的横切规则）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from destiny_mcp.services import weapon_local_data as wld
from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.weapon_detail_service import WeaponDetailService
from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService
from destiny_mcp.tools.assistants import weapon_assistant

WEAPON_HASH = 100
POOL_HASH = 700

FARMING_ROW = {
    "name": "测试武器",
    "list": "刷取清单-测试",
    "scale": "T",
    "tier": "T1",
    "frame": "精确重击 65",
    "element": "动能",
    "source": "深岩墓室",
    "perks": {"三号位": ["快速命中"], "四号位": ["萤火虫"]},
    "source_ref": {
        "source_type": "author_markdown",
        "local_path": "share/test.md",
        "updated_at": "2026.9.6",
        "trust": "untrusted_reference",
    },
}


class _Manifest:
    def __init__(self) -> None:
        self._items = {
            WEAPON_HASH: {"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3,
                          "itemTypeNameDisplay": "手炮", "tier": 5, "icon": "/w.png"},
            900: {"name": "精确重击框架", "icon": "/i.png"},
            901: {"name": "快速命中", "icon": "/p1.png"},
            902: {"name": "萤火虫", "icon": "/p2.png"},
        }

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [{"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3}]

    def list_weapon_catalog(self, weapon_type: str = "", *, weapon_name: str = "") -> list[dict]:
        return [{"itemHash": WEAPON_HASH, "name": "测试武器", "itemType": 3}]

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == WEAPON_HASH:
            return {
                "hash": WEAPON_HASH,
                "itemType": 3,
                "itemTypeDisplayName": "手炮",
                "displayProperties": {"name": "测试武器", "icon": "/w.png"},
                "inventory": {"tierType": 5},
                "defaultDamageType": 1,
                "stats": {"primaryBaseStatHash": 4284893193,
                          "stats": {"4284893193": {"statHash": 4284893193, "value": 140}}},
                "sockets": {"socketEntries": [
                    {"singleInitialItemHash": 900},
                    {"randomizedPlugSetHash": POOL_HASH},
                ]},
            }
        if item_hash == 900:
            return {"plug": {"plugCategoryIdentifier": "intrinsics"}}
        return {}

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [
            {"plugItemHash": 901, "name": "快速命中", "plugCategoryIdentifier": "frames"},
            {"plugItemHash": 902, "name": "萤火虫", "plugCategoryIdentifier": "frames"},
        ]

    def get_item_info(self, item_hash: int) -> dict:
        return self._items.get(item_hash, {})

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "intrinsics" if plug_hash == 900 else "frames"

    def get_sandbox_perk_description(self, plug_hash: int) -> None:
        return None

    def get_item_description(self, plug_hash: int) -> str:
        return "效果说明"

    def get_english_name(self, item_hash: int) -> str:
        return "Test Weapon"

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        return None


class _Starside:
    """本地资料替身：清单命中 + 社区命中 + 选取率命中。"""

    def __init__(self, *, broken: bool = False) -> None:
        self._broken = broken

    def lookup_farming(self, names: Any, *, limit: int = 5) -> dict:
        if self._broken:
            raise RuntimeError("清单坏了")
        return {
            "available": True,
            "matched_count": 1,
            "results": [FARMING_ROW],
            "unmatched": [],
            "coverage_scope": "indexed_rated_lists_only",
        }

    def search_knowledge(self, query: str, *, category: str = "", limit: int = 10, offset: int = 0) -> dict:
        if self._broken:
            raise RuntimeError("社区资料坏了")
        return {
            "archive_available": True,
            "archive_updated_at": "2026-09-09T00:00:00+00:00",
            "matched_count": 2,
            "results": [
                {"knowledge_id": "weapon-perks/1", "title": "快速命中", "group": "", "snippet": "…"},
                {"knowledge_id": "weapon-perks/2", "title": "武器总览", "group": "", "snippet": "…"},
            ],
        }


class _Popularity:
    def get_weapon_popularity(self, weapon_name: str) -> dict | None:
        return {
            "weapon": {"name": weapon_name},
            "perk_columns": [{
                "slot_name": "frames",
                "label": "特性",
                "items": [
                    {"name": "快速命中", "plug_hash": 901, "selection_rate": 42.0},
                    {"name": "萤火虫", "plug_hash": 902, "selection_rate": 12.5},
                ],
            }],
            "popular_combinations": [{"perks": [{"name": "快速命中", "plug_hash": 901}],
                                      "selection_rate": 30.0}],
            "source": {"kind": "user_screenshot", "label": "用户提供截图",
                       "captured_at": "2026-07-15", "note": "人工核对"},
        }


class _Wishlist:
    def is_god_roll_perk(self, item_hash: int, plug_hash: int) -> dict:
        return {"pve": plug_hash == 901, "pvp": False}

    def has_data(self, item_hash: int) -> bool:
        return True

    def get_god_roll_perks(self, item_hash: int) -> Any:
        return None  # 覆盖表测试只关心本地块，不关心推荐 roll


class _Resolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "m1", "membership_type": 3}

    async def get_profile(self, membership_id: str, membership_type: int, components: list[int]) -> dict:
        return {
            "characters": {"data": {"c1": {"classType": 1}}},
            "profileInventory": {"data": {"items": [
                {"itemHash": WEAPON_HASH, "itemInstanceId": "i1", "bucketHash": 138197802, "state": 0},
            ]}},
            "characterInventories": {"data": {"c1": {"items": []}}},
            "characterEquipment": {"data": {"c1": {"items": []}}},
            "itemComponents": {
                "instances": {"data": {"i1": {"primaryStat": {"value": 1900}}}},
                "sockets": {"data": {"i1": {"sockets": [{"plugHash": 900}]}}},
                "reusablePlugs": {"data": {"i1": {"plugs": {
                    "1": [{"plugItemHash": 901, "canInsert": True}],
                }}}},
                "stats": {"data": {"i1": {"stats": {}}}},
            },
        }


def _services(*, broken: bool = False) -> dict[str, Any]:
    manifest = _Manifest()
    perk_svc = PerkService(manifest, _Wishlist())  # type: ignore[arg-type]
    query_svc = ManifestQueryService(manifest)  # type: ignore[arg-type]
    starside = _Starside(broken=broken)
    return {
        "manifest": manifest,
        "resolver": _Resolver(),
        "starside_svc": starside,
        "perk_svc": perk_svc,
        "manifest_query_svc": query_svc,
        "weapon_detail_svc": WeaponDetailService(  # type: ignore[arg-type]
            manifest, _Resolver(), None, lookup_factory=perk_svc.god_roll_lookup
        ),
        "weapon_roll_filter_svc": WeaponRollFilterService(manifest),  # type: ignore[arg-type]
        # analyze 分支要它；这里只验覆盖表，用最小替身即可
        "weapon_analysis_svc": SimpleNamespace(
            analyze_weapon=_analysis_stub(perk_svc, query_svc)
        ),
    }


def _analysis_stub(perk_svc: Any, query_svc: Any):
    async def analyze_weapon(weapon_name: str, *, player_name: Any = None, include_inventory: bool = True) -> dict:
        template = query_svc.get_weapon_full_info(
            weapon_name, lookup_factory=perk_svc.god_roll_lookup
        )
        pool = await perk_svc.get_weapon_perks(weapon_name)
        return {
            "summary": f"已分析「{weapon_name}」。",
            "weapon": template["weapon"],
            "sockets": pool["sockets"],
            "stats": template["stats"],
            "god_roll": await perk_svc.get_god_roll(weapon_name),
            "inventory": None,
            "inventory_status": "not_requested",
            "warnings": [],
            "next_actions": [],
        }

    return analyze_weapon


def _ctx(services: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=services))


async def _call(services: dict[str, Any], **kwargs) -> dict:
    return await weapon_assistant(ctx=_ctx(services), **kwargs)


# ── 纯函数：汇总逻辑 ─────────────────────────────────────────────────────


def test_collect_reads_all_three_sources() -> None:
    data = wld.collect("测试武器", starside=_Starside(), popularity_svc=_Popularity())

    assert data.farming["matched_count"] == 1
    assert data.community["matched_count"] == 2
    assert data.popularity is not None
    assert data.warnings == []


def test_collect_never_raises_on_broken_sources() -> None:
    data = wld.collect("测试武器", starside=_Starside(broken=True), popularity_svc=_Popularity())

    assert data.farming == {}
    assert len(data.warnings) == 2  # 清单与社区各一条
    assert data.popularity is not None


def test_farming_block_carries_source_reference_and_cross_check() -> None:
    block = wld.farming_block(
        {"available": True, "results": [FARMING_ROW]},
        "测试武器",
        weapon={"frame": "精确重击框架", "damage_type": "动能"},
        sockets=[{"options": [{"name": "快速命中", "can_roll": True},
                              {"name": "萤火虫", "can_roll": False}]}],
    )

    assert block["tier"] == "T1"
    assert block["source_ref"]["updated_at"] == "2026.9.6"
    assert block["recommended_perks"] == {"三号位": ["快速命中"], "四号位": ["萤火虫"]}
    check = block["cross_check"]
    assert check["frame"]["agrees"] is True  # 清单「精确重击 65」 vs Manifest「精确重击框架」
    assert check["element"]["agrees"] is True
    assert {item["name"]: item["can_roll"] for item in check["perks"]} == {
        "快速命中": True,
        "萤火虫": False,
    }


def test_farming_block_says_not_listed_instead_of_pretending() -> None:
    block = wld.farming_block({"available": True, "results": []}, "没收录的武器")

    assert block["matched"] is False
    assert "不等于不值得刷" in block["note"]


def test_popularity_index_maps_plug_hash_to_rank_and_column() -> None:
    index = wld.popularity_index(_Popularity().get_weapon_popularity("测试武器"))

    assert index[901] == {"selection_rate": 42.0, "rank": 1, "column": "特性"}
    assert index[902]["rank"] == 2


# ── 工具层：recommended 汇总 ─────────────────────────────────────────────


async def test_options_carry_merged_recommendations() -> None:
    # perk_pool 是四路齐全的 intent（覆盖表里只有它和 analyze 带选取率）
    services = _services()
    services["perk_svc"]._popularity = _Popularity()  # type: ignore[assignment]
    response = await _call(services, intent="perk_pool", weapon_name="测试武器")

    sockets = response["data"]["sockets"]
    options = {option["name"]: option for socket in sockets for option in socket["options"]}
    fast = options["快速命中"]["recommended"]
    # 四路就地汇总：愿单 + 选取率 + 清单 + 社区
    assert fast["wishlist"] == {"pve": True, "pvp": False}
    assert fast["popularity"]["selection_rate"] == 42.0
    assert fast["farming"] == {"columns": ["三号位"], "must_farm": True}
    assert fast["community"] == {"knowledge_id": "weapon-perks/1", "match": "name_substring"}
    # 没有结论的 perk 不挂空壳
    assert "recommended" not in options["萤火虫"] or "wishlist" not in options["萤火虫"].get("recommended", {})


async def test_weapon_block_carries_local_blocks_and_sources() -> None:
    response = await _call(_services(), intent="info", weapon_name="测试武器")
    weapon = response["data"]["weapon"]

    assert weapon["farming"]["tier"] == "T1"
    assert weapon["sources"][0]["kind"] == "farming_list"
    assert weapon["sources"][0]["updated_at"] == "2026.9.6"
    assert weapon["sources"][0]["trust"] == "untrusted_reference"
    assert weapon["community"]["matched_count"] == 2


# ── 覆盖表（计划 §3.4）──────────────────────────────────────────────────


async def test_coverage_table_per_intent() -> None:
    """逐 intent 核对带哪些本地块；不该带的必须明说"这个 intent 不查"。"""
    expectations = {
        # intent: (清单命中, 选取率有数据, 社区有条目)
        "info": (True, False, True),
        "perk_pool": (True, True, True),
        "analyze": (True, True, True),
        "god_roll": (True, False, True),
        "popularity": (True, True, False),
    }
    for intent, (farming, popularity, community) in expectations.items():
        services = _services()
        services["perk_svc"]._popularity = _Popularity()  # type: ignore[assignment]
        response = await _call(services, intent=intent, weapon_name="测试武器")
        weapon = response["data"]["weapon"]
        label = intent

        assert weapon["farming"]["matched"] is farming, label
        assert weapon["popularity"]["available"] is popularity, label
        assert bool(weapon["community"]["matched_count"]) is community, label
        if not popularity:
            assert "不带" in weapon["popularity"]["note"], label


async def test_lean_mode_marks_blocks_not_checked() -> None:
    response = await _call(_services(), intent="type", weapon_type="手炮")

    weapon = response["data"]["weapons"]["items"][0]["weapon"]
    assert weapon["popularity"]["available"] is False
    assert "列表类" in weapon["popularity"]["note"]
    assert weapon["community"]["results"] == []


# ── fail-soft：本地资料坏掉不能弄坏官方数据 ─────────────────────────────


async def test_broken_local_sources_keep_official_data_and_warn() -> None:
    response = await _call(_services(broken=True), intent="info", weapon_name="测试武器")

    assert response["ok"] is True
    weapon = response["data"]["weapon"]
    assert weapon["name"] == "测试武器"
    assert weapon["farming"]["matched"] is False
    assert response["warnings"], "本地资料不可用必须冒泡成 warning"


async def test_missing_local_archive_says_unavailable_not_empty() -> None:
    services = _services()
    services["starside_svc"] = None
    response = await _call(services, intent="info", weapon_name="测试武器")

    weapon = response["data"]["weapon"]
    assert weapon["farming"]["available"] is False
    assert weapon["community"]["archive_available"] is False
    assert response["data"]["weapon"]["sources"] == []


def test_local_data_module_has_no_hard_dependency_on_starside() -> None:
    """`collect` 只吃鸭子类型：没有 starside 时也必须是干净的"没数据"。"""
    data = wld.collect("测试武器")

    assert data.available is False
    assert data.warnings == []
    assert Path("destiny_mcp/services/weapon_local_data.py").is_file()
