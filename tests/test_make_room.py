"""自动腾格（ADR-029）的判据守门。

夹具一律用**真 `Armor`**（不是手编 dict/namespace）：这个项目的教训是"夹具形状写错 → 单测全绿、
真机是错的"（2026-10-06 一天栽了三次）。
"""

from __future__ import annotations

import pytest

from destiny_mcp.build.models import Armor
from destiny_mcp.build.snapshot_version import snapshot_version
from destiny_mcp.models import LoadoutOperationResult
from destiny_mcp.services.make_room import (
    DEFAULT_LIMIT,
    make_room,
    pick_move_aside,
    sort_key,
)


def _armor(
    instance_id: str,
    name: str,
    *,
    tier: int = 5,
    power: int | None = 550,
    equipped: bool = False,
    locked: bool = False,
    masterworked: bool = True,
    artifice: bool = False,
    slot: str = "gauntlets",
) -> Armor:
    return Armor(
        item_instance_id=instance_id,
        item_hash=123,
        name=name,
        slot=slot,
        power=power,
        tier=tier,
        is_equipped=equipped,
        is_locked=locked,
        is_masterworked=masterworked,
        is_artifice=artifice,
    )


# ── "绝不腾"四条 ────────────────────────────────────────────────────────────


def test_never_moves_what_is_being_worn() -> None:
    worn = _armor("a", "在穿的", equipped=True)
    assert pick_move_aside([worn]) == []
    assert pick_move_aside([worn, _armor("b", "没穿的")])[0].item_instance_id == "b"


def test_never_moves_a_locked_piece() -> None:
    """锁定的绝不腾 —— 这一条**比 DIM 保守**（DIM 那段代码没按锁过滤，见 ADR-029）。"""
    locked = _armor("a", "锁了的", locked=True)
    assert pick_move_aside([locked]) == []


def test_never_moves_a_piece_the_plan_needs() -> None:
    plan_piece = _armor("a", "本次要装的")
    assert pick_move_aside([plan_piece], reserved_instance_ids={"a"}) == []


def test_never_moves_a_piece_an_official_loadout_uses() -> None:
    used = _armor("a", "官方槽在用")
    assert pick_move_aside([used], official_instance_ids={"a"}) == []
    assert pick_move_aside(
        [used, _armor("b", "没人用")], official_instance_ids={"a"}
    )[0].item_instance_id == "b"


# ── 排序：越靠前越先腾 ──────────────────────────────────────────────────────


def test_lower_tier_goes_first() -> None:
    legendary = _armor("a", "传说", tier=5)
    exotic = _armor("b", "异域", tier=6)
    assert [a.item_instance_id for a in pick_move_aside([exotic, legendary])] == ["a", "b"]


def test_lower_power_goes_first() -> None:
    low = _armor("a", "低光等", power=500)
    high = _armor("b", "高光等", power=550)
    assert [a.item_instance_id for a in pick_move_aside([high, low])] == ["a", "b"]


def test_masterworked_and_artifice_are_kept_longer() -> None:
    plain = _armor("a", "没大师化", masterworked=False)
    mw = _armor("b", "大师化", masterworked=True)
    artifice = _armor("c", "巧匠", masterworked=True, artifice=True)
    assert [a.item_instance_id for a in pick_move_aside([artifice, mw, plain])] == ["a", "b", "c"]


def test_unknown_power_is_not_moved_first() -> None:
    """光等未知 = **最后**才动它（不知道就别先动），不是当 0 处理。"""
    unknown = _armor("a", "光等未知", power=None)
    known = _armor("b", "光等 500", power=500)
    assert [a.item_instance_id for a in pick_move_aside([unknown, known])] == ["b", "a"]


def test_ordering_is_stable_without_reading_input_order() -> None:
    first = _armor("a", "同分甲")
    second = _armor("b", "同分乙")
    assert [a.item_instance_id for a in pick_move_aside([second, first])] == ["a", "b"]
    assert [a.item_instance_id for a in pick_move_aside([first, second])] == ["a", "b"]


