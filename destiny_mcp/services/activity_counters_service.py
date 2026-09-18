"""游戏内生涯计数器（profile 组件 1100 Metrics）。

为什么单独一个服务、而不是塞进 `activity_service`：这里是**另一个数据源**。
统计接口（`GetHistoricalStats`）给的是 Bungie 后端的对局聚合，计数器给的是游戏里
你自己看到的那个数字，两者口径不同、谁也替代不了谁：

- 组件 1100 的 `Opponents Defeated`：**从 S1 起累计**，含已删角色 → 真机 124,495；
- 统计接口账号级 `allPvP.allTime.opponentsDefeated`（`mergedAllCharacters`，**也含已删角色**）
  → 真机 78,864 = 现存 50,622 + 已删 28,242；两者差 45,631，拆不出来。

所以本模块只回答"游戏内计数器是多少"，**不碰**统计接口，也不替调用方合并两个数
（合并口径与差异说明在 `tools/_stats_branches.py`，见 ADR-005）。

两条真机结论直接决定这里的写法：

1. 组件 1100 **读取会抖动**：同一 URL 连续请求会出现整块 `metrics` 缺失（0 条）的响应，
   重试后恢复 402 条 → 必须重试；重试后仍为空就如实报 `unavailable`，**不许把空当 0**。
2. 名称/描述不在 profile 响应里，只能查 Manifest 的 `DestinyMetricDefinition`；
   查不到就降级成 `#hash`（`name_resolved: false`），不编、也不崩。

模式/周期（P3）：三条口径不同的计数器**同名叫「已击败对手」**（熔炉生涯 124,495 /
试炼生涯 10,696 / 熔炉本赛季 3,522），Manifest 的父节点链只能分模式家族、分不出生涯与赛季。
所以每一行额外带 `mode`/`period`/`label_zh`，**来自 `data/pvp_counters.py` 那张人工确认过的
对照表**：表里没有的给 `mode="other"` / `period=None` / `label_zh=""` —— 不猜。
`mode=` / `period=` 过滤按同一张表判定：词表外的取值直接报错，不返回空清单当答案
（"筛出来是空"和"你筛的词我不认识"是两件事）。
"""

from __future__ import annotations

from typing import Any

from ..bungie_client import BungieClient
from ..data import activity_modes, pvp_counters
from ..exceptions import InvalidArgumentError
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


def mode_label(mode: str, manifest: ManifestManager) -> str:
    """计数器家族 → 官方中文模式名（Manifest，zh 优先）。

    `mode="other"`（表里没收录的计数器）不是模式词，给一句人话；其余查不到就退回词本身
    —— 不编中文名。标签不再存第二份：`pvp_counters.MODE_LABELS_ZH` 已整表删除。
    放模块级而不是方法里：`parse_counters` 也是模块级函数（它只多要一个 manifest）。
    """
    if mode == "other":
        return "未分类"
    mode_type = activity_modes.MODE_TYPE.get(mode)
    if mode_type is None:
        return mode
    label = manifest.get_activity_mode_name(mode_type)
    return label if isinstance(label, str) and label.strip() else mode


