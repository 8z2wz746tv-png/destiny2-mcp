#!/usr/bin/env python
"""无解阶梯（`no_solution_ladder`）的分段计时：analyze / 逐档探测 / 调谐证据。

只读：整支脚本不写账号。用法：

    .venv/bin/python scripts/benchmark_ladder.py            # 默认那组真无解请求
    .venv/bin/python scripts/benchmark_ladder.py --probes 2 # 换探测档数

它沿用装备那支脚本的做法（`benchmark_equip_chain.py`）：把服务方法包一层计时，
拿真机数字说话，别凭感觉说"阶梯很慢"。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

from destiny_mcp.build.models import BuildRequest
from destiny_mcp.server import app_lifespan, create_server

# 真机语料 ⑪ 行那组：猎人 + 生命值顶到 200 → 真无解（今天实测整行 227 秒）
REQUEST = dict(
    character_class="hunter",
    weapons_target=150,
    health_target=200,
    class_target=100,
    melee_target=70,
    super_target=80,
)

PHASES: list[tuple[str, float, str]] = []


def _wrap(owner: Any, name: str, label: str) -> None:
    original = getattr(owner, name)

    async def timed(*args: Any, **kwargs: Any) -> Any:
        request = args[1] if len(args) > 1 else kwargs.get("request")
        detail = ""
        if request is not None:
            targets = getattr(request, "priority_stats", None) or []
            detail = f"targets={getattr(request, 'health_target', None)}/{getattr(request, 'melee_target', None)} prio={list(targets)[:2]}"
        started = time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            PHASES.append((label, time.perf_counter() - started, detail))

    setattr(owner, name, timed)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probes", type=int, default=4)
    # 真实调用点（find/recommend）刚跑过一次 0 候选的解，所以默认也带上这个提示
    parser.add_argument("--base-order-empty", action="store_true", default=True)
    args = parser.parse_args()

    async with app_lifespan(create_server()) as svc:
        build_svc = svc["build_svc"]
        _wrap(build_svc, "find_build", "② 试解一次")
        _wrap(build_svc, "analyze_build", "① analyze（单项上限）")
        # 阶梯入口
        from destiny_mcp.tools import _armor_ladder

        _wrap(_armor_ladder, "_tuning_evidence", "③ 调谐证据")

        request = BuildRequest(**REQUEST)
        started = time.perf_counter()
        table = await _armor_ladder.no_solution_ladder(
            svc, "__destiny_current_oauth_player__", request,
            max_probes=args.probes, base_order_empty=args.base_order_empty,
        )
        total = time.perf_counter() - started

    print(f"\n阶梯总计：{total:.1f}s（max_probes={args.probes}）")
    print("分段：")
    for label, seconds, detail in PHASES:
        print(f"  {label:16s} {seconds:7.1f}s  {detail}")
    calls = [p for p in PHASES if p[0].startswith("②")]
    print(f"  试解次数={len(calls)} 合计={sum(p[1] for p in calls):.1f}s")
    print(f"  ceiling={table.get('ceiling')} precision={table.get('precision')}")
    print(f"  trials={[(t.get('label'), t.get('ok')) for t in table.get('trials') or []]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
