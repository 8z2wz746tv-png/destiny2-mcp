"""职业金装（相对主义 / 唯我主义 / 坚忍克己）roll 到的两个异域特性。

**为什么单独一个模块**：`armor_payload` 贴着体积上限（536/536），而这一域的判断
与"六维属性怎么算"是两件事 —— 它读的是**插槽里装着哪颗异域 plug**。

**为什么要处理它**（2026-09-28 用户指出，之前完全没覆盖）：
三件职业金装各有**两个特性槽**，从 36 颗「X之灵」里各 roll 一颗。社区配装里那 14 套
"异域护甲"字段写的就是这两个特性名（例：「至纯光能之灵、曲腹蛛之灵」），而不是金装名 ——
我们既报不出"你这件 roll 的是哪两颗"，也就没法回答"我那件相对主义是不是至纯+曲腹蛛"。

**为什么不落一张 36 颗的静态表**（实测三条，见下）：

1. `plugCategoryIdentifier == "intrinsics"` **不能当判据**：它有 1004 条，皮肤
   （247 条 `armor_skins_*`）、护甲、武器全在里面；
2. 但 `plugCategoryHash == 1944746145` 是精确的：这一族 36 颗指向同一个值，
   **一个常量就够，不需要维护清单**；
3. **池子只能从账号读**：金装的特性槽在 Manifest 里是 `plugSources: 1`（只从实例来），
   定义里两个槽都是占位（`183430252` / `183430246`）—— 静态表能说"游戏里这一族有哪些"，
   却说不了"这件能不能 roll 到它"。那属于实例数据。

**一部分特性还带六维**（15 颗，如至纯光能之灵 = 超能+30 / 近战+25；另 21 颗纯机制，
如曲腹蛛之灵 = 手雷→织造铠甲）。带属性的那部分**早就进了 `roll`**
（`armor_payload` 把 `intrinsics` 与 `armor_stats` 并入同一支算），所以这里只做"报出来"，
不碰六维算法 —— 求解器本来就会偏好带至纯那件。
"""

from __future__ import annotations

from typing import Any, Callable

from ..utils.hash_utils import to_unsigned

#: 职业金装那两个特性槽的 plug 类别（36 颗「X之灵」共用这一个值）。
#: 实测 2026-09-28：`至纯光能之灵`(1476923953) 与 `曲腹蛛之灵`(3751917994) 都指向它。
#: **别凭印象写这个数** —— 我第一版写成了 1944746145（错一位），靠 `tests/test_armor_class_item.py`
#: 拿真实 Manifest 反查才逮住。
CLASS_ITEM_PERK_CATEGORY_HASH = 1744546145

#: 三件职业金装（相对主义 / 唯我主义 / 坚忍克己）。**只用来把"两个特性"翻回金装名**：
#: 社区模板那 14 套的"异域护甲"字段只写特性名（例「至纯光能之灵、曲腹蛛之灵」）而不写金装名，
#: 集齐两个特性必然是其中一件。识别身份**仍按金装名**（它就是一个 `item_hash`）。
CLASS_ITEM_NAMES: dict[str, tuple[str, str]] = {
    "hunter": ("相对主义", "猎人披风"),
    "warlock": ("唯我主义", "术士臂环"),
    "titan": ("坚忍克己", "泰坦印记"),
}

#: 社区模板里两个特性之间的分隔符（实测形态 `A、B`；半角逗号与加号也见过）。
PERK_SEPARATORS = ("、", ",", "，", "+")

def split_perk_names(raw: str) -> list[str]:
    """`"至纯光能之灵、曲腹蛛之灵"` → 两个名字。切完去空白，丢掉空串。"""
    parts = [raw]
    for sep in PERK_SEPARATORS:
        parts = [piece for chunk in parts for piece in chunk.split(sep)]
    return [piece.strip() for piece in parts if piece.strip()]

def perk_plug(manifest: Any, name: str) -> dict[str, Any] | None:
    """按名字拿一颗**职业金特性**的 plug 定义；不是这一族就返回 None。

    为什么按名字查单个而不是扫全表：`plugCategoryIdentifier="intrinsics"` 下有 1004 条
    （皮肤占 247 条一类），扫表既慢又不精确；而这里手上已经有名字，`search` 命中后
    验一下 `plugCategoryHash` 就够。也**不需要**维护 36 颗的静态清单。
    """
    for hit in manifest.search(name, limit=0) or []:
        item_hash = hit.get("itemHash") or hit.get("item_hash")
        if not item_hash:
            continue
        # 探测能力而不是硬依赖：社区比对的替身只实现 search（见 tests/test_starside_integration.py）。
        # 读不到定义 = 认不出是不是这一族 → None，让调用方按普通金装名走，不抛异常。
        get_definition = getattr(manifest, "get_item_definition", None)
        if get_definition is None:
            return None
        try:
            definition = get_definition(int(item_hash))
        except AttributeError:
            return None
        plug = (definition or {}).get("plug") or {}
        if plug.get("plugCategoryHash") != CLASS_ITEM_PERK_CATEGORY_HASH:
            continue
        display = (definition or {}).get("displayProperties") or {}
        # 名字要精确对上：search 是模糊的，别把近义条目当成那颗特性。
        if str(display.get("name", "")).strip().casefold() != name.strip().casefold():
            continue
        return {"item_hash": to_unsigned(int(item_hash)), "name": display.get("name") or name}
    return None

