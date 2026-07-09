"""Set bonus MCP tools — lookup_armor_set.

Rule 1: Thin wrapper. Business logic in SetBonusService.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error


@mcp.tool()
@handle_tool_error
async def lookup_armor_set(
    query: str = "",
    ctx: Context = None,
) -> dict:
    """查询护甲套装的组成和效果。

    支持两种查询方式：
    1. 输入套装名称 → 返回套装包含的所有护甲和效果
    2. 输入护甲名称 → 返回该护甲所属的套装和效果

    何时使用：用户问"兴旺幸存者套装有哪些部件"、"这件护甲属于哪个套装"时。

    Args:
        query: 套装名称或护甲名称（中英文均可）。

    Examples:
        lookup_armor_set(query="兴旺幸存者")
        lookup_armor_set(query="兴旺幸存者风帽")
    """
    if not query:
        return {"error": "请提供套装名称或护甲名称"}

    svc = get_ctx(ctx)
    return svc['set_bonus_svc'].lookup_armor_set(query)
