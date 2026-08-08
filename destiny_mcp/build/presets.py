"""Build Presets — curated build configurations for common activities.

Each preset is a BuildRequest with stat targets tuned for a specific
game mode or playstyle. They serve as starting points — the player
(or LLM) can override any value.

Naming convention: {ACTIVITY}_{CLASS}

Stat names (Renegades update):
  weapons — weapon reload speed and handling
  health  — survivability and damage resistance
  class   — class ability cooldown
  grenade — grenade cooldown
  melee   — melee cooldown
  super   — super ability cooldown
"""

from __future__ import annotations

from .models import BuildRequest

# ═══════════════════════════════════════════════════════════════════════════
# GM (Grandmaster Nightfall) — prioritize survivability
# ═══════════════════════════════════════════════════════════════════════════

GM_HUNTER = BuildRequest(
    character_class="hunter",
    health_target=200,
    grenade_target=200,
    description="GM Hunter: 200 Health for DR, 200 Grenade for grenades",
)

GM_WARLOCK = BuildRequest(
    character_class="warlock",
    health_target=200,
    class_target=200,
    grenade_target=200,
    description="GM Warlock: 200 Health + 200 Class + 200 Grenade",
)

GM_TITAN = BuildRequest(
    character_class="titan",
    health_target=200,
    grenade_target=200,
    description="GM Titan: 200 Health + 200 Grenade",
)

# ═══════════════════════════════════════════════════════════════════════════
# PvP (Crucible / Trials) — prioritize weapon handling and health
# ═══════════════════════════════════════════════════════════════════════════

PVP_HUNTER = BuildRequest(
    character_class="hunter",
    weapons_target=200,
    health_target=200,
    grenade_target=200,
    description="PvP Hunter: 200 Weapons + 200 Health + 200 Grenade",
)

PVP_WARLOCK = BuildRequest(
    character_class="warlock",
    health_target=200,
    class_target=200,
    grenade_target=200,
    description="PvP Warlock: 200 Health + 200 Class + 200 Grenade",
)

PVP_TITAN = BuildRequest(
    character_class="titan",
    health_target=200,
    class_target=200,
    grenade_target=200,
    description="PvP Titan: 200 Health + 200 Class + 200 Grenade",
)

# ═══════════════════════════════════════════════════════════════════════════
# Solo Dungeon — balance of survivability and ability uptime
# ═══════════════════════════════════════════════════════════════════════════

SOLO_DUNGEON_HUNTER = BuildRequest(
    character_class="hunter",
    health_target=200,
    weapons_target=200,
    grenade_target=200,
    description="Solo Dungeon Hunter: 200 Health + 200 Weapons + 200 Grenade",
)

SOLO_DUNGEON_WARLOCK = BuildRequest(
    character_class="warlock",
    health_target=200,
    class_target=200,
    grenade_target=200,
    description="Solo Dungeon Warlock: 200 Health + 200 Class + 200 Grenade",
)

SOLO_DUNGEON_TITAN = BuildRequest(
    character_class="titan",
    health_target=200,
    grenade_target=200,
    melee_target=200,
    description="Solo Dungeon Titan: 200 Health + 200 Grenade + 200 Melee",
)

# ═══════════════════════════════════════════════════════════════════════════
# Registry — all presets indexed by name
# ═══════════════════════════════════════════════════════════════════════════

PRESETS: dict[str, BuildRequest] = {
    "gm_hunter": GM_HUNTER,
    "gm_warlock": GM_WARLOCK,
    "gm_titan": GM_TITAN,
    "pvp_hunter": PVP_HUNTER,
    "pvp_warlock": PVP_WARLOCK,
    "pvp_titan": PVP_TITAN,
    "solo_dungeon_hunter": SOLO_DUNGEON_HUNTER,
    "solo_dungeon_warlock": SOLO_DUNGEON_WARLOCK,
    "solo_dungeon_titan": SOLO_DUNGEON_TITAN,
}


def get_preset(name: str) -> BuildRequest | None:
    """Look up a preset by name (case-insensitive)."""
    return PRESETS.get(name.lower().strip())
