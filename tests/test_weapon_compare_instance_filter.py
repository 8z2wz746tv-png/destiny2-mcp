"""Focused tests for selecting one owned weapon instance."""

from __future__ import annotations

import pytest

from destiny_mcp.exceptions import ConfigError, ItemNotFoundError
from destiny_mcp.services.weapon_compare_service import WeaponCompareService


class _ManifestStub:
    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        assert query == "测试武器"
        assert limit == 50
        return [
            {
                "itemHash": 100,
                "name": "测试武器",
                "itemType": 3,
                "icon": "/weapon.png",
            }
        ]


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
        assert components == [102, 200, 201, 205, 300, 305]
        return {
            "characters": {"data": {"character-1": {"classType": 1}}},
            "profileInventory": {
                "data": {
                    "items": [
                        {"itemHash": 100, "itemInstanceId": "vault-instance"}
                    ]
                }
            },
            "characterInventories": {
                "data": {
                    "character-1": {
                        "items": [
                            {"itemHash": 100, "itemInstanceId": "character-instance"}
                        ]
                    }
                }
            },
            "characterEquipment": {"data": {}},
            "itemComponents": {
                "instances": {
                    "data": {
                        "vault-instance": {"primaryStat": {"value": 1900}},
                        "character-instance": {"primaryStat": {"value": 1910}},
                    }
                },
                "sockets": {
                    "data": {
                        "vault-instance": {"sockets": []},
                        "character-instance": {"sockets": []},
                    }
                },
            },
        }


class _PerkServiceStub:
    def annotate_god_roll(self, item_hash: int, plug_hash: int, perk: object) -> None:
        raise AssertionError("empty socket fixtures must not annotate perks")


def _service() -> WeaponCompareService:
    return WeaponCompareService(
        _ManifestStub(),  # type: ignore[arg-type]
        _ResolverStub(),  # type: ignore[arg-type]
        _PerkServiceStub(),  # type: ignore[arg-type]
    )


async def test_filters_to_requested_owned_instance() -> None:
    result = await _service().compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
        item_instance_id=" character-instance ",
    )

    assert [instance.instance_id for instance in result.instances] == [
        "character-instance"
    ]
    assert result.instances[0].power == 1910
    assert result.differences == []


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

    assert {instance.instance_id for instance in result.instances} == {
        "vault-instance",
        "character-instance",
    }


async def test_rejects_blank_weapon_name_before_profile_lookup() -> None:
    with pytest.raises(ConfigError, match="weapon_name"):
        await _service().compare_weapon_instances("Guardian#1234", "   ")


async def test_keeps_absolute_weapon_icon_and_includes_tactical_mod() -> None:
    class Manifest(_ManifestStub):
        def get_item_info(self, item_hash: int) -> dict:
            return {
                4001: {"name": "Tactical Mod", "icon": "/mod.png"},
                5001: {"name": "Shader", "icon": "/shader.png"},
            }[item_hash]

        def get_plug_category_identifier(self, item_hash: int) -> str:
            return {
                4001: "enhancements.weapon.mod",
                5001: "enhancements.weapon.shader",
            }[item_hash]

        def get_sandbox_perk_description(self, item_hash: int) -> None:
            return None

        def get_item_description(self, item_hash: int) -> str:
            return "Improves weapon handling."

        def search(self, query: str, *, limit: int = 20) -> list[dict]:
            return [{
                "itemHash": 100,
                "name": "测试武器",
                "itemType": 3,
                "icon": "https://www.bungie.net/weapon.png",
            }]

    class Resolver(_ResolverStub):
        async def get_profile(
            self,
            membership_id: str,
            membership_type: int,
            components: list[int],
        ) -> dict:
            profile = await super().get_profile(
                membership_id,
                membership_type,
                components,
            )
            profile["itemComponents"]["sockets"]["data"]["vault-instance"] = {
                "sockets": [{"plugHash": 4001}, {"plugHash": 5001}]
            }
            return profile

    class PerkService:
        def annotate_god_roll(self, item_hash: int, plug_hash: int, perk: object) -> None:
            return None

    service = WeaponCompareService(
        Manifest(),  # type: ignore[arg-type]
        Resolver(),  # type: ignore[arg-type]
        PerkService(),  # type: ignore[arg-type]
    )

    result = await service.compare_weapon_instances(
        "Guardian#1234",
        "测试武器",
        item_instance_id="vault-instance",
    )

    assert result.instances[0].icon_url == "https://www.bungie.net/weapon.png"
    assert [perk.name for perk in result.instances[0].perks] == ["Tactical Mod"]
