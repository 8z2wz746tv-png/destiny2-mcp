"""通用定义查询与命名解析：白名单表查询、名称占位、按名搜索、收藏品反查。

以 mixin 挂在 ManifestManager 上。`get_definition` 与 `iter_definitions` 共用
ItemDefinitionMixin 的 `_VALID_TABLES` 白名单，前者委托它的 `_query_json`。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from .logging_config import get_logger
from .utils.hash_utils import to_unsigned
from .utils.icons import icon_url

logger = get_logger(__name__)

#: 上游给"这张图没做"的活动塞的通用横幅（实测 707/4069 条活动带它，PvP 活动全在这个坑里：
#: 铁旗、智谋、试炼都是它）。**占位不是图** —— 拿它当图标，界面上每张卡片都是同一张
#: "找不到图"的灰图，比没有还糟。所以遇到它往下退到 `displayProperties.icon`。
_ACTIVITY_IMAGE_PLACEHOLDER = "/img/theme/destiny/bgs/pgcrs/placeholder.jpg"

#: 上游"没有图标"的哨兵（`displayProperties.icon` 里也会出现它）。
_MISSING_ICON = "/img/misc/missing_icon_d2.png"


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

    def get_icon_url(self, *, item_hash: int = 0, activity_hash: int = 0) -> str:
        """图标 URL 的**唯一出口**：内部两条道（物品道 / 活动道）。

        **为什么必须是一个函数里分两条道**（2026-10-04 的缺口，见
        `docs/plans/ICON_URL_PLAN.md` §八）：物品与活动是 Manifest 的两张表、两种字段。
        把活动 hash 塞进物品道不会报错，只会返回空串 —— 于是"物品图全对、活动全裂"，
        而且看上去像"这个活动本来就没图"。一个出口 + 内部两条道，就不会有第二个地方
        需要知道"该走哪条"。

        | 道 | 表 | 字段 |
        | --- | --- | --- |
        | 物品道（`item_hash=`） | `DestinyInventoryItemDefinition` | `displayProperties.icon` |
        | 活动道（`activity_hash=`） | `DestinyActivityDefinition` | `pgcrImage` → `displayProperties.icon` |

        活动道的两条来源不是二选一，是**优先级**：`pgcrImage` 是那张全屏横幅（突袭/地牢
        用它才对得上卡片），但上游给"没做图"的活动塞了通用占位（PvP 活动全是它），
        所以占位与"没有图标"哨兵都要往下退到 `displayProperties.icon`。

        归一（相对路径 → 绝对地址）交给 `utils.icons.icon_url()`：那是**唯一**拼源站的地方。
        两个 hash 都不给、或定义查不到 → `""`（缺值不编）。
        """
        if activity_hash:
            return icon_url(self._activity_image_path(activity_hash))
        if item_hash:
            info = self.get_item_info(item_hash)
            return icon_url(info.get("icon") if isinstance(info, dict) else "")
        return ""

    def first_activity_with_icon(self, activity_hashes: Iterable[int]) -> tuple[int, str]:
        """一组候选活动 hash 里，第一个**查得到图**的那个：返回 `(hash, icon_url)`。

        为什么是"一组"：一个副本在 Manifest 里是**好几个活动**（普通/大师/竞赛各一条，
        各有各的 `pgcrImage`），`data/raids.py` 的每一行挂的就是这一组。
        **hash 与图必须同源** —— 拿 A 档的 hash 配 B 档的图，界面上就是"名字对、图是另一张"，
        而且不报错。一个都查不到时返回 `(0, "")`（缺值不编，不拿别的副本的图顶上）。
        """
        for candidate in activity_hashes or ():
            icon = self.get_icon_url(activity_hash=candidate)
            if icon:
                return to_unsigned(candidate), icon
        return 0, ""

    def _activity_image_path(self, activity_hash: int) -> str:
        definition = self.get_definition("DestinyActivityDefinition", activity_hash)
        if not isinstance(definition, dict):
            return ""
        candidates = (
            definition.get("pgcrImage"),
            (definition.get("displayProperties") or {}).get("icon"),
        )
        for value in candidates:
            text = str(value or "").strip()
            if text and text not in {_ACTIVITY_IMAGE_PLACEHOLDER, _MISSING_ICON}:
                return text
        return ""

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
