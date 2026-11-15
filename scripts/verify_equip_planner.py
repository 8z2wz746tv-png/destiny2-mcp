"""真机只读验证：异域互斥判据与装备槽（ADR-011）。

**不写账号**：只调 `TransferService.plan_equip_item`（服务端的只读预检），把计划打出来。
存在的理由：MCP 服务进程是宿主拉起的常驻子进程，改了代码要重启宿主才生效；这个脚本直接
用仓库里的新代码打真账号，改完就能验，不必等重启。

用法：

    .venv/bin/python scripts/verify_equip_planner.py <角色> <instance_id> [<instance_id> ...]

读的是 `.env` 里的默认玩家（和 MCP 工具一致）。
"""

from __future__ import annotations

import asyncio
import json
import sys

from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.player_resolver import PlayerResolver
from destiny_mcp.services.transfer_service import TransferService
from destiny_mcp.tools._helpers import resolve_player_name


async def _plan(service: TransferService, player: str, character: str, instance_id: str) -> dict:
    plan = await service.plan_equip_item(player, instance_id, character)
    payload = plan.model_dump()
    return {
        "item": payload["target_item"],
        "instance_id": instance_id,
        "status": payload["status"],
        "target_slot": payload["target_slot"],
        "steps": [
            {k: step[k] for k in ("action", "item", "slot", "replaces", "why")}
            for step in payload["steps"]
        ],
        "blockers": [
            {"reason": block["reason"], "slot": block["slot"], "detail": block["detail"]}
            for block in payload["blockers"]
        ],
        "message": payload["message"],
    }


async def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    character, instance_ids = sys.argv[1], sys.argv[2:]
    # 默认玩家与 MCP 工具同一处口径（`.env` 的 DESTINY_DEFAULT_PLAYER，否则当前 OAuth 玩家）。
    player = resolve_player_name("")

    manifest = ManifestManager()
    bungie = BungieClient()
    await bungie.start()
    try:
        await manifest.ensure_loaded(bungie)
        service = TransferService(bungie, manifest, PlayerResolver(bungie, manifest))
        for instance_id in instance_ids:
            print(json.dumps(await _plan(service, player, character, instance_id),
                             ensure_ascii=False, indent=1))
    finally:
        await bungie.close()
        manifest.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
