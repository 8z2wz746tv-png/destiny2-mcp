"""六维阶梯：无解时给差距、上限与台阶，而且不自动降目标。"""

from __future__ import annotations

from typing import Any

from types import SimpleNamespace

import pytest

from destiny_mcp.tools import _armor_ladder as ladder


class _Request:
    """最小 BuildRequest 替身：只要工具读得到的字段。"""

    def __init__(self, **kwargs):
        self.character_class = "hunter"
        self.weapons_target = None
        self.health_target = None
        self.class_target = None
        self.grenade_target = None
        self.melee_target = None
        self.super_target = None
        self.priority_stats = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    def model_copy(self, update=None):
        clone = _Request()
        clone.__dict__.update(self.__dict__)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


def test_priority_order_follows_targets_then_declared_order() -> None:
    request = _Request(weapons_target=150, class_target=100, grenade_target=70,
                       priority_stats=["weapons", "class_stat"])

    assert ladder._priority_order(request)[:3] == ["weapons", "class_stat", "grenade"]


def test_sample_orders_rotate_the_lowest_priority_target_first() -> None:
    request = _Request(weapons_target=150, class_target=100, grenade_target=70,
                       priority_stats=["weapons", "class_stat", "grenade"])

    orders = ladder.sample_orders(request, max_orders=3)

    assert orders[0] == ["weapons", "class_stat", "grenade", "health", "melee", "super_stat"]
    assert orders[1][0] == "grenade", "先翻最低优先的那一项"
    assert len(orders) == 3


def test_ladder_reports_shortfall_and_suggests_dropping_the_lowest_priority() -> None:
    request = _Request(weapons_target=150, class_target=100, grenade_target=70, melee_target=70,
                       priority_stats=["weapons", "class_stat", "grenade", "melee"])
    ceiling = {"weapons": 200, "class_stat": 130, "super_stat": 100, "grenade": 41, "melee": 26,
               "health": 6}

    table = ladder.build_ladder(request, ceiling=ceiling, precision="exact", reason="no_solution")

    assert table["shortfall"] == {"grenade": 29, "melee": 44}
    assert set(table["met"]) == {"weapons", "class_stat"}
    assert table["suggestion"]["drop"] == "melee", "先从优先级最低的未达标项开始降"
    assert table["suggestion"]["from"] == 70 and table["suggestion"]["to"] == 60
    assert table["suggestion"]["argument"] == "melee_target"
    assert "不能自动降" in table["suggestion"]["why"]


def test_ladder_does_not_invent_a_suggestion_when_everything_is_met() -> None:
    request = _Request(weapons_target=150, priority_stats=["weapons"])
    table = ladder.build_ladder(request, ceiling={"weapons": 200}, precision="exact")

    assert table["shortfall"] == {}
    assert table["suggestion"] is None


def test_sampled_precision_is_flagged_in_the_note() -> None:
    request = _Request(weapons_target=150)
    table = ladder.build_ladder(request, ceiling={"weapons": 120}, precision="sampled")

    assert "实采" in table["note"], "采样的上限必须说清是采样，不是精确值"
    assert table["shortfall"] == {"weapons": 30}


def test_relaxation_probes_escalate_from_original_to_top_priority_only() -> None:
    request = _Request(weapons_target=150, class_target=100, grenade_target=70, melee_target=70,
                       priority_stats=["weapons", "class_stat", "grenade", "melee"])

    probes = ladder.relaxation_probes(request)

    assert probes[0]["drop"] == []
    assert probes[1]["drop"] == ["melee"], "先放优先级最低的那一项"
    assert probes[2]["drop"] == ["melee", "grenade"]
    assert "melee_target" in ladder.apply_drops(request, ["melee"]).__dict__
    assert ladder.apply_drops(request, ["melee"]).melee_target is None
    assert request.melee_target == 70, "探测用的必须是副本，原请求不能被改"


