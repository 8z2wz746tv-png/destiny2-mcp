#!/usr/bin/env python
"""装备链条的分段计时：求解 → 逐件搬运 → 模组写入 → 回读核对。

只读部分（find、预览）随时可跑；带 `--write` 时**真写账号**（装五件护甲 + 模组），
用于给性能改动留改前/改后数字。用法：

    .venv/bin/python scripts/benchmark_equip_chain.py            # 只读：求解 + 确认回显
    .venv/bin/python scripts/benchmark_equip_chain.py --write    # 真写一次并分段计时
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from destiny_mcp.server import app_lifespan, create_server
from destiny_mcp.tools.assistants import build_assistant

# 用户那套猎人金装（真机上稳定有解）
EXOTIC = "快速装弹松身裤"
TARGETS: dict[str, Any] = dict(
    character="hunter",
    exotic_name=EXOTIC,
    weapons_target=150,
    class_target=100,
    grenade_target=70,
    melee_target=70,
    super_target=80,
)

PHASES: list[tuple[str, float]] = []


def _wrap(owner: Any, name: str, label: str) -> None:
    original = getattr(owner, name)

    async def timed(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            PHASES.append((label, time.perf_counter() - started))

    setattr(owner, name, timed)


async def main() -> int:
    async with app_lifespan(create_server()) as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})()})()

        build_svc = svc["build_svc"]
        _wrap(build_svc._inventory, "get_armor_snapshot", "① 护甲快照")
        _wrap(build_svc._equipment, "equip_exact", "② equip_exact 总")
        _wrap(build_svc._equipment, "_capture_recovery_state", "   回滚快照")
        _wrap(build_svc._equipment, "_equip_local_unlocked", "   搬运+模组")
        _wrap(build_svc._equipment, "_verify_loadout", "   回读核对")
        # 细分：每一次网络往返都单独计时，才能说出 52.8 秒花在哪
        _wrap(build_svc._equipment, "_apply_subclass_config", "     └ 子职业")
        _wrap(build_svc._equipment, "_prepare_mod_operations", "     └ 模组预检(每件)")
        _wrap(build_svc._equipment, "_insert_armor_mod", "     └ 写一颗模组")
        _wrap(build_svc._equipment, "_restore_exact_state", "   回滚执行")
        # `_transfer` 挂在 equipment 服务上（不是 BuildService）
        _wrap(build_svc._equipment._transfer, "transfer_item", "     └ 搬一件")
        _wrap(build_svc._equipment._transfer, "equip_items", "     └ 批量装备")
        _wrap(build_svc._equipment._resolver, "get_profile", "  profile 抓取")

        started = time.perf_counter()
        found = await build_assistant(ctx=ctx, intent="find", **TARGETS)
        solve = time.perf_counter() - started
        builds = ((found.get("data") or {}).get("builds")) or []
        print(f"find: {solve:.1f}s builds={len(builds)} code={(found.get('error') or {}).get('code')}")
        if not builds:
            print("没有候选，无法继续")
            return 1
        canonical = builds[0].get("canonical_build") or {}
        execution_id = builds[0].get("execution_id") or canonical.get("execution_id")

        started = time.perf_counter()
        preview = await build_assistant(
            ctx=ctx, intent="equip_build", execution_id=execution_id, character="hunter", confirmed=False
        )
        echo = time.perf_counter() - started
        rows = ((preview.get("candidates") or [{}])[0].get("items_preview")) or []
        print(f"确认回显: {echo:.1f}s 预览={len(rows)} 件 code={(preview.get('error') or {}).get('code')}")

        if "--write" not in sys.argv:
            print("（没加 --write，不写账号）")
            return 0

        started = time.perf_counter()
        done = await build_assistant(
            ctx=ctx, intent="equip_build", execution_id=execution_id, character="hunter", confirmed=True
        )
        total = time.perf_counter() - started
        result = (done.get("data") or {}).get("result") or {}
        if not result:
            # 失败时 steps 在 candidates[0].result 里（错误信封把整包结果放在那儿）
            result = ((done.get("candidates") or [{}])[0] or {}).get("result") or {}
        print(f"\n真写：{total:.1f}s ok={done.get('ok')} code={(done.get('error') or {}).get('code')}")
        print(f"  消息：{str(done.get('error', {}).get('message') or result.get('message'))[:200]}")
        print(f"  摘要：{str(done.get('summary'))[:120]}")
        print("  分段：")
        for label, seconds in PHASES:
            print(f"    {label:16s} {seconds:6.1f}s")
        print(f"  （分段合计 {sum(s for _, s in PHASES):.1f}s）")
        for step in result.get("steps") or []:
            print(f"    step {step.get('action'):12s} ok={step.get('success')} {str(step.get('detail'))[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
