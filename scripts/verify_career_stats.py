#!/usr/bin/env python
"""真机核对：生涯统计三档（P1）与两个来源并列（P2）。

只读脚本 —— 只调 `GetHistoricalStatsForAccount` / `GetHistoricalStats` / `GetProfile`（组件 1100）
与本地 Manifest，**不做任何写入**。它回答的是单测替身回答不了的两个问题：

1. **三档数字与真机是否一致**，以及"账号级 = 现存 + 已删"这条公式是不是在**每一项**上都成立
   （2026-09-17 实采：熔炉生涯击败 50,622 / 28,242 / 78,864）；
2. **两个来源是否并列给全**：游戏计数器（124,495，`profile.metrics`）与统计接口
   （78,864，`GetHistoricalStatsForAccount`），差值 45,631 是否写进 warnings。

    .venv/bin/python scripts/verify_career_stats.py

判据（数字来自 docs/plans/PVP_STATS_PLAN.md 的实采，2026-09-17；本脚本 2026-09-18 复采一致）：

0. 模式/周期：`mode="crucible"` 自行合并的结果与上游账号级 allPvP 逐项一致；
   `mode="trials"` 给得出试炼的 K/D；`period="season"` 如实报取不到（上游没有 Season）；
1. `activity_assistant(intent="stats")` 默认账号级，pvp 的 `opponents_defeated` 行三档 = 50,622 /
   28,242 / 78,864，且 `account_total == existing + deleted`（107,106 那个错不能再回来）；
2. 三档公式在**所有**行上成立：sum 类相加、max 类取最大、min 类取最小、none 类 `existing=null`；
3. 比值类（derived）的公式：拿 8 条角色的分量重算，与上游账号级的值在 1e-3 内一致；
4. `data.game_counters` 里 811894228 的 progress = 124,495、source = `profile.metrics`；
   warnings 里有差值 45,631 与"拆不出来"的说明；
5. 计数器读不到时**只降级**：`counters_unavailable` 有原因、`ok` 仍为 true（空 ≠ 0）；
6. 显式 `character="hunter"` 时 `scope=character` + 角色名，且行里没有三档键。

退出码 0 = 六条都成立；非 0 = 有一条不成立（看 FAIL 行的证据）。
"""

import asyncio
import sys

from destiny_mcp import activity_stats
from destiny_mcp.server import create_server, app_lifespan
from destiny_mcp.tools.assistants import activity_assistant

# 2026-09-17 实采、2026-09-18 复采一致（会随游玩增长，所以这里钉的是"当天真机"）。
EXPECTED_EXISTING = 50622
EXPECTED_DELETED = 28242
EXPECTED_ACCOUNT_TOTAL = 78864
OPPONENTS_DEFEATED = 811894228
EXPECTED_COUNTER = 124495
EXPECTED_DIFFERENCE = EXPECTED_COUNTER - EXPECTED_ACCOUNT_TOTAL

RESULTS: list[tuple[str, bool, str]] = []


