"""突袭/地牢战绩报表：把 `data/raids.py` 那张对照表读成一张表。

**数据来源只有一个**：profile 组件 1100（游戏内官方计数器），一次 `GetProfile` 全拿到 ——
所以这个模块没有"逐场扫描""分页""缓存"这些概念，它只做三件事：取数、组装、说清缺了什么。

为什么账要这么算（都写在这里，改之前先读）：

1. **完成数不是我们自己数的。** 实测对照过：逐场 PGCR 自己数完成场次会偏
   （深岩墓室我们数出 179「我完成」/ 211「任一人完成」，而官方计数器与第三方站点都是 229），
   因为 `GetActivityHistory` 本身不全（救赎花园历史 133 场 < 官方 140 次完成）。
   所以**只用官方计数器**。
2. **导师数是"人数"不是"次数"**（原文「带领完成首次…的守护者总人数」），
   一次带 3 个新人记 +3 —— 所以它能大于完成场次，这不是 bug。
3. **缺的给 `None`，不编 0**；而且要分清两种缺：
   - `missing_counters`：**这个副本根本没有**这一项计数器（结构性的，比如"平衡"没有完成数）；
   - 值是 `None` 但计数器**存在** → 那是这次没读到，进 `warnings`。
4. **哪些列做不了，写在 `unavailable` 里**（全程数、最短用时、排名、DayOne 编号），
   不是留空让人猜 —— 见 `docs/reference/bungie_api.md` 第十六节与
   `docs/plans/RAID_REPORT_PLAN.md`。
"""

from __future__ import annotations

from typing import Any

from ..data import raids
from ..exceptions import InvalidArgumentError
from ..logging_config import get_logger
from .activity_counters_service import ActivityCountersService, metric_progress
from .raid_runs import RaidScanner, RaidRunStore, fresh_verdict

logger = get_logger(__name__)

#: 顶部那句"哪些列没有"的来源：每条都指向实测结论，不是"暂不支持"这种含糊话。
_UNAVAILABLE: tuple[dict[str, str], ...] = (
    {
        "field": "day_one_rank",
        "reason": (
            "DayOne 名次是全球完成顺序（人群数据）：Bungie 官方只给「有没有在首日完成」的成就记录，"
            "不给编号；要做只能接第三方服务（RaidHub 的 semi-public API）。"
        ),
    },
    {
        "field": "ranks",
        "reason": (
            "Full Clears Rank / Speed Rank 是第三方站点自己库里的百分位排名"
            "（分母是它收录的全部玩家），本地无法计算。"
        ),
    },
)

#: PGCR 那两列没扫到时的原因（按副本出现在 `unavailable` 里，扫过就不再出现）
_NOT_SCANNED = "本地还没扫过这个副本的结算，这一列给 null；跑 intent=\"raid_scan\" 补齐。"


