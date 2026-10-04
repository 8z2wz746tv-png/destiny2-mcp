"""`weapon_assistant(intent="analyze")` 的默认出口投影。

真机基线（2026-09-25，`weapon_name="遗产"`）：一次 **68.6 KB**，其中

- 定义级插槽池 `sockets` **21.1 KB**（15 栏；枪管 22 项 / 弹匣 16 / 特性各 20 —— 每项 232 B，
  装的是 `plug_hash` + `name` + `can_roll` + `enhanced_plug_hash` + `stat_effects` + `recommended`）；
- 自己副本的逐件明细 `inventory.instances` **30.5 KB**（2 件），其中**每件 9 KB 是"可换项"** `options`
  （每项还带一整句 `description`）—— 那是"这把枪每个栏位能换成什么"，属于 `compare` 的活（ADR-007：
  可换项不进默认出口）。

投影口径（与 `find`/`duplicates` 同一套）：

1. **池子只给"值得看的那几个"**：本地愿单有结论（`recommended.pve/pvp`）的选项默认全给；
   一栏里一个都没有时，退回前 `option_sample` 个并说明 `options_note`（**不许静默变空** ——
   "没给"和"没有"是两件事）。完整池子按需走 `weapon_assistant(intent="perk_pool")`。
2. **副本去掉可换项**：保留现在装着什么（`sockets[].equipped` 的名字，含强化版 `name_plain`）、
   光等、位置、六维与差异结论；要看可换项走 `intent="compare"`。
3. 选项里的 0/空值不再逐个发（`enhanced_plug_hash: 0` 每项 22 B × 上百项）；强化版只留
   `enhanced: true` 这一条信息。
"""

from __future__ import annotations

from typing import Any

from .weapon_option_rows import option_row, recommended

#: 一栏里没有任何愿单结论时，退回展示前几个（够看清"这栏长什么样"，不假装池子很小）
OPTION_SAMPLE = 6

#: 这些栏决定一把枪的 roll（框架/枪管/弹匣/特性）：**固定也要列出来**，
#: 否则"这一栏没得选"会被读成"这一栏不存在"（0.7.13 的行视图踩过）。
_ROLL_COLUMN_KINDS = frozenset({"intrinsic", "barrel", "magazine", "battery", "trait", "origin"})

#: 副本行视图里**卡头那一行**的键（`name` + `item_hash` + `icon_url` 三件套必须齐：
#: 名字说"这是什么"、hash 说"是哪一个版本"、图给卡片一个能画的东西）。
#: 单独提成常量：投影是"挑几个键抄出来"，键清单写在内联元组里的话，
#: `tests/test_icon_url_output.py` 扫不到（它认的是**命名**的键清单常量）。
_WEAPON_ROW_KEYS = ("name", "name_en", "weapon_type", "frame", "ammo_type", "damage_type",
                    "has_enhanced", "roll_kind", "item_hash", "icon_url")


def _socket_row(socket: dict[str, Any]) -> dict[str, Any]:
    options = [o for o in socket.get("options") or [] if isinstance(o, dict)]
    picked = [o for o in options if recommended(o)]
    note = ""
    if not picked:
        picked = options[:OPTION_SAMPLE]
        if len(options) > OPTION_SAMPLE:
            note = (
                f"这一栏本地愿单没有结论，只列了前 {OPTION_SAMPLE} 项（共 {len(options)} 项）；"
                '要看全池用 weapon_assistant(intent="perk_pool")。'
            )
    row: dict[str, Any] = {
        key: socket[key]
        for key in ("kind", "slot", "option_count")
        if socket.get(key) not in (None, "")
    }
    row["option_count"] = socket.get("option_count") or len(options)
    row["recommended_count"] = len([o for o in options if recommended(o)])
    row["options"] = [option_row(o) for o in picked]
    if note:
        row["options_note"] = note
    return row


def _instance_row(instance: dict[str, Any]) -> dict[str, Any]:
    """副本行：去掉整块"可换项"（`options`），并把副本内插槽的选项一并去掉。"""
    row = {key: value for key, value in instance.items() if key != "options"}
    sockets = row.get("sockets")
    if isinstance(sockets, list):
        row["sockets"] = [
            {key: value for key, value in socket.items() if key != "options"}
            if isinstance(socket, dict)
            else socket
            for socket in sockets
        ]
    return row


def project_analysis(result: dict[str, Any]) -> dict[str, Any]:
    """把武器分析的结果投影成默认出口的形状（`analyze_payload` 调它）。"""
    inventory = result.get("inventory")
    projected_inventory = inventory
    if isinstance(inventory, dict):
        projected_inventory = {
            **{key: value for key, value in inventory.items() if key != "instances"},
            "instances": [_instance_row(i) for i in inventory.get("instances") or []],
            "options_hint": (
                '副本的"每个栏位能换成什么"不在默认响应里：用 '
                'weapon_assistant(intent="compare", item_instance_id=…)。'
            ),
        }
    return {
        **result,
        "sockets": [_socket_row(s) for s in result.get("sockets") or []],
        "inventory": projected_inventory,
    }


