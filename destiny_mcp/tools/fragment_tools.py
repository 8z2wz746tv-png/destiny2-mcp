"""Fragment MCP tools — list_fragments, get_fragment_details, list_subclass_options.

Rule 1: Thin wrappers. Business logic in FragmentService.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error


@mcp.tool()
@handle_tool_error
async def list_fragments(
    element: str = "",
    ctx: Context = None,
) -> dict:
    """列出某个元素的所有碎片（Fragment）。

    何时使用：用户问"虚空有哪些碎片"、"列出所有棱镜碎片"时。

    Args:
        element: 元素名称（void/solar/arc/stasis/strand/prism，或中文）。
    """
    svc = get_ctx(ctx)
    return svc['fragment_svc'].list_fragments(element)


@mcp.tool()
@handle_tool_error
async def get_fragment_details(
    fragment_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询碎片的详细信息（效果描述、属性加成）。

    何时使用：用户问"休止回声是什么效果"、"这个碎片加什么属性"时。

    Args:
        fragment_name: 碎片名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    return svc['fragment_svc'].get_fragment_details(fragment_name)


@mcp.tool()
@handle_tool_error
async def list_subclass_options(
    class_name: str = "",
    element: str = "",
    component: str = "",
    ctx: Context = None,
) -> dict:
    """列出某个子职业的所有可选项（超能、近战、手雷、星象、跳跃方式）。

    何时使用：用户问"猎人虚空有哪些手雷"、"术士烈日有哪些超能"时。

    Args:
        class_name: 职业（hunter/warlock/titan，或中文）。
        element: 元素（void/solar/arc/stasis/strand/prism，或中文）。
        component: 组件类型（super/melee/grenade/aspect/movement，或中文）。
    """
    svc = get_ctx(ctx)
    return svc['fragment_svc'].list_subclass_options(class_name, element, component)
