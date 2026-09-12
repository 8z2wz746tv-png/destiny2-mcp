"""副本对比（compare）的契约测试：筛选单个副本、必填校验、以及新的实例级形状。

P4 形状：`comparison.weapon` 是身份块（含 owned 摘要），`comparison.instances[]`
每项是 `{weapon, sockets, stats}`，sockets 为**实例级**（这一件能换的 310 + 现在装的）。
"""

from __future__ import annotations

import pytest

from destiny_mcp.exceptions import AuthenticationError, ConfigError, ItemNotFoundError
from destiny_mcp.services.profile_components import WEAPON_DETAIL
from destiny_mcp.services.weapon_compare_service import WeaponCompareService

WEAPON_HASH = 100
POOL_HASH = 700


class _ManifestStub:
    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        assert query == "测试武器"
        assert limit == 0
        return [
            {
                "itemHash": WEAPON_HASH,
                "name": "测试武器",
                "itemType": 3,
                "icon": "https://www.bungie.net/weapon.png",
            }
        ]

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == WEAPON_HASH:
            return {
                "hash": WEAPON_HASH,
                "itemType": 3,
                "itemTypeDisplayName": "手炮",
                "displayProperties": {
                    "name": "测试武器",
                    "icon": "/weapon.png",
                    "description": "测试用",
                },
                "inventory": {"tierType": 5},
                "equippingBlock": {"ammoType": 1},
                "sockets": {"socketEntries": [{"randomizedPlugSetHash": POOL_HASH}]},
            }
        return {}

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict]:
        assert plug_set_hash == POOL_HASH
        return [
            {"plugItemHash": 4001, "name": "Tactical Mod",
             "plugCategoryIdentifier": "v400.weapon.mod_guns"},
            {"plugItemHash": 4002, "name": "Backup Mag",
             "plugCategoryIdentifier": "v400.weapon.mod_guns"},
        ]

    def get_item_info(self, item_hash: int) -> dict:
        return {
            4001: {"name": "Tactical Mod", "icon": "/mod.png"},
            4002: {"name": "Backup Mag", "icon": "/mod2.png"},
        }.get(item_hash, {})

    def get_plug_category_identifier(self, item_hash: int) -> str:
        return "v400.weapon.mod_guns"

    def get_sandbox_perk_description(self, item_hash: int) -> None:
        return None

    def get_item_description(self, item_hash: int) -> str:
        return "Improves weapon handling."


class _ResolverStub:
    async def resolve_player(self, player_name: str) -> dict:
        assert player_name == "Guardian#1234"
        return {"membership_id": "member-1", "membership_type": 3}

    async def get_profile(
        self,
        membership_id: str,
        membership_type: int,
        components: list[int],
    ) -> dict:
        assert membership_id == "member-1"
        assert membership_type == 3
        assert components == WEAPON_DETAIL
        return {
            "characters": {"data": {"character-1": {"classType": 1}}},
            "profileInventory": {
                "data": {
                    "items": [
                        {
                            "itemHash": WEAPON_HASH,
                            "itemInstanceId": "vault-instance",
                            "bucketHash": 138197802,
                            "state": 1,
                        }
                    ]
                }
            },
            "characterInventories": {
                "data": {
                    "character-1": {
                        "items": [{"itemHash": WEAPON_HASH, "itemInstanceId": "character-instance"}]
                    }
                }
            },
            "characterEquipment": {"data": {"character-1": {"items": []}}},
            "itemComponents": {
                "instances": {
                    "data": {
                        "vault-instance": {
                            "primaryStat": {"value": 1900},
                            "gearTier": 4,
                            "itemLevel": 12,
                            "quality": 2,
                        },
                        "character-instance": {"primaryStat": {"value": 1910}},
                    }
                },
                "sockets": {
                    "data": {
                        "vault-instance": {"sockets": [{"plugHash": 4001}]},
                        "character-instance": {"sockets": [{"plugHash": 4001}]},
                    }
                },
                "reusablePlugs": {
                    "data": {
                        "vault-instance": {
                            "plugs": {"0": [
                                {"plugItemHash": 4001, "canInsert": True},
                                {"plugItemHash": 4002, "canInsert": True},
                            ]}
                        },
                        "character-instance": {
                            "plugs": {"0": [{"plugItemHash": 4001, "canInsert": True}]}
                        },
                    }
                },
                "stats": {"data": {"vault-instance": {"stats": {}}}},
            },
        }


class _PerkServiceStub:
    def annotate_god_roll(self, item_hash: int, plug_hash: int, perk: object) -> None:
        pass

    def god_roll_lookup(self, item_hash: int):
        return None


def _service(manifest=None, resolver=None) -> WeaponCompareService:
    return WeaponCompareService(
        (manifest or _ManifestStub()),  # type: ignore[arg-type]
        (resolver or _ResolverStub()),  # type: ignore[arg-type]
        _PerkServiceStub(),  # type: ignore[arg-type]
    )


async def test_filters_to_requested_owned_instance() -> None:
    result = await _service().compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
        item_instance_id=" character-instance ",
    )

    assert [instance["weapon"]["instance"]["instance_id"] for instance in result.instances] == [
        "character-instance"
    ]
    assert result.instances[0]["weapon"]["instance"]["power"] == 1910
    assert result.weapon["name"] == "测试武器"
    assert result.weapon["rarity"] == "传说"
    assert result.weapon["owned"]["count"] == 1
    assert result.differences == []


