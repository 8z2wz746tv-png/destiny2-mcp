"""Pure Armor 3.0 roll rules used by the build engine.

Tier-5 armor has one fixed archetype: its primary stat is 30, its secondary
stat is 25, and one of the four remaining stats is randomly selected at 20.
The three remaining stats are zero. This module deliberately models only
those legal rolls and the related masterwork/tuning transformations; it does
not read the Manifest or call the Bungie API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .constants import STAT_NAMES


StatName = Literal[
    "weapons",
    "health",
    "class_stat",
    "grenade",
    "melee",
    "super_stat",
]
StatsVector = tuple[int, int, int, int, int, int]
TuningKind = Literal["directional", "balanced"]

TIER_FIVE = 5
PRIMARY_STAT_VALUE = 30
SECONDARY_STAT_VALUE = 25
TERTIARY_STAT_VALUE = 20
FULL_MASTERWORK_ZERO_STAT_BONUS = 5
BALANCED_TUNING_LOWEST_STAT_BONUS = 1
DIRECTIONAL_TUNING_STAT_BONUS = 5
BALANCED_TUNING_HASH = -1172770080


@dataclass(frozen=True, slots=True)
class ArmorArchetype:
    """A fixed Tier-5 primary/secondary stat distribution."""

    plug_hash: int
    name: str
    primary_stat: StatName
    secondary_stat: StatName


@dataclass(frozen=True, slots=True)
class ArmorRollTemplate:
    """One legal unmasterworked Tier-5 armor roll."""

    archetype: ArmorArchetype
    tertiary_stat: StatName
    base_stats: StatsVector
    gear_tier: int = TIER_FIVE


@dataclass(frozen=True, slots=True)
class ArmorTuningOption:
    """One globally legal Armor 3.0 tuning plug."""

    plug_hash: int
    kind: TuningKind
    increased_stat: StatName | None = None
    decreased_stat: StatName | None = None


_ARCHETYPE_ROWS: tuple[tuple[int, str, StatName, StatName], ...] = (
    (-945573821, "Brawler", "melee", "health"),
    (549468645, "Bulwark", "health", "class_stat"),
    (1418248448, "Colossus", "super_stat", "health"),
    (-2072007163, "Demolitionist", "grenade", "class_stat"),
    (-1357301508, "Grenadier", "grenade", "super_stat"),
    (1807652646, "Gunner", "weapons", "grenade"),
    (-67901354, "Paragon", "super_stat", "melee"),
    (544009373, "Powerhouse", "weapons", "super_stat"),
    (351770835, "Reaver", "class_stat", "melee"),
    (-1791585361, "Siegebreaker", "health", "grenade"),
    (1687144140, "Skirmisher", "melee", "weapons"),
    (-2064538828, "Specialist", "class_stat", "weapons"),
)

ARMOR_ARCHETYPES: tuple[ArmorArchetype, ...] = tuple(
    ArmorArchetype(*row) for row in _ARCHETYPE_ROWS
)

# These are the current Manifest directional tuning plugs. Each pair is
# (increased_stat, decreased_stat). A prospective item has one hidden tuned
# stat, so it exposes only the five pairs that increase that stat; together the
# Manifest defines all thirty possible outcomes.
DIRECTIONAL_TUNING_HASHES: dict[tuple[StatName, StatName], int] = {
    ("class_stat", "grenade"): 1879022254,
    ("class_stat", "health"): -264306882,
    ("class_stat", "melee"): 1510949672,
    ("class_stat", "super_stat"): 957763733,
    ("class_stat", "weapons"): 323635379,
    ("grenade", "class_stat"): 1922571986,
    ("grenade", "health"): 455024236,
    ("grenade", "melee"): 309000506,
    ("grenade", "super_stat"): 1672416975,
    ("grenade", "weapons"): -178578123,
    ("health", "class_stat"): -984440564,
    ("health", "grenade"): -613884594,
    ("health", "melee"): 388618952,
    ("health", "super_stat"): -206143691,
    ("health", "weapons"): 2125798995,
    ("melee", "class_stat"): -84251828,
    ("melee", "grenade"): 534630542,
    ("melee", "health"): -130084194,
    ("melee", "super_stat"): 311164277,
    ("melee", "weapons"): -274617709,
    ("super_stat", "class_stat"): -740166907,
    ("super_stat", "grenade"): -348298289,
    ("super_stat", "health"): -268553035,
    ("super_stat", "melee"): 673231129,
    ("super_stat", "weapons"): -2050544686,
    ("weapons", "class_stat"): 1918710127,
    ("weapons", "grenade"): -1010524199,
    ("weapons", "health"): -1173206497,
    ("weapons", "melee"): 691392383,
    ("weapons", "super_stat"): 891771298,
}

_STAT_INDEX = {name: index for index, name in enumerate(STAT_NAMES)}
_STAT_SET = frozenset(STAT_NAMES)
_ARCHETYPES_BY_UNSIGNED_HASH = {
    archetype.plug_hash & 0xFFFFFFFF: archetype for archetype in ARMOR_ARCHETYPES
}


def resolve_archetype(plug_hash: object) -> ArmorArchetype | None:
    """Resolve a known archetype plug hash, accepting signed or unsigned IDs.

    Unknown, malformed, or out-of-range values return ``None`` so callers can
    reject an unsupported item rather than infer a stat distribution.
    """
    if isinstance(plug_hash, bool) or not isinstance(plug_hash, int):
        return None
    if not -(2**31) <= plug_hash <= 0xFFFFFFFF:
        return None
    return _ARCHETYPES_BY_UNSIGNED_HASH.get(plug_hash & 0xFFFFFFFF)


def tier5_templates(archetype_or_hash: object) -> tuple[ArmorRollTemplate, ...]:
    """Return the four legal Tier-5 rolls for one archetype.

    The tertiary stat must be one of the four stats outside the archetype's
    fixed primary/secondary pair. Invalid archetypes return no candidates.
    """
    archetype = _canonical_archetype(archetype_or_hash)
    if archetype is None:
        return ()

    templates: list[ArmorRollTemplate] = []
    for raw_tertiary_stat in STAT_NAMES:
        tertiary_stat = cast(StatName, raw_tertiary_stat)
        if tertiary_stat in {archetype.primary_stat, archetype.secondary_stat}:
            continue
        stats = [0] * len(STAT_NAMES)
        stats[_STAT_INDEX[archetype.primary_stat]] = PRIMARY_STAT_VALUE
        stats[_STAT_INDEX[archetype.secondary_stat]] = SECONDARY_STAT_VALUE
        stats[_STAT_INDEX[tertiary_stat]] = TERTIARY_STAT_VALUE
        templates.append(
            ArmorRollTemplate(
                archetype=archetype,
                tertiary_stat=tertiary_stat,
                base_stats=cast(StatsVector, tuple(stats)),
            )
        )
    return tuple(templates)


def all_tier5_templates() -> tuple[ArmorRollTemplate, ...]:
    """Return all 48 legal Tier-5 base-roll templates."""
    return tuple(template for archetype in ARMOR_ARCHETYPES for template in tier5_templates(archetype))


def is_valid_tier5_template(template: object) -> bool:
    """Return whether ``template`` exactly matches a legal Tier-5 roll."""
    if not isinstance(template, ArmorRollTemplate) or template.gear_tier != TIER_FIVE:
        return False
    archetype = _canonical_archetype(template.archetype)
    if archetype is None or template.tertiary_stat not in _STAT_SET:
        return False
    if template.tertiary_stat in {archetype.primary_stat, archetype.secondary_stat}:
        return False
    return template in tier5_templates(archetype)


def masterworked_stats(template: object) -> StatsVector | None:
    """Return a fully masterworked legal Tier-5 roll.

    A full masterwork adds five points only to the three zero base stats. It
    never changes the 30/25/20 distribution and invalid templates fail closed.
    """
    if not isinstance(template, ArmorRollTemplate) or not is_valid_tier5_template(template):
        return None
    return cast(
        StatsVector,
        tuple(
            value + FULL_MASTERWORK_ZERO_STAT_BONUS if value == 0 else value
            for value in template.base_stats
        ),
    )


def tuning_options(template: object) -> tuple[ArmorTuningOption, ...]:
    """Return every globally legal tuning result for a prospective Tier-5 roll.

    A real piece has a hidden random tuned stat. Its socket exposes only the
    five directional plugs that raise that stat, but the profile does not
    expose the hidden value separately. The Manifest defines all thirty
    ordered +5/-5 pairs, so installed-roll parsing validates against that
    global set and farm diagnostics enumerate each prospective hidden result.
    Balanced Tuning adds one point to each of the three lowest stats after full
    masterwork.
    """
    if not isinstance(template, ArmorRollTemplate) or not is_valid_tier5_template(template):
        return ()

    directional: list[ArmorTuningOption] = []
    for raw_increased_stat in STAT_NAMES:
        increased_stat = cast(StatName, raw_increased_stat)
        for raw_decreased_stat in STAT_NAMES:
            decreased_stat = cast(StatName, raw_decreased_stat)
            if decreased_stat == increased_stat:
                continue
            directional.append(
                ArmorTuningOption(
                    plug_hash=DIRECTIONAL_TUNING_HASHES[(increased_stat, decreased_stat)],
                    kind="directional",
                    increased_stat=increased_stat,
                    decreased_stat=decreased_stat,
                )
            )
    return (*directional, ArmorTuningOption(BALANCED_TUNING_HASH, "balanced"))


def apply_tuning(template: object, option: object) -> StatsVector | None:
    """Apply one valid tuning plug to a fully masterworked Tier-5 template.

    The input is intentionally a legal template rather than arbitrary six-stat
    values. This prevents the inverse builder from emitting an impossible roll
    or a tuning plug that is not defined by the current Manifest.
    """
    if (
        not isinstance(template, ArmorRollTemplate)
        or not is_valid_tier5_template(template)
        or not isinstance(option, ArmorTuningOption)
    ):
        return None
    if option not in tuning_options(template):
        return None

    stats = list(masterworked_stats(template) or ())
    if len(stats) != len(STAT_NAMES):
        return None

    if option.kind == "directional":
        if option.increased_stat is None or option.decreased_stat is None:
            return None
        increased_index = _STAT_INDEX[option.increased_stat]
        decreased_index = _STAT_INDEX[option.decreased_stat]
        if stats[decreased_index] < DIRECTIONAL_TUNING_STAT_BONUS:
            return None
        stats[increased_index] += DIRECTIONAL_TUNING_STAT_BONUS
        stats[decreased_index] -= DIRECTIONAL_TUNING_STAT_BONUS
    elif option.kind == "balanced":
        minimum = min(stats)
        lowest_indices = [index for index, value in enumerate(stats) if value == minimum]
        if len(lowest_indices) != 3:
            return None
        for index in lowest_indices:
            stats[index] += BALANCED_TUNING_LOWEST_STAT_BONUS
    else:
        return None

    return cast(StatsVector, tuple(stats))


def _canonical_archetype(value: object) -> ArmorArchetype | None:
    if isinstance(value, ArmorArchetype):
        canonical = resolve_archetype(value.plug_hash)
        return canonical if canonical == value else None
    return resolve_archetype(value)
