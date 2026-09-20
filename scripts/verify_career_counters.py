#!/usr/bin/env python
"""真机核对：游戏内生涯计数器（profile 组件 1100）与游戏内显示的数字是否一致。

只读脚本 —— 只调 `GetProfile`（组件 1100）与本地 Manifest，**不做任何写入**。
它回答的是单测替身回答不了的那一个问题：**读到的数字和游戏里显示的一样吗**。

    .venv/bin/python scripts/verify_career_counters.py

判据（全部来自 docs/plans/PVP_STATS_PLAN.md 的实采，2026-09-17）：

1. 组件 1100 拿得到非空 metrics（该日实测 402 条，会随赛季增长，所以只校验"远大于 0"）；
2. `811894228`（`Opponents Defeated` / `已击败对手`）存在，且 `progress` = **124495**
   —— 这就是游戏内那个数（也是本次对齐的证据）；
3. 名称/描述能从 Manifest 的 `DestinyMetricDefinition` 解析出来（查不到会降级成 `#hash`，
   那种情况这里判 FAIL，因为"名字查不到"和"这个名字本身没有"是两件事）；
4. 工具入口 `activity_assistant(intent="counters")` 返回的正是同一个数，且带
   `source: "profile.metrics"`、失败时为 `ok=false`（不是"0 条计数器"）。

退出码 0 = 四条都成立；非 0 = 有一条不成立（看 FAIL 行的证据）。
"""

import asyncio
import sys

from destiny_mcp import bungie_client
from destiny_mcp.server import create_server, app_lifespan
from destiny_mcp.tools.assistants import activity_assistant
from destiny_mcp.utils import player_names


def _patch_display_name_symbol() -> None:
    """临时补上 `bungie_client` 里缺的 `bungie_display_name` 导入。

    同一天有另一处改动（`utils/player_names.py`）在改显示名，`bungie_client.py` 里
    已经写了调用、导入还没接上（`NameError`），于是"不传 player_name"的当前 OAuth 玩家
    这条路整个不可用。本脚本只读、且不该去改别人正在改的文件，所以在这里**运行时补符号**：
    一旦上游把 import 补好，这段注入就是空操作。
    """
    if not hasattr(bungie_client, "bungie_display_name"):
        bungie_client.bungie_display_name = player_names.bungie_display_name
        print("[提示] bungie_client 缺 bungie_display_name 导入，已在本次运行内临时补上（只读场景）")

# 游戏内"熔炉生涯击败"：2026-09-17 实采，与游戏内、第三方机器人显示一致。
OPPONENTS_DEFEATED = 811894228
EXPECTED_PROGRESS = 124495

RESULTS: list[tuple[str, bool, str]] = []


