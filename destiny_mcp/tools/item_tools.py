"""Item/weapon/armor MCP tools — definitions, stats, catalysts, perks.

Rule 1: Thin wrappers. Business logic is in ManifestQueryService.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..exceptions import DestinyMCPError
from ..server import mcp
from ._helpers import get_ctx, handle_tool_error


@mcp.tool()
@handle_tool_error
async def get_item_definition(
    item_name: str = "",
    item_hash: int = 0,
    ctx: Context = None,
) -> dict:
    """查询物品的完整 manifest 定义（中英文名、描述、perk、部位、稀有度等）。

    何时使用：需要获取物品的详细信息时。
    何时使用：用户问"天穹夜鹰的效果是什么"、"这把武器的 perk 是什么"时。
    何时使用：知识库收集时，需要获取物品的完整信息。
    何时跳过：用户只想看背包里有什么（用 get_inventory）。

    Args:
        item_name: 物品名称（中英文均可，支持模糊匹配）。
        item_hash: 物品哈希值（与 item_name 二选一）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_item_full_definition(item_name=item_name, item_hash=item_hash)


@mcp.tool()
@handle_tool_error
async def get_exotic_armor_details(
    armor_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询异域护甲的详细信息（perk、机制、适用场景）。

    何时使用：用户问"天穹夜鹰的效果是什么"、"星火协议的 perk 是什么"时。
    何时使用：知识库收集时，需要获取金装的核心机制。
    何时跳过：用户只想看背包里有什么（用 get_inventory）。

    Args:
        armor_name: 金装名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_exotic_armor_details(armor_name)


@mcp.tool()
@handle_tool_error
async def list_exotic_armor(
    class_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询某个职业的所有异域护甲列表。

    何时使用：用户问"猎人有哪些金装"、"列出所有术士异域护甲"时。
    何时使用：知识库收集时，需要批量获取某个职业的所有金装。

    Args:
        class_name: 职业名称（hunter/warlock/titan，或中文：猎人/术士/泰坦）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_exotic_armor_list(class_name)


@mcp.tool()
@handle_tool_error
async def get_weapon_stats(
    weapon_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询武器的详细数值（射程、稳定性、操控性、填装速度等）。

    何时使用：用户问"这把枪的属性是什么"、"遗言的射程多少"时。
    何时使用：对比两把枪的数值差异。

    Args:
        weapon_name: 武器名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_weapon_stats(weapon_name)


@mcp.tool()
@handle_tool_error
async def get_perk_description(
    perk_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询 perk 的效果描述。

    何时使用：用户问"杀戮弹匣是什么效果"、"萤火虫的 perk 描述"时。
    何时使用：知识库收集时，需要获取 perk 的详细描述。

    Args:
        perk_name: perk 名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_perk_description(perk_name)


@mcp.tool()
@handle_tool_error
async def get_catalyst_details(
    weapon_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询异域武器的催化剂效果。

    何时使用：用户问"遗言的催化剂是什么"、"这把金装的催化效果"时。
    何时使用：知识库收集时，需要获取催化剂的详细信息。

    Args:
        weapon_name: 武器名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_catalyst_details(weapon_name)


@mcp.tool()
@handle_tool_error
async def get_weapon_info(
    weapon_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询武器的完整信息（名称、类型、属性、固有 perk、催化剂等）。

    何时使用：用户问"千语是什么武器"、"遗言的属性"时。
    何时使用：需要一次性获取武器的所有信息时。

    Args:
        weapon_name: 武器名称（中英文均可，支持模糊匹配）。

    Examples:
        get_weapon_info(weapon_name="千语")
        get_weapon_info(weapon_name="Thunderlord")
    """
    svc = get_ctx(ctx)
    query_svc = svc['manifest_query_svc']
    return query_svc.get_weapon_full_info(weapon_name)
