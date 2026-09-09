"""Owned roll searches must scan the full inventory, not a catalog sample."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.exceptions import AuthenticationError, ConfigError
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.services.weapon_detail_service import WeaponDetailService
from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService
from destiny_mcp.tools.assistants import weapon_assistant
from destiny_mcp.utils.hash_utils import to_signed


VAULT_BUCKET = 138197802
PERK_HASH = 900


@pytest.fixture
def account():
    manifest = ManifestManager()
    definitions = {}
    for index in range(130):
        item_hash = 0xF0000000 + index
        info = {
            "itemHash": item_hash,
            "name": f"测试武器 {index:03}",
            "itemType": 3,
            "itemTypeName": "Weapon",
            "itemTypeNameDisplay": "手炮" if index < 129 else "自动步枪",
            "tier": 5,
        }
        manifest._hash_index[item_hash] = info
        manifest._hash_index[to_signed(item_hash)] = info
        manifest._name_index[info["name"]] = [info]
        definitions[item_hash] = {
            "sockets": {"socketEntries": [{"randomizedPlugSetHash": 800}]},
        }
    manifest._hash_index[PERK_HASH] = {"name": "辉耀炽热", "itemType": 19}
    manifest._hash_index[901] = {"name": "快速命中", "itemType": 19}
    manifest._hash_index[902] = {"name": "弹药重构", "itemType": 19}
    manifest._hash_index[999] = {
        "itemHash": 999, "name": "测试护甲", "tier": 6,
        "itemType": 2, "itemTypeNameDisplay": "腿部护甲",
    }
    manifest._name_index["测试护甲"] = [manifest._hash_index[999]]
    manifest.get_item_definition = lambda h: definitions.get(h)
    manifest.get_plug_set_plugs = lambda h: [
        {"plugItemHash": PERK_HASH, "plugCategoryIdentifier": "unknown.new.category"},
        {"plugItemHash": 902, "name": "弹药重构"},
    ]
    manifest.get_plug_category_identifier = lambda h: "unknown.new.category"
    manifest.get_sandbox_perk_description = lambda h: None
    manifest.get_item_description = lambda h: ""
    manifest.get_english_name = lambda h: {PERK_HASH: "Incandescent"}.get(h, "")

    def raw(index, bucket=VAULT_BUCKET):
        return {"itemHash": 0xF0000000 + index, "itemInstanceId": str(index + 1), "bucketHash": bucket}

    profile = {
        "profileInventory": {"data": {"items": [raw(i) for i in range(127)] + [
            {"itemHash": 999, "itemInstanceId": "armor", "bucketHash": VAULT_BUCKET},
        ]}},
        "characters": {"data": {"t": {"classType": 0}, "h": {"classType": 1}, "w": {"classType": 2}}},
        "characterInventories": {"data": {
            "t": {"items": [raw(127, 1498876634)]},
            "h": {"items": [raw(128, 1498876634)]},
            "w": {"items": []},
        }},
        "characterEquipment": {"data": {
            "t": {"items": []}, "h": {"items": []},
            "w": {"items": [raw(129, 1498876634)]},
        }},
        "itemComponents": {
            "instances": {"data": {str(i + 1): {} for i in range(130)}},
            "sockets": {"data": {
                str(i + 1): {"sockets": [{"plugHash": PERK_HASH if i % 2 == 0 else 901}]}
                for i in range(130)
            }},
        },
    }
    resolver = SimpleNamespace(
        resolve_player=AsyncMock(return_value={"membership_id": "test", "membership_type": 3}),
        get_profile=AsyncMock(return_value=profile),
    )
    detail = WeaponDetailService(manifest, resolver)
    filter_svc = WeaponRollFilterService(manifest)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={
        "weapon_detail_svc": detail, "weapon_roll_filter_svc": filter_svc,
    }))
    return SimpleNamespace(manifest=manifest, definitions=definitions, profile=profile, detail=detail, ctx=ctx)


async def test_empty_type_scans_all_owned_weapons_with_actual_sockets(account):
    result = await account.detail.get_weapon_details_by_type("test", "")
    assert len(result.weapons) == 130
    assert {w.location.lower() for w in result.weapons} == {"仓库", "titan", "hunter", "warlock"}
    assert next(w for w in result.weapons if w.instance_id == "130").is_equipped
    weapon = next(w for w in result.weapons if w.instance_id == "1")
    assert weapon.perks_complete
    assert [s.plug_name for s in weapon.sockets] == ["辉耀炽热"]


async def test_named_type_is_not_capped_at_one_hundred_definitions(account):
    result = await account.detail.get_weapon_details_by_type("test", "手炮")
    assert len(result.weapons) == 129


@pytest.mark.parametrize("location, checked, matched", [
    ("vault", 127, 64), ("仓库", 127, 64), ("", 130, 65),
    ("all", 130, 65), ("猎人", 1, 1), ("warlock", 1, 0),
])
@pytest.mark.parametrize("perk", ["辉耀炽热", "Incandescent"])
async def test_assistant_filters_perk_name_and_location(account, location, checked, matched, perk):
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location=location,
        perk_name=perk, limit=2, ctx=account.ctx,
    )
    assert response["ok"], response
    data = response["data"]
    assert data["scope"] == "owned_inventory"
    assert data["perk_scope"] == "current_sockets"
    assert data["coverage_complete"] is True
    assert data["checked_count"] == checked
    assert data["matched_count"] == matched
    assert len(data["matched"]) == min(2, matched)
    assert data["filters"]["required_perks"] == [perk.lower()]


async def test_filter_does_not_match_manifest_only_perk(account):
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location="vault",
        perk_name="弹药重构", ctx=account.ctx,
    )
    assert response["data"]["checked_count"] == 127
    assert response["data"]["matched_count"] == 0
    assert response["data"]["coverage_complete"] is True


async def test_no_perk_filter_does_not_require_socket_data(account):
    account.profile["itemComponents"]["sockets"]["data"] = {}
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location="vault", ctx=account.ctx,
    )
    data = response["data"]
    assert data["coverage_complete"] is True
    assert data["checked_count"] == 127
    assert data["matched_count"] == 127


async def test_known_unnamed_placeholder_does_not_hide_actual_perks(account):
    account.definitions[903] = {"displayProperties": {"name": ""}, "plug": {}}
    account.profile["itemComponents"]["sockets"]["data"]["1"]["sockets"].append({"plugHash": 903})
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location="vault",
        perk_name="辉耀炽热", ctx=account.ctx,
    )
    data = response["data"]
    assert data["coverage_complete"] is True
    assert data["checked_count"] == 127
    assert data["matched_count"] == 64
    assert next(w for w in data["matched"] if w["instance_id"] == "1")["perks"] == ["辉耀炽热"]


@pytest.mark.parametrize("missing", [None, {}, {"sockets": []}, {"sockets": [{"plugHash": 9999}]}])
@pytest.mark.parametrize("filters", [{"perk_name": "辉耀炽热"}, {"excluded_perks": ["辉耀炽热"]}])
async def test_missing_sockets_are_unknown_not_nonmatches(account, missing, filters):
    sockets = account.profile["itemComponents"]["sockets"]["data"]
    if missing is None:
        del sockets["1"]
    else:
        sockets["1"] = missing
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location="vault",
        limit=200, ctx=account.ctx, **filters,
    )
    data = response["data"]
    assert data["coverage_complete"] is False
    assert data["scoped_count"] == 127
    assert data["checked_count"] == 126
    assert data["unknown_count"] == 1
    assert data["unknown"][0]["instance_id"] == "1"
    assert "1" not in {w["instance_id"] for w in data["matched"] + data["not_matched"]}
    assert response["warnings"]


async def test_missing_inventory_scope_is_not_an_empty_result(account):
    account.profile.pop("profileInventory")
    account.profile.pop("characterInventories")
    with pytest.raises(AuthenticationError):
        await account.detail.get_weapon_details_by_type("test", "")


async def test_partial_character_inventory_is_not_complete(account):
    del account.profile["characterInventories"]["data"]["h"]
    with pytest.raises(ConfigError):
        await account.detail.get_weapon_details_by_type("test", "")


async def test_unknown_vault_definition_is_not_silently_skipped(account):
    account.profile["profileInventory"]["data"]["items"].append({
        "itemHash": 9999, "itemInstanceId": "unknown", "bucketHash": VAULT_BUCKET,
    })
    with pytest.raises(ConfigError):
        await account.detail.get_weapon_details_by_type("test", "")


async def test_unknown_location_is_an_error(account):
    response = await weapon_assistant(
        intent="filter_rolls", player_name="test", location="vaul", ctx=account.ctx,
    )
    assert response["ok"] is False
