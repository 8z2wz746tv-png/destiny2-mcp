"""护甲模组的"这一位能不能插"：可插入清单、插入条件、以及写不进去时的说法。

根因（2026-09-21 真机）：Bungie 在 `characterPlugSets`（组件 207，随 305 一起回来）给出
**这一位角色实际能插入**的 plug —— 比 Manifest 的 plug set 小得多（实测头盔 21/61、
手臂 25/56）。以前只看 Manifest 的 plug set，于是：

- 把这一位没解锁的模组（条件里写着「必须在赛季神器中选择」）当能装，写上去被回
  1676 `DestinyFailedPlugInsertionRules`；
- 同名模组有"已解锁/未解锁"两档时，按属性加成挑，可能正好挑到没解锁的那档；
- `equip_loadout` 还把 1676 归成"Bungie 不允许 API 改护甲模组，请游戏内手动装" ——
  游戏里同样装不上。

这几种都不许再回来：`None`（上游没给这个 plug set）必须与 `False`（给了、里面没有）分开，
前者不判断、后者才是"装不了"。
"""

from __future__ import annotations

from typing import Any

import pytest

from destiny_mcp.exceptions import InvalidArgumentError, TransferError
from destiny_mcp.models import LoadoutItem
from destiny_mcp.services.armor_mod_service import ArmorModService
from destiny_mcp.services.armor_payload import (
    slot_key_from_bucket_hash,
    slot_key_from_bucket,
)
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService

HELMET_BUCKET = 3448274439          # 无符号（真机 Manifest 里就是这个值）
HELMET_BUCKET_SIGNED = -846692857   # 老代码抄的那个有符号值
GAUNTLETS_BUCKET = 3551918588
HELMET_SLOT_TYPE = 968742181
GENERAL_CATEGORY = 2487827355

HELMET_ITEM = 3091179819
PLUG_SET = 2037229815
LOCKED_MOD = 644105          # 真机：重型弹药搜寻者，不在可插入清单里
UNLOCKED_MOD = 25154119      # 真机：特殊弹药斥候，在清单里
EMPTY_PLUG = 1078080765


def _mod(plug_hash: int, name: str, *, cost: int = 1, rules: list[str] | None = None) -> dict:
    plug: dict[str, Any] = {
        "plugCategoryIdentifier": "enhancements.v2_head",
        "plugCategoryHash": 2912171003,
        "energyCost": {"energyCost": cost},
    }
    if rules is not None:
        plug["insertionRules"] = [{"failureMessage": message} for message in rules]
    return {"displayProperties": {"name": name}, "itemType": 19, "plug": plug}


class _Manifest:
    def __init__(self, definitions: dict[int, dict]) -> None:
        self._definitions = definitions

    def get_item_definition(self, item_hash: int):
        return self._definitions.get(item_hash)

    def get_item_info(self, item_hash: int):
        return None

    def get_definition(self, table: str, key: int):
        return None


class _Resolver:
    def __init__(self, profile: dict) -> None:
        self._profile = profile

    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def resolve_character_id(self, membership_id, membership_type, character):
        return "char-1"

    async def get_profile(self, membership_id, membership_type, components):
        return self._profile


# ── 单一出处：无符号 bucket hash ────────────────────────────────────────


def test_slot_key_from_bucket_hash_accepts_unsigned_and_signed() -> None:
    assert slot_key_from_bucket_hash(HELMET_BUCKET) == "helmet"
    assert slot_key_from_bucket_hash(GAUNTLETS_BUCKET) == "gauntlets"
    assert slot_key_from_bucket_hash(HELMET_BUCKET_SIGNED) == "helmet"


def test_slot_key_from_bucket_hash_is_empty_for_unknown() -> None:
    assert slot_key_from_bucket_hash(0) == ""
    assert slot_key_from_bucket_hash(None) == ""  # type: ignore[arg-type]


def test_armor_mod_slot_key_uses_the_unsigned_bucket() -> None:
    """回归：头盔/臂铠的 bucket hash 大于 2^31，老代码用有符号值查表 → 槽名空串。"""
    service = ArmorModService(None, _Manifest({}), None)  # type: ignore[arg-type]

    assert service._slot_key({"inventory": {"bucketTypeHash": HELMET_BUCKET}}) == "helmet"
    assert service._slot_key({"inventory": {"bucketTypeHash": GAUNTLETS_BUCKET}}) == "gauntlets"
    assert service._slot_key({"inventory": {}}) == ""


def test_slot_key_from_bucket_is_not_disturbed() -> None:
    assert slot_key_from_bucket("Leg Armor") == "legs"