def compare_rows(comparison: dict[str, Any]) -> dict[str, Any]:
    """同名多副本的**行视图**（`compare` 不带 `item_instance_id` 时的默认出口）。

    真机 2026-09-25：豆包那边为了判"留哪把"，对 19 件邮政官武器**一把一次** `compare(item_instance_id=…)`
    （每次响应还得靠宿主压缩），中间还去 grep 落盘的工具结果文件 —— 慢且容易漏。
    实际上它要的东西一次就能给全：**每把一行 × 每个可切换栏的全部项 × 愿单命中**。

    只列**有得选**的栏（`options` 多于一项）——固定栏（框架/着色器）没有取舍价值；
    `equipped` 指出现在装着哪一颗，`recommended` 标愿单结论（PvE/PvP）。

    **每一行都带武器自己的 `item_hash` 与 `icon_url`**（2026-10-05 补，用户实拍：8 把
    「Better Devils」的行视图里 `icon_url` 出现 **0** 次 —— 连卡头都只能放色块）。
    两个值取自**同一个身份块**（`instances[].weapon`，即 `weapon_payload.weapon_block`
    按**这件副本自己的 hash** 查出来的），所以"同一 hash 出两种图"在形状上不可能：
    图不是在这里按 hash 重算的，而是照抄那条已经走 `manifest_lookup.get_icon_url()`
    的物品道结果（单一出处）。
    """
    weapon = comparison.get("weapon") if isinstance(comparison.get("weapon"), dict) else {}
    rows: list[dict[str, Any]] = []
    for instance in comparison.get("instances") or []:
        if not isinstance(instance, dict):
            continue
        identity = instance.get("weapon") if isinstance(instance.get("weapon"), dict) else {}
        block = identity.get("instance") if isinstance(identity.get("instance"), dict) else {}
        sockets: list[dict[str, Any]] = []
        # 每副本的**实例级**插槽在 `options`（`scope="instance"`，来自组件 310）；
        # `sockets` 是定义级"列"清单（不含这件能换成什么）。读错这个键就得到空行 ——
        # 真机 2026-09-25 第一版就踩了：7 个副本行里 `sockets` 全是 []。
        for socket in instance.get("options") or instance.get("sockets") or []:
            if not isinstance(socket, dict):
                continue
            options = [o for o in socket.get("options") or [] if isinstance(o, dict)]
            kind = str(socket.get("kind") or "")
            # **roll 定义栏一律照列**（框架/枪管/弹匣/特性），即使只有 1 项（固定）——
            # 真机 2026-09-26 踩过：只列"有得选"的栏，会让"特性栏固定"的枪看起来像
            # **没有特性栏**，于是被读成"半成品"（报告里的 P0-2 就是这么来的）。
            if len(options) <= 1 and kind not in _ROLL_COLUMN_KINDS:
                continue
            entry: dict[str, Any] = {
                "slot": socket.get("slot") or kind or "",
                "equipped": (socket.get("equipped") or {}).get("name") or "",
                "options": [option_row(o, with_effects=False) for o in options],
            }
            if len(options) <= 1:
                entry["fixed"] = True
            sockets.append(entry)
        row: dict[str, Any] = {
            "instance_id": instance.get("instance_id") or block.get("instance_id") or "",
            # 身份对：hash 与图同源，缺定义时 hash=0、图=空串（"没查到"不是"没有"）
            "item_hash": identity.get("item_hash") or 0,
            "icon_url": identity.get("icon_url") or "",
            "location": instance.get("location") or block.get("location") or "",
            "power": instance.get("power") or block.get("power"),
            "is_equipped": bool(instance.get("is_equipped") or block.get("is_equipped")),
            "locked": bool(block.get("locked")),
            "sockets": sockets,
        }
        score = block.get("god_roll_score")
        if score:
            row["god_roll_score"] = score
        rows.append(row)
    return {
        # 卡头那一行也要能放图（行视图以前整包 0 个 `icon_url`）；`icon_url` 恒给，
        # 空串 = 查不到定义，渲染侧画同尺寸占位块（缺值给空串，不编地址）
        "weapon": {
            key: weapon.get(key)
            for key in _WEAPON_ROW_KEYS
            if weapon.get(key) not in (None, "")
        }
        | {"item_hash": weapon.get("item_hash") or 0, "icon_url": weapon.get("icon_url") or ""},
        "instances": rows,
        "rows_note": (
            "这是**副本行**：每行一把，`sockets[]` 只列有得选的栏（`equipped` 是现在装着的那颗，"
            "`options` 是这一栏全部可切换项，`recommended` 是本地愿单结论 —— 判「留哪把」要看整栏，"
            "只看 `equipped` 会误判；每项只给名字与愿单结论，数值说明用 perk_description 按需查）。"
            "要看某一个副本的完整明细（含固定栏、可换件、评分）用 "
            'weapon_assistant(intent="compare", weapon_name=…, item_instance_id=…)。'
        ),
    }
