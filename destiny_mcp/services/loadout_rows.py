"""已存配装的**清单行**：`loadout_assistant(intent="list")` 一套一行。

从 `loadout_service` 搬出来（那边贴着 1030 行上限）：清单行与完整模板是两件事 ——
`get` 给整包 `build_template`（每套约 11 KB，20 套 ≈ 227 KB），`list` 只给"让人挑一套"的
字段 + 一个**能画卡片的图块**。读写账号、缓存、迁移仍归 `loadout_service`，这里只做形状。

图块（`visuals`）的形状照社区配装列表（`data.results[].visuals.*`，
见 `services/starside_build_icons.py`）：同一段渲染 HTML 两边都能用。
那份的红线在这条道上同样成立 —— **错图比没图坏得多**，所以认不唯一就给空串/`None`，
由渲染侧画同尺寸占位块，不拿别的图顶上。
"""

from __future__ import annotations

from typing import Any


def _visual(name: str, item_hash: int | None, icon_url: str) -> dict[str, Any]:
    """「这一行是什么」三件套：名字 / hash / 图。

    `item_hash` 认不出来时给 `None`（**不编 0**：缺值与"真的是 0"是两件事）；
    `icon_url` 认不出来时给空串（渲染侧画占位块，不写 `<img src="">`）。
    """
    return {"name": name, "item_hash": item_hash, "icon_url": icon_url}


def _positive_hash(value: Any) -> int | None:
    """能当身份用的 hash：正整数才算，其余（缺失、0、布尔、字符串）一律 `None`。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _exotic_visual(armor: dict[str, Any]) -> dict[str, Any]:
    """金装那一行：从这套配装**自己的**五件护甲里反查回那一件。

    为什么按名字找：`build_template.armor.exotic` 本来就是建模板时从这五件的名字里挑的
    （`loadout_service._build_template`：`tier == 6` 的第一件），这里只是反过来取它的 hash 与图。
    **所以不碰 Manifest、不做名字解析** —— hash 与 `icon_url` 都是那条路已经算好、写在同一行的
    （`armor.items[].icon_url`，出处是 `manifest_lookup.get_icon_url()` 那条单一出处）。

    **同名 0 件或 2 件以上都不给**（按"没解析到"处理）：这一行的用途是画图，
    挑错一张比空着坏得多（同 `starside_build_icons` 的红线）。
    """
    name = str(armor.get("exotic") or "").strip()
    matches = [
        item for item in (armor.get("items") or [])
        if str(item.get("name") or "").strip() == name
    ]
    if not name or len(matches) != 1:
        return _visual(name, None, "")
    item = matches[0]
    return _visual(name, _positive_hash(item.get("item_hash")), str(item.get("icon_url") or ""))


def _subclass_visual(template: dict[str, Any]) -> dict[str, Any]:
    """子职业那一行：hash 与图**同源**（`build_template.class` 自己那两行）。"""
    class_block = template.get("class") or {}
    return _visual(
        str(template.get("subclass") or "").strip(),
        _positive_hash(class_block.get("subclass_item_hash")),
        str(class_block.get("icon_url") or ""),
    )


def loadout_rows(loadouts: list[dict]) -> list[dict]:
    """配装**清单行**：列表页只该给这些，完整 `build_template` 走 `get`。

    真机（2026-09-24）：`intent="list"` 一次回 **121 KB**（5 套，截断；共 21 套约 500 KB）—— 每件
    装备把同一份插槽数据发三遍（`plugs`/`perk_hashes`/`perks`），列表里还塞了完整模板。这里只留
    "让人挑一套"的字段，外加**能不能执行**与**怎么取详情**（能力字段不能省，省了模型就说"我做不到"）。

    2026-10-05 补 `visuals`（用户实测：整包 0 个 `icon_url`）：清单行此前只有名字，
    卡片上一张图都没有。给的是**账号数据里已经解析好的**两处身份（子职业那一行、
    金装那一件），不做任何名字→Manifest 的解析 —— 所以是纯加法，出不了错图。

    入参是**已经 dump 过的字典**（工具层拿到 payload 后调用，不再解析 pydantic 对象）。
    """
    rows: list[dict] = []
    for loadout in loadouts:
        template = loadout.get("build_template") or {}
        armor = template.get("armor") or {}
        class_block = template.get("class") or {}
        rows.append({
            "loadout_id": loadout.get("id", ""),
            "name": loadout.get("name", ""),
            "character": loadout.get("character", ""),
            "source": loadout.get("source", ""),
            "slot_number": loadout.get("slot_number"),
            "item_count": len(loadout.get("items") or []),
            "exotic_armor": str(armor.get("exotic") or "").strip(),
            "armor_set": str(armor.get("set") or "").strip(),
            "subclass": str(template.get("subclass") or "").strip(),
            "weapon_count": len(template.get("weapons") or []),
            "mod_count": sum(
                len(mods or []) for mods in (armor.get("mods") or {}).values()
            ),
            "subclass_plug_count": len(class_block.get("plugs") or []),
            # 卡片要的两行图（形状与社区配装列表的 `visuals` 一致）。
            # 套装（`armor.set`）**没有图**：`DestinyEquipableItemSetDefinition` 实测 `hasIcon: false`。
            "visuals": {
                "armor": {"exotic": _exotic_visual(armor)},
                "subclass": _subclass_visual(template),
            },
            "created_at": loadout.get("created_at", ""),
            "execution_supported": bool((template.get("execution") or {}).get("supported")),
            "detail_hint": (
                '要看这一套的逐件装备/模组/子职业：loadout_assistant(intent="get", '
                f'loadout_id="{loadout.get("id", "")}")'
            ),
        })
    return rows
