"""绑定单侧连接或按 JSON 子串扫描的物品查询。

与 ItemDefinitionMixin 的 `_query_json` 不同，这里的方法**不**做中英回退：
`get_english_name` 只读英文库，`get_localized_definition` 只读中文库，
另外两个按 JSON 子串扫描（因此依赖紧凑序列化）。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json
import sqlite3

from .utils.hash_utils import to_signed


class ItemQueryMixin:
    """按连接或 JSON 子串直接查询物品表。"""

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
