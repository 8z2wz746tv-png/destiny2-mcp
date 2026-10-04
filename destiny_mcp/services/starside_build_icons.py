"""社区配装模板 → Bungie 图标：**只有解析得到可核 hash、且同名定义的图一致时才出图**。

与隔壁 `starside_icons.py` 的分工：那个管的是 Starside 归档里**站点自己的**相对路径
（`assets/<主题>/icons/<hash>.webp`）；本模块管的是社区模板里的**物品名**能不能落到
Bungie CDN 的一张图上。两件事的数据来源和可信等级都不同，不合并。

## 为什么单独成模块

`starside_service.py` 贴着登记上限（769 行），社区模板的两条出口（列表行、单套详情）
都要补图标。判断逻辑全部落在本模块，调用点只多一行取值。

## 红线：宁可不出图，不可出错图

用户 2026-10-05 的价值判断（原话）：**玩家看图比看名快 —— 很多时候是看了图标才想起名字。**
所以错图比没图坏得多：看图记名字的人会**记错**。社区模板是**不可信参考**，它给的是一段
散文，唯一的输入是一串名字；名字到物品的这一跳必须可核。本模块因此有三条硬规则：

1. **只用精确名解析**（`starside_matching._exact_definitions`：归一后逐字相等 + 类型/稀有度/
   职业过滤）。相似度匹配一个都不使用 —— 那正是"活动名当套装名"的来源：
   实测模板里的「贪婪之握」是**活动**，Manifest 里精确名 0 命中 → 不出图。
2. **同名多版本必须图一致才出图**。实测反例：「重型弹药搜寻者」3 个 hash 里有 **2 种图**
   （`2867719094` 与 `644105`/`554409585`），挑一个就是猜。图不一致 → 空串。
3. **裸标签不许直接解析**。实测反例：模板写「分支: 棱镜」，而 `棱镜` 这个**精确名**命中的是
   一件**武器皮肤**（`3373357626`，`itemType=19`、`typeDisp=武器皮肤`）—— 拿它的图当
   "子职业"就是一张错图。子职业必须带类型过滤（`item_type=16`），并用「标签 + 职业名」
   拼出真正的物品名（`棱镜` + `猎人` → `棱镜猎人` → `4282591831`，实测命中）。

## 出图之后仍然不是账号事实

这里只回答"这个**名字**在游戏里对应哪张图"，不回答"你有没有"。后者要走
`inventory_match`（组件 305/310），两条道不能混。
"""

from __future__ import annotations

from typing import Any

from ..utils.icons import icon_url as _icon_url
from .armor_class_item import class_item_for_perks, split_perk_names
from .starside_matching import CLASS_TYPES, _exact_definitions

#: 子职业物品的 `itemType`（实测 `4282591831`「棱镜猎人」= 16）。
SUBCLASS_ITEM_TYPE = 16
#: 类型过滤用的 itemType：武器 3、护甲 2、插件/模组 19。
WEAPON_ITEM_TYPE = 3
ARMOR_ITEM_TYPE = 2
#: `tier` 字段：5 = 传说、6 = 异域。
LEGENDARY_TIER = 5
EXOTIC_TIER = 6


#: 列表行投影哪些键（原在 `starside_service.search_builds` 里；搬过来是为了让那个
#: 贴着登记上限的文件只留一行调用）。
LIST_ROW_KEYS = frozenset({
    "build_id", "title", "author", "updated_at", "scenario", "role", "category",
    "subclass", "class", "core", "description", "weapons", "armor", "review_notes",
    "source", "executable",
})


def agreed_icon(manifest: Any, item_hashes: list[int]) -> str:
    """一组候选 hash 的图标：**全部指向同一张图**才返回，否则空串。

    这是规则 2 的唯一实现点。空串不是"没做这一步"，是"做过了、结论是不出图"。
    """
    icons = set()
    for item_hash in item_hashes:
        icon = _icon_url(str((manifest.get_item_info(int(item_hash)) or {}).get("icon") or ""))
        if icon:
            icons.add(icon)
    return icons.pop() if len(icons) == 1 else ""


def resolved_row(manifest: Any, name: str, **filters: Any) -> dict[str, Any]:
    """名字 → `{name, icon_url}`（精确解析 + 图一致）。解析不到就给空串，**不猜**。"""
    definitions = _exact_definitions(manifest, name, **filters)
    return {"name": name, "icon_url": agreed_icon(manifest, [d["item_hash"] for d in definitions])}


