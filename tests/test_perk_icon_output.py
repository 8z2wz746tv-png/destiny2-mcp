from __future__ import annotations

import os

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.services.perk_service import PerkService
from destiny_mcp.services.manifest_query_service import ManifestQueryService


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
            "hash": 100,
            "itemType": 3,
            "itemTypeDisplayName": "手炮",
            "displayProperties": {
                "name": "测试武器",
                "icon": "/weapon.png",
            },
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

    # P4 形状：`{weapon, sockets}`，perk 就是 socket 里的 option
    assert result["weapon"]["icon_url"] == "https://www.bungie.net/weapon.png"
    assert len(result["sockets"]) == 1
    perk = result["sockets"][0]["options"][0]
    assert perk["icon_url"] == "https://www.bungie.net/perk.png"
    assert perk["description"] == "Manifest 物品描述"


def test_socket_option_includes_icon_and_description_fallback() -> None:
    """P4：取当前插槽的那套私有方法已删，行为由 weapon_payload 的插槽工厂承担。"""
    from destiny_mcp.services import weapon_payload

    manifest = _ManifestStub()
    definition = manifest.get_item_definition(100)

    sockets = weapon_payload.socket_list(manifest, definition)  # type: ignore[arg-type]

    assert len(sockets) == 1
    option = sockets[0]["options"][0]
    assert option["icon_url"] == "https://www.bungie.net/perk.png"
    assert option["description"] == "Manifest 物品描述"
    assert sockets[0]["equipped"] is None  # 没传实例数据就不假装装了什么


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
