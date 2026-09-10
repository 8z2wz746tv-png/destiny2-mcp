"""Destiny Manifest manager — item name lookups and search.

Downloads the Destiny manifest (SQLite database) and indexes item definitions
for fast lookup by name (Chinese and English) or by item hash.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING


from . import config
from .exceptions import CharacterNotFoundError, ManifestError
from .logging_config import get_logger
from .manifest_armor import ArmorCatalogMixin
from .manifest_catalog import ItemCatalogMixin
from .manifest_definitions import ItemDefinitionMixin
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
