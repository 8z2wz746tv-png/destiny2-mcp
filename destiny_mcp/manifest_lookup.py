"""通用定义查询与命名解析：白名单表查询、名称占位、按名搜索、收藏品反查。

以 mixin 挂在 ManifestManager 上。`get_definition` 与 `iter_definitions` 共用
ItemDefinitionMixin 的 `_VALID_TABLES` 白名单，前者委托它的 `_query_json`。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json

from .logging_config import get_logger

logger = get_logger(__name__)


class DefinitionLookupMixin:
    """非物品表的只读查询与名称解析。"""

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
