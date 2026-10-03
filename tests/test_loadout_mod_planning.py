"""护甲模组规划与插槽匹配的特征测试。

这几段（模组预检、插槽查找、能量腾挪、子职业插槽）此前只被 mock 过，从未真正执行。
这里先把当前行为钉住，作为后续按域拆分时的安全网；断言的是现状，不是理想设计。
"""

from __future__ import annotations

import http
from unittest.mock import AsyncMock, MagicMock

import aiobungie
import pytest

from destiny_mcp.exceptions import TransferError
from destiny_mcp.models import Loadout, LoadoutItem, LoadoutSubclassConfig
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService

GENERAL_MOD_CATEGORY = 2487827355  # enhancements.v2_general（属性模组）
LEGS_MOD_CATEGORY = 2111701510     # enhancements.v2_legs（腿部模组：回收器/洗礼/复原…）
ARTIFICE_MOD_CATEGORY = 3773173029
NOT_A_MOD_CATEGORY = 111111


class _Manifest:
    def __init__(self, definitions=None, plug_sets=None, infos=None, identifiers=None) -> None:
        self._definitions = definitions or {}
        self._plug_sets = plug_sets or {}
        self._infos = infos or {}
        self._identifiers = identifiers or {}

    def get_item_definition(self, item_hash: int):
        return self._definitions.get(item_hash)

    def get_item_info(self, item_hash: int):
        return self._infos.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        """真 Manifest 的取名义口。替身也按真形状给（键是 `name`，不是 `displayProperties`）。"""
        return str((self._infos.get(item_hash) or {}).get("name") or f"#{item_hash}")

    def get_definition(self, table: str, hash_id: int):
        if table == "DestinyPlugSetDefinition":
            return self._plug_sets.get(hash_id)
        return None

    def get_plug_category_identifier(self, plug_hash: int):
        return self._identifiers.get(plug_hash)


def _mod(category: int, cost: int) -> dict:
    return {"plug": {"plugCategoryHash": category, "energyCost": {"energyCost": cost}}}


def _service(manifest: _Manifest) -> LoadoutEquipmentService:
    return LoadoutEquipmentService(MagicMock(), manifest, MagicMock())  # type: ignore[arg-type]


def _item(**kwargs) -> LoadoutItem:
    base = dict(item_hash=100, name="Helmet", slot="helmet", item_instance_id="item-1")
    return LoadoutItem(**{**base, **kwargs})


# ── _plug_energy_cost ────────────────────────────────────────────────


def test_plug_energy_cost_reads_manifest_value() -> None:
    service = _service(_Manifest({400: _mod(GENERAL_MOD_CATEGORY, 3)}))

    assert service._plug_energy_cost(400) == 3


def test_plug_energy_cost_is_none_when_definition_or_value_is_missing() -> None:
    manifest = _Manifest({
        401: {"plug": {}},                                   # 没有 energyCost
        402: {"plug": {"energyCost": {"energyCost": "x"}}},  # 非数值
    })
    service = _service(manifest)

    assert service._plug_energy_cost(999) is None
    assert service._plug_energy_cost(401) == 0
    assert service._plug_energy_cost(402) is None


def test_plug_energy_cost_never_returns_negative() -> None:
    service = _service(_Manifest({403: _mod(GENERAL_MOD_CATEGORY, -5)}))

    assert service._plug_energy_cost(403) == 0


# ── _plug_category_hash ──────────────────────────────────────────────


def test_plug_category_hash_falls_back_to_item_summary() -> None:
    """定义里没有 plug 段时，退回 get_item_info 的摘要。"""
    manifest = _Manifest(
        definitions={500: {"displayProperties": {}}},
        infos={500: {"plug": {"plugCategoryHash": GENERAL_MOD_CATEGORY}}},
    )
    service = _service(manifest)

    assert service._plug_category_hash(500) == GENERAL_MOD_CATEGORY


def test_mod_label_reads_the_name_the_way_the_real_manifest_gives_it() -> None:
    """回执里的模组名要真的取到 —— 模板是照**真 Manifest** 的形状给的。

    真机 2026-09-24：`mod_label` 从 `get_item_info()` 的返回值里读 `displayProperties`，
    而那个访问器给的是规范化小字典（键叫 `name`，没有 `displayProperties`）→ 每次都取空、
    一路退回 hash：文档写着"steps 里的模组写中文名"，实际发出去的 30 条步骤全是 hash。
    单测当时没抓到，是因为替身返回的是原始定义。
    """
    manifest = _Manifest(infos={600: {"name": "手雷模组"}})
    service = _service(manifest)

    assert service.mod_label(600) == "手雷模组"
    assert service.mod_label(999) == "#999", "查不到时退回 hash，别编一个名字"


def test_plug_category_hash_is_zero_for_unknown_plug() -> None:
    service = _service(_Manifest())

    assert service._plug_category_hash(12345) == 0


# ── _find_mod_socket ─────────────────────────────────────────────────


async def test_find_mod_socket_returns_none_for_non_mod_category() -> None:
    service = _service(_Manifest({600: _mod(NOT_A_MOD_CATEGORY, 1)}))

    result = await service._find_mod_socket(
        "item-1", 100, 600, "player", 3, sockets_cache={"item-1": [{"plugHash": 0}]}
    )

    assert result is None


async def test_find_mod_socket_prefers_a_socket_that_can_hold_the_mod() -> None:
    """插槽定义通过 reusablePlugSetHash 声明可插该模组时，优先返回它。"""
    manifest = _Manifest(
        definitions={600: _mod(GENERAL_MOD_CATEGORY, 1)},
        plug_sets={700: {"reusablePlugItems": [{"plugItemHash": 600}]}},
    )
    service = _service(manifest)
    item_definition = {
        "sockets": {
            "socketEntries": [
                {"reusablePlugSetHash": 0},
                {"reusablePlugSetHash": 700},
            ]
        }
    }
    manifest._definitions[100] = item_definition

    result = await service._find_mod_socket(
        "item-1",
        100,
        600,
        "player",
        3,
        sockets_cache={"item-1": [{"plugHash": 0}, {"plugHash": 0}]},
    )

    assert result == 1


