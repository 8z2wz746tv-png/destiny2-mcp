"""Build Engine constants — single source of truth for stat definitions.

All stat hashes, names, and subclass bonuses live here. Every other module
(build/models, build/solver, services/build_service, services/item_parser)
imports from this file rather than defining its own copy.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════
# Stat ordering — consistent across solver, scorer, analyzer
# ═══════════════════════════════════════════════════════════════════════════
# Renegades update: old stats (Mobility/Resilience/Recovery/Discipline/
# Intellect/Strength) replaced with (Weapons/Health/Class/Grenade/Melee/Super).
# 'class' and 'super' are Python reserved words, so we use 'class_stat' and 'super_stat'.

#: 单项属性的"无限"哨兵值（求解器内部用它表示"没设上限"）。
#: 真机游戏里单项目标上限是 100，但求解器的中间计算（可达上限逐点推、乐观余量）
#: 需要一个大到不会先撞上的数 —— 200 就是 DIM 同源的那个值。
#: 放在这里是因为 `ranking` 与 `process_types` **都要用**，而 `constants` 是最底层。
MAX_STAT: int = 200


STAT_NAMES: tuple[str, ...] = (
    "weapons",
    "health",
    "class_stat",
    "grenade",
    "melee",
    "super_stat",
)

#: `STAT_NAMES` → `BuildRequest` 的字段名。只有 `class_stat` 不一样（请求里叫
#: `class_target`）。以前这张表在 `tools/_armor_ladder.py` 里抄了一份，`build_service`
#: 的日志又按 `STAT_NAMES` 拼字符串，于是 `class_stat_target` 直接 AttributeError ——
#: 一个事实写三处就会这样，收到这里。
REQUEST_TARGET_FIELDS: dict[str, str] = {
    "weapons": "weapons_target",
    "health": "health_target",
    "class_stat": "class_target",
    "grenade": "grenade_target",
    "melee": "melee_target",
    "super_stat": "super_target",
}

# ═══════════════════════════════════════════════════════════════════════════
# Destiny 2 stat hashes (from DestinyStatDefinition)
# ═══════════════════════════════════════════════════════════════════════════

STAT_HASH_TO_NAME: dict[int, str] = {
    2996146975: "weapons",      # was Mobility, now Weapons
    392767087: "health",        # was Resilience, now Health
    1943323491: "class_stat",   # was Recovery, now Class
    1735777505: "grenade",      # was Discipline, now Grenade
    144602215: "super_stat",    # was Intellect, now Super
    4244567218: "melee",        # was Strength, now Melee
}

NAME_TO_STAT_HASH: dict[str, int] = {v: k for k, v in STAT_HASH_TO_NAME.items()}

# Stat hashes in STAT_NAMES order (for positional vectors)
STAT_HASHES: list[int] = [
    2996146975,  # weapons
    392767087,   # health
    1943323491,  # class_stat
    1735777505,  # grenade
    4244567218,  # melee
    144602215,   # super_stat
]

# Hash → positional index (for fast lookup in investmentStats iteration)
MAIN_STAT_HASHES: dict[int, int] = {h: i for i, h in enumerate(STAT_HASHES)}

# ═══════════════════════════════════════════════════════════════════════════
# Armor slot bucket hashes (signed, from manifest.py BUCKET_NAMES)
# ═══════════════════════════════════════════════════════════════════════════

_SLOT_BUCKETS_SIGNED: dict[int, str] = {
    -846692857: "helmets",
    -743048708: "gauntlets",
    14239492: "chests",
    20886954: "legs",
    1585787867: "class_items",
}

# Normalize to unsigned 32-bit (Bungie API uses uint32 for bucket hashes)
ARMOR_SLOT_MAP: dict[int, str] = {}
for _hash, _slot in _SLOT_BUCKETS_SIGNED.items():
    ARMOR_SLOT_MAP[_hash & 0xFFFFFFFF] = _slot
    ARMOR_SLOT_MAP[_hash] = _slot  # also accept signed variant

ARMOR_SLOT_NAMES: dict[str, int] = {v: k for k, v in _SLOT_BUCKETS_SIGNED.items()}

# ═══════════════════════════════════════════════════════════════════════════
# Subclass base stat bonuses (hardcoded from Destiny 2 game data)
# ═══════════════════════════════════════════════════════════════════════════
# Vector order: [weapons, health, class_stat, grenade, melee, super_stat]
#
# Solar: +10 Health, -10 Weapons
# Arc:   +10 Weapons, -10 Class
# Void:  +10 Health, -10 Melee
# Stasis:+10 Health, -10 Super
# Strand:+10 Weapons, -10 Grenade
# Prismatic: +10 Grenade, +10 Melee, -10 Class

SUBCLASS_BONUSES: dict[int, list[int]] = {
    # ── Solar ──
    -2054078480: [-10, 10, 0, 0, 0, 0],   # Gunslinger (Solar Hunter)
    -658976260:  [-10, 10, 0, 0, 0, 0],   # Gunslinger (Solar Hunter v2)
    -1744643364: [-10, 10, 0, 0, 0, 0],   # Sunbreaker (Solar Titan)
    -1189032294: [-10, 10, 0, 0, 0, 0],   # Sunbreaker (Solar Titan v2)
    -813105499:  [-10, 10, 0, 0, 0, 0],   # Dawnblade (Solar Warlock)
    -353761345:  [-10, 10, 0, 0, 0, 0],   # Dawnblade (Solar Warlock v2)
    # ── Arc ──
    -1966755996: [10, 0, -10, 0, 0, 0],   # Arcstrider (Arc Hunter)
    1334959255:  [10, 0, -10, 0, 0, 0],   # Arcstrider (Arc Hunter v2)
    -1362577280: [10, 0, -10, 0, 0, 0],   # Striker (Arc Titan)
    -1336588487: [10, 0, -10, 0, 0, 0],   # Striker (Arc Titan v2)
    -1125970221: [10, 0, -10, 0, 0, 0],   # Stormcaller (Arc Warlock)
    1751782730:  [10, 0, -10, 0, 0, 0],   # Stormcaller (Arc Warlock v2)
    # ── Void ──
    -1841615876: [0, 10, 0, 0, -10, 0],   # Nightstalker (Void Hunter)
    -1069007477: [0, 10, 0, 0, -10, 0],   # Nightstalker (Void Hunter v2)
    -1452496184: [0, 10, 0, 0, -10, 0],   # Sentinel (Void Titan)
    -912575511:  [0, 10, 0, 0, -10, 0],   # Sentinel (Void Titan v2)
    -1445916469: [0, 10, 0, 0, -10, 0],   # Voidwalker (Void Warlock)
    -407074640:  [0, 10, 0, 0, -10, 0],   # Voidwalker (Void Warlock v2)
    # ── Stasis ──
    873720784:   [0, 10, 0, 0, 0, -10],   # Revenant (Stasis Hunter)
    613647804:   [0, 10, 0, 0, 0, -10],   # Behemoth (Stasis Titan)
    -1003421793: [0, 10, 0, 0, 0, -10],   # Shadebinder (Stasis Warlock)
    # ── Strand ──
    -90553722:   [10, 0, 0, -10, 0, 0],   # Broodweaver (Strand Warlock)
    242419885:   [10, 0, 0, -10, 0, 0],   # Berserker (Strand Titan)
    -509524697:  [10, 0, 0, -10, 0, 0],   # Threadrunner (Strand Hunter)
    # ── Prismatic ──
    3893112950:  [0, 0, -10, 10, 10, 0],   # Prismatic Warlock
    3893112951:  [0, 0, -10, 10, 10, 0],   # Prismatic Hunter
    3893112948:  [0, 0, -10, 10, 10, 0],   # Prismatic Titan
    -401854346:  [0, 0, -10, 10, 10, 0],   # Prismatic Warlock (current)
    1616346845:  [0, 0, -10, 10, 10, 0],   # Prismatic Titan (current)
    -12375465:   [0, 0, -10, 10, 10, 0],   # Prismatic Hunter (current)
}
