"""回归：列表要能自证"是不是全量"，空壳要说清是数据不完整。

外部复测报告（第 2 轮，2026-09-12）里与"响应是否说全"相关的四条：
`catalog` 给了 50 条却不说被裁过、`god_roll` 回一个只有标题的空壳、
`catalyst` 吐通用锻造词条、`inventory.type` 和 `community` 的列表缺总数/分页语义。
"""

from __future__ import annotations

from types import SimpleNamespace


from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService
from destiny_mcp.tools.assistants import build_assistant, inventory_assistant

# ── 1. catalog：裁过就要说 ─────────────────────────────────────────────────


class _CatalogManifest:
    """60 把都能滚出「萤火虫」，默认 limit=50 时必须声明截断。"""

    def list_weapon_catalog(self, weapon_type: str, *, weapon_name: str = "") -> list[dict]:
        return [
            {"itemHash": 1000 + index, "name": f"测试手炮{index}", "itemType": 3}
            for index in range(60)
        ]

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "displayProperties": {"name": f"测试手炮{item_hash - 1000}"},
            "sockets": {
                "socketCategories": [{"socketCategoryHash": 4241085061, "socketIndexes": [0]}],
                "socketEntries": [{"randomizedPlugSetHash": 200}],
            },
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [{"plugItemHash": 300, "name": "萤火虫", "plugCategoryIdentifier": "frames"}]

    def get_item_info(self, item_hash: int) -> dict:
        return {"name": "萤火虫"}

    def get_sandbox_perk_description(self, perk_hash: int) -> None:
        return None

    def get_item_description(self, item_hash: int) -> str:
        return ""


def test_catalog_declares_truncation_and_real_total() -> None:
    service = WeaponRollFilterService(_CatalogManifest())  # type: ignore[arg-type]

    result = service.filter_catalog(required_perks="萤火虫")

    assert result["matched_count"] == 60
    assert result["returned_count"] == len(result["matched"]) == 50
    assert result["truncated"] is True


def test_catalog_without_truncation_says_so_too() -> None:
    service = WeaponRollFilterService(_CatalogManifest())  # type: ignore[arg-type]

    result = service.filter_catalog(required_perks="萤火虫", limit=200)

    assert result["returned_count"] == result["matched_count"] == 60
    assert result["truncated"] is False


# ── 2. god_roll：有记录但解析不出条目 ──────────────────────────────────────


class _WeaponManifest:
    """随机 roll 武器（有随机池）：god_roll 才会走愿单分支。"""

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [{"itemHash": 100, "name": "测试武器", "itemType": 3}]

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "hash": item_hash,
            "displayProperties": {"name": "测试武器"},
            "inventory": {"tierType": 5},
            "sockets": {
                "socketCategories": [
                    {"socketCategoryHash": 4241085061, "socketIndexes": [0]},
                ],
                "socketEntries": [{"randomizedPlugSetHash": 200}],
            },
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [
            {
                "plugItemHash": 300,
                "name": "测试 Perk",
                "plugCategoryIdentifier": "frames",
                "currentlyCanRoll": True,
            }
        ]

    def get_item_info(self, plug_hash: int) -> dict:
        return {"name": "测试 Perk", "icon": ""}

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "frames"

    def get_sandbox_perk_description(self, plug_hash: int) -> None:
        return None


class _EmptyPerks:
    pve_perks: set[int] = set()
    pvp_perks: set[int] = set()
    sources = ["某个愿单.txt"]


class _WishlistWithEmptyEntry:
    def has_data(self, item_hash: int) -> bool:
        return True

    def get_god_roll_perks(self, item_hash: int) -> _EmptyPerks:
        return _EmptyPerks()


async def test_god_roll_with_unparsable_entry_explains_instead_of_empty_shell() -> None:
    service = PerkService(  # type: ignore[arg-type]
        _WeaponManifest(), popularity=None, wishlist=_WishlistWithEmptyEntry()
    )

    result = await service.get_god_roll("测试武器")

    assert result["kind"] == "none"
    assert "解析不出" in result["note"]
    assert result["source_detail"] == "某个愿单.txt", "要带上来源，方便判断是哪条数据不完整"


