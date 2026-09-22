"""P4：达标之后把**免费的调谐额度**吃干净（0 能量、+5/−5）。

以前的做法是"调谐只在目标达不到时当补救用"（`plan_tuning` 穷举 + 逐套复核，真机实测
一条请求 200+ 秒），于是"手雷 120 其实能到 130"这种免费的 10 点没人去拿。

现在分两条路，判据相同（`validate_fixed_process_items`：按真实目标重排属性模组再比六维）：

- **便宜路**（有解时走）：单件邻域贪心，池子大小 × 单件选项，亚秒级；
- **补救路**（严格解为空时走）：放宽目标复解 + 穷举复核，贵。

守的是三条：
① 不变量 —— 返回的解**不存在**单件调谐替换能再改进；
② 每一步都不许破坏任何约束（下限、上限、套装/异域都算）；
③ 对外只报**净改动**（贪心的中间步骤不是用户要看的）。

**注入验证的边界**（别以为每条都有单点守门）：

- 有单点守门的：① 不变量的基线必须是"当前状态"而不是原始状态（注掉就红）、
  ③ 净改动（注成"跳过所有净改动"就红）、以及"什么都不要求时不动调谐"。
- **② 每一步都过权威复核是结构性保证**：这个函数里只有一条求值路径
  （`validate_fixed_process_items`），没有"拍一个近似值"的分支可注 ——
  所以它靠的是"没有第二条路"，不是某一行断言。真要变坏，得有人新写一条求值路径，
  那时应该加的是"禁止再抄一套求值"的扫描守门，而不是在这条用例里硬凑。
"""

from __future__ import annotations

from typing import Any

from destiny_mcp.build.models import (
    Armor,
    ArmorStats,
    BuildConstraints,
    InventorySnapshot,
    StatModDefinition,
    NAME_TO_STAT_HASH,
)
from destiny_mcp.build.constants import STAT_NAMES
from destiny_mcp.build.ranking import goodness_key
from destiny_mcp.build.solver import prepare_fixed_set_context, solve
from destiny_mcp.build.tuning import (
    armor_with_tuning,
    local_tuning_improvement,
    piece_tuning,
    tuning_catalog,
)

SLOTS = ("helmets", "gauntlets", "chests", "legs", "class_items")
GRENADE = STAT_NAMES.index("grenade")
MELEE = STAT_NAMES.index("melee")
HEALTH = STAT_NAMES.index("health")


class _FakeManifest:
    """只回六维最全的那件"假护甲定义"，让 `piece_tuning` 认为它有调谐槽。

    调谐目录（`tuning_catalog`）本身是**纯表**，不查 manifest —— 所以替身够用。
    """

    def get_item_definition(self, item_hash: int) -> dict[str, Any]:
        return {"sockets": {"socketEntries": [
            {"socketTypeHash": 2581339086, "reusablePlugSetHash": 1155052024}
        ]}}


def _armor(slot: str, stats: dict[str, int], *, tuning: int | None = None) -> Armor:
    return Armor(
        item_instance_id=f"p-{slot}",
        item_hash=hash(slot) % 1000,
        name=slot,
        slot=slot,
        stats=ArmorStats(**stats),
        energy_capacity=10,
        armor_system="armor_3",
        gear_tier=5,
        tuning_mod_hash=tuning,
    )


def _snapshot(armors: list[Armor]) -> InventorySnapshot:
    buckets: dict[str, list[Armor]] = {slot: [] for slot in SLOTS}
    for armor in armors:
        buckets[armor.slot].append(armor)
    return InventorySnapshot(
        **buckets,
        stat_mod_definitions=[StatModDefinition(
            hash=7001, stat_hash=NAME_TO_STAT_HASH["weapons"],
            value=10, energy_cost=3, kind="general",
        )],
    )


def _five(**stats: int) -> list[Armor]:
    return [_armor(slot, stats) for slot in SLOTS]


def _catalog_hash_for(increased: str, decreased: str) -> int:
    for choice in tuning_catalog(_FakeManifest()):
        if choice.increased == increased and choice.decreased == decreased:
            return choice.plug_hash
    raise AssertionError(f"目录里没有 +{increased}/-{decreased}")


def _key(stats: list[int], constraints: BuildConstraints) -> tuple[int, ...]:
    return goodness_key(stats, constraints)


def _final_stats(armor_set: Any) -> list[int]:
    return [
        armor_set.stats[index] + armor_set.bonus_stats[index] for index in range(6)
    ]


# ── 不变量：吃干净之后，没有单件替换能再改进 ─────────────────────────────


