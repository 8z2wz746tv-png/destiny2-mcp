"""突袭/地牢的逐场索引：把算「全程次数」与「最短用时」需要的几个字段抠出来存下。

## 为什么不能直接用 `pgcr_cache`

那个缓存是给「最近 N 场」用的：上限 2000 个文件、按 mtime 淘汰。一次全量扫描会把它
**自己转出去**（2095 场 ≈ 100 MB），第二遍又得重拉。这里存的是派生出来的小行
（一条约 150 字节，2095 场 ≈ 300 KB），可以一直留着，而且**只增不改**。

## 实测（2026-09-30，真机）

- 枚举：`GetActivityHistory` 按 `mode=4`（突袭）翻页，11 页拿到 2095 场，12.6 秒；
- 取数：**PGCR 串行 0.88 秒/场；并发 4/8/12 都是 1.1~1.3 场/秒** —— 并发没用，
  瓶颈在上游单场延迟，不在我们的请求速率；
- 于是：全扫 2095 场 ≈ 26 分钟、深岩墓室 336 场 ≈ 4.5 分钟、往日之苦 24 场 ≈ 20 秒。

**结论：扫描必须按副本、分块、可续。** 一次调用永远扫不完，所以这里提供
`scan_chunk(...)`（扫一块就返回，告诉你还剩多少），而不是一个"扫到底"的函数。

## 存的是什么字段（以及为什么存原始值）

只存从 PGCR 里读出来的**原始事实**：时长、`activityWasStartedFromBeginning`、
`startingPhaseIndex`、我这一场完成没完成、人数。**不存"是不是全程"这个判断** ——
那是政策（RaidHub 默认把坏窗口的场次算检查点，raid.report 算全程），
政策会变、原始值不会（见 `docs/reference/bungie_api.md` 16.1 与 `fresh_verdict`）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from .. import config
from ..data import activity_modes
from ..logging_config import get_logger

logger = get_logger(__name__)

#: 索引文件名（放缓存目录，但它**不是**可淘汰缓存：见模块开头的说明）
STORE_NAME = "raid_runs.jsonl"

#: 按副本类型取 `modeType`（唯一出处是 `data/activity_modes`，这里只引用它）。
#: **必须按类型分别传**：写死突袭那一个，地牢场次根本不在突袭模式的历史里 ——
#: 真机后果是 11 个地牢全扫出 0 场（看起来像"你没打过地牢"）。
_MODE_BY_KIND = {
    "raid": int(activity_modes.MODES["raid"]["mode_type"]),
    "dungeon": int(activity_modes.MODES["dungeon"]["mode_type"]),
}

#: 一次扫描最多拉多少场（防一手"用户没给 count"时打满上游）
MAX_CHUNK = 250

#: `GetActivityHistory` 单页上限（上游硬限制）
_PAGE_SIZE = 250


def store_path() -> Path:
    """索引文件路径：`DESTINY_CACHE_PATH/raid_runs.jsonl`。"""
    return Path(config.DESTINY_CACHE_PATH, STORE_NAME)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


class RaidRunStore:
    """JSONL 追加写的逐场索引。同一 `instance_id` 后写的覆盖先写的。

    追加写是崩溃安全的：崩在半个文件上，坏的是最后一行，前面的照样能读 ——
    所以 `load()` 跳过解析不了的行而不是整个报错。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or store_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, dict[str, Any]]:
        """读全量：`instance_id` → 行。文件不存在就是空（不是错误）。"""
        if not self._path.is_file():
            return {}
        out: dict[str, dict[str, Any]] = {}
        broken = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    broken += 1
                    continue  # 崩在半行上：跳过它，别把整个索引判死
                instance_id = row.get("instance_id") if isinstance(row, dict) else None
                if isinstance(instance_id, str) and instance_id:
                    out[instance_id] = row
        if broken:
            logger.warning("逐场索引有 %d 行读不出来（写入中断的残留），已跳过", broken)
        return out

    def append(self, rows: Iterable[dict[str, Any]]) -> int:
        """追加若干行，返回写入条数。"""
        rows = [r for r in rows if r.get("instance_id")]
        if not rows:
            return 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(rows)

    def rows_for(self, activity_hashes: set[int]) -> list[dict[str, Any]]:
        """取某个副本（一组活动 hash）的已索引场次。"""
        return [
            row
            for row in self.load().values()
            if _int_or_none(row.get("reference_id")) in activity_hashes
        ]


