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

ASSISTANTS = Path(__file__).parents[1] / "destiny_mcp" / "tools" / "assistants.py"

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


def _dispatch_literals() -> set[str]:
    """assistants.py 里出现在集合、元组或比较里的字符串字面量。"""
    tree = ast.parse(ASSISTANTS.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = [
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if values and len(values) == len(node.elts):
                found.update(values)
        elif isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    found.add(side.value)
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
