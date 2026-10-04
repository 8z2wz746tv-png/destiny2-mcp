"""perk **选项行**的唯一形状（`{name, can_roll, stat_effects, recommended}` + `enhanced`）。

为什么单独一个模块：同一个形状被两条出口投影共用 —— `analyze` 的插槽行
（`weapon_analysis_projection._socket_row`）与 `compare` 的副本行（`compare_rows` 每栏的
`options[]`）。两处各写一份的话"留哪些键"会漂，而这两条路对模型是同一个问题
（"这一栏能换成什么"）。

两处口径的**唯一差别**是 `stat_effects`：只有 `analyze` 那条给（副本行视图里判断信号是
愿单结论，每项 75 B 的数值说明会把整包从 15 KB 撑到 30 KB；要看效果有 `perk_description`），
所以它是 `option_row(..., with_effects=False)` 的参数，不是两份实现。

原来这两段在 `weapon_analysis_projection` 里，那个文件贴着 184 行上限；搬出来是为了给
"副本行视图补 `icon_url`"腾地方（上限跟着收紧，没有抬高）。
"""

from __future__ import annotations

from typing import Any

#: 选项里要保留的键（其余丢掉：`plug_hash`/`enhanced_plug_hash` 是给跨入口比对用的，
#: 模型要按名字问详情有 `perk_description`）
OPTION_KEYS = ("name", "can_roll", "stat_effects", "recommended")


def option_row(option: dict[str, Any], *, with_effects: bool = True) -> dict[str, Any]:
    """选项 → 行。`with_effects=False` 时丢掉 `stat_effects`（见模块开头）。"""
    keys = OPTION_KEYS if with_effects else tuple(k for k in OPTION_KEYS if k != "stat_effects")
    row: dict[str, Any] = {
        key: option[key] for key in keys if option.get(key) not in (None, [], {})
    }
    if option.get("enhanced_plug_hash"):
        row["enhanced"] = True
    return row


def recommended(option: dict[str, Any]) -> bool:
    """这一项有没有本地愿单结论（PvE/PvP 任一即可）。"""
    verdict = option.get("recommended")
    if not isinstance(verdict, dict):
        return False
    wishlist = verdict.get("wishlist")
    if isinstance(wishlist, dict):
        return bool(wishlist.get("pve") or wishlist.get("pvp"))
    return bool(verdict)