def fresh_verdict(row: dict[str, Any]) -> str:
    """这一场算不算「全程」—— 返回 `"fresh"` / `"checkpoint"` / `"unknown"`。

    判据来自上游那个字段，而它**历史上坏过三段**（`docs/reference/bungie_api.md` 16.1）：
    Beyond Light 之前只看 `startingPhaseIndex`；BL→巫后彻底坏、Bungie 明说"永远不会修"；
    2022-05-24 修好后**不追溯**（之前的场次团灭一次就永久 False）。

    所以这里如实分三档，而不是硬判成二值：

    - 字段是 `True` → `fresh`（可信）；
    - 字段是 `False` 且场次在 2022-05-24 之后 → `checkpoint`（可信）；
    - 其余（字段缺失，或早于修复日）→ `unknown` —— **不算进全程，也不算掉**，
      由调用方决定要不要把它当分母。
    """
    was_started = _bool_or_none(row.get("was_started_from_beginning"))
    if was_started is True:
        return "fresh"
    if was_started is False and _after_fresh_fix(row.get("period")):
        return "checkpoint"
    return "unknown"


#: `activityWasStartedFromBeginning` 的修复日（Bungie 2022-05-24 修好且不追溯）
_FRESH_FIX = "2022-05-24"


def _after_fresh_fix(period: Any) -> bool:
    return isinstance(period, str) and period[:10] >= _FRESH_FIX


def row_from_pgcr(pgcr: dict[str, Any], membership_id: str, reference_id: int) -> dict[str, Any] | None:
    """把一条 PGCR 压成索引里的一行。认不出自己那一份就返回 `None`。

    时长与完成度都取**我自己那份** `entries[]` —— 实测这样算出来的最短用时与截图逐秒吻合
    （深岩墓室 2192s / 救赎花园 1843s / 破碎王座 885s）。人数取 `len(entries)`
    **不是** `playerCount`（实测 6 人突袭里出现过 7/8）。
    """
    entries = pgcr.get("entries")
    if not isinstance(entries, list) or not entries:
        return None
    mine = None
    for entry in entries:
        info = (entry or {}).get("player") or {}
        if str((info.get("destinyUserInfo") or {}).get("membershipId") or "") == str(membership_id):
            mine = entry
            break
    if mine is None:
        return None
    values = mine.get("values") or {}

    def basic(key: str) -> Any:
        slot = values.get(key)
        return (slot or {}).get("basic", {}).get("value") if isinstance(slot, dict) else None

    details = pgcr.get("activityDetails") or {}
    return {
        "instance_id": str(details.get("instanceId") or ""),
        "reference_id": reference_id,
        "period": pgcr.get("period"),
        "duration_seconds": _int_or_none(basic("activityDurationSeconds")),
        "completed": _int_or_none(basic("completed")),
        "was_started_from_beginning": _bool_or_none(pgcr.get("activityWasStartedFromBeginning")),
        "starting_phase_index": _int_or_none(pgcr.get("startingPhaseIndex")),
        "player_count": len(entries),
        "scanned_at": int(time.time()),
    }


def row_from_history(details: dict[str, Any]) -> dict[str, Any] | None:
    """把一条 `GetActivityHistory` 记录压成索引里的"待扫"行（不花 PGCR 调用）。

    这一行只证明"这场存在"，`scanned` 为假。它的价值是**让第二次扫描不用重新翻页**：
    翻页本身要 12.6 秒（11 页 / 2095 场，实测），而这行的成本约 100 字节。
    """
    instance_id = str(details.get("instanceId") or "")
    reference_id = _int_or_none(details.get("referenceId"))
    if not instance_id or reference_id is None:
        return None
    return {
        "instance_id": instance_id,
        "reference_id": reference_id,
        "period": details.get("period"),
        "scanned": False,
    }