@pytest.mark.asyncio
async def test_ladder_finds_the_smallest_relaxation_that_works() -> None:
    """目标是互斥的：只换优先级永远是 0，放下最低优先那项才能解出来。"""
    analysis = SimpleNamespace(max_possible={"weapons": 200}, precision="exact", reason="no_solution")
    seen: list[Any] = []

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            seen.append(request.melee_target)
            if request.melee_target is None:
                return [SimpleNamespace(build=SimpleNamespace(weapons=200, grenade=41, melee=26))]
            return []

    table = await ladder.no_solution_ladder(
        {"build_svc": _Build()}, "Tester#1234",
        _Request(weapons_target=150, grenade_target=70, melee_target=70,
                 priority_stats=["weapons", "grenade", "melee"]),
    )

    assert seen[:2] == [70, 70], "原样那一档要真的试过（含换优先级）"
    assert table["precision"] == "sampled"
    assert table["ceiling"] == {"weapons": 200, "grenade": 41, "melee": 26}
    assert table["shortfall"] == {"grenade": 29, "melee": 44}
    assert table["suggestion"]["drop"] == ["melee"]
    assert "melee" in table["suggestion"]["why"] or "近战" in table["suggestion"]["why"]
    assert [trial["ok"] for trial in table["trials"]][:2] == [False, True]


@pytest.mark.asyncio
async def test_single_stat_maximum_is_not_used_as_the_simultaneous_ceiling() -> None:
    """`analyze` 的 max_possible 是单项上限：不能当成"同时能达到"（实机踩过）。"""
    analysis = SimpleNamespace(
        max_possible={"weapons": 200, "melee": 150}, precision="exact", reason="no_solution"
    )

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            # 同一套约束下按当前优先级：武器能到 200，但近战只有 26
            return [SimpleNamespace(build=SimpleNamespace(weapons=200, melee=26))]

    table = await ladder.no_solution_ladder(
        {"build_svc": _Build()}, "Tester#1234",
        _Request(weapons_target=150, melee_target=70, priority_stats=["weapons", "melee"]),
    )

    assert table["single_stat_ceiling"] == {"weapons": 200, "melee": 150}
    assert table["ceiling"] == {"weapons": 200, "melee": 26}, "同时能达到的只有实采值"
    assert table["shortfall"] == {"melee": 44}
    # 注意：这个替身对任何目标都返回同一个解，所以"原样"那一档就成功了 ——
    # 阶梯此时给的是"需要放下哪些目标"的空列表，而不是编一个降级建议。
    assert table["suggestion"]["drop"] == []
    assert "不是" in table["single_stat_note"]




@pytest.mark.asyncio
async def test_completion_rate_becomes_na_without_hard_targets() -> None:
    """只给优先级时 completion_rate 没有意义：标 None + 说明，而不是 0.0。"""
    from destiny_mcp.tools import _build_flow

    class _Build:
        async def recommend_build(self, player_name, request):
            return {"results": [{"score": 50.3, "completion_rate": 0.0,
                                 "build": {"items": [{"slot": "helmets"}]}}]}

    svc = {"build_svc": _Build()}
    response = await _build_flow.recommend(
        svc, "Tester#1234", _Request(priority_stats=["weapons"]), {"character": "hunter"},
        lambda value: value,
    )

    result = response["data"]["recommendation"]["results"][0]
    assert result["completion_rate"] is None
    assert "没有硬目标" in result["completion_rate_note"]


@pytest.mark.asyncio
async def test_completion_rate_stays_when_targets_are_given() -> None:
    from destiny_mcp.tools import _build_flow

    class _Build:
        async def recommend_build(self, player_name, request):
            return {"results": [{"score": 51.2, "completion_rate": 1.0, "build": {}}]}

    response = await _build_flow.recommend(
        {"build_svc": _Build()}, "Tester#1234", _Request(weapons_target=150),
        {"character": "hunter"}, lambda value: value,
    )

    assert response["data"]["recommendation"]["results"][0]["completion_rate"] == 1.0


