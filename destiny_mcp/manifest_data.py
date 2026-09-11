"""Manifest 的静态常量：基址、物品类型名、社区别名。

下沉到这一层，让 manifest.py 与按域拆出的 mixin 都能引用，而不互相 import。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .logging_config import get_logger

logger = get_logger(__name__)

BUNGIE_BASE_URL = "https://www.bungie.net"

# Item type names
# 0 是 Bungie 枚举里名为 None 的成员（商人占位条目、部分任务步），不是"缺数据"；
# 输出空串而不是 "None"，否则 JSON 里看起来像个字符串化的 null。
ITEM_TYPE_NAMES: dict[int, str] = {
    0: "",
    1: "Currency",
    2: "Armor",
    3: "Weapon",
    7: "Message",
    8: "Engram",
    9: "Consumable",
    10: "Exchange Material",
    11: "Mission Reward",
    12: "Quest Step",
    13: "Quest Step Complete",
    14: "Emblem",
    15: "Quest",
    16: "Subclass",
    17: "Clan Banner",
    18: "Aura",
    19: "Mod",
    20: "Dummy",
    21: "Ship",
    22: "Vehicle",
    23: "Emote",
    24: "Ghost",
    25: "Package",
    26: "Bounty",
    27: "Wrapper",
    28: "Seasonal Artifact",
    29: "Finisher",
    30: "Pattern",
}

# Community nickname aliases (Chinese)
ITEM_ALIASES: dict[str, list[str]] = {
    "狼头": ["加拉尔号角"],
    "千语": ["千语"],
    "遗言": ["遗言"],
    "伊邪那岐": ["伊邪那岐的重担"],
    "猫头鹰": ["以太之猫"],
    "威能": ["威能武器"],
    "动能": ["动能武器"],
    "能量": ["能量武器"],
}


def _load_community_names() -> dict[str, list[str]]:
    """Load community name aliases from YAML file.

    Returns a dict mapping Chinese community names to official Chinese names.
    """
    yaml_path = Path(__file__).parent / "data" / "community_names.yaml"
    if not yaml_path.exists():
        return {}

    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to load community names from %s: %s", yaml_path, e)
        return {}

    aliases: dict[str, list[str]] = {}

    # Weapons: Chinese community name → English official name
    # We need to map to Chinese official names for search to work
    # For now, store English names as-is (search handles both)
    for zh_name, en_name in (data.get("weapons") or {}).items():
        aliases[zh_name.lower()] = [en_name]

    # Armor: Chinese community name → English official name
    for zh_name, en_name in (data.get("armor") or {}).items():
        aliases[zh_name.lower()] = [en_name]

    return aliases


# Load community names at module level
_COMMUNITY_ALIASES = _load_community_names()

# Merge into ITEM_ALIASES
ITEM_ALIASES.update(_COMMUNITY_ALIASES)


# 职业名 → classType
CHARACTER_CLASS_MAP: dict[str, int] = {
    "titan": 0,
    "hunter": 1,
    "warlock": 2,
    "泰坦": 0,
    "猎人": 1,
    "术士": 2,
}