def parse_counters(raw_metrics: Any, manifest: ManifestManager) -> list[dict]:
    """组件 1100 的 `metrics` 字典 → 行式计数器（未排序）。

    每行固定带 `source: "profile.metrics"`：同一个生涯数字在统计接口那边**还有另一个值**，
    调用方必须能看出这个数是从哪来的。

    模式/周期三件套（`mode`/`period`/`mode_label`/`period_label`/`label_zh`）来自
    `data/pvp_counters.py`：表里没有的 hash 得到 `other` / `None` / `""`，**不猜**。
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

        classification = pvp_counters.classify(metric_hash)
        mode = classification["mode"]
        period = classification["period"]
        counters.append({
            "metric_hash": metric_hash,
            "name": name if isinstance(name, str) and name.strip() else f"#{metric_hash}",
            "description": description if isinstance(description, str) else "",
            "progress": _int_or_none(progress_section.get("progress")),
            "completion_value": _int_or_none(progress_section.get("completionValue")),
            "source": "profile.metrics",
            "name_resolved": bool(isinstance(name, str) and name.strip()),
            "mode": mode,
            # 中文名来自 Manifest（zh 优先）：这里以前读 pvp_counters 的标签表，
            # 而那张表与 activity_service.MODE_NAMES 是两份会各自烂掉的副本。
            "mode_label": mode_label(mode, manifest),
            "period": period,
            "period_label": pvp_counters.PERIOD_LABELS_ZH.get(period, "") if period else "",
            # 表里的中文标签（与 Manifest 的 name 分开：一个是我们的口径，一个是上游文本；
            # 表里没有就是 ""，此时 name 才是唯一可用的名字）。
            "label_zh": classification["label_zh"],
        })
    return counters


def filter_counters(
    counters: list[dict],
    query: str,
    mode: str = "",
    period: str = "",
) -> list[dict]:
    """按 `query`（名称或描述子串）/`mode`/`period` 过滤；空串表示不过滤。

    `mode`/`period` 的词表外取值在这里**报错**而不是返回空：用户说 `mode="日落"` 时
    "一条都没有"是错答案（日落本来就没有计入对照表），要说清"这张表认哪些词"。
    """
    keyword = query.strip().lower()
    wanted_mode = mode.strip().lower()
    wanted_period = period.strip().lower()
    if wanted_mode and wanted_mode not in pvp_counters.filter_modes():
        raise InvalidArgumentError(
            f"counters 不支持 mode={mode!r}；这张对照表只收录 "
            f"{'、'.join(pvp_counters.filter_modes())}。"
        )
    if wanted_period and wanted_period not in pvp_counters.PERIODS:
        raise InvalidArgumentError(
            f"counters 不支持 period={period!r}；可取 "
            f"{'、'.join(pvp_counters.PERIODS)}。"
        )

    def keep(counter: dict) -> bool:
        if keyword and keyword not in counter["name"].lower() and keyword not in counter["description"].lower():
            return False
        if wanted_mode and counter["mode"] != wanted_mode:
            return False
        if wanted_period and counter["period"] != wanted_period:
            return False
        return True

    return [counter for counter in counters if keep(counter)]


def _sort_key(counter: dict) -> tuple[int, int]:
    """数值降序；进度为 None 的排在最后（它们不是 0，只是没读到）。"""
    progress = counter["progress"]
    return (0 if progress is not None else 1, -(progress or 0))


def payload(
    counters: list[dict],
    manifest: ManifestManager,
    *,
    limit: int,
    unavailable: str = "",
    query: str = "",
    mode: str = "",
    period: str = "",
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
        "filter": {"query": query, "mode": mode, "period": period, "limit": limit},
        # 词表给出去，调用方（与读响应的人）才知道 mode/period 的取值从哪来、各有几个。
        "labels": {
            "modes": {
                family: mode_label(family, manifest)
                for family in pvp_counters.filter_modes()
            },
            "periods": pvp_counters.PERIOD_LABELS_ZH,
        },
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
        mode: str = "",
        period: str = "",
    ) -> dict:
        """游戏内生涯计数器：默认全量候选按数值降序，最多 20 条。

        口径要说清（调用方要照着讲给用户听）：组件 1100 是**游戏内那个计数器**，
        从 S1 起累计、含已删角色，和统计接口的账号级数字**不是一回事**。

        Args:
            player_name: 玩家 BungieName。
            query: 名称或描述的子串过滤（大小写不敏感），**匹配的是中文原文** ——
                传 `"熔炉"` / `"已击败对手"` / `"胜场"` 这类中文词；传英文（`crucible`、
                `trials`）会一条都匹配不到（真机实测 `query="crucible"` → 0 条），
                按模式筛请用 `mode=`，按周期筛用 `period=`。
            limit: 最多返回几条，默认 20；`total`/`truncated` 说明一共筛出多少。
            mode: 按对照表的模式家族筛（crucible/trials/iron_banner/competitive/gambit/raid）。
                三条件计数器的名字会重名，靠它区分；词表外的取值报 `invalid_argument_error`。
            period: 按周期筛（career/season/act）。`season` 是"本赛季"那批 ——
                统计接口没有赛季周期，赛季数字只能从这里拿。

        Returns:
            `{"counters": [...], "total", "returned", "truncated", "unavailable", "filter",
            "labels", "components"}`；读不到时 `counters=[]` 且 `unavailable` 写清原因，**不报成功**。
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
            empty = payload([], self._manifest, limit=limit, unavailable=reason, query=query, mode=mode, period=period)
            empty["warnings"] = []
            return empty

        parsed = parse_counters(raw_metrics, self._manifest)
        counters = filter_counters(parsed, query, mode, period)
        logger.info(
            "生涯计数器：player=%s 原始=%d 过滤后=%d query=%r mode=%r period=%r",
            player_name, len(raw_metrics), len(counters), query, mode, period,
        )
        result = payload(counters, self._manifest, limit=limit, query=query, mode=mode, period=period)
        # 可选数据的降级（名字查不到、进度缺失）只写进 warnings，不改 `counters` 的形状：
        # 每一行始终是同一组键，缺的是值，不是结构。
        result["warnings"] = self._degradation_warnings(counters)
        if query.strip() and result["total"] == 0 and parsed:
            # 别让"英文词匹配不到中文计数器"被读成"这个账号没有这条计数"：
            # 真机就踩过（`query="crucible"` → 0 条，而熔炉那批计数器明明在）。
            result["warnings"].append(
                f"query={query!r} 没有匹配到任何计数器：`query` 做的是**名称与描述的子串匹配**"
                "（既不是模式词、也不是模糊搜索）。按模式筛请用 mode=，按周期筛用 period=；"
                "按名字找先试中文名 —— 真机实测 `query=\"crucible\"` → 0 条，"
                "`query=\"熔炉\"` → 13 条（本账号的计数器名称来自中文 Manifest）。"
            )
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
        # 没分类不等于 PvE：对照表只收了常被问的那批，剩下的如实标 other/None，
        # 免得读的人把 mode="other" 当成"没有模式"。
        unclassified = sum(1 for counter in counters if counter["mode"] == "other")
        if unclassified:
            warnings.append(
                f"{unclassified} 条计数器不在模式对照表里（mode=\"other\"、period=null）："
                "这是**没有收录**，不是「没有模式」；要看它们就按 name/description 读。"
            )
        return warnings
