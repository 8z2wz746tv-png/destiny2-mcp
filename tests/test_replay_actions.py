"""`confirmation_required` 必须给**能原样回放**的调用（B 件事）。

实测（2026-10-09 全量审计 5407 次调用）：`confirmation_required` 出现 **176 次** —— 模型看得懂
"要确认"，却常把参数重写一遍（少传、换 intent），于是白跑一个来回（中位 ~40 秒）。修法是把
"同一组参数 + `confirmed=true`"直接拼成一条可照抄的调用放进 `next_actions[0]`。
"""

from __future__ import annotations

import pathlib
import re

from destiny_mcp.tools._responses import confirmation_required_response

_TOOLS = pathlib.Path(__file__).resolve().parent.parent / "destiny_mcp" / "tools"


def _next_actions(payload: dict) -> list:
    return payload["next_actions"]


def test_the_replay_call_is_first_and_complete() -> None:
    """回放动作排在最前、`tool` 对、参数一个不改、只补 `confirmed=true`。"""
    out = confirmation_required_response(
        "move", {"intent": "move", "item_name": "星狐座"}, tool="inventory_assistant",
        replay={"intent": "move", "item_name": "星狐座", "destination": "vault"},
    )
    first = _next_actions(out)[0]
    assert isinstance(first, dict), f"第一条必须是可回放的调用，实际：{first!r}"
    assert first["tool"] == "inventory_assistant"
    assert first["arguments"] == {
        "intent": "move", "item_name": "星狐座", "destination": "vault", "confirmed": True,
    }, first["arguments"]
    assert "不改" in first["label"] or "原样" in first["label"]


def test_without_a_replay_it_stays_a_plain_hint() -> None:
    """没给 `tool`/`replay` 时行为不变（旧调用方不受影响，也不会凭空造一条调用）。"""
    out = confirmation_required_response("move", {"intent": "move"})
    assert all(isinstance(a, str) for a in _next_actions(out)), _next_actions(out)


def test_every_confirmation_call_site_declares_its_tool() -> None:
    """静态扫描：谁调确认信封，谁就得给 `tool=` —— 否则那条回执没法照抄。

    这条是给**将来新加的写入入口**用的：漏了它不会报错，只会让模型继续白跑（正是这次要治的病）。
    """
    offenders: list[str] = []
    for path in sorted(_TOOLS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(confirmation_required_response|_confirmation_required)\(", text):
            line_no = text[: match.start()].count("\n") + 1
            line = text.splitlines()[line_no - 1]
            if line.lstrip().startswith("def "):
                continue          # 定义本身不算调用点
            # 只看**这次调用自己的前几行**：往后扫太多会读到下一次调用的 `tool=`，
            # 于是"漏接"永远扫不出来（2026-10-09 注入验证当场抓到这条太松）。
            window = text.splitlines()[line_no - 1: line_no + 12]
            if not any("tool=" in ln for ln in window):
                offenders.append(f"{path.name}:{line_no}")
    assert not offenders, (
        "这些确认入口没给 tool=（回执里就没有\"照抄就行\"的调用）：" + "、".join(offenders)
    )


def test_the_resolve_again_action_replays_the_original_conditions() -> None:
    """`stale` 后的回放必须是**原来那组条件** —— 不是 `intent="recommend"`。

    2026-10-09：这类失败在审计里 15 次，`next_actions` 给的都是
    `{"intent": "recommend", "character": "warlock"}` —— 换了词、还丢掉了金装/套装/属性目标，
    模型照做会求出**另一套配装**（用户要的那套被换掉了）。求解参数存在候选里，原样回带。
    """
    from types import SimpleNamespace

    from destiny_mcp.tools._replay_actions import resolve_again_action

    original = {
        "intent": "find", "character": "warlock", "exotic_name": "阿罕卡拉之颅",
        "set_bonus_name": "移民号陨落", "grenade_target": 100, "super_target": 150,
        "priority_stats": "grenade", "top_n": 1,
    }
    svc = {"build_svc": SimpleNamespace(
        get_build_candidate=lambda _player, _exec_id: {"success": True, "search_args": original},
    )}
    action = resolve_again_action(svc, "Tester#1234", SimpleNamespace(execution_id="exec-1"), "warlock")

    assert action["tool"] == "build_assistant"
    assert action["arguments"] == original, action["arguments"]
    assert "原来" in action["label"]


def test_the_resolve_again_action_falls_back_without_inventing_conditions() -> None:
    """候选里没存参数（老候选）→ 退回最朴素的一条，**不编**金装/目标。"""
    from types import SimpleNamespace

    from destiny_mcp.tools._replay_actions import resolve_again_action

    svc = {"build_svc": SimpleNamespace(
        get_build_candidate=lambda _player, _exec_id: {"success": False, "code": "unknown_execution_id"},
    )}
    action = resolve_again_action(svc, "Tester#1234", SimpleNamespace(execution_id="gone"), "warlock")
    assert action["arguments"] == {"intent": "find", "character": "warlock"}, action["arguments"]
