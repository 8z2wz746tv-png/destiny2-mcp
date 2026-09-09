"""Inventory MCP tools — get_inventory, search_items, search_items_by_type.

Rule 1: Tools are thin wrappers. Business logic is in services.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..exceptions import DestinyMCPError
from ._registry import mcp
from ._formatters import format_armor_mods, format_inventory, format_search_items
from ._helpers import get_ctx, handle_tool_error, resolve_player_name
from ._responses import error_response, ok_response


@mcp.tool()
@handle_tool_error
async def summarize_inventory(
    player_name: str | None = None,
    location: str = "",
    item_type: str = "",
    limit: int = 10,
    ctx: Context = None,
) -> dict:
    """汇总账号背包概况，返回数量分布、已装备物品、最高光物品和少量示例。

    何时使用：用户说"看看我的背包"、"我的仓库大概有什么"、"我有哪些武器/护甲"
    且不需要完整逐件列表时，优先使用这个工具。
    何时跳过：用户明确要某个角色/仓库的完整列表时，使用 get_inventory。
    何时跳过：用户要找某个具体物品位置时，使用 search_items。

    Args:
        player_name: Bungie 名称；多用户登录路径不填时使用当前登录用户。
        location: hunter/warlock/titan/vault/all，或中文：猎人/术士/泰坦/仓库/全部。
        item_type: weapon/armor/all，或中文：武器/护甲/全部。
        limit: 示例物品数量上限，范围 1-25。
    """
    svc = get_ctx(ctx)
    resolved_player_name = resolve_player_name(svc, player_name)
    if not resolved_player_name:
        return error_response(
            "auth_required",
            "请先登录 Bungie，或显式提供 player_name。",
            next_actions=[
                {
                    "label": "登录 Bungie 后重试",
                    "tool": "summarize_inventory",
                }
            ],
        )

    try:
        result = await svc['inventory_analysis_svc'].summarize_inventory(
            resolved_player_name,
            location=location,
            item_type=item_type,
            limit=limit,
        )
    except DestinyMCPError as exc:
        return error_response(
            "inventory_summary_failed",
            str(exc),
            next_actions=[
                {
                    "label": "缩小范围或确认 Bungie 登录状态后重试",
                    "tool": "summarize_inventory",
                }
            ],
        )

    return ok_response(
        result["summary"],
        {"inventory": result["inventory"]},
        next_actions=result["next_actions"],
        warnings=result["warnings"],
    )


@mcp.tool()
@handle_tool_error
async def get_inventory(
    player_name: str | None = None,
    location: str = "",
    item_type: str = "",
    armor_slot: str = "",
    rarity: str = "",
    ctx: Context = None,
) -> str:
    """获取某个角色背包或仓库的全部物品列表，支持按类型/部位/稀有度过滤。

    何时使用：用户说"看看我的猎人背包"、"仓库里有什么"时调用。
    何时使用：用户说"我有哪些异域腿甲"时（item_type="armor", armor_slot="legs", rarity="exotic"）。
    何时跳过：用户要找某个特定物品时（用 search_items 更精确）。
    何时跳过：用户只想看角色等级时（用 get_profile）。

    返回格式：按部位分组的物品列表，每行显示编号、名称、光等、是否已装备。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        location: 'hunter'/'warlock'/'titan'/'vault'/'all'
                  （或中文：猎人/术士/泰坦/仓库/全部）。
        item_type: 按类型过滤 — 'weapon'/'armor'，不填=全部。
        armor_slot: 按护甲部位过滤 — 'helmet'/'gauntlets'/'chest'/'legs'/'class_item'，不填=全部。
        rarity: 按稀有度过滤 — 'exotic'/'legendary'/'rare'，不填=全部。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return error_response(
            "auth_required",
            "请先登录 Bungie，或显式提供 player_name。",
            next_actions=[{"label": "登录 Bungie 后重试", "tool": "get_inventory"}],
        )
    response = await svc['inventory_svc'].get_inventory(
        player_name, location,
        item_type=item_type or None,
        armor_slot=armor_slot or None,
        rarity=rarity or None,
    )
    return format_inventory(response)