def test_tuning_first_rung_comes_before_dropping_targets() -> None:
    """没有额度数据时：缺口 ≤5 给"调谐能补"的提示，≤10 给属性模组，更大只剩降目标。"""
    request = _Request(weapons_target=150, grenade_target=70, melee_target=60)
    ceiling = {"weapons": 145, "grenade": 61, "melee": 20}

    table = ladder.build_ladder(request, ceiling=ceiling, precision="sampled")

    levers = {item["stat"]: item["lever"] for item in table["tuning_first"]}
    assert levers == {"weapons": "tuning", "grenade": "stat_mod"}
    assert "melee" not in levers, "差 40 点不是调谐/模组能补的，别给出误导性提示"
    assert any("调谐" in item["why"] for item in table["tuning_first"])
    # 没有额度数据时必须自认是"杠杆提示"，不能假装已经算进 ceiling
    assert "人工可用的杠杆提示" in table["tuning_first_note"]
    assert table["tuning_attempted"] is False


def test_tuning_rung_uses_headroom_evidence_when_available() -> None:
    """有额度数据时：说清"试过了、每项最多补多少、为什么补不上"。"""
    request = _Request(grenade_target=70, melee_target=60)
    ceiling = {"grenade": 65, "melee": 20}
    tuning = {"per_stat_max_gain": {"grenade": 25, "melee": 25}, "allowance": {}}

    table = ladder.build_ladder(
        request, ceiling=ceiling, precision="sampled", tuning=tuning
    )

    rung = next(item for item in table["tuning_first"] if item["stat"] == "grenade")
    assert rung["lever"] == "tuning"
    assert rung["headroom"] == 25
    assert rung["solver_attempted"] is True
    assert "已经试过调谐" in rung["why"]
    assert "已经试过调谐" in table["tuning_first_note"]
    assert table["tuning_headroom"]["grenade"] == 25


def test_tuning_rung_says_so_when_armor_cannot_be_tuned() -> None:
    """额度是 0（legacy 护甲没有调谐槽）时不能劝人去改调谐。"""
    request = _Request(grenade_target=70)
    tuning = {"per_stat_max_gain": {}, "allowance": {"grenade": 0}}

    table = ladder.build_ladder(
        request, ceiling={"grenade": 65}, precision="sampled", tuning=tuning
    )

    rung = table["tuning_first"][0]
    assert rung["lever"] == "no_tuning"
    assert "没有可用的调谐槽" in rung["why"]


def test_partial_headroom_is_reported_as_insufficient() -> None:
    """额度只够补一部分（差 5、最多补 3）时，别劝人白折腾。"""
    request = _Request(grenade_target=70)
    tuning = {"per_stat_max_gain": {"grenade": 3}, "allowance": {"grenade": 3}}

    table = ladder.build_ladder(
        request, ceiling={"grenade": 65}, precision="sampled", tuning=tuning
    )

    rung = table["tuning_first"][0]
    assert rung["lever"] == "no_tuning"
    assert "最多只补 3 点" in rung["why"]


def test_tuning_first_is_absent_when_everything_is_met() -> None:
    request = _Request(weapons_target=100)
    table = ladder.build_ladder(request, ceiling={"weapons": 200}, precision="exact")

    assert table["tuning_first"] == []
    assert table["tuning_first_note"] == ""


@pytest.mark.asyncio
async def test_too_large_requests_are_refused_with_narrowing_advice() -> None:
    """规模超限是"没算"，不是"无解"：给收窄建议，而不是让调用方等到超时。"""
    from destiny_mcp.build.analyzer import too_large_reason
    from destiny_mcp.exceptions import BuildTooLargeError
    from destiny_mcp.tools import _build_flow

    reason = too_large_reason(243_400_640, [38, 56, 38, 70, 43], 20_000_000)

    class _Build:
        async def recommend_build(self, player_name, request):
            raise BuildTooLargeError(reason)

        async def find_build(self, player_name, request, coverage=None):
            raise BuildTooLargeError(reason)

    svc = {"build_svc": _Build()}
    response = await _build_flow.recommend(
        svc, "Tester#1234", _Request(priority_stats=["weapons"]), {"character": "warlock"},
        lambda value: value,
    )
    data = response["data"]

    assert response["ok"] is True, "这不是错误，而是「没有计算」"
    assert data["not_computed"]["precision"] == "not_computed"
    assert data["recommendation"]["results"] == [], "不许把「没算」答成「无解」"
    assert any("金装" in action for action in response["next_actions"]), "要给收窄建议"
    assert "ladder" not in data, "没算就别做阶梯（采样也只是再撞一次闸门）"

    found = await _build_flow.find(
        svc, "Tester#1234", _Request(priority_stats=["weapons"]), {"character": "warlock"},
        lambda value: value,
    )
    assert found["ok"] is True and found["data"]["builds"] == []
    assert found["data"]["not_computed"]["reason"].startswith("这次分析的组合规模太大")


