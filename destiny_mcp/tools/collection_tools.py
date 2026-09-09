"""Collection MCP tools — collectible unlock status."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ._registry import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def search_collectible_nodes(
    query: str = "",
    limit: int = 20,
    ctx: Context = None,
) -> dict:
    """按名称搜索收藏品/展示节点，返回可用于 get_collectible_node_status 的 node_hash。

    何时使用：用户问"异域收藏还缺什么"但没有提供 collectible_node_hash。
    何时跳过：用户已经给了明确 node_hash，直接用 get_collectible_node_status。

    Args:
        query: 节点名称关键词，例如 "异域"、"徽章"、"武器"。
        limit: 返回候选数量。
    """
    svc = get_ctx(ctx)
    return svc['collection_svc'].search_collectible_nodes(query, limit=limit)


@mcp.tool()
@handle_tool_error
async def get_collectible_item_status(
    player_name: str | None = None,
    item_name: str = "",
    character: str | None = None,
    limit: int = 10,
    ctx: Context = None,
) -> dict:
    """按物品名查询收藏品解锁状态。

    何时使用：用户问"我收藏里有没有死亡使者/某个异域/某个红框"。
    何时跳过：用户要查当前背包里有没有物品，用 search_items。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_name: 物品名称关键词。
        character: 可选，指定角色；不填使用第一个角色。
        limit: 搜索候选数量。
    """
    if not item_name:
        return {"success": False, "message": "必须提供 item_name。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['collection_svc'].get_collectible_item_status(
        player_name,
        item_name,
        character=character,
        limit=limit,
    )


@mcp.tool()
@handle_tool_error
async def get_collectible_node_status(
    player_name: str | None = None,
    collectible_node_hash: int = 0,
    character: str | None = None,
    include_invisible: bool = False,
    limit: int = 200,
    ctx: Context = None,
) -> dict:
    """查询收藏品节点下的已获得/未获得状态。

    何时使用：用户提供收藏品 Presentation Node hash，想看这个节点下还缺什么。
    何时跳过：用户只是搜背包/仓库中已有物品，用 search_items 或 get_inventory。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        collectible_node_hash: 收藏品 Presentation Node hash。
        character: 可选，指定角色；不填使用第一个角色。
        include_invisible: 是否包含 Bungie 标记为不可见的收藏品。
        limit: 返回明细数量，默认 200，最大 500。
    """
    if not collectible_node_hash:
        return {"success": False, "message": "必须提供 collectible_node_hash。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['collection_svc'].get_collectible_node_status(
        player_name,
        collectible_node_hash,
        character=character,
        include_invisible=include_invisible,
        limit=limit,
    )
