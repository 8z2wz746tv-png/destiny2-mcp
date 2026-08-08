"""Weapon MCP tools — analyze_weapon, perk pools, owned rolls, weapon search."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..exceptions import DestinyMCPError
from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name
from ._responses import error_response, ok_response


def _resolve_analysis_player_name(
    svc: dict,
    player_name: str | None,
    include_inventory: bool,
) -> str | None:
    """Resolve which player inventory analyze_weapon should inspect."""
    if not include_inventory:
        return None
    return resolve_player_name(svc, player_name)


@mcp.tool()
@handle_tool_error
async def analyze_weapon(
    weapon_name: str = "",
    player_name: str | None = None,
    include_inventory: bool = True,
    ctx: Context = None,
) -> dict:
    """聚合分析一把武器：静态信息、perk 池、god roll、账号内副本对比。

    何时使用：用户问"这把枪怎么样"、"我的命运悲剧怎么留"、"千语有哪些
    perk，我仓库里有没有好 roll"时。
    何时使用：不确定该调用 get_weapon_info、get_weapon_perks、get_god_roll
    还是 compare_weapon_instances 时，优先使用这个聚合工具。
    何时跳过：用户明确只要某个低级结果时，使用对应专用工具。

    Args:
        weapon_name: 武器名（中英文均可，支持模糊匹配）。
        player_name: Bungie 名称；多用户登录路径不填时使用当前登录用户。
        include_inventory: 是否尝试对比账号内副本。false 时只返回 manifest/god roll 信息。
    """
    svc = get_ctx(ctx)
    analysis_svc = svc['weapon_analysis_svc']
    resolved_player_name = _resolve_analysis_player_name(
        svc, player_name, include_inventory
    )

    try:
        result = await analysis_svc.analyze_weapon(
            weapon_name,
            player_name=resolved_player_name,
            include_inventory=include_inventory,
        )
    except DestinyMCPError as exc:
        return error_response(
            "weapon_analysis_failed",
            str(exc),
            next_actions=[
                {
                    "label": "缩短武器名或确认 Bungie 登录状态后重试",
                    "tool": "analyze_weapon",
                }
            ],
        )

    return ok_response(
        result["summary"],
        {
            "weapon": result["weapon"],
            "perk_pool": result["perk_pool"],
            "god_roll": result["god_roll"],
            "inventory": result["inventory"],
        },
        next_actions=result["next_actions"],
        warnings=result["warnings"],
    )


@mcp.tool()
@handle_tool_error
async def get_weapon_perks(
    weapon_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询武器的 perk 池（所有可能 roll 到的 perk 列表）。

    何时使用：用户问"这把枪能 roll 什么 perk"、"千语的 perk 池是什么"时。
    何时使用：对比两把枪的 perk 差异前，先查 perk 池了解可能性。
    何时跳过：用户想看自己背包里某把枪的当前 roll（用 compare_weapon_instances）。

    返回武器的完整 perk 池，按栏位分组（枪管、弹匣、特性等），包含 perk 效果描述。

    Args:
        weapon_name: 武器名（中英文均可，模糊匹配），如 '千语'、'Fatebringer'、'命运悲剧'。
    """
    svc = get_ctx(ctx)
    return await svc['perk_svc'].get_weapon_perks(weapon_name)


@mcp.tool()
@handle_tool_error
async def compare_weapon_instances(
    player_name: str | None = None,
    weapon_name: str = "",
    item_instance_id: str = "",
    ctx: Context = None,
) -> dict:
    """对比同一把武器在账号中的所有副本，列出各自的 perk 和差异。

    何时使用：用户说"我有两把千语，哪个好"、"对比一下我的命运悲剧"时。
    何时使用：用户想看某把武器当前装备了什么 perk 时。
    何时跳过：用户只想看 perk 池（所有可能的 perk），不想看自己的副本（用 get_weapon_perks）。

    自动搜索账号中所有该武器的副本（仓库+角色），读取每个副本当前的 perk，
    并高亮显示差异。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        weapon_name: 武器名（中英文均可），如 '千语'、'Fatebringer'。
        item_instance_id: 可选。只查看这个物品实例；不填则对比全部同名副本。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['weapon_compare_svc'].compare_weapon_instances(
        player_name, weapon_name, item_instance_id or None
    )


@mcp.tool()
@handle_tool_error
async def search_weapons_by_type(
    player_name: str | None = None,
    type_name: str = "",
    ctx: Context = None,
) -> dict:
    """按武器类型查询账号中所有该类型武器的完整详情。

    返回每把武器的：框架、枪管、弹匣、特性1、特性2、原始特性、
    追踪器、武器模组、纪念物、着色器，以及武器属性（伤害、射程、
    稳定性、操控性、填装速度、辅助瞄准、变焦、空中效率、后坐方向、
    射速、弹夹容量）。

    何时使用：用户说"列出我所有的冲锋枪"、"我的手炮有哪些 perk"时。
    何时使用：用户想看某类型武器的完整属性和 perk 时。
    何时跳过：用户只想看某把特定武器（用 compare_weapon_instances）。
    何时跳过：用户只想看 perk 池（用 get_weapon_perks）。

    支持的武器类型（中文）：微型冲锋枪、手炮、自动步枪、脉冲步枪、
    侦察步枪、弓箭、融合步枪、霰弹枪、狙击步枪、榴弹发射器、
    线性融合步枪、火箭发射器、刀剑、机枪、追踪步枪、剑等。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        type_name: 武器类型的显示名称（如 '微型冲锋枪'、'手炮'、'自动步枪'）。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['weapon_detail_svc'].get_weapon_details_by_type(
        player_name, type_name
    )


@mcp.tool()
@handle_tool_error
async def get_god_roll(
    weapon_name: str = "",
    ctx: Context = None,
) -> str:
    """查询武器的社区推荐 god roll（来自 DIM wish list）。

    何时使用：用户问"千语的 god roll 是什么"、"这把枪怎么留"时。
    何时跳过：用户想看自己背包里某把枪的当前 roll（用 compare_weapon_instances）。
    何时跳过：用户想看 perk 池（用 get_weapon_perks）。

    返回：PvE 和 PvP 的推荐 perk 组合，标注来源。
    如果没有该武器的 god roll 数据，返回"暂无推荐"。
    """
    svc = get_ctx(ctx)
    return await svc['perk_svc'].get_god_roll(weapon_name)