def test_sort_key_reads_the_real_model_fields() -> None:
    """字段名拼错必须炸：这条防止"夹具与真模型一起写错"那类事故。"""
    armor = _armor("a", "甲")
    assert sort_key(armor)[:4] == (5, 550, True, False)


def test_limit_caps_how_many_we_move() -> None:
    many = [_armor(f"{i:02d}", f"件{i}") for i in range(10)]
    assert len(pick_move_aside(many)) == DEFAULT_LIMIT
    assert len(pick_move_aside(many, limit=1)) == 1


# ── 执行：搬了哪几件、说了什么、什么时候不搬 ───────────────────────────────


@pytest.mark.asyncio
async def test_make_room_moves_and_reports_each_piece() -> None:
    moved: list[str] = []

    async def move_to_vault(armor: Armor) -> None:
        moved.append(armor.item_instance_id)

    result = await make_room(
        candidates=[_armor("a", "该走的", power=500), _armor("b", "在穿的", equipped=True)],
        move_to_vault=move_to_vault,
    )

    assert moved == ["a"], "穿着的不能被搬"
    moved_names = [a.name for a in result.moved]
    assert moved_names == ["该走的"]
    assert result.steps and "→ 仓库" in result.steps[0]
    assert result.blocked_reason == ""


@pytest.mark.asyncio
async def test_make_room_says_why_nothing_could_move() -> None:
    async def move_to_vault(armor: Armor) -> None:  # pragma: no cover - 不该被调用
        raise AssertionError("没有可腾的件时不许搬")

    result = await make_room(
        candidates=[_armor("a", "在穿的", equipped=True), _armor("b", "锁了的", locked=True)],
        move_to_vault=move_to_vault,
    )

    assert result.moved == [] and result.steps == []
    assert "没有可腾的件" in result.blocked_reason
    assert "锁定的" in result.blocked_reason, "要如实说清为什么没动它"


@pytest.mark.asyncio
async def test_a_failed_move_is_not_swallowed() -> None:
    """搬不动就**抛出来**（调用方去如实报），不许静默换下一件。"""

    async def move_to_vault(armor: Armor) -> None:
        raise RuntimeError("上游拒了")

    with pytest.raises(RuntimeError):
        await make_room(candidates=[_armor("a", "该走的")], move_to_vault=move_to_vault)


def test_never_picks_a_piece_that_is_already_in_the_vault() -> None:
    """仓库里的件不算候选：**把仓库件搬进仓库什么都不会发生**（格子还是满的）。

    2026-10-06 真机翻的车：快照里同一槽位既有身上的、也有仓库里的（臂铠格 57 件），
    判据没排掉仓库件，于是"腾了"却一格没空、复检照样拒 —— 而单测当时是绿的。
    """
    vault_piece = _armor("a", "仓库里的")  # source_location 默认空 = 身上
    vault_piece.source_location = "vault"
    assert pick_move_aside([vault_piece]) == []

    on_body = _armor("b", "身上的")
    on_body.source_location = "character"
    assert [a.item_instance_id for a in pick_move_aside([vault_piece, on_body])] == ["b"]