def check(row: str, ok: bool, evidence: str) -> None:
    RESULTS.append((row, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'} | {row}\n       {evidence}")


def _rows(payload: dict) -> list[dict]:
    return [row for group in payload.get("groups") or [] for row in group.get("stats") or []]


def _row(payload: dict, group_key: str, stat_id: str) -> dict:
    for group in payload.get("groups") or []:
        if group.get("key") != group_key:
            continue
        for row in group.get("stats") or []:
            if row.get("stat_id") == stat_id:
                return row
    return {}


async def main() -> int:
    async with app_lifespan(create_server("normal")) as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})})()

        # ── ① 三档数字 ────────────────────────────────────────────────────────
        response = await activity_assistant(ctx=ctx, intent="stats")
        if not response.get("ok"):
            check("① intent='stats' 可用", False,
                  f"ok=false code={response.get('error', {}).get('code')} "
                  f"message={response.get('error', {}).get('message')}")
            return 1
        stats = response["data"]["stats"]
        defeated = _row(stats, "pvp", "opponents_defeated")
        check(
            "① 账号级默认 + pvp 熔炉生涯击败三档 = 50,622 / 28,242 / 78,864",
            stats.get("scope") == "account"
            and stats.get("source") == "GetHistoricalStatsForAccount"
            and defeated.get("existing") == EXPECTED_EXISTING
            and defeated.get("deleted") == EXPECTED_DELETED
            and defeated.get("account_total") == EXPECTED_ACCOUNT_TOTAL
            and defeated.get("account_total") == defeated.get("existing") + defeated.get("deleted"),
            f"scope={stats.get('scope')} source={stats.get('source')} characters={stats.get('characters')} "
            f"| existing={defeated.get('existing')} deleted={defeated.get('deleted')} "
            f"account_total={defeated.get('account_total')} | 行={defeated}",
        )

        # ── ② 三档公式在每一项上都成立 ─────────────────────────────────────────
        all_rows = _rows(stats)
        mismatched = []
        for row in all_rows:
            kind = row.get("aggregate")
            existing, deleted, total = row.get("existing"), row.get("deleted"), row.get("account_total")
            if kind == activity_stats.AGGREGATE_NONE:
                if existing is not None:
                    mismatched.append(f"{row['stat_id']}: none 类却有 existing={existing}")
                continue
            if kind == activity_stats.AGGREGATE_DERIVED or None in (existing, deleted, total):
                continue
            expected = (
                existing + deleted if kind == activity_stats.AGGREGATE_SUM
                else max(existing, deleted) if kind == activity_stats.AGGREGATE_MAX
                else min(existing, deleted) if kind == activity_stats.AGGREGATE_MIN
                else None
            )
            if expected is not None and abs(total - expected) > 1e-3:
                mismatched.append(f"{row['stat_id']}({kind}): {total} != {expected}")
        check(
            "② 三档公式逐项成立（sum 相加 / max 取最大 / min 取最小 / none 给 null）",
            not mismatched and all_rows,
            f"行数={len(all_rows)} 不一致={mismatched[:6]} "
            f"（sum={sum(1 for r in all_rows if r.get('aggregate') == 'sum')} "
            f"max={sum(1 for r in all_rows if r.get('aggregate') == 'max')} "
            f"min={sum(1 for r in all_rows if r.get('aggregate') == 'min')} "
            f"derived={sum(1 for r in all_rows if r.get('aggregate') == 'derived')} "
            f"none={sum(1 for r in all_rows if r.get('aggregate') == 'none')}）",
        )

        # ── ③ 比值公式：拿 8 条角色的分量重算，与上游账号级值比 ──────────────────
        act = svc["activity_svc"]
        player = await act._resolver.resolve_player(await _player_name(svc))
        mid, mtype = str(player["membership_id"]), int(player["membership_type"])
        raw = await act._bungie.get_historical_stats_for_account(mtype, mid)
        characters = [c for c in (raw.get("characters") or []) if isinstance(c, dict)]
        derived_mismatch = []
        for group_key, upstream in (("pve", "allPvE"), ("pvp", "allPvP")):
            sections = [
                (((c.get("results") or {}).get(upstream) or {}).get("allTime")) or {}
                for c in characters
            ]
            computed = activity_stats.merge_sections(sections)
            for stat_id, (components, _formula) in activity_stats.DERIVED_STATS.items():
                row = _row(stats, group_key, activity_stats._snake(stat_id))
                if not row:
                    continue
                expected = (computed.get(stat_id) or {}).get("basic", {}).get("value")
                actual = row.get("account_total")
                if expected is None or actual is None or abs(actual - expected) > 1e-3:
                    derived_mismatch.append(f"{group_key}.{stat_id}: 表里 {actual} vs 重算 {expected}")
        check(
            "③ 比值类公式正确（重算 8 条角色 == 上游账号级，1e-3 内）",
            not derived_mismatch,
            f"不一致={derived_mismatch or '无'}（核对 {len(activity_stats.DERIVED_STATS)} 个公式 × 2 组）",
        )

        # ── ④ 两个来源并列（P2）────────────────────────────────────────────────
        counters = response["data"].get("game_counters") or []
        counter = next((row for row in counters if row.get("metric_hash") == OPPONENTS_DEFEATED), None)
        warnings_text = " ".join(response.get("warnings") or [])
        check(
            "④ game_counters 给计数器 124,495，warnings 说明与统计接口差 45,631",
            counter is not None
            and counter.get("progress") == EXPECTED_COUNTER
            and counter.get("source") == "profile.metrics"
            and str(EXPECTED_DIFFERENCE) in warnings_text
            and "不是我们能拆出来的部分" in warnings_text,
            f"counter={counter} | warnings={warnings_text[:260]}",
        )

        # ── ⑤ 计数器读不到只降级（空 ≠ 0）──────────────────────────────────────
        original = svc["activity_counters_svc"]._read_metrics
        counters_module = sys.modules["destiny_mcp.services.activity_counters_service"]
        original_delay = counters_module.DELAY_SECONDS
        counters_module.DELAY_SECONDS = 0

        async def always_empty(membership_id: str, membership_type: int) -> dict:
            return {}

        svc["activity_counters_svc"]._read_metrics = always_empty
        try:
            degraded = await activity_assistant(ctx=ctx, intent="stats")
        finally:
            svc["activity_counters_svc"]._read_metrics = original
            counters_module.DELAY_SECONDS = original_delay
        degraded_stats = (degraded.get("data") or {}).get("stats") or {}
        degraded_reason = (degraded.get("data") or {}).get("counters_unavailable") or ""
        check(
            "⑤ 计数器读不到 → 统计结果照给，counters_unavailable 写明原因（ok 仍为 true）",
            degraded.get("ok") is True
            and _row(degraded_stats, "pvp", "opponents_defeated").get("account_total") == EXPECTED_ACCOUNT_TOTAL
            and "组件 1100" in degraded_reason
            and any("没读到" in warning or "不等于" in warning for warning in degraded.get("warnings") or []),
            f"ok={degraded.get('ok')} counters_unavailable={degraded_reason[:80]!r} "
            f"三档仍在={_row(degraded_stats, 'pvp', 'opponents_defeated').get('account_total')}",
        )

        # ── ⑥ 显式单角色：scope=character + 角色名，行里没有三档 ─────────────────
        single = await activity_assistant(ctx=ctx, intent="stats", character="hunter")
        single_stats = (single.get("data") or {}).get("stats") or {}
        single_rows = _rows(single_stats)
        check(
            "⑥ character='hunter' → scope=character + 角色名，行里不给三档",
            single.get("ok") is True
            and single_stats.get("scope") == "character"
            and single_stats.get("source") == "GetHistoricalStats"
            and (single_stats.get("character") or {}).get("name") == "Hunter"
            and single_rows
            and all("account_total" not in row for row in single_rows),
            f"scope={single_stats.get('scope')} character={single_stats.get('character')} "
            f"行数={len(single_rows)} 组={[g.get('key') for g in single_stats.get('groups') or []]}",
        )

        # ── ⑦ P3-2：mode=crucible(5) 的账号级合并必须复现上游 allPvP 的账号级数字 ──
        #    这是最硬的一条：按模式的账号级合计是我们自己从 8 条角色合出来的，
        #    而 mode=5 对应的上游组正是 allPvP —— 两者应当逐项一致（比值允许四位精度差）。
        crucible = await activity_assistant(ctx=ctx, intent="stats", mode="crucible")
        crucible_stats = (crucible.get("data") or {}).get("stats") or {}
        account_rows = {row["stat_id"]: row for row in _rows(stats)}
        mismatched = []
        checked = 0
        for row in _rows(crucible_stats):
            reference = account_rows.get(row["stat_id"])
            if row.get("aggregate") == "none" or reference is None:
                continue
            expected = reference.get("account_total")
            actual = row.get("account_total")
            if expected is None or actual is None:
                continue
            checked += 1
            if abs(actual - expected) > 1e-3:
                mismatched.append(f"{row['stat_id']}: 自行合并 {actual} vs 上游 allPvP {expected}")
        check(
            "⑦ mode='crucible' 自行合并 == 上游账号级 allPvP（逐项，1e-3 内）",
            crucible.get("ok") is True
            and crucible_stats.get("aggregation") == "computed"
            and (crucible_stats.get("mode") or {}).get("upstream_group") == "allPvP"
            and checked > 50
            and not mismatched,
            f"mode={crucible_stats.get('mode')} 核对={checked} 项 不一致={mismatched[:5] or '无'}",
        )

        # ── ⑧ 试炼：按模式给得出；本赛季如实报取不到（上游没有 Season） ────────────
        trials = await activity_assistant(ctx=ctx, intent="stats", mode="trials")
        trials_stats = (trials.get("data") or {}).get("stats") or {}
        trials_kd = _row(trials_stats, "trials", "kills_deaths_ratio")
        untouched = dict(trials_stats)
        season = await activity_assistant(ctx=ctx, intent="stats", mode="trials", period="season")
        check(
            "⑧ 试炼按模式给得出；period='season' 如实报 unavailable（不降级成生涯、不试 500 参数）",
            trials.get("ok") is True
            and (trials_stats.get("mode") or {}).get("upstream_modes") == 84
            and (trials_stats.get("mode") or {}).get("upstream_group") == "trials_of_osiris"
            and trials_kd.get("account_total") is not None
            and season.get("ok") is False
            and "unavailable" in season.get("error", {}).get("message", "")
            and any("counters" in str(action) for action in season.get("next_actions") or [])
            and trials_stats == untouched,
            f"试炼 mode={trials_stats.get('mode')} K/D={trials_kd.get('account_total')}"
            f"（{trials_kd.get('display')}）角色={trials_stats.get('characters')} "
            f"| season → ok={season.get('ok')} code={season.get('error', {}).get('code')} "
            f"msg={season.get('error', {}).get('message', '')[:90]}",
        )

    failed = [row for row, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 条通过")
    return 1 if failed else 0


async def _player_name(svc) -> str:
    """默认玩家：与工具层同一条路（.env 里配的那个）。"""
    from destiny_mcp.tools._helpers import resolve_player_name

    return resolve_player_name(None)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
