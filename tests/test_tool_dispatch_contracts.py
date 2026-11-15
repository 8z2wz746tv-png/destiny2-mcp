"""工具分派契约：写入意图只有一个来源，声明与分派都不能漂移。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from destiny_mcp.tools import _requests as R
from destiny_mcp.tools.assistants import (
    SELF_GUARDED_WRITE_INTENTS,
    _requires_confirmation,
)

TOOLS_DIR = Path(__file__).parents[1] / "destiny_mcp" / "tools"
# 分派层 = 主文件 + 抽出去的 *_branches 模块（武器/护甲/子职业神器都这么拆的）；
# 只扫主文件的话，一次"抽代码降体量"的重构会把声明着的 intent 判成没有分支。
DISPATCH_FILES = [TOOLS_DIR / "assistants.py", *sorted(TOOLS_DIR.glob("_*_branches.py"))]
# intent 取值的单一出处在 `_requests.py`：一组同义 intent 收进模块常量之后，分派里就只剩
# `intent in SOME_CONST` 这种写法 —— 常量也要能被认出来，否则"收进常量"这个正确做法
# 反而会被这条守门判红。
REQUESTS_FILE = TOOLS_DIR / "_requests.py"

INTENT_TYPES = (
    R.PlayerIntent,
    R.InventoryIntent,
    R.WeaponIntent,
    R.BuildIntent,
    R.LoadoutIntent,
    R.SubclassIntent,
    R.ActivityIntent,
    R.WorldIntent,
)


def _declared_intents() -> set[str]:
    return {value for annotation in INTENT_TYPES for value in get_args(annotation)}


def _string_sequence(node: ast.AST | None) -> set[str] | None:
    """全字符串的集合/元组/列表字面量 → 取值集合；有一个不是字符串就不认。"""
    if not isinstance(node, (ast.Set, ast.Tuple, ast.List)) or not node.elts:
        return None
    if not all(
        isinstance(element, ast.Constant) and isinstance(element.value, str)
        for element in node.elts
    ):
        return None
    return {element.value for element in node.elts}  # type: ignore[union-attr]


def _constant_groups() -> dict[str, set[str]]:
    """模块级"全字符串常量"的名字 → 取值（`_requests.py` 与分派文件）。"""
    groups: dict[str, set[str]] = {}
    for path in [REQUESTS_FILE, *DISPATCH_FILES]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                targets: list[ast.expr] = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                continue
            values = _string_sequence(node.value)
            if values is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    groups[target.id] = values
    return groups


def _dispatch_literals() -> set[str]:
    """分派文件里出现在集合、元组、比较或模块常量引用里的字符串字面量。"""
    found: set[str] = set()
    constants = _constant_groups()
    for path in DISPATCH_FILES:
        found |= _literals_in(path, constants)
    return found


def _literals_in(path: Path, constants: dict[str, set[str]]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = _string_sequence(node)
            if values is not None:
                found |= values
        elif isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    found.add(side.value)
                elif isinstance(side, ast.Name):
                    found |= constants.get(side.id, set())
    return found


def test_every_declared_intent_has_a_dispatch_branch() -> None:
    """声明了却没有分支的 intent，只会在运行时才返回 unsupported_intent。"""
    missing = sorted(_declared_intents() - _dispatch_literals())

    assert not missing, f"这些 intent 已声明但没有分派分支：{missing}"


def test_every_write_intent_is_a_declared_intent() -> None:
    undeclared = sorted(R.WRITE_INTENTS - _declared_intents())

    assert not undeclared, f"写入清单里有未声明的 intent：{undeclared}"


def test_no_write_intent_is_left_unguarded() -> None:
    """每个写入 intent 必须走通用确认，或明确登记为工具自校验。"""
    unguarded = sorted(
        intent
        for intent in R.WRITE_INTENTS
        if not _requires_confirmation(intent)
        and intent not in SELF_GUARDED_WRITE_INTENTS
    )

    assert not unguarded, f"这些写入 intent 没有任何确认路径：{unguarded}"
    assert SELF_GUARDED_WRITE_INTENTS <= R.WRITE_INTENTS


def test_generic_guard_does_not_claim_self_guarded_intents() -> None:
    """自校验的写入不能同时被通用入口认领，否则读代码的人会以为通用路径已经覆盖。"""
    assert not (_requires_confirmation("equip_build"))
    assert "equip_build" in SELF_GUARDED_WRITE_INTENTS
