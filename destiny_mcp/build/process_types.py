"""Process worker types for the Build Engine.

Translated from DIM's src/app/loadout-builder/process-worker/types.ts.
These types are used internally by the solver and mod assignment logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import NAME_TO_STAT_HASH as _STAT_NAME_TO_HASH
from .models import STAT_NAMES

# ═══════════════════════════════════════════════════════════════════════════
# Mod stat boost constants (from DIM types.ts)
# ═══════════════════════════════════════════════════════════════════════════

ARTIFICE_STAT_BOOST: int = 3
MINOR_STAT_BOOST: int = 5
MAJOR_STAT_BOOST: int = 10

MAX_STAT: int = 200


# ═══════════════════════════════════════════════════════════════════════════
# ProcessItem — minimal armor representation for the solver
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProcessItem:
    """Minimal armor item representation for the solver.

    Translated from DIM's ProcessItem interface.
    """

    id: str
    hash: int = 0
    name: str = ""
    is_exotic: bool = False
    is_artifice: bool = False
    remaining_energy_capacity: int = 0
    power: int = 0
    stats: dict[int, int] = field(default_factory=dict)
    compatible_activity_mod: str | None = None
    set_bonus: int | None = None
    intrinsic_perks: list[int] = field(default_factory=list)
    has_set_bonus_mod_socket: bool = False
    included_tuning_mod: int | None = None


# ═══════════════════════════════════════════════════════════════════════════
# AutoModData — mod definitions
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ModDef:
    """A single mod definition with hash and energy cost."""

    hash: int
    cost: int


@dataclass
class StatMods:
    """Major and minor mod definitions for a single stat."""

    major_mod: ModDef | None = None
    minor_mod: ModDef | None = None


@dataclass
class AutoModData:
    """Data describing the mods that can be automatically picked.

    Translated from DIM's AutoModData interface.
    """

    general_mods: dict[int, StatMods] = field(default_factory=dict)
    artifice_mods: dict[int, int] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# ModsPick — a mod assignment for one stat
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ModsPick:
    """A particular way of achieving a target stat value for a single stat.

    Translated from DIM's ModsPick interface.
    """

    num_artifice_mods: int
    num_general_mods: int
    general_mods_costs: list[int]
    mod_hashes: list[int]
    mod_energy_cost: int
    target_stat_index: int
    exact_stat_points: int


# ═══════════════════════════════════════════════════════════════════════════
# ProcessMod — a locked mod
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProcessMod:
    """A mod that must be included in the build."""

    hash: int
    energy_cost: int
    tag: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# ProcessResult — solver output
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProcessArmorSet:
    """A single armor set result from the solver."""

    armor: list[Any] = field(default_factory=list)  # list[Armor] from models.py
    process_items: list[ProcessItem] = field(default_factory=list)  # for internal use
    stats: list[int] = field(default_factory=list)
    bonus_stats: list[int] = field(default_factory=list)
    stat_mods: list[int] = field(default_factory=list)
    stat_mod_assignments: dict[str, list[int]] = field(default_factory=dict)
    #: 排序键（`build/ranking.goodness_key`），搜索/剪枝/候选保留/展示**同一处**生成。
    #: 以前这里是三个各自为政的字段（enabled_stats_total / stat_mix / stats_total），
    #: 外面还有一套加权总分和 `completion_rate` 打头的展示排序 —— 三套口径互相打架。
    rank_key: tuple[int, ...] = ()
    power: int = 0


@dataclass(frozen=True)
class SearchCoverage:
    """这次搜索**到底搜完了没有**。

    存在的理由：调用方要能区分"枚举完了、真的没有满足下限的方案"与"没搜完/被截断"。
    本仓库已经栽过一次同类跟头（`analyze` 在没验证过的情况下断言"没有合法组合"，0.1.13 修），
    所以这条纪律写进类型里而不是靠注释：**预算/配额用尽永远不能产生"不可行"的结论**
    （对照 d2-armor-solver 的 "no limit can create an infeasibility proof"）。

    今天五层枚举没有配额，所以 `exhaustive` 恒为 True；P4 要把调谐枚举与预算做进来时，
    这里是唯一的出口。
    """

    exhaustive: bool
    combos: int
    truncated_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "exhaustive": self.exhaustive,
            "combos": self.combos,
            "truncated_by": self.truncated_by or None,
        }


@dataclass(frozen=True)
class SearchDiagnostics:
    """一次求解"关于自己"的全部信息：搜完了没有 + 每项属性**单独**能顶到多少。

    `reachable_ceilings` 按 `STAT_NAMES` 顺序，是"在其余属性仍满足下限的前提下，这一项
    最多能到多少"（`update_max_stats` 逐点试出来的，DIM 同源）。**逐项可达 ≠ 同时可达**：
    六个数放在一起并不能构成一套配装，回答时必须原样带上这句话（阶梯那边已经钉过同一条）。
    """

    coverage: SearchCoverage
    reachable_ceilings: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.coverage.to_dict(),
            "reachable": dict(zip(STAT_NAMES, self.reachable_ceilings)),
            "reachable_note": (
                "每一项都是「其余属性仍满足下限时，这一项**已验证能到**多少」。"
                "两个都要记住：① 它是**保守下界**，不是上限 —— 模组按 5/10 加减，这一步只验证了"
                "「要求到这么多」时还装得下，实际可能更高（真机实测手雷报 120，把下限写成 130 "
                "时确实能到 130）；② **逐项可达 ≠ 同时达到**，六个数放在一起不构成一套配装。"
                "要真的顶上去，就把目标写成下限再加优先级。"
            )
            if self.reachable_ceilings
            else None,
        }


@dataclass
class ProcessResult:
    """Result from the solver."""

    sets: list[ProcessArmorSet] = field(default_factory=list)
    combos: int = 0
    #: 搜索有没有跑完。默认 True（五层枚举无配额）；将来加了配额就必须如实置 False。
    complete: bool = True
    truncated_by: str = ""
    #: 逐项可达上限，按 `STAT_NAMES` 顺序（空 = 没算）。口径见 `SearchDiagnostics`。
    reachable_ceilings: list[int] = field(default_factory=list)

    @property
    def coverage(self) -> SearchCoverage:
        return SearchCoverage(
            exhaustive=self.complete, combos=self.combos, truncated_by=self.truncated_by
        )

    @property
    def diagnostics(self) -> SearchDiagnostics:
        return SearchDiagnostics(
            coverage=self.coverage, reachable_ceilings=list(self.reachable_ceilings)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Helper: convert Armor (from models.py) to ProcessItem
# ═══════════════════════════════════════════════════════════════════════════

def armor_to_process_item(armor: Any) -> ProcessItem:
    """Convert an Armor model to a ProcessItem for the solver.

    Args:
        armor: An Armor instance from build/models.py.

    Returns:
        A ProcessItem ready for the solver.
    """
    stats: dict[int, int] = {}
    for stat_name in STAT_NAMES:
        stat_hash = _STAT_NAME_TO_HASH[stat_name]
        stats[stat_hash] = armor.stats.get(stat_name)

    remaining_energy = max(0, int(getattr(armor, "energy_capacity", 0) or 0))

    return ProcessItem(
        id=armor.item_instance_id,
        hash=armor.item_hash,
        name=armor.name,
        is_exotic=armor.is_exotic,
        is_artifice=armor.is_artifice,
        remaining_energy_capacity=remaining_energy,
        power=armor.power or 0,
        stats=stats,
        set_bonus=armor.set_bonus_hash,
        has_set_bonus_mod_socket=getattr(armor, 'has_set_bonus_mod_socket', False),
    )
