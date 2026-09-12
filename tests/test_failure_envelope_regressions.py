"""回归：不要把失败说成成功，也不要把差异说成「都在仓库」。

外部复测报告（2026-09-12）抓到的四条，这里各钉一个断言：

1. `god_roll` 对不存在的武器返回 `ok=true` + 一段「未找到武器」的文字；
2. `popularity` 组把「打错名字」答成「暂无录入快照」；
3. `compare` 的差异项用 location 定位，两把都在仓库时得到
   `present_in=仓库 / absent_in=仓库`，无法判断是哪一把；
4. `loadout` 列表缺 total/returned，无法自证是否全量。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from destiny_mcp.exceptions import ManifestError
from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.weapon_compare_service import WeaponCompareService
from destiny_mcp.tools.assistants import loadout_assistant, weapon_assistant

# ── 1/2. god_roll：武器不存在 ≠ 本地愿单没收录 ──────────────────────────────


class _ManifestWithWeapon:
    """随机 roll 武器（有随机池）—— 这样才会去查愿单，而不是走"固定 perk"分支。"""

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


class _ManifestWithoutWeapon:
    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return []

    def get_item_definition(self, item_hash: int) -> dict:
        return {}


class _NoWishlist:
    """愿单存在但没收录这把武器：代表"本地没数据"，不是"没有这把武器"。"""

    def has_data(self, item_hash: int) -> bool:
        return False


async def test_god_roll_unknown_weapon_raises_manifest_error() -> None:
    service = PerkService(_ManifestWithoutWeapon(), popularity=None)  # type: ignore[arg-type]

    with pytest.raises(ManifestError, match="找不到武器"):
        await service.get_god_roll("不存在的武器xyz")


async def test_god_roll_without_wishlist_data_is_still_a_success() -> None:
    """武器存在、只是本地愿单没收录 —— 这必须是成功 + 说明，不能变成错误。"""
    service = PerkService(  # type: ignore[arg-type]
        _ManifestWithWeapon(), popularity=None, wishlist=_NoWishlist()
    )

    result = await service.get_god_roll("测试武器")

    assert result["kind"] == "none"
    assert "没收录" in result["note"]
    assert result["pve"] == [] and result["pvp"] == []


# ── 2. popularity：打错名字必须是失败 ──────────────────────────────────────


class _RaisingQueryService:
    def get_weapon_stats(self, weapon_name: str) -> dict:
        raise ManifestError(f"找不到物品：{weapon_name}")


def _ctx(**services: object) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=services)
    )


async def test_popularity_unknown_weapon_is_not_reported_as_missing_snapshot() -> None:
    # 注意：popularity 不认领 player_name（那是 analyze/filter_rolls 的参数），
    # 传了会被参数守卫先拦掉，所以这里不传。
    result = await weapon_assistant(
        intent="popularity",
        weapon_name="不存在的武器xyz",
        ctx=_ctx(manifest_query_svc=_RaisingQueryService()),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "manifest_error"


# ── 3. compare：差异必须指向具体副本 ───────────────────────────────────────


class _CompareManifest:
    def get_item_info(self, item_hash: int) -> dict:
        return {"name": "测试 Perk"}

    def get_plug_category_identifier(self, item_hash: int) -> str:
        return "frames"

    def get_sandbox_perk_description(self, item_hash: int) -> None:
        return None

    def get_item_description(self, item_hash: int) -> str:
        return ""

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [{"itemHash": 100, "name": "测试武器", "itemType": 3, "icon": "/w.png"}]

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "hash": 100,
            "itemType": 3,
            "itemTypeDisplayName": "手炮",
            "displayProperties": {"name": "测试武器", "icon": "/w.png"},
            "inventory": {"tierType": 5},
            "sockets": {"socketEntries": [{"randomizedPlugSetHash": 700}]},
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [
            {"plugItemHash": 6001, "name": "Perk A", "plugCategoryIdentifier": "frames"},
            {"plugItemHash": 6002, "name": "Perk B", "plugCategoryIdentifier": "frames"},
        ]


class _CompareResolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "member-1", "membership_type": 3}

    async def get_profile(
        self, membership_id: str, membership_type: int, components: list[int]
    ) -> dict:
        return {
            "characters": {"data": {"character-1": {"classType": 1}}},
            "profileInventory": {
                "data": {
                    "items": [
                        {"itemHash": 100, "itemInstanceId": "vault-a", "bucketHash": 138197802},
                        {"itemHash": 100, "itemInstanceId": "vault-b", "bucketHash": 138197802},
                    ]
                }
            },
            "characterInventories": {"data": {"character-1": {"items": []}}},
            "characterEquipment": {"data": {"character-1": {"items": []}}},
            "itemComponents": {
                "instances": {
                    "data": {"vault-a": {"primaryStat": {"value": 1900}},
                             "vault-b": {"primaryStat": {"value": 1900}}}
                },
                "sockets": {
                    "data": {
                        "vault-a": {"sockets": [{"plugHash": 6001}]},
                        "vault-b": {"sockets": [{"plugHash": 6002}]},
                    }
                },
            },
        }


class _NoAnnotations:
    def annotate_god_roll(self, item_hash: int, plug_hash: int, perk: object) -> None:
        pass

    def god_roll_lookup(self, item_hash: int):
        return None


async def test_compare_difference_names_the_instance_not_the_location() -> None:
    service = WeaponCompareService(  # type: ignore[arg-type]
        _CompareManifest(), _CompareResolver(), _NoAnnotations()
    )

    result = await service.compare_weapon_instances("Tester#1234", "测试武器")

    assert result.differences, "两把的 Perk 不同，应当有差异项"
    for difference in result.differences:
        # 关键：两把都在仓库，所以定位必须靠实例，不能靠位置
        assert difference["present_in_instance"] != difference["absent_in_instance"]
        assert difference["present_in_instance"] in {"vault-a", "vault-b"}
        assert difference["present_in_location"] == difference["absent_in_location"] == "仓库"


# ── 4. loadout 列表要能自证全量 ────────────────────────────────────────────


class _LoadoutStub:
    async def get_loadouts(
        self, player_name: str, character: str | None, limit: int | None = None, offset: int = 0
    ) -> dict:
        """P4/D4 起服务层负责切片，所以替身也要照实按 limit/offset 返回计数。"""
        all_loadouts = [{"id": "local:1"}, {"id": "local:2"}]
        window = all_loadouts[offset : offset + limit] if limit else all_loadouts[offset:]
        truncated = offset + len(window) < len(all_loadouts)
        return {
            "player_name": player_name,
            "loadouts": window,
            "scope": "account_saved",
            "loadout_format": "build_template",
            "total_loadouts": len(all_loadouts),
            "returned_loadouts": len(window),
            "truncated": truncated,
            "next_offset": (offset + len(window)) if truncated else None,
        }


async def test_loadout_list_reports_total_and_returned() -> None:
    result = await loadout_assistant(
        intent="list",
        player_name="Tester#1234",
        ctx=_ctx(loadout_svc=_LoadoutStub()),
    )

    data = result["data"]
    assert result["ok"] is True
    assert data["total_loadouts"] == data["returned_loadouts"] == len(data["loadouts"]) == 2


# ── 5. 「武器」的名字被非武器条目占用时，不能误报「不是武器」 ─────────────


class _AmbiguousManifest:
    """精确名查到的是一条非武器（真实数据里「遗产」就是这样）。"""

    def get_item_definition_by_name(self, item_name: str) -> dict:
        return {"hash": 900, "itemType": 19, "displayProperties": {"name": item_name}}

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [
            {"itemHash": 900, "name": query, "itemType": 19},
            {"itemHash": 901, "name": query, "itemType": 3},
        ]

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "hash": item_hash,
            "itemType": 3,
            "itemTypeDisplayName": "霰弹枪",
            "displayProperties": {"name": "遗产", "icon": "/w.png"},
            "investmentStats": [],
        }

    def get_localized_definition(self, table: str, hash_id: int) -> dict:
        return {}

    def get_definition(self, table: str, hash_id: int) -> dict:
        return {}

    def get_english_name(self, item_hash: int) -> str:
        return "Heritage"


def test_weapon_stats_resolves_the_weapon_not_the_same_named_item() -> None:
    service = object.__new__(ManifestQueryService)
    service._manifest = _AmbiguousManifest()  # type: ignore[attr-defined]

    result = service.get_weapon_stats("遗产")

    # P4 形状：`{weapon, stats}`；重点是"按名字搜到的是武器，而不是同名的其它条目"
    assert result["weapon"]["weapon_type"] == "霰弹枪"
    # 901 = 武器那条；900 是同名的非武器条目
    assert result["weapon"]["item_hash"] == 901
