"""Player MCP tools — search_player, find_players, get_profile."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def search_player(
    player_name: str | None = None,
    ctx: Context = None,
) -> list:
    """将 Bungie 名称（如 'husky#1234'）解析为会员 ID。

    何时使用：用户第一次提到玩家名称，且本会话尚未解析过该玩家时调用。
    何时跳过：如果本会话已经解析过该玩家（有 membership_id），不要重复调用。
    何时跳过：用户只问游戏机制问题，不需要玩家数据时。

    Args:
        player_name: Bungie 名称，如 'husky#1234'。不填则使用默认玩家。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['player_svc'].search_player(player_name)


@mcp.tool()
@handle_tool_error
async def find_players(
    name: str,
    ctx: Context = None,
) -> str:
    """模糊搜索玩家，输入不完整的名字也能找到。

    通过 Bungie 用户搜索查找候选玩家，并按置信度评分排序。
    评分依据：近期活跃度、游戏时长、成就分数。

    何时使用:
    - 用户只说了玩家名但没带 #数字ID（如 "husky" 而不是 "husky#1234"）
    - 用户说"找一下这个人"、"搜索玩家 XXX"
    - 不确定玩家全名时

    何时跳过:
    - 用户已经提供了完整的 Bungie 名称（用 search_player）
    - 用户想查自己的信息（用 get_profile）

    Args:
        name: 玩家名称或名称前缀（不需要带 #数字ID）。

    Examples:
        find_players(name="husky")
        find_players(name="OneTop")
    """
    svc = get_ctx(ctx)
    results = await svc['player_svc'].find_players(name)

    if not results:
        return f"未找到名称包含 '{name}' 的玩家。"

    lines = [f"=== 搜索 '{name}' 的结果 ({len(results)} 个) ===\n"]
    lines.append(f"{'玩家':<25} {'置信度':<8} {'最后登录':<12} {'游戏时长':<10} {'成就分'}")
    lines.append("─" * 75)

    for r in results:
        hours = f"{r['playtime_hours']}h" if r['playtime_hours'] else "-"
        last = r['last_played'] or "-"
        triumph = str(r['triumph_score']) if r['triumph_score'] else "-"
        lines.append(
            f"{r['display_name']:<25} {r['confidence']:<8} {last:<12} {hours:<10} {triumph}"
        )

    lines.append(f"\n使用完整名称 (如 '{results[0]['display_name']}#xxxx') 调用 search_player 获取更多信息。")
    return "\n".join(lines)


@mcp.tool()
@handle_tool_error
async def get_profile(
    player_name: str | None = None,
    ctx: Context = None,
) -> dict:
    """获取玩家的角色概览（猎人/术士/泰坦的等级和信息）。

    何时使用：用户问"我的角色"、"光等多少"、"有哪些角色"时调用。
    何时跳过：用户只想看背包物品，不需要角色详情时（用 get_inventory）。
    何时跳过：用户要移动/装备物品时（用 move_item，内部会自动解析角色）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['player_svc'].get_profile(player_name)
