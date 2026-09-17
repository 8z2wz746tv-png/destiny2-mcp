"""游戏内生涯计数器（profile 组件 1100 Metrics）。

为什么单独一个服务、而不是塞进 `activity_service`：这里是**另一个数据源**。
统计接口（`GetHistoricalStats`）给的是 Bungie 后端的对局聚合，计数器给的是游戏里
你自己看到的那个数字，两者口径不同、谁也替代不了谁：

- 组件 1100 的 `Opponents Defeated`：**从 S1 起累计**，含已删角色 → 真机 124,495；
- 统计接口账号级 `allPvP.allTime.opponentsDefeated`：只算它还列举得出的角色 → 107,106。

所以本模块只回答"游戏内计数器是多少"，**不碰**统计接口，也不替调用方合并两个数
（合并口径与差异说明属于 P2，见 docs/plans/PVP_STATS_PLAN.md）。

两条真机结论直接决定这里的写法：

1. 组件 1100 **读取会抖动**：同一 URL 连续请求会出现整块 `metrics` 缺失（0 条）的响应，
   重试后恢复 402 条 → 必须重试；重试后仍为空就如实报 `unavailable`，**不许把空当 0**。
2. 名称/描述不在 profile 响应里，只能查 Manifest 的 `DestinyMetricDefinition`；
   查不到就降级成 `#hash`（`name_resolved: false`），不编、也不崩。
"""

from __future__ import annotations

from typing import Any

from ..bungie_client import BungieClient
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver
from . import profile_components
from .write_readback import read_until

logger = get_logger(__name__)

# 抖动的重试节奏。比写入回读的窗口（8 × 1.5s ≈ 10.5s）更短：那是"等上游把写入同步出来"，
# 这里是"再读一次就恢复"的读抖动；真等十秒不如早点把"读不到"如实报出去。
ATTEMPTS = 4
DELAY_SECONDS = 1.0


