"""Transfer MCP tools — transfer_item, equip_item, move_item."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ._registry import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def transfer_item(
    player_name: str | None = None,
    item_instance_id: str = "",
    to_character: str = "",
    from_character: str | None = None,
    ctx: Context = None,
) -> dict:
    """按物品实例 ID 移动物品到指定角色或仓库。

    何时使用：你已经通过 search_items 拿到了 itemInstanceId，需要精确移动时。
    何时跳过：用户只说了物品名字没给 ID（用 move_item 更方便，自动搜索+移动）。

    注意：命运2不支持角色间直接转移。猎人→术士会自动走 猎人→仓库→术士（两步）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_instance_id: 物品实例 ID（来自 search_items 的结果）。
        to_character: 目标：'hunter'/'warlock'/'titan'/'vault'（或中文）。
        from_character: 可选，来源角色（用于校验）。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].transfer_item(
        player_name, item_instance_id, to_character, from_character
    )


@mcp.tool()
@handle_tool_error
async def equip_item(
    player_name: str | None = None,
    item_instance_id: str = "",
    character: str = "",
    ctx: Context = None,
) -> dict:
    """装备物品到指定角色。物品必须已经在该角色背包中（不能从仓库直接装备）。

    何时使用：物品已经在目标角色背包上，需要装备时。
    何时跳过：物品还在仓库或其他角色上（用 move_item 并设 equip=True，一步到位）。

    重要规则：
    - 命运2中，每个角色同时只能装备1件异域（金色）护甲。如果目标角色已有异域护甲，再装备异域护甲会失败。
    - 每个角色同时只能装备1把异域（金色）武器。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_instance_id: 物品实例 ID（来自 search_items 的结果）。
        character: 装备到哪个角色（'hunter'/'warlock'/'titan'，或中文）。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].equip_item(
        player_name, item_instance_id, character
    )


@mcp.tool()
@handle_tool_error
async def equip_items(
    player_name: str | None = None,
    item_instance_ids: list[str] | None = None,
    character: str = "",
    ctx: Context = None,
) -> dict:
    """批量装备多件物品到同一个角色，使用 Bungie 官方 EquipItems 原子接口。

    何时使用：用户已经明确要同时换多件装备，例如"同时装备这三把武器"。
    何时跳过：物品还在仓库或其他角色上，先用 move_item 转到目标角色。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家/当前 OAuth 玩家。
        item_instance_ids: 要装备的物品实例 ID 列表。
        character: 目标角色（hunter/warlock/titan，或中文）。
    """
    if not item_instance_ids:
        return {"success": False, "message": "必须提供 item_instance_ids。"}
    if not character:
        return {"success": False, "message": "必须提供 character。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].equip_items(
        player_name, item_instance_ids, character
    )


@mcp.tool()
@handle_tool_error
async def pull_from_postmaster(
    player_name: str | None = None,
    item_instance_id: str = "",
    character: str | None = None,
    ctx: Context = None,
) -> dict:
    """从邮政官取回指定物品。

    何时使用：用户说"把邮政官里的金球/这件装备拉出来"，并且已经有 itemInstanceId。
    何时跳过：普通仓库/角色背包物品移动，用 move_item 或 transfer_item。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家/当前 OAuth 玩家。
        item_instance_id: 邮政官物品实例 ID。
        character: 可选，指定取回到哪个角色；不填则使用物品所属角色。
    """
    if not item_instance_id:
        return {"success": False, "message": "必须提供 item_instance_id。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].pull_from_postmaster(
        player_name, item_instance_id, character
    )


@mcp.tool()
@handle_tool_error
async def set_item_lock_state(
    player_name: str | None = None,
    item_instance_id: str = "",
    locked: bool = True,
    character: str | None = None,
    ctx: Context = None,
) -> dict:
    """锁定或解锁一件装备。

    何时使用：用户明确要求"锁上这把好 roll"、"解锁这件装备"。
    何时跳过：只是分析装备好坏时，不要自动锁定，先给建议。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家/当前 OAuth 玩家。
        item_instance_id: 物品实例 ID。
        locked: true=锁定，false=解锁。
        character: 可选。仓库物品可不填，工具会用任一角色满足 Bungie 请求字段。
    """
    if not item_instance_id:
        return {"success": False, "message": "必须提供 item_instance_id。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].set_item_lock_state(
        player_name, item_instance_id, locked, character
    )


@mcp.tool()
@handle_tool_error
async def set_quest_tracked_state(
    player_name: str | None = None,
    item_instance_id: str = "",
    tracked: bool = True,
    character: str | None = None,
    ctx: Context = None,
) -> dict:
    """追踪或取消追踪一个任务/悬赏物品。

    何时使用：用户明确要求"追踪这个任务"、"取消追踪这个悬赏"。
    何时跳过：只想查看任务信息时不要自动修改追踪状态。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家/当前 OAuth 玩家。
        item_instance_id: 任务/悬赏实例 ID。
        tracked: true=追踪，false=取消追踪。
        character: 可选，任务所在角色；不填则根据物品位置推断。
    """
    if not item_instance_id:
        return {"success": False, "message": "必须提供 item_instance_id。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].set_quest_tracked_state(
        player_name, item_instance_id, tracked, character
    )


@mcp.tool()
@handle_tool_error
async def move_item(
    player_name: str | None = None,
    item_name: str = "",
    destination: str = "",
    equip: bool = False,
    source: str | None = None,
    item_instance_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """按物品名称移动物品到指定位置，可选自动装备。一站式完成搜索+转移+装备。

    何时使用：用户说"把千语移到猎人"、"把XXX装上"时。这是最常用的移动工具。
    何时使用：你不知道 itemInstanceId，只知道物品名字时。
    何时跳过：你已经有 itemInstanceId 且需要更精确控制时（用 transfer_item）。

    消歧义流程（同名多件装备）：
    当返回 needs_disambiguation=true 时，直接把 question 字段的内容原样展示给用户，然后结束回复。
    不要自己重新格式化，不要添加额外解释，不要尝试再次调用工具。
    等用户回复编号后，带 item_instance_id 再次调用本工具。

    重要规则：
    - 命运2中，护甲有5个部位（头盔/手甲/胸甲/腿甲/职业护甲），每个角色同时只能装备1件异域（金色）护甲。
    - 如果用户要求装备多件异域护甲，必须提醒这个限制。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_name: 物品名称（中英文均可，如 '千语'、'Thousand Words'）。
        destination: 目标位置：'hunter'/'warlock'/'titan'/'vault'（或中文）。
        equip: 移动后是否自动装备（目标必须是角色，不能是仓库）。
        source: 可选，限定从哪个位置搜索（如只从仓库找）。
        item_instance_id: 可选，指定转移哪一件（消歧义后使用）。

    Examples:
        move_item(player_name="husky#1234", item_name="千语", destination="hunter", equip=True)
        move_item(player_name="husky#1234", item_name="伊邪那岐", destination="warlock")
        move_item(player_name="husky#1234", item_name="星火协议", destination="warlock", item_instance_id="123456")
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].move_item(
        player_name, item_name, destination, equip, source, item_instance_id
    )


@mcp.tool()
@handle_tool_error
async def apply_mod(
    player_name: str | None = None,
    item_instance_id: str = "",
    mod_name: str = "",
    character: str = "",
    ctx: Context = None,
) -> dict:
    """给护甲装备模组。

    何时使用：用户说"给XXX装手雷模组"时。
    何时使用：配装引擎推荐模组后自动安装时。
    何时跳过：用户只想看有哪些模组可用（用 get_armor_mods）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        item_instance_id: 护甲实例 ID（从 search_items 获取）。
        mod_name: 模组名称（中英文均可，如 '手雷模组'、'Grenade Mod'）。
        character: 角色名（hunter/warlock/titan，或中文）。

    Examples:
        apply_mod(player_name="husky#1234", item_instance_id="123456", mod_name="手雷模组", character="warlock")
        apply_mod(item_instance_id="123456", mod_name="恢复模组", character="hunter")
    """
    if not item_instance_id:
        return {"success": False, "message": "必须提供 item_instance_id。"}
    if not mod_name:
        return {"success": False, "message": "必须提供 mod_name。"}
    if not character:
        return {"success": False, "message": "必须提供 character。"}

    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['transfer_svc'].apply_mod(
        player_name, item_instance_id, mod_name, character
    )
