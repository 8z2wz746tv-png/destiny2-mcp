"""Subclass MCP tools — get_subclass, modify_subclass."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def get_subclass(
    player_name: str | None = None,
    character: str = "",
    ctx: Context = None,
) -> dict:
    """读取角色当前的子职业配置（超能、近战、手雷、技能、移动、碎片等）。

    何时使用：用户问"我的猎人用的什么子职业"、"我的碎片配的什么"时。
    何时使用：修改子职业前，先读取当前配置确认现状。
    何时跳过：用户只想看角色等级或背包（用 get_profile 或 get_inventory）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（'hunter'/'warlock'/'titan'，或中文：猎人/术士/泰坦）。
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)
    return await svc['subclass_svc'].get_subclass(
        player_name, character
    )


@mcp.tool()
@handle_tool_error
async def modify_subclass(
    player_name: str | None = None,
    character: str = "",
    super: str | None = None,
    melee: str | None = None,
    grenade: str | None = None,
    class_ability: str | None = None,
    movement: str | None = None,
    aspect_1: str | None = None,
    aspect_2: str | None = None,
    fragment_1: str | None = None,
    fragment_2: str | None = None,
    fragment_3: str | None = None,
    fragment_4: str | None = None,
    fragment_5: str | None = None,
    fragment_6: str | None = None,
    ctx: Context = None,
) -> dict:
    """修改角色的子职业配置（超能、近战、手雷等）。

    只需指定要改的能力，未指定的保持不变。支持中英文名称模糊匹配。

    何时使用：用户说"把手雷换成燃烧弹"、"碎片换成XXX"时。
    何时跳过：用户只想看当前配置（用 get_subclass）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（'hunter'/'warlock'/'titan'，或中文）。
        super: 目标超能名称。
        melee: 目标近战名称。
        grenade: 目标手雷名称。
        class_ability: 目标职业技能名称。
        movement: 目标移动技能名称。
        aspect_1: 第一个碎片名称。
        aspect_2: 第二个碎片名称。
        fragment_1 到 fragment_6: 碎片名称（最多6个槽位）。

    Examples:
        modify_subclass(player_name="husky#1234", character="hunter", grenade="Incendiary Grenade")
        modify_subclass(player_name="husky#1234", character="猎人", super="金枪", aspect_1="燃爆")
    """
    player_name = resolve_player_name(player_name)
    svc = get_ctx(ctx)

    # Build changes dict from non-None arguments
    changes: dict[str, str] = {}
    if super is not None:
        changes["super"] = super
    if melee is not None:
        changes["melee"] = melee
    if grenade is not None:
        changes["grenade"] = grenade
    if class_ability is not None:
        changes["class_ability"] = class_ability
    if movement is not None:
        changes["movement"] = movement
    if aspect_1 is not None:
        changes["aspect_1"] = aspect_1
    if aspect_2 is not None:
        changes["aspect_2"] = aspect_2
    if fragment_1 is not None:
        changes["fragment_1"] = fragment_1
    if fragment_2 is not None:
        changes["fragment_2"] = fragment_2
    if fragment_3 is not None:
        changes["fragment_3"] = fragment_3
    if fragment_4 is not None:
        changes["fragment_4"] = fragment_4
    if fragment_5 is not None:
        changes["fragment_5"] = fragment_5
    if fragment_6 is not None:
        changes["fragment_6"] = fragment_6

    if not changes:
        return {
            "success": False,
            "message": "未指定任何更改。请提供至少一个要修改的技能。",
        }

    return await svc['subclass_svc'].modify_subclass(
        player_name, character, changes
    )