# ── 3. catalyst：只有异域才有，且不能吐通用锻造词条 ─────────────────────────


class _CatalystManifest:
    """传说武器：sockets 里全是 v400 通用大师杰作词条。"""

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [{"itemHash": 500, "name": query, "itemType": 3}]

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == 500:
            return {
                "hash": 500,
                "inventory": {"tierType": 5},
                "displayProperties": {"name": "测试传说"},
            }
        return {
            "displayProperties": {"name": "1阶：稳定性"},
            "plug": {"plugCategoryIdentifier": "v400.plugs.weapons.masterworks.stat.stability"},
        }

    def find_items_by_type(self, item_type: int, *, extra_json_like: str = "") -> list[dict]:
        return []

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [{"plugItemHash": 700}]

    def get_english_name(self, item_hash: int) -> str:
        return ""


def test_catalyst_on_legendary_reports_no_catalyst_instead_of_craft_levels() -> None:
    service = object.__new__(ManifestQueryService)
    service._manifest = _CatalystManifest()  # type: ignore[attr-defined]

    result = service.get_catalyst_details("测试传说")

    assert result["is_exotic"] is False
    assert result["count"] == 0
    assert result["catalysts"] == []
    assert result["unlock_state"] == "not_checked"
    assert "只有异域" in result["note"]


# ── 4/5. 工具层列表要能自证全量 ────────────────────────────────────────────


def _ctx(**services: object) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=services))


class _InventoryStub:
    async def search_items_by_type(self, player_name: str, type_name: str, location: str) -> dict:
        return {"query": type_name, "items": [{"name": f"第{i}把"} for i in range(7)]}


async def test_inventory_type_reports_total_and_returned() -> None:
    result = await inventory_assistant(
        intent="type",
        type_name="手炮",
        player_name="Tester#1234",
        ctx=_ctx(inventory_svc=_InventoryStub()),
    )

    payload = result["data"]["result"]
    assert result["ok"] is True
    assert payload["total"] == payload["returned"] == len(payload["items"]) == 7
    assert payload["truncated"] is False
    # P4 边界：按类型列持有物品时不读 305/310，所以没有 perk 与可换部件
    assert all("sockets" not in item and "options" not in item for item in payload["items"])
    assert result["data"]["weapon_schema_version"] >= 1


class _StarsideStub:
    """matched 数可调：=1 时工具会自动定位那一套，>1 时保留搜索结果让调用方挑。"""

    def __init__(self, matched: int = 1) -> None:
        self._matched = matched

    def search_builds(self, **kwargs: object) -> dict:
        return {
            "results": [
                {"build_id": f"示例配装{i}", "title": f"示例配装{i}"} for i in range(self._matched)
            ],
            "matched_count": self._matched,
            "build_count": self._matched,
            "archive_available": True,
        }

    def get_build(self, build_id: str) -> dict:
        return {"build_id": build_id, "title": "示例配装", "weapons": [], "armor": []}


async def test_community_with_pinned_build_drops_search_paging() -> None:
    result = await build_assistant(
        intent="community",
        character="hunter",
        community_build_id="示例配装",
        include_inventory=False,
        ctx=_ctx(starside_svc=_StarsideStub()),
    )

    data = result["data"]
    assert result["ok"] is True
    assert data["selected_build"]["build_id"] == "示例配装"
    for stale in ("results", "next_offset", "offset", "returned_count", "limit"):
        assert stale not in data, f"指定了具体某一套，不该再返回搜索分页字段 {stale}"


async def test_community_search_without_id_keeps_paging() -> None:
    """命中多套、调用方又没指定时，搜索结果必须留着让他挑，不能被一起收掉。"""
    result = await build_assistant(
        intent="community",
        character="hunter",
        include_inventory=False,
        ctx=_ctx(starside_svc=_StarsideStub(matched=3)),
    )

    data = result["data"]
    assert len(data["results"]) == 3, "搜索结果要留着"
    assert "selected_build" not in data, "没指定就不该替他挑一套"
