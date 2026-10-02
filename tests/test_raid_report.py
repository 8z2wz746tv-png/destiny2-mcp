"""突袭/地牢报表的守门测试。

守三件事，每件都对应一次真实踩过的坑：

1. **表自身的自洽**（任何机器都能跑）：副本名唯一、类型只有 raid/dungeon、
   计数器种类都在词表里、**同一个 hash 不许出现在两个副本上**（复制粘贴的典型后果）。
2. **表的 hash 在 Manifest 里真的存在、真的属于这个副本**（有本地 Manifest 才跑，没有就 skip）：
   拿 `DestinyMetricDefinition` 反查，描述里必须出现副本名，而且必须是 **all-time** 那一条
   （同一副本的「本周 / 本赛季 / 总人数」三个计数器**名字完全相同**，只能靠描述区分 —— 抓错就拿错数）。
3. **服务与信封的行为**：缺计数器给 `None` 而不是 0、`missing_counters` 说清是"这个副本没有"
   而不是"这次没读到"、组件 1100 读不到时**不报成功**、`unavailable` 永远在。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from destiny_mcp import config
from destiny_mcp.data import raids
from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.services.raid_report_service import RaidReportService
from destiny_mcp.services.raid_runs import RaidRunStore, RaidScanner, fresh_verdict
from destiny_mcp.tools import _raid_report_branches as branch

# ── 1. 表自身的自洽（不依赖 Manifest） ────────────────────────────────────────


def test_every_entry_has_a_known_kind_and_at_least_one_counter() -> None:
    for name, entry in raids.ENTRIES.items():
        assert entry.name == name, f"{name}: NamedTuple 的名字与 key 不一致"
        assert entry.kind in raids.KIND_LABELS_ZH, f"{name}: 类型 {entry.kind!r} 不在词表里"
        assert entry.counters, f"{name}: 一个计数器都没有，这行没有意义"


def test_counter_kinds_are_all_in_the_vocabulary() -> None:
    for name, entry in raids.ENTRIES.items():
        unknown = set(entry.counters) - set(raids.COUNTER_LABELS_ZH)
        assert not unknown, f"{name}: 计数器种类 {sorted(unknown)} 没有中文标签"
    assert set(raids.COUNTER_ORDER) <= set(raids.COUNTER_LABELS_ZH)


def test_no_metric_hash_is_shared_by_two_raids() -> None:
    """同一个 hash 出现在两个副本上 = 复制粘贴没改干净，会静默把两个副本的数字弄成一样。"""
    seen: dict[int, str] = {}
    for name, entry in raids.ENTRIES.items():
        for kind, metric_hash in entry.counters.items():
            assert metric_hash not in seen, (
                f"{name} 的 {kind} 与 {seen[metric_hash]} 用了同一个 hash {metric_hash}"
            )
            seen[metric_hash] = name


# ── 2. 对着 Manifest 核（没有本地库就 skip） ─────────────────────────────────


def _metric_definitions() -> dict[int, dict[str, Any]]:
    db = Path(config.DESTINY_MANIFEST_PATH) / "destiny_manifest_zh.sqlite3"
    if not db.is_file():
        pytest.skip(f"本地没有中文 Manifest（{db}），跳过与 Manifest 的对照")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("select id, json from DestinyMetricDefinition").fetchall()
    finally:
        conn.close()
    out: dict[int, dict[str, Any]] = {}
    for raw_id, raw_json in rows:
        definition = json.loads(raw_json)
        # `id` 列存的是有符号 hash，表里写的是无符号 —— 两边都要能对上
        out[int(raw_id) % 2**32] = definition
    return out


def test_every_hash_exists_in_the_manifest_and_belongs_to_its_raid() -> None:
    """hash 必须存在，且描述里必须出现副本名 —— 这是"表没抄错"的唯一机器判据。"""
    definitions = _metric_definitions()
    problems: list[str] = []
    for name, entry in raids.ENTRIES.items():
        for kind, metric_hash in entry.counters.items():
            definition = definitions.get(metric_hash)
            if definition is None:
                problems.append(f"{name}/{kind}: Manifest 里没有 metric {metric_hash}")
                continue
            display = definition.get("displayProperties") or {}
            text = f"{display.get('name') or ''} {display.get('description') or ''}"
            if name not in text:
                problems.append(
                    f"{name}/{kind}: metric {metric_hash} 的文案里没有副本名（{text.strip()[:40]!r}）"
                )
    assert not problems, "突袭表的 hash 与 Manifest 对不上：\n  " + "\n  ".join(problems)


def test_every_hash_is_the_all_time_counter() -> None:
    """描述里出现「本周 / 本赛季 / 本篇章 / 本次发布」的就是周期变体，**不能收进表**。

    同一副本这三个变体**名字完全相同**（如三条都叫「梦魇根源导师」），只能靠描述区分。
    """
    definitions = _metric_definitions()
    period_words = ("本周", "本赛季", "本篇章", "本次发布")
    offenders: list[str] = []
    for name, entry in raids.ENTRIES.items():
        for kind, metric_hash in entry.counters.items():
            definition = definitions.get(metric_hash)
            if definition is None:
                continue  # 上一条测试负责报"不存在"
            description = ((definition.get("displayProperties") or {}).get("description") or "")
            hit = [word for word in period_words if word in description]
            if hit:
                offenders.append(f"{name}/{kind}: metric {metric_hash} 是周期变体（{hit}）")
    assert not offenders, "表里混进了周期变体计数器：\n  " + "\n  ".join(offenders)


# ── 3. 服务与信封行为 ────────────────────────────────────────────────────────


class _FakeCounters:
    """替身：`read_metrics` 是 `ActivityCountersService` 抽出来的那个公开方法。"""

    def __init__(self, metrics: dict | None, reason: str = "") -> None:
        self._metrics = metrics if metrics is not None else {}
        self._reason = reason

    async def read_metrics(self, _player_name: str) -> tuple[dict, str]:
        return self._metrics, self._reason


def _metrics_for(*pairs: tuple[int, int | None]) -> dict:
    """构造组件 1100 形状的 metrics：`{hash: {"objectiveProgress": {"progress": n}}}`。"""
    out = {}
    for metric_hash, value in pairs:
        if value is None:
            continue
        out[str(metric_hash)] = {"objectiveProgress": {"progress": value}}
    return out


@pytest.mark.asyncio
async def test_missing_counter_is_none_and_named_in_missing_counters() -> None:
    """「平衡」没有完成数计数器 —— 那一格必须是 `None` 并进 `missing_counters`，**不是 0**。"""
    service = RaidReportService(_FakeCounters(_metrics_for((2041961731, 6))))
    report = await service.get_report("p", "dungeon")
    row = next(r for r in report["rows"] if r["activity"] == "平衡")
    assert row["completions"] is None
    assert "completions" in row["missing_counters"]
    # 它的导师计数读到了 6，就不该出现在 missing 里
    assert row["sherpas"] == 6
    assert "sherpas" not in row["missing_counters"]


@pytest.mark.asyncio
async def test_counter_present_but_unreadable_is_a_warning_not_a_zero() -> None:
    """表里有、这次没读到 → `None` + warning，绝不写成 0。"""
    service = RaidReportService(_FakeCounters(_metrics_for()))  # 一个值都不给
    report = await service.get_report("p", "raid")
    row = next(r for r in report["rows"] if r["activity"] == "深岩墓室")
    assert row["completions"] is None
    assert "completions" not in (row.get("missing_counters") or [])
    assert any("深岩墓室" in w for w in report["warnings"])


@pytest.mark.asyncio
async def test_unavailable_always_explains_the_columns_we_cannot_produce() -> None:
    service = RaidReportService(_FakeCounters(_metrics_for((954805812, 229))))
    report = await service.get_report("p", "raid")
    fields = {item["field"] for item in report["unavailable"]}
    assert {"full_clears", "fastest_seconds", "day_one_rank", "ranks"} <= fields
    for item in report["unavailable"]:
        assert item["reason"].strip(), f"{item['field']} 只写了字段名没写原因"


@pytest.mark.asyncio
async def test_unknown_mode_is_rejected_not_answered_with_an_empty_list() -> None:
    """"筛出来是空"和"你筛的词我不认识"是两件事。"""
    service = RaidReportService(_FakeCounters(_metrics_for()))
    with pytest.raises(InvalidArgumentError):
        await service.get_report("p", "crucible")


@pytest.mark.asyncio
async def test_counters_unavailable_is_not_reported_as_success() -> None:
    """组件 1100 读不到 → 信封 `ok=false`，不能回一张空表当成功。"""
    service = RaidReportService(_FakeCounters(None, "组件 1100 返回空：上游抖动"))
    svc = {"raid_report_svc": service}
    response = await branch.raid_report_response(svc, "p", "raid")
    assert response["ok"] is False
    assert "组件 1100" in response["error"]["message"]


@pytest.mark.asyncio
async def test_badges_only_appear_when_the_count_is_positive() -> None:
    """无瑕 = 0 不算徽章（"一次都没有"不该挂个徽章），有值才挂。"""
    metrics = _metrics_for((1034442994, 0), (1084707005, 3))
    service = RaidReportService(_FakeCounters(metrics))
    report = await service.get_report("p", "dungeon")
    row = next(r for r in report["rows"] if r["activity"] == "二象性")
    kinds = [b["kind"] for b in row["badges"]]
    assert "solo_flawless" in kinds
    assert "flawless" not in kinds


@pytest.mark.asyncio
async def test_totals_never_count_a_missing_counter_as_zero() -> None:
    metrics = _metrics_for((954805812, 5), (2330596844, 0))
    service = RaidReportService(_FakeCounters(metrics))
    report = await service.get_report("p", "raid")
    # 只有一个副本的完成数被读到（5），其余全是 None —— 合计就该是 5，不是"其余按 0 算"
    assert report["totals"]["completions"] == 5
    coverage = report["coverage"]["completions"]
    assert coverage["rows_with_counter"] == len(raids.by_kind("raid"))
    assert coverage["rows_total"] == len(raids.by_kind("raid"))


# ── 4. 副本 → 活动 hash 的归组表（P2 守门） ─────────────────────────────────


def _activity_rows() -> list[tuple[int, str, int]]:
    db = Path(config.DESTINY_MANIFEST_PATH) / "destiny_manifest_zh.sqlite3"
    if not db.is_file():
        pytest.skip(f"本地没有中文 Manifest（{db}），跳过归组表对照")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("select id, json from DestinyActivityDefinition").fetchall()
    finally:
        conn.close()
    out = []
    for raw_id, raw_json in rows:
        definition = json.loads(raw_json)
        display = definition.get("displayProperties") or {}
        out.append((
            int(raw_id) % 2**32,
            (display.get("name") or "").strip(),
            definition.get("activityTypeHash"),
        ))
    return out


def test_every_raid_or_dungeon_activity_is_grouped_or_explicitly_unknown() -> None:
    """每条突袭/地牢活动要么归到某个副本，要么明确是 `None` —— **不许悄悄归错**。

    这条是"新副本上线"的探针：Bungie 若再加一条逗号名（`A，B`）或新的难度后缀，
    `normalize_activity_name` 会返回 `None` 而不是猜一个副本，测试在这里说明白。
    """
    unhandled = []
    for _hash, name, activity_type in _activity_rows():
        if activity_type not in (2043403989, 608898761) or not name:
            continue
        if raids.normalize_activity_name(name) is None:
            unhandled.append(name)
    # `众神殿` / `特色安可` / `特色重置` 是官方另算的口径（没有对应的完成数计数器），
    # 不在本表范围里 —— 它们**应当**归不出来，列出来是为了让这条测试的意图可读。
    expected_unknown = [n for n in unhandled if n.startswith(("众神殿", "特色安可", "特色重置"))]
    assert len(expected_unknown) == len(unhandled), (
        f"这些突袭/地牢活动没有被归到任何副本：{sorted(set(unhandled) - set(expected_unknown))}"
    )


def test_frozen_hashes_match_the_manifest_by_difficulty() -> None:
    """表里钉住的 `hashes` 必须等于"按标准档从 Manifest 现算"的结果。

    现算的规则（与生成时一致）：名字归一化后等于副本名、且难度档是标准档
    （大师/巅峰/史诗/永恒 是另一档，拿它们算最短用时没有意义）。
    """
    derived: dict[str, set[int]] = {name: set() for name in raids.ENTRIES}
    for activity_hash, name, activity_type in _activity_rows():
        if activity_type not in (2043403989, 608898761) or not name:
            continue
        grouped = raids.normalize_activity_name(name)
        if grouped and raids.is_standard_difficulty(name):
            derived[grouped].add(activity_hash)
    mismatch = {
        name: {"表里": sorted(set(entry.hashes)), "现算": sorted(derived[name])}
        for name, entry in raids.ENTRIES.items()
        if set(entry.hashes) != derived[name]
    }
    assert not mismatch, f"归组表与 Manifest 对不上：{mismatch}"


def test_no_two_raids_share_an_activity_hash() -> None:
    seen: dict[int, str] = {}
    for name, entry in raids.ENTRIES.items():
        for activity_hash in entry.hashes:
            assert activity_hash not in seen, (
                f"{name} 与 {seen[activity_hash]} 共用了活动 hash {activity_hash}"
            )
            seen[activity_hash] = name


# ── 5. 「全程」的判据与逐场索引（P4） ───────────────────────────────────────


@pytest.mark.parametrize("row,expected", [
    ({"was_started_from_beginning": True, "period": "2020-01-01T00:00:00Z"}, "fresh"),
    ({"was_started_from_beginning": False, "period": "2024-06-07T18:00:00Z"}, "checkpoint"),
    # 修复日（2022-05-24）之前的 False 不可信：那时团灭一次就永久判非全程，Bungie 说不追溯
    ({"was_started_from_beginning": False, "period": "2021-05-22T18:00:00Z"}, "unknown"),
    # 字段缺失（2022-02-21 之前加的）也是 unknown，不是 checkpoint
    ({"was_started_from_beginning": None, "period": "2020-01-01T00:00:00Z"}, "unknown"),
])
def test_fresh_verdict_keeps_the_broken_window_unknown(row: dict, expected: str) -> None:
    assert fresh_verdict(row) == expected


def test_store_round_trip_and_survives_a_broken_line(tmp_path: Path) -> None:
    """JSONL 追加写：崩在半行上不该把整个索引判死，而且同 instance 后写的覆盖先写的。"""
    store = RaidRunStore(tmp_path / "raid_runs.jsonl")
    assert store.load() == {}  # 文件不存在不是错误
    store.append([{"instance_id": "a", "scanned": False}])
    store.append([{"instance_id": "a", "scanned": True}, {"instance_id": "b", "scanned": True}])
    with (tmp_path / "raid_runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"instance_id": "c", "brok\n')  # 写到一半崩了
    loaded = store.load()
    assert loaded["a"]["scanned"] is True  # 后写覆盖先写
    assert set(loaded) == {"a", "b"}  # 坏行被跳过，前面的行照样读得到


def _frozen(rows: list[dict]) -> RaidRunStore:
    class _Store(RaidRunStore):
        def load(self) -> dict:
            return {r["instance_id"]: r for r in rows}

        def rows_for(self, activity_hashes: set[int]) -> list[dict]:
            return [r for r in self.load().values() if r.get("reference_id") in activity_hashes]

    return _Store()


@pytest.mark.asyncio
async def test_pgcr_columns_use_only_fresh_completed_runs() -> None:
    """全程/最短用时：只认 fresh + 我自己完成；checkpoint 与非我完成的场次都不算。"""
    hashes = set(raids.ENTRIES["深岩墓室"].hashes)
    reference = sorted(hashes)[0]
    rows = [
        # 三个 fresh 完成：时长 3000 / 2192 / 4000 → 最短 2192
        {"instance_id": "1", "reference_id": reference, "scanned": True, "completed": 1,
         "duration_seconds": 3000, "period": "2024-01-01T00:00:00Z",
         "was_started_from_beginning": True},
        {"instance_id": "2", "reference_id": reference, "scanned": True, "completed": 1,
         "duration_seconds": 2192, "period": "2024-01-02T00:00:00Z",
         "was_started_from_beginning": True},
        # 没完成（半途退出）→ 不算全程、也不参与最短用时
        {"instance_id": "3", "reference_id": reference, "scanned": True, "completed": 0,
         "duration_seconds": 100, "period": "2024-01-03T00:00:00Z",
         "was_started_from_beginning": True},
        # 检查点开局 → 不算
        {"instance_id": "4", "reference_id": reference, "scanned": True, "completed": 1,
         "duration_seconds": 4000, "period": "2024-01-04T00:00:00Z",
         "was_started_from_beginning": False},
        # 坏窗口里的（字段不可信）→ 既不算进也不算掉
        {"instance_id": "5", "reference_id": reference, "scanned": True, "completed": 1,
         "duration_seconds": 50, "period": "2021-01-01T00:00:00Z",
         "was_started_from_beginning": None},
    ]
    service = RaidReportService(_FakeCounters(_metrics_for((954805812, 229))), None)
    service._store = _frozen(rows)
    report = await service.get_report("p", "raid")
    row = next(r for r in report["rows"] if r["activity"] == "深岩墓室")
    # 默认口径（含坏窗口那段，同 raid.report）：a、b 两场 + 坏窗口里的 e
    assert row["full_clears"] == 3
    # 严格口径（只认上游明确标记的）：只剩 a、b
    assert row["full_clears_strict"] == 2
    # **最短用时只从严格口径取**：e 那场 50 秒是检查点性质的短时长，混进来就成了"最快全程"
    assert row["fastest_seconds"] == 2192
    assert report["totals"]["full_clears"] == 3


@pytest.mark.asyncio
async def test_unscanned_raid_says_why_instead_of_reporting_zero() -> None:
    """没扫过的副本：两列 `None` + `unavailable` 指路，**不是 0**。"""
    service = RaidReportService(_FakeCounters(_metrics_for((954805812, 229))), None)
    service._store = _frozen([])
    report = await service.get_report("p", "raid")
    row = next(r for r in report["rows"] if r["activity"] == "深岩墓室")
    assert row["full_clears"] is None and row["fastest_seconds"] is None
    fields = {u["field"] for u in report["unavailable"]}
    assert {"full_clears", "fastest_seconds"} <= fields
    assert any("raid_scan" in u["reason"] for u in report["unavailable"])
    assert report["coverage"]["full_clears"]["rows_with_counter"] == 0


class _FakeBungie:
    """替身：只回历史与 PGCR，并记下被调了几次（验证"扫过的不会再扫"）。"""

    def __init__(self, history: list[dict], pgcr: dict[str, dict]) -> None:
        self._history = history
        self._pgcr = pgcr
        self.pgcr_calls: list[str] = []
        self.history_calls = 0
        self.history_params: list[dict] = []

    async def get_activity_history(self, *_a, **_kw) -> dict:
        self.history_calls += 1
        self.history_params.append(_a[3] if len(_a) > 3 else (_kw.get("params") or {}))
        return {"activities": self._history}

    async def get_pgcr(self, instance_id: str) -> dict:
        self.pgcr_calls.append(instance_id)
        return self._pgcr[instance_id]


class _FakeResolver:
    async def resolve_player(self, _name: str) -> dict:
        return {"membership_id": "4611686018000000001", "membership_type": 3}

    async def get_profile(self, *_a, **_kw) -> dict:
        return {"characters": {"data": {"c1": {}}}}


def _pgcr(instance_id: str, membership_id: str, duration: int, fresh: bool) -> dict:
    return {
        "period": "2024-01-01T00:00:00Z",
        "activityWasStartedFromBeginning": fresh,
        "activityDetails": {"instanceId": instance_id},
        "entries": [
            {"player": {"destinyUserInfo": {"membershipId": membership_id}},
             "values": {"activityDurationSeconds": {"basic": {"value": duration}},
                        "completed": {"basic": {"value": 1}}}},
            {"player": {"destinyUserInfo": {"membershipId": "9"}}, "values": {}},
        ],
    }


@pytest.mark.asyncio
async def test_scan_chunk_is_resumable_and_never_refetches(tmp_path: Path) -> None:
    """分块续扫：第一块扫完不再重复拉 PGCR，第二块接着扫剩下的。"""
    mine = "4611686018000000001"
    reference = sorted(raids.ENTRIES["深岩墓室"].hashes)[0]
    history = [{"activityDetails": {"instanceId": f"i{n}", "referenceId": reference}}
               for n in range(5)]
    pgcr = {f"i{n}": _pgcr(f"i{n}", mine, 1000 + n, True) for n in range(5)}
    bungie, store = _FakeBungie(history, pgcr), RaidRunStore(tmp_path / "runs.jsonl")
    scanner = RaidScanner(bungie, _FakeResolver(), store)
    entry = raids.ENTRIES["深岩墓室"]

    first = await scanner.scan_chunk("p", entry, 3)
    assert first["scanned"] == 3 and first["pending"] == 2 and first["total"] == 5
    second = await scanner.scan_chunk("p", entry, 3)
    assert second["scanned"] == 2 and second["pending"] == 0
    # 还有待扫的场次时**不再翻页**：翻一次 12.6 秒（实测 11 页 / 2095 场），
    # 分块续扫要来回许多次，每次都重翻纯属白花。
    assert bungie.history_calls == 1, "第二块又翻了一遍页（枚举缓存没生效）"
    third = await scanner.scan_chunk("p", entry, 3)
    assert third["scanned"] == 0 and third["pending"] == 0
    assert sorted(bungie.pgcr_calls) == ["i0", "i1", "i2", "i3", "i4"]  # 每场只拉一次


@pytest.mark.asyncio
async def test_scan_ignores_other_activities_and_unknown_names(tmp_path: Path) -> None:
    """别的副本的场次不该进索引；副本名不认识要报错，不是回空结果。"""
    mine = "4611686018000000001"
    other = sorted(raids.ENTRIES["最后一愿"].hashes)[0]
    history = [{"activityDetails": {"instanceId": "x1", "referenceId": other}}]
    bungie = _FakeBungie(history, {"x1": _pgcr("x1", mine, 500, True)})
    scanner = RaidScanner(bungie, _FakeResolver(), RaidRunStore(tmp_path / "runs.jsonl"))
    result = await scanner.scan_chunk("p", raids.ENTRIES["深岩墓室"], 10)
    assert result["total"] == 0 and bungie.pgcr_calls == []

    service = RaidReportService(_FakeCounters(_metrics_for()), scanner)
    with pytest.raises(InvalidArgumentError):
        await service.scan("p", "不存在的副本", 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected_mode", [("深岩墓室", 4), ("破碎王座", 82)])
async def test_scan_asks_for_the_mode_of_that_activity_kind(
    tmp_path: Path, name: str, expected_mode: int,
) -> None:
    """按副本类型传对应的 `modeType`。

    真机 2026-10-01 的缺陷：写死突袭模式（4），于是**11 个地牢全扫出 0 场** ——
    表面上像"你没打过地牢"，实际是历史查询问错了模式。地牢是 82。
    """
    bungie = _FakeBungie([], {})
    scanner = RaidScanner(bungie, _FakeResolver(), RaidRunStore(tmp_path / "runs.jsonl"))
    await scanner.scan_chunk("p", raids.ENTRIES[name], 1)

    assert bungie.history_params, "没有发历史请求"
    assert all(p.get("mode") == expected_mode for p in bungie.history_params), (
        f"{name} 用了错误的模式：{[p.get('mode') for p in bungie.history_params]}，期望 {expected_mode}"
    )