async def test_find_mod_socket_matches_same_category_then_exact_mod() -> None:
    """先找同类别但不同的模组，再退而求其次找已经装了同一个模组的插槽。"""
    manifest = _Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 1),
        601: _mod(GENERAL_MOD_CATEGORY, 1),
        602: _mod(ARTIFICE_MOD_CATEGORY, 1),
    })
    service = _service(manifest)

    same_category = await service._find_mod_socket(
        "item-1",
        100,
        600,
        "player",
        3,
        sockets_cache={"item-1": [{"plugHash": 602}, {"plugHash": 601}]},
    )
    already_installed = await service._find_mod_socket(
        "item-1",
        100,
        600,
        "player",
        3,
        sockets_cache={"item-1": [{"plugHash": 602}, {"plugHash": 600}]},
    )

    assert same_category == 1
    assert already_installed == 1


async def test_find_mod_socket_honours_excluded_sockets() -> None:
    manifest = _Manifest({601: _mod(GENERAL_MOD_CATEGORY, 1)})
    service = _service(manifest)

    result = await service._find_mod_socket(
        "item-1",
        100,
        600,
        "player",
        3,
        sockets_cache={"item-1": [{"plugHash": 601}]},
        excluded_socket_indices={0},
    )

    assert result is None


# ── _insert_armor_mod ────────────────────────────────────────────────


async def test_insert_armor_mod_prefers_the_free_endpoint() -> None:
    """护甲模组走 **free** 接口，哪怕它消耗能量。

    以前按"能量消耗 > 0"选接口是错的：Bungie 的 free 指**没有材料消耗**，官方文档明确
    `InsertSocketPlugFree` 覆盖 "Perks, **Armor Mods**, Shaders, Ornaments"；付费接口
    `InsertSocketPlug` 要 `AdvancedWriteActions` scope，于是真机上装属性模组一直 403
    `Access not permitted by application scope`（DIM 能做正是因为 DIM 用 free 接口）。
    """
    service = _service(_Manifest({600: _mod(GENERAL_MOD_CATEGORY, 2)}))
    service._bungie.insert_socket_plug_free = AsyncMock(return_value={"ErrorCode": 1})
    service._bungie.insert_socket_plug = AsyncMock(return_value={"ErrorCode": 1})

    result = await service._insert_armor_mod("item-1", 600, 3, "char", 3)

    assert result == {"ErrorCode": 1}
    service._bungie.insert_socket_plug_free.assert_awaited_once_with(
        "item-1", 600, 3, 0, "char", 3
    )
    service._bungie.insert_socket_plug.assert_not_awaited()


async def test_insert_armor_mod_never_falls_back_to_the_paid_socket_api() -> None:
    """free 接口回 1663「只能游戏内做」时**不再退付费接口**，原话交回去。

    以前这里会再调一次 `insert_socket_plug`，而那个接口要 AWA 三段流程
    （`AwaInitializeRequest` → 用户亲自批准 → `AwaGetActionToken`）拿 `actionToken` ——
    我们没发这个字段，退过去必然再失败一次。1663 本身已经是一句完整的上游结论
    （至少对应"角色不在社交区/轨道/离线"与"这个槽本身禁用"两种），原文照转即可；
    再退一次只是多一次白往返，还把真原因换成付费接口的二次报错。
    这是 2026-10-03 删掉的分支，`_insert_armor_mod` 的 docstring 里记着为什么。
    """
    service = _service(_Manifest({600: _mod(GENERAL_MOD_CATEGORY, 2)}))
    forbidden = {
        "ErrorCode": 1663,
        "ErrorStatus": "DestinyItemActionForbidden",
        "Message": "This action can only be done in-game. I know, we're working on it.",
    }
    service._bungie.insert_socket_plug_free = AsyncMock(return_value=forbidden)
    service._bungie.insert_socket_plug = AsyncMock(return_value={"ErrorCode": 1})

    result = await service._insert_armor_mod("item-1", 600, 3, "char", 3)

    assert result == forbidden, "上游原话要原样交回去（别吞、也别二次包装）"
    service._bungie.insert_socket_plug.assert_not_awaited()


async def test_insert_armor_mod_does_not_fall_back_on_scope_errors() -> None:
    """403「scope 不够」不是"这个 plug 不免费"，别拿付费接口再撞一次。"""
    service = _service(_Manifest({600: _mod(GENERAL_MOD_CATEGORY, 2)}))
    forbidden = {
        "ErrorCode": 403,
        "Message": "Access not permitted by application scope",
    }
    service._bungie.insert_socket_plug_free = AsyncMock(return_value=forbidden)
    service._bungie.insert_socket_plug = AsyncMock(return_value={"ErrorCode": 1})

    assert await service._insert_armor_mod("item-1", 600, 3, "char", 3) == forbidden
    service._bungie.insert_socket_plug.assert_not_awaited()


# ── _prepare_mod_operations ──────────────────────────────────────────


async def test_prepare_mod_operations_uses_explicit_socket_map_in_order() -> None:
    service = _service(_Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 2),
        601: _mod(GENERAL_MOD_CATEGORY, 3),
    }))
    item = _item(mod_sockets={3: 601, 1: 600})

    operations = await service._prepare_mod_operations(
        item,
        "player",
        3,
        {"item-1": [{"plugHash": 0}, {"plugHash": 0}, {"plugHash": 0}, {"plugHash": 0}]},
        {"item-1": {"energy": {"energyCapacity": 10, "energyUsed": 0}}},
    )

    assert [op.as_tuple() for op in operations] == [("mod", 600, 1), ("mod", 601, 3)]


async def test_prepare_mod_operations_rejects_a_mod_without_a_unique_socket() -> None:
    service = _service(_Manifest({600: _mod(NOT_A_MOD_CATEGORY, 1)}))

    with pytest.raises(TransferError, match="找不到模组"):
        await service._prepare_mod_operations(
            _item(mods=[600]),
            "player",
            3,
            {"item-1": [{"plugHash": 0}]},
            {"item-1": {"energy": {"energyCapacity": 10, "energyUsed": 0}}},
        )


