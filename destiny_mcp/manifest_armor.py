"""护甲模组与套装加成：ManifestManager 的护甲域。

以 mixin 拆出，方法仍然通过 self 使用连接与通用查询，调用点不用改。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from .logging_config import get_logger
from .utils.hash_utils import to_signed

if TYPE_CHECKING:
    import sqlite3 as sqlite3_types

logger = get_logger(__name__)


class ArmorCatalogMixin:
    """依赖 ManifestManager 提供的连接与通用查询。"""

    _conn: "sqlite3_types.Connection | None"
    _zh_conn: "sqlite3_types.Connection | None"

    if TYPE_CHECKING:
        def get_sandbox_perk_description(self, perk_hash: int) -> dict[str, Any] | None: ...
        def get_item_definition(self, item_hash: int) -> dict[str, Any] | None: ...

    # ── Armor Mods ──────────────────────────────────────────────────────

    # Armor mod plugCategoryHash values
    _ARMOR_MOD_CATEGORIES = {
        2487827355: "general",       # enhancements.v2_general (属性模组)
        2912171003: "helmet",        # enhancements.v2_head
        3422420680: "gauntlets",     # enhancements.v2_arms
        1526202480: "chest",         # enhancements.v2_chest
        2111701510: "legs",          # enhancements.v2_legs
        912441879: "class_item",     # enhancements.v2_class_item
        3773173029: "artifice",      # enhancements.artifice (精工)
        3481777685: "tuning",        # armor_tiering (调谐模组)
    }

    # Stat hash → readable name
    # 从 manifest 中文版查询的官方翻译
    _STAT_NAMES = {
        2996146975: "武器",
        392767087: "生命值",
        1943323491: "职业",
        1735777505: "手雷",
        144602215: "超能",
        4244567218: "近战",
    }

    def get_armor_mods(
        self, slot: str = "", category: str = "all", stat: str = ""
    ) -> list[dict]:
        """从 manifest 查询护甲模组的真实名称和效果。

        Args:
            slot: 过滤部位 — "helmet"/"gauntlets"/"chest"/"legs"/"class_item"，空=全部
            category: 过滤类别 — "general"(属性模组)/"slot_specific"(部位专属)/"artifice"(精工)/"all"
            stat: 按效果关键词过滤 — 匹配 stat_bonus 的 key 或 description 中包含该关键词的模组

        Returns:
            [{name, hash, description, slot, stat_bonus, energy_cost, category}, ...]
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        # Determine which plugCategoryHashes to include
        if category == "general":
            target_hashes = {2487827355}
        elif category == "slot_specific":
            target_hashes = {2912171003, 3422420680, 1526202480, 2111701510, 912441879}
        elif category == "artifice":
            target_hashes = {3773173029}
        else:  # all
            target_hashes = set(self._ARMOR_MOD_CATEGORIES.keys())

        # Map slot name to plugCategoryHash for filtering
        slot_to_hash = {
            "helmet": 2912171003,
            "gauntlets": 3422420680,
            "chest": 1526202480,
            "legs": 2111701510,
            "class_item": 912441879,
        }
        if slot and slot in slot_to_hash:
            target_hashes = {slot_to_hash[slot]}
            # Also include general mods (they go in any slot)
            if category != "general":
                target_hashes.add(2487827355)

        results: list[dict] = []
        cur = conn.execute(
            "SELECT id, json FROM DestinyInventoryItemDefinition "
            "WHERE json LIKE '%\"itemType\":19%'"
        )
        for row in cur:
            try:
                data = json.loads(row["json"])
            except json.JSONDecodeError:
                continue

            plug = data.get("plug", {})
            pch = plug.get("plugCategoryHash", 0)
            if pch not in target_hashes:
                continue

            name = (data.get("displayProperties") or {}).get("name", "")
            if not name or "已锁定" in name or "空模组" in name:
                continue

            desc = (data.get("displayProperties") or {}).get("description", "")

            # 模组效果描述通常在 perks → DestinySandboxPerkDefinition 里
            if not desc:
                perk_descs = []
                for p in data.get("perks", []):
                    perk_hash = p.get("perkHash", 0)
                    if perk_hash:
                        perk_info = self.get_sandbox_perk_description(perk_hash)
                        if perk_info:
                            perk_desc = perk_info.get("description", "")
                            if perk_desc:
                                perk_descs.append(perk_desc)
                if perk_descs:
                    desc = "; ".join(perk_descs)

            energy = (plug.get("energyCost") or {}).get("energyCost", 0)

            # Extract stat bonuses
            stat_bonus = {}
            for s in data.get("investmentStats", []):
                stat_hash = s.get("statTypeHash", 0)
                value = s.get("value", 0)
                # Skip the generic "cost" stat (3578062600)
                if stat_hash == 3578062600 or value <= 0:
                    continue
                stat_name = self._STAT_NAMES.get(stat_hash)
                if stat_name:
                    stat_bonus[stat_name] = value

            slot_name = self._ARMOR_MOD_CATEGORIES.get(pch, "unknown")

            results.append({
                "name": name,
                "hash": row["id"],
                "description": desc,
                "slot": slot_name,
                "stat_bonus": stat_bonus,
                "energy_cost": energy,
                "category": category if category != "all" else slot_name,
            })

        # Filter by stat keyword if specified
        if stat:
            stat_lower = stat.lower()
            filtered = []
            for mod in results:
                # Match stat_bonus keys (e.g., "恢复", "手雷")
                stat_keys = [k.lower() for k in mod.get("stat_bonus", {}).keys()]
                # Match description text
                desc_lower = mod.get("description", "").lower()
                # Match mod name
                name_lower = mod.get("name", "").lower()

                if (
                    any(stat_lower in k for k in stat_keys)
                    or stat_lower in desc_lower
                    or stat_lower in name_lower
                ):
                    filtered.append(mod)
            results = filtered

        # Sort: general mods first, then by slot, then by energy cost
        results.sort(key=lambda x: (x["slot"] != "general", x["slot"], x["energy_cost"]))
        return results

    # ── Set Bonus (EquipableItemSet) ────────────────────────────────

    def get_set_bonus_info(self, item_hash: int) -> dict | None:
        """Get the set bonus info for an armor piece.

        Reads equippingBlock.equipableItemSetHash from the item definition,
        then looks up the DestinyEquipableItemSetDefinition.

        Returns:
            {set_hash, set_name, perks: [{required_set_count, sandbox_perk_hash, perk_name, perk_description}]}
            or None if the item has no set bonus.
        """
        item_def = self.get_item_definition(item_hash)
        if not item_def:
            return None

        set_hash = (item_def.get("equippingBlock") or {}).get("equipableItemSetHash", 0)
        if not set_hash:
            return None

        return self._lookup_set_bonus(set_hash)

    def get_set_bonus_by_hash(self, set_hash: int) -> dict | None:
        """Look up set bonus info by set hash directly."""
        return self._lookup_set_bonus(set_hash)

    def get_all_set_bonuses(self) -> dict[int, dict]:
        """Get all equipable item set definitions.

        Returns:
            dict mapping set_hash → {set_name, items, perks}
        """
        result: dict[int, dict] = {}
        if not self._conn:
            return result

        try:
            cur = self._conn.execute(
                "SELECT json FROM DestinyEquipableItemSetDefinition"
            )
            for row in cur:
                try:
                    data = json.loads(row["json"])
                    set_hash = data.get("hash", 0)
                    set_name = data.get("displayProperties", {}).get("name", "")
                    set_items = data.get("setItems", [])
                    raw_perks = data.get("setPerks", [])

                    # Look up Chinese name from zh manifest
                    if self._zh_conn:
                        signed = to_signed(set_hash)
                        zh_row = None
                        for h in (set_hash, signed):
                            zh_cur = self._zh_conn.execute(
                                "SELECT json FROM DestinyEquipableItemSetDefinition WHERE id = ?",
                                (h,)
                            )
                            zh_row = zh_cur.fetchone()
                            if zh_row:
                                break
                        if zh_row:
                            zh_data = json.loads(zh_row["json"])
                            zh_name = zh_data.get("displayProperties", {}).get("name", "")
                            if zh_name:
                                set_name = zh_name

                    # Enrich perks with names and descriptions
                    perks = []
                    for p in raw_perks:
                        perk_hash = p.get("sandboxPerkHash", 0)
                        perk_info = self.get_sandbox_perk_description(perk_hash)
                        perks.append({
                            "required_set_count": p.get("requiredSetCount", 0),
                            "sandbox_perk_hash": perk_hash,
                            "perk_name": perk_info.get("name", "") if perk_info else "",
                            "perk_description": perk_info.get("description", "") if perk_info else "",
                        })

                    result[set_hash] = {
                        "set_name": set_name,
                        "items": set_items,
                        "perks": perks,
                    }
                except (json.JSONDecodeError, KeyError):
                    continue
        except sqlite3.Error:
            logger.warning("Failed to load set bonus definitions", exc_info=True)

        return result

    def _lookup_set_bonus(self, set_hash: int) -> dict | None:
        """Internal: look up a single set bonus definition."""
        if not self._conn:
            return None

        try:
            # Try both unsigned and signed hashes (manifest stores signed)
            signed = to_signed(set_hash)
            row = None
            for h in (set_hash, signed):
                cur = self._conn.execute(
                    "SELECT json FROM DestinyEquipableItemSetDefinition WHERE id = ?",
                    (h,)
                )
                row = cur.fetchone()
                if row:
                    break
            if not row:
                return None

            data = json.loads(row["json"])
            set_name = data.get("displayProperties", {}).get("name", "")
            raw_perks = data.get("setPerks", [])

            # Look up Chinese name
            if self._zh_conn:
                zh_row = None
                for h in (set_hash, signed):
                    zh_cur = self._zh_conn.execute(
                        "SELECT json FROM DestinyEquipableItemSetDefinition WHERE id = ?",
                        (h,)
                    )
                    zh_row = zh_cur.fetchone()
                    if zh_row:
                        break
                if zh_row:
                    zh_data = json.loads(zh_row["json"])
                    zh_name = zh_data.get("displayProperties", {}).get("name", "")
                    if zh_name:
                        set_name = zh_name

            # Enrich perks
            perks = []
            for p in raw_perks:
                perk_hash = p.get("sandboxPerkHash", 0)
                perk_info = self.get_sandbox_perk_description(perk_hash)
                perks.append({
                    "required_set_count": p.get("requiredSetCount", 0),
                    "sandbox_perk_hash": perk_hash,
                    "perk_name": perk_info.get("name", "") if perk_info else "",
                    "perk_description": perk_info.get("description", "") if perk_info else "",
                })

            return {
                "set_hash": set_hash,
                "set_name": set_name,
                "perks": perks,
            }
        except (sqlite3.Error, json.JSONDecodeError, KeyError):
            logger.warning("Failed to look up set bonus %d", set_hash, exc_info=True)
            return None

    def search_set_bonus(self, query: str) -> dict | None:
        """Search for a set bonus by name (Chinese or English).

        Returns the first matching set bonus info, or None.
        """
        all_sets = self.get_all_set_bonuses()
        query_lower = query.strip().lower()

        # Exact match first
        for set_hash, info in all_sets.items():
            if info["set_name"].lower() == query_lower:
                return {"set_hash": set_hash, **info}

        # Substring match
        for set_hash, info in all_sets.items():
            if query_lower in info["set_name"].lower():
                return {"set_hash": set_hash, **info}

        return None
