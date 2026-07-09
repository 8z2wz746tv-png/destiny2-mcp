"""Activity MCP tools — activity history and PGCR queries.

Provides tools for querying player activity history and
Post-Game Carnage Reports (PGCR).
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def get_activity_history(
    player_name: str | None = None,
    character: str | None = None,
    mode: str | None = None,
    count: int = 50,
    ctx: Context = None,
) -> str:
    """查询玩家的活动历史记录（突袭、熔炉、日落、试炼等）。

    不指定角色时，查询所有角色的活动并按时间合并显示。

    何时使用:
    - 用户问"我最近打了什么"、"查一下我的突袭记录"
    - 用户想看 PvP 或 PvE 的近期表现
    - 用户想查某个角色的活动历史

    何时跳过:
    - 用户只想看角色信息（用 get_profile）
    - 用户想查生涯统计数据（用 get_historical_stats）

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名 (hunter/warlock/titan, 或中文)。不填则查询所有角色。
        mode: 活动模式。支持: 突袭/raid, 熔炉/crucible, 日落/nightfall, 试炼, 地牢/dungeon,
              智谋/gambit, 巡逻/patrol, 猛攻/onslaught。不填则返回所有模式。
        count: 返回数量，默认 50，最大 250。

    Examples:
        get_activity_history(player_name="husky#1234", mode="突袭", count=5)
        get_activity_history(player_name="husky#1234", mode="熔炉", count=20)
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    activities = await svc['activity_svc'].get_activity_history(
        player_name, character=character, mode=mode, count=count,
    )

    if not activities:
        return "未找到活动记录。"

    # Format as compact list
    lines = [f"=== 活动历史 ({len(activities)} 条) ===\n"]
    for i, act in enumerate(activities, 1):
        status = "✅" if act["is_completed"] else "❌"
        char_tag = f"[{act['character']}]" if act.get("character") else ""
        kills = int(act['kills'])
        deaths = int(act['deaths'])
        kd = f"K{kills}/D{deaths}"
        time_str = act['start_time'][5:16].replace("T", " ")  # MM-DD HH:MM
        # 显示活动类型中文名（突袭/熔炉/日落等）
        mode_name = act.get('mode_name', '')
        mode_tag = f" | {mode_name}" if mode_name else ""
        lines.append(
            f"{i:2d}. {time_str} {char_tag} {act['activity_name']}{mode_tag} | {kd} {status}"
        )
    return "\n".join(lines)


@mcp.tool()
@handle_tool_error
async def get_pgcr(
    activity_id: str,
    ctx: Context = None,
) -> str:
    """查询某场活动的详细战绩报告 (PGCR)。

    显示该场活动中所有玩家的击杀/死亡/助攻/得分等数据。
    activity_id 可以从 get_activity_history 的结果中获取。

    何时使用:
    - 用户想看某场具体比赛的详细数据
    - 用户问"那把突袭队友打了多少"
    - 想查看某个 activity_id 的详细信息

    何时跳过:
    - 用户只是想看近期活动列表（用 get_activity_history）

    Args:
        activity_id: 活动实例 ID（从 get_activity_history 获取的 instance_id）。

    Examples:
        get_pgcr(activity_id="12345678901")
    """
    svc = get_ctx(ctx)
    result = await svc['activity_svc'].get_pgcr(activity_id)

    # Format PGCR
    lines = [
        f"=== {result['activity_name']} ===",
        f"实例 ID: {result['instance_id']}",
        f"时间: {result['start_time'][:16]}",
        "",
        f"{'玩家':<20} {'职业':<8} {'光等':<6} {'击杀':<6} {'死亡':<6} {'助攻':<6} {'K/D':<8} {'状态'}",
        "─" * 90,
    ]

    for entry in result["entries"]:
        completed = "✅" if entry["completed"] else "❌"
        lines.append(
            f"{entry['player_name']:<20} {entry['class']:<8} {entry['light_level']:<6} "
            f"{entry['kills']:<6} {entry['deaths']:<6} {entry['assists']:<6} "
            f"{entry['kd_ratio']:<8} {completed}"
        )

    return "\n".join(lines)


@mcp.tool()
@handle_tool_error
async def get_historical_stats(
    player_name: str | None = None,
    character: str | None = None,
    ctx: Context = None,
) -> str:
    """查询玩家的生涯 PvE/PvP 统计数据。

    包含总击杀、总死亡、K/D 比、游戏时长、活动完成数等。

    何时使用:
    - 用户问"我的生涯数据"、"我打了多少小时"
    - 想看 PvE 或 PvP 的总体表现

    何时跳过:
    - 用户想看近期活动详情（用 get_activity_history）
    - 用户想看某场比赛数据（用 get_pgcr）

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名。不填则使用第一个角色。

    Examples:
        get_historical_stats(player_name="husky#1234")
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['activity_svc'].get_historical_stats(player_name, character)

    lines = ["=== 生涯统计 ===\n"]

    pve = result.get("pve", {})
    if pve:
        lines.append("── PvE ──")
        for stat_id, display_val in pve.items():
            lines.append(f"  {stat_id}: {display_val}")

    pvp = result.get("pvp", {})
    if pvp:
        lines.append("\n── PvP ──")
        for stat_id, display_val in pvp.items():
            lines.append(f"  {stat_id}: {display_val}")

    if not pve and not pvp:
        lines.append("未找到统计数据。")

    return "\n".join(lines)