async def test_prepare_mod_operations_requires_readable_energy_capacity() -> None:
    service = _service(_Manifest({600: _mod(GENERAL_MOD_CATEGORY, 1)}))

    with pytest.raises(TransferError, match="能量上限"):
        await service._prepare_mod_operations(
            _item(mod_sockets={0: 600}),
            "player",
            3,
            {"item-1": [{"plugHash": 0}]},
            {"item-1": {"energy": {}}},
        )


async def test_prepare_mod_operations_derives_used_energy_from_sockets() -> None:
    """读不到 energyUsed 时，按当前插槽里的模组费用求和，并据此腾挪。"""
    manifest = _Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 4),
        700: _mod(GENERAL_MOD_CATEGORY, 5),
        701: _mod(GENERAL_MOD_CATEGORY, 5),
        800: _mod(GENERAL_MOD_CATEGORY, 0),
    })
    manifest._definitions[100] = {
        "sockets": {
            "socketEntries": [
                {"singleInitialItemHash": 800},
                {"singleInitialItemHash": 800},
                {"singleInitialItemHash": 0},
            ]
        }
    }
    service = _service(manifest)

    operations = await service._prepare_mod_operations(
        _item(mod_sockets={2: 600}),
        "player",
        3,
        {"item-1": [{"plugHash": 700}, {"plugHash": 701}, {"plugHash": 0}]},
        {"item-1": {"energy": {"energyCapacity": 6}}},
    )

    # 推导出的已用能量是 5 + 5 = 10；加目标 4 点后超 6 点上限，必须腾出两个插槽。
    assert [op.as_tuple() for op in operations] == [
        ("clear", 800, 0), ("clear", 800, 1), ("mod", 600, 2)
    ]


async def test_prepare_mod_operations_clears_minimum_energy_first() -> None:
    """能量不够时先插入最少量的清空操作，再写模组，且清空排在前面。"""
    manifest = _Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 5),   # 目标模组
        700: _mod(GENERAL_MOD_CATEGORY, 4),   # 当前占用，可释放 4
        701: _mod(GENERAL_MOD_CATEGORY, 3),   # 当前占用，可释放 3
        800: _mod(GENERAL_MOD_CATEGORY, 0),   # 对应插槽的默认空模组
    })
    manifest._definitions[100] = {
        "sockets": {
            "socketEntries": [
                {"singleInitialItemHash": 0},
                {"singleInitialItemHash": 800},
            ]
        }
    }
    service = _service(manifest)

    operations = await service._prepare_mod_operations(
        _item(mod_sockets={0: 600}),
        "player",
        3,
        {"item-1": [{"plugHash": 0}, {"plugHash": 701}]},
        {"item-1": {"energy": {"energyCapacity": 6, "energyUsed": 3}}},
    )

    assert [op.as_tuple() for op in operations] == [("clear", 800, 1), ("mod", 600, 0)]


async def test_prepare_mod_operations_refuses_when_energy_cannot_be_freed() -> None:
    service = _service(_Manifest({600: _mod(GENERAL_MOD_CATEGORY, 5)}))
    service._manifest._definitions[100] = {"sockets": {"socketEntries": []}}

    with pytest.raises(TransferError, match="腾出"):
        await service._prepare_mod_operations(
            _item(mod_sockets={0: 600}),
            "player",
            3,
            {"item-1": [{"plugHash": 0}]},
            {"item-1": {"energy": {"energyCapacity": 1, "energyUsed": 1}}},
        )


# ── 读取辅助 ─────────────────────────────────────────────────────────


def test_read_armor_mod_sockets_includes_empty_default_plugs() -> None:
    manifest = _Manifest({
        100: {"sockets": {"socketEntries": [
            {"singleInitialItemHash": 0},
            {"singleInitialItemHash": 800},
        ]}},
        600: _mod(GENERAL_MOD_CATEGORY, 1),
        800: _mod(GENERAL_MOD_CATEGORY, 0),
    })
    service = _service(manifest)

    result = service.read_armor_mod_sockets("item-1", 100, {
        "item-1": {"sockets": [{"plugHash": 600}, {"plugHash": 0}]}
    })

    assert result == {0: 600, 1: 800}


def test_read_armor_mods_keeps_only_mod_categories() -> None:
    service = _service(_Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 1),
        601: _mod(NOT_A_MOD_CATEGORY, 1),
    }))

    mods = service.read_armor_mods("item-1", {
        "item-1": {"sockets": [{"plugHash": 600}, {"plugHash": 601}, {"plugHash": 0}]}
    })

    assert mods == [600]


def test_read_armor_mod_sockets_and_mods_tolerate_missing_instance() -> None:
    service = _service(_Manifest())

    assert service.read_armor_mod_sockets("", 100, {}) == {}
    assert service.read_armor_mods("missing", {}) == []


# ── read_subclass_config ─────────────────────────────────────────────


def test_read_subclass_config_maps_socket_types_from_identifier() -> None:
    manifest = _Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={
            900: "hunter.solar.supers",
            901: "shared.void.grenades",
            902: "warlock.arc.aspects",
            903: "shared.stasis.fragments",
        },
    )
    service = _service(manifest)

    config = service.read_subclass_config("sub-1", 100, {
        "sub-1": {"sockets": [
            {"plugHash": 900, "isEnabled": True},
            {"plugHash": 901, "isEnabled": True},
            {"plugHash": 902, "isEnabled": True},
            {"plugHash": 903, "isEnabled": True},
            {"plugHash": 0, "isEnabled": True},
        ]}
    })

    assert config is not None
    assert (config.super_hash, config.grenade_hash) == (900, 901)
    assert config.aspect_hashes == [902]
    assert config.fragment_hashes == [903]
    # 空插槽不进 plug_sockets
    assert config.plug_sockets == {0: 900, 1: 901, 2: 902, 3: 903}


def test_read_subclass_config_falls_back_to_plug_category_hash() -> None:
    """没有 plugCategoryIdentifier 时，用插槽类别常量兜底。"""
    service = _service(_Manifest({
        900: _mod(LoadoutEquipmentService._PLUG_CAT_SUPER, 0),
        901: _mod(LoadoutEquipmentService._PLUG_CAT_FRAGMENTS, 0),
    }))

    config = service.read_subclass_config("sub-1", 100, {
        "sub-1": {"sockets": [
            {"plugHash": 900, "isEnabled": True}, {"plugHash": 901, "isEnabled": True}
        ]}
    })

    assert config is not None
    assert config.super_hash == 900
    assert config.fragment_hashes == [901]