def _weapon_rows(manifest: Any, build: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for weapon in build.get("weapons") or []:
        tier = EXOTIC_TIER if weapon.get("tier") == "exotic" else LEGENDARY_TIER
        rows.append(resolved_row(manifest, weapon["name"], item_type=WEAPON_ITEM_TYPE, tier=tier))
    return rows


def _exotic_row(
    manifest: Any, build: dict[str, Any], class_id: str, class_type: int | None
) -> dict[str, Any] | None:
    """异域护甲行。职业金装的写法是**两个特性名**（「至纯光能之灵、曲腹蛛之灵」），
    先按特性还原成金装名 —— 与 `validate_build` 调用同一个函数，不另写一套判据。"""
    exotic = str((build.get("armor") or {}).get("exotic") or "").strip()
    if not exotic:
        return None
    class_item = class_item_for_perks(manifest, split_perk_names(exotic), class_id)
    name = class_item[0] if class_item else exotic
    if class_type is None:
        return {"name": name, "icon_url": ""}
    return resolved_row(manifest, name, item_type=ARMOR_ITEM_TYPE, tier=EXOTIC_TIER, class_type=class_type)


def _set_row(build: dict[str, Any]) -> dict[str, Any] | None:
    """套装行：**只有名字，没有图**。

    不是"还没做"：套装是 `DestinyEquipableItemSetDefinition`，实测 `741162535`
    写着 `hasIcon: false` —— 它在 Manifest 里就没有图标（`test_icon_url_output._EXEMPT`
    里「套装不是物品」是同一条口径）。给空串而不是省略，是为了让渲染侧知道
    "这一项存在、只是没有图"，而不是"模板里没写"。"""
    sets = (build.get("armor") or {}).get("set_requirements") or []
    if not sets:
        return None
    return {"name": sets[0]["name"], "icon_url": ""}


def _subclass_row(
    manifest: Any, build: dict[str, Any], class_type: int | None
) -> dict[str, Any] | None:
    """子职业行（规则 3 的唯一实现点）。

    模板的 `分支` 是**短标签**（「棱镜」），直接拿它去解析会命中武器皮肤。所以：

    1. 先用「标签 + 职业名」拼真正的物品名（`棱镜` + `猎人` → `棱镜猎人`），**带类型过滤**
       去解析 —— 这是 Manifest 自己的命名法，解析不到就没有；
    2. 拼不出来时退回"拿标签本身带类型过滤"再试一次（模板偶尔写全名），仍不行就不出图。
    """
    label = str(build.get("subclass") or "").strip()
    if not label:
        return None
    class_display = str((build.get("class") or {}).get("name") or "").strip()
    filters: dict[str, Any] = {"item_type": SUBCLASS_ITEM_TYPE}
    if class_type is not None:
        filters["class_type"] = class_type
    candidates = [f"{label}{class_display}", label] if class_display else [label]
    for name in candidates:
        row = resolved_row(manifest, name, **filters)
        if row["icon_url"]:
            return row
    return {"name": label, "icon_url": ""}


def build_visuals(manifest: Any, build: dict[str, Any]) -> dict[str, Any]:
    """一套配装的**简版**图 + 名：护甲 / 武器 / 子职业（用户口径：列表只给这三项）。

    每一项都可能是 `icon_url: ""` —— 那是"解析不到或图不一致"，**不是漏了**。
    """
    class_id = str((build.get("class") or {}).get("id") or "")
    class_type = CLASS_TYPES.get(class_id)
    armor: dict[str, Any] = {}
    exotic = _exotic_row(manifest, build, class_id, class_type)
    if exotic:
        armor["exotic"] = exotic
    set_row = _set_row(build)
    if set_row:
        armor["set"] = set_row
    return {
        "weapons": _weapon_rows(manifest, build),
        "armor": armor,
        "subclass": _subclass_row(manifest, build, class_type),
    }


def _perk_icon(manifest: Any, row: dict[str, Any], *, hash_key: str = "item_hash") -> None:
    """给一行"插件"补 `icon_url`（`{}` 就地改；这些行都带一个已解析出来的 hash）。"""
    item_hash = row.get(hash_key)
    row["icon_url"] = agreed_icon(manifest, [int(item_hash)]) if item_hash else ""


def with_requirement_icons(manifest: Any, validation: dict[str, Any]) -> dict[str, Any]:
    """详情：给 `validation.requirements[]` 的每一行补 `icon_url`（**全部细节**那一档）。

    这些行本来就有 `definitions[].item_hash`（`validate_build` 解析出来的），所以这一跳
    **不是按名字猜**，是"拿已经解析出来的 hash 取图"；仍然要过"图一致"那道闸
    （同一行可能解析到多个同版本定义）。套装行给空串（见 `_set_row`）。

    三处都有图才算"全部细节"：行本身（武器/护甲/模组/神器/子职业组件）、武器 perk 行、
    职业金装那两个特性行。**`definitions[]` 不加** —— 那是候选清单，一行挂好几个同版本
    定义，逐条给图会把详情撑胖，而它们指向的就是本行那一张图（同一套判据算出来的）。
    """
    for requirement in validation.get("requirements") or []:
        definitions = requirement.get("definitions") or []
        if requirement.get("kind") == "armor_set":
            requirement["icon_url"] = ""
        else:
            requirement["icon_url"] = agreed_icon(
                manifest,
                [d["item_hash"] for d in definitions if d.get("item_hash")],
            )
        for perk in requirement.get("perk_resolutions") or []:
            perk["icon_url"] = agreed_icon(
                manifest,
                [d["item_hash"] for d in (perk.get("definitions") or []) if d.get("item_hash")],
            )
        for perk in requirement.get("required_class_item_perks") or []:
            _perk_icon(manifest, perk)
    return validation


def list_row(manifest: Any, build: dict[str, Any]) -> dict[str, Any]:
    """社区配装**列表行**：模板投影 + 简版 `visuals`（护甲/武器/子职业）。"""
    return {
        **{key: value for key, value in build.items() if key in LIST_ROW_KEYS},
        "visuals": build_visuals(manifest, build),
    }


__all__ = ["agreed_icon", "build_visuals", "list_row", "resolved_row", "with_requirement_icons"]