def class_item_for_perks(
    manifest: Any, names: list[str], class_key: str | None
) -> tuple[str, list[dict[str, Any]]] | None:
    """一串特性名 → `(金装名, 已解析的特性)`；只要有一个不是这一族就返回 None。

    返回 None 表示"这不是职业金特性的写法"，调用方该按普通金装名去解析 —— **不许**猜。
    """
    if not names or class_key not in CLASS_ITEM_NAMES:
        return None
    resolved = []
    for name in names:
        plug = perk_plug(manifest, name)
        if plug is None:
            return None
        resolved.append(plug)
    armor_name, _display = CLASS_ITEM_NAMES[class_key]
    return armor_name, resolved

def class_item_perks(
    sockets: list[dict[str, Any]],
    lookup: Callable[[int], dict[str, Any] | None],
    stats_of: Callable[[dict[str, Any] | None], dict[str, int]],
) -> list[dict[str, Any]]:
    """从插槽行里挑出**职业金装的异域特性**，按槽序返回。

    `stats_of` 由调用方传进来（`armor_payload._plug_stats`）—— 六维 hash → 键名那份映射
    是它的单一出处，这里不再抄第二份，也避免和它互相 import。
    返回空列表 = 这件没有 roll 特性（不是职业金装，或槽是空的）。
    """
    out: list[dict[str, Any]] = []
    for row in sockets:
        plug_hash = int(row.get("plug_hash") or 0)
        if not plug_hash:
            continue
        definition = lookup(plug_hash) or {}
        plug = definition.get("plug") or {}
        if plug.get("plugCategoryHash") != CLASS_ITEM_PERK_CATEGORY_HASH:
            continue
        out.append({
            "name": row.get("name") or "",
            "item_hash": plug_hash,
            # 纯机制特性没有属性 —— 给空 dict，不编 0（"没有加成"和"没读到"是两回事）。
            "stats": stats_of(definition),
        })
    return out

def classify_required_perks(
    required: list[dict[str, Any]],
    sockets_by_instance: dict[str, list[dict[str, Any]]],
    candidate_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """把"要求的两颗特性"和"实际 roll 到的"对一遍。

    返回 `(核对结果, 最佳那件)`；最佳那件是**同时满足全部要求**的第一件（没有就是 None）。

    入参 `sockets_by_instance` 里的每项已经是"**只含职业金特性**的已装插槽"
    （`[{name, plug_hash}]`，由本模块 `class_item_perks_of` 抽好）—— 判据只写一份。

    三种结论不许混：
    - `verified`：读到了，且有副本同时满足全部要求 —— 最佳那件给它是谁。
    - `owned_wrong_perks`：读到了，但没有任何副本满足 —— 列出各副本实际滚到的，供人决定。
    - `unknown`：某个副本的插槽没读到（组件 305 缺这件）—— **不能**降级成"不满足"。
    """
    required_hashes = {to_unsigned(int(p["item_hash"])) for p in required}
    rolled: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    read_any = False
    for instance_id in candidate_ids:
        sockets = sockets_by_instance.get(str(instance_id))
        if sockets is None:
            continue
        read_any = True
        names = [plug.get("name") or "" for plug in sockets]
        got = {to_unsigned(int(plug.get("plug_hash") or 0)) for plug in sockets}
        if required_hashes <= got and best is None:
            best = {"instance_id": str(instance_id), "perks": names}
        rolled.append({"instance_id": str(instance_id), "perks": names})
    if not read_any:
        return {"status": "unknown", "read": 0, "reason": "socket_component_missing_for_candidates"}, None
    if best is not None:
        return {"status": "verified", "read": len(rolled), "rolled": rolled}, best
    return {"status": "owned_wrong_perks", "read": len(rolled), "rolled": rolled}, None


def class_item_perks_of(sockets: list[dict[str, Any]], lookup: Callable[[int], dict[str, Any] | None]) -> list[dict[str, Any]]:
    """**已装插槽**（组件 305 的原始形态）→ 职业金装那两个异域特性 `[{name, plug_hash}]`，按槽序。

    `armor_payload` 拼载荷时用它（载荷里给的是 `{name, item_hash, stats}`，那是给人看的形状）；
    "核对某件 roll 得对不对"也用它 —— 两边共用一个判据，不可能再分叉。
    """
    out: list[dict[str, Any]] = []
    for entry in sockets or []:
        plug_hash = int(entry.get("plugHash") or entry.get("plug_hash") or 0)
        plug_def = lookup(plug_hash) or {} if plug_hash else {}
        if (plug_def.get("plug") or {}).get("plugCategoryHash") != CLASS_ITEM_PERK_CATEGORY_HASH:
            continue
        name = (plug_def.get("displayProperties") or {}).get("name") or ""
        out.append({"name": name, "plug_hash": plug_hash})
    return out