@pytest.mark.asyncio
async def test_making_room_advances_the_candidate_baseline() -> None:
    """腾完要把候选基线推到**腾完之后**的实况，否则紧接着的写前复检必然判 stale。

    2026-10-06 真机第二次翻车：腾动本身改了快照（`source_location` 在里面），复检拿求解那一刻的
    指纹比 → 报"库存或护甲状态已变化，请重新求解"，而"变化"正是我们刚替用户腾的那一下。
    """
    from destiny_mcp.build.models import InventorySnapshot
    from destiny_mcp.services.make_room import make_room_for_build

    def _piece(instance_id: str, name: str, *, location: str) -> Armor:
        armor = _armor(instance_id, name)
        armor.source_location = location
        return armor

    # 计划里那件在**仓库**、角色臂铠格 10/10（含 10 件身上的）→ 必须腾一件出来。
    plan_piece = _piece("plan", "计划那件", location="vault")
    full = InventorySnapshot(gauntlets=[
        plan_piece] + [_piece(f"on{i}", f"身上{i}", location="character") for i in range(10)])
    freed = InventorySnapshot(gauntlets=[plan_piece] + [
        _piece(f"on{i}", f"身上{i}", location="character") for i in range(9)
    ])

    class _Inventory:
        def __init__(self) -> None:
            self.n = 0

        async def get_armor_snapshot(self, player_name: str, character: str) -> InventorySnapshot:
            self.n += 1
            return full if self.n == 1 else freed  # 第一次判格满，之后都是"腾完的样子"

    class _Equipment:
        async def move_single_to_vault(self, player_name: str, armor: Armor) -> None:
            return None

    class _ManifestStub:
        def get_bucket_definition(self, _hash: int) -> dict:
            return {"itemCount": 10, "displayProperties": {"name": "臂铠"}}

    class _Plan:
        """只需要 `items` 与 `model_copy` —— 不为这一条测试去拼整个 CanonicalBuild。"""

        def __init__(self) -> None:
            self.class_type = "warlock"
            self.snapshot_version = "v-solve-time"
            self.execution_id = "exec-1"
            self.items = [type("I", (), {"item_instance_id": "plan", "slot": "gauntlets"})()]

        def model_copy(self, update: dict):
            clone = _Plan()
            clone.snapshot_version = update["snapshot_version"]
            return clone

    class _Store:
        def __init__(self) -> None:
            self.registered: list[str] = []

        def register(self, build, player_name: str) -> None:
            self.registered.append(build.snapshot_version)

    store = _Store()
    steps, prefix = await make_room_for_build(
        player_name="Tester#1234", character="warlock", inventory=_Inventory(),
        equipment=_Equipment(), manifest=_ManifestStub(), build=_Plan(), candidates=store,
    )

    assert prefix, "这个用例的前提是确实腾了一件"
    assert store.registered == [snapshot_version(freed)], (
        "腾完必须把候选基线推进到**腾完之后**那份快照；否则复检拿旧指纹比 → 报 stale"
    )
    assert store.registered[0] != "v-solve-time"


@pytest.mark.asyncio
async def test_a_blocked_piece_frees_exactly_one_slot() -> None:
    """一个卡住的件只腾**一格**就够 —— 多腾是白搬（真机上等于白丢几个格子）。

    2026-10-06 真机第四次翻车：`limit` 用了默认 3，一次格满搬走三件，用户白丢三个格子。
    """
    from destiny_mcp.build.models import InventorySnapshot
    from destiny_mcp.services.make_room import make_room_for_build

    def _piece(instance_id: str, name: str, *, location: str) -> Armor:
        armor = _armor(instance_id, name, power=10)
        armor.source_location = location
        return armor

    def _snap(on_body: int) -> InventorySnapshot:
        return InventorySnapshot(gauntlets=[
            _piece("plan", "计划那件", location="vault"),
            *[_piece(f"on{i}", f"身上{i}", location="character") for i in range(on_body)],
        ])

    class _Inventory:
        def __init__(self) -> None:
            self.n = 0

        async def get_armor_snapshot(self, player_name: str, character: str) -> InventorySnapshot:
            self.n += 1
            return _snap(10) if self.n == 1 else _snap(9)

    class _Equipment:
        def __init__(self) -> None:
            self.moved: list[str] = []

        async def move_single_to_vault(self, player_name: str, armor: Armor) -> None:
            self.moved.append(armor.item_instance_id)

    class _ManifestStub:
        def get_bucket_definition(self, _hash: int) -> dict:
            return {"itemCount": 10, "displayProperties": {"name": "臂铠"}}

    class _Plan:
        class_type = "warlock"
        snapshot_version = "v"
        execution_id = "e"
        items = [type("I", (), {"item_instance_id": "plan", "slot": "gauntlets"})()]

        def model_copy(self, update: dict):
            return self

    equipment = _Equipment()
    steps, prefix = await make_room_for_build(
        player_name="Tester#1234", character="warlock", inventory=_Inventory(),
        equipment=equipment, manifest=_ManifestStub(), build=_Plan(),
    )

    assert prefix, "该腾的时候要腾"
    assert len(equipment.moved) == 1, f"该只腾一件，实际 {len(equipment.moved)} 件"


