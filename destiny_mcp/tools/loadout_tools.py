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


@mcp.tool()
@handle_tool_error
async def search_official_loadout_identifiers(
    kind: str = "all",
    query: str = "",
    limit: int = 30,
    ctx: Context = None,
) -> dict:
    """搜索官方配装槽可用的名称、图标、颜色 hash。

    何时使用：用户想设置官方配装槽图标/颜色/名称，但只说了自然语言描述。
    何时跳过：用户已经明确提供 name_hash/icon_hash/color_hash。

    Args:
        kind: all/name/icon/color，或中文 全部/名称/图标/颜色。
        query: 可选关键词。
        limit: 每类最多返回数量。
    """
    svc = get_ctx(ctx)
    return svc['loadout_svc'].search_official_loadout_identifiers(
        kind=kind,
        query=query,
        limit=limit,
    )


@mcp.tool()
@handle_tool_error
async def snapshot_official_loadout(
    player_name: str | None = None,
    character: str = "",
    slot_number: int = 1,
    name_hash: int | None = None,
    icon_hash: int | None = None,
    color_hash: int | None = None,
    ctx: Context = None,
) -> dict:
    """把角色当前装备写入游戏内官方配装槽。

    何时使用：用户明确说"把当前这套保存到游戏内猎人 3 号配装槽"。
    何时跳过：用户只想保存到本地无限配装，用 save_loadout。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（hunter/warlock/titan，或中文）。
        slot_number: 游戏内配装槽位，1 到 10。
        name_hash: 可选，官方配装名称 hash。
        icon_hash: 可选，官方配装图标 hash。
        color_hash: 可选，官方配装颜色 hash。
    """
    if not character:
        return {"success": False, "message": "必须指定角色（character 参数）。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].snapshot_official_loadout(
        player_name,
        character,
        slot_number,
        name_hash,
        icon_hash,
        color_hash,
    )
    return result.model_dump()


@mcp.tool()
@handle_tool_error
async def update_official_loadout_identifiers(
    player_name: str | None = None,
    character: str = "",
    slot_number: int = 1,
    name_hash: int | None = None,
    icon_hash: int | None = None,
    color_hash: int | None = None,
    ctx: Context = None,
) -> dict:
    """更新游戏内官方配装槽的名称、图标和颜色 hash。

    何时使用：用户明确要改某个官方配装槽的展示标识，并且你已获得 hash。
    何时跳过：用户只是随口描述颜色/图标但没有可用 hash 时，先说明需要官方 hash。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（hunter/warlock/titan，或中文）。
        slot_number: 游戏内配装槽位，1 到 10。
        name_hash: 可选，官方配装名称 hash。
        icon_hash: 可选，官方配装图标 hash。
        color_hash: 可选，官方配装颜色 hash。
    """
    if not character:
        return {"success": False, "message": "必须指定角色（character 参数）。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].update_official_loadout_identifiers(
        player_name,
        character,
        slot_number,
        name_hash,
        icon_hash,
        color_hash,
    )
    return result.model_dump()


@mcp.tool()
@handle_tool_error
async def clear_official_loadout(
    player_name: str | None = None,
    character: str = "",
    slot_number: int = 1,
    ctx: Context = None,
) -> dict:
    """清空游戏内官方配装槽。

    何时使用：用户明确说"清空猎人 3 号官方配装槽"。
    何时跳过：删除本地自建配装时，用 delete_loadout。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（hunter/warlock/titan，或中文）。
        slot_number: 游戏内配装槽位，1 到 10。
    """
    if not character:
        return {"success": False, "message": "必须指定角色（character 参数）。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    result = await svc['loadout_svc'].clear_official_loadout(
        player_name,
        character,
        slot_number,
    )
    return result.model_dump()