def _int_or_none(value: Any) -> int | None:
    """计数器里的数字：json 里可能是 int、float 或字符串数字。

    取不到就是 `None`：项目原则是"缺值给 None，不编 0"——计数器上"没读到进度"
    和"进度真的是 0"是两件事，编成 0 会变成一条假的生涯数据。
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def parse_counters(raw_metrics: Any, manifest: ManifestManager) -> list[dict]:
    """组件 1100 的 `metrics` 字典 → 行式计数器（未排序）。

    每行固定带 `source: "profile.metrics"`：同一个生涯数字在统计接口那边**还有另一个值**，
    调用方必须能看出这个数是从哪来的。
    """
    if not isinstance(raw_metrics, dict):
        return []

    counters: list[dict] = []
    for raw_hash, entry in raw_metrics.items():
        metric_hash = _int_or_none(raw_hash)
        if metric_hash is None:
            continue
        progress_section = entry.get("objectiveProgress") if isinstance(entry, dict) else None
        if not isinstance(progress_section, dict):
            progress_section = {}

        definition = manifest.get_metric_definition(metric_hash)
        display = definition.get("displayProperties") if isinstance(definition, dict) else None
        if not isinstance(display, dict):
            display = {}
        name = display.get("name")
        description = display.get("description")

        counters.append({
            "metric_hash": metric_hash,
            "name": name if isinstance(name, str) and name.strip() else f"#{metric_hash}",
            "description": description if isinstance(description, str) else "",
            "progress": _int_or_none(progress_section.get("progress")),
            "completion_value": _int_or_none(progress_section.get("completionValue")),
            "source": "profile.metrics",
            "name_resolved": bool(isinstance(name, str) and name.strip()),
        })
    return counters


def filter_counters(counters: list[dict], query: str) -> list[dict]:
    """按 `query`（名称或描述的子串，大小写不敏感）过滤；空串表示不过滤。"""
    keyword = query.strip().lower()
    if not keyword:
        return counters
    return [
        counter for counter in counters
        if keyword in counter["name"].lower() or keyword in counter["description"].lower()
    ]


def _sort_key(counter: dict) -> tuple[int, int]:
    """数值降序；进度为 None 的排在最后（它们不是 0，只是没读到）。"""
    progress = counter["progress"]
    return (0 if progress is not None else 1, -(progress or 0))


def payload(
    counters: list[dict],
    *,
    limit: int,
    unavailable: str = "",
    query: str = "",
) -> dict:
    """行式返回：`counters` + `total`/`returned`/`truncated` + `unavailable`。

    `total`/`returned`/`truncated` 是在**过滤之后**算的：调用方要能自己判断
    "筛出来就这些"还是"被 limit 截了"。
    """
    ordered = sorted(counters, key=_sort_key)
    returned = ordered[:limit] if limit and limit > 0 else ordered
    return {
        "counters": returned,
        "total": len(ordered),
        "returned": len(returned),
        "truncated": len(returned) < len(ordered),
        "unavailable": unavailable,
        # 每个数字都要能自证范围：这是筛过之后的清单，还是全量。
        "filter": {"query": query, "limit": limit},
        # 组件号进日志的同一份说明也进返回值：排查"这次为什么没拿到 1100"时不用猜。
        "components": profile_components.describe(profile_components.METRICS),
    }


class ActivityCountersService:
    """读游戏内生涯计数器（profile 组件 1100）。"""

    def __init__(
        self, bungie: BungieClient, manifest: ManifestManager, resolver: PlayerResolver
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    async def _read_metrics(self, membership_id: str, membership_type: int) -> dict:
        """读一次组件 1100，返回 `metrics` 字典（读不到就是 `{}`）。

        上游故障（映射成异常）与"整块缺失"在这里**同样**返回 `{}`：对调用方而言两者都是
        "这一次没读到"，而重试正是针对这种情况；重试仍失败时由上层如实报 `unavailable`
        —— 这里除了空字典之外不编造任何东西。
        """
        try:
            profile = await self._resolver.get_profile(
                membership_id, membership_type, list(profile_components.METRICS)
            )
        except Exception as exc:  # noqa: BLE001 - 重试要覆盖"这次没读到"的所有形态
            logger.warning("读取计数器组件 1100 失败：%s", exc)
            return {}

        if not isinstance(profile, dict):
            return {}
        metrics = profile.get("metrics")
        data = metrics.get("data") if isinstance(metrics, dict) else None
        raw = data.get("metrics") if isinstance(data, dict) else None
        if not isinstance(raw, dict) or not raw:
            # 真机实测：同一 URL 连续请求会出现整块缺失（0 条）的 200 响应。
            logger.warning("组件 1100 返回空 metrics（上游抖动），准备重试")
            return {}
        return raw

    async def get_career_counters(
        self,
        player_name: str,
        query: str = "",
        limit: int = 20,
    ) -> dict:
        """游戏内生涯计数器：默认全量候选按数值降序，最多 20 条。

        口径要说清（调用方要照着讲给用户听）：组件 1100 是**游戏内那个计数器**，
        从 S1 起累计、含已删角色，和统计接口的账号级数字**不是一回事**。

        Args:
            player_name: 玩家 BungieName。
            query: 名称或描述的子串过滤（大小写不敏感）。默认空 = 不过滤（本账号约 402 条）；
                只要 PvP 相关的就传 `"crucible"`（试炼/铁旗/智谋都在 Crucible 描述下），
                要试炼就传 `"trials"`。
            limit: 最多返回几条，默认 20；`total`/`truncated` 说明一共筛出多少。

        Returns:
            `{"counters": [...], "total", "returned", "truncated", "unavailable", "filter",
            "components"}`；读不到时 `counters=[]` 且 `unavailable` 写清原因，**不报成功**。
        """
        player = await self._resolver.resolve_player(player_name)
        membership_id = str(player["membership_id"])
        membership_type = int(player["membership_type"])

        # 抖动的判据是"拿到非空 metrics"；重试只重试、不判断成败，判断留给这里。
        raw_metrics = await read_until(
            lambda: self._read_metrics(membership_id, membership_type),
            lambda metrics: bool(metrics),
            attempts=ATTEMPTS,
            delay=DELAY_SECONDS,
        )
        if not raw_metrics:
            reason = (
                f"组件 1100（Metrics）返回空：上游读取抖动，已重试 {ATTEMPTS} 次仍未拿到计数器。"
                "这不代表你没有任何计数（空不是 0），请稍后重试。"
            )
            logger.warning("生涯计数器不可用：player=%s", player_name)
            empty = payload([], limit=limit, unavailable=reason, query=query)
            empty["warnings"] = []
            return empty

        counters = filter_counters(parse_counters(raw_metrics, self._manifest), query)
        logger.info(
            "生涯计数器：player=%s 原始=%d 过滤后=%d query=%r",
            player_name, len(raw_metrics), len(counters), query,
        )
        result = payload(counters, limit=limit, query=query)
        # 可选数据的降级（名字查不到、进度缺失）只写进 warnings，不改 `counters` 的形状：
        # 每一行始终是同一组键，缺的是值，不是结构。
        result["warnings"] = self._degradation_warnings(counters)
        return result

    @staticmethod
    def _degradation_warnings(counters: list[dict]) -> list[str]:
        warnings: list[str] = []
        unresolved = sum(1 for counter in counters if not counter["name_resolved"])
        if unresolved:
            warnings.append(
                f"{unresolved} 条计数器在 Manifest（DestinyMetricDefinition）里查不到名称，"
                "已降级为 #hash 显示；它们的进度仍然可用。"
            )
        missing_progress = sum(1 for counter in counters if counter["progress"] is None)
        if missing_progress:
            warnings.append(
                f"{missing_progress} 条计数器没有 progress 字段（给的是 null，不是 0）。"
            )
        return warnings
