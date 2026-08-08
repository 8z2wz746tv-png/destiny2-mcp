from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSISTANT_TOOLS = {
    "player_assistant",
    "inventory_assistant",
    "weapon_assistant",
    "build_assistant",
    "loadout_assistant",
    "subclass_assistant",
    "activity_assistant",
    "world_assistant",
}


def _registered_tools(profile: str | None = None) -> set[str]:
    env = os.environ.copy()
    env.update({
        "BUNGIE_API_KEY": "dummy",
        "BUNGIE_CLIENT_ID": "1",
        "BUNGIE_CLIENT_SECRET": "dummy",
    })
    if profile is None:
        env.pop("DESTINY_MCP_TOOL_PROFILE", None)
    else:
        env["DESTINY_MCP_TOOL_PROFILE"] = profile

    script = textwrap.dedent("""
        import json
        from destiny_mcp import server

        print(json.dumps(sorted(server.mcp._tool_manager._tools.keys())))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_default_profile_exposes_assistant_tools_only() -> None:
    tools = _registered_tools()

    assert tools == ASSISTANT_TOOLS
    assert "raw_api_call" not in tools
    assert "transfer_item" not in tools


def test_full_profile_keeps_legacy_tools_for_compatibility() -> None:
    tools = _registered_tools("full")

    assert ASSISTANT_TOOLS.issubset(tools)
    assert "raw_api_call" in tools
    assert "transfer_item" in tools
    assert "compare_weapon_instances" in tools


def test_expert_profile_adds_common_read_tools_without_raw_api() -> None:
    tools = _registered_tools("expert")

    assert ASSISTANT_TOOLS.issubset(tools)
    assert "get_inventory" in tools
    assert "get_weapon_perks" in tools
    assert "raw_api_call" not in tools
    assert "transfer_item" not in tools


def test_weapon_roll_filter_matches_explicit_perk_terms() -> None:
    service = WeaponRollFilterService()

    result = service.filter_rolls(
        [
            {
                "name": "午夜政变",
                "instance_id": "1",
                "location": "vault",
                "power": 540,
                "sockets": [
                    {"plug_name": "快速命中"},
                    {"plug_name": "动能震颤"},
                ],
            },
            {
                "name": "午夜政变",
                "instance_id": "2",
                "location": "vault",
                "power": 540,
                "sockets": [
                    {"plug_name": "移动目标"},
                    {"plug_name": "强力首发"},
                ],
            },
        ],
        weapon_name="午夜",
        location="vault",
        required_perks=["快速"],
        any_perks="动能, 狂暴",
        excluded_perks=["强力首发"],
    )

    assert result["checked_count"] == 2
    assert result["matched_count"] == 1
    assert result["matched"][0]["instance_id"] == "1"
    assert result["filters"]["any_perks"] == ["动能", "狂暴"]