def test_no_single_piece_tuning_can_improve_the_result_any_further() -> None:
    """这条就是"吃干净"的定义：返回的解在单件邻域里已经是局部最优。"""
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=19, melee=20, health=20))
    constraints = BuildConstraints(grenade_min=95, melee_min=100)
    solved = solve(snapshot, constraints)
    assert solved.sets, "这套要能解出来，否则验不到「局部最优」"

    context = prepare_fixed_set_context(snapshot, constraints)
    improved_set, plan = local_tuning_improvement(solved.sets[0], context, manifest)
    assert plan.changes, "免费额度没吃：这套明明还能靠调谐涨手雷"
    # 贪心必须是"没有改进了"停的，不能是"步数上限到了"停的 —— 这条断言就是被真事咬出来的：
    # 上限还是 12 的时候它在这里变红（第 13 步本来还能用平衡调整再涨 3 点）。
    assert plan.exhausted is False, "碰了步数上限：返回的这套算不上局部最优"

    after = _final_stats(improved_set)
    before = _final_stats(solved.sets[0])
    assert _key(after, constraints) > _key(before, constraints)

    # 不变量：任何**单件**再换调谐，都不许比现在更好
    for index, armor in enumerate(improved_set.armor):
        piece = piece_tuning(armor, manifest)
        assert piece is not None
        for choice in piece.options:
            if choice.is_noop or choice.plug_hash == piece.current.plug_hash:
                continue
            candidate = list(improved_set.armor)
            candidate[index] = armor_with_tuning(armor, piece, choice)
            from destiny_mcp.build.process_types import armor_to_process_item
            from destiny_mcp.build.solver import validate_fixed_process_items

            verified = validate_fixed_process_items(
                [armor_to_process_item(row) for row in candidate], context
            )
            if verified is None:
                continue
            assert verified.rank_key <= improved_set.rank_key, (
                f"第 {index} 件换成 {choice.name} 还能更好 —— 说明没吃干净"
            )


def test_hitting_the_step_cap_is_reported_not_hidden() -> None:
    """跑到步数上限时如实标出来（上限只是成本护栏，不是"已经最优"的意思）。

    这条是可注入的：把上限压到 1，标记必须翻成 True；默认上限下必须是 False。
    """
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=19, melee=20, health=20))
    constraints = BuildConstraints(grenade_min=95, melee_min=100)
    solved = solve(snapshot, constraints)
    context = prepare_fixed_set_context(snapshot, constraints)

    _, tight = local_tuning_improvement(solved.sets[0], context, manifest, max_steps=1)
    assert tight.exhausted is True

    _, loose = local_tuning_improvement(solved.sets[0], context, manifest)
    assert loose.exhausted is False


def test_local_tuning_never_breaks_a_constraint() -> None:
    """每一步都过权威复核：不许为了涨手雷把近战或超能掉破。"""
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=19, melee=20, health=20))
    constraints = BuildConstraints(grenade_min=95, melee_min=100, super_stat_max=100)
    solved = solve(snapshot, constraints)
    context = prepare_fixed_set_context(snapshot, constraints)

    improved_set, _plan = local_tuning_improvement(solved.sets[0], context, manifest)

    final = _final_stats(improved_set)
    assert final[MELEE] >= 100, "下限不许破"
    assert final[GRENADE] >= 95


def test_changes_are_netted_per_piece() -> None:
    """贪心可能对同一件走好几步，对外只报"从原样改成最终样"。

    真机第一次跑就报了 12 条改动、实际只涉及 5 件 —— 中间步骤不是用户要看的。
    """
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=19, melee=20, health=20))
    constraints = BuildConstraints(grenade_min=95, melee_min=100)
    solved = solve(snapshot, constraints)
    context = prepare_fixed_set_context(snapshot, constraints)

    improved_set, plan = local_tuning_improvement(solved.sets[0], context, manifest)

    assert plan.changes, "这套本来就该有改动，否则这条用例是真空成立"
    touched = [change.item_instance_id for change in plan.changes]
    assert len(touched) == len(set(touched)), f"同一件报了多条：{touched}"
    assert len(touched) <= len(SLOTS)
    for change in plan.changes:
        assert change.from_plug != change.to_plug, "净改动不该出现「换成自己」"


def test_no_free_lunch_leaves_the_set_untouched() -> None:
    """已经很优的解：不动它，并如实说"没有可拿的"。"""
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=30, melee=30, health=30))
    constraints = BuildConstraints()  # 什么目标都没有 → 调谐无从"改进"
    solved = solve(snapshot, constraints)
    context = prepare_fixed_set_context(snapshot, constraints)

    improved_set, plan = local_tuning_improvement(solved.sets[0], context, manifest)

    assert plan.changes == ()
    assert improved_set is solved.sets[0], "没改动就该原样返回同一个对象"
    assert "吃满" in plan.reason or "没有" in plan.reason


def test_extra_energy_is_not_left_on_the_table_when_it_cannot_be_used() -> None:
    """拿不到就是拿不到：所有件都到上限时不许硬凑改动。"""
    manifest = _FakeManifest()
    snapshot = _snapshot(_five(grenade=40, melee=40, health=40))
    constraints = BuildConstraints(grenade_min=100, stat_caps={"grenade": 200})
    solved = solve(snapshot, constraints)
    context = prepare_fixed_set_context(snapshot, constraints)

    _improved_set, plan = local_tuning_improvement(solved.sets[0], context, manifest)

    assert all(
        change.to_plug != change.from_plug for change in plan.changes
    ), "报出来的每条都得是真改动"


# ── 与补救路径的分工：便宜路不许被当成"全局最优的证明" ──────────────────


def test_local_search_reports_its_own_boundary() -> None:
    """`local_tuning_improvement` 的注释里必须留着"局部 ≠ 全局"这句。

    它不是形式主义：d2-armor-solver 对同类算法写过同一句，而我们真机上
    `reachable` 报 120、把下限写成 130 时确实能到 130 —— 局部搜索的边界是真实存在的。
    """
    doc = local_tuning_improvement.__doc__ or ""

    assert "局部" in doc and "全局" in doc
