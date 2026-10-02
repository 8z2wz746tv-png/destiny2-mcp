"""插入条件（上游 1676）的话术：候选条件与**已被账号事实证伪**的条件分开写。

上游回 1676 `DestinyFailedPlugInsertionRules` 时**不说是哪一条没过**（错误体里
`message_data` 是空的），手上只有 Manifest 的 `plug.insertionRules[].failureMessage` ——
那是这颗模组的**静态门槛清单**，不是"你没过的那条"。以前把清单念成原因
（「插入条件没满足（1676）：需要守护者等级3」），于是用户**早就满足的那条**成了失败理由：
2026-10 真机，守护者等级 11 的账号读成"我等级不够"，而那颗模组只要 3 级。
「需要守护者等级N」这类条件与账号等级（组件 100）比一比，`N <= 等级` 的排除出候选；
比不了（没读到等级）就如实说判不了 —— "没读到"既不许当成"条件成立"，也不许当成"不成立"。
抬头分两种：上游**真回过** 1676 用 `insertion_rule_blocker_text`；预检（`equip_mod` 的方案、
`equip_build` 的模组预检）还没往上写过，用 `insertion_rule_preflight_text` —— 那种场合说
"上游拒绝了"等于替上游编回执。三段判断本身两处共用（`insertion_rule_candidates_text`）。
"""

from __future__ import annotations

import re

#: 「需要守护者等级N」的识别式（`N` 后可能还跟着「：商人挑战」这类说明，不用管）。**只认带
#: 「守护者」三个字的写法**：实测另有 717 条写作 `需要等级10`（例：竞争黑沙）、还有
#: `需要武器等级N` —— 都是**另一套等级**，拿守护者等级判它们会把真门槛说成"你已满足"。
_GUARDIAN_RANK_CONDITION = re.compile(r"需要守护者等级\s*(\d+)")


def guardian_rank_from_profile(profile: dict | None) -> int | None:
    """账号的守护者等级（`Response.profile.data.currentGuardianRank`，组件 100）。

    读不到给 `None`，**不编 0**：官方字段说明写着它「starts at rank 1」，0 是这个字段没填；
    当成"满足"是编结论，当成"不满足"同样是。
    """
    data = ((profile or {}).get("profile") or {}).get("data") or {}
    rank = data.get("currentGuardianRank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        return None
    return rank


def guardian_rank_requirement(condition: str) -> int | None:
    """条件文案要的守护者等级；不是「需要守护者等级N」这类写法就给 `None`（不判断）。"""
    match = _GUARDIAN_RANK_CONDITION.search(condition or "")
    return int(match.group(1)) if match else None


def insertion_rule_candidates_text(conditions: list[str], profile: dict | None) -> str:
    """候选／已排除／判不了三段（**不含 1676 抬头**）：预检与上游回绝共用这一份判断。
    `profile` 是这次读到的账号档案（组件 100）；没有它就必须说"判不了"。
    """
    if not conditions:
        return "Manifest 里这颗模组没给条件文本（insertionRules 是空的），列不出候选。"
    guardian_rank = guardian_rank_from_profile(profile)
    candidates: list[str] = []
    excluded: list[str] = []
    unjudged: list[str] = []
    for condition in conditions:
        required = guardian_rank_requirement(condition)
        if required is None or guardian_rank is None:
            # 不是守护者等级那类写法（`需要等级10` 是另一套等级）→ 原样留作候选；
            # 是那类却没读到等级 → 也留作候选，另记一笔"判不了"。
            candidates.append(condition)
            if required is not None:
                unjudged.append(condition)
        elif required <= guardian_rank:
            excluded.append(f"{condition}（你的守护者等级 {guardian_rank} 已满足这条，它不是本次原因）")
        else:
            # 门槛比等级高：可能没过，但上游没说哪一条 —— 只留候选，不替它指认。
            candidates.append(condition)
    if candidates:
        head = "候选条件（Manifest 给的静态门槛清单，不是上游指认的原因）：" + "；".join(candidates) + "。"
    else:
        head = "候选条件：Manifest 给的那几条都被账号事实排除了，没有剩下的候选。"
    tail = "已排除：" + "；".join(excluded) + "。" if excluded else ""
    if unjudged:
        tail += "没读到守护者等级，无法判断这几条是否成立：" + "；".join(unjudged) + "。"
    return head + tail


def insertion_rule_preflight_text(conditions: list[str], profile: dict | None) -> str:
    """预检（还没往上写过）的措辞：与 1676 那条**分开**，别让人以为已经试过了。"""
    return (
        "不在 Bungie 给这一位角色的可插入清单里（组件 207），游戏里同样装不上；"
        "这次是**写入之前**的判断、还没往上写过，真写上去上游会回 1676（插入条件没满足）。"
        + insertion_rule_candidates_text(conditions, profile)
    )


def insertion_rule_blocker_text(conditions: list[str], profile: dict | None) -> str:
    """上游回了 1676 时的中文说明：错误码与上游原文照旧留着，调用方一眼看得出是上游挡的。"""
    return (
        "插入条件没满足（1676）：上游原文 DestinyFailedPlugInsertionRules，"
        "但它没说是哪一条没过（错误体里 message_data 是空的）。"
        + insertion_rule_candidates_text(conditions, profile)
    )


__all__ = [
    "guardian_rank_from_profile", "guardian_rank_requirement", "insertion_rule_blocker_text",
    "insertion_rule_candidates_text", "insertion_rule_preflight_text",
]
