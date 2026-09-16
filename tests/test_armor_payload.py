"""护甲载荷工厂：三层属性、分族、插槽短键必须说一不二。

数据取自实机勘测（docs/plans/ARMOR_FORMAT_PLAN.md §1）：T5 词条 = 主 30 / 副 25 / 第三 20，
大师满级给最低三项 +5，调谐 ±5，属性模组 +10，词条本体槽不可改。
这里用替身 lookup，任何机器都能跑。
"""

from __future__ import annotations

from destiny_mcp.services.armor_payload import (
    ARMOR_SCHEMA_VERSION,
    armor_payload,
    armor_system_of,
    slot_key_from_bucket,
    slot_key_from_solver,
    stats_from_component,
)

STAT_WEAPONS = 2996146975
STAT_HEALTH = 392767087
STAT_CLASS = 1943323491
STAT_GRENADE = 1735777505
STAT_SUPER = 144602215
STAT_MELEE = 4244567218

H = {
    "item": 157934631,
    "archetype_powerhouse": 544009373,
    "roll_weapons_30": 1001,
    "roll_super_25": 1002,
    "roll_grenade_20": 1003,
    "empty_general": 2001,
    "empty_legs": 2002,
    "masterwork_cap_only": 3001,
    "masterwork_full": 3002,
    "tuning_plus_super_minus_health": 4001,
    "mod_weapons_10": 5001,
    "shader": 6001,
    "skin": 6002,
    "raid_v700": 7001,
    "intrinsic_super_melee": 8001,
}


def _plug(category: str, name: str = "", stats: dict[int, int] | None = None, energy: int = 0) -> dict:
    return {
        "displayProperties": {"name": name},
        "plug": {"plugCategoryIdentifier": category, "energyCost": {"energyCost": energy}},
        "investmentStats": [
            {"statTypeHash": h, "value": v} for h, v in (stats or {}).items()
        ],
    }


DEFS = {
    H["item"]: {
        "displayProperties": {"name": "至高狂徒腿铠"},
        "itemType": 2,
    },
    H["archetype_powerhouse"]: _plug("armor_archetypes", "高能者"),
    H["roll_weapons_30"]: _plug("armor_stats", "", {STAT_WEAPONS: 30}),
    H["roll_super_25"]: _plug("armor_stats", "", {STAT_SUPER: 25}),
    H["roll_grenade_20"]: _plug("armor_stats", "", {STAT_GRENADE: 20}),
    H["empty_general"]: _plug("enhancements.v2_general", "空模组插槽"),
    H["empty_legs"]: _plug("enhancements.v2_legs", "空模组插槽", energy=0),
    H["masterwork_cap_only"]: _plug("v460.plugs.armor.masterworks", "升级护甲", {16120457: 11}),
    H["masterwork_full"]: _plug(
        "v460.plugs.armor.masterworks", "升级护甲",
        {16120457: 11, STAT_WEAPONS: 5, STAT_HEALTH: 5, STAT_GRENADE: 5,
         STAT_SUPER: 5, STAT_CLASS: 5, STAT_MELEE: 5},
    ),
    H["tuning_plus_super_minus_health"]: _plug(
        "core.gear_systems.armor_tiering.plugs.tuning.mods", "+超能 / -生命值",
        {STAT_SUPER: 5, STAT_HEALTH: -5},
    ),
    H["mod_weapons_10"]: _plug("enhancements.v2_general", "武器模组", {STAT_WEAPONS: 10}, energy=3),
    H["shader"]: _plug("shader", "默认着色器"),
    H["skin"]: _plug("armor_skins_empty", "默认皮肤"),
    H["raid_v700"]: _plug("enhancements.raid_v700", "纠缠不清"),
}


def lookup(plug_hash: int):
    return DEFS.get(plug_hash)


def _sockets(*hashes: int) -> list[dict]:
    return [{"plugHash": h} for h in hashes]


def _stats(values: dict[int, int]) -> dict:
    return {str(h): {"statHash": h, "value": v} for h, v in values.items()}


def test_slot_keys_are_singular_and_shared() -> None:
    assert slot_key_from_bucket("Leg Armor") == "legs"
    assert slot_key_from_bucket("Helmet") == "helmet"
    assert slot_key_from_solver("chests") == "chest"
    assert slot_key_from_solver("class_items") == "class_item"


def test_armor_system_follows_the_structure_not_the_field() -> None:
    """分族看结构：实测 54 件老护甲也带 `gearTier: 0`，但它们是 15 槽布局。

    按"组件里有没有 gearTier 字段"判会把这 54 件误判成 3.0，然后给它们套一套
    不存在的规则（词条原型、调谐、词条反推）。
    """
    assert armor_system_of({"gearTier": 5}) == "armor_3"
    assert armor_system_of({"gearTier": 1}) == "armor_3"
    assert armor_system_of({"gearTier": 0}) == "legacy"
    assert armor_system_of({}) == "legacy"