def test_read_subclass_config_leaves_disabled_sockets_out_of_plug_sockets() -> None:
    """**禁用槽不进 `plug_sockets`** —— 这就是"第 6 颗碎片被排进 socket 14"的根因那一步。

    真机现场（棱镜术士，`subclass_assistant(intent="get")` 与 profile 组件 305 一致）：
    socket 9–13 是 `isEnabled: true` 的碎片槽，socket 14 是 `isEnabled: false`、
    里面躺着占位 `空碎片插槽`（无符号 2808665197）。旧写法把 14 也收进 `plug_sockets`，
    下游 `replace_fragment_config` 于是数出 6 个碎片槽、把第 6 颗排进 14，
    上游回 HTTP 500 `DestinySocketActionNotAllowed`（`request.plug.socketIndex`:
    `The requested socket is disabled.`）。同一颗碎片写进 socket 12 就成功。
    """
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={
            900: "shared.solar.fragments",
            901: "shared.solar.fragments",
            2808665197: "shared.solar.fragments",
        },
    ))

    config = service.read_subclass_config("sub-1", 100, {
        "sub-1": {"sockets": [
            {"plugHash": 900, "isEnabled": True},
            {"plugHash": 901, "isEnabled": True},
            {"plugHash": 2808665197, "isEnabled": False},
        ]}
    })

    assert config is not None
    assert config.plug_sockets == {0: 900, 1: 901}, (
        "禁用槽 2 不许出现在可排槽位里 —— 它是真机那个 socket 14"
    )
    assert config.fragment_hashes == [900, 901]
    assert config.socket_states == {0: True, 1: True, 2: False}, (
        "活动状态要如实记下来：报错时要能点名「哪个槽被禁用、所以没排」"
    )
    assert config.fragment_sockets == [0, 1, 2], (
        "读到的碎片槽（含禁用的那个）要记下来：报错必须点得出槽号，而槽号不许按位置推"
    )


def test_read_subclass_config_treats_a_missing_is_enabled_as_not_assignable() -> None:
    """`isEnabled` 缺字段 = 状态未知：**不排**（不许猜它开着）。

    与"账号明说 false"是两件事，所以 `socket_states` 里也不留记录（`None` ≠ `False`）。
    """
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments", 901: "hunter.solar.supers"},
    ))

    config = service.read_subclass_config("sub-1", 100, {
        "sub-1": {"sockets": [
            {"plugHash": 901},
            {"plugHash": 900, "isEnabled": True},
        ]}
    })

    assert config is not None
    assert config.plug_sockets == {1: 900}
    assert config.super_hash == 0
    assert config.socket_states == {1: True}


def test_read_subclass_config_returns_none_without_recognised_sockets() -> None:
    service = _service(_Manifest({900: _mod(NOT_A_MOD_CATEGORY, 0)}))

    assert service.read_subclass_config("", 100, {}) is None
    assert service.read_subclass_config("sub-1", 100, {}) is None
    assert service.read_subclass_config(
        "sub-1", 100, {"sub-1": {"sockets": [{"plugHash": 900}]}}
    ) is None


# ── _find_subclass_socket ────────────────────────────────────────────


async def test_find_subclass_socket_matches_initial_item_then_plug_set() -> None:
    manifest = _Manifest(
        definitions={100: {"sockets": {"socketEntries": [
            {"singleInitialItemHash": 900},
            {"reusablePlugSetHash": 700},
        ]}}},
        plug_sets={700: {"reusablePlugItems": [{"plugItemHash": 901}]}},
    )
    service = _service(manifest)
    sockets_map = {"sub-1": {"sockets": [
        {"plugHash": 0, "isEnabled": True}, {"plugHash": 0, "isEnabled": True},
    ]}}

    assert await service._find_subclass_socket("sub-1", 100, 900, "super", sockets_map) == 0
    assert await service._find_subclass_socket("sub-1", 100, 901, "super", sockets_map) == 1


async def test_find_subclass_socket_falls_back_to_socket_type() -> None:
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments", 901: "shared.solar.fragments"},
    ))

    result = await service._find_subclass_socket(
        "sub-1", 100, 900, "fragment",
        {"sub-1": {"sockets": [{"plugHash": 901, "isEnabled": True}]}},
    )

    assert result == 0


async def test_find_subclass_socket_falls_back_to_category_hash_without_identifier() -> None:
    service = _service(_Manifest({
        100: {"sockets": {"socketEntries": []}},
        900: _mod(LoadoutEquipmentService._PLUG_CAT_ASPECTS, 0),
        901: _mod(LoadoutEquipmentService._PLUG_CAT_ASPECTS, 0),
    }))

    result = await service._find_subclass_socket(
        "sub-1", 100, 900, "aspect",
        {"sub-1": {"sockets": [{"plugHash": 901, "isEnabled": True}]}},
    )

    assert result == 0


async def test_find_subclass_socket_honours_assigned_and_returns_none() -> None:
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments"},
    ))
    sockets_map = {"sub-1": {"sockets": [{"plugHash": 900, "isEnabled": True}]}}

    assert await service._find_subclass_socket(
        "sub-1", 100, 900, "fragment", sockets_map, assigned_sockets={0}
    ) is None
    # 类型对不上又不共享类别时找不到插槽
    assert await service._find_subclass_socket("sub-1", 100, 999, "super", sockets_map) is None


