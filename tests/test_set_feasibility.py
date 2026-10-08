"""套装**凑不齐**的判定（`build/set_feasibility.set_bonus_shortfall`）。

真机 2026-10-06：泰坦要「移民号陨落」4 件套，他能穿的只有 3 个部位（头盔 1 / 腿甲 2 / 臂铠 4，
胸甲与职业护甲各 0）—— 23 件里其余的是**术士的**。工具当时回的是
"各项目标单看都在单项上限之内……"（面向属性的话术），一句正确的废话。

这里的判据是**上界**：每个部位最多穿一件（万能插槽也占一个部位），所以
"有这套件的部位数"就是能凑到的最大件数；**小于**要的件数才是"数学上不可能"。
"""

from __future__ import annotations

from types import SimpleNamespace

from destiny_mcp.build.constants import SOLVER_SLOTS
from destiny_mcp.build.set_feasibility import set_bonus_shortfall

LABELS = {
    "helmets": "头盔",
    "gauntlets": "臂铠",
    "chests": "胸甲",
    "legs": "腿甲",
    "class_items": "职业护甲",
}


class _Armor:
    def __init__(self, *, set_bonus: int | None = None, wildcard: bool = False) -> None:
        self.set_bonus = set_bonus
        self.has_set_bonus_mod_socket = wildcard


class _Snapshot:
    """只带这个判定要用的两面：`execution.label(slot)` 与 `get_slot(slot)`。"""

    def __init__(self, pieces: dict[str, list[_Armor]]) -> None:
        self._pieces = pieces
        self.execution = SimpleNamespace(label=lambda slot: LABELS.get(slot, slot))

    def get_slot(self, slot: str) -> list[_Armor]:
        return self._pieces.get(slot, [])


def _constraints(*, set_hash: int | None = 4242, need: int = 4) -> SimpleNamespace:
    return SimpleNamespace(set_bonus_hash=set_hash, set_bonus_count=need)


def _snapshot_with(covered: set[str], *, wildcard: set[str] = frozenset()) -> _Snapshot:
    return _Snapshot({
        slot: [_Armor(set_bonus=4242)] if slot in covered
        else [_Armor(wildcard=True)] if slot in wildcard
        else []
        for slot in SOLVER_SLOTS
    })


def test_three_slots_cannot_make_a_four_piece_set() -> None:
    """只有 3 个部位有这套 → 凑不出 4 件套，且要说清缺哪几个。"""
    sentence = set_bonus_shortfall(
        _snapshot_with({"helmets", "legs", "gauntlets"}), _constraints(need=4)
    )

    assert sentence, "3 个部位凑 4 件是**数学上不可能**，必须给结论而不是沉默"
    assert "凑不出 4 件" in sentence
    assert "只有 3 个部位" in sentence
    for covered in ("头盔", "腿甲", "臂铠"):
        assert covered in sentence, "要说清**哪几个部位有**"
    for missing in ("胸甲", "职业护甲"):
        assert missing in sentence, "要说清**缺哪几个部位**"
    assert "数出来的上限" in sentence, "这是上界判定，不是猜的 —— 话术要说清这一点"


def test_exactly_enough_slots_makes_no_claim() -> None:
    """刚好够（4 个部位）就**不下结论** —— 出不出得来是属性/金装那些事。"""
    assert set_bonus_shortfall(
        _snapshot_with({"helmets", "legs", "gauntlets", "chests"}), _constraints(need=4)
    ) is None


def test_a_wildcard_socket_counts_toward_the_upper_bound() -> None:
    """万能插槽能顶任意套装一件 —— 3 个部位有货 + 1 个部位有万能插槽 = 上限 4，够了。"""
    short = _snapshot_with({"helmets", "legs"}, wildcard={"gauntlets"})
    assert set_bonus_shortfall(short, _constraints(need=4)), "2+1=3 < 4，仍然凑不齐"
    assert set_bonus_shortfall(
        _snapshot_with({"helmets", "legs"}, wildcard={"gauntlets", "chests"}), _constraints(need=4)
    ) is None, "2+2=4，刚好够 → 不下结论"


def test_no_set_constraint_means_no_claim() -> None:
    """没要求套装、或要 0 件：一个字都不说（别去数一个没人问的东西）。"""
    snapshot = _snapshot_with(set())

    assert set_bonus_shortfall(snapshot, _constraints(set_hash=None, need=4)) is None
    assert set_bonus_shortfall(snapshot, _constraints(need=0)) is None


def test_a_piece_counted_twice_does_not_fake_coverage() -> None:
    """同一部位有 3 件这套也**只算 1 件**（一个部位只能穿一件）—— 上界不许被件数撑大。"""
    snapshot = _Snapshot({
        slot: [_Armor(set_bonus=4242), _Armor(set_bonus=4242), _Armor(set_bonus=4242)]
        for slot in ("helmets", "legs", "gauntlets")
    })

    sentence = set_bonus_shortfall(snapshot, _constraints(need=4))

    assert sentence and "只有 3 个部位" in sentence, "3 个部位 × 每处 3 件，仍然是 3 < 4"
