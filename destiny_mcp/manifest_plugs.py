"""Plug 池与 Perk 描述：武器插槽类别、可插条目与 Perk 文本。

以 mixin 挂在 ManifestManager 上。`get_plug_set_plugs` 富化时要用
ItemDefinitionMixin 的 `get_item_definition`，前两个方法用它的 `_query_json`；
`get_plug_category_identifier` 则直接读 self._conn（现状，见对应测试）。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json

from .logging_config import get_logger
from .utils.hash_utils import to_signed

logger = get_logger(__name__)


class PlugCatalogMixin:
    """插件与 Perk 的只读查询，带内存缓存。"""

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
