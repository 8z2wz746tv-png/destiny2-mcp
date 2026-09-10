"""Plug 定义查询：护甲模组与子职业两条链共用。

只依赖 ManifestManager，不认识装备流程，放在两者下面，避免两条链互相引用。
"""

from __future__ import annotations


class PlugLookupMixin:
    """按 plug hash 读类别；子类通过 self._manifest 取定义。"""

    def _plug_category_hash(self, plug_hash: int) -> int:
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            definition = {}
        if not (definition.get("plug") or {}):
            summary = self._manifest.get_item_info(plug_hash)
            definition = summary if isinstance(summary, dict) else {}
        category = (definition.get("plug") or {}).get("plugCategoryHash", 0)
        return category if isinstance(category, int) else 0
