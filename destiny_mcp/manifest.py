"""Destiny Manifest manager — item name lookups and search.

Downloads the Destiny manifest (SQLite database) and indexes item definitions
for fast lookup by name (Chinese and English) or by item hash.
"""

from __future__ import annotations

import json
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import config
from .exceptions import CharacterNotFoundError, ManifestError
from .logging_config import get_logger
from .manifest_armor import ArmorCatalogMixin
from .utils.hash_utils import to_signed

logger = get_logger(__name__)

if TYPE_CHECKING:
    from .bungie_client import BungieClient

# Bungie CDN base URL for item icons
BUNGIE_BASE_URL = "https://www.bungie.net"

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
CHARACTER_CLASS_MAP: dict[str, int] = {
    "titan": 0,
    "hunter": 1,
    "warlock": 2,
    "泰坦": 0,
    "猎人": 1,
    "术士": 2,
}

# Item type names
ITEM_TYPE_NAMES: dict[int, str] = {
    0: "None",
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


class ManifestManager(ArmorCatalogMixin):
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

    def _build_name_index(self, conn: sqlite3.Connection, *, language: str) -> None:
        """Build in-memory name index from a manifest connection.
        Merges entries into self._name_index (supports loading multiple languages).
        Also populates self._hash_index (Chinese loaded last takes priority).
        """
        cursor = conn.execute(
            "SELECT id, json FROM DestinyInventoryItemDefinition"
        )
        for row in cursor:
            item_id = row["id"]
            try:
                data = json.loads(row["json"])
            except json.JSONDecodeError:
                continue

            display = data.get("displayProperties") or {}
            name = display.get("name", "")
            if not name:
                continue

            key = name.lower().strip()
            item_type = data.get("itemType", 0)
            tier = (data.get("inventory") or {}).get("tierType", 0)
            bucket_type_hash = (data.get("inventory") or {}).get("bucketTypeHash", 0)
            class_type = data.get("classType", -1)
            signed_item_id = to_signed(item_id)
            if language == "en":
                self._english_name_by_hash[item_id] = name
                self._english_name_by_hash[signed_item_id] = name
                display_type = str(data.get("itemTypeDisplayName") or "")
                self._english_type_display_by_hash[item_id] = display_type
                self._english_type_display_by_hash[signed_item_id] = display_type
            english_name = self._english_name_by_hash.get(
                item_id,
                self._english_name_by_hash.get(signed_item_id, name if language == "en" else ""),
            )

            entry = {
                "itemHash": item_id,
                "name": name,
                "nameEn": english_name,
                "itemType": item_type,
                "itemTypeName": ITEM_TYPE_NAMES.get(item_type, f"Type({item_type})"),
                "itemTypeNameDisplay": data.get("itemTypeDisplayName", ""),
                "tier": tier,
                "icon": (BUNGIE_BASE_URL + display["icon"]) if display.get("icon") else "",
                "classType": class_type,
                "damageType": data.get("defaultDamageType", data.get("damageType", 0)),
                "ammoType": (data.get("equippingBlock") or {}).get("ammoType", 0),
                "bucketTypeHash": bucket_type_hash,
                "language": language,
            }

            if key not in self._name_index:
                self._name_index[key] = []
            self._name_index[key].append(entry)

            # Hash index — Chinese manifest loaded last overwrites English
            self._hash_index[item_id] = entry

    def _canonical_item_entry(self, item: dict) -> dict:
        """Return the preferred-language entry for a search hit."""
        item_hash = item.get("itemHash", 0)
        signed_hash = to_signed(item_hash)
        canonical = self._hash_index.get(item_hash) or self._hash_index.get(signed_hash)
        if not canonical:
            canonical = item
        result = dict(canonical)
        if not result.get("nameEn"):
            result["nameEn"] = (
                self._english_name_by_hash.get(item_hash)
                or self._english_name_by_hash.get(signed_hash)
                or item.get("nameEn", "")
            )
        result["itemTypeNameDisplayEn"] = (
            self._english_type_display_by_hash.get(item_hash)
            or self._english_type_display_by_hash.get(signed_hash)
            or item.get("itemTypeNameDisplayEn", "")
        )
        return result

    # ── Public API ─────────────────────────────────────────────────

    def search(self, query: str, *, limit: int = 20) -> list[dict]:
        """Fuzzy search items by name (Chinese or English).

        Supports community nicknames via ITEM_ALIASES mapping (e.g. 千语 → 千言萬語).

        Search priority:
          1. Exact name match (highest priority)
          2. Starts-with match (query is prefix of item name)
          3. Substring match (lowest priority)

        Within each tier, sorted by tier (exotic first) then by name.

        Args:
            query: Partial or full item name.
            limit: Max results to return; 0 returns all matches.

        Returns:
            List of item dicts with keys: itemHash, name, itemType, itemTypeName, tier, icon.
        """
        if not self._name_index:
            raise ManifestError("Manifest not loaded. Call ensure_loaded() first.")

        q = query.lower().strip()
        if not q:
            return []

        # Expand aliases: also search using official names
        search_keys = [q]
        if q in ITEM_ALIASES:
            for alias_target in ITEM_ALIASES[q]:
                search_keys.append(alias_target.lower())

        # Three-tier matching
        exact: list[dict] = []      # exact name match
        prefix: list[dict] = []     # query is prefix of item name
        substring: list[dict] = []  # query is substring of item name

        seen: set[int] = set()

        for key in search_keys:
            # Exact match
            if key in self._name_index:
                for item in self._name_index[key]:
                    h = item["itemHash"]
                    if h not in seen:
                        seen.add(h)
                        exact.append(self._canonical_item_entry(item))

            # Prefix + substring match
            for idx_key, items in self._name_index.items():
                if idx_key in search_keys:
                    continue
                if key in idx_key:
                    for item in items:
                        h = item["itemHash"]
                        if h not in seen:
                            seen.add(h)
                            canonical = self._canonical_item_entry(item)
                            if idx_key.startswith(key):
                                prefix.append(canonical)
                            else:
                                substring.append(canonical)

        # Sort each tier by tier (highest first) then name
        for group in (exact, prefix, substring):
            group.sort(key=lambda x: (-x["tier"], x["name"]))

        # Combine: exact → prefix → substring
        combined = exact + prefix + substring
        return combined if limit <= 0 else combined[:limit]

    def search_fuzzy(
        self,
        query: str,
        *,
        limit: int = 20,
        min_score: float = 0.65,
        item_type: int | None = None,
        tier: int | None = None,
        class_type: int | None = None,
    ) -> list[dict]:
        """Search item names with typo tolerance; callers must confirm hits."""
        if not self._name_index:
            raise ManifestError("Manifest not loaded. Call ensure_loaded() first.")

        def normalize(value: str) -> str:
            return "".join(char for char in value.casefold() if char.isalnum())

        normalized_query = normalize(query)
        if not normalized_query:
            return []

        search_terms = [normalized_query]
        search_terms.extend(
            normalized
            for target in ITEM_ALIASES.get(query.casefold().strip(), [])
            if (normalized := normalize(target))
        )
        matches: dict[int, dict] = {}
        for indexed_name, items in self._name_index.items():
            eligible = [
                item
                for item in items
                if (item_type is None or item.get("itemType") == item_type)
                and (tier is None or item.get("tier") == tier)
                and (
                    class_type is None
                    or item.get("classType", -1) in {-1, class_type}
                )
            ]
            if not eligible:
                continue
            normalized_name = normalize(indexed_name)
            if not normalized_name:
                continue
            score = max(
                SequenceMatcher(None, term, normalized_name).ratio()
                for term in search_terms
            )
            if score < min_score:
                continue
            for item in eligible:
                canonical = self._canonical_item_entry(item)
                item_hash = canonical["itemHash"]
                previous = matches.get(item_hash)
                if previous is None or score > previous["match_score"]:
                    canonical["match_score"] = round(score, 3)
                    matches[item_hash] = canonical

        return sorted(
            matches.values(),
            key=lambda item: (
                -item["match_score"],
                -item.get("tier", 0),
                item.get("name", ""),
            ),
        )[:limit]

    def search_by_type_name(
        self, type_name: str, *, limit: int = 100
    ) -> list[dict]:
        """Search items by weapon/armor type display name.

        Args:
            type_name: Type display name (e.g. '微型冲锋枪', '手炮', '自动步枪').
            limit: Max results; 0 returns all matches.

        Returns:
            List of item dicts matching the type.
        """
        if not self._name_index:
            raise ManifestError("Manifest not loaded. Call ensure_loaded() first.")

        q = type_name.lower().strip()
        if not q:
            return []
        results: list[dict] = []

        for items in self._name_index.values():
            for item in items:
                display_type = item.get("itemTypeNameDisplay", "").lower()
                if q in display_type or q in item.get("itemTypeName", "").lower():
                    results.append(item)

        # Deduplicate
        seen: set[int] = set()
        unique: list[dict] = []
        for item in results:
            h = item["itemHash"]
            if h not in seen:
                seen.add(h)
                unique.append(self._canonical_item_entry(item))

        unique.sort(key=lambda x: (-x["tier"], x["name"]))
        return unique if limit <= 0 else unique[:limit]

    def get_item_name(self, item_hash: int) -> str:
        """Look up an item name by hash. Returns hex hash string if unknown."""
        if not self._hash_index:
            return f"#{item_hash}"

        # Manifest stores signed 32-bit hashes; API may return unsigned.
        signed_hash = to_signed(item_hash)
        entry = self._hash_index.get(item_hash) or self._hash_index.get(signed_hash)
        return entry["name"] if entry else f"#{item_hash}"

    def get_item_info(self, item_hash: int) -> dict | None:
        """Get full item definition for a given hash."""
        signed_hash = to_signed(item_hash)
        return self._hash_index.get(item_hash) or self._hash_index.get(signed_hash)

    def get_item_description(self, item_hash: int) -> str:
        """Return localized display text from the full inventory definition."""
        definition = self.get_item_definition(item_hash)
        if not isinstance(definition, dict):
            return ""
        display = definition.get("displayProperties")
        if not isinstance(display, dict):
            return ""
        description = display.get("description")
        return description if isinstance(description, str) else ""

    def _query_json(self, table: str, item_hash: int) -> dict | None:
        # VERSION: 2026-06-16-v2 — Chinese-first perk lookup
        """Query a JSON table from manifest, trying Chinese first then English."""
        if table not in self._VALID_TABLES:
            return None
        signed_hash = to_signed(item_hash)
        # Try Chinese manifest first (preferred language)
        for conn in (self._zh_conn, self._conn):
            if not conn:
                continue
            for h in (item_hash, signed_hash):
                try:
                    cur = conn.execute(
                        f"SELECT json FROM {table} WHERE id = ?", (h,)
                    )
                    row = cur.fetchone()
                    if row:
                        return json.loads(row["json"])
                except (sqlite3.Error, json.JSONDecodeError) as e:
                    logger.debug("Query %s hash=%s failed: %s", table, h, e)
                    continue
        return None

    def _query_json_from_conn(self, table: str, item_hash: int, conn) -> dict | None:
        """Query a JSON table from a specific manifest connection."""
        if not conn:
            return None
        if table not in self._VALID_TABLES:
            return None
        signed_hash = to_signed(item_hash)
        for h in (item_hash, signed_hash):
            try:
                cur = conn.execute(
                    f"SELECT json FROM {table} WHERE id = ?", (h,)
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row["json"])
            except (sqlite3.Error, json.JSONDecodeError) as e:
                logger.debug("Query %s hash=%s from conn failed: %s", table, h, e)
                continue
        return None

    def get_item_definition(self, item_hash: int) -> dict | None:
        """Get the full raw JSON definition for an item from the manifest.

        Unlike get_item_info() which returns the indexed summary, this returns
        the complete definition dict (sockets, stats, plug info, etc.).

        Results are cached in memory — repeated lookups for the same hash
        hit the cache instead of querying SQLite.
        """
        if item_hash in self._definition_cache:
            return self._definition_cache[item_hash]

        data = self._query_json("DestinyInventoryItemDefinition", item_hash)
        if data:
            self._definition_cache[item_hash] = data
            signed_hash = to_signed(item_hash)
            self._definition_cache[signed_hash] = data
        return data

    def get_item_definition_by_name(self, item_name: str) -> dict | None:
        """Get the full raw JSON definition for an item by name.

        Searches for the item by name, then returns the full definition.
        Returns None if not found.
        """
        results = self.search(item_name, limit=1)
        if not results:
            return None
        item_hash = results[0]["itemHash"]
        return self.get_item_definition(item_hash)

    def get_exotic_armor_by_class(self, class_name: str) -> list[dict]:
        """Get all exotic armor for a specific class.

        Args:
            class_name: 'hunter', 'warlock', 'titan' (or Chinese: 猎人, 术士, 泰坦)

        Returns:
            List of item dicts with name, itemHash, slot, etc.
        """
        class_type = CHARACTER_CLASS_MAP.get(class_name.lower(), -1)
        if class_type < 0:
            return []

        # 用名字去重，保留第一个
        seen_names = set()
        results = []
        for item_hash, item_info in self._hash_index.items():
            if (item_info.get('itemType') == 2 and      # 护甲
                item_info.get('tier') == 6 and          # 异域
                item_info.get('classType') == class_type):
                name = item_info.get('name', '')
                if name not in seen_names:
                    seen_names.add(name)
                    results.append(item_info)

        # 按名称排序
        results.sort(key=lambda x: x.get('name', ''))
        return results

    def list_weapon_catalog(
        self,
        weapon_type: str = "",
        *,
        weapon_name: str = "",
        limit: int = 0,
    ) -> list[dict]:
        """List unique weapon definitions from the loaded Manifest.

        This is separate from profile queries and therefore includes weapons
        the configured player does not own. ``limit=0`` returns all matches.
        """
        if not self._name_index:
            raise ManifestError("Manifest not loaded. Call ensure_loaded() first.")

        type_query = weapon_type.strip().casefold()
        name_query = weapon_name.strip().casefold()
        seen_hashes: set[int] = set()
        results: list[dict] = []

        for raw_hash, item in self._hash_index.items():
            if item.get("itemType") != 3:
                continue
            canonical = self._canonical_item_entry(item)
            item_hash = int(canonical.get("itemHash", raw_hash))
            signed_hash = to_signed(item_hash)
            if item_hash in seen_hashes or signed_hash in seen_hashes:
                continue
            seen_hashes.update((item_hash, signed_hash))

            display_types = (
                str(canonical.get("itemTypeNameDisplay", "")),
                str(canonical.get("itemTypeNameDisplayEn", "")),
            )
            if type_query and not any(
                self._weapon_type_matches(type_query, display_type)
                for display_type in display_types
            ):
                continue
            names = (str(canonical.get("name", "")), str(canonical.get("nameEn", "")))
            if name_query and not any(name_query in name.casefold() for name in names):
                continue
            results.append(canonical)

        results.sort(key=lambda value: (-value.get("tier", 0), value.get("name", "")))
        return results if limit <= 0 else results[:limit]

    @staticmethod
    def _weapon_type_matches(query: str, display_type: str) -> bool:
        """Match common Chinese/English weapon-type names."""
        candidate = display_type.strip().casefold()
        if not candidate:
            return False
        if query in candidate or candidate in query:
            return True
        aliases = {
            "火箭筒": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "火箭发射器": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "rocket launcher": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "rocket launchers": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
        }
        return any(alias in candidate or candidate in alias for alias in aliases.get(query, {query}))

    def list_items(
        self,
        item_type: str = "",
        tier: str = "",
        class_name: str = "",
        damage_type: str = "",
        ammo_type: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """List items from manifest with filters.

        Args:
            item_type: 'weapon', 'armor', 'mod', 'ghost', 'ship', 'sparrow', etc.
            tier: 'exotic', 'legendary', 'rare', 'common', 'uncommon'
            class_name: 'hunter', 'warlock', 'titan' (for armor)
            damage_type: 'kinetic', 'solar', 'arc', 'void', 'stasis', 'strand'
            ammo_type: 'primary', 'special', 'heavy'
            limit: Max results to return

        Returns:
            List of item dicts with name, itemHash, etc.
        """
        # 类型映射
        type_map = {
            "weapon": 3, "武器": 3,
            "armor": 2, "护甲": 2,
            "mod": 19, "模组": 19,
            "ghost": 24, "机灵": 24,
            "ship": 21, "飞船": 21,
            "sparrow": 22, "快雀": 22,
            "emblem": 14, "徽章": 14,
            "consumable": 9, "消耗品": 9,
            "material": 10, "材料": 10,
            "subclass": 16, "子职业": 16,
        }

        # 稀有度映射
        tier_map = {
            "exotic": 6, "异域": 6,
            "legendary": 5, "传说": 5,
            "rare": 4, "稀有": 4,
            "uncommon": 3, "罕见": 3,
            "common": 2, "普通": 2,
        }

        # 职业映射
        class_type = CHARACTER_CLASS_MAP.get(class_name.lower(), -1) if class_name else -1

        # 伤害类型映射
        damage_map = {
            "kinetic": 1, "动能": 1,
            "solar": 2, "烈日": 2,
            "arc": 3, "电弧": 3,
            "void": 4, "虚空": 4,
            "stasis": 5, "冰影": 5,
            "strand": 6, "编织": 6,
        }

        # 弹药类型映射
        ammo_map = {
            "primary": 1, "主要": 1,
            "special": 2, "特殊": 2,
            "heavy": 3, "威能": 3,
        }

        target_type = type_map.get(item_type.lower(), -1) if item_type else -1
        target_tier = tier_map.get(tier.lower(), -1) if tier else -1
        target_damage = damage_map.get(damage_type.lower(), -1) if damage_type else -1
        target_ammo = ammo_map.get(ammo_type.lower(), -1) if ammo_type else -1

        # 用名字去重
        seen_names = set()
        results = []

        for item_hash, item_info in self._hash_index.items():
            # 类型过滤
            if target_type >= 0 and item_info.get('itemType') != target_type:
                continue

            # 稀有度过滤
            if target_tier >= 0 and item_info.get('tier') != target_tier:
                continue

            # 职业过滤（仅护甲）
            if class_type >= 0 and item_info.get('itemType') == 2:
                if item_info.get('classType') != class_type:
                    continue

            # 伤害类型过滤（仅武器）
            if target_damage >= 0 and item_info.get('itemType') == 3:
                if item_info.get('damageType') != target_damage:
                    continue

            # 弹药类型过滤（仅武器）
            if target_ammo >= 0 and item_info.get('itemType') == 3:
                if item_info.get('ammoType') != target_ammo:
                    continue

            name = item_info.get('name', '')
            if not name or name in seen_names:
                continue

            seen_names.add(name)
            results.append(item_info)

            if len(results) >= limit:
                break

        # 按名称排序
        results.sort(key=lambda x: x.get('name', ''))
        return results

    def get_plug_set_plugs(self, plug_set_hash: int) -> list[dict] | None:
        """Get all plugs in a plug set (for weapon perk pools).

        Returns a list of dicts with at least 'plugItemHash' and
        'plugCategoryIdentifier' keys.
        """
        if plug_set_hash in self._plug_set_cache:
            return self._plug_set_cache[plug_set_hash]

        data = self._query_json("DestinyPlugSetDefinition", plug_set_hash)
        if not data:
            return None

        raw_plugs = data.get("reusablePlugItems", [])
        enriched: list[dict] = []
        for p in raw_plugs:
            ph = p.get("plugItemHash")
            if not ph:
                continue
            item_def = self.get_item_definition(ph)
            name = ""
            cat_id = ""
            if item_def:
                name = (item_def.get("displayProperties") or {}).get("name", "")
                cat_id = (item_def.get("plug") or {}).get("plugCategoryIdentifier", "")
            enriched.append({
                "plugItemHash": ph,
                "name": name,
                "plugCategoryIdentifier": cat_id,
            })
        self._plug_set_cache[plug_set_hash] = enriched
        return enriched

    def get_sandbox_perk_description(self, perk_hash: int) -> dict | None:
        """Look up a sandbox perk's name and description.

        Used for weapon perk effect text.
        """
        if perk_hash in self._sandbox_perk_cache:
            return self._sandbox_perk_cache[perk_hash]

        data = self._query_json("DestinySandboxPerkDefinition", perk_hash)
        if not data:
            return None

        result = {
            "name": data.get("displayProperties", {}).get("name", ""),
            "description": data.get("displayProperties", {}).get("description", ""),
        }
        self._sandbox_perk_cache[perk_hash] = result
        return result

    def get_plug_category_identifier(self, plug_hash: int) -> str | None:
        """Look up a plug's plugCategoryIdentifier from the manifest.

        Used to categorize weapon sockets (barrel, magazine, perk, etc.).
        """
        if not self._conn:
            return None

        signed_hash = to_signed(plug_hash)
        for h in (plug_hash, signed_hash):
            cur = self._conn.execute(
                "SELECT json FROM DestinyInventoryItemDefinition WHERE id = ?", (h,)
            )
            row = cur.fetchone()
            if row:
                try:
                    data = json.loads(row["json"])
                    return data.get("plug", {}).get("plugCategoryIdentifier")
                except json.JSONDecodeError as e:
                    logger.debug("JSON decode failed for plug hash=%s: %s", h, e)
                    continue
        return None

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

    _VALID_TABLES = frozenset({
        "DestinyVendorDefinition",
        "DestinyMilestoneDefinition",
        "DestinyActivityDefinition",
        "DestinyActivityTypeDefinition",
        "DestinyPlugSetDefinition",
        "DestinyInventoryItemDefinition",
        "DestinySandboxPerkDefinition",
        "DestinyStatDefinition",
        "DestinyEquipmentSlotDefinition",
        "DestinyItemCategoryDefinition",
        "DestinyCollectibleDefinition",
        "DestinyPresentationNodeDefinition",
        "DestinyLoadoutColorDefinition",
        "DestinyLoadoutIconDefinition",
        "DestinyLoadoutNameDefinition",
    })

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        """Generic manifest definition lookup by table and hash.

        Tries Chinese manifest first, then English, and accepts both signed
        and unsigned hash variants.

        Args:
            table: Manifest table name (e.g. 'DestinyVendorDefinition').
            hash_id: Definition hash.

        Returns:
            Parsed JSON dict or None.
        """
        if not self._conn and not self._zh_conn:
            return None
        if table not in self._VALID_TABLES:
            logger.warning("get_definition: unknown table '%s'", table)
            return None
        return self._query_json(table, hash_id)

    def get_vendor_definition(self, vendor_hash: int) -> dict | None:
        """Look up vendor definition from DestinyVendorDefinition."""
        return self.get_definition("DestinyVendorDefinition", vendor_hash)

    def get_vendor_name(self, vendor_hash: int) -> str:
        """Look up vendor display name."""
        defn = self.get_vendor_definition(vendor_hash)
        if defn:
            return (defn.get("displayProperties") or {}).get("name", f"Vendor({vendor_hash})")
        return f"Vendor({vendor_hash})"

    def get_milestone_definition(self, milestone_hash: int) -> dict | None:
        """Look up milestone definition from DestinyMilestoneDefinition."""
        return self.get_definition("DestinyMilestoneDefinition", milestone_hash)

    def get_milestone_name(self, milestone_hash: int) -> str:
        """Look up milestone display name."""
        defn = self.get_milestone_definition(milestone_hash)
        if defn:
            return (defn.get("displayProperties") or {}).get("name", f"Milestone({milestone_hash})")
        return f"Milestone({milestone_hash})"

    def get_activity_name(self, activity_hash: int) -> str:
        """Look up activity name from DestinyActivityDefinition."""
        defn = self.get_definition("DestinyActivityDefinition", activity_hash)
        if defn:
            return (defn.get("displayProperties") or {}).get("name", f"Activity({activity_hash})")
        return f"Activity({activity_hash})"

    def get_activity_type_name(self, activity_type_hash: int) -> str:
        """Look up activity type name from DestinyActivityTypeDefinition."""
        defn = self.get_definition("DestinyActivityTypeDefinition", activity_type_hash)
        if defn:
            return (defn.get("displayProperties") or {}).get("name", "")
        return ""

    def iter_definitions(self, table: str, *, limit: int = 1000) -> list[dict]:
        """Return definitions from a manifest table.

        This is intentionally limited and table-whitelisted because it is used
        by user-facing search helpers, not as a general SQL escape hatch.
        """
        if table not in self._VALID_TABLES:
            logger.warning("iter_definitions: unknown table '%s'", table)
            return []
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        cur = conn.execute(f"SELECT id, json FROM {table} LIMIT ?", (max(1, limit),))
        results: list[dict] = []
        for row in cur:
            try:
                data = json.loads(row["json"])
            except (json.JSONDecodeError, KeyError):
                continue
            data.setdefault("hash", row["id"])
            results.append(data)
        return results

    def search_definitions_by_name(
        self,
        table: str,
        query: str = "",
        *,
        limit: int = 20,
        scan_limit: int = 5000,
    ) -> list[dict]:
        """Search a definition table by display name/description."""
        q = query.strip().lower()
        matches: list[dict] = []
        for data in self.iter_definitions(table, limit=scan_limit):
            display = data.get("displayProperties") or {}
            name = str(display.get("name", ""))
            description = str(display.get("description", ""))
            if q and q not in name.lower() and q not in description.lower():
                continue
            matches.append(data)
            if len(matches) >= limit:
                break
        return matches

    def find_collectible_by_item_hash(self, item_hash: int) -> dict | None:
        """Find a collectible definition that points to an inventory item hash."""
        for collectible in self.iter_definitions(
            "DestinyCollectibleDefinition",
            limit=20000,
        ):
            if int(collectible.get("itemHash", 0)) == int(item_hash):
                return collectible
        return None

    def close(self) -> None:
        """Close SQLite connections."""
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._zh_conn:
            self._zh_conn.close()
            self._zh_conn = None

    # ── Public query helpers (Phase 1: replace external _conn access) ─

    def get_english_name(self, item_hash: int) -> str:
        """Get the English display name for an item hash.

        Queries the English manifest connection specifically (not the
        Chinese-first fallback that _query_json uses).

        Returns empty string if not found.
        """
        if not self._conn:
            return ""
        signed_hash = to_signed(item_hash)
        for h in (item_hash, signed_hash):
            try:
                cur = self._conn.execute(
                    "SELECT json FROM DestinyInventoryItemDefinition WHERE id = ?",
                    (h,),
                )
                row = cur.fetchone()
                if row:
                    data = json.loads(row["json"])
                    return (data.get("displayProperties") or {}).get("name", "")
            except (sqlite3.Error, json.JSONDecodeError):
                continue
        return ""

    def find_items_by_plug_category(self, keyword: str) -> list[dict]:
        """Find items whose plugCategoryIdentifier contains *keyword*.

        Returns a list of dicts with keys: hash, name, plugCategoryIdentifier,
        displayProperties.
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        cur = conn.execute(
            "SELECT id, json FROM DestinyInventoryItemDefinition "
            "WHERE json LIKE ? OR json LIKE ?",
            (f"%{keyword}%", f"%{keyword}%"),
        )
        results: list[dict] = []
        for row in cur:
            try:
                data = json.loads(row["json"])
            except (json.JSONDecodeError, KeyError):
                continue
            plug_cat = (data.get("plug") or {}).get("plugCategoryIdentifier", "")
            if keyword not in plug_cat:
                continue
            results.append({
                "hash": data.get("hash", row["id"]),
                "name": (data.get("displayProperties") or {}).get("name", ""),
                "plugCategoryIdentifier": plug_cat,
                "displayProperties": data.get("displayProperties") or {},
            })
        return results

    def find_items_by_type(
        self, item_type: int, *, extra_json_like: str = ""
    ) -> list[dict]:
        """Find items by itemType, optionally filtered by a JSON substring.

        Returns full definition dicts.
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        if extra_json_like:
            cur = conn.execute(
                "SELECT id, json FROM DestinyInventoryItemDefinition "
                "WHERE json LIKE ? AND json LIKE ?",
                (f'%"itemType":{item_type}%', f"%{extra_json_like}%"),
            )
        else:
            cur = conn.execute(
                "SELECT id, json FROM DestinyInventoryItemDefinition "
                "WHERE json LIKE ?",
                (f'%"itemType":{item_type}%',),
            )

        results: list[dict] = []
        for row in cur:
            try:
                data = json.loads(row["json"])
            except (json.JSONDecodeError, KeyError):
                continue
            results.append(data)
        return results

    def get_localized_definition(self, table: str, item_hash: int) -> dict | None:
        """Query a definition from the localized (Chinese) manifest only.

        Unlike _query_json which tries Chinese first then English fallback,
        this queries the Chinese manifest exclusively and returns None if
        the Chinese manifest is not loaded.
        """
        if not self._zh_conn:
            return None
        if table not in self._VALID_TABLES:
            return None
        signed_hash = to_signed(item_hash)
        for h in (item_hash, signed_hash):
            try:
                cur = self._zh_conn.execute(
                    f"SELECT json FROM {table} WHERE id = ?", (h,)
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row["json"])
            except (sqlite3.Error, json.JSONDecodeError):
                continue
        return None

    # ── Seasonal Artifact ─────────────────────────────────────────

    def get_all_artifacts(self) -> list[dict]:
        """获取所有可用的赛季神器列表。

        Returns:
            [
                {
                    "name": "好奇之器",
                    "hash": -1600062152,
                    "description": "...",
                },
                ...
            ]
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        # 查询 bucket=1506418338 的神器物品
        # 注意：有些物品名称为空，需要过滤掉
        cur = conn.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)

        results = []
        seen_names = set()
        for row in cur:
            data = json.loads(row["json"])
            name = (data.get("displayProperties") or {}).get("name", "")
            desc = (data.get("displayProperties") or {}).get("description", "")

            # 跳过空名称或重复名称
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            results.append({
                "name": name,
                "hash": row["id"],
                "description": desc[:100] if desc else "",
            })

        # 按名称排序
        results.sort(key=lambda x: x["name"])
        return results

    def get_artifact_by_name(self, name: str) -> dict | None:
        """根据名称模糊匹配赛季神器。

        Args:
            name: 神器名称（支持模糊匹配）

        Returns:
            {"name", "hash", "description"} 或 None
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return None

        # 查询 bucket=1506418338 的神器物品
        cur = conn.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)

        # 先尝试精确匹配
        for row in cur:
            data = json.loads(row["json"])
            artifact_name = (data.get("displayProperties") or {}).get("name", "")
            # 跳过空名称
            if not artifact_name:
                continue
            if name == artifact_name:
                desc = (data.get("displayProperties") or {}).get("description", "")
                return {
                    "name": artifact_name,
                    "hash": row["id"],
                    "description": desc,
                }

        # 模糊匹配
        cur.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)
        for row in cur:
            data = json.loads(row["json"])
            artifact_name = (data.get("displayProperties") or {}).get("name", "")
            # 跳过空名称
            if not artifact_name:
                continue
            if name in artifact_name or artifact_name in name:
                desc = (data.get("displayProperties") or {}).get("description", "")
                return {
                    "name": artifact_name,
                    "hash": row["id"],
                    "description": desc,
                }

        return None

    def get_current_artifact(self) -> dict | None:
        """获取最新的赛季神器（按 hash 排序取最新）。

        Returns:
            {
                "name": "好奇之器",
                "description": "...",
                "hash": -1400744370,
                "tiers": [
                    {
                        "tier_hash": 3144670121,
                        "display_title": "1阶",
                        "min_unlock_points": 0,
                        "mods": [
                            {"name": "xxx", "hash": 123456, "description": "..."},
                            ...
                        ]
                    },
                    ...
                ]
            }
            或 None（如果 manifest 未加载）
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return None

        # 查询所有赛季神器（按 hash 排序取最新）
        cur = conn.execute(
            "SELECT id, json FROM DestinyArtifactDefinition ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None

        data = json.loads(row["json"])
        return self._parse_artifact(row["id"], data)

    def get_artifact_by_hash(self, artifact_hash: int) -> dict | None:
        """根据 hash 获取赛季神器详情。

        Args:
            artifact_hash: 神器的 hash

        Returns:
            同 get_current_artifact 的返回格式，或 None
        """
        from .utils.hash_utils import to_signed

        conn = self._zh_conn or self._conn
        if not conn:
            return None

        signed_hash = to_signed(artifact_hash)
        cur = conn.execute(
            "SELECT id, json FROM DestinyArtifactDefinition WHERE id = ?",
            (signed_hash,)
        )
        row = cur.fetchone()
        if not row:
            return None

        data = json.loads(row["json"])
        return self._parse_artifact(row["id"], data)

    def _parse_artifact(self, artifact_hash: int, data: dict) -> dict:
        """解析赛季神器数据。"""
        from .utils.hash_utils import to_signed

        name = (data.get("displayProperties") or {}).get("name", "")
        desc = (data.get("displayProperties") or {}).get("description", "")

        tiers = []
        for tier in data.get("tiers", []):
            tier_hash = tier.get("tierHash", 0)
            display_title = tier.get("displayTitle", "")
            min_points = tier.get("minimumUnlockPointsUsedRequirement", 0)

            mods = []
            for item in tier.get("items", []):
                item_hash = item.get("itemHash", 0)
                # artifact definition 里的 hash 是 unsigned，需要转换为 signed 查询
                signed_hash = to_signed(item_hash)
                item_def = self.get_item_definition(signed_hash)
                if item_def:
                    mod_name = (item_def.get("displayProperties") or {}).get("name", "")
                    mod_desc = (item_def.get("displayProperties") or {}).get("description", "")

                    # 如果描述为空，尝试从 perks 获取
                    if not mod_desc:
                        perk_descs = []
                        for p in item_def.get("perks", []):
                            perk_hash = p.get("perkHash", 0)
                            if perk_hash:
                                perk_info = self.get_sandbox_perk_description(perk_hash)
                                if perk_info:
                                    perk_desc = perk_info.get("description", "")
                                    if perk_desc:
                                        perk_descs.append(perk_desc)
                        if perk_descs:
                            mod_desc = "; ".join(perk_descs)

                    mods.append({
                        "name": mod_name,
                        "hash": item_hash,  # 返回 unsigned hash 给用户
                        "description": mod_desc,
                    })

            tiers.append({
                "tier_hash": tier_hash,
                "display_title": display_title,
                "min_unlock_points": min_points,
                "mods": mods,
            })

        return {
            "name": name,
            "description": desc,
            "hash": artifact_hash,
            "tiers": tiers,
        }

    def get_artifact_mod_details(self, mod_hash: int) -> dict | None:
        """获取赛季神器模组的详细信息。

        Args:
            mod_hash: 模组的 hash

        Returns:
            {"name", "hash", "description", "perks": [{"name", "description"}]}
            或 None
        """
        item_def = self.get_item_definition(mod_hash)
        if not item_def:
            return None

        name = (item_def.get("displayProperties") or {}).get("name", "")
        desc = (item_def.get("displayProperties") or {}).get("description", "")

        # 获取 perk 效果
        perks = []
        for p in item_def.get("perks", []):
            perk_hash = p.get("perkHash", 0)
            if perk_hash:
                perk_info = self.get_sandbox_perk_description(perk_hash)
                if perk_info:
                    perks.append({
                        "name": perk_info.get("name", ""),
                        "description": perk_info.get("description", ""),
                    })

        # 如果主描述为空，用 perks 描述组合
        if not desc and perks:
            desc = "; ".join(p["description"] for p in perks if p["description"])

        return {
            "name": name,
            "hash": mod_hash,
            "description": desc,
            "perks": perks,
        }
