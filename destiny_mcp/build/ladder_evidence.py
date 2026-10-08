"""无解阶梯的**口径**：`ceiling` 为什么空、`precision` 到底是哪一种。

判据只有一处：`tools/_armor_ladder` 拿到五档探针结果后往这里问一句"这次算哪种"。
纯口径（只看每档的 `ok` 字段），所以放 layer 2，不放贴着体量上限的 `tools/`。

## 三态（2026-10-06 之前只有两态）

`sampled` = 至少一档跑成了、`ceiling` 是实采；`no_solution` = **每一档都跑了、一档都没解**
（"试过，没有"）；`not_computed` = **至少一档没探成**（"没算"，看 `probe_failures`）。
仓库自己的规矩要求分开：**「没探成」≠「试过没有」**。真机证据与逐条断言在
`tests/test_ladder_evidence.py`，读法在 `docs/testing/TESTING_CORPUS.md`。
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

#: **执行前提先把候选砍光**时专用（`analysis.blocked_by` 非空）：这一档的 trials 也是
#: "每档都跑了、都没解"，但**原因不是属性** —— 照 `no_solution` 念就成了一句没算过的结论。
BLOCKED_NOTE = (
    "这次能用的件里有需要**先准备**的（见 `blocked_by`：腾一格 / 先顶下冲突的金装）。"
    "注意它们**已经算进**上面这些结论了（ADR-027 起求解不再剔件）——这条是装备前要做的事，"
    "不是「属性配不出来」，也不是「没算」。"
)


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


def verdict_block(*, covered: bool, truncated_by: str | None, rotated_ok: bool) -> dict:
    """`verdict` 那一块（口径，不是判据）。

    `covered=False`（没搜完）时 `satisfiable` 必须是 `None`：**不许把"没搜完"写成"不可行"**
    （no limit can create an infeasibility proof）。`rotated_ok` 是"换优先级顺序能出解"，
    与原始请求是不是无解是两件事。
    """
    return {
        "satisfiable": False if covered else None,
        "evidence": (
            "工具按原始优先级实测 0 候选（这张阶梯就是因此生成的）"
            if covered
            else "工具按原始优先级返回 0 候选，但**这次没搜完**"
                 f"（截断原因：{truncated_by or '未说明'}），所以不能断言不可行"
        ),
        "note": (
            "ceiling 是各次探测**逐项**取的最大值，不等于同一套护甲能同时达到；"
            "trials 里 ok=true 的档是**换了优先级顺序或放下目标**之后的解，原始请求一个字没改；"
            "ok=null 的档是**这次没探成**（带 reason），不能读成「试过、没有解」。"
        ),
        "solved_after_rotation": rotated_ok,
    }


def positive_single_stat(max_possible: dict | None) -> dict[str, int]:
    """`analyze` 的 `max_possible` → 只留 **> 0** 的项。

    **0 不是"这一项只能到 0"，是"这次没算出来"**（真机：带套装约束时六项全 0，而同一账号
    的单项上限本来是 200/142/200/195/180/185）。缺值不给 0（仓库红线），剔空了下面的 note 会说清。
    """
    return {
        str(stat): int(value)
        for stat, value in (max_possible or {}).items()
        if int(value) > 0
    }


def single_stat_note(single_stat: dict) -> str:
    """`single_stat_ceiling` 的读法；它为空时说的是"没算出来"，**不是 0**。"""
    if single_stat:
        return (
            "single_stat_ceiling 是「把点全堆在这一项上」的上限，**不是**同时能达到的值；"
            "ceiling 才是同一套约束下按优先级实采出来的。"
        )
    return (
        "single_stat_ceiling 这次**没算出来**（所以是空的，**不是 0**）："
        "analyze 在当前约束下一个组合都没采到。"
    )


def precision_note(precision: str, *, blocked: bool = False) -> str:
    """`precision` → `note`；`blocked=True` 时用执行前提那一段（`BLOCKED_NOTE`）。

    为什么 blocked 要单独一句话：有件要先准备时，`ceiling`/`shortfall` 说的仍然是**含那些件的
    全量结论**（ADR-027 起不再剔件），但调用方必须知道"装备前还得先做一件事" ——
    照 `no_solution` 念会把"要先准备"读成"这些目标同时满足不了"。

    认不出的 `precision` 退回 `no_solution` 那句（含 `"exact"` 这类历史调用）。
    """
    if blocked:
        return BLOCKED_NOTE
    return PRECISION_NOTES.get(precision, PRECISION_NOTES["no_solution"])
