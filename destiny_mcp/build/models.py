"""Build Engine data models.

Defines Armor, InventorySnapshot, BuildRequest, BuildResult, and related
models for the build solver. Separate from src/destiny_mcp/models.py to
keep the Build Engine domain isolated (per ADR-005 design principle #2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field

from ..logging_config import get_logger
from ..models import ArmorStats
from ..utils.hash_utils import to_unsigned
from ..build_import.models import CanonicalBuild
from .armor_rules import (
    ArmorRollTemplate,
    ArmorTuningOption,
    resolve_archetype,
    tier5_templates,
    tuning_options,
)
from .constants import (
    ARMOR_SLOT_MAP,
    NAME_TO_STAT_HASH,
    STAT_HASH_TO_NAME,
    STAT_NAMES,
    MAIN_STAT_HASHES,
)

if TYPE_CHECKING:
    from ..manifest import ManifestManager

logger = get_logger(__name__)

__all__ = ["NAME_TO_STAT_HASH"]


def armor_slot_from_bucket(bucket_hash: int) -> str | None:
    """Return armor slot name (helmet/gauntlets/chest/legs/class_item) or None."""
    return ARMOR_SLOT_MAP.get(bucket_hash)


def _matching_armor3_roll(
    final_stats: dict[str, int],
    gear_tier: int,
    archetype_hash: int,
    tuning_hash: int | None,
) -> tuple[ArmorRollTemplate, ArmorTuningOption | None] | None:
    """Restore a legal Tier-5 roll from stats after installed mods are removed."""
    archetype = resolve_archetype(archetype_hash)
    if archetype is None:
        return None
    actual = tuple(int(final_stats.get(stat_name, 0)) for stat_name in STAT_NAMES)
    matches: list[tuple[ArmorRollTemplate, ArmorTuningOption | None]] = []
    for template in tier5_templates(archetype):
        expected = [value + gear_tier if value == 0 else value for value in template.base_stats]
        option: ArmorTuningOption | None = None
        if tuning_hash is not None:
            option = next(
                (
                    candidate
                    for candidate in tuning_options(template)
                    if candidate.plug_hash == tuning_hash
                    or (candidate.plug_hash & 0xFFFFFFFF) == (tuning_hash & 0xFFFFFFFF)
                ),
                None,
            )
            if option is None:
                continue
            if option.kind == "balanced":
                for index, value in enumerate(template.base_stats):
                    if value == 0:
                        expected[index] += 1
            elif option.increased_stat and option.decreased_stat:
                expected[STAT_NAMES.index(option.increased_stat)] += 5
                expected[STAT_NAMES.index(option.decreased_stat)] -= 5
        if actual == tuple(expected):
            matches.append((template, option))
    return matches[0] if len(matches) == 1 else None


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════
# ArmorStats is imported from ..models (single source of truth)


class StatModDefinition(BaseModel):
    """One executable armor stat mod loaded from the current manifest."""

    hash: int = Field(gt=0)
    stat_hash: int = Field(gt=0)
    value: int = Field(gt=0)
    energy_cost: int = Field(default=0, ge=0)
    kind: Literal["general", "artifice"]


class Armor(BaseModel):
    """A single armor piece with stats."""

    item_instance_id: str
    item_hash: int
    name: str
    slot: str = Field(description="helmet / gauntlets / chest / legs / class_item")
    power: int | None = None
    stats: ArmorStats = Field(default_factory=ArmorStats)
    is_exotic: bool = False
    tier: int = 0  # inventory.tierType: 5=Legendary, 6=Exotic
    is_masterworked: bool = False
    energy_capacity: int = 0  # 0-10, masterwork = 10
    # Armor 3.0 metadata is optional because older profiles only expose the
    # legacy stat model.  Farm diagnostics only trust verified Tier-5 rolls.
    armor_system: Literal["legacy", "armor_3"] = "legacy"
    gear_tier: int | None = Field(default=None, ge=1, le=5)
    archetype_hash: int | None = None
    archetype_name: str = ""
    primary_stat: str = ""
    secondary_stat: str = ""
    tertiary_stat: str = ""
    base_roll_stats: ArmorStats = Field(default_factory=ArmorStats)
    masterwork_level: int = Field(default=0, ge=0, le=5)
    tuning_mod_hash: int | None = None
    tuning_name: str = ""
    armor3_roll_verified: bool = False
    roll_parse_error: str = ""
    is_artifice: bool = False  # artifice armor has +3 mod slot
    has_set_bonus_mod_socket: bool = False  # wildcard: counts as any set
    set_bonus_hash: int | None = None  # DestinyEquipableItemSetDefinition hash
    set_bonus_name: str = ""  # Set display name (Chinese)
    icon_url: str = Field(default="", description="Bungie CDN icon URL for rendering in web UI")
    source_location: str = ""
    source_character_id: str = ""
    is_equipped: bool = False


def _load_stat_mod_definitions(
    manifest: "ManifestManager",
) -> list[StatModDefinition]:
    """Load current general/artifice stat mods as executable definitions."""
    get_mods = getattr(manifest, "get_armor_mods", None)
    get_definition = getattr(manifest, "get_item_definition", None)
    if not callable(get_mods) or not callable(get_definition):
        return []

    raw_mods = get_mods(category="all")
    if not isinstance(raw_mods, list):
        return []

    selected: dict[tuple[str, int, int], StatModDefinition] = {}
    for raw_mod in raw_mods:
        if not isinstance(raw_mod, dict):
            continue
        kind = str(raw_mod.get("category") or "")
        if kind not in {"general", "artifice"}:
            continue
        try:
            item_hash = to_unsigned(int(raw_mod.get("hash") or 0))
            energy_cost = max(0, int(raw_mod.get("energy_cost") or 0))
        except (TypeError, ValueError):
            continue
        if item_hash == 0:
            continue
        definition = get_definition(item_hash)
        if not isinstance(definition, dict):
            continue
        for stat in definition.get("investmentStats", []):
            if not isinstance(stat, dict):
                continue
            stat_hash = stat.get("statTypeHash", 0)
            value = stat.get("value", 0)
            if (
                stat_hash not in MAIN_STAT_HASHES
                or not isinstance(value, int)
                or value <= 0
            ):
                continue
            mod_kind = cast(Literal["general", "artifice"], kind)
            candidate = StatModDefinition(
                hash=item_hash,
                stat_hash=stat_hash,
                value=value,
                energy_cost=energy_cost,
                kind=mod_kind,
            )
            key = (kind, stat_hash, value)
            current = selected.get(key)
            if current is None or (candidate.energy_cost, candidate.hash) < (
                current.energy_cost,
                current.hash,
            ):
                selected[key] = candidate

    return sorted(
        selected.values(),
        key=lambda mod: (
            mod.kind,
            mod.stat_hash,
            mod.value,
            mod.energy_cost,
            mod.hash,
        ),
    )


class InventorySnapshot(BaseModel):
    """All armor pieces grouped by slot, ready for the solver."""

    helmets: list[Armor] = Field(default_factory=list)
    gauntlets: list[Armor] = Field(default_factory=list)
    chests: list[Armor] = Field(default_factory=list)
    legs: list[Armor] = Field(default_factory=list)
    class_items: list[Armor] = Field(default_factory=list)
    stat_mod_definitions: list[StatModDefinition] = Field(default_factory=list)

    @property
    def total_pieces(self) -> int:
        return (
            len(self.helmets)
            + len(self.gauntlets)
            + len(self.chests)
            + len(self.legs)
            + len(self.class_items)
        )

    def get_slot(self, slot: str) -> list[Armor]:
        """Get armor list for a given slot name."""
        return getattr(self, slot, [])

    # ── Construction ───────────────────────────────────────────────────

    @classmethod
    def from_profile(
        cls,
        profile: dict,
        manifest: "ManifestManager",
        character_class: str = "",
    ) -> "InventorySnapshot":
        """Build an InventorySnapshot from a raw GetProfile response.

        The profile must include components 102 (profileInventory), 200
        (characters), 201 (characterInventories), 205 (characterEquipment),
        300 (itemInstances), 304 (itemStats), and 305 (itemSockets).

        Args:
            profile: Raw GetProfile response (already unwrapped from Bungie envelope).
            manifest: ManifestManager for item name/type/tier lookups.
            character_class: Target class filter — "hunter"/"warlock"/"titan" (or Chinese).
                           If empty, includes all classes.

        Returns:
            InventorySnapshot with armor grouped by slot.
        """
        snapshot = cls(stat_mod_definitions=_load_stat_mod_definitions(manifest))
        instances_data = (
            profile.get("itemComponents", {})
            .get("instances", {})
            .get("data", {})
        )
        stats_data = (
            profile.get("itemComponents", {})
            .get("stats", {})
            .get("data", {})
        )
        sockets_data_map = (
            profile.get("itemComponents", {})
            .get("sockets", {})
            .get("data", {})
        )

        # Plug category hashes. Armor 3.0 sockets identify the archetype and
        # tuning result; general/artifice stat mods are stripped below.
        _ARTIFICE_PLUG_CATEGORIES = {3773173029, 595201146}
        _ARMOR3_ARCHETYPE_CATEGORY = 778194869
        _ARMOR3_TUNING_CATEGORY = 3481777685
        _SET_BONUS_SELECTOR_CATEGORY = 1313063513
        _MOD_CATEGORIES = {
            2487827355,  # armor mods (grenade, melee, etc.)
            *_ARTIFICE_PLUG_CATEGORIES,
        }

        def _strip_installed_plugs_from_stats(
            final_stats: dict[str, int],
            sockets_data: list[dict],
            manifest: "ManifestManager",
        ) -> dict[str, int]:
            """Get base stats for the optimizer.

            Bungie API component 302 returns FINAL stats including all mods,
            masterwork, artifice bonuses, and tuning mods. We strip only
            player-installed general mods (attribute mods + artifice mods) to
            get base+masterwork+tuning stats. Masterwork and tuning bonuses
            are kept as-is.

            Two-phase check per plug:
            1. plugCategoryHash must be in the general/artifice mod whitelist.
               This excludes tuning mods (separate socket) and masterwork
               (baked into API response, not a socket plug).
            2. investmentStats must contain at least one of the 6 main armor
               stat hashes. This prevents stripping plugs from the whitelist
               that don't actually affect stats (e.g., cosmetic plugs).
            Both conditions must be true for a plug to be stripped.
            """
            result = dict(final_stats)

            for socket in sockets_data:
                plug_hash = socket.get("plugHash", 0)
                if not plug_hash:
                    continue
                plug_def = manifest.get_item_definition(plug_hash)
                if not plug_def:
                    continue
                plug_cat = (plug_def.get("plug") or {}).get("plugCategoryHash", 0)
                if plug_cat not in _MOD_CATEGORIES:
                    continue
                for stat_entry in plug_def.get("investmentStats", []):
                    stat_hash = stat_entry.get("statTypeHash", 0)
                    value = stat_entry.get("value", 0)
                    if value == 0:
                        continue
                    if stat_hash == 16120457:
                        continue
                    # Safety: only strip if this stat is one of the 6 main
                    # armor stats (not energy cost, not PvE/PvP bonuses, etc.)
                    if stat_hash not in MAIN_STAT_HASHES:
                        continue
                    stat_name = STAT_HASH_TO_NAME.get(stat_hash)
                    if stat_name and stat_name in result:
                        result[stat_name] = max(0, result[stat_name] - value)

            return result

        def _parse_armor(
            raw: dict,
            location: str,
            target_class_type: int = -1,
            source_character_id: str = "",
            is_equipped: bool = False,
        ) -> Armor | None:
            """Parse a single item into an Armor model, or return None if not armor.

            Args:
                raw: Raw item data from API.
                location: "vault" or "character".
                target_class_type: 0=Titan, 1=Hunter, 2=Warlock, -1=any.
            """
            bucket_hash = raw.get("bucketHash", 0)
            slot = armor_slot_from_bucket(bucket_hash)
            if slot is None:
                return None  # Not an armor piece

            inst_id = str(raw.get("itemInstanceId", "0"))
            item_hash = raw.get("itemHash", 0)
            info = manifest.get_item_info(item_hash) or {}

            # Check class type: armor is class-specific (0=Titan, 1=Hunter, 2=Warlock)
            # classType=3 means "any class" (rare for armor)
            if target_class_type >= 0:
                armor_class_type = info.get("classType", -1)
                if armor_class_type >= 0 and armor_class_type <= 2 and armor_class_type != target_class_type:
                    return None  # Wrong class
            instance = instances_data.get(inst_id, {})
            item_stats = stats_data.get(inst_id, {}).get("stats", {})

            # Parse 6 armor stats from component 304 (these are FINAL values
            # including all mods, masterwork, artifice bonuses)
            final_stats: dict[str, int] = {}
            for stat_hash, stat_name in STAT_HASH_TO_NAME.items():
                stat_entry = item_stats.get(str(stat_hash), {}) or item_stats.get(
                    stat_hash, {}
                )
                value = stat_entry.get("value", 0) if isinstance(stat_entry, dict) else 0
                final_stats[stat_name] = value

            tier = info.get("tier", 0)

            raw_gear_tier = instance.get("gearTier") if isinstance(instance, dict) else None
            armor_system: Literal["legacy", "armor_3"] = (
                "armor_3" if raw_gear_tier is not None else "legacy"
            )
            gear_tier = (
                raw_gear_tier
                if isinstance(raw_gear_tier, int)
                and not isinstance(raw_gear_tier, bool)
                and 1 <= raw_gear_tier <= 5
                else None
            )
            # Energy capacity remains relevant for legacy armor and is kept as
            # an informational field for Armor 3.0 pieces.
            energy = instance.get("energy", {}) if isinstance(instance, dict) else {}
            energy_capacity = int(energy.get("energyCapacity", 0) or 0) if isinstance(energy, dict) else 0
            is_masterworked = gear_tier == 5 if armor_system == "armor_3" else energy_capacity >= 10

            # Artifice armor & set bonus wildcard: check sockets
            sockets_data = sockets_data_map.get(inst_id, {}).get("sockets", [])
            is_artifice = False
            has_set_bonus_mod_socket = False
            archetype_hashes: list[int] = []
            tuning_hash: int | None = None
            tuning_name = ""
            for socket in sockets_data:
                plug_hash = socket.get("plugHash", 0)
                if plug_hash:
                    plug_def = manifest.get_item_definition(plug_hash)
                    if plug_def:
                        plug_category = (plug_def.get("plug") or {}).get("plugCategoryHash", 0)
                        if plug_category in _ARTIFICE_PLUG_CATEGORIES:
                            is_artifice = True
                        if plug_category == _SET_BONUS_SELECTOR_CATEGORY:
                            has_set_bonus_mod_socket = True
                        if plug_category == _ARMOR3_ARCHETYPE_CATEGORY:
                            archetype_hashes.append(int(plug_hash))
                        if plug_category == _ARMOR3_TUNING_CATEGORY:
                            if any(
                                entry.get("statTypeHash") in MAIN_STAT_HASHES
                                and int(entry.get("value", 0) or 0) != 0
                                for entry in plug_def.get("investmentStats", [])
                                if isinstance(entry, dict)
                            ):
                                tuning_hash = int(plug_hash)
                                tuning_name = str((plug_def.get("displayProperties") or {}).get("name") or "")

            # Strip installed plug contributions to get base stats.
            # DIM's optimizer works on base+masterwork, not final stats.
            base_stats_dict = _strip_installed_plugs_from_stats(
                final_stats, sockets_data, manifest,
            )

            armor3_roll_verified = False
            roll_parse_error = ""
            archetype_hash: int | None = None
            archetype_name = ""
            primary_stat = ""
            secondary_stat = ""
            tertiary_stat = ""
            base_roll_stats = ArmorStats()
            masterwork_level = gear_tier or 0
            if armor_system == "armor_3":
                if gear_tier is None:
                    roll_parse_error = "Armor 3.0 gearTier is missing or outside 1-5."
                elif gear_tier != 5:
                    roll_parse_error = (
                        f"Armor 3.0 gearTier={gear_tier} is not supported for roll inversion."
                    )
                elif not sockets_data:
                    roll_parse_error = "Armor 3.0 socket data is unavailable."
                elif len(archetype_hashes) != 1:
                    roll_parse_error = "Armor 3.0 archetype socket is missing or ambiguous."
                else:
                    archetype_hash = archetype_hashes[0]
                    roll = _matching_armor3_roll(base_stats_dict, gear_tier, archetype_hash, tuning_hash)
                    if roll is None:
                        roll_parse_error = (
                            "Armor 3.0 stats do not match a legal Tier-5 archetype roll."
                        )
                    else:
                        template, _ = roll
                        armor3_roll_verified = True
                        archetype_name = template.archetype.name
                        primary_stat = template.archetype.primary_stat
                        secondary_stat = template.archetype.secondary_stat
                        tertiary_stat = template.tertiary_stat
                        base_roll_stats = ArmorStats(**dict(zip(STAT_NAMES, template.base_stats)))
            # Legacy armor retains the historical optimizer behavior. Armor 3.0
            # stats are validated against 30/25/20 and never receive +2 here.
            elif not is_masterworked:
                for stat_name in STAT_NAMES:
                    base_stats_dict[stat_name] = base_stats_dict.get(stat_name, 0) + 2

            armor_stats = ArmorStats()
            for stat_name in STAT_NAMES:
                setattr(armor_stats, stat_name, base_stats_dict.get(stat_name, 0))

            # Set bonus: read equippingBlock.equipableItemSetHash
            set_bonus_hash = None
            set_bonus_name = ""
            set_bonus_info = manifest.get_set_bonus_info(item_hash)
            if set_bonus_info:
                set_bonus_hash = set_bonus_info["set_hash"]
                set_bonus_name = set_bonus_info["set_name"]

            icon_path = info.get("icon", "") if info else ""
            icon_url = (
                icon_path
                if icon_path.startswith("https://www.bungie.net/")
                else f"https://www.bungie.net{icon_path}" if icon_path else ""
            )

            return Armor(
                item_instance_id=inst_id,
                item_hash=item_hash,
                name=manifest.get_item_name(item_hash),
                slot=slot,
                power=(
                    instance.get("primaryStat", {}).get("value")
                    if instance
                    else None
                ),
                stats=armor_stats,
                is_exotic=(tier == 6),
                tier=tier,
                is_masterworked=is_masterworked,
                energy_capacity=energy_capacity,
                armor_system=armor_system,
                gear_tier=gear_tier,
                archetype_hash=archetype_hash,
                archetype_name=archetype_name,
                primary_stat=primary_stat,
                secondary_stat=secondary_stat,
                tertiary_stat=tertiary_stat,
                base_roll_stats=base_roll_stats,
                masterwork_level=masterwork_level,
                tuning_mod_hash=tuning_hash,
                tuning_name=tuning_name,
                armor3_roll_verified=armor3_roll_verified,
                roll_parse_error=roll_parse_error,
                is_artifice=is_artifice,
                has_set_bonus_mod_socket=has_set_bonus_mod_socket,
                set_bonus_hash=set_bonus_hash,
                set_bonus_name=set_bonus_name,
                icon_url=icon_url,
                source_location=location,
                source_character_id=source_character_id,
                is_equipped=is_equipped,
            )

        # Class type mapping: 0=Titan, 1=Hunter, 2=Warlock
        CLASS_MAP = {"titan": 0, "hunter": 1, "warlock": 2,
                     "泰坦": 0, "猎人": 1, "术士": 2}
        target_class_type = CLASS_MAP.get(character_class.lower(), -1) if character_class else -1

        # Vault (shared across all classes — filter by armor classType)
        vault_items = (
            profile.get("profileInventory", {})
            .get("data", {})
            .get("items", [])
        )
        for raw in vault_items:
            armor = _parse_armor(raw, location="vault", target_class_type=target_class_type)
            if armor is not None:
                getattr(snapshot, armor.slot).append(armor)

        # Characters
        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})

        for char_id in chars_data:
            char_data = chars_data[char_id]
            char_class_type = char_data.get("classType", -1)

            # Skip characters that don't match the target class
            if target_class_type >= 0 and char_class_type != target_class_type:
                continue

            source_location = {
                0: "titan",
                1: "hunter",
                2: "warlock",
            }.get(char_class_type, "unknown")
            for raw in inv_data.get(char_id, {}).get("items", []):
                armor = _parse_armor(
                    raw,
                    location=source_location,
                    target_class_type=target_class_type,
                    source_character_id=str(char_id),
                )
                if armor is not None:
                    getattr(snapshot, armor.slot).append(armor)
            for raw in equip_data.get(char_id, {}).get("items", []):
                armor = _parse_armor(
                    raw,
                    location=source_location,
                    target_class_type=target_class_type,
                    source_character_id=str(char_id),
                    is_equipped=True,
                )
                if armor is not None:
                    getattr(snapshot, armor.slot).append(armor)

        logger.debug(
            "InventorySnapshot built: %d helmets, %d gauntlets, %d chests, "
            "%d legs, %d class_items",
            len(snapshot.helmets),
            len(snapshot.gauntlets),
            len(snapshot.chests),
            len(snapshot.legs),
            len(snapshot.class_items),
        )
        return snapshot


# ═══════════════════════════════════════════════════════════════════════════
# Build Engine pipeline models
# ═══════════════════════════════════════════════════════════════════════════


class BuildRequest(BaseModel):
    """User-facing build request — what the player wants.

    This is the input from the LLM (after intent extraction) or from a
    preset. All stat targets are minimum values (≥).
    """

    character_class: str = Field(
        default="",
        description="Target class: hunter / warlock / titan (or 猎人/术士/泰坦)",
    )
    exotic_name: str | None = Field(
        default=None,
        description="Exotic armor name in Chinese or English (e.g. '天穹夜鹰')",
    )
    weapons_target: int | None = Field(default=None, ge=0, le=200)
    health_target: int | None = Field(default=None, ge=0, le=200)
    class_target: int | None = Field(default=None, ge=0, le=200)
    grenade_target: int | None = Field(default=None, ge=0, le=200)
    melee_target: int | None = Field(default=None, ge=0, le=200)
    super_target: int | None = Field(default=None, ge=0, le=200)
    top_n: int = Field(default=5, ge=1, le=20, description="Number of builds to return")
    description: str = Field(default="", description="Human-readable description of this build request")
    include_subclass_fragment: bool = Field(
        default=False,
        description="Whether to include subclass/fragment stat bonuses in the calculation. "
                    "Set to True when user mentions fragments/subclass bonuses. "
                    "When False, targets are pure armor+mod stats.",
    )
    fragment_names: list[str] = Field(
        default_factory=list,
        description="Fragment names to use (Chinese or English). "
                    "When provided, engine looks up these fragments' stat bonuses "
                    "from manifest instead of reading equipped fragments. "
                    "Example: ['保护琢面', '黎明琢面']",
    )
    set_bonus_name: str | None = Field(
        default=None,
        description="Set bonus name (e.g. '兴旺幸存者', 'Thriving Survivor'). "
                    "Only include armor from this set.",
    )
    set_bonus_count: int | None = Field(
        default=None, ge=2, le=4,
        description="Required number of set pieces (2 or 4). Default: 2",
    )
    priority_stats: list[str] = Field(
        default_factory=list,
        description="Stats to maximize in strict order after meeting all minimums. "
                    "Use 'melee' for Chinese '力量' or English 'strength'.",
    )
    priority_stat: str | None = Field(
        default=None,
        description="Stat to maximize after meeting all minimums (dump stat). "
                    "E.g. 'melee' means: meet all ≥ targets first, then dump "
                    "all remaining mod capacity into melee. "
                    "Accepts: weapons/health/class/grenade/melee/super "
                    "(or Chinese: 武器/生命/职业/手雷/近战/超能).",
    )


class BuildConstraints(BaseModel):
    """Normalized, solver-ready constraints.

    Produced by ConstraintParser from a BuildRequest. All stat fields are
    resolved to concrete minimum values (defaulting to 0).

    subclass_stats and fragment_stats are additive bonuses that reduce
    the armor requirements. The solver subtracts them from targets before
    searching, then adds them back when reporting final stats.
    """

    weapons_min: int = 0
    health_min: int = 0
    class_stat_min: int = 0
    grenade_min: int = 0
    melee_min: int = 0
    super_stat_min: int = 0
    exotic_hash: int | None = None
    exotic_hashes: set[int] = Field(default_factory=set, description="All hashes for the exotic (different versions of same item)")
    class_type: int | None = None
    top_n: int = 5
    subclass_stats: list[int] = Field(default_factory=lambda: [0, 0, 0, 0, 0, 0], description="Subclass stat bonuses in STAT_NAMES order")
    fragment_stats: list[int] = Field(default_factory=lambda: [0, 0, 0, 0, 0, 0], description="Fragment stat bonuses in STAT_NAMES order")
    set_bonus_hash: int | None = None  # Resolved from set_bonus_name
    set_bonus_count: int = 0  # Required pieces (0 = no filter)
    priority_stat_indices: list[int] = Field(default_factory=list)
    priority_stat_index: int | None = None  # Index into STAT_NAMES for dump stat

    def as_vector(self) -> list[int]:
        """Return stat minimums as a list in STAT_NAMES order."""
        return [getattr(self, f"{s}_min") for s in STAT_NAMES]

    def subclass_and_fragment_vector(self) -> list[int]:
        """Return combined subclass + fragment bonuses as a vector."""
        return [self.subclass_stats[i] + self.fragment_stats[i] for i in range(6)]

    @property
    def ordered_priority_indices(self) -> list[int]:
        """Return valid, de-duplicated priorities with legacy fallback."""
        result: list[int] = []
        for index in self.priority_stat_indices:
            if 0 <= index < len(STAT_NAMES) and index not in result:
                result.append(index)
        if (
            not result
            and self.priority_stat_index is not None
            and 0 <= self.priority_stat_index < len(STAT_NAMES)
        ):
            result.append(self.priority_stat_index)
        return result

    @property
    def has_any_target(self) -> bool:
        return any(self.as_vector())


class BuildCandidate(BaseModel):
    """One valid armor combination produced by the solver.

    Stats are armor + mod only (no subclass/fragment bonuses).
    Use subclass_fragment_bonus to get the final in-game stats.
    """

    items: list[Armor] = Field(description="5 armor pieces (one per slot)")
    weapons: int = 0
    health: int = 0
    class_stat: int = 0       # Class stat
    grenade: int = 0
    melee: int = 0
    super_stat: int = 0        # Super stat
    bonus_stats: dict[str, int] = Field(
        default_factory=dict,
        description="Stat bonuses from mods (stat_name -> bonus amount)",
    )
    stat_mods: list[int] = Field(
        default_factory=list,
        description="Hashes of assigned stat mods",
    )
    subclass_fragment_bonus: list[int] = Field(
        default_factory=lambda: [0, 0, 0, 0, 0, 0],
        description="Subclass + fragment bonuses in STAT_NAMES order. "
                    "Add these to get final in-game stats.",
    )

    @property
    def total_stats(self) -> int:
        return (
            self.weapons
            + self.health
            + self.class_stat
            + self.grenade
            + self.melee
            + self.super_stat
        )

    def stat(self, name: str) -> int:
        return getattr(self, name, 0)


class BuildResult(BaseModel):
    """Final scored build returned to the caller."""

    score: float = Field(description="Composite score (higher = better)")
    completion_rate: float = Field(
        description="Fraction of stat targets met (0.0–1.0)"
    )
    build: BuildCandidate
    missing_requirements: list[str] = Field(
        default_factory=list,
        description="Stat targets that weren't met",
    )
    fragment_details: list[dict] = Field(
        default_factory=list,
        description="Fragment stat details: [{name, stats: {stat: bonus}}]",
    )
    active_set_bonuses: list[dict] = Field(
        default_factory=list,
        description="Active set bonuses: [{set_name, piece_count, perks: [{name, description}]}]",
    )
    canonical_build: CanonicalBuild | None = Field(
        default=None,
        description="Exact executable build contract generated from this result",
    )


class BuildAnalysis(BaseModel):
    """Failure analysis when no build satisfies constraints."""

    reason: str = Field(description="Why no build was found")
    max_possible: dict[str, int] = Field(
        default_factory=dict,
        description="Max achievable stats given current inventory",
    )
    suggested_farm: list[str] = Field(
        default_factory=list,
        description="Suggested farming activities",
    )
    farm_options: list["ArmorFarmOption"] = Field(
        default_factory=list,
        description="Legal Armor 3.0 rolls that can satisfy a farm-target request.",
    )
    farm_plans: list["ArmorFarmPlan"] = Field(
        default_factory=list,
        description="Minimum two-piece Armor 3.0 farm plans when one piece is insufficient.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Explicit calculation assumptions for diagnostic results.",
    )


class ArmorFarmOption(BaseModel):
    """One legal, not-yet-owned Armor 3.0 roll for a farm diagnostic.

    This is diagnostic output only: it has no instance ID and cannot be passed
    to equip/transfer operations.
    """

    replacement_slot: str
    baseline: Literal["equipped", "inventory"]
    archetype_hash: int
    archetype_name: str
    primary_stat: str
    secondary_stat: str
    tertiary_stat: str
    base_stats: ArmorStats
    masterworked_stats: ArmorStats
    tuning_name: str = ""
    tuning_hash: int | None = None
    tuning_delta: ArmorStats = Field(default_factory=ArmorStats)
    projected_stats: ArmorStats
    projected_total: ArmorStats
    stat_mod_bonus: ArmorStats = Field(default_factory=ArmorStats)
    stat_mods: list[int] = Field(default_factory=list)
    requires_set_piece: bool = False
    locked_items: list[dict[str, str]] = Field(default_factory=list)


class ArmorFarmPiece(BaseModel):
    """One prospective legendary piece inside a multi-piece farm plan."""

    replacement_slot: str
    archetype_hash: int
    archetype_name: str
    primary_stat: str
    secondary_stat: str
    tertiary_stat: str
    base_stats: ArmorStats
    masterworked_stats: ArmorStats
    tuning_name: str = ""
    tuning_hash: int | None = None
    tuning_delta: ArmorStats = Field(default_factory=ArmorStats)
    projected_stats: ArmorStats
    requires_set_piece: bool = False


class ArmorFarmPlan(BaseModel):
    """A read-only plan for farming the minimum two replacement pieces."""

    replacement_count: Literal[2] = 2
    baseline: Literal["equipped", "inventory"]
    pieces: list[ArmorFarmPiece] = Field(min_length=2, max_length=2)
    projected_total: ArmorStats
    stat_mod_bonus: ArmorStats = Field(default_factory=ArmorStats)
    stat_mods: list[int] = Field(default_factory=list)
    locked_items: list[dict[str, str]] = Field(default_factory=list)


class BuildRecommendation(BaseModel):
    """AI-facing build recommendation result.

    Contains build candidates when the optimizer finds matches, or an
    analysis explaining why the request could not be satisfied.
    """

    results: list[BuildResult] = Field(default_factory=list)
    analysis: BuildAnalysis | None = None
