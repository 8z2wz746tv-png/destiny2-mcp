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
    enabled_stats_total: int = 0
    stats_total: int = 0
    stat_mix: int = 0
    power: int = 0


@dataclass
class ProcessResult:
    """Result from the solver."""

    sets: list[ProcessArmorSet] = field(default_factory=list)
    combos: int = 0


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
