"""一颗模组只出一条结论（真机 2026-10-03 的 15 对矛盾）。

真机回执（`build_assistant(intent="equip_build")`，5 件 × 3 颗功能模组）里每一颗都同时出现两条：

    action=mod         detail="模组 '谐振虹吸' → '光芒领主面具'"                            success=true
    action=mod_blocked detail="'光芒领主面具' 插槽 -1 的模组 '谐振虹吸' 装不上：同名版本都不在…"  success=false

根因不在"两处各记一条、没人去重"，而在**预检那一趟留下的副作用**：

1. `_mod_preflight`（Step 0）按设计是只读守门、结果有意丢弃，但它跑在**调用方那批
   `LoadoutItem` 对象**上；
2. 规划会往 `item.mod_sockets` 记下"我挑中的槽位"（写完之后 `_verify_loadout` 要按它逐槽回读），
   而这个字段同时又是**调用方的输入**（存下来的配装用它指定槽位）；
3. 于是写入那一趟（Step 2）把预检自己挑的槽位读成了"调用方指定"：同一颗模组先按指定槽位写一次
   （成功），再作为功能模组组去挑槽 —— 那几格已经被指定占了 → `blocked`，槽号还是内部哨兵 `-1`，
   原因也是没查过 207 清单就下的断言。

所以这里钉两件事：**守门不在计划上留痕**、**一颗模组只出一条结论**。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.models import Loadout, LoadoutItem
from destiny_mcp.services import write_readback
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService

ITEM = "6917530188460608169"   # 真机那件的实例号（光芒领主面具）
ITEM_HASH = 3091179819

# 真机那几颗模组（hash 与名字取自 2026-10-03 的 canonical build / zh Manifest）：
# 三颗照抄来的功能模组都在 `enhancements.v2_head` 里，那颗属性模组在 `enhancements.v2_general` 里。
STAT_MOD = 1180408010          # 生命值模组
SIPHON = -462601277            # 谐振虹吸
FINDER = -1699128059           # 特殊武器弹药搜寻者
HEAVY = 644105                 # 重型弹药搜寻者
FUNCTIONAL = (SIPHON, FINDER, HEAVY)

# 每颗模组只落得进自己那一格。真机那件头盔上也是三格各装一颗：回执里三颗都写成功了，
# 而规划阶段同一个槽不许被排两次（排第二次直接抛"找不到唯一兼容插槽"）→ 三个槽。
_SOCKET_OF = {STAT_MOD: 0, SIPHON: 1, FINDER: 2, HEAVY: 3}
_NAMES = {STAT_MOD: "生命值模组", SIPHON: "谐振虹吸", FINDER: "特殊武器弹药搜寻者", HEAVY: "重型弹药搜寻者"}


async def _find_mod_socket(
    item_instance_id, item_hash, mod_hash, membership_id, membership_type,
    sockets_cache=None, excluded_socket_indices=None,
):
    """替身按"每颗模组只有一格"的真实约束答：那格被占了就**挑不到**（返回 None）。

    真机也一样：`_find_mod_socket` 挑不到就返回 None，`_choose_functional_variant` 于是给
    "装不上"—— 区别只在真机那一次是**被预检留下的槽位**占掉的。
    """
    socket_index = _SOCKET_OF.get(mod_hash)
    if socket_index is None or socket_index in set(excluded_socket_indices or ()):
        return None
    return socket_index


def _loadout() -> Loadout:
    return Loadout(
        id="local-1",
        name="照抄配装",
        character="warlock",
        items=[LoadoutItem(
            item_hash=ITEM_HASH,
            name="光芒领主面具",
            slot="helmet",
            item_instance_id=ITEM,
            mods=[STAT_MOD],
            functional_mod_groups=[[plug] for plug in FUNCTIONAL],
        )],
    )


def _profile() -> dict:
    return {
        "characters": {"data": {"char-1": {"classType": 2}}},
        "itemComponents": {
            "instances": {"data": {ITEM: {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            "sockets": {"data": {ITEM: {"sockets": [{"plugHash": 0} for _ in range(4)]}}},
        },
    }


def _service() -> LoadoutEquipmentService:
    service = LoadoutEquipmentService(
        MagicMock(name="bungie"), MagicMock(name="manifest"), MagicMock(name="resolver"),
    )
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "mid", "membership_type": 3}
    )
    service._resolver.resolve_character_id = AsyncMock(return_value="char-1")
    service._resolver.get_profile = AsyncMock(return_value=_profile())
    service._manifest.get_item_definition = MagicMock(
        return_value={"sockets": {"socketEntries": []}}
    )
    service._manifest.get_item_name = MagicMock(side_effect=lambda h: _NAMES.get(h, str(h)))
    service._find_mod_socket = _find_mod_socket
    service._insert_armor_mod = AsyncMock(return_value={"ErrorCode": 1})
    service._transfer.transfer_item = AsyncMock(return_value=SimpleNamespace(success=True))
    service._transfer.equip_items = AsyncMock(return_value={"success": True})
    service.read_armor_mod_sockets = MagicMock(return_value={})
    service._capture_recovery_state = AsyncMock(return_value={
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": Loadout(id="prev", name="执行前配装", character="warlock", items=[]),
        "target_states": {},
    })
    return service


def _conclusions(steps: list) -> tuple[dict[str, int], dict[str, int]]:
    """回执 → (写成的模组名 → 条数, 报"装不上"的模组名 → 条数)。"""
    written: dict[str, int] = {}
    blocked: dict[str, int] = {}
    for step in steps:
        for name in _NAMES.values():
            if step.action in ("mod", "mod_clear") and f"模组 '{name}'" in step.detail:
                written[name] = written.get(name, 0) + 1
            elif step.action == "mod_blocked" and f"模组 '{name}'" in step.detail:
                blocked[name] = blocked.get(name, 0) + 1
    return written, blocked


@pytest.mark.asyncio
async def test_preflight_leaves_no_trace_in_the_write_pass_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守门是只读的：它挑中的槽位**不许**成为写入那一趟的输入。

    真机 2026-10-03 的 15 对矛盾全部来自这一条 —— 写入那一趟把预检留下的 `mod_sockets`
    读成了"调用方指定槽位"。注入违规（Step 0 传 `loadout` 而不是副本）时本条会红：
    第二次规划开始时 `mod_sockets` 里已经有预检留下的 3 个槽位。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _service()
    loadout = _loadout()
    seen: list[dict[int, int]] = []
    real = service._prepare_mod_operations

    async def spy(item, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(dict(item.mod_sockets))
        return await real(item, *args, **kwargs)

    service._prepare_mod_operations = spy  # type: ignore[method-assign]
    await service.equip_local("Alpha#0100", loadout)

    assert len(seen) == 2, "两趟规划：预检一次、写入一次"
    assert seen[0] == {}, "规划开始时 `mod_sockets` 里只有调用方指定的槽位（这份配装没有）"
    assert seen[1] == {}, (
        "预检的结果有意丢弃，不许把它挑中的槽位带进写入那一趟的输入"
    )
    assert loadout.items[0].mod_sockets == {1: SIPHON, 2: FINDER, 3: HEAVY}, (
        "写入那一趟自己挑的槽位照旧要记下来（回读核对按它逐槽比）"
    )


@pytest.mark.asyncio
async def test_every_mod_gets_exactly_one_conclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """走完预检 + 写入两趟：每颗模组只出一条结论，槽号哨兵 `-1` 不进 detail。

    这条是**端到端**的：`_mod_preflight`、`_prepare_mod_operations`、`_plan_functional_mods`
    全是真代码，只有"哪颗进哪格"和上游写入是替身。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    service = _service()
    loadout = _loadout()

    result = await service.equip_local("Alpha#0100", loadout)

    written, blocked = _conclusions(result.steps)
    assert written == {name: 1 for name in _NAMES.values()}, (written, result.steps)
    assert blocked == {}, f"写成的模组不许再被判'装不上'：{blocked}"
    assert not [
        step for step in result.steps if step.action == "mod_blocked"
    ], result.steps
    assert all("插槽 -1" not in step.detail for step in result.steps), [
        step.detail for step in result.steps
    ]
    # 属性模组也照常安排（脏掉的 `mod_sockets` 会让 `mods` 整份被跳过）
    assert any("模组 '生命值模组'" in step.detail for step in result.steps), result.steps


def test_blocked_step_never_prints_the_socket_sentinel() -> None:
    """`插槽 -1` 是内部哨兵，面向用户的 detail 里只能读成"没找到可用的槽"。"""
    service = _service()

    step = service.blocked_mod_step(
        _loadout().items[0], SIPHON, -1, "没找到可用的槽（同名版本在这一位落不下）"
    )

    assert "插槽 -1" not in step.detail
    assert step.detail == (
        "'光芒领主面具' 的模组 '谐振虹吸' 装不上："
        "没找到可用的槽（同名版本在这一位落不下）"
    )
