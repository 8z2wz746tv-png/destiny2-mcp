from __future__ import annotations

from pathlib import Path
from typing import get_args, get_origin

import yaml

from destiny_mcp.tools import _requests


ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "skills" / "destiny2-mcp"


def _literal_values(annotation) -> set[str]:
    if get_origin(annotation) is not None:
        return {str(value) for value in get_args(annotation)}
    return set()


def test_platform_neutral_skill_has_required_entrypoints() -> None:
    entry = SKILL_ROOT / "SKILL.md"
    assert entry.is_file()
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("---\nname: destiny2-mcp")
    for reference in (
        "routing.md",
        "evidence-and-completeness.md",
        "community-builds.md",
        "account-loadouts.md",
        "execution-safety.md",
    ):
        assert (SKILL_ROOT / "references" / reference).is_file()
        assert f"references/{reference}" in text


def test_skill_routing_intents_exist_in_request_contracts() -> None:
    expected = {
        "PlayerIntent": {"profile"},
        "InventoryIntent": {"summary", "get", "search", "type"},
        "WeaponIntent": {"filter_rolls", "catalog", "perk_description", "community"},
        "BuildIntent": {"recommend", "farm_target", "equip_build", "community"},
        "LoadoutIntent": {"list", "get"},
        "WorldIntent": {"vendor", "weekly", "weekly_full", "community"},
    }
    for name, required in expected.items():
        actual = _literal_values(getattr(_requests, name))
        assert required <= actual, (name, required - actual)


def test_cross_agent_behavior_cases_are_machine_readable() -> None:
    cases = yaml.safe_load(
        (ROOT / "tests" / "agent_behavior_cases.yaml").read_text(encoding="utf-8")
    )
    assert len(cases) >= 8
    tools = {
        "player_assistant",
        "inventory_assistant",
        "weapon_assistant",
        "build_assistant",
        "loadout_assistant",
        "subclass_assistant",
        "activity_assistant",
        "world_assistant",
    }
    for case in cases:
        assert {"id", "prompt", "expected_tool", "expected_intent"} <= case.keys()
        assert case["expected_tool"] in tools