async def test_find_subclass_socket_never_returns_a_disabled_socket() -> None:
    """**禁用槽不是候选**：真机棱镜术士 socket 14 是禁用的碎片槽，按类型兜底正好会挑中它。

    真机原文（`equip_build` 的 `subclass` 步骤）：
    `DestinySocketActionNotAllowed` / `request.plug.socketIndex: The requested socket is disabled.`
    —— 同一颗碎片写进开着的那几个槽（9–13）都成功。
    """
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments", 901: "shared.solar.fragments"},
    ))
    sockets_map = {"sub-1": {"sockets": [
        {"plugHash": 900, "isEnabled": True},
        {"plugHash": 901, "isEnabled": False},
    ]}}

    # 槽 0 已被占（assigned），按类型兜底下一个就是禁用槽 1 —— 必须报"找不到"而不是挑它
    assert await service._find_subclass_socket(
        "sub-1", 100, 901, "fragment", sockets_map, assigned_sockets={0}
    ) is None
    assert await service._find_subclass_socket(
        "sub-1", 100, 901, "fragment", sockets_map
    ) == 0


# ── _apply_subclass_config ───────────────────────────────────────────
#
# 这段决定要不要写入账号，失败必须如实上报而不是静默跳过。


def _subclass_service() -> LoadoutEquipmentService:
    manifest = _Manifest(
        infos={50: {"itemType": 16}},
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={
            900: "hunter.solar.supers",
            901: "shared.void.grenades",
            902: "hunter.solar.supers",
            903: "shared.void.grenades",
        },
    )
    service = _service(manifest)
    service._bungie.insert_socket_plug_free = AsyncMock(
        return_value={"ErrorCode": 1}
    )
    return service


def _subclass_loadout(config) -> Loadout:
    return Loadout(id="l", name="L", character="hunter", subclass=config, items=[])


def _profile_with_subclass(instance_id: str = "sub-1"):
    return {
        "characterEquipment": {"data": {"char": {"items": [
            {"itemHash": 50, "itemInstanceId": instance_id}
        ]}}},
        # 子职业插槽的类型靠插槽里当前插着的 plug 判定，所以这里不能是空插槽。
        # `isEnabled` 必须写上：现场（profile 组件 305 的 `DestinyItemSocketState`）就是这么报的，
        # 而**只有明说 true 的槽才算能写**。替身不报这个字段，等于替账号断言一件它没说过的事，
        # 于是"状态未知"这条真实分支会被替身悄悄盖住。
        "itemComponents": {"sockets": {"data": {instance_id: {"sockets": [
            {"plugHash": 902, "isEnabled": True}, {"plugHash": 903, "isEnabled": True}
        ]}}}},
    }


async def _resolve(service, profile):
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "player", "membership_type": 3}
    )
    service._resolver.get_profile = AsyncMock(return_value=profile)


async def test_apply_subclass_config_is_a_noop_without_a_subclass() -> None:
    service = _subclass_service()
    steps: list = []

    assert await service._apply_subclass_config(
        "player", _subclass_loadout(None), "char", 3, steps
    ) == (True, "")
    assert steps == []


async def test_apply_subclass_config_reports_a_missing_equipped_subclass() -> None:
    service = _subclass_service()
    await _resolve(service, {"characterEquipment": {"data": {"char": {"items": []}}}})
    steps: list = []

    ok, _why = await service._apply_subclass_config(
        "player",
        _subclass_loadout(LoadoutSubclassConfig(subclass_item_hash=50, super_hash=900)),
        "char",
        3,
        steps,
    )

    assert ok is False
    assert [step.detail for step in steps] == ["找不到已装备的子职业"]


async def test_apply_subclass_config_stops_when_the_target_subclass_is_not_in_the_bag() -> None:
    """保存的子职业不在这个角色背包里：如实失败，不许改到别的子职业上。"""
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    service._bungie.equip_item = AsyncMock(return_value={"ErrorCode": 1})
    steps: list = []

    ok, _why = await service._apply_subclass_config(
        "player",
        _subclass_loadout(LoadoutSubclassConfig(subclass_item_hash=999, super_hash=900)),
        "char",
        3,
        steps,
    )

    assert ok is False
    assert "不在这个角色的背包里" in steps[0].detail
    service._bungie.equip_item.assert_not_awaited()
    service._bungie.insert_socket_plug_free.assert_not_awaited()


async def test_apply_subclass_config_switches_first_then_writes_the_new_item() -> None:
    """不一致时**先换上再配**，而且插槽要写在新物品上（旧实例的槽索引对新物品没意义）。"""
    service = _subclass_service()
    before = _profile_with_subclass()
    before["characterInventories"] = {"data": {"char": {"items": [
        {"itemHash": 999, "itemInstanceId": "sub-2"}
    ]}}}
    switched = _profile_with_subclass("sub-2")
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "player", "membership_type": 3}
    )
    # 第一次读看到旧物品 + 背包里的目标；换完那次读看到新物品
    service._resolver.get_profile = AsyncMock(side_effect=[before, switched])
    service._bungie.equip_item = AsyncMock(return_value={"ErrorCode": 1})
    steps: list = []

    ok, _why = await service._apply_subclass_config(
        "player",
        _subclass_loadout(
            LoadoutSubclassConfig(
                subclass_item_hash=999, subclass_instance_id="sub-2", plug_sockets={0: 900}
            )
        ),
        "char",
        3,
        steps,
    )

    assert ok is True
    service._bungie.equip_item.assert_awaited_once_with(
        item_instance_id="sub-2", character_id="char", membership_type=3
    )
    assert steps[0].detail == "已换上保存的子职业（实例 sub-2）"
    assert service._bungie.insert_socket_plug_free.await_args.args[0] == "sub-2"


async def test_apply_subclass_config_uses_exact_socket_map_when_present() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={1: 901}
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is True
    assert [(step.action, step.detail, step.success) for step in steps] == [
        ("subclass", "plug '#901' 已应用", True)
    ]
    service._bungie.insert_socket_plug_free.assert_awaited_once_with(
        "sub-1", 901, 1, 0, "char", 3
    )


async def test_apply_subclass_config_falls_back_to_named_hashes() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50,
        subclass_instance_id="sub-1",
        super_hash=900,
        grenade_hash=901,
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is True
    assert [step.detail for step in steps] == [
        "super '#900' 已应用", "grenade '#901' 已应用",
    ]
    # 两个 plug 分别占用插槽 0 和 1
    assert [call.args[2] for call in service._bungie.insert_socket_plug_free.await_args_list] == [0, 1]


