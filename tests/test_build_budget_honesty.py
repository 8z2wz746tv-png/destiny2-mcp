"""P1：预算/截断**永远不能**产生"不可行"的结论。

本仓库栽过同类跟头：`analyze` 曾在没验证过的情况下断言"没有合法组合"，而同一组约束
`recommend` 能配出 100% 达标的方案（0.1.13 修）。这次把纪律钉在类型与守门上：

- `SearchCoverage.exhaustive` 是唯一能说"枚举完了、没有满足下限的方案"的凭据；
- 没搜完（预算用尽）→ `satisfiable` 必须是 `None`、话术必须说"没算完"；
- 堆截断（top-N 容量）**不许**把唯一一套满足下限的方案挤掉 —— 今天靠
  `validate_fixed_process_items` 把"补不上下限"的组合挡在堆外实现，这条守门就是钉住它。

第二条不是假想：构造 3125 套组合、其中 3124 套"优先级高但永远达不到下限"、
唯一达标的那套在优先级上垫底，堆容量只有 200。如果哪天有人把"能否满足下限"
从"进堆前的校验"挪到"进堆后的排序"，这条会立刻变红。
"""

from __future__ import annotations

import pytest

from destiny_mcp.build.models import Armor, BuildConstraints, InventorySnapshot
from destiny_mcp.build.process_types import ProcessResult, SearchCoverage
from destiny_mcp.build.solver import RETURNED_ARMOR_SETS, solve

STAT_NAMES = ["weapons", "health", "class_stat", "grenade", "super_stat", "melee"]


# ── 堆截断不许制造"假无解" ──────────────────────────────────────────────


def _piece(index: int, weapons: int, grenade: int, *, energy: int = 0) -> Armor:
    return Armor(
        item_instance_id=f"probe-{index}",
        item_hash=1000 + index,
        name=f"probe{index}",
        slot="helmet",
        stats={
            name: (weapons if name == "weapons" else grenade if name == "grenade" else 0)
            for name in STAT_NAMES
        },
        energy_capacity=energy,
        tier=5,
        gear_tier=5,
        armor_system="armor_3",
    )


def _one_satisfying_among_3124_decoys() -> InventorySnapshot:
    """每槽 1 件武器件（8 点）+ 4 件手雷件（30 点）；武器下限 40 = 必须五件全用武器件。

    不达标那 3124 套的缺口是 40 ≤ `max_mod_bonus`(50)，所以**过得了早剪**、会进堆竞争，
    而它们在"手雷优先"的排序里全部高于唯一达标那套（手雷 0）。
    """
    def slot(offset: int) -> list[Armor]:
        return [_piece(offset, 8, 0)] + [
            _piece(offset + i + 1, 0, 30) for i in range(4)
        ]

    return InventorySnapshot(
        helmets=slot(0), gauntlets=slot(10), chests=slot(20),
        legs=slot(30), class_items=slot(40),
    )


def test_a_satisfying_set_survives_the_top_n_heap() -> None:
    snapshot = _one_satisfying_among_3124_decoys()
    assert RETURNED_ARMOR_SETS < 5**5 - 1, "这条用例要的就是堆装不下所有组合"

    result = solve(snapshot, BuildConstraints(weapons_min=40, priority_stat_indices=[3]))

    assert len(result.sets) == 1, "唯一满足下限的那套被堆挤掉了 = 假无解"
    assert result.sets[0].stats[0] == 40
    assert result.coverage.exhaustive is True


def test_the_same_snapshot_is_honestly_empty_without_a_reachable_target() -> None:
    """对照组：把下限抬到模组补不上的高度，就应该如实返回 0 套 + exhaustive=True。"""
    snapshot = _one_satisfying_among_3124_decoys()

    result = solve(snapshot, BuildConstraints(weapons_min=200, priority_stat_indices=[3]))

    assert result.sets == []
    assert result.coverage.exhaustive is True, "空结果必须自证'枚举完了'"
    assert result.combos == 5**5


# ── 覆盖率的形状：没搜完 ≠ 不可行 ───────────────────────────────────────


def test_search_coverage_serialises_truncation_as_a_reason() -> None:
    assert SearchCoverage(exhaustive=True, combos=7).to_dict() == {
        "exhaustive": True, "combos": 7, "truncated_by": None,
    }
    truncated = SearchCoverage(exhaustive=False, combos=7, truncated_by="budget").to_dict()
    assert truncated["exhaustive"] is False
    assert truncated["truncated_by"] == "budget", "说没搜完就得说清被什么截断"


def test_process_result_defaults_to_exhaustive() -> None:
    """默认必须是"搜完了"：将来加配额的人必须**显式**把它置 False，不能默默继承。"""
    assert ProcessResult().coverage.exhaustive is True
    assert ProcessResult(complete=False, truncated_by="quota").coverage.to_dict() == {
        "exhaustive": False, "combos": 0, "truncated_by": "quota",
    }


# ── 0 候选的话术与 verdict ───────────────────────────────────────────────


def test_empty_result_message_certifies_the_exhaustive_search() -> None:
    from destiny_mcp.tools._build_flow import _empty_message

    message = _empty_message({"exhaustive": True, "combos": 3125})

    assert "枚举完了" in message
    assert "3,125" in message


def test_truncated_search_never_claims_there_is_no_solution() -> None:
    from destiny_mcp.tools._build_flow import _empty_message

    message = _empty_message({"exhaustive": False, "truncated_by": "budget"})

    assert "没有搜完" in message
    assert "budget" in message


@pytest.mark.asyncio
async def test_ladder_verdict_is_none_when_the_search_did_not_finish() -> None:
    """`satisfiable=false` 只在真穷尽时出现；没搜完时必须是 None。"""
    from destiny_mcp.tools import _armor_ladder

    class _NoSolutions:
        async def analyze_build(self, player_name, request):
            return type("A", (), {"max_possible": {}, "reason": "", "precision": "not_computed"})()

        async def find_build(self, player_name, request, coverage=None):
            return []

    from destiny_mcp.build.models import BuildRequest

    svc = {"build_svc": _NoSolutions()}
    request = BuildRequest(character_class="hunter", weapons_target=150)

    truncated = await _armor_ladder.no_solution_ladder(
        svc, "p", request, max_probes=1,
        coverage={"exhaustive": False, "combos": 10, "truncated_by": "budget"},
    )
    exhausted = await _armor_ladder.no_solution_ladder(
        svc, "p", request, max_probes=1,
        coverage={"exhaustive": True, "combos": 10, "truncated_by": None},
    )

    assert truncated["verdict"]["satisfiable"] is None, "没搜完不许断言不可行"
    assert "没搜完" in truncated["verdict"]["evidence"]
    assert exhausted["verdict"]["satisfiable"] is False
