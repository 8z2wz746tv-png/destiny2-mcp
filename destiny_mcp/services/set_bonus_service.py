"""Set bonus service — business logic for armor set lookups.

Extracted from set_tools.py per Rule 1: tools should not contain
business logic.

查不到套装时**抛异常**，不返回 `{"error": ...}`：后者会被上层包进 `ok=true`
的信封里，模型看到的是"成功"。
"""

from __future__ import annotations

from ..exceptions import DefinitionNotFoundError
from ..logging_config import get_logger
from ..manifest import ManifestManager

logger = get_logger(__name__)


class SetBonusService:
    """Business logic for armor set bonus queries."""

    def __init__(self, manifest: ManifestManager) -> None:
        self._manifest = manifest

    def lookup_armor_set(self, query: str) -> dict:
        """Look up an armor set by name or armor piece name.

        Three-phase search:
        1. Exact match on set name
        2. Fuzzy (substring) match on set name
        3. Search for armor items, then cross-reference against set names

        Args:
            query: Set name or armor piece name (Chinese or English).

        Returns:
            Set info dict with set_name, set_hash, armor_count, armor_pieces, perks.
            Armor set info dict.
        """
        all_sets = self._manifest.get_all_set_bonuses()
        matched_set = None

        # Phase 1: exact match
        for set_hash, info in all_sets.items():
            if info["set_name"].lower() == query.lower():
                matched_set = (set_hash, info)
                break

        # Phase 2: fuzzy match
        if not matched_set:
            query_lower = query.lower()
            for set_hash, info in all_sets.items():
                if query_lower in info["set_name"].lower():
                    matched_set = (set_hash, info)
                    break

        # Phase 3: search by armor piece name
        if not matched_set:
            results = self._manifest.search(query, limit=5)
            for item in results:
                if item.get("itemType") == 2:
                    item_name = item.get("name", "").lower()
                    for set_hash, info in all_sets.items():
                        set_name = info.get("set_name", "").lower()
                        if set_name and set_name in item_name:
                            matched_set = (set_hash, info)
                            break
                        if item_name and item_name in set_name:
                            matched_set = (set_hash, info)
                            break
                if matched_set:
                    break

        if not matched_set:
            # 这是 Manifest 定义查询，不是"你账号里的东西没了"：
            # 以前抛 ItemNotFoundError，外层又拼一句「它可能已被分解或移走」，
            # 对从没拥有过这个套装的用户是误导。
            # 消息只给"是什么没找到"，模板由异常类补（避免套两层引号）。
            raise DefinitionNotFoundError(query, "（查的是套装效果。）")

        set_hash, info = matched_set

        perks = [
            {
                "required_count": p.get("required_set_count", 0),
                "name": p.get("perk_name", ""),
                "description": p.get("perk_description", ""),
            }
            for p in info.get("perks", [])
        ]

        armor_pieces = []
        for item_hash in info.get("items", []):
            if isinstance(item_hash, dict):
                item_hash = item_hash.get("itemHash", 0)
            item_def = self._manifest.get_item_definition(item_hash)
            if item_def:
                display = item_def.get("displayProperties") or {}
                armor_pieces.append({
                    "name": display.get("name", ""),
                    "hash": item_hash,
                })

        return {
            "set_name": info["set_name"],
            "set_hash": set_hash,
            "armor_count": len(armor_pieces),
            "armor_pieces": armor_pieces,
            "perks": perks,
        }

    def list_all_set_bonuses(self) -> dict:
        """List all available armor set bonuses.

        Returns:
            {"count": int, "sets": list}
        """
        all_sets = self._manifest.get_all_set_bonuses()

        sets = []
        for set_hash, info in sorted(all_sets.items(), key=lambda x: x[1]["set_name"]):
            perks = []
            for p in info.get("perks", []):
                perks.append({
                    "required_count": p.get("required_set_count", 0),
                    "name": p.get("perk_name", ""),
                    "description": p.get("perk_description", ""),
                })

            sets.append({
                "name": info["set_name"],
                "set_hash": set_hash,
                "item_count": len(info.get("items", [])),
                "perks": perks,
            })

        return {
            "count": len(sets),
            "sets": sets,
        }