@mcp.tool()
@handle_tool_error
async def search_items(
    player_name: str | None = None,
    item_name: str = "",
    ctx: Context = None,
) -> str:
    """按名称搜索物品在所有角色和仓库中的位置。

    何时使用：用户要找某个特定物品（如"千语在哪"）、或要移动/装备物品前需要 itemInstanceId 时。
    何时使用：用户说"我有没有XXX"时。
    何时跳过：用户只想浏览某个角色的全部背包内容（用 get_inventory）。

    支持中英文部分名称匹配（如 '千语'、'Thousand'、'伊邪那岐'）。

    重要：如果搜索结果为空，直接告知用户未找到，不要猜测或编造物品名。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_name: 物品名称（中英文均可，支持模糊匹配）。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return error_response(
            "auth_required",
            "请先登录 Bungie，或显式提供 player_name。",
            next_actions=[{"label": "登录 Bungie 后重试", "tool": "search_items"}],
        )
    response = await svc['inventory_svc'].search_items(player_name, item_name)
    return format_search_items(response)


@mcp.tool()
@handle_tool_error
async def search_items_by_type(
    player_name: str | None = None,
    type_name: str = "",
    ctx: Context = None,
) -> str:
    """按武器/护甲类型搜索物品（如列出所有冲锋枪、所有手炮等）。

    何时使用：用户说"列出我所有的冲锋枪"、"我有哪些手炮"时调用。
    何时跳过：用户要找某个特定名称的物品（用 search_items）。
    何时跳过：用户想看某个角色的全部背包（用 get_inventory）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        type_name: 武器/护甲类型的显示名称（如 '微型冲锋枪'、'手炮'、'自动步枪'）。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return error_response(
            "auth_required",
            "请先登录 Bungie，或显式提供 player_name。",
            next_actions=[
                {"label": "登录 Bungie 后重试", "tool": "search_items_by_type"}
            ],
        )
    response = await svc['inventory_svc'].search_items_by_type(player_name, type_name)
    return format_search_items(response)


@mcp.tool()
@handle_tool_error
async def get_armor_mods(
    slot: str = "",
    category: str = "all",
    stat: str = "",
    ctx: Context = None,
) -> str:
    """查询护甲模组的真实名称和效果（从游戏 manifest 查询，非玩家背包）。

    何时使用：需要知道某个部位有哪些可用模组时。
    何时使用：推荐模组搭配前，先查真实模组列表避免编造名称。
    何时使用：用户问"头盔有什么模组"、"有哪些手雷属性模组"时。
    何时跳过：用户只想看自己背包里有什么模组（用 get_inventory）。

    重要：如果返回为空或报错，不要猜测模组名称，直接告知用户模组数据不可用。

    Args:
        slot: 按部位过滤 — 'helmet'/'gauntlets'/'chest'/'legs'/'class_item'，不填=全部
        category: 按类别过滤 — 'general'(通用属性模组) / 'slot_specific'(部位专属模组) / 'artifice'(精工模组) / 'all'(全部)
        stat: 按效果关键词过滤 — 如 '恢复'/'手雷'/'超能'/'近战'/'生命'/'武器'，匹配模组的属性加成或描述。不填=不过滤。
    """
    svc = get_ctx(ctx)
    manifest = svc['manifest']
    mods = manifest.get_armor_mods(slot=slot, category=category, stat=stat)
    return format_armor_mods(mods, slot, category)


@mcp.tool()
@handle_tool_error
async def list_items(
    item_type: str = "",
    tier: str = "",
    class_name: str = "",
    limit: int = 100,
    ctx: Context = None,
) -> dict:
    """从 manifest 查询物品列表，支持按类型、稀有度、职业过滤。

    何时使用：用户问"猎人有哪些金装"、"列出所有传说武器"、"有哪些模组"时。
    何时使用：知识库收集时，需要批量获取某个类型的物品。

    Args:
        item_type: 物品类型（weapon/armor/mod 等，或中文：武器/护甲/模组）。
        tier: 稀有度（exotic/legendary 等，或中文：异域/传说）。
        class_name: 职业（仅护甲有效，hunter/warlock/titan）。
        limit: 最大返回数量，默认 100。
    """
    svc = get_ctx(ctx)
    manifest = svc['manifest']

    items = manifest.list_items(
        item_type=item_type, tier=tier, class_name=class_name, limit=limit,
    )

    if not items:
        return {"error": "找不到符合条件的物品", "count": 0}

    return {
        "count": len(items),
        "items": [
            {"name": i.get('name', ''), "itemHash": i.get('itemHash', 0),
             "itemType": i.get('itemTypeName', ''), "tier": i.get('tierName', '')}
            for i in items
        ],
    }