class _StubManifest:
    """只回答调谐相关的两个问题：这件有调谐槽吗、这个插件叫什么。"""

    def get_item_definition(self, item_hash):
        from destiny_mcp.build.tuning import EMPTY_TUNING_PLUG_HASH, TUNING_PLUG_SET_HASH

        return {
            "sockets": {
                "socketEntries": [
                    {
                        "reusablePlugSetHash": TUNING_PLUG_SET_HASH,
                        "singleInitialItemHash": EMPTY_TUNING_PLUG_HASH,
                    }
                ]
            }
        }

    def get_item_name(self, plug_hash):
        return ""


def _all_tuning_hashes() -> tuple[int, ...]:
    """夹具里的件**允许装全部调谐**（真实清单来自组件 310，每件只有 6 颗）。"""
    from destiny_mcp.build.tuning import tuning_catalog

    return tuple(choice.plug_hash for choice in tuning_catalog(_StubManifest()))


def _tunable_snapshot():
    from destiny_mcp.build.models import Armor, ArmorStats, InventorySnapshot
    from destiny_mcp.build.constants import STAT_NAMES

    buckets = {}
    for slot in ("helmets", "gauntlets", "chests", "legs", "class_items"):
        buckets[slot] = [
            Armor(
                item_instance_id=f"inst-{slot}",
                item_hash=1,
                name=slot,
                slot=slot,
                stats=ArmorStats(**{name: 10 for name in STAT_NAMES}),
                armor_system="armor_3",
                gear_tier=5,
                tuning_option_hashes=_all_tuning_hashes(),
            )
        ]
    return InventorySnapshot(**buckets)


@pytest.mark.asyncio
async def test_ladder_pulls_tuning_evidence_from_the_inventory_service() -> None:
    """有库存服务时，阶梯要说"已经试过调谐"并给出每项额度，而不是老口径的提示。"""
    analysis = SimpleNamespace(max_possible={}, precision="exact", reason="no_solution")

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            # 放开目标那一档能解出手雷 65（于是原目标 70 差 5 点）
            if request.grenade_target is None:
                return [SimpleNamespace(build=SimpleNamespace(grenade=65))]
            return []

    class _Inventory:
        async def get_armor_snapshot(self, player_name, character_class):
            return _tunable_snapshot()

    table = await ladder.no_solution_ladder(
        {"build_svc": _Build(), "inventory_svc": _Inventory(), "manifest": _StubManifest()},
        "Tester#1234",
        _Request(grenade_target=70),
    )

    assert table["tuning_attempted"] is True
    assert table["tuning_headroom"]["grenade"] == 25
    rung = table["tuning_first"][0]
    assert rung["lever"] == "tuning"
    assert rung["solver_attempted"] is True
    assert "已经试过调谐" in table["tuning_first_note"]


@pytest.mark.asyncio
async def test_ladder_survives_a_failing_inventory_lookup() -> None:
    """拿不到额度数据时退回"杠杆提示"，不能让诊断跟着崩。"""
    analysis = SimpleNamespace(max_possible={}, precision="exact", reason="no_solution")

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            return []

    class _Inventory:
        async def get_armor_snapshot(self, player_name, character_class):
            raise RuntimeError("组件缺失")

    class _BuildWithSample(_Build):
        async def find_build(self, player_name, request, coverage=None):
            if request.grenade_target is None:
                return [SimpleNamespace(build=SimpleNamespace(grenade=65))]
            return []

    table = await ladder.no_solution_ladder(
        {"build_svc": _BuildWithSample(), "inventory_svc": _Inventory(), "manifest": _StubManifest()},
        "Tester#1234",
        _Request(grenade_target=70),
    )

    assert table["tuning_attempted"] is False
    assert table["tuning_first"][0]["lever"] == "tuning"
    assert "人工可用的杠杆提示" in table["tuning_first_note"]