def test_t5_piece_reads_roll_and_three_layers() -> None:
    """至高狂徒腿铠实录：词条 30/25/20，未满大师，能量 11。"""
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5, "energy": {"energyCapacity": 11, "energyUsed": 0, "energyUnused": 11}},
        sockets=_sockets(
            H["empty_general"], H["empty_legs"], H["shader"],
            H["masterwork_cap_only"], H["archetype_powerhouse"],
            H["roll_weapons_30"], H["roll_super_25"], H["roll_grenade_20"], H["skin"],
        ),
        stats=_stats({STAT_WEAPONS: 30, STAT_SUPER: 25, STAT_GRENADE: 20}),
        item_instance_id="6917530198796768597", location="hunter", power=550, bucket="Leg Armor",
    )

    assert payload["armor_schema_version"] == ARMOR_SCHEMA_VERSION
    identity = payload["identity"]
    assert identity["slot"] == "legs" and identity["slot_display"] == "腿部护甲"
    assert identity["gear_tier"] == 5 and identity["gear_tier_note"] is None
    assert identity["armor_system"] == "armor_3"
    assert identity["archetype"]["name"] == "高能者"
    assert payload["instance"]["power"] == 550
    assert payload["instance"]["energy"] == {
        "capacity": 11, "used": 0, "unused": 11, "type_hash": None,
    }
    stats = payload["stats"]
    assert stats["roll"]["weapons"] == 30 and stats["roll"]["super_stat"] == 25
    assert stats["roll"]["grenade"] == 20 and stats["roll"]["melee"] == 0, "六项都要在，缺的补 0"
    assert stats["final"]["weapons"] == 30 and stats["final"]["melee"] == 0
    assert stats["base"] == stats["roll"], "armor_3 的基础属性就是词条本体，直接读不推断"
    kinds = {row["kind"]: row for row in payload["sockets"]}
    assert kinds["archetype"]["editable"] is False
    assert kinds["roll"]["editable"] is False, "词条本体的槽不能标成可改"
    assert kinds["general"]["editable"] is True
    assert "raid_family" not in payload["instance"]


def test_masterworked_piece_keeps_roll_as_base_and_reports_applied_bonus() -> None:
    """满大师给最低三项 +5：base 仍是词条本身，大师的**实际生效**值单独报。"""
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5},
        sockets=_sockets(
            H["masterwork_full"], H["archetype_powerhouse"],
            H["roll_weapons_30"], H["roll_super_25"], H["roll_grenade_20"],
        ),
        stats=_stats({STAT_WEAPONS: 30, STAT_SUPER: 25, STAT_GRENADE: 20,
                      STAT_HEALTH: 5, STAT_CLASS: 5, STAT_MELEE: 5}),
        bucket="Leg Armor",
    )

    masterwork = payload["instance"]["masterwork"]
    assert masterwork["level"] == 5
    assert masterwork["stat_bonus"] == {"health": 5, "class_stat": 5, "melee": 5}, (
        "报的应该是实际生效的三项，而不是 plug 定义里写的六项各 +5"
    )
    assert payload["stats"]["final"]["health"] == 5
    assert payload["stats"]["base"]["health"] == 0
    assert payload["stats"]["base"]["weapons"] == 30
    assert payload["stats"]["roll"]["super_stat"] == 25


def test_tuning_is_exposed_with_its_delta() -> None:
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5},
        sockets=_sockets(
            H["archetype_powerhouse"], H["roll_weapons_30"], H["roll_super_25"], H["roll_grenade_20"],
            H["tuning_plus_super_minus_health"],
        ),
        stats=_stats({STAT_WEAPONS: 30, STAT_SUPER: 30, STAT_GRENADE: 20}),
        bucket="Leg Armor",
    )

    tuning = payload["instance"]["tuning"]
    assert tuning["name"] == "+超能 / -生命值"
    assert tuning["delta"] == {"super_stat": 5, "health": -5}
    assert payload["stats"]["base"]["super_stat"] == 25


def test_stat_mod_is_subtracted_and_reported() -> None:
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5},
        sockets=_sockets(
            H["mod_weapons_10"], H["archetype_powerhouse"],
            H["roll_weapons_30"], H["roll_super_25"], H["roll_grenade_20"],
        ),
        stats=_stats({STAT_WEAPONS: 40, STAT_SUPER: 25, STAT_GRENADE: 20}),
        bucket="Leg Armor",
    )

    assert payload["stats"]["final"]["weapons"] == 40
    assert payload["stats"]["base"]["weapons"] == 30


