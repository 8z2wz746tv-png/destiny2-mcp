"""Loadout MCP tools — get_loadouts, save_loadout, delete_loadout, equip_loadout (配装管理)."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def get_loadouts(
    player_name: str | None = None,
    character: str | None = None,
    ctx: Context = None,
) -> dict:
    """列出角色的所有配装（包括游戏内官方配装和自建配装）。

    何时使用：用户问"我有哪些配装"、"看看我的装备方案"时。
    何时使用：用户要选择一个配装来装备时，先列出可用配装。
    何时跳过：用户只想看当前装备（用 get_inventory）。

    返回两类配装：
    - 官方配装（source=bungie）：游戏内保存的，每个角色最多 10 个
    - 自建配装（source=local）：通过本服务保存的，数量无限制

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 可选，按角色筛选（hunter/warlock/titan，或中文）。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].get_loadouts(player_name, character)
    return result.model_dump()


@mcp.tool()
@handle_tool_error
async def save_loadout(
    player_name: str | None = None,
    name: str = "",
    character: str = "",
    notes: str = "",
    ctx: Context = None,
) -> dict:
    """把角色当前装备保存为一个配装。

    何时使用：用户说"把现在的装备存成配装"、"保存当前方案"时。
    何时跳过：用户想修改已有的配装（先删除再重新保存）。

    配装会保存到本地存储（不受游戏内 10 个槽位限制）。
    保存的内容包括：当前已装备的护甲（头/手/胸/腿/职业）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        name: 配装名称（如 'GM 配装'、'PvP 配装'）。
        character: 角色名（hunter/warlock/titan，或中文）。
        notes: 可选备注（如 '高韧性高纪律'）。
    """
    if not name:
        return {"success": False, "message": "必须指定配装名称（name 参数）。"}
    if not character:
        return {"success": False, "message": "必须指定角色（character 参数）。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].save_loadout(player_name, name, character, notes)
    return result.model_dump()


@mcp.tool()
@handle_tool_error
async def delete_loadout(
    loadout_id: str = "",
    ctx: Context = None,
) -> dict:
    """删除一个自建配装。

    何时使用：用户说"删掉某个配装"、"不需要这个方案了"时。
    注意：只能删除自建配装（source=local），不能删除游戏内官方配装。

    Args:
        loadout_id: 配装 ID（来自 get_loadouts 返回的 id 字段）。
    """
    if not loadout_id:
        return {"success": False, "message": "必须指定 loadout_id。"}

    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].delete_loadout(loadout_id)
    return result.model_dump()


@mcp.tool()
@handle_tool_error
async def equip_loadout(
    player_name: str | None = None,
    loadout_id: str = "",
    ctx: Context = None,
) -> dict:
    """穿上选中的配装方案。

    何时使用：用户说"穿上 GM 配装"、"切换到 PvP 方案"时。
    何时跳过：用户只想看配装列表（用 get_loadouts）。

    支持两种配装来源：
    - 官方配装（source=bungie）：通过 Bungie API 一键装备
    - 自建配装（source=local）：逐件转移+装备

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        loadout_id: 配装 ID（来自 get_loadouts 返回的 id 字段）。
    """
    if not loadout_id:
        return {"success": False, "message": "必须指定 loadout_id。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].equip_loadout(player_name, loadout_id)
    return result.model_dump()