class RaidScanner:
    """按副本、分块、可续的扫描器。

    一次调用**扫不完**（全量 2095 场 ≈ 26 分钟，实测），所以这里只做一块：
    翻页找齐这个副本的所有场次 → 挑出还没扫的 → 最多取 `limit` 场 PGCR → 落盘 → 报还剩多少。
    """

    def __init__(self, bungie: Any, resolver: Any, store: RaidRunStore | None = None) -> None:
        self._bungie = bungie
        self._resolver = resolver
        self._store = store or RaidRunStore()

    @property
    def store(self) -> RaidRunStore:
        """报表服务读它（PGCR 那两列只有这一个来源）。"""
        return self._store

    async def scan_chunk(self, player_name: str, entry: Any, limit: int) -> dict[str, Any]:
        """扫一块。返回 `{"scanned", "pending", "total", "pages", "unavailable"}`。"""
        limit = max(1, min(int(limit or 1), MAX_CHUNK))
        player = await self._resolver.resolve_player(player_name)
        mid, mtype = str(player["membership_id"]), int(player["membership_type"])

        # 1) **先看本地有没有待扫的**：有就不再翻页。
        # 翻页枚举一次 12.6 秒（11 页 / 2095 场，实测），而分块续扫要来回调很多次 ——
        # 每次都重翻一遍纯属白花。索引里已经躺着全部场次（`scanned: False` 的那些），
        # 所以只在"本地没有待扫的"时才翻页（这时翻页是为了发现新打的场次）。
        indexed = self._store.load()
        wanted = set(entry.hashes)
        pending = [
            r for r in indexed.values()
            if _int_or_none(r.get("reference_id")) in wanted and not r.get("scanned")
        ]
        pages = 0
        if not pending:

            profile = await self._resolver.get_profile(mid, mtype, [200])
            chars = list(((profile.get("characters") or {}).get("data") or {}))
            discovered: list[dict] = []
            for char_id in chars:
                page = 0
                while True:
                    response = await self._bungie.get_activity_history(
                        mtype, mid, char_id,
                        {"count": _PAGE_SIZE, "mode": _MODE_BY_KIND.get(entry.kind, 0), "page": page},
                    )
                    activities = response.get("activities") or []
                    pages += 1
                    for item in activities:
                        details = item.get("activityDetails") or {}
                        if _int_or_none(details.get("referenceId")) not in wanted:
                            continue
                        instance_id = str(details.get("instanceId") or "")
                        if not instance_id or instance_id in indexed:
                            continue
                        row = row_from_history(details)
                        if row:
                            discovered.append(row)
                            indexed[instance_id] = row  # 同一页里可能重复出现
                    if len(activities) < _PAGE_SIZE:
                        break
                    page += 1
            self._store.append(discovered)

        # 2) 挑还没扫的，扫一块（翻页后可能有新发现的场次，重新算一次）
        mine = [r for r in indexed.values() if _int_or_none(r.get("reference_id")) in wanted]
        pending = [r for r in mine if not r.get("scanned")]
        batch = pending[:limit]
        done, failed = [], 0
        for row in batch:
            try:
                pgcr = await self._bungie.get_pgcr(row["instance_id"])
            except Exception as exc:  # noqa: BLE001 - 单场失败不该让整块失败
                logger.warning("PGCR %s 拉取失败：%s", row["instance_id"], exc)
                failed += 1
                continue
            fresh = row_from_pgcr(pgcr, mid, int(row["reference_id"]))
            if fresh:
                fresh["scanned"] = True
                done.append(fresh)
        self._store.append(done)
        logger.info(
            "扫描 %s：本块 %d 场（失败 %d），共 %d 场，还剩 %d 场",
            entry.name, len(done), failed, len(mine), max(0, len(pending) - len(done)),
        )
        return {
            "scanned": len(done),
            "failed": failed,
            "pending": max(0, len(pending) - len(done)),
            "total": len(mine),
            "pages": pages,
            "unavailable": (
                f"有 {failed} 场 PGCR 拉取失败（上游），这一块没算进去；下次扫描会重试它们。"
                if failed else ""
            ),
        }
