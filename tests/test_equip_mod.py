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
#: 另一颗调谐（同类别、假 Manifest 认识）：当"清单里有调谐、但没有你要的那一颗"里的那个"有"
OTHER_TUNING = 4002
# 真机 Manifest 里 "+职业 / -手雷" 的 hash（实机抓过）
TUNING_CLASS_UP_GRENADE_DOWN = 1879022254
# 真机抓过的一对：`+超能 / -生命值` 搜索给有符号、组件 310 给无符号（光芒领主面具 6917530188460608169）
SIGNED_TUNING = -268553035

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
    OTHER_TUNING: {
        "displayProperties": {"name": "+纪律 / -力量"},
        "itemType": 19,
        "plug": {"plugCategoryHash": 3481777685, "energyCost": {"energyCost": 0}},
        "investmentStats": [],
    },
    SIGNED_TUNING: {
        "displayProperties": {"name": "+超能 / -生命值"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "core.gear_systems.armor_tiering.plugs.tuning.mods",
                 "plugCategoryHash": 3481777685, "energyCost": {"energyCost": 0}},
        "investmentStats": [],
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
        if definition is None and item_hash > 2**31:
            # 真机 manifest 两种写法都认（有符号的 -268553035 与无符号的 4026414261 是同一颗）。
            # 夹具不认的话，310 里那颗按无符号查不到类别 → 整份清单被当成"读不到" →
            # 判据短路成"可写"，守门就成了摆设（这条测试第一版就是这么假通过的）。
            definition = DEFS.get(item_hash - 2**32)
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
        if query.replace(" ", "") == "+超能/-生命值":
            return [{"itemHash": SIGNED_TUNING, "itemType": 19}]
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


async def test_tuning_plan_is_writable_when_nothing_says_otherwise() -> None:
    """调谐**能写**：`writable=True`，工具层据此走"确认后写入"那套。

    判据只有一条 —— **这颗在不在"这件护甲允许的清单"里**（组件 310，ADR-014 修订）。
    读不到那份清单时**不拦**（缺数据 ≠ 不许），交给上游说话。
    """
    plan = await _service().plan("Tester#1234", "6917", "+职业 / -手雷", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["writable"] is True
    assert plan["writable_reason"] == ""
    # 被推翻的说法一个都不许回来。
    for stale in ("1663", "只允许游戏内改", "还没验证过", "只能换成你已经拥有的那一颗"):
        assert stale not in str(plan), f"旧口径又出现了：{stale}"


async def test_a_tuning_the_role_list_says_no_to_is_still_writable() -> None:
    """**判据只有组件 310 一条**：那份"这一位能不能插"的清单在调谐槽上不可信，不许拿它拦调谐。

    2026-09-22 真机（`scripts/verify_tuning_write.py warlock --piece 6917530188462629544
    --to 891771298 --apply`）：至高碎片槽 11 的 `+武器 / -超能` 被这份清单判"不可插入"
    （连**正装着的那颗**都不在清单里），写入却成功 —— `ErrorCode=1`，回读六维
    超能 25→20、近战 0→5。拿它拦的后果是把能装的调谐说成"游戏里同样装不上"。

    这里把角色级清单桩成"这个 plug set 只认别的调谐"（→ 判 False），而那件护甲的 310 清单
    里有这颗 → 期望可写，且**不报** `unlock_state`：false 摆在 `to` 里会被读成"写不了"。
    """
    service = _service()
    profile = await service._resolver.get_profile("1", 3, [])
    profile["characterPlugSets"] = {
        "data": {"char-hunter": {"plugs": {"1155052024": [{"plugItemHash": OTHER_TUNING}]}}}
    }
    profile["itemComponents"]["reusablePlugs"] = {
        "data": {"6917": {"plugs": {"2": [
            {"plugItemHash": TUNING_CLASS_UP_GRENADE_DOWN, "canInsert": True},
        ]}}}
    }

    async def stable(membership_id, membership_type, components):
        return profile

    service._resolver.get_profile = stable  # type: ignore[assignment]
    plan = await service.plan("Tester#1234", "6917", "+职业 / -手雷", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["writable"] is True, plan["writable_reason"]
    assert plan["writable_reason"] == ""
    assert plan["to"]["unlock_state"] is None
    for stale in ("1676", "游戏里同样装不上", "不在 Bungie 给这一位角色的可插入清单里"):
        assert stale not in str(plan), f"该判据又拿调谐当模组拦了：{stale}"


async def test_a_signed_hash_tuning_that_is_in_the_list_is_writable() -> None:
    """清单里**有**这颗、但它的 hash 是负数（有符号）时，必须判成可写。

    真机踩到（2026-09-22）：光芒领主面具 `6917530188460608169` 的 310 清单里有
    `+超能 / -生命值`（组件 310 给的是**无符号** `4026414261`），而 `manifest.search`
    给的是**有符号** `-268553035` —— 裸 `in` 永远 False，工具于是把清单里有的调谐
    判成"装不到这件上"，还附了一句 1675 的理由（错判、且是**过严**那一侧）。
    """
    service = _service()
    profile = await service._resolver.get_profile("1", 3, [])
    # 310 给无符号（真机口径），搜索给有符号 —— 这正是两边不相等的那一对
    profile["itemComponents"]["reusablePlugs"] = {
        "data": {"6917": {"plugs": {"2": [
            {"plugItemHash": SIGNED_TUNING & 0xFFFFFFFF, "canInsert": True},
        ]}}}
    }

    async def stable(membership_id, membership_type, components):
        return profile

    service._resolver.get_profile = stable  # type: ignore[assignment]
    plan = await service.plan("Tester#1234", "6917", "+超能 / -生命值", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["to"]["hash"] == SIGNED_TUNING, "方案里照旧报搜索给的那颗（有符号）"
    assert plan["writable"] is True, plan["writable_reason"]
    assert plan["writable_reason"] == ""


async def test_tuning_plan_is_blocked_when_the_piece_does_not_allow_it() -> None:
    """清单里**没有**这颗 → 明确不写（省得用户确认完再撞 1675），并把 1675 的含义说清。

    注意两件事（写这条测试时踩过）：
    ① 桩的 `get_profile` 每次返回**新字典**，要在替换后的函数里返回同一个对象，改动才生效；
    ② 清单里那颗必须是**假 Manifest 认识的调谐类别**（否则类别查成 0，会被当成"没有清单"）。
    """
    service = _service()
    profile = await service._resolver.get_profile("1", 3, [])
    # 这件护甲的调谐清单里只有 4002（另一颗），而我们要换成的是「+职业 / -手雷」（4001）
    profile["itemComponents"]["reusablePlugs"] = {
        "data": {"6917": {"plugs": {"2": [{"plugItemHash": OTHER_TUNING, "canInsert": True}]}}}
    }

    async def stable(membership_id, membership_type, components):
        return profile

    service._resolver.get_profile = stable  # type: ignore[assignment]
    plan = await service.plan("Tester#1234", "6917", "+职业 / -手雷", "hunter")

    assert plan["kind"] == "tuning"
    assert plan["writable"] is False
    reason = plan["writable_reason"]
    assert "310" in reason and "1675" in reason
    assert "不是「你没材料」" in reason


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


async def test_tool_asks_for_confirmation_before_writing_tuning() -> None:
    """调谐现在能写：`confirmed=false` 时只签发确认请求，**一个字节都不写**。"""
    bungie = _Bungie()
    service = _service(bungie)

    response = await branches.equip_mod(
        _ctx(service).request_context.lifespan_context,
        "Tester#1234", "6917", "+职业 / -手雷", "hunter", False,
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "confirmation_required"
    assert response["candidates"][0]["writable"] is True
    assert not bungie.inserts, "没确认就一个字节都不能写"


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