# ── P2：撞上上游格满 → 按回执点名的件腾一格 → 重试一次 ──────────────────────

_NO_ROOM_DETAIL = (
    "'光泽袖甲' 装备失败: 操作失败：Transfer vault→character。Internalservererror: (\n"
    "  http_status: 500,\n  message: There are no item slots available to transfer this item.,\n"
    "  error_status: DestinyNoRoomInDestination,"
)


def _failed(detail: str) -> LoadoutOperationResult:
    from destiny_mcp.models import MoveItemStep

    return LoadoutOperationResult(
        success=False, loadout_name="Exact", message="执行失败，已恢复执行前状态。",
        steps=[MoveItemStep(action="error", detail=detail, success=False)],
    )


def test_only_no_room_failures_are_treated_as_make_room_cases() -> None:
    """只有**上游真的回格满**（回执里带那句/那个错误码）才算腾格场景 —— 别的失败不许去搬东西。"""
    from destiny_mcp.services.make_room import no_room_failed_item_names

    assert no_room_failed_item_names(_failed(_NO_ROOM_DETAIL)) == {"光泽袖甲"}
    assert no_room_failed_item_names(_failed("'某件' 装备失败: 网络超时")) == set()
    # 成功的那条不算
    from destiny_mcp.models import LoadoutOperationResult, MoveItemStep

    ok = LoadoutOperationResult(success=True, loadout_name="Exact", steps=[
        MoveItemStep(action="transfer", detail=_NO_ROOM_DETAIL, success=True)])
    assert no_room_failed_item_names(ok) == set()


@pytest.mark.asyncio
async def test_a_full_slot_failure_is_retried_once_after_making_room() -> None:
    """第一次撞格满 → 按点名腾一格 → **重试一次**；回执里要写明"第一次撞了、已重试"。"""
    from destiny_mcp.models import LoadoutOperationResult
    from destiny_mcp.services.make_room import equip_with_make_room_retry

    attempts: list[int] = []
    asked: list[set[str]] = []

    async def attempt():
        attempts.append(1)
        if len(attempts) == 1:
            return _failed(_NO_ROOM_DETAIL)
        return LoadoutOperationResult(success=True, loadout_name="Exact", message="已装备，回读核对通过。")

    async def make_room(**kwargs):
        asked.append(kwargs["names"])
        return ["腾格：'某个备用的' → 仓库（臂铠）"], "已自动腾出某个备用的（搬到仓库）。"

    result, steps, note = await equip_with_make_room_retry(
        attempt=attempt, make_room=make_room, room_args={})

    assert len(attempts) == 2, "该重试一次"
    assert asked == [{"光泽袖甲"}], "只给回执点名的那件腾地方"
    assert result.success and len(steps) == 1
    assert "已重试一次" in note and "格子满" in note


@pytest.mark.asyncio
async def test_a_full_slot_failure_without_anything_to_move_does_not_retry() -> None:
    """腾不出来 → **不重试**、把第一次的失败原样交回去（不许硬写第二次）。"""
    from destiny_mcp.services.make_room import equip_with_make_room_retry

    attempts: list[int] = []

    async def attempt():
        attempts.append(1)
        return _failed(_NO_ROOM_DETAIL)

    async def make_room(**kwargs):
        return [], "这个格子里没有可腾的件"

    result, steps, note = await equip_with_make_room_retry(
        attempt=attempt, make_room=make_room, room_args={})

    assert len(attempts) == 1, "腾不出来就不许再试"
    assert not result.success and steps == []
    assert "没有可腾的件" in note
