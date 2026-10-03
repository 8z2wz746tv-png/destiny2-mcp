"""调谐写入的判据：一颗调谐能不能装到某件护甲上。

**为什么要单独一个模块**：这条判据被**两条写入路径**共用 ——
`services/armor_mod_service.py`（`equip_mod` 的确认阶段）与
`services/loadout_mod_sockets.py`（`equip_build` 的模组预检）。2026-09-28 真机事故就是
"两边各写一遍、只改了一边"：确认阶段按组件 310 判（对），预检按组件 207 判（错），
于是 `equip_build` 把 3 颗调谐全判成"游戏里同样装不上"、**连上游都没试**。
判据收在这里以后，谁都不许再抄第二份。

放 `build/` 而不是 `services/`：它是纯判据（不碰账号、不碰网络），与 `build/models.py`
的 `tuning_is_allowed` / `tuning_options_from_reusable` 同一域，且 `services/` 依赖只能向下。

口径来源见 `docs/adr/014-tuning-writes-need-ownership.md` 与
`docs/plans/TUNING_WRITE_PLAN.md`。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .models import tuning_is_allowed, tuning_options_from_reusable

#: 调谐插件的 plug 类别（`core.gear_systems.armor_tiering.plugs.tuning.mods`）。
TUNING_CATEGORY_HASH = 3481777685


def plug_is_tuning(plug_hash: int, category_of: Callable[[int], int]) -> bool:
    """这颗插件是不是**调谐**。

    按 plug 类别判，不按名字 —— 名字会被本地化与强化版后缀影响。
    """
    return category_of(plug_hash) == TUNING_CATEGORY_HASH


def tuning_write_blocker(
    item_instance_id: str,
    plug_hash: int,
    reusable_plugs: Mapping[str, Any] | None,
    category_of: Callable[[int], int],
) -> str:
    """调谐能不能写：返回空串 = 能写，非空 = 写不了的原因（中文）。

    判据只有一条 —— 那颗在不在**这件护甲允许的清单**里（组件 310 `ItemReusablePlugs`）。
    `unlock_state`（角色级 plug set，组件 207/`characterPlugSets`）在调谐槽上**不可信**：
    实测连正装着的那颗调谐都不在那份清单里，而一颗被判"不可插入"的调谐写入上游照样接受
    （2026-09-22 真机：至高碎片槽 11 `+武器 / -超能` → `ErrorCode=1`，回读插槽与六维都对）。

    读不到清单（没请求 310 / 这件没有调谐槽）= **不拦** —— 缺数据 ≠ 不许，交给上游说话。
    这一条正是那次事故的要害：`equip_build` 的执行路径只请求 `INVENTORY_SOCKETS`
    （305 那条），手上根本没有 310，却拿 207 把能装的调谐判成装不上。

    `tuning_is_allowed` 两边过 `to_unsigned`：调用方给的 hash 多来自 `manifest.search`
    （有符号），310 清单来自 profile（无符号）—— 裸 `in` 会把清单里有的那颗判成装不上。
    """
    allowed = tuning_options_from_reusable(
        ((reusable_plugs or {}).get(item_instance_id) or {}),
        category_of,
        TUNING_CATEGORY_HASH,
    )
    if not allowed or tuning_is_allowed(allowed, plug_hash):
        return ""
    return (
        "这颗调谐**不在这件护甲允许的调谐里**（组件 310 的清单里没有它）："
        "上游会回 1675「这颗装不到这件上」，不是「你没材料」。"
        "换一件能装它的护甲，或者换成这件清单里已有的那几颗。"
    )
