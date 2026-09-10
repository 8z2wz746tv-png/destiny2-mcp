"""Destiny Manifest manager — item name lookups and search.

Downloads the Destiny manifest (SQLite database) and indexes item definitions
for fast lookup by name (Chinese and English) or by item hash.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING


from . import config
from .exceptions import CharacterNotFoundError, ManifestError
from .logging_config import get_logger
from .manifest_armor import ArmorCatalogMixin
from .manifest_artifacts import ArtifactCatalogMixin
from .manifest_catalog import ItemCatalogMixin
from .manifest_definitions import ItemDefinitionMixin
from .manifest_item_queries import ItemQueryMixin
from .manifest_lookup import DefinitionLookupMixin
from .manifest_plugs import PlugCatalogMixin
from .manifest_data import (
    BUNGIE_BASE_URL as BUNGIE_BASE_URL,
    CHARACTER_CLASS_MAP as CHARACTER_CLASS_MAP,
    ITEM_ALIASES as ITEM_ALIASES,
    ITEM_TYPE_NAMES as ITEM_TYPE_NAMES,
)
from .manifest_search import SearchIndexMixin
from .utils.hash_utils import to_signed

logger = get_logger(__name__)

if TYPE_CHECKING:
    from .bungie_client import BungieClient

# Bungie CDN base URL for item icons

# Inventory bucket hashes for common slots
BUCKET_NAMES: dict[int, str] = {
    # Weapons (equipment slot hashes)
    1498876634: "Kinetic Weapons",
    2465295065: "Energy Weapons",
    953998645: "Power Weapons",
    # Armor
    -846692857: "Helmet",
    -743048708: "Gauntlets",
    14239492: "Chest Armor",
    20886954: "Leg Armor",
    1585787867: "Class Armor",
    # Other
    138197802: "Vault (General)",
    284967655: "Ships",
    2025709351: "Vehicle",
    -271772482: "Ghost",
    1107761855: "Emotes",
    375726501: "Engrams",
    1345459588: "Quests",
    -981765538: "Modifications",
    1469714392: "Consumables",
    1506418338: "Seasonal Artifact",
    215593132: "Lost Items",
    687325600: "Accessories",
    -429652670: "Materials",
    -20632005: "Emblems",
    -2521334: "Clan Banners",
}

# Weapon socket category hashes (unsigned, matching Bungie API responses)
SOCKET_CAT_INTRINSIC = 3956125808       # 固有特性 — weapon frame
SOCKET_CAT_WEAPON_PERKS = 4241085061    # 武器特性 — barrel, magazine, traits
SOCKET_CAT_COSMETICS = 2048875504       # 武器外观 — shaders
SOCKET_CAT_MODS = 2685412949            # 武器模组 — backup mag, targeting adjuster etc
SOCKET_CAT_MEMENTO = 3371622796         # 纪念物

# Socket label mapping for weapon perks
SOCKET_LABELS: dict[str, str] = {
    "barrels": "枪管",
    "barrel": "枪管",
    "sights": "瞄具",
    "scopes": "瞄具",
    "magazines": "弹匣",
    "magazine": "弹匣",
    "batteries": "弹匣",
    "grips": "握把",
    "stocks": "枪托",
    "perks": "特性",
    "frames": "特性",
    "intrinsic": "固有特性",
    "shader": "着色器",
    "mod": "模组",
    "masterwork": "大师杰作",
    "kill_vfx": "战斗特效",
    "skin": "皮肤",
    "tiering": "纪念物",
    "origins": "起源特性",
    "tracker": "追踪器",
    "catalyst": "催化",
}

# Weapon stat display names (Renegades update)
# 从 manifest 中文版查询的官方翻译
WEAPON_STAT_NAMES: dict[str, str] = {
    "4284893193": "每分钟发射数",  # RPM
    "4043523819": "伤害",
    "1240592695": "射程",
    "155624089": "稳定性",
    "943549884": "操控性",
    "4188031367": "填装速度",
    "1345609583": "辅助瞄准",
    "3555269338": "变焦",
    "2714457168": "空中效率",
    "2715839340": "后坐方向",
    "3871231066": "弹匣",
}

# Character class type mapping

def class_type_name(class_type: int) -> str:
    """Convert classType int to display name."""
    return {0: "Titan", 1: "Hunter", 2: "Warlock"}.get(class_type, "Unknown")


def resolve_character_name(name: str) -> int:
    """Convert a friendly character name to classType int.

    Raises CharacterNotFoundError if the name is not recognized.
    """
    name = name.strip().lower()
    if name in CHARACTER_CLASS_MAP:
        return CHARACTER_CLASS_MAP[name]
    raise CharacterNotFoundError(name, ["hunter", "warlock", "titan"])


class ManifestManager(
    SearchIndexMixin,
    ItemDefinitionMixin,
    ItemCatalogMixin,
    PlugCatalogMixin,
    DefinitionLookupMixin,
    ItemQueryMixin,
    ArtifactCatalogMixin,
    ArmorCatalogMixin,
):
    """Manages the Destiny manifest (SQLite) for item lookups.

    Supports bilingual search (English + Chinese) by loading two manifest
    files and merging their name indexes.
    """

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._zh_conn: sqlite3.Connection | None = None
        self._name_index: dict[str, list[dict]] = {}  # lowercase name → [item info]
        self._hash_index: dict[int, dict] = {}  # signed hash → item info (Chinese priority)
        self._english_name_by_hash: dict[int, str] = {}
        self._english_type_display_by_hash: dict[int, str] = {}
        self._definition_cache: dict[int, dict] = {}  # itemHash → full definition JSON
        self._plug_set_cache: dict[int, list[dict]] = {}  # plugSetHash → [{plugItemHash, ...}]
        self._sandbox_perk_cache: dict[int, dict] = {}  # perkHash → {name, description}

    @property
    def manifest_path(self) -> Path:
        return config.DESTINY_MANIFEST_PATH / "destiny_manifest.sqlite3"

    @property
    def manifest_path_zh(self) -> Path:
        return config.DESTINY_MANIFEST_PATH / "destiny_manifest_zh.sqlite3"

    def is_loaded(self) -> bool:
        return self._conn is not None and self._hash_index != {}

    async def ensure_loaded(self, bungie_client: "BungieClient") -> None:
        """Load manifest from disk, downloading if necessary.

        Must be called after bungie_client.start().
        """
        if self.is_loaded():
            return

        # Download English manifest for alias search, and Simplified Chinese
        # manifest for default display names.
        if not self.manifest_path.exists():
            await bungie_client.fetch_manifest("en")
        if not self.manifest_path_zh.exists():
            try:
                await bungie_client.fetch_manifest("zh-chs")
            except Exception as exc:
                logger.warning(
                    "Simplified Chinese manifest download failed; "
                    "continuing with English-only manifest: %s",
                    exc,
                )

        self._load_from_file()

    def _load_from_file(self) -> None:
        """Open the SQLite manifests and build the combined name index."""
        if not self.manifest_path.exists():
            raise ManifestError(
                f"Manifest not found at {self.manifest_path}. "
                "Run the manifest download first."
            )

        logger.info("ManifestManager._load_from_file: loading from %s", self.manifest_path)

        self._name_index.clear()
        self._hash_index.clear()
        self._english_name_by_hash.clear()
        self._english_type_display_by_hash.clear()
        self._definition_cache.clear()
        self._plug_set_cache.clear()
        self._sandbox_perk_cache.clear()

        # Load English manifest first so names remain searchable as aliases.
        self._conn = sqlite3.connect(str(self.manifest_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._build_name_index(self._conn, language="en")

        # Load Chinese manifest AFTER English — overwrites hash index entries
        # so Chinese names take priority
        if self.manifest_path_zh.exists():
            self._zh_conn = sqlite3.connect(str(self.manifest_path_zh))
            self._zh_conn.row_factory = sqlite3.Row
            self._zh_conn.execute("PRAGMA journal_mode=WAL")
            self._build_name_index(self._zh_conn, language="zh-chs")

    # ── Public API ─────────────────────────────────────────────────

    @staticmethod
    def bucket_name(bucket_hash: int) -> str:
        """Look up bucket display name by hash.

        BUCKET_NAMES uses signed hashes (from manifest), but API returns
        unsigned. Try both.
        """
        name = BUCKET_NAMES.get(bucket_hash)
        if name:
            return name
        # Try converting unsigned → signed
        signed = to_signed(bucket_hash)
        return BUCKET_NAMES.get(signed, f"Bucket({bucket_hash})")

    @staticmethod
    def item_type_name(item_type: int) -> str:
        return ITEM_TYPE_NAMES.get(item_type, f"Type({item_type})")

    # ── Vendor & Milestone definitions ───────────────────────────────
    def close(self) -> None:
        """Close SQLite connections."""
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._zh_conn:
            self._zh_conn.close()
            self._zh_conn = None

    # ── Public query helpers (Phase 1: replace external _conn access) ─
    # ── Seasonal Artifact ─────────────────────────────────────────
