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
        "DestinyInventoryBucketDefinition",
        "DestinySandboxPerkDefinition",
        "DestinyStatDefinition",
        "DestinyEquipmentSlotDefinition",
        "DestinyItemCategoryDefinition",
        "DestinyCollectibleDefinition",
        "DestinyPresentationNodeDefinition",
        "DestinyLoadoutColorDefinition",
        "DestinyLoadoutIconDefinition",
        "DestinyLoadoutNameDefinition",
        "DestinyMetricDefinition",
        # 锻造图样（图鉴「模式和催化」）：图样的解锁进度是**记录**，不是收藏品 ——
        # 组件 800 里一条都没有、组件 1300 只给"能塑形哪些 perk"（实测见
        # docs/plans/PATTERN_QUERY_PLAN.md），所以记录与它的目标定义必须查得到。
        "DestinyRecordDefinition",
        "DestinyObjectiveDefinition",
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

    def get_bucket_definition(self, bucket_hash: int) -> dict | None:
        """库存桶定义（`DestinyInventoryBucketDefinition`）：**容量 `itemCount` 只在这里**。

        装备编排要判断"这类背包还能不能再放一件"，唯一权威来源就是它；桶 hash 是 uint32，
        头盔/臂铠那两个超过 int32 上限，`_query_json` 内部已做 `to_signed()` 回退（真机验证过）。
        """
        return self._query_json("DestinyInventoryBucketDefinition", bucket_hash)

    def get_metric_definition(self, metric_hash: int) -> dict | None:
        """计数器定义（`DestinyMetricDefinition`）：名称与描述只在这里。

        profile 组件 1100 只给 `metricHash` + 进度数字，**不给名字**；"熔炉生涯击败"
        这句话要从 `displayProperties.name/description` 取。和桶定义一样，计数器 hash
        是 uint32，`_query_json` 已做 `to_signed()` 回退；查不到返回 None（调用方降级成
        `#hash`，不许编一个名字）。
        """
        return self._query_json("DestinyMetricDefinition", metric_hash)

    def get_activity_mode_name(self, mode_type: int) -> str:
        """模式类型的官方中文名（zh Manifest 的 `DestinyActivityModeDefinition`）。

        名字是上游字段（`displayProperties.name`），**别在我们这边再抄一张标签表**：
        抄过的那张 `MODE_NAMES[69] = "猛攻"` 真机对照是错的（69 = 多人竞技PvP），
        而且只有 8 条，真机跑出来的子模式（43/44/73/89/91…）全不在里面。

        索引按 `modeType` 建（实测 75 个模式类型、无重复），第一问时懒加载一次；
        `zh` 先建、`en` 只补缺。查不到返回 `""` —— 调用方降级成 `模式<modeType>`，
        不编名字（"没查到"和"没有"是两件事）。

        属性在方法里懒初始化，不写进 `ManifestManager.__init__`：入口那个文件贴着体量上限，
        谁都不许再往里加行（`tests/test_module_size_ratchet.py`）。
        """
        if getattr(self, "_mode_names", None) is None:
            names: dict[int, str] = {}
            for conn in (self._zh_conn, self._conn):
                if not conn:
                    continue
                try:
                    rows = conn.execute(
                        "SELECT json FROM DestinyActivityModeDefinition"
                    ).fetchall()
                except sqlite3.Error as e:
                    logger.warning("读取 DestinyActivityModeDefinition 失败：%s", e)
                    continue
                for row in rows:
                    try:
                        definition = json.loads(row["json"])
                    except (json.JSONDecodeError, TypeError, KeyError):
                        # `KeyError`：连接没设 `row_factory` 时 `row["json"]` 会抛这个，
                        # 而不是 sqlite3.Error —— 少了它整张索引会静默变空。
                        continue
                    if not isinstance(definition, dict):
                        # 合法 JSON 但不是 dict（数组/字符串）：以前 `.get()` 会抛
                        # AttributeError 冒到调用方，把整条 history/stats/counters 带崩。
                        continue
                    raw_type = definition.get("modeType")
                    display = definition.get("displayProperties") or {}
                    name = display.get("name") if isinstance(display, dict) else None
                    if isinstance(raw_type, bool) or not isinstance(raw_type, int):
                        continue
                    if isinstance(name, str) and name.strip():
                        names.setdefault(raw_type, name.strip())
            if not names:
                # 索引为空 = 全站模式名会退化成"模式<号>"。这条必须能看见（以前只有 debug），
                # 否则表现是"界面突然全是模式43"，而日志里什么都没有。
                logger.warning(
                    "模式名索引为空：模式名将退化成 模式<modeType>。"
                    "检查 Manifest 的 DestinyActivityModeDefinition 表与连接 row_factory。"
                )
            self._mode_names = names
        return self._mode_names.get(mode_type, "")
