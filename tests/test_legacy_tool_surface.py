"""历史工具面（expert / full profile）的冒烟契约。

这 16 个模块提供 74 个历史工具，默认工具面不暴露它们，也几乎没有行为测试。
这里不测行为，只守住最容易静静腐烂的部分：模块能导入、会被注册、
每个工具都有描述和可用的 schema、profile 清单里没有拼错的模块名。
"""

from __future__ import annotations

import pytest

from destiny_mcp import server
from destiny_mcp.tools._registry import mcp as registry

LEGACY_MODULES = (
    "api_tools",
    "activity_tools",
    "artifact_tools",
    "build_import_tools",
    "build_tools",
    "collection_tools",
    "fragment_tools",
    "inventory_tools",
    "item_tools",
    "loadout_tools",
    "player_tools",
    "set_tools",
    "subclass_tools",
    "transfer_tools",
    "vendor_tools",
    "weapon_tools",
)


def _registered_modules() -> dict[str, int]:
    """模块名 → 该模块注册的工具数。"""
    counts: dict[str, int] = {}
    for definition in registry._definitions:
        name = definition.function.__module__.rsplit(".", 1)[-1]
        counts[name] = counts.get(name, 0) + 1
    return counts


def test_profile_module_lists_contain_no_typos() -> None:
    """profile 元组里写了不存在的模块，只会在启动注册那一刻炸。"""
    for profile in ("normal", "expert", "full"):
        server.create_server(profile)

    modules = _registered_modules()
    for profile_modules in (
        server._NORMAL_TOOL_MODULES,
        server._EXPERT_TOOL_MODULES,
        server._FULL_TOOL_MODULES,
    ):
        for name in profile_modules:
            assert name in modules, f"{name} 被列进 profile 但没有注册任何工具"


def test_every_legacy_module_still_registers_tools() -> None:
    """整个模块的工具被删空时，这里会失败，而不是等到 full profile 的用户发现。"""
    server.create_server("full")
    modules = _registered_modules()

    missing = sorted(name for name in LEGACY_MODULES if not modules.get(name))
    assert not missing, f"这些历史工具模块没有注册任何工具：{missing}"


@pytest.mark.parametrize("profile", ["normal", "expert", "full"])
async def test_every_exposed_tool_has_a_description_and_object_schema(profile: str) -> None:
    instance = server.create_server(profile)
    tools = await instance.list_tools()

    assert tools, f"{profile} profile 没有暴露任何工具"
    for tool in tools:
        assert tool.description and tool.description.strip(), f"{tool.name} 缺少描述"
        assert tool.inputSchema.get("type") == "object", f"{tool.name} 的 schema 不是对象"


async def test_legacy_tools_are_additive_to_the_assistant_surface() -> None:
    """expert / full 只是叠加历史工具，不能顶掉 8 个 assistant。"""
    normal = {tool.name for tool in await server.create_server("normal").list_tools()}
    expert = {tool.name for tool in await server.create_server("expert").list_tools()}
    full = {tool.name for tool in await server.create_server("full").list_tools()}

    assert normal < expert < full
    assert expert - normal, "expert profile 没有追加任何工具"
    assert full - expert, "full profile 没有追加任何工具"