def test_legacy_armor_has_no_archetype_or_roll_layers() -> None:
    """老护甲：没有 gearTier、没有词条原型，不能按 3.0 的字段硬套。"""
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"energy": {"energyCapacity": 10, "energyUsed": 0, "energyUnused": 10}},
        sockets=_sockets(H["empty_general"], H["shader"], H["skin"]),
        stats=_stats({STAT_HEALTH: 27, STAT_MELEE: 10, STAT_GRENADE: 18}),
        bucket="Leg Armor",
    )

    assert payload["identity"]["armor_system"] == "legacy"
    assert payload["identity"]["gear_tier"] is None
    assert "不在分级体系内" in payload["identity"]["gear_tier_note"]
    assert "archetype" not in payload["identity"]
    assert payload["stats"]["roll"] is None, "老护甲没有词条槽，roll 必须为空"
    assert payload["stats"]["base"]["health"] == 27, "老护甲的 base = final − 已装模组"
    assert any("老护甲没有词条槽" in note for note in payload["stats"]["notes"]), (
        "口径要说清楚，不能让人以为这是掉落时的原始值"
    )


def test_raid_socket_is_reported_by_family() -> None:
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5},
        sockets=_sockets(H["raid_v700"], H["archetype_powerhouse"],
                         H["roll_weapons_30"], H["roll_super_25"], H["roll_grenade_20"]),
        stats=_stats({STAT_WEAPONS: 30, STAT_SUPER: 25, STAT_GRENADE: 20}),
        bucket="Chest Armor",
    )

    assert payload["instance"]["raid_family"] == "v700"
    assert payload["sockets"][0]["kind"] == "raid"


def test_include_sockets_false_keeps_the_list_light() -> None:
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup,
        instance={"gearTier": 5}, sockets=_sockets(H["archetype_powerhouse"]),
        bucket="Leg Armor", include_sockets=False, include_stats_layers=False,
    )

    assert "sockets" not in payload
    assert "stats" not in payload
    assert payload["identity"]["slot"] == "legs"


def test_stats_from_component_fills_every_stat() -> None:
    stats = stats_from_component({str(STAT_WEAPONS): {"value": 12}})
    assert stats["weapons"] == 12
    assert set(stats) == {"weapons", "health", "class_stat", "grenade", "super_stat", "melee"}
    assert stats["melee"] == 0


def test_exotic_intrinsics_count_as_the_fixed_roll() -> None:
    """异域护甲的固定分布藏在 `intrinsics` 里（实测：相对主义 = 超能30/近战25）。

    不把它算进 roll，大师的实际生效量就会被算成 30 点 —— 这正是实机验证时
    抓到的假象（27 件报出 level=30）。
    """
    table = {**DEFS, H["intrinsic_super_melee"]: _plug(
        "intrinsics", "至纯光能之灵", {STAT_SUPER: 30, STAT_MELEE: 25})}
    payload = armor_payload(
        item_hash=H["item"], lookup=lambda h: table.get(h),
        instance={"gearTier": 5},
        sockets=_sockets(
            H["masterwork_full"], H["intrinsic_super_melee"],
            H["archetype_powerhouse"], H["roll_grenade_20"],
        ),
        stats=_stats({STAT_WEAPONS: 5, STAT_HEALTH: 5, STAT_CLASS: 5,
                      STAT_GRENADE: 20, STAT_SUPER: 30, STAT_MELEE: 25}),
        bucket="Class Armor",
    )

    assert payload["stats"]["roll"]["super_stat"] == 30
    assert payload["stats"]["roll"]["melee"] == 25
    assert payload["stats"]["roll"]["grenade"] == 20
    masterwork = payload["instance"]["masterwork"]
    assert masterwork["level"] == 5, "大师等级是 1–5，不是被 intrinsics 撑出来的数"
    assert masterwork["stat_bonus"] == {"weapons": 5, "health": 5, "class_stat": 5}


def test_empty_socket_is_marked_empty_not_unknown() -> None:
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup, instance={"gearTier": 5},
        sockets=[{"plugHash": 0}], bucket="Leg Armor",
    )

    row = payload["sockets"][0]
    assert row["kind"] == "empty" and row["empty"] is True and row["editable"] is False


def test_payload_key_shape_is_stable() -> None:
    """键集合快照：后面几步只许加键，不许悄悄改结构。"""
    payload = armor_payload(
        item_hash=H["item"], lookup=lookup, instance={"gearTier": 5},
        sockets=_sockets(H["archetype_powerhouse"], H["roll_weapons_30"],
                         H["roll_super_25"], H["roll_grenade_20"]),
        stats=_stats({STAT_WEAPONS: 30, STAT_SUPER: 25, STAT_GRENADE: 20}),
        item_instance_id="1", location="vault", power=550, bucket="Leg Armor",
        set_info={"hash": 1, "name": "测试套装"},
    )

    assert set(payload) == {"identity", "instance", "stats", "sockets", "armor_schema_version"}
    assert set(payload["identity"]) == {
        "item_hash", "name", "name_en", "slot", "slot_display", "item_type_display",
        "gear_tier", "gear_tier_note", "armor_system", "icon_url", "archetype", "set",
    }
    assert set(payload["instance"]) == {
        "item_instance_id", "location", "character_id", "power", "is_equipped",
        "quantity", "energy",
    }
    assert set(payload["stats"]) == {"roll", "base", "final", "notes"}
    assert set(payload["sockets"][0]) == {
        "index", "kind", "editable", "plug_hash", "name", "energy_cost", "empty",
    }
