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

#: 一栏里没有任何愿单结论时，退回展示前几个（够看清"这栏长什么样"，不假装池子很小）
OPTION_SAMPLE = 6

#: 选项里要保留的键（其余丢掉：`plug_hash`/`enhanced_plug_hash` 是给跨入口比对用的，
#: 模型要按名字问详情有 `perk_description`）
_OPTION_KEYS = ("name", "can_roll", "stat_effects", "recommended")


def _option_row(option: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        key: option[key] for key in _OPTION_KEYS if option.get(key) not in (None, [], {})
    }
    if option.get("enhanced_plug_hash"):
        row["enhanced"] = True
    return row


def _recommended(option: dict[str, Any]) -> bool:
    verdict = option.get("recommended")
    if not isinstance(verdict, dict):
        return False
    wishlist = verdict.get("wishlist")
    if isinstance(wishlist, dict):
        return bool(wishlist.get("pve") or wishlist.get("pvp"))
    return bool(verdict)


def _socket_row(socket: dict[str, Any]) -> dict[str, Any]:
    options = [o for o in socket.get("options") or [] if isinstance(o, dict)]
    picked = [o for o in options if _recommended(o)]
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
    row["recommended_count"] = len([o for o in options if _recommended(o)])
    row["options"] = [_option_row(o) for o in picked]
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