async def test_apply_subclass_config_records_per_plug_failures_and_keeps_going() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    service._bungie.insert_socket_plug_free = AsyncMock(
        side_effect=[{"ErrorCode": 0}, {"ErrorCode": 1}]
    )
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50,
        subclass_instance_id="sub-1",
        super_hash=900,
        grenade_hash=901,
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    # 失败那一行必须带上游原文（与模组那条路同一口径）：以前只有"plug <hash> 已应用"，
    # 真机 2026-10-03 第 3 轮一颗碎片写失败时，原因就是这么丢的。
    assert [(step.detail, step.success) for step in steps] == [
        ("super '#900' 已应用 失败：上游没给原因", False),
        ("grenade '#901' 已应用", True),
    ]
    assert _why and "#900" in _why


async def test_apply_subclass_config_reports_a_missing_socket_and_keeps_going() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={9: 901}
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert steps[0].detail == "找不到 plug 901 的兼容插槽"
    service._bungie.insert_socket_plug_free.assert_not_awaited()


async def test_apply_subclass_config_never_writes_into_a_disabled_socket() -> None:
    """方案里的槽到执行时是禁用的：**不写、如实说**（真机 socket 14 那条路的最后一道闸）。

    真机原文就是这个意思：`DestinySocketActionNotAllowed` /
    `request.plug.socketIndex: The requested socket is disabled.` —— 上游 500，
    而回执里当时只有一句"已应用"。这里改成：明确说"槽 N 禁用，不是可写的槽"，并且真的不调上游。
    """
    service = _subclass_service()
    profile = {
        "characterEquipment": {"data": {"char": {"items": [
            {"itemHash": 50, "itemInstanceId": "sub-1"}
        ]}}},
        "itemComponents": {"sockets": {"data": {"sub-1": {"sockets": [
            {"plugHash": 902, "isEnabled": True},
            {"plugHash": 903, "isEnabled": False},
        ]}}}},
    }
    await _resolve(service, profile)
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={1: 901}
    )

    ok, why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert steps[0].detail == "plug '#901' 未排：槽 1 禁用，不是可写的槽"
    assert steps[0].success is False
    assert why and "槽 1 禁用" in why
    service._bungie.insert_socket_plug_free.assert_not_awaited()


async def test_apply_subclass_config_refuses_a_socket_without_a_live_state() -> None:
    """现场没给 `isEnabled`（状态未知）：同样不写 —— 读不到就不赌它开着。"""
    service = _subclass_service()
    profile = {
        "characterEquipment": {"data": {"char": {"items": [
            {"itemHash": 50, "itemInstanceId": "sub-1"}
        ]}}},
        "itemComponents": {"sockets": {"data": {"sub-1": {"sockets": [
            {"plugHash": 902},
        ]}}}},
    }
    await _resolve(service, profile)
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={0: 901}
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert "状态未知" in steps[0].detail
    service._bungie.insert_socket_plug_free.assert_not_awaited()


async def test_apply_subclass_config_turns_http_errors_into_failed_steps() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    service._bungie.insert_socket_plug_free = AsyncMock(
        side_effect=aiobungie.HTTPError("boom", http.HTTPStatus.BAD_REQUEST)
    )
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={0: 900}
    )

    ok, _why = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert steps[0].action == "error"
    assert steps[0].detail.startswith("plug 900 应用失败:")
    assert steps[0].success is False


async def test_empty_socket_cache_is_reread_instead_of_failing_the_preflight() -> None:
    """缓存里是一条**空列表**时要现读一次，而不是每件都报"找不到唯一兼容插槽"。

    真机复现：`equip_build` 先把仓库里那件护甲搬过来，随后逐件报
    `找不到模组 <hash> 在 '<装备名>' 上的唯一兼容插槽` —— 因为搬过来那件在旧快照里
    没有插槽数据（缓存里是 []），而旧代码只判"键在不在缓存里"，于是每个槽都被
    `index >= len(sockets_data)` 跳过。
    """
    general = 2487827355  # enhancements.v2_general（属性模组）
    mod = 4183296050
    manifest = _Manifest(
        definitions={
            50: {"sockets": {"socketEntries": [{"reusablePlugSetHash": 111}]}},
            mod: _mod(general, 3),
        },
        plug_sets={111: {"reusablePlugItems": [{"plugItemHash": mod}]}},
    )
    service = _service(manifest)
    service._resolver.get_profile = AsyncMock(return_value={
        "itemComponents": {"sockets": {"data": {"sub-1": {"sockets": [{"plugHash": 0}]}}}}
    })

    index = await service._find_mod_socket(
        "sub-1", 50, mod, "player", 3, sockets_cache={"sub-1": []}
    )

    assert index == 0, "空缓存要触发重读，而不是当成没有插槽"


async def test_find_mod_socket_writes_tuning_like_any_other_mod() -> None:
    """**调谐槽也是插槽**：`equip_build` 把调谐和属性模组一视同仁地写进去。

    这是"代写调谐"这条能力的执行端守门（口径见 `docs/plans/TUNING_WRITE_PLAN.md`）：
    候选计划里带的调谐插件走的就是 `mods` → `_find_mod_socket`（它按插件类别找槽，
    调谐类别本来就在 `_MOD_CATEGORY_HASHES` 里），不需要第二条写入路径。
    """
    tuning_category = 3481777685  # core.gear_systems.armor_tiering.plugs.tuning.mods
    manifest = _Manifest(
        definitions={900: _mod(tuning_category, 0)},
        plug_sets={701: {"reusablePlugItems": [{"plugItemHash": 900}]}},
    )
    service = _service(manifest)
    manifest._definitions[100] = {
        "sockets": {
            "socketEntries": [
                {"reusablePlugSetHash": 0},
                {"reusablePlugSetHash": 701},   # 调谐槽在这一位
            ]
        }
    }

    result = await service._find_mod_socket(
        "item-1",
        100,
        900,
        "player",
        3,
        sockets_cache={"item-1": [{"plugHash": 0}, {"plugHash": 0}]},
    )

    assert result == 1, "调谐必须和别的模组一样，按类别找到它自己的插槽"


