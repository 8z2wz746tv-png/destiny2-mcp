"""结论路径不许「静默降级」：拿不到证据可以退回更弱的说法，但必须留下痕迹。

背景：0.1.13 修掉的那条 P1 就是这一类——`analyze` 在没验证过的情况下断言"配不出来"。
同一片区域还有三处会悄悄换口径/换结论：

1. `_armor_ladder._tuning_evidence` 拿不到调谐额度 → 以前直接 `return None`，
   载荷里只剩一句"没有额度数据"，没人知道为什么（现在随载荷给
   `tuning_unavailable_reason`）；
2. 阶梯探测抛异常 → 以前 `continue` 会让 `for...else` 把它记成"试过、没有解"，
   等于**把没测过的档说成否定结论**（现在记 `ok: null` + `not_probed` + `reason`）；
3. `_param_contracts.declared_intents` 读不出注解 → 参数归属校验少一层，
   以前完全静默（现在 ERROR 日志 + 响应里加 warning）。

这个文件做两件事：给上面三条加行为断言，再用一条扫描规则把"结论路径"的
`except` 都是留痕的钉死。新增结论性模块（诊断、上限、阶梯这类）请加进 `_CONCLUSION_MODULES`。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from destiny_mcp.build.models import BuildRequest
from destiny_mcp.tools import _armor_ladder as ladder
from destiny_mcp.tools import _param_contracts as contracts
from destiny_mcp.tools import assistants

SOURCE_ROOT = Path(__file__).resolve().parents[1]

# 出结论的模块：这里的 except 要么重抛，要么把失败写进返回值/日志
_CONCLUSION_MODULES = (
    "destiny_mcp/tools/_armor_ladder.py",
)


def _request(**kwargs: Any) -> BuildRequest:
    """用真实的 BuildRequest（阶梯会 model_copy 它，替身容易缺方法）。"""
    return BuildRequest(character_class="hunter", **kwargs)


class _FindingService:
    """`find_build` 按脚本行为：抛异常 / 返回结果 / 返回空。"""

    def __init__(self, *, raises: Exception | None = None, results: list | None = None) -> None:
        self._raises = raises
        self._results = results or []
        self.calls = 0

    async def find_build(self, player_name: str, request: Any):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._results

    async def probe_find_build(self, player_name: str, request: Any):
        """阶梯走这个入口（只读并发档）；替身接到同一条脚本上。"""
        return await self.find_build(player_name, request)


class _AnalyzeService:
    def __init__(self) -> None:
        self.max_possible = {"weapons": 100}
        self.reason = "组合规模太大"
        self.precision = "not_computed"

    async def analyze_build(self, player_name: str, request: Any):
        return self


@pytest.mark.asyncio
async def test_failed_probe_is_marked_not_probed_instead_of_no_solution() -> None:
    """探测抛异常 → `ok: null` + 原因；绝不能被记成「试过、没有解」。"""
    service = {
        "build_svc": _AnalyzeService(),
        "inventory_svc": None,
        "manifest": None,
    }
    service["build_svc"] = type(
        "Svc",
        (),
        {
            "analyze_build": _AnalyzeService().analyze_build,
            "find_build": _FindingService(raises=RuntimeError("上游 503")).find_build,
            "probe_find_build": _FindingService(raises=RuntimeError("上游 503")).probe_find_build,
        },
    )()

    table = await ladder.no_solution_ladder(service, "p", _request())

    not_probed = [trial for trial in table["trials"] if trial.get("ok") is None]
    assert not_probed, "探测全失败时必须有 ok=null 的档"
    assert all(trial.get("not_probed") for trial in not_probed)
    assert all("503" in trial.get("reason", "") for trial in not_probed)
    assert not any(trial.get("ok") is False for trial in table["trials"]), (
        "没探成的档不许记成 ok=false（那是「试过没有解」的意思）"
    )
    assert table["probe_failures"], "要在顶层列出没探成的档"
    assert "没探成" in table["verdict"]["note"]


@pytest.mark.asyncio
async def test_unavailable_tuning_evidence_carries_the_reason() -> None:
    """拿不到调谐额度 → 载荷里带原因，而不是只留一句「没有额度数据」。"""
    class _BrokenInventory:
        async def get_armor_snapshot(self, player_name: str, character: str):
            raise RuntimeError("角色名不对")

    service = {
        "build_svc": type(
            "Svc", (), {"analyze_build": _AnalyzeService().analyze_build,
                        "find_build": _FindingService(results=[]).find_build},
        )(),
        "inventory_svc": _BrokenInventory(),
        "manifest": object(),
    }

    table = await ladder.no_solution_ladder(service, "p", _request())

    assert table["tuning_attempted"] is False
    reason = table["tuning_unavailable_reason"]
    assert reason and "角色名不对" in reason
    assert "原因" in table["tuning_first_note"] or not table["tuning_first"]


def test_all_eight_tools_have_readable_intent_lists() -> None:
    """名单读不出来就少一层参数校验：这里钉住八个工具都读得出来。"""
    tools = (
        "player_assistant", "inventory_assistant", "weapon_assistant", "build_assistant",
        "loadout_assistant", "subclass_assistant", "activity_assistant", "world_assistant",
    )
    empty = [name for name in tools if not contracts.declared_intents(getattr(assistants, name))]

    assert not empty, f"这些工具读不出 intent 名单（参数校验会降级）：{empty}"


def test_conclusion_modules_never_swallow_an_error_silently() -> None:
    """结论路径的每个 except 都必须重抛或留痕（append/赋值/日志）。"""
    offenders: list[str] = []
    for relative in _CONCLUSION_MODULES:
        path = SOURCE_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            keeps_trace = (
                "raise" in body
                or ".append(" in body
                or "logger." in body
                or "reason" in body
                or "warning" in body
            )
            if not keeps_trace:
                offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "这些 except 吞掉了失败却没留痕（结论路径必须能说清「为什么退回了更弱的说法」）：\n  "
        + "\n  ".join(offenders)
    )
