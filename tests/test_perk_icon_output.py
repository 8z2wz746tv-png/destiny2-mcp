from __future__ import annotations

import os

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.services.weapon_detail_service import WeaponDetailService


class _PopularityStub:
    def get_weapon_popularity(self, weapon_name: str) -> None:
        return None


class _ManifestStub:
    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        return [
            {
                "itemHash": 100,
                "name": "测试武器",
                "itemType": 3,
                "icon": "https://www.bungie.net/weapon.png",
            }
        ]

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "itemTypeDisplayName": "手炮",
            "sockets": {
                "socketCategories": [
                    {"socketCategoryHash": 4241085061, "socketIndexes": [0]}
                ],
                "socketEntries": [{"randomizedPlugSetHash": 200}],
            },
        }

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        return [
            {
                "plugItemHash": 300,
                "name": "测试 Perk",
                "plugCategoryIdentifier": "barrels",
            }
        ]

    def get_sandbox_perk_description(self, perk_hash: int) -> None:
        return None

    def get_item_description(self, item_hash: int) -> str:
        return "Manifest 物品描述"

    def get_item_info(self, item_hash: int) -> dict:
        return {
            "name": "测试 Perk",
            "icon": "https://www.bungie.net/perk.png",
        }

    def get_plug_category_identifier(self, item_hash: int) -> str:
        return "barrels"


async def test_perk_pool_includes_weapon_and_perk_icons_with_description_fallback() -> None:
    manifest = _ManifestStub()
    service = PerkService(manifest, popularity=_PopularityStub())  # type: ignore[arg-type]

    result = await service.get_weapon_perks("测试")

    assert result.icon_url == "https://www.bungie.net/weapon.png"
    assert len(result.slots) == 1
    perk = result.slots[0].plugs[0]
    assert perk.icon_url == "https://www.bungie.net/perk.png"
    assert perk.description == "Manifest 物品描述"


def test_current_weapon_socket_includes_icon_and_description_fallback() -> None:
    manifest = _ManifestStub()
    service = object.__new__(WeaponDetailService)
    service._manifest = manifest  # type: ignore[attr-defined]

    socket = service._categorize_socket(
        0,
        300,
        {
            "sockets": {
                "socketEntries": [{"randomizedPlugSetHash": 200}],
            }
        },
        {"count": 0},
    )

    assert socket is not None
    assert socket.icon_url == "https://www.bungie.net/perk.png"
    assert socket.description == "Manifest 物品描述"


def test_intrinsic_perk_includes_absolute_icon_url() -> None:
    class Manifest:
        def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
            return [{"plugItemHash": 300}]

        def get_item_definition(self, item_hash: int) -> dict:
            return {
                "plug": {"plugCategoryIdentifier": "intrinsics"},
                "displayProperties": {
                    "name": "测试框架",
                    "description": "固有效果",
                    "icon": "/intrinsic.png",
                },
            }

    service = object.__new__(ManifestQueryService)
    service._manifest = Manifest()  # type: ignore[attr-defined]

    result = service._extract_intrinsic_perks({
        "sockets": {"socketEntries": [{"reusablePlugSetHash": 200}]}
    })

    assert result == [{
        "name": "测试框架",
        "description": "固有效果",
        "icon_url": "https://www.bungie.net/intrinsic.png",
    }]
