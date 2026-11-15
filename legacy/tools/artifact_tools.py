"""Seasonal artifact MCP tools — get_seasonal_artifact, get_artifact_mod_info, equip_artifact_mod.

Rule 1: Thin wrappers. Business logic in manifest and ArtifactService.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ._registry import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name


@mcp.tool()
@handle_tool_error
async def get_seasonal_artifact(
    artifact_name: str = "",
    ctx: Context = None,
) -> str:
    """查询赛季神器的信息，包括所有等级和可选模组。

    每个赛季神器有 5 个等级，每个等级解锁不同的模组列表。
    命运 2 中有多个赛季神器可以切换使用。

    何时使用：
    - 用户问"有哪些赛季神器"、"神器有哪些模组"
    - 用户想了解赛季神器的等级和解锁条件

    何时跳过：
    - 用户只想看当前装备的神器模组（用 get_inventory）
    - 用户想装备神器模组（用 equip_artifact_mod）

    Args:
        artifact_name: 神器名称（可选，支持模糊匹配）
                      不填则列出所有可用神器
                      填写则显示指定神器的详情
    """
    svc = get_ctx(ctx)
    manifest = svc['manifest']

    if not artifact_name:
        artifacts = manifest.get_all_artifacts()
        if not artifacts:
            return "⚠️ 未找到任何赛季神器"

        lines = ["=== 所有赛季神器 ==="]
        for i, art in enumerate(artifacts, 1):
            lines.append(f"{i}. {art['name']} (hash: {art['hash']})")
            if art.get('description'):
                lines.append(f"   {art['description'][:60]}")
        lines.append("")
        lines.append("使用 artifact_name 参数查看特定神器的详情")
        return "\n".join(lines)

    artifact = manifest.get_artifact_by_name(artifact_name)
    if not artifact:
        return f"⚠️ 未找到名称包含 '{artifact_name}' 的赛季神器"

    lines = [
        f"=== {artifact['name']} ===",
        f"hash: {artifact['hash']}",
    ]
    if artifact.get('description'):
        lines.append(f"描述：{artifact['description'][:100]}")

    artifact_def = manifest.get_current_artifact()
    if artifact_def and artifact_def.get('tiers'):
        lines.append("")
        lines.append("=== 模组信息 ===")
        for tier in artifact_def['tiers']:
            tier_name = tier['display_title']
            min_points = tier['min_unlock_points']
            mods = tier['mods']

            lines.append(f"\n{tier_name}（需 {min_points} 解锁点，{len(mods)} 个模组）:")
            for i, mod in enumerate(mods[:3], 1):
                lines.append(f"  {i}. {mod['name']} | hash: {mod['hash']}")
                if mod.get('description'):
                    lines.append(f"     效果：{mod['description'][:50]}")
            if len(mods) > 3:
                lines.append(f"  ... 还有 {len(mods) - 3} 个模组")

    return "\n".join(lines)


@mcp.tool()
@handle_tool_error
async def get_artifact_mod_info(
    mod_hash: int,
    ctx: Context = None,
) -> str:
    """查询赛季神器模组的详细信息。

    何时使用：
    - 用户问"某个神器模组的效果是什么"
    - 用户想了解某个模组的详细 perk

    Args:
        mod_hash: 模组的 hash 值（从 get_seasonal_artifact 获取）
    """
    svc = get_ctx(ctx)
    manifest = svc['manifest']
    mod_info = manifest.get_artifact_mod_details(mod_hash)

    if not mod_info:
        return f"⚠️ 未找到 hash={mod_hash} 的模组"

    lines = [
        f"=== {mod_info['name']} ===",
        f"hash: {mod_info['hash']}",
        f"描述：{mod_info['description'][:100]}",
    ]

    if mod_info["perks"]:
        lines.append("Perk 效果：")
        for perk in mod_info["perks"]:
            lines.append(f"  - {perk['name']}: {perk['description'][:80]}")

    return "\n".join(lines)


@mcp.tool()
@handle_tool_error
async def equip_artifact_mod(
    mod_hash: int,
    character: str = "",
    player_name: str | None = None,
    ctx: Context = None,
) -> str:
    """装备赛季神器模组。

    赛季神器模组通过 InsertSocketPlugFree API 装备，不需要消耗品。

    何时使用：
    - 用户说"装备XX神器模组"、"激活XX模组"
    - 用户在 get_seasonal_artifact 结果中选定了要装备的模组

    何时跳过：
    - 用户只想查看有哪些模组（用 get_seasonal_artifact）
    - 用户想装备护甲模组（用 apply_mod）

    Args:
        mod_hash: 要装备的模组 hash（从 get_seasonal_artifact 获取）
        character: 角色名（hunter/warlock/titan，或中文）
        player_name: Bungie 名称

    注意：
    - 赛季神器模组是全局共享的，装备一次对所有角色生效
    - 需要足够的解锁点数才能装备高等级模组
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(player_name)
    result = await svc['artifact_svc'].equip_artifact_mod(player_name, mod_hash, character)

    if result["success"]:
        return f"✅ {result['message']}"
    else:
        return f"⚠️ {result['message']}"
