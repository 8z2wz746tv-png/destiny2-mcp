"""hash 归一只有一份写法：`utils/hash_utils.hash_variants`。

"同一个 hash 有两套值域（有符号/无符号）"这个病 2026-10 咬了三次 —— DIM 愿望单查表、
配装规模闸门的指定金装、碎片配置 —— 三次的形状都一样：**某处自己写了一份归一**
（`hashes.update({h, to_signed(h), to_unsigned(h)})` 或 `set.add(to_signed(h))` +
`set.add(to_unsigned(h))`）。所以归一收在 `hash_variants` 一处，这里钉两件事：
它本身的契约，以及**没有人再抄一份**。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from destiny_mcp.utils.hash_utils import hash_variants

SOURCE_ROOT = Path(__file__).parents[1] / "destiny_mcp"
HASH_UTILS = SOURCE_ROOT / "utils" / "hash_utils.py"  # 归一唯一出处：扫描跳过它自己

# 真机那对值（`保护琢面`）：同一个碎片，两种写法差 2^32
PROTECTION_SIGNED = -1668045176
PROTECTION_UNSIGNED = 2626922120

# 历史上被抄过两次的形态（现在应当被下面的扫描抓住）
_OLD_SOLVER_FORM = """
def f(constraints):
    exotic_hashes = set()
    for h in constraints.exotic_hashes:
        exotic_hashes.add(h)
        exotic_hashes.add(to_signed(h))
        exotic_hashes.add(to_unsigned(h))
    return exotic_hashes
"""
_OLD_CONSTRAINTS_FORM = """
def f(constraints):
    hashes = set()
    for item_hash in constraints.exotic_hashes:
        hashes.update({item_hash, to_signed(item_hash), to_unsigned(item_hash)})
    return hashes
"""


def test_hash_variants_covers_both_writeups() -> None:
    """一颗碎片的两种写法是同一颗：两种写法喂进来，得到同一个集合。"""
    assert hash_variants(PROTECTION_SIGNED) == {PROTECTION_SIGNED, PROTECTION_UNSIGNED}
    assert hash_variants(PROTECTION_UNSIGNED) == {PROTECTION_SIGNED, PROTECTION_UNSIGNED}
    assert hash_variants(None, 124726498) == {124726498}, (
        "None 是「没指定」，跳过即可（可选字段常这么传）"
    )


def _variant_sets_in(source: str) -> list[tuple[int, str]]:
    """找出"自己又归一了一份"的位置：同一个集合里同时放了两种写法。

    只认两种历史形态 —— 集合/列表字面量里两种写法并存，或往同一个容器 `.add()` 两种写法。
    **一个函数里一正一反各转一次不算**（`subclass_service.modify_subclass` 就是：查分类
    用有符号、写接口用无符号，那是两件事，不是归一）。
    """
    hits: list[tuple[int, str]] = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                written = _both_writeups(
                    element for element in node.elts if isinstance(element, ast.Call)
                )
                if written:
                    hits.append((node.lineno, f"{function.name} 的字面量里两种写法都有（{written}）"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "add" and node.args:
                    # `.add()` 要按容器名分组：往同一个集合里两种写法才算抄了一份。
                    target = ast.unparse(node.func.value)
                    written = _both_writeups(
                        call
                        for other in ast.walk(function)
                        if isinstance(other, ast.Call)
                        and isinstance(other.func, ast.Attribute)
                        and other.func.attr == "add"
                        and other.args
                        and ast.unparse(other.func.value) == target
                        for call in other.args
                        if isinstance(call, ast.Call)
                    )
                    if written:
                        hits.append((node.lineno, f"{function.name} 的 {target}.add 两种写法都有（{written}）"))
    return hits


def _both_writeups(calls) -> str:
    """这些调用里既有 `to_signed(x)` 又有 `to_unsigned(x)`（同一个 x）时，返回那个 x。"""
    converted: dict[str, set[str]] = {"to_signed": set(), "to_unsigned": set()}
    for call in calls:
        if not (isinstance(call.func, ast.Name) and call.args):
            continue
        if call.func.id in converted:
            converted[call.func.id].add(ast.unparse(call.args[0]))
    shared = converted["to_signed"] & converted["to_unsigned"]
    return ", ".join(sorted(shared))


@pytest.mark.parametrize(
    "source", [_OLD_SOLVER_FORM, _OLD_CONSTRAINTS_FORM], ids=["add 三连", "集合字面量"]
)
def test_扫描器抓得住历史那两种抄法(source: str) -> None:
    """扫描自己也要有牙：这两种形态当年都真出现过（solver / constraints-farm_target）。"""
    assert _variant_sets_in(source), "扫描器抓不到历史上的形态，等于没有闸"


def test_归一逻辑没有再被抄一份() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path == HASH_UTILS:
            continue
        offenders.extend(
            f"{path.relative_to(SOURCE_ROOT.parent)}:{line} {what}"
            for line, what in _variant_sets_in(path.read_text(encoding="utf-8"))
        )

    assert not offenders, (
        "又有人自己写了一份 hash 归一：有符号/无符号要比较就调 "
        f"`utils/hash_utils.hash_variants`（两套值域的坑见那里的说明）。{offenders}"
    )
