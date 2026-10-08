"""工具描述必须写全**能力与用法**（`tools/_tool_usage.py`）。

背景（2026-10-06 盘点）：130 个 intent 里 **94 个在工具 docstring 里连名字都没有** ——
模型只看见 schema 里那串枚举名，不知道每个是干什么的。用法只写在 skill 里，
而 **skill 可能没被加载**（MCP 只保证 advertise 一个 URL）。于是"工具功能用不全"。

这里钉四件事：
1. 八个工具一个不漏，且没有多余的键；
2. **本工具每一个 intent 都必须出现在它的用法文本里**（缺一个 = 那个能力只存在于 skill 里）；
3. 每处出现所在的行要有**用途**（不是把名字堆成一行）；
4. **接线**：描述真的挂到 `@mcp.tool(description=…)` 上了（写好了没接上去 = 白写，
   2026-10-06 的 `next_actions` 就是这种漏法）。
"""

from __future__ import annotations

import ast
import pathlib
import typing

import pytest

from destiny_mcp.tools import _requests as requests_module
from destiny_mcp.tools import assistants as _assistants  # noqa: F401  注册表靠 import 填充（import 才有定义）
from destiny_mcp.tools._registry import mcp
from destiny_mcp.tools._tool_usage import TOOL_USAGE

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = {
    "player_assistant": "PlayerIntent",
    "inventory_assistant": "InventoryIntent",
    "weapon_assistant": "WeaponIntent",
    "build_assistant": "BuildIntent",
    "loadout_assistant": "LoadoutIntent",
    "subclass_assistant": "SubclassIntent",
    "activity_assistant": "ActivityIntent",
    "world_assistant": "WorldIntent",
}


def _english_intents(intent_type: str) -> list[str]:
    """只要英文规范名 —— 中文是别名，不必逐条写进用法（`intent` 参数说明里有）。"""
    return [name for name in typing.get_args(getattr(requests_module, intent_type)) if name.isascii()]


def test_every_tool_has_a_usage_block() -> None:
    assert set(TOOL_USAGE) == set(TOOLS), "多出来或漏掉的工具键"


@pytest.mark.parametrize(("tool", "intent_type"), sorted(TOOLS.items()))
def test_every_intent_of_the_tool_is_documented_with_a_purpose(
    tool: str, intent_type: str
) -> None:
    text = TOOL_USAGE[tool]
    lines = text.splitlines()
    missing = []
    for intent in _english_intents(intent_type):
        hit = [line for line in lines if f"`{intent}`" in line]
        if not hit:
            missing.append(intent)
            continue
        # 用途：那一行得有点长度、并且带中文（不是把名字堆成一行）
        assert any(len(line) >= 20 and any("\u4e00" <= ch <= "\u9fff" for ch in line) for line in hit), (
            f"{tool} 的 `{intent}` 只列了名字，没写它干什么用"
        )
    assert not missing, f"{tool} 的用法里没提到这些 intent（能力不许只存在于 skill 里）：{missing}"


def test_the_descriptions_are_actually_wired_onto_the_tools() -> None:
    """**接线级**：写了用法不等于挂上去了 —— 从注册表里读回来核对。"""
    wired = {
        definition.function.__name__: definition.kwargs.get("description")
        for definition in mcp._definitions  # noqa: SLF001 - 测试就是要看注册表
    }
    for tool, usage in TOOL_USAGE.items():
        assert tool in wired, f"{tool} 没注册"
        assert wired[tool] == usage, (
            f"{tool} 的描述没挂上（或与 _tool_usage 不一致）—— 写好了不接线等于没写"
        )


def test_the_usage_text_is_not_the_same_string_for_every_tool() -> None:
    """防"复制粘贴一整块"：八个工具的文本必须互不相同，且各自的 intent 覆盖率非零。"""
    assert len(set(TOOL_USAGE.values())) == len(TOOL_USAGE)
    for tool, intent_type in TOOLS.items():
        assert len(_english_intents(intent_type)) > 0


def test_the_module_docstring_of_each_tool_does_not_grow_an_intent_index() -> None:
    """能力清单**只有一处**（`_tool_usage`）：`assistants.py` 的 docstring 里不许再抄一份。

    一个口径只写一处 —— 两份必然漂（本仓的老账）。这里只做**反向**检查：
    docstring 里不出现成串的 intent 名。
    """
    tree = ast.parse((ROOT / "destiny_mcp/tools/assistants.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in TOOLS:
            doc = ast.get_docstring(node) or ""
            listed = [
                intent
                for intent in _english_intents(TOOLS[node.name])
                if f"`{intent}`" in doc
            ]
            assert len(listed) < 3, (
                f"{node.name} 的 docstring 里又抄了一份 intent 清单（{listed}）—— "
                "用法只写在 `_tool_usage.py`"
            )
