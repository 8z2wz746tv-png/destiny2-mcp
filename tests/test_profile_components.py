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
    loadout_recovery,
    loadout_service,
    loadout_subclass_sockets,
    profile_cache,
    profile_components,
    subclass_service,
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


def test_loadout_artifact_and_subclass_sets_unchanged():
    assert profile_components.LOADOUT_SLOTS == [102, 200, 201, 205, 206]
    assert profile_components.ARTIFACT == [100, 102, 200, 201, 205, 300, 302]
    # 换子职业必须看得见角色背包(201)里的其它子职业物品，305 读插槽
    assert profile_components.SUBCLASS == [200, 201, 205, 305]


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
        # P4：对比也要 310（"这一件能换什么"），所以从 INVENTORY_SOCKETS 升到 WEAPON_DETAIL
        (weapon_compare_service, "WEAPON_DETAIL", 2),
        (weapon_detail_service, "WEAPON_DETAIL", 2),
        (artifact_service, "ARTIFACT", 5),  # 装模组读+回读、读神器状态、换神器读+回读
        (subclass_service, "SUBCLASS", 3),  # 读当前配置 + 换之前找候选 + 换完回读核对
        (loadout_subclass_sockets, "SUBCLASS", 2),  # 换之前读一次、换完重读插槽
        (loadout_service, "LOADOUT_SLOTS", 1),
        (loadout_service, "ARMOR_SNAPSHOT", 1),
        # 抓取执行前状态的那段已抽到 loadout_recovery.py
        (loadout_recovery, "ARMOR_SNAPSHOT", 1),
        # 抓取/恢复那两段已抽到 loadout_recovery.py，钉桩跟着搬家
        (loadout_recovery, "INVENTORY_MINIMAL", 1),
        (loadout_equipment_service, "INVENTORY_SOCKETS", 2),  # 子职业 + 模组插槽
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
        loadout_subclass_sockets,
        profile_cache,
        subclass_service,
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


def test_socket_reads_always_carry_an_inventory_component() -> None:
    """要 305（插槽）就必须同时带上"清单类"组件，否则上游**一个插槽都不返回**。

    真机实测：`get_profile(..., [305])` → 0 件带插槽；带上 102/200/201/205/300 → 1627 件。
    模组插槽读取以前正是只写了 `[305]`，于是"找不到兼容插槽"报了一整晚，
    真正的原因是那次请求根本没拿到数据（静默读空）。
    """
    import re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "destiny_mcp"
    inventory = ("102", "200", "201", "205")
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "get_profile(" not in line or "305" not in line:
                continue
            if "profile_components" in line:  # 命名集合（含清单类组件）放行
                continue
            if not re.search(r"\[\s*\d", line):
                continue
            if any(token in line for token in inventory):
                continue
            offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert offenders == [], (
        "要 305 就得带清单类组件（102/200/201/205），否则插槽读回来是空的：\n"
        + "\n".join(offenders)
    )
