"""Human-readable formatters for tool responses.

These functions convert structured data into text that LLMs can directly
use and present to users, reducing hallucination risk.
"""

from __future__ import annotations

from collections import defaultdict

from ..models.inventory import InventoryItem, InventoryResponse, SearchItemsResponse


# ── Bucket display names (Chinese) ──────────────────────────────────────────
# 从 manifest 中文版查询的官方翻译
_BUCKET_NAMES = {
    "Kinetic Weapons": "动能武器",
    "Energy Weapons": "能量武器",
    "Power Weapons": "威能武器",
    "Helmet": "头盔",
    "Gauntlets": "臂铠",
    "Chest Armor": "胸部护甲",
    "Leg Armor": "腿部护甲",
    "Class Armor": "职业护甲",
    "Subclass": "子职业",
}


def _bucket_label(bucket: str) -> str:
    """Return Chinese bucket name, falling back to the raw string."""
    return _BUCKET_NAMES.get(bucket, bucket)


def _format_item(item: InventoryItem, index: int) -> str:
    """Format a single inventory item as a readable line."""
    equipped = " [已装备]" if item.is_equipped else ""
    power = f" | 光等 {item.power}" if item.power else ""
    # 显示物品类型中文名（手炮/冲锋枪等），让 AI 更容易识别
    item_type = f" | {item.item_type_display}" if item.item_type_display else ""
    # 护甲显示六维属性（FINAL值，含大师杰作/模组/精工加成）
    stats_str = ""
    if item.stats:
        s = item.stats
        stats_str = (
            f" | 武器{s.weapons} 生命{s.health} 职业{s.class_stat}"
            f" 手雷{s.grenade} 近战{s.melee} 超能{s.super_stat}"
        )
    return f"{index}. {item.name}{item_type}{power}{stats_str}{equipped}"


def _group_by_bucket(items: list[InventoryItem]) -> dict[str, list[InventoryItem]]:
    """Group items by their bucket type."""
    groups: dict[str, list[InventoryItem]] = defaultdict(list)
    for item in items:
        groups[item.bucket_type].append(item)
    return dict(groups)


# ── Public formatters ───────────────────────────────────────────────────────


def format_inventory(response: InventoryResponse) -> str:
    """Format get_inventory response as a readable grouped list."""
    if not response.items:
        hint = ""
        if response.location == "vault":
            hint = "\n注意：这只表示仓库（Vault）没有匹配物品；角色身上的装备请查 location=all 或具体职业。"
        return (
            f"{response.location}：没有找到任何物品。\n"
            "不要猜测物品名称，请告知用户该位置没有符合条件的物品。"
            f"{hint}"
        )

    groups = _group_by_bucket(response.items)
    lines: list[str] = []

    for bucket, items in groups.items():
        label = _bucket_label(bucket)
        lines.append(f"=== {response.location} · {label} ({len(items)}) ===")
        for i, item in enumerate(items, 1):
            lines.append(_format_item(item, i))
        lines.append("")  # blank line between groups

    return "\n".join(lines).rstrip()


def format_search_items(response: SearchItemsResponse) -> str:
    """Format search_items response as a readable list."""
    if not response.items:
        return (
            f"搜索「{response.query}」结果：未找到匹配物品。\n\n"
            "建议：\n"
            "1. 检查物品名称是否正确\n"
            "2. 尝试用更短的关键词搜索\n"
            "3. 用 get_inventory 查看完整物品列表"
        )

    lines = [f"搜索「{response.query}」找到 {len(response.items)} 个匹配："]
    for i, item in enumerate(response.items, 1):
        lines.append(_format_item(item, i))

    return "\n".join(lines)


def format_armor_mods(mods: list[dict], slot: str, category: str) -> str:
    """Format get_armor_mods response as a readable list.

    Returns an error message if mods is empty, to prevent LLM hallucination.
    """
    if not mods:
        return (
            "错误：未找到匹配的护甲模组数据。可能是过滤条件没有匹配，"
            "或当前 manifest 的护甲模组分类暂未覆盖。\n"
            "不要猜测模组名称，请告知用户模组数据暂时不可用。"
        )

    slot_label = slot if slot else "全部部位"
    cat_label = category if category != "all" else "全部类别"
    lines = [f"=== 护甲模组 · {slot_label} · {cat_label} ({len(mods)}) ==="]

    for i, mod in enumerate(mods, 1):
        name = mod.get("name", "未知")
        cost = mod.get("energy_cost", "?")
        stat_bonus = mod.get("stat_bonus", {})
        desc = mod.get("description", "")

        # Build stat bonus string
        if stat_bonus:
            bonus_parts = [f"+{v} {k}" for k, v in stat_bonus.items()]
            bonus_str = " | ".join(bonus_parts)
            lines.append(f"{i}. {name} | {bonus_str} | {cost}能量")
        elif desc:
            # For mods without stat bonus (e.g., slot-specific effects), show description
            # Truncate long descriptions to keep it readable
            desc_short = desc[:60] + "..." if len(desc) > 60 else desc
            lines.append(f"{i}. {name} | {desc_short} | {cost}能量")
        else:
            lines.append(f"{i}. {name} | {cost}能量")

    return "\n".join(lines)