async def test_instance_sockets_carry_what_is_installed_and_what_can_be_inserted() -> None:
    result = await _service().compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
        item_instance_id="vault-instance",
    )
    instance = result.instances[0]

    # 定义级 sockets 带 equipped（现在装的），实例级 options 是这一件能换的
    assert [socket["scope"] for socket in instance["sockets"]] == ["definition"]
    socket = instance["sockets"][0]
    assert socket["kind"] == "mod"
    assert socket["equipped"] == {"plug_hash": 4001, "name": "Tactical Mod"}
    assert socket["option_count"] == 2
    assert [entry["scope"] for entry in instance["options"]] == ["instance"]
    assert [option["name"] for option in instance["options"][0]["options"]] == [
        "Tactical Mod",
        "Backup Mag",
    ]
    assert instance["weapon"]["gear_tier"] == 4
    assert instance["weapon"]["instance"]["locked"] is True


async def test_rejects_unknown_requested_instance() -> None:
    with pytest.raises(ItemNotFoundError, match="missing-instance"):
        await _service().compare_weapon_instances(
            "Guardian#1234",
            "测试武器",
            item_instance_id="missing-instance",
        )


async def test_omitted_instance_keeps_all_owned_copies() -> None:
    result = await _service().compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
    )

    assert {
        instance["weapon"]["instance"]["instance_id"] for instance in result.instances
    } == {"vault-instance", "character-instance"}


async def test_rejects_blank_weapon_name_before_profile_lookup() -> None:
    with pytest.raises(ConfigError, match="weapon_name"):
        await _service().compare_weapon_instances("Guardian#1234", "   ")


async def test_rejects_missing_inventory_scope_instead_of_reporting_not_owned() -> None:
    class Resolver(_ResolverStub):
        async def get_profile(self, *args, **kwargs) -> dict:
            profile = await super().get_profile(*args, **kwargs)
            profile["profileInventory"]["data"]["items"] = []
            profile["characterInventories"]["data"]["character-1"]["items"] = []
            profile["characterEquipment"]["data"]["character-1"]["items"] = [{
                "itemHash": 999,
                "itemInstanceId": "equipped",
            }]
            return profile

    with pytest.raises(AuthenticationError, match="ReadDestinyInventoryAndVault"):
        await _service(resolver=Resolver()).compare_weapon_instances(
            "Guardian#1234", "测试武器"
        )


async def test_rejects_missing_socket_component_instead_of_empty_perks() -> None:
    class Resolver(_ResolverStub):
        async def get_profile(self, *args, **kwargs) -> dict:
            profile = await super().get_profile(*args, **kwargs)
            del profile["itemComponents"]["sockets"]["data"]["vault-instance"]
            return profile

    with pytest.raises(ConfigError, match="缺少当前插槽数据"):
        await _service(resolver=Resolver()).compare_weapon_instances(
            "Guardian#1234",
            "测试武器",
            item_instance_id="vault-instance",
        )


async def test_keeps_absolute_weapon_icon_and_lists_every_socket() -> None:
    """P4：实例级 sockets 不再按类别挑挑拣拣 —— 模组、着色器都各自成栏。"""
    manifest = _ManifestStub()

    def plugs(_hash: int) -> list[dict]:
        return [
            {"plugItemHash": 4001, "name": "Tactical Mod",
             "plugCategoryIdentifier": "v400.weapon.mod_guns"},
            {"plugItemHash": 5001, "name": "Shader",
             "plugCategoryIdentifier": "shaders"},
        ]

    manifest.get_plug_set_plugs = plugs  # type: ignore[method-assign]
    manifest.get_item_info = lambda h: {  # type: ignore[method-assign]
        4001: {"name": "Tactical Mod", "icon": "/mod.png"},
        5001: {"name": "Shader", "icon": "/shader.png"},
    }.get(h, {})
    manifest.get_plug_category_identifier = lambda h: (  # type: ignore[method-assign]
        "shaders" if h == 5001 else "v400.weapon.mod_guns"
    )

    class Resolver(_ResolverStub):
        async def get_profile(self, *args, **kwargs) -> dict:
            profile = await super().get_profile(*args, **kwargs)
            # 这一件两个 plug 都插得进去
            profile["itemComponents"]["reusablePlugs"]["data"]["vault-instance"] = {
                "plugs": {"0": [
                    {"plugItemHash": 4001, "canInsert": True},
                    {"plugItemHash": 5001, "canInsert": True},
                ]}
            }
            return profile

    result = await _service(manifest=manifest, resolver=Resolver()).compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
        item_instance_id="vault-instance",
    )

    assert result.weapon["icon_url"] == "https://www.bungie.net/weapon.png"
    # 列清单只给计数（不展开池子），实例级 options 才列内容 —— 同一个池子不为每个副本重复一份
    sockets = result.instances[0]["sockets"]
    assert [socket["kind"] for socket in sockets] == ["mod"]
    assert sockets[0]["option_count"] == 2
    assert sockets[0]["options"] == []
    assert sockets[0]["options_available"] is False
    assert [option["name"] for option in result.instances[0]["options"][0]["options"]] == [
        "Tactical Mod",
        "Shader",
    ]
