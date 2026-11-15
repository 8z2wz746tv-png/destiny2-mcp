#!/usr/bin/env python3
"""求解器基准：同一批用例、同一套指标，对**当前 checkout 或指定的另一个 checkout** 跑。

为什么要有它：P1–P4 要动目标函数、排序与搜索域，全是"更快还是更慢、候选有没有变"这类
问题。没有可复现的基准，"改进了"就只能靠聊天记录里的旧数字，那不是证据。

用法：

    # 当前 checkout，三次取中位，落 docs/benchmarks/build-solver-<label>.json
    .venv/bin/python scripts/benchmark_build_solver.py --label p0-baseline

    # 另一个 checkout（paired 对照用；sys.path 前置，实测能压过 editable 安装）
    git worktree add /tmp/d2base <ref>
    .venv/bin/python scripts/benchmark_build_solver.py --label before --source-root /tmp/d2base

    # 两份结果对起来看
    .venv/bin/python scripts/benchmark_build_solver.py --compare before.json after.json

口径（抄 d2-armor-solver 的 benchmark 纪律，见 docs/plans/SOLVER_OPTIMALITY_PLAN.md）：
- 每个用例跑 `--trials` 次，**报中位**，原始各次也留在 JSON 里；
- 没解时"首个解耗时"这类字段记 `null`，**不记 0**；
- 区分**端到端**（走 `BuildService.find_build`，含 worker 进程开销）与**内核**
  （直接 `solve()`，给出 `combos` 这个确定性工作量），两者分别报；
- 只写自己的输出文件，不动被指向的 checkout。

**这不是 timing 断言**：秒数受机器负载影响，跨机器比没有意义；有意义的是同一台机器上的
paired 对照与 `combos` 这类确定性计数。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "benchmarks"

#: 用例：名字 → `BuildRequest` 参数。选型原则是**快**（能反复跑）且**各自钉住一种行为**。
#:
#: 拆分依据是 P0 实测的成本剖面（`docs/benchmarks/build-solver-p0-baseline.json`）：
#: 「严格解为空 → 调谐补救」这条路是**枚举的 800 倍**（同一组约束：内核 0.27s / 75,712 组合，
#: `solve_with_tuning` 212.66s），所以带金装 + 高目标那类一律放 `--heavy`。
#: 默认集只留枚举主导的用例，2 分钟以内跑完。
CASES: list[tuple[str, str, dict[str, Any]]] = [
    # 只有优先级、没有硬目标：钉住"没要求就不优化"这条口径（枚举主导，~18s）
    ("hunter_priority_only", "hunter", {
        "priority_stats": ["grenade", "weapons"], "top_n": 5,
    }),
    # 目标"刚好达标"那一类：本会话那个现象的缩小版（~8s；术士版在 --heavy 里）
    ("titan_meets_minimum", "titan", {
        "weapons_target": 100, "grenade_target": 100,
        "class_target": 70, "melee_target": 70, "super_target": 80,
        "priority_stats": ["grenade", "weapons"], "top_n": 5,
    }),
]

HEAVY_CASES: list[tuple[str, str, dict[str, Any]]] = [
    # 严格解为空、靠调谐救回来：**最贵的一条**（实测 ~283s，其中补救 212s）
    ("hunter_priority_set", "hunter", {
        "exotic_name": "快速装弹松身裤",
        "weapons_target": 150, "class_target": 100, "super_target": 80,
        "melee_target": 70, "grenade_target": 70,
        "priority_stats": ["weapons", "class_stat", "super_stat", "melee", "grenade"],
        "top_n": 5,
    }),
    # 无解：钉住 `ladder` / `satisfiable=false` 那条路（猎人实测 ~75s）
    ("hunter_infeasible", "hunter", {
        "weapons_target": 150, "class_target": 100, "super_target": 80,
        "melee_target": 70, "health_target": 200,
        "priority_stats": ["weapons", "class_stat", "super_stat", "melee"],
    }),
    # 本会话那套：星火协议 + 埃希恩记忆 4 件 + 六项目标 + 手雷优先（术士 ~190s）
    ("warlock_khvostov", "warlock", {
        "exotic_name": "星火协议", "set_bonus_name": "埃希恩记忆", "set_bonus_count": 4,
        "weapons_target": 100, "grenade_target": 100,
        "class_target": 70, "melee_target": 70, "super_target": 80,
        "priority_stats": ["grenade", "weapons"], "top_n": 5,
    }),
    # 同一个人，但把手雷目标抬到 130：用来量"顶到极限"要花多少代价（P4 的验收口径之一）
    ("warlock_grenade_130", "warlock", {
        "exotic_name": "星火协议", "set_bonus_name": "埃希恩记忆", "set_bonus_count": 4,
        "weapons_target": 100, "grenade_target": 130,
        "class_target": 70, "melee_target": 70, "super_target": 80,
        "priority_stats": ["grenade", "weapons"], "top_n": 5,
    }),
]

STAT_KEYS = ("weapons", "health", "class_stat", "grenade", "super_stat", "melee")


def _median(values: list[float | int | None]) -> float | None:
    """中位；全是 None 就给 None（**不记 0**）。"""
    present = [value for value in values if value is not None]
    return round(statistics.median(present), 1) if present else None


async def _run_case(
    service: Any,
    manifest: Any,
    case_name: str,
    character: str,
    kwargs: dict[str, Any],
    trials: int,
    player: str,
) -> dict[str, Any]:
    """跑一个用例：端到端 + 内核工作量分开量。

    `service`/`manifest` 由调用方共享 —— 每条用例各建一份会把 359MB 的 Manifest 反复加载，
    那不是求解成本（P0 第一版就栽在这上面：8 分钟还没走完第一条用例）。
    """
    from destiny_mcp.build.models import BuildRequest
    from destiny_mcp.services.build_service import _parse_constraints

    rows: list[dict[str, Any]] = []
    snapshot = None
    parsed = None
    for trial in range(trials + 1):
        request = BuildRequest(character_class=character, **kwargs)
        started = time.perf_counter()
        status = "ok"
        results: list[Any] = []
        try:
            results = await service.find_build(player, request)
        except Exception as exc:  # noqa: BLE001 - 基准要把失败形态也记下来
            status = f"{type(exc).__name__}"
        service_ms = round((time.perf_counter() - started) * 1000, 1)

        # 第 0 次是同形状的预热（模块导入、Manifest 索引、worker 首次拉起），不计入统计。
        if trial == 0:
            try:
                snapshot = await service._inventory.get_armor_snapshot(player, character)
                parsed = _parse_constraints(request, manifest)
            except Exception:  # noqa: BLE001 - 量不到就说量不到
                snapshot = parsed = None
            continue

        top = results[0] if results else None
        row: dict[str, Any] = {
            "trial": trial - 1,
            "status": status,
            "serviceMs": service_ms,
            "candidates": len(results),
            "topStats": (
                {key: top.build.stat(key) for key in STAT_KEYS} if top else None
            ),
            "topCompletionRate": getattr(top, "completion_rate", None) if top else None,
            "solveMs": None,
            "combos": None,
            "setsFromKernel": None,
        }
        rows.append(row)

        # 内核工作量：同一份快照与约束，直接调 solve()，读它自己的 combos。
        # **只算一次**：`solve()` 是确定性的（同快照同约束 → 同 combos），每次 trial 都重跑
        # 等于把基准成本翻倍 —— P0 第一版就这么多烧了一倍时间。
        if snapshot is not None and parsed is not None and row["combos"] is None:
            from destiny_mcp.build.solver import solve

            started = time.perf_counter()
            outcome = solve(snapshot, parsed)
            row["solveMs"] = round((time.perf_counter() - started) * 1000, 1)
            row["combos"] = outcome.combos
            row["setsFromKernel"] = len(outcome.sets)

    # 收尾（client / manifest 的关闭）由调用方负责：它们跨用例共享。
    return {
        "case": case_name,
        "character": character,
        "request": {k: v for k, v in kwargs.items()},
        "median": {
            "serviceMs": _median([r["serviceMs"] for r in rows]),
            "solveMs": _median([r["solveMs"] for r in rows]),
            "combos": _median([r["combos"] for r in rows]),
            "candidates": _median([r["candidates"] for r in rows]),
        },
        "trials": rows,
    }


async def main_async(args: argparse.Namespace) -> int:
    if args.source_root:
        # 必须早于 `import destiny_mcp`：实测 sys.path 前置能压过 editable 安装的 finder。
        sys.path.insert(0, str(Path(args.source_root).resolve()))

    import destiny_mcp

    from destiny_mcp.bungie_client import BungieClient
    from destiny_mcp.manifest import ManifestManager
    from destiny_mcp.player_resolver import PlayerResolver
    from destiny_mcp.services.build_service import BuildService
    from destiny_mcp.tools._helpers import resolve_player_name

    player = resolve_player_name("")
    cases = list(CASES) + (list(HEAVY_CASES) if args.heavy else [])
    if args.only:
        wanted = set(args.only)
        cases = [case for case in cases if case[0] in wanted]
        if not cases:
            print(f"没有匹配的用例：{sorted(wanted)}")
            return 2

    print(f"destiny_mcp = {destiny_mcp.__file__}")
    print(f"用例 {len(cases)} 个 × {args.trials} 次；这条不是 timing 断言，秒数只用于同机对照\n")

    manifest = ManifestManager()
    bungie = BungieClient()
    await bungie.start()
    rows = []
    try:
        await manifest.ensure_loaded(bungie)
        service = BuildService(bungie, manifest, PlayerResolver(bungie, manifest))
        for case_name, character, kwargs in cases:
            print(f"── {case_name}（{character}）…", flush=True)
            row = await _run_case(
                service, manifest, case_name, character, kwargs, args.trials, player
            )
            rows.append(row)
            median = row["median"]
            print(
                f"   端到端 {median['serviceMs']}ms 候选 {median['candidates']} "
                f"| 内核 {median['solveMs']}ms combos={median['combos']} "
                f"| {row['trials'][0]['status']} {row['trials'][0]['topStats']}"
            )
    finally:
        await bungie.close()
        manifest.close()

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = DEFAULT_OUT / f"build-solver-{args.label}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "label": args.label,
                "sourceRoot": str(Path(args.source_root).resolve()) if args.source_root else str(ROOT),
                "destinyMcpFile": destiny_mcp.__file__,
                "trials": args.trials,
                "heavy": args.heavy,
                "rows": rows,
                "notes": [
                    "秒数受机器负载影响，跨机器比较无意义；有意义的只有同机 paired 对照与 combos 这类计数。",
                    "没解时对应字段是 null，不是 0。",
                    "端到端走 BuildService.find_build（含 worker 进程开销），内核是直接 solve()。",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n落盘 {out_path}")
    return 0


def compare(left: Path, right: Path) -> int:
    a = json.loads(left.read_text(encoding="utf-8"))
    b = json.loads(right.read_text(encoding="utf-8"))
    by_case = {row["case"]: row for row in b["rows"]}
    print(f"左 {a['label']}（{a['destinyMcpFile']}）  →  右 {b['label']}（{b['destinyMcpFile']}）\n")
    print(f"{'用例':22s} {'端到端':>18s} {'内核':>18s} {'combos':>20s}")
    for row in a["rows"]:
        other = by_case.get(row["case"])
        if other is None:
            continue
        cells = []
        for key in ("serviceMs", "solveMs", "combos"):
            before, after = row["median"].get(key), other["median"].get(key)
            if before is None or after is None:
                cells.append("null → " + ("null" if after is None else str(after)))
                continue
            ratio = f"{after / before:.2f}×" if before else "—"
            cells.append(f"{before} → {after} ({ratio})")
        print(f"{row['case']:22s} {cells[0]:>18s} {cells[1]:>18s} {cells[2]:>20s}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="local", help="输出文件名里的标签")
    parser.add_argument("--source-root", default="", help="另一个 checkout 的路径（paired 对照用）")
    parser.add_argument("--out", default="", help="显式输出路径")
    parser.add_argument("--trials", type=int, default=3, help="每个用例跑几次，报中位")
    parser.add_argument("--only", action="append", default=[], help="只跑指定用例（可重复）")
    parser.add_argument("--heavy", action="store_true", help="把术士那两条重用例也跑上（~7 分钟）")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("LEFT", "RIGHT"))
    args = parser.parse_args()
    if args.compare:
        return compare(*args.compare)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
