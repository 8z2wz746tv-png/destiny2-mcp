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


async def test_insert_armor_mod_picks_paid_or_free_endpoint_by_cost() -> None:
    service = _service(_Manifest({
        600: _mod(GENERAL_MOD_CATEGORY, 2),
        601: _mod(GENERAL_MOD_CATEGORY, 0),
    }))
    service._bungie.insert_socket_plug = AsyncMock(return_value={"paid": True})
    service._bungie.insert_socket_plug_free = AsyncMock(return_value={"free": True})

    paid = await service._insert_armor_mod("item-1", 600, 3, "char", 3)
    free = await service._insert_armor_mod("item-1", 601, 4, "char", 3)

    assert paid == {"paid": True}
    assert free == {"free": True}
    service._bungie.insert_socket_plug.assert_awaited_once_with("item-1", 600, 3, 0, "char", 3)
    service._bungie.insert_socket_plug_free.assert_awaited_once_with("item-1", 601, 4, 0, "char", 3)


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

    assert operations == [("mod", 600, 1), ("mod", 601, 3)]


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
    assert operations == [("clear", 800, 0), ("clear", 800, 1), ("mod", 600, 2)]


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

    assert operations == [("clear", 800, 1), ("mod", 600, 0)]


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
            {"plugHash": 900}, {"plugHash": 901}, {"plugHash": 902},
            {"plugHash": 903}, {"plugHash": 0},
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
        "sub-1": {"sockets": [{"plugHash": 900}, {"plugHash": 901}]}
    })

    assert config is not None
    assert config.super_hash == 900
    assert config.fragment_hashes == [901]


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
    sockets_map = {"sub-1": {"sockets": [{"plugHash": 0}, {"plugHash": 0}]}}

    assert await service._find_subclass_socket("sub-1", 100, 900, "super", sockets_map) == 0
    assert await service._find_subclass_socket("sub-1", 100, 901, "super", sockets_map) == 1


async def test_find_subclass_socket_falls_back_to_socket_type() -> None:
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments", 901: "shared.solar.fragments"},
    ))

    result = await service._find_subclass_socket(
        "sub-1", 100, 900, "fragment", {"sub-1": {"sockets": [{"plugHash": 901}]}}
    )

    assert result == 0


async def test_find_subclass_socket_falls_back_to_category_hash_without_identifier() -> None:
    service = _service(_Manifest({
        100: {"sockets": {"socketEntries": []}},
        900: _mod(LoadoutEquipmentService._PLUG_CAT_ASPECTS, 0),
        901: _mod(LoadoutEquipmentService._PLUG_CAT_ASPECTS, 0),
    }))

    result = await service._find_subclass_socket(
        "sub-1", 100, 900, "aspect", {"sub-1": {"sockets": [{"plugHash": 901}]}}
    )

    assert result == 0


async def test_find_subclass_socket_honours_assigned_and_returns_none() -> None:
    service = _service(_Manifest(
        definitions={100: {"sockets": {"socketEntries": []}}},
        identifiers={900: "shared.solar.fragments"},
    ))
    sockets_map = {"sub-1": {"sockets": [{"plugHash": 900}]}}

    assert await service._find_subclass_socket(
        "sub-1", 100, 900, "fragment", sockets_map, assigned_sockets={0}
    ) is None
    # 类型对不上又不共享类别时找不到插槽
    assert await service._find_subclass_socket("sub-1", 100, 999, "super", sockets_map) is None


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
        "itemComponents": {"sockets": {"data": {instance_id: {"sockets": [
            {"plugHash": 902}, {"plugHash": 903}
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
    ) is True
    assert steps == []


async def test_apply_subclass_config_reports_a_missing_equipped_subclass() -> None:
    service = _subclass_service()
    await _resolve(service, {"characterEquipment": {"data": {"char": {"items": []}}}})
    steps: list = []

    ok = await service._apply_subclass_config(
        "player",
        _subclass_loadout(LoadoutSubclassConfig(subclass_item_hash=50, super_hash=900)),
        "char",
        3,
        steps,
    )

    assert ok is False
    assert [step.detail for step in steps] == ["找不到已装备的子职业"]


async def test_apply_subclass_config_refuses_a_different_subclass() -> None:
    """保存/确认的子职业与当前装备不一致时必须停手，不能改到别的子职业上。"""
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []

    ok = await service._apply_subclass_config(
        "player",
        _subclass_loadout(LoadoutSubclassConfig(subclass_item_hash=999, super_hash=900)),
        "char",
        3,
        steps,
    )

    assert ok is False
    assert steps[0].detail == "当前子职业与保存/确认的子职业不一致。"
    service._bungie.insert_socket_plug_free.assert_not_awaited()


async def test_apply_subclass_config_uses_exact_socket_map_when_present() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={1: 901}
    )

    ok = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is True
    assert [(step.action, step.detail, step.success) for step in steps] == [
        ("subclass", "plug 901 已应用", True)
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

    ok = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is True
    assert [step.detail for step in steps] == ["super 900 已应用", "grenade 901 已应用"]
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

    ok = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert [(step.detail, step.success) for step in steps] == [
        ("super 900 已应用", False),
        ("grenade 901 已应用", True),
    ]


async def test_apply_subclass_config_reports_a_missing_socket_and_keeps_going() -> None:
    service = _subclass_service()
    await _resolve(service, _profile_with_subclass())
    steps: list = []
    config = LoadoutSubclassConfig(
        subclass_item_hash=50, subclass_instance_id="sub-1", plug_sockets={9: 901}
    )

    ok = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert steps[0].detail == "找不到 plug 901 的兼容插槽"
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

    ok = await service._apply_subclass_config(
        "player", _subclass_loadout(config), "char", 3, steps
    )

    assert ok is False
    assert steps[0].action == "error"
    assert steps[0].detail.startswith("plug 900 应用失败:")
    assert steps[0].success is False