# ── 调谐不能被组件 207 判据拦下（2026-09-28 真机事故的回归守门）─────────────


async def test_tuning_is_not_blocked_by_the_role_level_plug_sets() -> None:
    """调谐在预检里**不许**被组件 207 的"这一位能不能插"拦下 —— 207 根本不覆盖调谐槽。

    真机（2026-09-26，`docs/plans/TUNING_WRITE_PLAN.md` 那轮之后）：
    `equip_build` 把 3 颗调谐全判成"装不上：不在 Bungie 给这一位角色的可插入清单里
    （游戏里同样装不上）"，还给了「需要守护者等级3」这种从 207 抄来的插入条件 ——
    **连上游都没试过一次**。根因是执行路径（本函数）只请求 `INVENTORY_SOCKETS`（305 那条），
    手上没有组件 310（逐件的调谐清单），却拿 207 判调谐；而 207 里**正装着的那颗调谐都不在**。

    注入验证：把本函数里的 `not self.plug_is_tuning(mod_hash)` 去掉，这条立刻变红
    （调谐会被排成 `blocked`）。
    """
    tuning_category = 3481777685  # core.gear_systems.armor_tertiary... 见 TUNING_CATEGORY_HASH
    installed, target = 901, 900
    manifest = _Manifest(
        definitions={
            # 插槽定义挂在**这件护甲的 item_hash**（_item() 默认 100）上：
            # 预检读的就是 item.item_hash，建在别的 hash 上会退化成"没数据 = 不判断"。
            100: {"sockets": {"socketEntries": [{"reusablePlugSetHash": 701}]}},
            installed: _mod(tuning_category, 0),
            target: _mod(tuning_category, 0),
        },
        plug_sets={701: {"reusablePlugItems": [{"plugItemHash": installed}]}},
        infos={name: {"name": name} for name in (installed, target)},
    )
    service = _service(manifest)
    item = _item(mod_sockets={0: target})
    sockets_cache = {"item-1": [{"plugHash": installed}]}
    instances = {"item-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}}
    # 207 池里有"已装着的那颗"、没有目标那颗 —— 旧代码正是据此把它判成装不上。
    insertable = {701: {installed}}

    ops = await service._prepare_mod_operations(
        item, "player", 3, sockets_cache, instances, insertable
    )

    actions = [(op.action, op.plug_hash) for op in ops]
    assert ("blocked", target) not in actions, (
        "调谐被 207 判据拦下了 —— 它只该走组件 310（这里没有 310，就该不判断）"
    )
    assert ("mod", target) in actions, "调谐应当照常排进写入计划"


async def test_non_tuning_mod_is_still_blocked_by_the_role_level_plug_sets() -> None:
    """反面：**非调谐**模组仍然照 207 拦 —— 别把上一条的修法放宽成"什么都不拦"。

    守的是"修一处、别塌另一处"：这条与上面那条是一对，任何一边松掉都能被抓到。
    """
    target = 900
    manifest = _Manifest(
        definitions={
            100: {"sockets": {"socketEntries": [{"reusablePlugSetHash": 701}]}},
            target: _mod(GENERAL_MOD_CATEGORY, 3),
        },
        plug_sets={701: {"reusablePlugItems": [{"plugItemHash": target}]}},
        infos={target: {"name": "职业模组"}},
    )
    service = _service(manifest)
    item = _item(mod_sockets={0: target})
    sockets_cache = {"item-1": [{"plugHash": 0}]}
    instances = {"item-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}}

    ops = await service._prepare_mod_operations(
        item, "player", 3, sockets_cache, instances, {701: set()}
    )

    actions = [(op.action, op.plug_hash) for op in ops]
    assert ("blocked", target) in actions, "非调谐模组不在 207 清单里时必须照旧拦住"


# ── 功能模组的能量按"最终净额"判（2026-10-03 真机白丢一颗的回归守门）─────────
#
# 真机那一件的现场（拿真 Manifest 的值搭出来：护甲 `光芒领主护腿` 3094263124，
# 插槽 0 = 通用属性模组 plug set 731468111，插槽 1/2/3 = 腿部模组 plug set 541478408）：
#
#   已装  插槽 0 职业模组 3 点 / 1 复原 1 点 / 2 宽恕 3 点 / 3 武器洗礼 3 点 → 已用 10，上限 11
#   照抄  [缚丝回收器 3 点|1 点] [谐振回收器 1 点|2 点] [武器洗礼 3 点|1 点]
#   求解器给了插槽 0 一颗 3 点的「手雷模组」（替掉同样 3 点的「职业模组」→ 净额 0）
#
# 旧代码按组顺序判"此刻够不够"：缚丝回收器要 3 点、替掉 1 点的复原 → 10 + 2 = 12/11 跳过；
# 可它后面那颗谐振回收器（1 点，替掉 3 点的宽恕）会腾出 2 点，全部装完只要 10/11 ——
# 20 颗里白丢的那一颗就是它。修法见 `loadout_functional_mods._plan_functional_mods`。

LEGS = "光芒领主护腿"
LEGS_ITEM_HASH = 3094263124
GENERAL_PLUG_SET = 731468111
LEGS_PLUG_SET = 541478408
GENERAL_EMPTY = 1980618587
LEGS_EMPTY = 2269836811
GRENADE_MOD = 1435557120        # 手雷模组 3 点（求解器给的属性模组）
CLASS_MOD = 4204488676          # 职业模组 3 点（现装在插槽 0）
RECUPERATION = 4087056174       # 复原 1 点
ABSOLUTION = 2793473444         # 宽恕 3 点
STRAND_SCAVENGER_3 = 2257238439  # 缚丝回收器 3 点（这一位能插的那版）
STRAND_SCAVENGER_1 = 1305848463  # 缚丝回收器 1 点（便宜那版：不在 207 清单里）
HARMONIC_SCAVENGER_1 = 877723168   # 谐振回收器 1 点
HARMONIC_SCAVENGER_2 = 1301391064  # 谐振回收器 2 点
WEAPON_SURGE_3 = 4046357305     # 武器洗礼 3 点（现装着）
WEAPON_SURGE_1 = 1901221009     # 武器洗礼 1 点


def _legs_manifest() -> _Manifest:
    return _Manifest(
        definitions={
            LEGS_ITEM_HASH: {"sockets": {"socketEntries": [
                {"reusablePlugSetHash": GENERAL_PLUG_SET, "singleInitialItemHash": GENERAL_EMPTY},
                {"reusablePlugSetHash": LEGS_PLUG_SET, "singleInitialItemHash": LEGS_EMPTY},
                {"reusablePlugSetHash": LEGS_PLUG_SET, "singleInitialItemHash": LEGS_EMPTY},
                {"reusablePlugSetHash": LEGS_PLUG_SET, "singleInitialItemHash": LEGS_EMPTY},
            ]}},
            GENERAL_EMPTY: _mod(GENERAL_MOD_CATEGORY, 0),
            LEGS_EMPTY: _mod(LEGS_MOD_CATEGORY, 0),
            GRENADE_MOD: _mod(GENERAL_MOD_CATEGORY, 3),
            CLASS_MOD: _mod(GENERAL_MOD_CATEGORY, 3),
            RECUPERATION: _mod(LEGS_MOD_CATEGORY, 1),
            ABSOLUTION: _mod(LEGS_MOD_CATEGORY, 3),
            STRAND_SCAVENGER_3: _mod(LEGS_MOD_CATEGORY, 3),
            STRAND_SCAVENGER_1: _mod(LEGS_MOD_CATEGORY, 1),
            HARMONIC_SCAVENGER_1: _mod(LEGS_MOD_CATEGORY, 1),
            HARMONIC_SCAVENGER_2: _mod(LEGS_MOD_CATEGORY, 2),
            WEAPON_SURGE_3: _mod(LEGS_MOD_CATEGORY, 3),
            WEAPON_SURGE_1: _mod(LEGS_MOD_CATEGORY, 1),
        },
        plug_sets={
            GENERAL_PLUG_SET: {"reusablePlugItems": [
                {"plugItemHash": GENERAL_EMPTY}, {"plugItemHash": GRENADE_MOD},
                {"plugItemHash": CLASS_MOD},
            ]},
            LEGS_PLUG_SET: {"reusablePlugItems": [
                {"plugItemHash": LEGS_EMPTY}, {"plugItemHash": RECUPERATION},
                {"plugItemHash": ABSOLUTION}, {"plugItemHash": STRAND_SCAVENGER_3},
                {"plugItemHash": STRAND_SCAVENGER_1}, {"plugItemHash": HARMONIC_SCAVENGER_1},
                {"plugItemHash": HARMONIC_SCAVENGER_2}, {"plugItemHash": WEAPON_SURGE_3},
                {"plugItemHash": WEAPON_SURGE_1},
            ]},
        },
        infos={hash_id: {"name": f"#{hash_id}"} for hash_id in (
            GRENADE_MOD, CLASS_MOD, RECUPERATION, ABSOLUTION, STRAND_SCAVENGER_3,
            STRAND_SCAVENGER_1, HARMONIC_SCAVENGER_1, HARMONIC_SCAVENGER_2,
            WEAPON_SURGE_3, WEAPON_SURGE_1,
        )},
    )


async def test_functional_mod_energy_is_judged_by_the_final_net_amount() -> None:
    """瞬时 12/11 但最终只有 10/11 的那一颗**必须装上**（真机 2026-10-03 白丢的那颗）。

    注入验证：把 `_plan_functional_mods` 第二趟的 `sorted(...)` 换回 `pending`（按组顺序判
    瞬时值）—— 这条立刻变红：「缚丝回收器」会被排成 `blocked`。
    """
    manifest = _legs_manifest()
    service = _service(manifest)
    item = _item(
        item_hash=LEGS_ITEM_HASH, name=LEGS, slot="legs",
        mod_sockets={0: GRENADE_MOD},
        functional_mod_groups=[
            [STRAND_SCAVENGER_3, STRAND_SCAVENGER_1],
            [HARMONIC_SCAVENGER_1, HARMONIC_SCAVENGER_2],
            [WEAPON_SURGE_3, WEAPON_SURGE_1],
        ],
    )
    sockets = [
        {"plugHash": CLASS_MOD}, {"plugHash": RECUPERATION},
        {"plugHash": ABSOLUTION}, {"plugHash": WEAPON_SURGE_3},
    ]
    instances = {"item-1": {"energy": {"energyCapacity": 11, "energyUsed": 10}}}
    # 组件 207：便宜那版「缚丝回收器」**不在**这一位的可插入清单里（真机就是这么给的）
    insertable = {
        GENERAL_PLUG_SET: {GRENADE_MOD, CLASS_MOD},
        LEGS_PLUG_SET: {
            STRAND_SCAVENGER_3, HARMONIC_SCAVENGER_1, WEAPON_SURGE_3, RECUPERATION,
        },
    }

    ops = await service._prepare_mod_operations(
        item, "player", 3, {"item-1": sockets}, instances, insertable
    )

    actions = [op.as_tuple() for op in ops]
    assert ("blocked", STRAND_SCAVENGER_3, 1) not in actions, (
        "缚丝回收器被按瞬时值判成装不下了 —— 判的该是全部组算完之后的净额"
    )
    assert actions == [
        ("mod", GRENADE_MOD, 0),
        ("keep", WEAPON_SURGE_3, 3),
        # 先腾后占：腾出 2 点的那颗排在要 2 点的那颗前面（游戏逐颗校验能量）
        ("mod", HARMONIC_SCAVENGER_1, 2),
        ("mod", STRAND_SCAVENGER_3, 1),
    ]
    assert item.mod_sockets == {
        0: GRENADE_MOD, 1: STRAND_SCAVENGER_3, 2: HARMONIC_SCAVENGER_1, 3: WEAPON_SURGE_3,
    }, "写过的槽都要进 mod_sockets，否则回读核对会把成功的判成失败"
    # 真机最终形态：3 + 3 + 1 + 3 = 10 ≤ 11（旧代码把这一套算成 12）
    assert sum(
        cost for hash_id in item.mod_sockets.values()
        if (cost := service._plug_energy_cost(hash_id)) is not None
    ) == 10