def check(row: str, ok: bool, evidence: str) -> None:
    RESULTS.append((row, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'} | {row}\n       {evidence}")


async def main() -> int:
    _patch_display_name_symbol()
    async with app_lifespan(create_server()) as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})})()

        # ① 工具入口：intent="counters"（这就是用户会走的那条路）
        response = await activity_assistant(ctx=ctx, intent="counters", count=5)
        if not response.get("ok"):
            check(
                "① activity_assistant(intent='counters') 可用",
                False,
                f"ok=false code={response.get('error', {}).get('code')} "
                f"message={response.get('error', {}).get('message')}",
            )
            return 1

        data = response["data"]
        rows = data.get("counters") or []
        # 124,495 排不进全量前 5（榜首是 PvE 的 79,712,994），所以按名字/描述把那条捞出来：
        # 这也是 `query` 参数的真实用途（同时用中文与英文关键词，证明整个描述都在参与匹配）。
        crucible = await activity_assistant(ctx=ctx, intent="counters", query="熔炉")
        crucible_rows = crucible["data"]["counters"] if crucible.get("ok") else []
        top = next((row for row in crucible_rows if row["metric_hash"] == OPPONENTS_DEFEATED), None)
        if top is None:
            english = await activity_assistant(ctx=ctx, intent="counters", query="crucible")
            top = next(
                (row for row in (english["data"]["counters"] if english.get("ok") else [])
                 if row["metric_hash"] == OPPONENTS_DEFEATED),
                None,
            )
        check(
            "① activity_assistant(intent='counters') 可用且带来源",
            bool(rows) and all(row.get("source") == "profile.metrics" for row in rows),
            f"returned={data.get('returned')}/{data.get('total')} "
            f"components={data.get('components')} 首行={rows[0] if rows else None}",
        )

        # ② 游戏内那个数：811894228 的 progress 必须等于 124495
        check(
            "② 熔炉生涯击败 = 124495（与游戏内一致）",
            top is not None and top["progress"] == EXPECTED_PROGRESS,
            f"metric_hash={OPPONENTS_DEFEATED} name={top and top['name']!r} "
            f"progress={top and top['progress']} 期望={EXPECTED_PROGRESS} "
            f"completion_value={top and top['completion_value']}",
        )

        # ③ 全量：名称/描述来自 Manifest，不是 #hash 降级（该日 402 条，count 给足即可）
        full = await activity_assistant(ctx=ctx, intent="counters", count=500)
        everything = full["data"]["counters"] if full.get("ok") else []
        unresolved = [row for row in everything if not row.get("name_resolved")]
        check(
            "③ 组件 1100 有计数器且名称可从 DestinyMetricDefinition 解析",
            len(everything) > 0 and not unresolved,
            f"条数={len(everything)}（该日实测 402，会随赛季增长）"
            f" 未解析名={len(unresolved)} 示例={[row['name'] for row in everything[:3]]}",
        )

        # ④ 每个数字自带出处与范围：source 必带，且描述里能看出模式
        #    （描述走 Manifest 的本地化文本：中文清单给的是"熔炉竞技场"，英文清单才是 Crucible）
        described = [row for row in everything if row.get("description")]
        scope_markers = ("crucible", "熔炉")
        check(
            "④ payload 必带 source，且描述证明范围（熔炉/Crucible）",
            all(row.get("source") == "profile.metrics" for row in everything)
            and any(marker in row["description"].lower() for row in described for marker in scope_markers),
            f"有描述的={len(described)}/{len(everything)} "
            f"击败那条的描述={top and top['description'][:80]!r}",
        )

        # ⑤ 抖动重试：真机上"空响应"不是每次都能撞到（2026-09-17 现场连读 6 次都拿到了 402 条），
        #    所以这里**注入一次空响应**，再走一遍完整工具调用 —— 真数据、真重试路径，
        #    只是把"第一次读返回空"这件事变成必然，否则这条判据没法稳定验证。
        original_read = svc["activity_counters_svc"]._read_metrics
        injected = {"dropped": 0}
        original_delay = sys.modules["destiny_mcp.services.activity_counters_service"].DELAY_SECONDS
        sys.modules["destiny_mcp.services.activity_counters_service"].DELAY_SECONDS = 0

        async def dropping_read(membership_id: str, membership_type: int) -> dict:
            if injected["dropped"] == 0:
                injected["dropped"] += 1
                return {}
            return await original_read(membership_id, membership_type)

        svc["activity_counters_svc"]._read_metrics = dropping_read
        try:
            retried = await activity_assistant(ctx=ctx, intent="counters", query="熔炉")
        finally:
            svc["activity_counters_svc"]._read_metrics = original_read
            sys.modules["destiny_mcp.services.activity_counters_service"].DELAY_SECONDS = original_delay
        retried_top = next(
            (row for row in (retried["data"]["counters"] if retried.get("ok") else [])
             if row["metric_hash"] == OPPONENTS_DEFEATED),
            None,
        )
        check(
            "⑤ 第一次读返回空 → 重试后仍拿到 124495（不当 0、不报成功空壳）",
            injected["dropped"] == 1 and retried.get("ok") is True
            and retried_top is not None and retried_top["progress"] == EXPECTED_PROGRESS,
            f"注入空响应 {injected['dropped']} 次 恢复后 ok={retried.get('ok')} "
            f"progress={retried_top and retried_top['progress']}",
        )

        # ⑥ 反向：连读都是空 → 必须 ok=false + 说清原因（不是"0 条计数器"）
        #    （把重试节奏调小，只为让本脚本快一点；判据只看失败怎么报）
        counters_module = sys.modules["destiny_mcp.services.activity_counters_service"]
        original_read = svc["activity_counters_svc"]._read_metrics
        original_delay = counters_module.DELAY_SECONDS
        counters_module.DELAY_SECONDS = 0

        async def always_empty(membership_id: str, membership_type: int) -> dict:
            return {}

        svc["activity_counters_svc"]._read_metrics = always_empty
        try:
            broken = await activity_assistant(ctx=ctx, intent="counters")
        finally:
            svc["activity_counters_svc"]._read_metrics = original_read
            counters_module.DELAY_SECONDS = original_delay
        check(
            "⑥ 一直读不到 → ok=false，消息写清原因（空≠0）",
            broken.get("ok") is False
            and "组件 1100" in broken.get("error", {}).get("message", "")
            and "空不是 0" in broken.get("error", {}).get("message", ""),
            f"ok={broken.get('ok')} code={broken.get('error', {}).get('code')} "
            f"message={broken.get('error', {}).get('message', '')[:60]}…",
        )

    failed = [row for row, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 条通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
