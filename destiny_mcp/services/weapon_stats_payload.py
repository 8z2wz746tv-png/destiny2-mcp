"""武器**属性值**（`stats`）的唯一形状：`[{stat_hash, name, value, display, is_primary, display_as_numeric}]`。

从 `weapon_payload.py` 拆出来（那个文件是上帝模块，加了闸：只能减不能加）。
拆的理由不只是行数：属性值的取数规则（顺序来自 StatGroup、实例值优先）以前一半在这里、
一半在 `weapon_profile.stat_value`，两边都得改才改得动一件事。

谁在用：`info` / `stats` / `type` 列表行 / 副本对比 / 旧工具面，全部走这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from . import weapon_profile

if TYPE_CHECKING:  # pragma: no cover
    from ..manifest import ManifestManager


def _stat_order(manifest: "ManifestManager", definition: Mapping[str, Any]) -> tuple[list[int], dict[int, bool]]:
    """属性顺序与"是否按数字展示"都来自 StatGroup；没有分组时退回定义顺序。"""
    core = definition.get("stats") or {}
    group_hash = core.get("statGroupHash")
    group = (
        manifest.get_definition("DestinyStatGroupDefinition", group_hash)
        if group_hash
        else None
    )
    scaled = (group or {}).get("scaledStats") or []
    if scaled:
        order = [int(entry.get("statHash") or 0) for entry in scaled if entry.get("statHash")]
        numeric = {
            int(entry.get("statHash") or 0): bool(entry.get("displayAsNumeric"))
            for entry in scaled
        }
        return order, numeric
    order = [
        int(entry.get("statTypeHash") or 0)
        for entry in definition.get("investmentStats") or []
        if entry.get("statTypeHash")
    ]
    return order, {}


def stat_list(
    manifest: "ManifestManager",
    definition: Mapping[str, Any] | None,
    instance_stats: Mapping[str | int, Any] | None = None,  # 304 给字符串键，历史调用方也传过 int
    *,
    names: Any = None,
) -> list[dict[str, Any]]:
    """`stats` 块：`[{stat_hash, name, value, display, is_primary, display_as_numeric}]`。

    值优先取实例（组件 304 `itemComponents.stats`），否则取定义里的显示值
    （`definition.stats.stats[hash].value`，不是 `investmentStats` —— 后者漏掉框架加成，
    遗产的每分钟发射数会从 65 变成 30）。没名字的属性直接不输出，避免编一个名字糊弄。
    """
    if not definition:
        return []
    if names is None:
        from ..manifest_names import names_for

        names = names_for(manifest)
    core = definition.get("stats") or {}
    primary = core.get("primaryBaseStatHash")
    order, numeric = _stat_order(manifest, definition)
    instance = instance_stats if isinstance(instance_stats, Mapping) else {}

    stats: list[dict[str, Any]] = []
    seen: set[int] = set()
    for stat_hash in order:
        if not stat_hash or stat_hash in seen:
            continue
        seen.add(stat_hash)
        entry = instance.get(str(stat_hash)) or instance.get(stat_hash) or {}
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if not isinstance(value, int) or isinstance(value, bool):
            value = weapon_profile.stat_value(definition, stat_hash)
        name = str(names.stat(stat_hash) or "")
        if value is None or not name:
            continue
        stats.append(
            {
                "stat_hash": stat_hash,
                "name": name,
                "value": value,
                "display": str(value),
                "is_primary": stat_hash == primary,
                "display_as_numeric": numeric.get(stat_hash),
            }
        )
    return stats
