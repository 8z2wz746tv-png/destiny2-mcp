"""组件号常量：锁死每个集合的确切内容。

为什么值得单独盯：收拢字面量时最容易犯的错是"看着差不多就替成一个常量"。
本例中 `[102,200,201,205,300,305]` 和 `[102,200,201,205,300,304,305]` 只差
一个 304，但它们是两条不同的历史路径 —— 替错了就悄悄多取一个组件（响应变大、
耗时上升），而功能测试多半还是绿的。所以这里把数字钉死，改常量必须改测试，
改的人就得先回答"这个调用点到底要不要 304"。
"""

from __future__ import annotations

import inspect

import pytest

from destiny_mcp.services import (
    artifact_service,
    inventory_analysis_service,
    inventory_service,
    loadout_equipment_service,
    loadout_service,
    profile_cache,
    profile_components,
    transfer_service,
    weapon_compare_service,
    weapon_detail_service,
)


def test_inventory_minimal_has_no_stats_component():
    # 300=物品实例、304=属性值、305=插槽
    assert profile_components.INVENTORY == [102, 200, 201, 205, 300, 304]
    assert profile_components.INVENTORY_MINIMAL == [102, 200, 201, 205, 300]
    assert profile_components.INVENTORY_SOCKETS == [102, 200, 201, 205, 300, 305]
    assert profile_components.ARMOR_SNAPSHOT == [102, 200, 201, 205, 300, 304, 305]


def test_weapon_detail_asks_for_every_weapon_component():
    # P3：武器详情必须同时拿到已装 plug(305)、能换的 plug(310)、展示 perk(302)、催化剂进度(308)
    assert profile_components.WEAPON_DETAIL == [102, 200, 201, 205, 300, 304, 305, 302, 310, 308]


def test_cache_union_covers_every_cached_caller():
    """缓存的后台刷新用 FULL；少一个组件就会把并集降级、逼下一次调用重拉 10 MB。

    只有走共享缓存的调用方（武器对比 INVENTORY_SOCKETS、武器详情 WEAPON_DETAIL、
    库存 INVENTORY）需要被覆盖；配装(206)与神器(100)走自己的取数路径，不在此列。
    """
    cached_callers = (
        profile_components.INVENTORY,
        profile_components.INVENTORY_SOCKETS,
        profile_components.WEAPON_DETAIL,
    )
    for components in cached_callers:
        assert set(profile_components.FULL) >= set(components), components


def test_loadout_and_artifact_sets_unchanged():
    assert profile_components.LOADOUT_SLOTS == [102, 200, 201, 205, 206]
    assert profile_components.ARTIFACT == [100, 102, 200, 201, 205, 300, 302]


@pytest.mark.parametrize(
    ("module", "constant", "expected"),
    [
        (inventory_service, "_INVENTORY_PROFILE_COMPONENTS", "INVENTORY"),
        (inventory_service, "_ARMOR_SNAPSHOT_COMPONENTS", "ARMOR_SNAPSHOT"),
        (inventory_analysis_service, "PROFILE_COMPONENTS", "INVENTORY"),
        (inventory_analysis_service, "DUPLICATE_WEAPON_PROFILE_COMPONENTS", "INVENTORY_SOCKETS"),
        (profile_cache, "_FULL_COMPONENTS", "FULL"),
        (profile_cache, "_BASIC_COMPONENTS", "INVENTORY"),
    ],
)
def test_module_level_constants_point_at_the_right_named_set(module, constant, expected):
    assert getattr(module, constant) is getattr(profile_components, expected)


def _source(module) -> str:
    return inspect.getsource(module)


@pytest.mark.parametrize(
    ("module", "named_set", "count"),
    [
        (transfer_service, "INVENTORY", 1),
        (weapon_compare_service, "INVENTORY_SOCKETS", 2),
        (weapon_detail_service, "WEAPON_DETAIL", 2),
        (artifact_service, "ARTIFACT", 1),
        (loadout_service, "LOADOUT_SLOTS", 1),
        (loadout_service, "ARMOR_SNAPSHOT", 1),
        (loadout_equipment_service, "ARMOR_SNAPSHOT", 1),
        (loadout_equipment_service, "INVENTORY_MINIMAL", 1),
        (loadout_equipment_service, "INVENTORY_SOCKETS", 1),
    ],
)
def test_call_sites_keep_their_historical_set(module, named_set, count):
    source = _source(module)
    assert source.count(f"profile_components.{named_set}") == count


def test_no_service_writes_a_raw_component_list_any_more():
    """除了定义处，服务里不许再出现裸组件号字面量。"""
    offenders = []
    for module in (
        artifact_service,
        inventory_analysis_service,
        inventory_service,
        loadout_equipment_service,
        loadout_service,
        profile_cache,
        transfer_service,
        weapon_compare_service,
        weapon_detail_service,
    ):
        for number, line in enumerate(_source(module).splitlines(), start=1):
            if "get_profile(" in line or line.lstrip().startswith("_"):
                continue
            for token in ("[102", "[100", ", 102,", ", 200,", ", 300,"):
                if token in line and "profile_components" not in line:
                    offenders.append(f"{module.__name__}:{number}: {line.strip()}")
                    break
    assert offenders == [], "请改用 profile_components 里的命名集合：\n" + "\n".join(offenders)


def test_describe_makes_logs_readable():
    assert profile_components.describe(profile_components.INVENTORY_MINIMAL) == (
        "102,200,201,205,300"
    )
