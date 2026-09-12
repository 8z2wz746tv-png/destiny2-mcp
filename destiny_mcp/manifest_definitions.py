"""物品定义查询：hash 索引、原始 JSON 定义与中英连接回退。

以 mixin 挂在 ManifestManager 上，通过 self 使用连接、hash 索引与定义缓存。
`get_item_definition_by_name` 需要 `self.search`，依赖 SearchIndexMixin 已在 MRO 里。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json
import sqlite3

from .logging_config import get_logger
from .utils.hash_utils import to_signed

logger = get_logger(__name__)


class ItemDefinitionMixin:
    """物品信息的两种粒度：索引摘要与完整定义。"""

    _VALID_TABLES = frozenset({
        "DestinyVendorDefinition",
        "DestinyProgressionDefinition",
        "DestinyDamageTypeDefinition",
        "DestinyBreakerTypeDefinition",
        "DestinyStatGroupDefinition",
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
