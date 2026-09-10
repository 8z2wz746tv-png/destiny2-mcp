"""清单名称索引与搜索：中英双语索引、三层匹配与模糊匹配。

以 mixin 挂在 ManifestManager 上，通过 self 使用三个索引，调用点不用改。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import sqlite3

from .exceptions import ManifestError
from .manifest_data import BUNGIE_BASE_URL, ITEM_ALIASES, ITEM_TYPE_NAMES
from .utils.hash_utils import to_signed


class SearchIndexMixin:
    """依赖 ManifestManager 的连接与名称索引。"""

    if False:  # pragma: no cover - 仅用于类型说明
        _name_index: dict[str, list[dict]]
        _hash_index: dict[int, dict]
        _english_name_by_hash: dict[int, str]
        _english_type_display_by_hash: dict[int, str]

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