# ── 可插入清单 ──────────────────────────────────────────────────────────


def _service(manifest: _Manifest, profile: dict | None = None) -> ArmorModService:
    return ArmorModService(None, manifest, _Resolver(profile or {}))  # type: ignore[arg-type]


def test_insertable_plugs_merges_profile_and_character_scopes() -> None:
    profile = {
        "profilePlugSets": {"data": {"plugs": {str(PLUG_SET): [{"plugItemHash": 1}]}}},
        "characterPlugSets": {
            "data": {
                "char-1": {"plugs": {str(PLUG_SET): [{"plugItemHash": 2}]}},
                "char-other": {"plugs": {str(PLUG_SET): [{"plugItemHash": 3}]}},
            }
        },
    }
    service = _service(_Manifest({}))

    pools = service.insertable_plugs(profile, "char-1")

    assert pools[PLUG_SET] == {1, 2}, "自己的角色级清单要并进来，别的角色不能混进来"
    assert 999 not in pools, "上游没给的 plug set 不能凭空出现（那是「没数据」，不是「不允许」）"


def test_insertable_plugs_is_empty_without_plug_sets() -> None:
    assert _service(_Manifest({})).insertable_plugs({}, "char-1") == {}


def test_socket_plug_sets_reads_reusable_and_randomized() -> None:
    definition = {"sockets": {"socketEntries": [
        {"reusablePlugSetHash": 11},
        {"randomizedPlugSetHash": 22},
        {},
    ]}}
    service = _service(_Manifest({}))

    assert service.socket_plug_sets(definition, 0) == [11]
    assert service.socket_plug_sets(definition, 1) == [22]
    assert service.socket_plug_sets(definition, 2) == []
    assert service.socket_plug_sets(definition, 9) == []
    assert service.socket_plug_sets(None, 0) == []


def test_plug_is_insertable_distinguishes_no_data_from_not_allowed() -> None:
    service = _service(_Manifest({}))
    definition = {"sockets": {"socketEntries": [{"reusablePlugSetHash": PLUG_SET}]}}

    assert service.plug_is_insertable({}, definition, 0, LOCKED_MOD) is None, "没数据 = 不判断"
    assert service.plug_is_insertable({PLUG_SET: {UNLOCKED_MOD}}, definition, 0, LOCKED_MOD) is False
    assert service.plug_is_insertable({PLUG_SET: {UNLOCKED_MOD}}, definition, 0, UNLOCKED_MOD) is True


def test_plug_is_insertable_always_accepts_the_plug_already_in_the_socket() -> None:
    """上游那份清单会漏：实测调谐槽里正装着的那颗就不在清单里。"""
    service = _service(_Manifest({}))
    definition = {"sockets": {"socketEntries": [{"reusablePlugSetHash": PLUG_SET}]}}

    assert service.plug_is_insertable(
        {PLUG_SET: {UNLOCKED_MOD}}, definition, 0, LOCKED_MOD, current_plug_hash=LOCKED_MOD
    ) is True


# ── 有符号 / 无符号：manifest 给有符号、profile 给无符号 ────────────────

SIGNED_MOD = -207911122          # 真机 `复原`
UNSIGNED_MOD = 4087056174        # 同一个 hash 的上游写法


def test_insertable_plugs_stores_unsigned_hashes() -> None:
    """上游给无符号，但同一颗也可能以有符号出现；这里统一收成无符号，下游不必各自转换。"""
    profile = {"characterPlugSets": {"data": {"char-1": {"plugs": {
        str(PLUG_SET): [{"plugItemHash": UNSIGNED_MOD}, {"plugItemHash": SIGNED_MOD}],
    }}}}}

    pools = _service(_Manifest({})).insertable_plugs(profile, "char-1")

    assert pools[PLUG_SET] == {UNSIGNED_MOD}


def test_plug_is_insertable_compares_unsigned_on_both_sides() -> None:
    """回归：`manifest.search` 给有符号 hash、profile 给无符号，直接比永远不相等 ——
    第一版没转，凡是负数 hash 的模组全被判成"没解锁"（正装着的 `复原`、`特殊终结技` 都中招）。"""
    service = _service(_Manifest({}))
    definition = {"sockets": {"socketEntries": [{"reusablePlugSetHash": PLUG_SET}]}}

    assert service.plug_is_insertable(
        {PLUG_SET: {UNSIGNED_MOD}}, definition, 0, SIGNED_MOD
    ) is True
    assert service.plug_is_insertable(
        {PLUG_SET: {UNSIGNED_MOD}}, definition, 0, SIGNED_MOD,
        current_plug_hash=UNSIGNED_MOD,
    ) is True, "有符号的目标 vs 无符号的槽里现值，也要认出来是同一颗"


