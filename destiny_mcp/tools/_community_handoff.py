"""社区模板 → 求解的**指路**：把它给的参数抬到 `next_actions`。

为什么单独一个模块：2026-10-06 真机踩到 —— `community_build` 的响应里
`solver_handoff.arguments` 本身就是"下一步该拿什么参数跑 `find`"（连 15 颗功能模组的
部位前缀都写好了，套装还**故意置 null**），但它埋在那份 **122 KB 响应的第 1111 行**，
而 `next_actions` 是**空的**。连写这个工具的人都没读到，于是照着社区模板自己拼参数、
白跑一轮 0 候选（还多传了它明确剔除的套装约束）。

**货在深处、指路又没有 = 指望模型把 122 KB 读完。** 所以这里把它抬到出口
（与 `summary` 那句"不可直接执行"同一条教训：`summary` 与 `next_actions` 是
唯一一定会被读到的地方 —— 2026-09-14 那次是调用方越过了 payload 里的
`execution_supported=false`，这次是漏了 `solver_handoff`）。
"""

from __future__ import annotations

from typing import Any


def community_handoff(selected: dict[str, Any] | None) -> tuple[list[str], str]:
    """返回 `(next_actions, 加在摘要后面的一句)`；模板没有 `solver_handoff` 时给 `([], "")`。"""
    # ⚠️ 真路径是 `validation.solver_handoff`（它是**校验**那一步产出的，不是模板自带的）。
    # 2026-10-06 真机探针抓到：第一版读的是顶层 —— 而我的假夹具也正好编成顶层，
    # 于是单测 + 注入全绿，出口却一句指路都没有。夹具换成真的 `validate_build(...)`
    # 输出才算数（同一天第二次栽在"我自己编的形状"上）。
    validation = (selected or {}).get("validation") or {}
    handoff = validation.get("solver_handoff") or (selected or {}).get("solver_handoff") or {}
    args = handoff.get("arguments") or {}
    if not args:
        return [], ""
    mods = args.get("functional_mods") or []
    return (
        [
            "照 `solver_handoff.arguments` 跑一次求解（社区模板不是可执行凭据）："
            f'intent="find"、character={args.get("character")!r}、'
            f'exotic_name={args.get("exotic_name")!r}'
            + (f"、functional_mods={len(mods)} 颗（原样照传那份清单）" if mods else "")
            + "；里面的套装是 null 属于**故意**（本库对不上的名字不硬塞）。",
            "要装备：用这次 find 签发的 execution_id 走 equip_build，确认后才写账号。",
        ],
        f"（可照抄 {len(mods)} 颗功能模组）" if mods else "",
    )
