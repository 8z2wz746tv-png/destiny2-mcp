"""换模组：校验要在写入前说清，确认前一个字节都不能写。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from destiny_mcp.exceptions import InvalidArgumentError, TransferError
from destiny_mcp.services.armor_mod_service import ArmorModService
from destiny_mcp.tools import _armor_branches as branches

STAT_GRENADE = 1735777505
STAT_CLASS = 1943323491

MOD_HASH = 5001
EMPTY_GENERAL = 2001
EMPTY_LEGS = 2002
TUNING = 4001
# 真机 Manifest 里 "+职业 / -手雷" 的 hash（实机抓过）
TUNING_CLASS_UP_GRENADE_DOWN = 1879022254

DEFS: dict[int, dict[str, Any]] = {
    157934631: {
        "displayProperties": {"name": "至高狂徒腿铠"},
        "itemType": 2,
        "inventory": {"bucketTypeHash": 20886954},
    },
    MOD_HASH: {
        "displayProperties": {"name": "纪律模组"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                 "plugCategoryHash": 2487827355, "energyCost": {"energyCost": 3}},
        "investmentStats": [{"statTypeHash": STAT_GRENADE, "value": 10}],
    },
    EMPTY_GENERAL: {
        "displayProperties": {"name": "空模组插槽"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                 "plugCategoryHash": 2487827355, "energyCost": {"energyCost": 0}},
    },
    EMPTY_LEGS: {
        "displayProperties": {"name": "空模组插槽"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "enhancements.v2_legs",
                 "plugCategoryHash": 2111701510, "energyCost": {"energyCost": 0}},
    },
    TUNING: {
        "displayProperties": {"name": "空调整模组插槽"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "core.gear_systems.armor_tiering.plugs.tuning.mods",
                 "plugCategoryHash": 3481777685, "energyCost": {"energyCost": 0}},
    },
    TUNING_CLASS_UP_GRENADE_DOWN: {
        "displayProperties": {"name": "+职业 / -手雷"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "core.gear_systems.armor_tiering.plugs.tuning.mods",
                 "plugCategoryHash": 3481777685, "energyCost": {"energyCost": 0}},
        "investmentStats": [
            {"statTypeHash": STAT_CLASS, "value": 5},
            {"statTypeHash": STAT_GRENADE, "value": -5},
        ],
    },
}

# 定义里的插槽：0 号槽接受 general 类模组（plug set 里有 MOD_HASH）
SOCKET_ENTRIES = [
    {"reusablePlugSetHash": 900, "plugSources": 2},
    {"reusablePlugSetHash": 901, "plugSources": 2},
    # 2 号槽 = 调谐槽（实机 plug set 就是 1155052024）
    {"reusablePlugSetHash": 1155052024, "plugSources": 7},
]
PLUG_SETS = {
    900: {"reusablePlugItems": [{"plugItemHash": EMPTY_GENERAL}, {"plugItemHash": MOD_HASH}]},
    901: {"reusablePlugItems": [{"plugItemHash": EMPTY_LEGS}]},
    1155052024: {
        "reusablePlugItems": [
            {"plugItemHash": TUNING},
            {"plugItemHash": TUNING_CLASS_UP_GRENADE_DOWN},
        ]
    },
}


class _Manifest:
    def get_item_definition(self, item_hash: int):
        definition = DEFS.get(item_hash)
        if definition is None:
            return None
        if item_hash == 157934631:
            return {**definition, "sockets": {"socketEntries": SOCKET_ENTRIES}}
        return definition

    def get_item_info(self, item_hash: int):
        return {}

    def get_english_name(self, item_hash: int):
        return ""

    def get_definition(self, table: str, key: int):
        return PLUG_SETS.get(key)

    def search(self, query: str, limit: int = 0, item_type: int | None = None):
        if "纪律" in query:
            return [{"itemHash": MOD_HASH, "itemType": 19}]
        if query.replace(" ", "") == "+职业/-手雷":
            return [{"itemHash": TUNING_CLASS_UP_GRENADE_DOWN, "itemType": 19}]
        return []

    def get_item_name(self, item_hash: int):
        definition = DEFS.get(item_hash) or {}
        return (definition.get("displayProperties") or {}).get("name", "")


class _Resolver:
    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def resolve_character_id(self, membership_id: str, membership_type: int, character: str):
        return "char-hunter"

    async def get_profile(self, membership_id: str, membership_type: int, components: list[int]):
        return {
            "characterEquipment": {"data": {"char-hunter": {"items": [
                {"itemInstanceId": "6917", "itemHash": 157934631},
            ]}}},
            "characterInventories": {"data": {"char-hunter": {"items": []}}},
            "itemComponents": {
                "instances": {"data": {"6917": {"gearTier": 5,
                                                "primaryStat": {"value": 550},
                                                "energy": {"energyCapacity": 11, "energyUsed": 0,
                                                           "energyUnused": 11}}}},
                "sockets": {"data": {"6917": {"sockets": [
                    {"plugHash": EMPTY_GENERAL}, {"plugHash": EMPTY_LEGS},
                    {"plugHash": TUNING},
                ]}}},
            },
        }


class _Bungie:
    def __init__(self) -> None:
        self.inserts: list[tuple] = []

    # Bungie 的真实形状：成功是 {"ErrorCode": 1, "Message": "Ok"}（失败也是这个信封，
    # 只是 ErrorCode 不同）—— 以前这里回 {"success": True}，把"没检查 ErrorCode"的
    # 真 bug 遮住了（0.1.8 实机才发现）。
    async def insert_socket_plug(self, *args, **kwargs):
        self.inserts.append(("paid", args))
        return {"ErrorCode": 1, "Message": "Ok"}

    async def insert_socket_plug_free(self, *args, **kwargs):
        self.inserts.append(("free", args))
        return {"ErrorCode": 1, "Message": "Ok"}


def _service(bungie: _Bungie | None = None) -> ArmorModService:
    return ArmorModService(bungie or _Bungie(), _Manifest(), _Resolver())


async def test_plan_finds_the_socket_and_reports_energy_change() -> None:
    plan = await _service().plan("Tester#1234", "6917", "纪律模组", "hunter")

    assert plan["socket_index"] == 0
    assert plan["to"]["hash"] == MOD_HASH
    assert plan["to"]["energy_cost"] == 3
    assert plan["energy"] == {"capacity": 11, "used": 0, "after": 3}
    assert plan["slot"] == "legs"


async def test_plan_refuses_when_energy_is_not_enough() -> None:
    service = _service()
    profile = await service._resolver.get_profile("1", 3, [])
    profile["itemComponents"]["instances"]["data"]["6917"]["energy"]["energyUsed"] = 10

    async def tight(membership_id, membership_type, components):
        return profile

    service._resolver.get_profile = tight  # type: ignore[assignment]

    with pytest.raises(InvalidArgumentError, match="能量不够"):
        await service.plan("Tester#1234", "6917", "纪律模组", "hunter")


async def test_plan_refuses_when_the_piece_is_not_on_that_character() -> None:
    with pytest.raises(InvalidArgumentError, match="不在该角色身上"):
        await _service().plan("Tester#1234", "别处的实例", "纪律模组", "hunter")


async def test_plan_refuses_when_no_socket_accepts_the_mod() -> None:
    service = _service()
    service._manifest = _Manifest()
    with pytest.raises(InvalidArgumentError, match="没找到护甲模组"):
        await service.plan("Tester#1234", "6917", "不存在的模组", "hunter")


async def test_apply_inserts_into_the_planned_socket() -> None:
    bungie = _Bungie()
    service = _service(bungie)
    plan = await service.plan("Tester#1234", "6917", "纪律模组", "hunter")

    result = await service.apply(plan)

    assert result["success"] is True
    assert bungie.inserts and bungie.inserts[0][0] == "free", (
        "护甲模组走 free 端点（Bungie 的 free 指没有材料消耗，官方文档明确覆盖 Armor Mods）"
    )
    kind, args = bungie.inserts[0]
    assert args[0] == "6917" and args[1] == MOD_HASH and args[2] == 0


async def test_apply_wraps_upstream_failure() -> None:
    class _Broken(_Bungie):
        async def insert_socket_plug_free(self, *args, **kwargs):
            raise RuntimeError("Bungie 说不行")

    service = _service(_Broken())
    plan = await service.plan("Tester#1234", "6917", "纪律模组", "hunter")

    with pytest.raises(TransferError, match="Bungie 说不行"):
        await service.apply(plan)


def _ctx(service: ArmorModService) -> SimpleNamespace:
    services = {
        "armor_mod_svc": service,
        "manifest": _Manifest(),
        "starside_svc": None,
    }
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=services))


async def test_tool_returns_a_readable_confirmation_without_writing() -> None:
    bungie = _Bungie()
    response = await branches.equip_mod(
        _ctx(_service(bungie)).request_context.lifespan_context,
        "Tester#1234", "6917", "纪律模组", "hunter", False,
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "confirmation_required"
    candidate = response["candidates"][0]
    assert candidate["energy"] == {"capacity": 11, "used": 0, "after": 3}
    assert "至高狂徒腿铠" in candidate["summary"]
    assert "纪律模组" in candidate["summary"] and "3/11" in candidate["summary"]
    assert candidate["to"]["stat_bonus"] == {"grenade": 10}
    assert bungie.inserts == [], "确认前不许写"


async def test_tool_applies_after_confirmation() -> None:
    bungie = _Bungie()
    response = await branches.equip_mod(
        _ctx(_service(bungie)).request_context.lifespan_context,
        "Tester#1234", "6917", "纪律模组", "hunter", True,
    )

    assert response["ok"] is True
    assert "已把" in response["summary"]
    assert len(bungie.inserts) == 1


async def test_prefers_the_variant_that_actually_grants_stats() -> None:
    """同名多版本（实测「手雷模组」有 +0/1 能量 与 +10/3 能量 两个）：选真有加成的。"""
    weak, strong = 6001, 6002

    class _TwoVariants(_Manifest):
        def get_item_definition(self, item_hash: int):
            if item_hash == weak:
                return {"displayProperties": {"name": "手雷模组"}, "itemType": 19,
                        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                                 "plugCategoryHash": 2487827355,
                                 "energyCost": {"energyCost": 1}}}
            if item_hash == strong:
                return {"displayProperties": {"name": "手雷模组"}, "itemType": 19,
                        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                                 "plugCategoryHash": 2487827355,
                                 "energyCost": {"energyCost": 3}},
                        "investmentStats": [{"statTypeHash": STAT_GRENADE, "value": 10}]}
            return super().get_item_definition(item_hash)

        def search(self, query: str, limit: int = 0, item_type: int | None = None):
            return [{"itemHash": weak, "itemType": 19}, {"itemHash": strong, "itemType": 19}]

        def get_definition(self, table: str, key: int):
            if key == 900:
                return {"reusablePlugItems": [{"plugItemHash": EMPTY_GENERAL},
                                              {"plugItemHash": weak},
                                              {"plugItemHash": strong}]}
            return super().get_definition(table, key)

    service = _service()
    service._manifest = _TwoVariants()

    plan = await service.plan("Tester#1234", "6917", "手雷模组", "hunter")

    assert plan["to"]["hash"] == strong, "要选真正加属性的版本"
    assert plan["to"]["energy_cost"] == 3
    assert plan["alternatives"], "同名其它可行版本要列出来，别让调用方以为只有一个"


async def test_legacy_stat_names_still_find_the_mod() -> None:
    """玩家还在用旧六维名（纪律=手雷）：要能自动换成新名再找。"""
    service = _service()
    service._manifest = type("M", (_Manifest,), {
        "search": lambda self, query, limit=0: (
            [{"itemHash": MOD_HASH, "itemType": 19}] if "手雷" in query else []
        ),
    })()

    plan = await service.plan("Tester#1234", "6917", "纪律模组", "hunter")

    assert plan["to"]["hash"] == MOD_HASH


def test_slot_keys_are_added_without_touching_the_original_fields() -> None:
    """求解器的复数槽位名旁边补单数键与中文名，原字段不动。"""
    payload = {
        "results": [
            {"build": {"items": [{"slot": "helmets", "name": "铁能面罩"},
                                 {"slot": "class_items", "name": "印记"}]}},
        ],
        "farm_options": [{"replacement_slot": "legs"}],
    }

    enriched = branches.with_slot_keys(payload)

    items = enriched["results"][0]["build"]["items"]
    assert items[0]["slot"] == "helmets", "原字段必须保留"
    assert items[0]["slot_key"] == "helmet"
    assert items[0]["slot_display"] == "头盔"
    assert items[1]["slot_key"] == "class_item"
    assert enriched["farm_options"][0]["replacement_slot"] == "legs"
    assert enriched["farm_options"][0]["replacement_slot_key"] == "legs"
    assert enriched["farm_options"][0]["replacement_slot_display"] == "腿部护甲"


async def test_alternatives_use_readable_six_stat_keys() -> None:
    """alternatives 的加成要和 `to.stat_bonus` 同一套键名，且不含非六维属性。"""
    weak, strong = 6001, 6002

    class _TwoVariants(_Manifest):
        def get_item_definition(self, item_hash: int):
            if item_hash == weak:
                return {"displayProperties": {"name": "手雷模组"}, "itemType": 19,
                        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                                 "plugCategoryHash": 2487827355,
                                 "energyCost": {"energyCost": 1}},
                        "investmentStats": [{"statTypeHash": 3578062600, "value": 1}]}
            if item_hash == strong:
                return {"displayProperties": {"name": "手雷模组"}, "itemType": 19,
                        "plug": {"plugCategoryIdentifier": "enhancements.v2_general",
                                 "plugCategoryHash": 2487827355,
                                 "energyCost": {"energyCost": 3}},
                        "investmentStats": [{"statTypeHash": STAT_GRENADE, "value": 10}]}
            return super().get_item_definition(item_hash)

        def search(self, query: str, limit: int = 0, item_type: int | None = None):
            return [{"itemHash": weak, "itemType": 19}, {"itemHash": strong, "itemType": 19}]

        def get_definition(self, table: str, key: int):
            if key == 900:
                return {"reusablePlugItems": [{"plugItemHash": EMPTY_GENERAL},
                                              {"plugItemHash": weak},
                                              {"plugItemHash": strong}]}
            return super().get_definition(table, key)

    service = _service()
    service._manifest = _TwoVariants()
    plan = await service.plan("Tester#1234", "6917", "手雷模组", "hunter")

    assert plan["to"]["stat_bonus"] == {} or plan["to"]["stat_bonus"] == {"grenade": 10}
    for alternative in plan["alternatives"]:
        assert "stat_bonus_hashes" not in alternative, "原始 hash 字段已换成可读键"
        assert set(alternative["stat_bonus"]) <= {
            "weapons", "health", "class_stat", "grenade", "super_stat", "melee",
        }, "非六维属性（如 3578062600 费用）不许出现"


# ── 调谐：同一个写入路径，但免费、且不进"已装模组"快照 ─────────────────


async def test_plan_can_change_tuning_by_its_full_name() -> None:
    plan = await _service().plan("Tester#1234", "6917", "+职业 / -手雷", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["socket_index"] == 2, "调谐要装进调谐槽（实机是 11 号槽，这份替身是 2 号）"
    assert plan["to"]["hash"] == TUNING_CLASS_UP_GRENADE_DOWN
    assert plan["to"]["energy_cost"] == 0
    assert plan["to"]["stat_bonus"] == {"class_stat": 5, "grenade": -5}
    assert plan["energy"]["after"] == plan["energy"]["used"], "调谐不吃能量"
    assert "零和" in plan["note"]


async def test_tuning_plan_is_marked_not_writable() -> None:
    """调谐带 `writable=False` + 原因，工具层据此**不**走"确认后写入"那套。

    理由的口径（2026-09-22 真机验证，ADR-014）：**换得动，但只能换成你已经拥有的那一颗** ——
    换成身上别的护甲正装着的那颗成功（`ErrorCode=1`、回读插槽与六维都对、换回也成），
    换成没有的一颗回 **1675** 要材料。本项目还没开替你写调谐这条路，所以照旧只给方案。
    """
    plan = await _service().plan("Tester#1234", "6917", "+职业 / -手雷", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["writable"] is False
    reason = plan["writable_reason"]
    assert "只能换成你已经拥有的那一颗" in reason
    assert "1675" in reason
    # 被推翻的两代说法都不许回来（1663 出自字段名 bug；"还没验证过"已被真机验证取代）。
    assert "1663" not in reason
    assert "只允许游戏内改" not in reason
    assert "还没验证过" not in reason


async def test_apply_raises_when_bungie_returns_an_error_envelope() -> None:
    """回归：Bungie 把错误放在 200 响应的信封里（ErrorCode != 1），不能报成功。

    实机踩过：换调谐时账号一个字节没变，工具却回 `success: true`。
    """
    class _Refusing(_Bungie):
        async def insert_socket_plug(self, *args, **kwargs):
            self.inserts.append(("paid", args))
            return {
                "ErrorCode": 1663,
                "Message": "This action can only be done in-game.",
            }

        async def insert_socket_plug_free(self, *args, **kwargs):
            self.inserts.append(("free", args))
            return {
                "ErrorCode": 1663,
                "Message": "This action can only be done in-game.",
            }

    service = _service(_Refusing())
    plan = await service.plan("Tester#1234", "6917", "纪律模组", "hunter")

    with pytest.raises(TransferError, match="in-game"):
        await service.apply(plan)


async def test_apply_maps_a_missing_scope_to_a_readable_message() -> None:
    """付费插槽要 AdvancedWriteActions：403 要说清是权限问题，不是"稍后重试"。"""
    class _NoScope(_Bungie):
        # 两种端点都要覆盖：真机上令牌缺 scope 时 **free 接口也照样 403**
        # （`AccessNotPermittedByApplicationScope`），不是"free 就能绕过去"。
        async def insert_socket_plug(self, *args, **kwargs):
            self.inserts.append(("paid", args))
            return {
                "ErrorCode": 2108,
                "Message": "Forbidden: AccessNotPermittedByApplicationScope "
                           "(RequiredScope: AdvancedWriteActions)",
            }

        async def insert_socket_plug_free(self, *args, **kwargs):
            self.inserts.append(("free", args))
            return {
                "ErrorCode": 2108,
                "Message": "Forbidden: AccessNotPermittedByApplicationScope "
                           "(RequiredScope: AdvancedWriteActions)",
            }

    service = _service(_NoScope())
    plan = await service.plan("Tester#1234", "6917", "纪律模组", "hunter")

    with pytest.raises(TransferError, match="AdvancedWriteActions"):
        await service.apply(plan)


async def test_tool_returns_the_tuning_plan_without_asking_for_confirmation() -> None:
    """调谐写不进去：工具直接给方案 + 警告，而不是让用户确认一个做不到的写入。"""
    bungie = _Bungie()
    service = _service(bungie)

    response = await branches.equip_mod(
        _ctx(service).request_context.lifespan_context,
        "Tester#1234", "6917", "+职业 / -手雷", "hunter", False,
    )

    assert response["ok"] is True
    assert response["data"]["armor_mod"]["written"] is False
    assert response["warnings"], "必须说清「只能在游戏内改」"
    assert not bungie.inserts, "一个字节都不能写"


async def test_ambiguous_tuning_query_makes_the_caller_pick_one() -> None:
    """"手雷调谐"没说要减哪一项：不许替用户猜，列出 5 个选项。"""
    with pytest.raises(InvalidArgumentError, match="零和"):
        await _service().plan("Tester#1234", "6917", "手雷调谐", "hunter")


async def test_tuning_shows_up_in_mod_snapshots_like_any_other_plug() -> None:
    """实测口径：调谐本来就在护甲模组类别里，所以读取快照时一视同仁（含调谐）。"""
    service = _service()
    sockets = {"6917": {"sockets": [
        {"plugHash": EMPTY_GENERAL},
        {"plugHash": EMPTY_LEGS},
        {"plugHash": TUNING},
    ]}}

    mods = service.read_armor_mods("6917", sockets)

    assert mods == [EMPTY_GENERAL, EMPTY_LEGS, TUNING], (
        "调谐的 plug 类别 3481777685 属于 _ARMOR_MOD_CATEGORIES，"
        "所以 read_armor_mods 会像其它插件一样读出来"
    )