async def test_find_mod_socket_matches_an_installed_signed_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_find_mod_socket` 的"这个槽已经装着它"也要按无符号比，否则会返回错槽。"""
    manifest = _plan_manifest()
    manifest._definitions[SIGNED_MOD] = _mod(SIGNED_MOD, "复原")
    manifest._definitions[HELMET_ITEM]["sockets"]["socketEntries"] = [
        {"reusablePlugSetHash": PLUG_SET} for _ in range(3)
    ]
    _patch_plug_set(monkeypatch, manifest)
    service = _equipment(manifest)
    sockets = [{"plugHash": EMPTY_PLUG}, {"plugHash": UNSIGNED_MOD}, {"plugHash": EMPTY_PLUG}]

    index = await service._find_mod_socket(
        "item-1", HELMET_ITEM, SIGNED_MOD, "1", 3, {"item-1": sockets},
    )

    assert index == 1, "其余槽是同类模组，只有 1 号槽真的装着它 —— 必须按无符号比才认得出"


# ── 插入条件 ────────────────────────────────────────────────────────────


def test_plug_insertion_conditions_keeps_only_real_messages() -> None:
    manifest = _Manifest({
        LOCKED_MOD: _mod(LOCKED_MOD, "重型弹药搜寻者",
                         rules=["需要守护者等级3", "必须在赛季神器中选择", ""]),
        UNLOCKED_MOD: _mod(UNLOCKED_MOD, "特殊弹药斥候"),
    })
    service = _service(manifest)

    assert service.plug_insertion_conditions(LOCKED_MOD) == [
        "需要守护者等级3", "必须在赛季神器中选择",
    ]
    assert service.plug_insertion_conditions(UNLOCKED_MOD) == []
    assert service.plug_insertion_conditions(123456) == []


# ── plan：同名挑已解锁那档；没有就明说写不进去 ───────────────────────────


def _profile(insertable: set[int] | None) -> dict:
    """一件头盔 + 一个接受两颗同名模组的插槽；`None` = 上游不给这份清单。"""
    profile: dict[str, Any] = {
        "characterEquipment": {"data": {"char-1": {"items": [
            {"itemInstanceId": "item-1", "itemHash": HELMET_ITEM},
        ]}}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "itemComponents": {
            "instances": {"data": {"item-1": {"gearTier": 5,
                                              "primaryStat": {"value": 550},
                                              "energy": {"energyCapacity": 11, "energyUsed": 1,
                                                         "energyUnused": 10}}}},
            "sockets": {"data": {"item-1": {"sockets": [{"plugHash": EMPTY_PLUG}]}}},
        },
    }
    if insertable is not None:
        profile["characterPlugSets"] = {
            "data": {"char-1": {"plugs": {str(PLUG_SET): [
                {"plugItemHash": plug_hash} for plug_hash in sorted(insertable)
            ]}}}
        }
    return profile


class _SearchingManifest(_Manifest):
    def search(self, query: str, limit: int = 0, item_type: int | None = None):
        if "弹药" in query:
            return [
                {"itemHash": LOCKED_MOD, "itemType": 19},
                {"itemHash": UNLOCKED_MOD, "itemType": 19},
            ]
        return []


def _plan_manifest() -> _SearchingManifest:
    return _SearchingManifest({
        HELMET_ITEM: {
            "displayProperties": {"name": "光芒领主面具"},
            "itemType": 2,
            "inventory": {"bucketTypeHash": HELMET_BUCKET},
            "sockets": {"socketEntries": [{"reusablePlugSetHash": PLUG_SET}]},
        },
        EMPTY_PLUG: _mod(EMPTY_PLUG, "空模组插槽", cost=0),
        LOCKED_MOD: _mod(LOCKED_MOD, "重型弹药搜寻者", rules=["必须在赛季神器中选择"]),
        UNLOCKED_MOD: _mod(UNLOCKED_MOD, "特殊弹药斥候"),
    })


def _patch_plug_set(monkeypatch: pytest.MonkeyPatch, manifest: _SearchingManifest) -> None:
    """`_find_mod_socket` 走 manifest.get_definition 查 plug set。"""
    manifest.get_definition = lambda table, key: (  # type: ignore[method-assign]
        {"reusablePlugItems": [{"plugItemHash": EMPTY_PLUG},
                               {"plugItemHash": LOCKED_MOD},
                               {"plugItemHash": UNLOCKED_MOD}]}
        if key == PLUG_SET else None
    )


async def test_plan_prefers_the_unlocked_twin_of_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名两颗都能塞时，必须挑这一位**已解锁**的那颗。"""
    manifest = _plan_manifest()
    _patch_plug_set(monkeypatch, manifest)
    service = _service(manifest, _profile({UNLOCKED_MOD}))

    plan = await service.plan("Tester#1234", "item-1", "弹药搜寻者", "hunter")

    assert plan["to"]["hash"] == UNLOCKED_MOD
    assert plan["to"]["unlock_state"] is True
    assert plan["writable"] is True
    assert plan["writable_reason"] == ""
    assert plan["slot"] == "helmet", "顺带钉住无符号 bucket → 槽名不再是空串"
    assert {alt["unlock_state"] for alt in plan["alternatives"]} == {False}


async def test_plan_refuses_and_explains_when_nothing_is_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _plan_manifest()
    _patch_plug_set(monkeypatch, manifest)
    service = _service(manifest, _profile({999}))  # 清单在，但两颗都不在里面

    plan = await service.plan("Tester#1234", "item-1", "弹药搜寻者", "hunter")

    assert plan["to"]["unlock_state"] is False
    assert plan["writable"] is False
    assert "必须在赛季神器中选择" in plan["writable_reason"]
    assert "1676" in plan["writable_reason"]


async def test_plan_stays_writable_when_upstream_sends_no_plug_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游没给这份清单 → `None`，不许读成「没解锁」。"""
    manifest = _plan_manifest()
    _patch_plug_set(monkeypatch, manifest)
    service = _service(manifest, _profile(None))

    plan = await service.plan("Tester#1234", "item-1", "弹药搜寻者", "hunter")

    assert plan["to"]["unlock_state"] is None
    assert plan["writable"] is True
    assert plan["writable_reason"] == ""


async def test_plan_refuses_a_socket_that_cannot_hold_the_mod() -> None:
    """槽里装着别类东西、plug set 里也没有这两颗模组时，要说"没有能装的插槽"。"""
    manifest = _plan_manifest()
    manifest._definitions[EMPTY_PLUG] = {
        "displayProperties": {"name": "默认着色器"},
        "itemType": 19,
        "plug": {"plugCategoryIdentifier": "shader",
                 "plugCategoryHash": 2973005342, "energyCost": {"energyCost": 0}},
    }
    manifest.get_definition = lambda table, key: (  # type: ignore[method-assign]
        {"reusablePlugItems": [{"plugItemHash": EMPTY_PLUG}]} if key == PLUG_SET else None
    )
    service = _service(manifest, _profile(None))

    with pytest.raises(InvalidArgumentError, match="没有能装"):
        await service.plan("Tester#1234", "item-1", "弹药搜寻者", "hunter")


# ── apply：1676 要说清是插入条件，不是"去游戏里手动装" ─────────────────


class _RefusingBungie:
    def __init__(self, code: int, message: str, status: str = "") -> None:
        self._payload = {"ErrorCode": code, "Message": message, "ErrorStatus": status}

    async def insert_socket_plug_free(self, *args, **kwargs):
        return self._payload

    async def insert_socket_plug(self, *args, **kwargs):
        return self._payload


def _apply_plan(plug_hash: int = LOCKED_MOD) -> dict:
    return {
        "item_instance_id": "item-1",
        "item_name": "光芒领主面具",
        "socket_index": 0,
        "character_id": "char-1",
        "membership_type": 3,
        "to": {"hash": plug_hash, "name": "重型弹药搜寻者"},
        "from": {"hash": None, "name": None, "energy_cost": 0},
        "energy": {"capacity": 11, "used": 1, "after": 2},
    }


async def test_apply_reports_the_insertion_conditions_on_1676() -> None:
    manifest = _plan_manifest()
    service = ArmorModService(
        _RefusingBungie(1676, "The requirements have not been met.",
                        "DestinyFailedPlugInsertionRules"),
        manifest,
        None,  # type: ignore[arg-type]
    )

    with pytest.raises(TransferError) as excinfo:
        await service.apply(_apply_plan())

    message = str(excinfo.value)
    assert "1676" in message
    assert "必须在赛季神器中选择" in message


async def test_apply_keeps_the_paid_endpoint_attribution() -> None:
    service = ArmorModService(
        _RefusingBungie(403, "Access not permitted by application scope"),
        _plan_manifest(),
        None,  # type: ignore[arg-type]
    )

    with pytest.raises(TransferError, match="AdvancedWriteActions"):
        await service.apply(_apply_plan())


# ── 写入被挡时的分类：三种原因不许混成一句话 ────────────────────────────


def _equipment(manifest: _Manifest) -> LoadoutEquipmentService:
    from unittest.mock import MagicMock

    return LoadoutEquipmentService(MagicMock(), manifest, MagicMock())  # type: ignore[arg-type]


def test_mod_write_blocker_names_the_insertion_conditions() -> None:
    service = _equipment(_plan_manifest())

    detail = service.mod_write_blocker(
        {"ErrorCode": 1676, "ErrorStatus": "DestinyFailedPlugInsertionRules"}, LOCKED_MOD
    )

    assert "插入条件没满足" in detail
    assert "必须在赛季神器中选择" in detail
    assert "游戏里" not in detail, "1676 不是「去游戏里装」，别那样写"


def test_mod_write_blocker_treats_1675_as_blocked_not_fatal() -> None:
    """1675 要材料 = **挡住这一颗**，不是硬失败 —— 否则一颗插件会把整条配装回退掉。

    2026-09-22 实测：调谐不在组件 310 清单里时上游回 1675。配装那边靠
    `steps.mod_blocked` 判定"装备已换好、只这一颗没写进去"，见
    `tests/test_exact_build_execution.py::test_a_blocked_plug_keeps_the_equipment_and_skips_rollback`。
    """
    service = _equipment(_plan_manifest())

    detail = service.mod_write_blocker(
        {"ErrorCode": 1675, "ErrorStatus": "DestinyCannotAffordMaterialRequirements"}, LOCKED_MOD
    )

    assert "要材料" in detail and "1675" in detail
    # 旧说法"换成你已经拥有的那一颗"作废了：1675 是"这颗装不到这件上"。
    assert "你已经拥有" not in detail
    # 1675 与 1676 是两回事，别串味。
    assert "插入条件" not in detail


def test_mod_write_blocker_separates_scope_and_in_game() -> None:
    service = _equipment(_plan_manifest())

    assert "AWA" in service.mod_write_blocker(
        {"ErrorCode": 403, "Message": "Access not permitted by application scope"}, LOCKED_MOD
    )
    assert "in-game" in service.mod_write_blocker(
        {"ErrorCode": 1663, "Message": "This action can only be done in-game."}, LOCKED_MOD
    )
    assert service.mod_write_blocker({"ErrorCode": 1, "Message": "Ok"}, LOCKED_MOD) == ""


# ── 配装预检：写之前就说清，别写一半才让上游拒 ──────────────────────────


async def test_prepare_mod_operations_refuses_a_locked_mod_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _plan_manifest()
    _patch_plug_set(monkeypatch, manifest)
    service = _equipment(manifest)
    service._resolver = _Resolver({})  # type: ignore[assignment]
    item = LoadoutItem(item_hash=HELMET_ITEM, name="光芒领主面具", slot="helmet",
                       item_instance_id="item-1", mods=[LOCKED_MOD],
                       mod_sockets={0: LOCKED_MOD})

    with pytest.raises(TransferError, match="必须在赛季神器中选择"):
        await service._prepare_mod_operations(
            item, "1", 3, {"item-1": [{"plugHash": EMPTY_PLUG}]},
            {"item-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}},
            {PLUG_SET: {UNLOCKED_MOD}},
        )


async def test_prepare_mod_operations_skips_the_check_without_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _plan_manifest()
    _patch_plug_set(monkeypatch, manifest)
    service = _equipment(manifest)
    item = LoadoutItem(item_hash=HELMET_ITEM, name="光芒领主面具", slot="helmet",
                       item_instance_id="item-1", mods=[LOCKED_MOD],
                       mod_sockets={0: LOCKED_MOD})

    operations = await service._prepare_mod_operations(
        item, "1", 3, {"item-1": [{"plugHash": EMPTY_PLUG}]},
        {"item-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}},
        None,
    )

    assert operations == [("mod", LOCKED_MOD, 0)]


# ── 1679：这个槽已经装着它了 ────────────────────────────────────────────


async def test_apply_treats_an_already_installed_plug_as_a_no_op() -> None:
    """上游回 1679 `DestinySocketAlreadyHasPlug` 时，用户要的状态已经成立，不算失败。"""
    service = ArmorModService(
        _RefusingBungie(1679, "The request to modify an item failed.",
                        "DestinySocketAlreadyHasPlug"),
        _plan_manifest(),
        None,  # type: ignore[arg-type]
    )

    result = await service.apply(_apply_plan())

    assert result["success"] is True
    assert result["already_installed"] is True
    assert result["socket_index"] == 0