class RaidReportService:
    """`activity_assistant(intent="raid_report")` 的取数与组装。"""

    def __init__(
        self,
        counters: ActivityCountersService,
        scanner: RaidScanner | None = None,
        manifest: Any | None = None,
    ) -> None:
        # 复用计数器服务的读法与抖动重试：两个功能读的是同一个组件 1100，
        # 重试节奏与"空不是 0"的话术只能有一处（`read_metrics`）。
        self._counters = counters
        # 逐场索引（PGCR 那两列的来源）。没注入就用默认路径 —— 单测里给替身。
        self._scanner = scanner
        self._store = scanner.store if scanner else RaidRunStore()
        # 活动道（`get_icon_url(activity_hash=…)`）要查 DestinyActivityDefinition。
        # 只读替身测试不带它 —— 那时给出的图标是空串，不是编出来的地址。
        self._manifest = manifest

    async def scan(self, player_name: str, activity: str, limit: int) -> dict:
        """`raid_scan` 的取数：按副本扫一块 PGCR，落进逐场索引。"""
        if self._scanner is None:
            raise InvalidArgumentError("扫描器没有装配（ra id_scan 需要 bungie 客户端）。")
        entry = raids.ENTRIES.get((activity or "").strip())
        if entry is None:
            raise InvalidArgumentError(
                f"raid_scan 只认副本名（如 深岩墓室 / 最后一愿），收到 {activity!r}；"
                f"可选的副本见 intent=\"raid_report\" 的行。"
            )
        result = await self._scanner.scan_chunk(player_name, entry, limit)
        result["activity"] = entry.name
        return result

    async def get_report(self, player_name: str, kind: str) -> dict:
        """按类型（`raid` / `dungeon`）取报表。

        Returns:
            `{"kind", "rows", "totals", "coverage", "missing_counters", "unavailable",
            "sources", "warnings"}`。组件 1100 读不到时 `rows=[]` 且 `unavailable` 里
            第一条就是原因 —— **不报成功**（空不是 0）。
        """
        kind = (kind or "").strip().lower()
        if kind not in raids.KIND_LABELS_ZH:
            raise InvalidArgumentError(
                f'raid_report 的 mode 只认 raid / dungeon（突袭 / 地牢），收到 {kind!r}。'
            )

        entries = raids.by_kind(kind)
        metrics, reason = await self._counters.read_metrics(player_name)
        if reason:
            logger.warning("突袭报表不可用：player=%s reason=%s", player_name, reason)
            return {
                "kind": kind,
                "rows": [],
                "totals": {},
                "coverage": {},
                "unavailable": [{"field": "counters", "reason": reason}],
                "sources": self._sources(),
                "warnings": [],
            }

        warnings: list[str] = []
        rows: list[dict[str, Any]] = []
        for entry in entries:
            activity_hash, icon_url = self._activity_identity(entry)
            row: dict[str, Any] = {
                "activity": entry.name,
                "activity_hash": activity_hash,
                "icon_url": icon_url,
                "kind": entry.kind,
            }
            badges: list[dict[str, Any]] = []
            missing: list[str] = []
            for counter_kind in raids.COUNTER_ORDER:
                metric_hash = entry.counters.get(counter_kind)
                if metric_hash is None:
                    row[counter_kind] = None
                    missing.append(counter_kind)
                    continue
                value = metric_progress(metrics, metric_hash)
                row[counter_kind] = value
                if value is None:
                    # 表里有、这次却读不到：不是"没有"，值得单独说一句。
                    warnings.append(
                        f"「{entry.name}」的{raids.COUNTER_LABELS_ZH[counter_kind]}这一项"
                        f"（metric {metric_hash}）本次没读到，给的是 null。"
                    )
                elif value > 0 and counter_kind in {"flawless", "solo_flawless"}:
                    badges.append({"kind": counter_kind, "count": value})
            row["badges"] = badges
            if missing:
                row["missing_counters"] = missing
            self._attach_pgcr_columns(row, entry)
            rows.append(row)

        totals = self._totals(rows)
        coverage = self._coverage(rows, len(entries))
        unavailable = list(_UNAVAILABLE)
        not_scanned = [r["activity"] for r in rows if r.get("not_scanned")]
        if not_scanned:
            names = "、".join(not_scanned[:6]) + ("…" if len(not_scanned) > 6 else "")
            unavailable = [
                {"field": field, "reason": f"{_NOT_SCANNED}未扫描：{names}。"}
                for field in ("full_clears", "fastest_seconds")
            ] + unavailable
        if reason == "":
            logger.info(
                "突袭报表：player=%s kind=%s 行=%d", player_name, kind, len(rows)
            )
        return {
            "kind": kind,
            "rows": rows,
            "totals": totals,
            "coverage": coverage,
            "unavailable": unavailable,
            "sources": self._sources(),
            "warnings": warnings,
        }

    def _activity_identity(self, entry: raids.RaidEntry) -> tuple[int, str]:
        """这一行的活动身份：`activity_hash` 与 `icon_url` **同源**（活动道）。

        `data/raids.py` 一个副本挂了好几个难度档的活动 hash（普通/大师/竞赛各自一张图），
        所以不能"hash 给第一个、图随便查" —— 取第一个**查得到图**的那个。
        替身测试不带 manifest：那时给 `(0, "")`，**不编一个别的副本的图**。
        """
        lookup = getattr(self._manifest, "first_activity_with_icon", None)
        return lookup(entry.hashes) if callable(lookup) else (0, "")

    def _attach_pgcr_columns(self, row: dict[str, Any], entry: raids.RaidEntry) -> None:
        """把「全程次数」与「全程最短用时」接到行上 —— **只从本地逐场索引读**，不发请求。

        口径（与 RaidHub 默认不同、与 raid.report 一致，见 bungie_api.md 16.1）：

        - 只算 `fresh_verdict == "fresh"` 的场次（`activityWasStartedFromBeginning` 为真）；
        - 还要**我自己完成**（`completed == 1`）—— 半途退出的场次不该算完成；
        - `unknown` 那些（字段坏掉的三段窗口）既不算进也不算掉，单独报数量。

        没扫过这个副本时两列给 `None`，原因写进 `unavailable`（**不是 0**）。
        """
        scanned = [r for r in self._store.rows_for(set(entry.hashes)) if r.get("scanned")]
        if not scanned:
            row["full_clears"] = None
            row["full_clears_strict"] = None
            row["fastest_seconds"] = None
            row["not_scanned"] = True
            return

        # 两种口径都给，因为**上游那段窗口让"唯一正确的数"不存在**：
        # - `fresh`：字段明确说是全程开局；
        # - `unknown`：字段坏掉的那段（Beyond Light→巫后，Bungie 明说永不修、不追溯）——
        #   拿它当排除依据等于把一整段历史判死。
        # 默认口径把 `unknown` 当全程（与 raid.report 处理坏窗口的方式一致），
        # `checkpoint`（修复日之后的 False）两种口径都不算。
        strict = [r for r in scanned if fresh_verdict(r) == "fresh" and r.get("completed") == 1]
        lenient = [
            r for r in scanned
            if fresh_verdict(r) in {"fresh", "unknown"} and r.get("completed") == 1
        ]
        row["full_clears"] = len(lenient)
        row["full_clears_strict"] = len(strict)
        # **最短用时只从严格口径里取**：检查点开局的场次时长短（跳过了前面的关卡），
        # 混进来会得到一个"最快的全程"其实是半程的数字。次数可以宽松，时间不行。
        durations = [
            r["duration_seconds"] for r in strict
            if isinstance(r.get("duration_seconds"), int)
        ]
        row["fastest_seconds"] = min(durations) if durations else None

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
        """各列合计，只累加**读到的**值（缺的不当 0）。"""
        totals: dict[str, int] = {}
        for counter_kind in raids.COUNTER_ORDER:
            values = [r[counter_kind] for r in rows if r.get(counter_kind) is not None]
            if values:
                totals[counter_kind] = sum(values)
        # 全程可以求和；最短用时是 min，求和没有意义，所以不进 totals。
        full = [r["full_clears"] for r in rows if isinstance(r.get("full_clears"), int)]
        if full:
            totals["full_clears"] = sum(full)
        return totals

    @staticmethod
    def _coverage(rows: list[dict[str, Any]], total: int) -> dict[str, dict[str, int]]:
        """每一列的覆盖面：合计是几个副本凑出来的，得能自证。

        `rows_with_counter` 数的是"这个副本**有**这项计数器"，不是"读到了值" ——
        它回答的是"为什么合计只有 n 行"，不是"这次读没读到"。
        """
        coverage: dict[str, dict[str, int]] = {}
        for counter_kind in raids.COUNTER_ORDER:
            coverage[counter_kind] = {
                "rows_with_counter": sum(
                    1 for r in rows if counter_kind not in (r.get("missing_counters") or [])
                ),
                "rows_total": total,
            }
        # PGCR 那两列的"覆盖"含义不同：不是"有没有这项"，而是"扫出来没有"。
        for pgcr_field in ("full_clears", "fastest_seconds"):
            coverage[pgcr_field] = {
                "rows_with_counter": sum(1 for r in rows if r.get(pgcr_field) is not None),
                "rows_total": total,
            }
        return coverage

    def _sources(self) -> dict[str, str]:
        return {
            "counters": "profile.metrics(1100)",
            "pgcr_columns": f"本地逐场索引 {self._store.path.name}（由 intent=\"raid_scan\" 建立）",
            "table": "destiny_mcp/data/raids.py",
        }