@pytest.mark.asyncio
async def test_find_reports_which_candidates_need_tuning() -> None:
    """要靠调谐才达标的方案必须在响应里点名，并说清改动在哪。"""
    from destiny_mcp.tools import _build_flow

    change = {
        "item_instance_id": "inst-legs",
        "item_name": "测试腿甲",
        "slot": "legs",
        "from": {"hash": 1, "name": "空调整模组插槽"},
        "to": {"hash": 1922571986, "name": "+手雷 / -职业"},
        "delta": {"grenade": 5, "class_stat": -5},
    }

    class _Build:
        async def find_build(self, player_name, request, coverage=None):
            return [
                {
                    "score": 50.0,
                    "completion_rate": 1.0,
                    "build": {"items": [{"slot": "legs"}]},
                    "tuning_changes": [change],
                    "requires_tuning": True,
                    "tuning_note": "这套方案要先把 1 件护甲的调谐改掉才能达标。",
                }
            ]

    response = await _build_flow.find(
        {"build_svc": _Build()}, "Tester#1234", _Request(grenade_target=70),
        {"character": "hunter"}, lambda value: value,
    )
    data = response["data"]

    assert data["tuning"]["build_count"] == 1
    assert data["tuning"]["change_count"] == 1
    assert data["tuning"]["changes"][0]["to"]["name"] == "+手雷 / -职业"
    assert "要先改调谐" in response["summary"]
    assert data["builds"][0]["tuning_changes"] == [change]


@pytest.mark.asyncio
async def test_find_hides_the_tuning_block_when_nothing_needs_it() -> None:
    from destiny_mcp.tools import _build_flow

    class _Build:
        async def find_build(self, player_name, request, coverage=None):
            return [
                {
                    "score": 50.0,
                    "completion_rate": 1.0,
                    "build": {"items": []},
                    "tuning_changes": [],
                    "requires_tuning": False,
                    "tuning_note": "",
                }
            ]

    response = await _build_flow.find(
        {"build_svc": _Build()}, "Tester#1234", _Request(grenade_target=70),
        {"character": "hunter"}, lambda value: value,
    )

    assert "tuning" not in response["data"]
    assert response["summary"] == "找到 1 个候选配装。"


@pytest.mark.asyncio
async def test_ladder_says_when_the_original_targets_are_impossible() -> None:
    """「原样」实测 0 候选时必须给 verdict，别让 ceiling 的逐项最大值看起来像"其实能满足"。"""
    analysis = SimpleNamespace(max_possible={}, precision="exact", reason="no_solution")

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            return []

    table = await ladder.no_solution_ladder(
        {"build_svc": _Build()}, "Tester#1234", _Request(grenade_target=70)
    )

    assert table["verdict"]["satisfiable"] is False
    assert "0 候选" in table["verdict"]["evidence"]
    assert "逐项" in table["verdict"]["note"]
    assert table["tuning_first"] == []


@pytest.mark.asyncio
async def test_ladder_reports_insufficient_tuning_headroom_instead_of_silence() -> None:
    """缺口比调谐额度大：也要有一档说明"补不上"，不能什么都不给。"""
    analysis = SimpleNamespace(max_possible={}, precision="exact", reason="no_solution")

    class _Build:
        async def analyze_build(self, player_name, request):
            return analysis

        async def find_build(self, player_name, request, coverage=None):
            if request.grenade_target is None:
                return [SimpleNamespace(build=SimpleNamespace(grenade=20))]
            return []

    class _Inventory:
        async def get_armor_snapshot(self, player_name, character_class):
            return _tunable_snapshot()

    table = await ladder.no_solution_ladder(
        {"build_svc": _Build(), "inventory_svc": _Inventory(), "manifest": _StubManifest()},
        "Tester#1234",
        _Request(grenade_target=70),
    )

    rung = table["tuning_first"][0]
    assert rung["lever"] == "tuning_insufficient"
    assert "补不上这一档" in rung["why"]
