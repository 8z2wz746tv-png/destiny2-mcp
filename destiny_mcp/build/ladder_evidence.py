"""无解阶梯的**口径**：`ceiling` 为什么空、`precision` 到底是哪一种。

判据只有一处：`tools/_armor_ladder` 拿到五档探针结果后往这里问一句"这次算哪种"。
纯口径（只看每档的 `ok` 字段），所以放 layer 2，不放贴着体量上限的 `tools/`。

## 三态（2026-10-06 之前只有两态）

| 值 | 含义 | 调用方该做什么 |
| --- | --- | --- |
| `sampled` | 至少一档跑成了，`ceiling` 是实采 | 正常读 `ceiling`/`shortfall` |
| `no_solution` | **每一档都跑了**、一档都没解 —— "试过，没有" | 别报成"没算"；谈降目标/换件/反推待刷 |
| `not_computed` | **至少一档没探成**（`ok: None` + `not_probed` + `reason`）—— "没算" | 别报成"无解"；去看 `probe_failures` 里的原因 |

以前后两种**共用 `not_computed`**，于是"算了但没有解"被读成"根本没算"。
真机 2026-10-06 用户报的"同一次无解、口径不稳定"（不带套装约束给了 ceiling、
带套装约束变成空 + `not_computed`）就是这个：前者的探针跑成了、后者每档都失败，
**两次都在如实报告，只是标签分不开**。仓库自己的规矩也要求分开：
**「没探成」≠「试过没有」**。
"""

from __future__ import annotations

PRECISION_NOTES: dict[str, str] = {
    "sampled": (
        "ceiling = 同一套约束下、按某种优先级**同时**能达到的值（实采，逐项取最大）；"
        "它不等于「游戏里最多能到多少」，也不等于单项上限。降级只能由用户确认后执行。"
    ),
    "no_solution": (
        "每一档都实解过、一档都没解：这五件护甲在**同一套约束**下同时满足不了这些目标 ——"
        "这是「试过、没有」，不是「没算」。ceiling 留空而不是编 0。"
    ),
    "not_computed": (
        "这次**没探成**：至少有一档没跑完（原因见 `trials[].not_probed` 与 `probe_failures`），"
        "所以这既不是「无解」也不是「有解」。ceiling 留空而不是编 0。"
    ),
}

#: `precision` 的三态。`sampled` 之外的两种**不能合并**（见模块 docstring）。
PRECISIONS: tuple[str, ...] = ("sampled", "no_solution", "not_computed")


def classify_precision(trials: list[dict]) -> str:
    """五档探针结果 → `precision` 三态之一。

    - 有任意一档 `ok is True` → `sampled`（有样本，ceiling 是实采）；
    - **每一档都跑过**（`ok is False`）→ `no_solution`（试过，没有）；
    - 其余（含一档都没跑、或至少一档 `ok is None`）→ `not_computed`（没算）。

    没有 `trials` 时给 `not_computed`：什么都没探过，不能说"试过没有"。
    """
    if any(trial.get("ok") is True for trial in trials):
        return "sampled"
    if trials and all(trial.get("ok") is False for trial in trials):
        return "no_solution"
    return "not_computed"


def precision_note(precision: str) -> str:
    """`precision` → `note`。认不出的取值退回 `no_solution` 那句（含 `"exact"` 这类历史调用）。"""
    return PRECISION_NOTES.get(precision, PRECISION_NOTES["no_solution"])
