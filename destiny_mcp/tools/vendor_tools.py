"""Vendor & Weekly MCP tools — get_vendor_inventory, get_weekly_reset."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..exceptions import DestinyMCPError
from ._registry import mcp
from ._helpers import get_ctx, handle_tool_error, resolve_player_name
from ._responses import error_response, ok_response

# Fields to strip from compressed vendor output (zero/empty after compression)
_STRIP_PERK_FIELDS = {"plug_hash", "description", "plug_category", "god_roll_pve", "god_roll_pvp"}
_STRIP_ITEM_FIELDS = {"vendor_item_index", "item_hash", "icon", "can_be_sold", "item_type", "tier"}
_STRIP_COST_FIELDS = {"item_hash", "quantity"}
_STRIP_VENDOR_FIELDS = {"icon", "next_refresh"}


def _strip_empty_fields(data: dict) -> dict:
    """Remove zero/empty fields from compressed vendor output to reduce size."""
    for vendor in data.get("vendors", []):
        for f in _STRIP_VENDOR_FIELDS:
            vendor.pop(f, None)
        for item in vendor.get("sale_items", []):
            for f in _STRIP_ITEM_FIELDS:
                item.pop(f, None)
            # Strip empty lists / false booleans / null perks (default = no info)
            if not item.get("failure_reasons"):
                item.pop("failure_reasons", None)
            if not item.get("owned"):
                item.pop("owned", None)
            if not item.get("costs"):
                item.pop("costs", None)
            if item.get("perks") is None:
                item.pop("perks", None)
            for perk in item.get("perks", []) or []:
                for f in _STRIP_PERK_FIELDS:
                    perk.pop(f, None)
            for cost in item.get("costs", []) or []:
                for f in _STRIP_COST_FIELDS:
                    cost.pop(f, None)
    return data


@mcp.tool()
@handle_tool_error
async def summarize_weekly_reset(
    limit: int = 12,
    ctx: Context = None,
) -> dict:
    """汇总本周重置活动，返回分类数量、重点活动和下次重置时间。

    何时使用：用户问"这周有什么活动"、"本周日落/突袭/试炼是什么"这类
    周常概况问题时，优先使用这个工具。
    何时跳过：用户明确要完整里程碑明细时，使用 get_weekly_reset。

    Args:
        limit: 重点活动数量上限，范围 1-25。
    """
    svc = get_ctx(ctx)
    try:
        result = await svc['weekly_analysis_svc'].summarize_weekly_reset(limit=limit)
    except DestinyMCPError as exc:
        return error_response(
            "weekly_summary_failed",
            str(exc),
            next_actions=[
                {
                    "label": "稍后重试周常概况",
                    "tool": "summarize_weekly_reset",
                }
            ],
        )

    return ok_response(
        result["summary"],
        {"weekly": result["weekly"]},
        next_actions=result["next_actions"],
        warnings=result["warnings"],
    )


@mcp.tool()
@handle_tool_error
async def get_vendor_inventory(
    player_name: str | None = None,
    character: str = "",
    vendor_name: str = "",
    ctx: Context = None,
) -> dict:
    """查询商人当前在卖什么，包括商品详情、购买代价和可用状态。

    何时使用：用户问"祖尔在卖什么"、"枪匠这周有什么好东西"时。
    何时使用：用户问"某件商品我能不能买"、"还差什么材料"时。
    何时跳过：用户想查自己的库存（用 get_inventory）。
    何时跳过：用户想查每周重置活动（用 get_weekly_reset）。

    返回每件商品的：名称、类型、品质、购买代价、是否已拥有、是否可购买。
    枪匠等卖武器的商人还会返回商品的 perk roll 信息。

    支持的商人名（中英文）：Xur/祖尔/老九/仄、Banshee/枪匠、Ada/艾达、Rahool/拉乎尔、
    Drifter/漂流者、Zavala/扎瓦拉、Shaxx/沙克斯、Eververse/银币商店。

    注意：
    - 祖尔只在周五至周二出现，其他时间查询会返回空。
    - 需要 OAuth scope ReadDestinyVendorsAndAdvisors，首次使用需重新授权。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色名（商人库存因角色而异）。
        vendor_name: 商人名（中英文均可，如 '枪匠'、'祖尔'），不填返回所有商人。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)

    result = await svc['vendor_svc'].get_vendor_inventory(
        player_name, character, vendor_name
    )
    return _strip_empty_fields(result.model_dump())


@mcp.tool()
@handle_tool_error
async def get_weekly_reset(
    ctx: Context = None,
) -> dict:
    """查询本周重置信息（日落、试炼、突袭挑战等）。

    何时使用：用户问"这周日落是什么"、"本周有什么活动"时。
    何时跳过：用户想查商人库存（用 get_vendor_inventory）。

    返回本周的里程碑活动列表，按类型分类（日落/试炼/突袭/熔炉/其他）。
    不需要 OAuth，任何人可用。
    """
    svc = get_ctx(ctx)

    try:
        result = await svc['weekly_svc'].get_weekly_reset()
    except DestinyMCPError as exc:
        return error_response(
            "weekly_reset_unavailable",
            str(exc),
            next_actions=[
                {
                    "label": "等待 Bungie 服务恢复后重试",
                    "tool": "get_weekly_reset",
                }
            ],
        )
    return result.model_dump()
