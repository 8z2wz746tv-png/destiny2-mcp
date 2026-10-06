"""hash 的两套值域：跨域比较的静态守门。

**为什么有这个文件（这笔账要记清楚，不然下次还会有人问"为什么不能直接比"）：**

同一个编号在仓库里有两套写法，而且**谁给哪一套全看来源**：

- **有符号**（Manifest 的 `id` 列、`manifest.search()` 返回的 `itemHash`）——
  sqlite 里存的是 int32，`黎明副歌` = `-1978053128`；
- **无符号**（物品定义 JSON 体内的 `plugItemHash` / `singleInitialItemHash` /
  `plugHash`，以及 Bungie API 的 profile 组件）——同一个编号 = `2316914168`。

漏归一的后果**不是报错，是"永远比不中"**：静默失效。真机上这个病 2026-10 咬了三次：

1. **DIM 愿望单**用无符号做键、Manifest 给有符号 → god roll 标记整表失效；
2. **配装规模闸门的"指定金装"收窄**：拿账号快照的无符号去比 `manifest.search()` 的有符号
   → 恒 False → 该部位退化成全量件数 → 背包大的角色任何带金装的 `find` 都撞组合上限，
   而闸门给的第一条建议恰恰是"指定一件金装"（建议了也不生效）；
3. **碎片配置**（`services/build_fragments.py`）：请求里的碎片 hash 来自 `manifest.search()`
   （有符号），插槽的 `singleInitialItemHash` 在物品 JSON 体内（无符号）→ 真机 5 颗里
   4 颗被判"与插槽不兼容"，能过的恰好是 `124726498` 这种 < 2^31 的小 hash。

**判据（三段，都能解释，也都有明确的漏检边界）：**

1. **采集**：只挑"两边都像 hash"的比较（`==` / `!=` / `in` / `not in`）——名字以
   `hash`/`hashes` 结尾、或 `d.get("…Hash")` / `d["…Hash"]` 这种字段读、或
   `to_signed`/`to_unsigned`/`hash_variants` 的调用结果。不做数据流全图，只看这四类比较。
2. **定域**：对每个操作数算一个"可能落在哪些表示里"的集合（`s` 有符号 / `u` 无符号）：
   - 字面量与模块/类级常量表**静态求值**（含 `{h: i for i, h in enumerate(TABLE)}`
     这种推导式）；取值全在 `[0, 2^31)` 的算"两套写法一模一样"（`bit_same`，**不可能**出错），
     含 `> 2^31` 的算无符号，含负数的算有符号；
   - `manifest.search()` / `get_item_info()` 的结果行 → `itemHash` 是**有符号**（库里的 id 列），
     同一行的其它字段按原始 JSON 算**无符号** —— 这一行里的两种写法正是最阴的地方；
   - `get_item_definition()` / `get_definition()` / 任何 `d.get("…Hash")` → **无符号**；
   - `to_signed` / `to_unsigned` → 各自那一套；同名多次赋值/多个调用点取并集；
   - 形参按**调用点**回填（方法要跳过 `self`），因此 `f(x)` 这条链会一路追到实参。
3. **判定**：两边表示集合**无交集** = `mismatch`（判红）；一边判不准、另一边**只可能是单一表示**
   = `unresolved`（可疑，进 `LEDGER` 逐条写理由）；一边判不准、另一边两套都有 = 放过。
   注意 `==` / `!=` 比的是**一个**值：标量"两条赋值路径各给一套写法"仍算判不准；
   只有容器（`in` 的右边）同时装着两套写法才算真的安全。"值 < 2^31"（`bit_same`）
   不算判不准 —— 这种编号两套写法本来就一模一样，**不可能**出错。

**白名单不是"让自己变绿"的开关**：`LEDGER` 是**冻结的复核台账**——新增的可疑点会直接判红，
台账里过期（代码已经归一/删掉）的条目也会判红，逼着条目跟着代码走。**真问题不许塞进
台账当"已复核"**：确实存在、只是这一轮不修的，写在下面的 `KNOWN_UNFIXED` 里，理由里
明说"这是 bug"，并让测试在它还出现时**点名但不判红**（修好之后会变成过期条目，测试判红
提醒把它删掉）。

**注入验证（仓库规矩：守门必须咬过一次）**：`test_guard_bites_on_the_fragment_bug_shape`
把**修复前**的碎片代码当作一个额外源文件塞进索引，断言它被判 `mismatch`；同一测试再把
**修复后**（`hash_variants`）的版本塞进去，断言它被判成"归一过"。2026-10 实测这段代码在
修复前后正好是 `mismatch` / `ok` 两个结果 —— 守门会咬人这件事是跑出来的，不是声称的。

**已知局限（漏检边界，别把它当成"hash 全查过了"）：**

- 只认得出**静态可解析**的链：跨模块的调用链、`dict.items()` 解出来的键、
  属性上的字段（`obj.item_hash`）都算不出值域 → 落进 `unresolved` 或干脆被"两边都判不准"放过；
- 比较之外的形式（`if plug_hash:`、排序、去重键、拼进 SQL/URL）**不在扫描范围**；
- 值域靠**命名与已知访问器**推断（`.search` / `.get_item_definition` / `…Hash` 字段名）。
  换个写法绕开这些线索（例如把 hash 存进没有 `hash` 字样的变量、或先 `int()` 再比）就漏；
- `hash_variants` 这类"两套都算上"的写法一律按安全放行 —— 它**确实**安全，但反过来说，
  扫描器不会检查你是不是"该用它的地方没用"；
- 台账按 `文件::表达式` 冻结，**不按行号**（行号会随重构漂移）；同一个表达式在一处
  归一、另一处没归一，只会命中一次，需要人工看。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "destiny_mcp"

SIGNED = "signed"
UNSIGNED = "unsigned"
BIT_SAME = "bit_same"
UNKNOWN = "unknown"
INT32_MAX = 0x7FFFFFFF
# 追形参链要跨好几层函数；真正的防环靠 _Resolver._active，这里只是个上限。
MAX_DEPTH = 24

# 名称索引行（有符号那一侧）：`itemHash` 来自库里的 id 列。**多一个都要在这里登记**，
# 漏一个就会把它当原始 JSON（无符号），假的"同域"就是这么来的。
ROW_ACCESSORS = {"search", "search_fuzzy", "search_by_type_name", "get_item_info"}
# 原始定义 JSON（无符号那一侧）：物品 JSON 体里的 `*Hash` 字段。
RAW_ACCESSORS = {
    "get_item_definition",
    "get_definition",
    "get_item_definition_by_name",
    "_query_json",
    "_query_json_from_conn",
    "get_plug_set_plugs",
    "get_bucket_definition",
    "get_metric_definition",
}
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
COMPARISONS = {ast.Eq: "==", ast.NotEq: "!=", ast.In: "in", ast.NotIn: "not in"}


# ───────────────────────────── 值模型 ─────────────────────────────
@dataclass(frozen=True)
class _Val:
    """一个表达式可能是什么。

    `tags` 是标量本身的取值域；`elem` 是"如果它是容器，成员是什么"；
    `kind == "shape"` 时 `fields` 是字典字面量里已知的键（`{"plug_hash": …}` 这种行）。
    """

    tags: frozenset[str] | None = frozenset({UNKNOWN})
    kind: str = "scalar"  # scalar | rows | raw | shape | empty
    elem: "_Val | None" = None
    fields: tuple[tuple[str, "_Val"], ...] = ()

    def field(self, key: str) -> "_Val":
        for name, value in self.fields:
            if name == key:
                return value
        return UNKNOWN_VAL


UNKNOWN_VAL = _Val()
ROWS = _Val(tags=None, kind="rows")
RAW = _Val(tags=None, kind="raw")
EMPTY = _Val(tags=None, kind="empty")
SIGNED_VAL = _Val(tags=frozenset({SIGNED}))
UNSIGNED_VAL = _Val(tags=frozenset({UNSIGNED}))


def _scalar_tags(value: _Val) -> frozenset[str]:
    return value.tags if value.tags is not None else frozenset({UNKNOWN})


def _merge(values: list[_Val], container: bool = False) -> _Val:
    values = [value for value in values if value.kind != "empty"]
    if not values:
        return UNKNOWN_VAL
    kinds = {value.kind for value in values if value.kind != "scalar"}
    if kinds == {"rows"}:
        return ROWS
    if kinds == {"raw"}:
        return RAW
    if all(value.kind == "shape" for value in values):
        grouped: dict[str, list[_Val]] = {}
        for value in values:
            for key, member in value.fields:
                grouped.setdefault(key, []).append(member)
        return _Val(
            kind="shape",
            fields=tuple((key, _merge(items, container=True)) for key, items in sorted(grouped.items())),
        )
    tags: set[str] = set()
    elems: list[_Val] = []
    for value in values:
        tags |= set(_scalar_tags(value))
        if value.elem is not None:
            elems.append(value.elem)
    # 同一个 hash 的两种写法都进了这个容器 → 不管那个 hash 本来是哪一套都能查中。
    # 只在"容器成员"上成立；标量同时可能是两套时仍然算判不准。
    if container and SIGNED in tags and UNSIGNED in tags:
        tags.discard(UNKNOWN)
    return _Val(tags=frozenset(tags), elem=_merge(elems, container=True) if elems else None)


def _representations(tags: frozenset[str], *, scalar: bool = False) -> frozenset[str] | None:
    """这个值可能落在哪些写法里（"s"/"u"）；`None` = 判不准。

    `scalar=True`（`==` / `!=` 的操作数）时，"两套写法都可能"不算"两套都对得上" ——
    比的是**一个**具体的值，两条赋值路径给不同写法就是对不上；而容器（`in` 的右边）
    同时装着两套写法才是真的安全。`bit_same` 不在此列：值本身 < 2^31，两套写法重合。
    """
    if UNKNOWN in tags or (scalar and SIGNED in tags and UNSIGNED in tags):
        return None
    out: set[str] = set()
    if BIT_SAME in tags:
        out |= {"s", "u"}
    if SIGNED in tags:
        out.add("s")
    if UNSIGNED in tags:
        out.add("u")
    return frozenset(out) if out else None


def _tags_of(values: set[int] | None) -> frozenset[str]:
    if not values:
        return frozenset({UNKNOWN})
    tags: set[str] = set()
    for value in values:
        if value < 0:
            tags.add(SIGNED)
        elif value > INT32_MAX:
            tags.add(UNSIGNED)
        else:
            tags.add(BIT_SAME)
    return frozenset(tags)


# ───────────────────────── 常量表静态求值 ─────────────────────────


class _ConstEval:
    """模块级/类级常量表 → 那一组整数（够用就行，认不出给 `None`）。"""

    def __init__(self, index: "_Index") -> None:
        self._index = index
        self._seen: set[str] = set()

    def values(self, node: ast.AST | None, depth: int = 0) -> set[int] | None:
        if node is None or depth > 8:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return {node.value}
        if isinstance(node, ast.Name):
            rhs = self._const_rhs(node.id)
            if rhs is None or node.id in self._seen:
                return None
            self._seen.add(node.id)
            try:
                out: set[int] = set()
                for item in rhs:
                    got = self.values(item, depth + 1)
                    if got is None:
                        return None
                    out |= got
                return out or None
            finally:
                self._seen.discard(node.id)
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            out = set()
            for item in node.elts:
                got = self.values(item, depth + 1)
                if got is None:
                    return None
                out |= got
            return out
        if isinstance(node, ast.Dict):
            # `in d` / 迭代 d 拿到的都是**键**（`_ARMOR_MOD_CATEGORIES` 那种表就是这么用的）
            out = set()
            for key in node.keys:
                got = self.values(key, depth + 1)
                if got is None:
                    return None
                out |= got
            return out
        if isinstance(node, (ast.SetComp, ast.ListComp)):
            return self._comprehension(node.elt, node.generators, depth)
        if isinstance(node, ast.DictComp):
            return self._comprehension(node.key, node.generators, depth)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left = self.values(node.left, depth + 1)
            right = self.values(node.right, depth + 1)
            return None if left is None or right is None else left | right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "frozenset",
            "set",
            "tuple",
            "list",
            "sorted",
        }:
            out = set()
            for arg in node.args:
                got = self.values(arg, depth + 1)
                if got is None:
                    return None
                out |= got
            return out
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"keys", "values"}
        ):
            return self.values(node.func.value, depth + 1)
        return None

    def _const_rhs(self, name: str) -> list[ast.expr] | None:
        # 按路径排序遍历：同名常量落在哪个文件都不能影响结果（干净 HOME / 别的机器也一样）
        for path in sorted(self._index.files, key=str):
            info = self._index.files[path]
            for scope in [info.module, *info.classes.values()]:
                if name in scope.binds:
                    bound = [item for item in scope.binds[name] if isinstance(item, ast.expr)]
                    if bound:
                        return bound
        return None

    def _comprehension(self, elt: ast.AST, generators: list[ast.Comprehension], depth: int) -> set[int] | None:
        """`{h: i for i, h in enumerate(STAT_HASHES)}` 这类：只要能求出被迭代的那张表。"""
        if len(generators) != 1 or generators[0].ifs:
            return None
        generator = generators[0]
        iterable: ast.AST | None = generator.iter
        slot = 0
        if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name):
            if iterable.func.id == "enumerate":
                slot = 1
                iterable = iterable.args[0] if iterable.args else None
            elif iterable.func.id in {"sorted", "list", "tuple", "set", "frozenset"}:
                iterable = iterable.args[0] if iterable.args else None
        if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute):
            if iterable.func.attr == "items":
                slot = 0
                iterable = iterable.func.value
            else:
                return None
        if iterable is None:
            return None
        table = self.values(iterable, depth + 1)
        if table is None:
            return None
        target = generator.target
        if isinstance(target, ast.Name):
            return table if (slot == 0 and isinstance(elt, ast.Name) and elt.id == target.id) else None
        if isinstance(target, ast.Tuple) and slot < len(target.elts):
            inner = target.elts[slot]
            if isinstance(elt, ast.Name) and isinstance(inner, ast.Name) and elt.id == inner.id:
                return table
        return None


# ─────────────────────────── 作用域与索引 ───────────────────────────


@dataclass
class _Slot:
    """元组解包的某一格 / `zip` 的第 i 路（`a, b = f(...)`）。"""

    value: ast.expr
    index: int


@dataclass
class _Scope:
    kind: str  # module | class | function | comprehension
    name: str = ""
    node: ast.AST | None = None
    parent: "_Scope | None" = None
    params: dict[str, int] = field(default_factory=dict)
    binds: dict[str, list[object]] = field(default_factory=dict)
    iters: dict[str, list[object]] = field(default_factory=dict)
    adds: dict[str, list[tuple[str, ast.expr]]] = field(default_factory=dict)
    returns: list[ast.expr] = field(default_factory=list)
    compares: list[tuple[ast.Compare, "_Scope"]] = field(default_factory=list)
    calls: list[tuple[str, ast.Call, "_Scope"]] = field(default_factory=list)

    def qualname(self) -> str:
        if self.parent is not None and self.parent.kind == "class":
            return f"{self.parent.name}.{self.name}"
        return self.name


@dataclass
class _FileInfo:
    path: Path
    module: _Scope
    funcs: dict[str, _Scope] = field(default_factory=dict)
    classes: dict[str, _Scope] = field(default_factory=dict)


@dataclass
class _Index:
    files: dict[Path, _FileInfo] = field(default_factory=dict)
    funcs: dict[str, list[_Scope]] = field(default_factory=dict)
    call_sites: dict[str, list[tuple[ast.Call, _Scope]]] = field(default_factory=dict)
    compares: list[tuple[Path, ast.Compare, _Scope]] = field(default_factory=list)


def _walk_own(node: ast.AST):
    """这棵子树里的节点，**不下钻**嵌套 def/class/lambda（那是别人的作用域）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, NESTED):
            continue
        yield child
        yield from _walk_own(child)


def _all_scopes(info: _FileInfo) -> list[_Scope]:
    return [info.module, *info.classes.values(), *info.funcs.values()]


def _record(scope: _Scope, node: ast.AST) -> None:
    if isinstance(node, ast.Compare):
        scope.compares.append((node, scope))
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name:
            scope.calls.append((name, node, scope))
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and node.args:
            base, attr = func.value.id, func.attr
            if attr in {"add", "append", "extend", "update"}:
                scope.adds.setdefault(base, []).append((attr, node.args[0]))
            elif attr == "setdefault" and len(node.args) >= 2:
                scope.adds.setdefault(base, []).append(("add", node.args[0]))
                scope.adds.setdefault(base, []).append(("add", node.args[1]))
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        scope.binds.setdefault(node.target.id, []).append(node.value)


def _bind_target(scope: _Scope, target: ast.AST, value: ast.expr) -> None:
    if isinstance(target, ast.Name):
        scope.binds.setdefault(target.id, []).append(value)
    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
        # `d[k] = v` 也是往容器里塞成员
        scope.adds.setdefault(target.value.id, []).append(("add", value))
    elif isinstance(target, (ast.Tuple, ast.List)):
        for index, elt in enumerate(target.elts):
            if isinstance(elt, ast.Name):
                scope.binds.setdefault(elt.id, []).append(_Slot(value, index))
    elif isinstance(target, ast.Starred) and isinstance(target.value, ast.Name):
        scope.binds.setdefault(target.value.id, []).append(value)


def _bind_iter_target(scope: _Scope, target: ast.AST, iterable: ast.expr) -> None:
    if isinstance(target, ast.Name):
        scope.iters.setdefault(target.id, []).append(iterable)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for index, elt in enumerate(target.elts):
            if isinstance(elt, ast.Name):
                scope.iters.setdefault(elt.id, []).append(_Slot(iterable, index))


def _collect(scope: _Scope, body: list[ast.stmt]) -> None:
    for stmt in body:
        _collect_stmt(scope, stmt)


def _collect_stmt(scope: _Scope, stmt: ast.stmt) -> None:
    if isinstance(stmt, NESTED):
        return
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            _bind_target(scope, target, stmt.value)
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        _bind_target(scope, stmt.target, stmt.value)
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        scope.adds.setdefault(stmt.target.id, []).append(("update", stmt.value))
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        _bind_iter_target(scope, stmt.target, stmt.iter)
    elif isinstance(stmt, ast.With):
        for item in stmt.items:
            if item.optional_vars is not None:
                _bind_target(scope, item.optional_vars, item.context_expr)
    if isinstance(stmt, ast.Return) and stmt.value is not None and scope.kind == "function":
        scope.returns.append(stmt.value)
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.stmt):
            _collect_stmt(scope, child)
            continue
        for node in _walk_own(child):
            _record(scope, node)
        _record(scope, child)


def _index_defs(index: _Index, info: _FileInfo, body: list[ast.stmt], parent: _Scope) -> None:
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = _Scope(kind="function", name=stmt.name, node=stmt, parent=parent)
            for position, arg in enumerate(
                stmt.args.posonlyargs + stmt.args.args + stmt.args.kwonlyargs
            ):
                scope.params[arg.arg] = position
            _collect(scope, stmt.body)
            info.funcs[scope.qualname()] = scope
            index.funcs.setdefault(stmt.name, []).append(scope)
        elif isinstance(stmt, ast.ClassDef):
            scope = _Scope(kind="class", name=stmt.name, node=stmt, parent=parent)
            _collect(scope, stmt.body)
            info.classes[stmt.name] = scope
            _index_defs(index, info, stmt.body, scope)


def _build_index(
    paths: list[Path], extra: dict[Path, str] | None = None
) -> _Index:
    """`extra` 是"内存里的额外源文件"——注入验证用它，不必真往仓库里写文件。"""
    index = _Index()
    sources = {path: path.read_text(encoding="utf-8") for path in paths}
    sources.update(extra or {})
    for path in sorted(sources, key=str):
        tree = ast.parse(sources[path], filename=str(path))
        module = _Scope(kind="module", name=path.stem, node=tree)
        info = _FileInfo(path=path, module=module)
        index.files[path] = info
        _collect(module, tree.body)
        _index_defs(index, info, tree.body, module)
    for path in sorted(index.files, key=str):
        info = index.files[path]
        for scope in _all_scopes(info):
            for name, call, caller in scope.calls:
                index.call_sites.setdefault(name, []).append((call, caller))
            for node, owner in scope.compares:
                index.compares.append((path, node, owner))
    return index


# ───────────────────────────── 定域 ─────────────────────────────


class _Resolver:
    def __init__(self, index: _Index) -> None:
        self._index = index
        self._consts = _ConstEval(index)
        self._active: set[tuple] = set()
        self._memo: dict[tuple, _Val] = {}

    # —— 标量 ——
    def value(self, node: ast.AST | None, scope: _Scope, depth: int = 0) -> _Val:
        if node is None or depth > MAX_DEPTH:
            return UNKNOWN_VAL
        if isinstance(node, _Slot):
            return self._slot_value(node, scope, depth)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int):
                return UNKNOWN_VAL
            return _Val(tags=_tags_of({node.value}))
        if isinstance(node, ast.Name):
            return self._name_value(node.id, scope, depth)
        if isinstance(node, ast.Attribute):
            # `obj.item_hash` 这种：字段在哪个类上、由谁填的，静态追不动 —— 如实说判不准
            return UNKNOWN_VAL
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
                return self.element(node.value, scope, depth)
            return _field_of(self.value(node.value, scope, depth), self._key(node.slice))
        if isinstance(node, ast.Call):
            return self._call_value(node, scope, depth)
        if isinstance(node, ast.IfExp):
            return _merge([self.value(node.body, scope, depth), self.value(node.orelse, scope, depth)], container=True)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return _merge([self.value(node.left, scope, depth), self.value(node.right, scope, depth)], container=True)
        if isinstance(node, ast.BoolOp):
            return _merge([self.value(item, scope, depth) for item in node.values], container=True)
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, int)
        ):
            return _Val(tags=_tags_of({-node.operand.value}))
        if isinstance(node, ast.Dict):
            fields = tuple(
                (key.value, self.value(item, scope, depth + 1))
                for key, item in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
            return _Val(kind="shape", fields=fields) if fields else EMPTY
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            return _Val(elem=self.element(node, scope, depth))
        if isinstance(node, (ast.SetComp, ast.ListComp, ast.DictComp, ast.GeneratorExp)):
            return _Val(elem=self.element(node, scope, depth))
        if isinstance(node, ast.Compare):
            return _Val(tags=frozenset({BIT_SAME}))
        return UNKNOWN_VAL

    @staticmethod
    def _key(slice_node: ast.AST) -> str:
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, (str, int)):
            return str(slice_node.value)
        return ""

    def _name_value(self, name: str, scope: _Scope, depth: int) -> _Val:
        found = self._local(scope, name, depth, want="value")
        if found is not None:
            return found
        current: _Scope | None = scope
        while current is not None:
            if name in current.params:
                got = self._param_values(current, name, depth, container=False)
                return got if got is not None else UNKNOWN_VAL
            current = current.parent
        const = self._consts.values(ast.Name(id=name))
        return _Val(tags=_tags_of(const)) if const is not None else UNKNOWN_VAL

    def _local(self, scope: _Scope, name: str, depth: int, want: str) -> _Val | None:
        current: _Scope | None = scope
        while current is not None:
            if name in current.binds or name in current.iters or name in current.adds:
                key = (id(current), name, want)
                if key in self._memo:
                    return self._memo[key]
                if key in self._active:  # 自引用（`x = f(x)` 之类）——别再往下钻
                    return UNKNOWN_VAL
                self._active.add(key)
                try:
                    out = self._local_value(current, name, depth, want)
                finally:
                    self._active.discard(key)
                self._memo[key] = out
                return out
            current = current.parent
        return None

    def _local_value(self, scope: _Scope, name: str, depth: int, want: str) -> _Val:
        if want == "value":
            values = [self.value(item, scope, depth + 1) for item in scope.binds.get(name, [])]
            values += [
                self._slot_value(item, scope, depth + 1) if isinstance(item, _Slot)
                else self._iter_slot(item, 0, scope, depth + 1)
                for item in scope.iters.get(name, [])
            ]
            return _merge(values or [UNKNOWN_VAL], container=True)
        return self._element_of_name(scope, name, depth)

    def _element_of_name(self, scope: _Scope, name: str, depth: int) -> _Val:
        values: list[_Val] = []
        for bound in scope.binds.get(name, []):
            values.append(self.element(bound, scope, depth + 1))
        for item in scope.iters.get(name, []):
            values.append(
                self._slot_value(item, scope, depth + 1) if isinstance(item, _Slot)
                else self.element(item, scope, depth + 1)
            )
        for method, arg in scope.adds.get(name, []):
            # `update/extend` 加进去的是 arg 的成员，`add/append` 加进去的就是 arg 本身
            values.append(self.element(arg, scope, depth + 1) if method in {"update", "extend"}
                          else self.value(arg, scope, depth + 1))
        return _merge(values, container=True)

    # —— 成员 ——
    def element(self, node: ast.AST | None, scope: _Scope, depth: int = 0) -> _Val:
        if node is None or depth > MAX_DEPTH:
            return UNKNOWN_VAL
        if isinstance(node, _Slot):
            return self._slot_element(node, scope, depth)
        if isinstance(node, ast.Name):
            found = self._local(scope, node.id, depth, want="element")
            if found is not None:
                return found
            current: _Scope | None = scope
            while current is not None:
                if node.id in current.params:
                    got = self._param_values(current, node.id, depth, container=True)
                    return got if got is not None else UNKNOWN_VAL
                current = current.parent
            const = self._const_val(node)
            if const is not None:
                return const
            const_expr = self._const_expr(node.id)
            return self.element(const_expr, scope, depth + 1) if const_expr is not None else UNKNOWN_VAL
        if isinstance(node, ast.Call):
            const = self._const_val(node)
            return const if const is not None else self._call_element(node, scope, depth)
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            if not node.elts:
                return EMPTY
            return _merge([self.value(item, scope, depth) for item in node.elts], container=True)
        if isinstance(node, ast.Dict):
            if not node.keys:
                return EMPTY
            return _merge([self.value(key, scope, depth) for key in node.keys], container=True)
        if isinstance(node, (ast.SetComp, ast.ListComp, ast.GeneratorExp)):
            return self._comprehension_element(node.elt, node.generators, scope, depth)
        if isinstance(node, ast.DictComp):
            return self._comprehension_element(node.key, node.generators, scope, depth)
        if isinstance(node, ast.Subscript):
            return self.element(self.value(node.value, scope, depth), scope, depth)
        return self.element(self.value(node, scope, depth), scope, depth + 1)

    def _comprehension_element(
        self, elt: ast.AST, generators: list[ast.Comprehension], scope: _Scope, depth: int
    ) -> _Val:
        sub = _Scope(kind="comprehension", name="<comp>", parent=scope)
        for generator in generators:
            for name in _target_names(generator.target):
                sub.iters.setdefault(name, []).append(generator.iter)
        return self.value(elt, sub, depth + 1)

    def _slot_value(self, slot: _Slot, scope: _Scope, depth: int) -> _Val:
        if depth > MAX_DEPTH:
            return UNKNOWN_VAL
        if isinstance(slot.value, (ast.Tuple, ast.List)) and slot.index < len(slot.value.elts):
            return self.value(slot.value.elts[slot.index], scope, depth + 1)
        if isinstance(slot.value, ast.Call):
            got = self._call_return_slot(slot.value, slot.index, "value", depth)
            if got is not None:
                return got
        return self._iter_slot(slot.value, slot.index, scope, depth + 1)

    def _slot_element(self, slot: _Slot, scope: _Scope, depth: int) -> _Val:
        if depth > MAX_DEPTH:
            return UNKNOWN_VAL
        if isinstance(slot.value, (ast.Tuple, ast.List)) and slot.index < len(slot.value.elts):
            return self.element(slot.value.elts[slot.index], scope, depth + 1)
        if isinstance(slot.value, ast.Call):
            got = self._call_return_slot(slot.value, slot.index, "element", depth)
            if got is not None:
                return got
        return self._iter_slot(slot.value, slot.index, scope, depth + 1)

    def _call_return_slot(self, call: ast.Call, index: int, want: str, depth: int) -> _Val | None:
        """`a, b = f(...)`：取 f 返回的那个元组的第 index 格。"""
        func = call.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        scopes = self._index.funcs.get(name)
        if not scopes:
            return None
        values: list[_Val] = []
        for scope in scopes:
            for ret in scope.returns:
                if not isinstance(ret, (ast.Tuple, ast.List)) or index >= len(ret.elts):
                    return None
                inner = ret.elts[index]
                values.append(
                    self.element(inner, scope, depth + 1) if want == "element"
                    else self.value(inner, scope, depth + 1)
                )
        return _merge(values, container=True) if values else None

    def _iter_slot(self, iterable: ast.AST, index: int, scope: _Scope, depth: int) -> _Val:
        if depth > MAX_DEPTH:
            return UNKNOWN_VAL
        if isinstance(iterable, ast.Call):
            func = iterable.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name == "zip":
                if index < len(iterable.args):
                    return self._iter_slot(iterable.args[index], 0, scope, depth + 1)
                return UNKNOWN_VAL
            if name == "enumerate":
                if index == 1 and iterable.args:
                    return self._iter_slot(iterable.args[0], 0, scope, depth + 1)
                return _Val(tags=frozenset({BIT_SAME})) if index == 0 else UNKNOWN_VAL
            if name in {"items", "keys", "values"}:
                # `for k, v in d.items()`：键可能是 hash 也可能是名字，静态判不了
                return UNKNOWN_VAL
        if index != 0:
            return UNKNOWN_VAL
        if isinstance(iterable, ast.expr):
            const = self._const_val(iterable)
            if const is not None:
                return const
        return self.element(iterable, scope, depth + 1)

    def _param_values(self, scope: _Scope, name: str, depth: int, *, container: bool) -> _Val | None:
        """形参的值：**按调用点回填**；多处调用取并集。

        方法要跳掉 `self`（调用点给的实参从第二个形参起对）。同名方法被多态调用时会把
        所有调用点并起来 —— 这是有意的保守：宁可判不准，也不要假装知道。
        """
        index = scope.params.get(name)
        if index is None:
            return None
        if scope.parent is not None and scope.parent.kind == "class":
            index -= 1
        if index < 0:
            return UNKNOWN_VAL
        values: list[_Val] = []
        for call, caller in self._index.call_sites.get(scope.name, []):
            if index >= len(call.args):
                continue
            arg = call.args[index]
            values.append(
                self.element(arg, caller, depth + 1) if container
                else self.value(arg, caller, depth + 1)
            )
        return _merge(values, container=True) if values else UNKNOWN_VAL

    def _const_val(self, node: ast.AST) -> _Val | None:
        if not isinstance(node, ast.expr):
            return None
        target = node
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"keys", "values", "items"}
            and not node.args
        ):
            target = node.func.value
        got = self._consts.values(target)
        return _Val(tags=_tags_of(got)) if got is not None else None

    def _const_expr(self, name: str) -> ast.expr | None:
        rhs = self._consts._const_rhs(name)
        return rhs[0] if rhs else None

    # —— 调用 ——
    def _call_value(self, call: ast.Call, scope: _Scope, depth: int) -> _Val:
        if depth > MAX_DEPTH:
            return UNKNOWN_VAL
        func = call.func
        if isinstance(func, ast.Name):
            if func.id == "to_signed":
                return SIGNED_VAL
            if func.id == "to_unsigned":
                return UNSIGNED_VAL
            if func.id in {"int", "float"} and call.args:
                return self.value(call.args[0], scope, depth + 1)
            if func.id in {"set", "list", "frozenset", "tuple", "sorted"}:
                if not call.args:
                    return EMPTY
                return _Val(elem=self.element(call.args[0], scope, depth + 1))
            if func.id in {"len", "bool", "str", "repr"}:
                return UNKNOWN_VAL
            return self._func_return(func.id, depth)
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in RAW_ACCESSORS:
                return RAW
            if attr in ROW_ACCESSORS:
                return ROWS
            if attr == "get" and call.args:
                key = self._key(call.args[0])
                if not key:
                    # `.get(变量)` 取的是成员
                    return self.element(func.value, scope, depth + 1)
                return _field_of(self.value(func.value, scope, depth + 1), key)
            if attr in {"keys", "values", "items"}:
                return UNKNOWN_VAL
            return self._func_return(attr, depth)
        return UNKNOWN_VAL

    def _func_return(self, name: str, depth: int) -> _Val:
        scopes = self._index.funcs.get(name)
        if not scopes or depth > MAX_DEPTH:
            return UNKNOWN_VAL
        values: list[_Val] = []
        for scope in scopes:
            for ret in scope.returns:
                values.append(self.value(ret, scope, depth + 1))
        return _merge(values, container=True)

    def _call_element(self, call: ast.Call, scope: _Scope, depth: int) -> _Val:
        if depth > MAX_DEPTH:
            return UNKNOWN_VAL
        func = call.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if isinstance(func, ast.Name) and func.id in {"set", "list", "frozenset", "tuple", "sorted", "dict"}:
            if not call.args:
                return UNKNOWN_VAL
            return self.element(call.args[0], scope, depth + 1)
        if name in RAW_ACCESSORS:
            return UNKNOWN_VAL
        if name in ROW_ACCESSORS:
            return ROWS
        scopes = self._index.funcs.get(name)
        if scopes:
            values: list[_Val] = []
            for item in scopes:
                for ret in item.returns:
                    values.append(self.element(ret, item, depth + 1))
            return _merge(values, container=True)
        return self.element(self._call_value(call, scope, depth), scope, depth + 1)


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _field_of(base: _Val, key: str) -> _Val:
    if not key:
        return UNKNOWN_VAL
    if base.kind == "shape":
        return base.field(key)
    if base.kind == "rows":
        # 名称索引行是最阴的一处：`itemHash` 来自库里的 id 列（有符号），
        # 同一行的其余字段直接取自原始 JSON（无符号）。
        if key == "itemHash":
            return SIGNED_VAL
        return UNSIGNED_VAL if key.lower().endswith("hash") else UNKNOWN_VAL
    if base.kind == "raw":
        return UNSIGNED_VAL if key.lower().endswith("hash") else UNKNOWN_VAL
    if key.lower().endswith(("hash", "hashes")):
        # 判不出 base 时按"原始载荷字段"算（绝大多数 `d.get("…Hash")` 都是）
        return UNSIGNED_VAL
    return UNKNOWN_VAL


# ───────────────────────────── 采集与判定 ─────────────────────────────


class _Site(NamedTuple):
    path: str
    line: int
    expr: str
    kind: str
    detail: str


def _is_hash_key(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower().endswith("hash")
    )


def _is_hashy(node: ast.AST, scope: _Scope | None = None, depth: int = 0) -> bool:
    if isinstance(node, ast.Name):
        if node.id.lower().endswith(("hash", "hashes")):
            return True
        if scope is None or depth > 2:
            return False
        current: _Scope | None = scope
        while current is not None:
            for bound in list(current.binds.get(node.id, [])) + list(current.iters.get(node.id, [])):
                if isinstance(bound, ast.expr) and _is_hashy(bound, current, depth + 1):
                    return True
            for _method, arg in current.adds.get(node.id, []):
                if _is_hashy(arg, current, depth + 1):
                    return True
            current = current.parent
        return False
    if isinstance(node, ast.Attribute):
        return node.attr.lower().endswith(("hash", "hashes"))
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"to_signed", "to_unsigned", "hash_variants"}:
            return True
        if isinstance(func, ast.Attribute) and func.attr in {"get", "pop"} and node.args:
            return _is_hash_key(node.args[0])
        return False
    if isinstance(node, ast.Subscript):
        return _is_hash_key(node.slice)
    return False


def _read_base(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
        return node.func.value
    if isinstance(node, ast.Subscript):
        return node.value
    return None


def _read_key(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        return node.args[0]
    if isinstance(node, ast.Subscript):
        return node.slice
    return None


def _expand(node: ast.AST, scope: _Scope, depth: int = 0) -> ast.AST:
    """把"只是给某个读法起了个名字"的局部变量还原回那个读法（最多两层）。"""
    while depth < 2 and isinstance(node, ast.Name):
        bound: ast.expr | None = None
        current: _Scope | None = scope
        while current is not None and bound is None:
            for item in current.binds.get(node.id, []):
                if isinstance(item, ast.expr):
                    bound = item
                    break
            current = current.parent
        if not isinstance(bound, (ast.Call, ast.Subscript)):
            return node
        node = bound
        depth += 1
    return node


def _is_row_source(node: ast.AST, scope: _Scope, resolver: _Resolver) -> bool:
    base = _read_base(node)
    if base is None:
        return False
    probe = _Scope(kind="module", name="<probe>", parent=scope)
    return resolver.value(base, probe, 1).kind == "rows"


def _both_raw_reads(a: ast.AST, b: ast.AST, scope: _Scope, resolver: _Resolver) -> bool:
    """两边都是原始载荷里的 `*Hash` 字段 —— 都是无符号，同域，不可能错。"""
    for node in (_expand(a, scope), _expand(b, scope)):
        if _read_base(node) is None or not _is_hash_key(_read_key(node)):
            return False
        if _is_row_source(node, scope, resolver):
            return False
    return True


def _same_source(a: ast.AST, b: ast.AST, scope: _Scope, resolver: _Resolver) -> bool:
    """从**同一个来源**（同一个 dict）取的字段：写法必然一致。

    唯一的例外就是名称索引行（`itemHash` 有符号、同行其它字段无符号），所以排除掉它。
    """
    left, right = _expand(a, scope), _expand(b, scope)
    base_left, base_right = _read_base(left), _read_base(right)
    if base_left is None or base_right is None:
        return False
    if ast.dump(base_left) != ast.dump(base_right):
        return False
    return not (_is_row_source(left, scope, resolver) or _is_row_source(right, scope, resolver))


def _in_added_elements(element: ast.AST, container: ast.AST, scope: _Scope) -> bool:
    """`x in seen` 而 `seen.add(x)` 用的就是同一个表达式 —— 同源。"""
    if not isinstance(container, ast.Name):
        return False
    want = ast.dump(element)
    current: _Scope | None = scope
    while current is not None:
        for _method, arg in current.adds.get(container.id, []):
            if ast.dump(arg) == want:
                return True
        current = current.parent
    return False


def _verdict(left: frozenset[str], right: frozenset[str], *, scalar: bool) -> str:
    """`mismatch` = 两套写法**不可能**相交；`unresolved` = 一边判不准、另一边只有一套写法。"""
    left_reps, right_reps = (
        _representations(left, scalar=scalar),
        _representations(right, scalar=scalar),
    )
    if left_reps is None and right_reps is None:
        return "ok"
    if left_reps is None or right_reps is None:
        other = right_reps if left_reps is None else left_reps
        return "unresolved" if other is not None and len(other) == 1 else "ok"
    return "ok" if (left_reps & right_reps) else "mismatch"


def _scan(index: _Index) -> list[_Site]:
    """返回按 (路径, 行号, 表达式, 判定) 排序的结果 —— 与文件遍历顺序无关。"""
    resolver = _Resolver(index)
    sites: list[_Site] = []
    for path, info in sorted(index.files.items(), key=lambda item: str(item[0])):
        relative = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        for scope in _all_scopes(info):
            for node, owner in scope.compares:
                for op, left, right in zip(
                    node.ops, [node.left, *node.comparators], node.comparators
                ):
                    if type(op) not in COMPARISONS:
                        continue
                    if not (_is_hashy(left, owner) and _is_hashy(right, owner)):
                        continue
                    expr = (
                        f"{ast.unparse(left)} {COMPARISONS[type(op)]} {ast.unparse(right)}"
                    )
                    kind, detail = _classify(op, left, right, owner, resolver)
                    sites.append(_Site(str(relative), node.lineno, expr, kind, detail))
    return sorted(set(sites))


def _classify(
    op: ast.cmpop, left: ast.AST, right: ast.AST, scope: _Scope, resolver: _Resolver
) -> tuple[str, str]:
    if _both_raw_reads(left, right, scope, resolver):
        return "raw_pair", "两边都是原始载荷字段（都无符号）"
    if _same_source(left, right, scope, resolver):
        return "same_source", "同一个来源的两个字段"
    membership = isinstance(op, (ast.In, ast.NotIn))
    if membership and _in_added_elements(left, right, scope):
        return "re_added", "先 add 进去、再拿来查（同一个表达式）"
    left_tags = _scalar_tags(resolver.value(left, scope))
    right_tags = _scalar_tags(
        resolver.element(right, scope) if membership else resolver.value(right, scope)
    )
    verdict = _verdict(left_tags, right_tags, scalar=not membership)
    detail = f"左={'/'.join(sorted(left_tags))} 右={'/'.join(sorted(right_tags))}"
    if verdict == "mismatch":
        return "mismatch", detail
    if verdict == "unresolved":
        return "unresolved", detail
    # 有一边**自己就带两套写法**（`hash_variants`、或 `h` 与 `to_unsigned(h)` 同进一个集合）
    # —— 那一边不管对面给哪一套都能命中，这就是这个仓库修这个病的标准写法。
    for reps in (
        _representations(left_tags),
        _representations(right_tags, scalar=not membership),
    ):
        if reps is not None and {"s", "u"} <= reps:
            return "widened", detail
    return "aligned", detail


def _source_files() -> list[Path]:
    return sorted(
        (path for path in SOURCE_ROOT.rglob("*.py") if "__pycache__" not in path.parts),
        key=str,
    )


def _scan_tree(extra: dict[Path, str] | None = None) -> list[_Site]:
    return _scan(_build_index(_source_files(), extra))


# ───────────────────────────── 复核台账 ─────────────────────────────
#
# 键 = `相对路径::表达式`（**不按行号**：行号会随重构漂移，表达式不会）。
# 每一条都要写清"为什么静态判不出来"以及"它到底安不安全"。
# 新增条目 / 过期条目的判红规则见下面两个测试。
LEDGER: dict[str, str] = {
    "destiny_mcp/manifest_armor.py::pch not in target_hashes": (
        "「安全，判不准只是因为一支取不出值的分支」左边 `plug.get('plugCategoryHash')` 是定义 "
        "JSON 体内的原始字段（无符号）；`target_hashes` 由 `{2487827355}`、"
        "`{2912171003, 3422420680, 1526202480, 2111701510, 912441879}`、`{3773173029}`、"
        "`set(self._ARMOR_MOD_CATEGORIES.keys())`（键含 3481777685）拼成，全在无符号那一侧。"
        "静态算不出是因为还有一支 `{slot_to_hash[slot_key]}`。"
    ),
    # **下面这三条原来在这里，2026-10-03 已删**：它们记的是回读核对里那三条跨值域比较
    # （`actual_sockets[…].get('plugHash') != plug_hash` 等），而当天真机证明那**不是"判不准"、
    # 是恒为 False 的真 bug**（社区模板给有符号的 `回天掌法` = -1847517590，账号上是 2447449706
    # → 两次回读窗口白烧 147.8 秒）。现在两边都过 `to_unsigned`，这几条比较被下面
    # `test_normalization_is_positively_recognized` 当"归一过"正面钉住；台账里留着就是过期条目。
    "destiny_mcp/services/loadout_subclass_sockets.py::item.get('itemHash') == subclass.subclass_item_hash": (
        "「判不准，有真风险」找「要换上的那个子职业」时按 hash 认物品：左边是背包里的 API 字段"
        "（无符号），右边是配装里记的。认不中的后果是「要装的子职业不在这个角色的背包里」这条"
        "误报 —— 而这正是 2026-09-28 那条修复要解决的问题，所以值域必须一致。"
    ),
    "destiny_mcp/services/loadout_subclass_sockets.py::entry.get('singleInitialItemHash', 0) == plug_hash": (
        "「判不准，有真风险」碎片/技能找槽位：右边 `plug_hash` 是形参，调用点给的是"
        "`subclass.plug_sockets`（profile 读到的 → 无符号）**或**配装方案里的 "
        "`super_hash`/`aspect_hashes`/`fragment_hashes`（可能来自 `manifest.search()` → 有符号）。"
        "同一个文件 285 行那条判断已经 `to_unsigned` 过了，这两条没跟 —— 混来源时就是"
        "「找不到兼容插槽」。"
    ),
    "destiny_mcp/services/loadout_subclass_sockets.py::item.get('plugItemHash', 0) == plug_hash": (
        "「判不准，有真风险」同上一行：这一条是翻 `reusablePlugItems` 的池子。"
    ),
    "destiny_mcp/services/loadout_subclass_sockets.py::current_hash == plug_hash": (
        "「判不准，有真风险」同一函数里「这个槽现在装的是不是它」的短路判断：左边是组件 305 的"
        "无符号值，右边同上。判错的后果是多打一次写接口（1679 / 白烧几秒），不会写坏账号。"
    ),
    "destiny_mcp/services/weapon_profile.py::entry.get('statTypeHash') == stat_hash": (
        "「真问题（半归一），本轮不修」同一个函数上面几行专门写了 "
        "`str(stat_hash + 4294967296 if stat_hash < 0 else stat_hash)` —— 也就是**声明**"
        "这个函数两套写法都收；但这条 `investmentStats` 回退只按原样比。今天两个调用方"
        "（`rpm_of` 的常量、`_stat_order` 从定义 JSON 取的键）都给无符号，所以没炸；"
        "一旦有人传 `manifest.search()` 的 hash，显示值那条路查得到、投资值这条查不到，"
        "表现是**属性静默消失**。修法是这里也过 `to_unsigned`（两边一起归一）。"
    ),
    "destiny_mcp/services/weekly_service.py::milestone_hash in RAID_MILESTONE_HASHES": (
        "「安全，判不准」`RAID_MILESTONE_HASHES = {2712317338}`（无符号）；`milestone_hash` 是"
        "里程碑列表的键，来自 profile（无符号）。静态算不出是因为调用点在同一个文件的另一处、"
        "键又是 `(...).items()` 解出来的 —— 键的取值域从 `.items()` 读不出来。"
    ),
    "destiny_mcp/tools/_stats_branches.py::row.get('metric_hash') == metric_hash": (
        "「安全，判不准」`metric_hash` 是 `COUNTER_STAT_PAIRS[_COUNTER_MODE]` 的键，那张表在 "
        "`data/pvp_counters.py`，是按组件 1100（游戏内计数器）的 hash 人工核对成表的 → 无符号；"
        "`row` 来自 `read_metrics`（同一个组件）。静态算不出嵌套字典的键。"
    ),
}

# 确实存在、这一轮不修的问题（**原因写在 LEDGER 里**）。它们出现时测试会**点名**，
# 但不判红 —— 目的是"不许悄悄修好之后还把条目留在台账里"，而不是"放它一马"。
KNOWN_UNFIXED = {
    "destiny_mcp/services/weapon_profile.py::entry.get('statTypeHash') == stat_hash",
}


def test_cross_domain_hash_comparisons_are_reviewed() -> None:
    """可疑点必须逐条复核过；台账里过期的条目也要删掉。"""
    sites = _scan_tree()
    unresolved = {
        f"{site.path}::{site.expr}": site
        for site in sites
        if site.kind == "unresolved"
    }
    fresh = {key: site for key, site in unresolved.items() if key not in KNOWN_UNFIXED}
    unreviewed = sorted(key for key in fresh if key not in LEDGER)
    stale = sorted(key for key in LEDGER if key not in unresolved)

    assert not unreviewed, (
        "这些比较点跨了 hash 的两套值域（Manifest 的 id 列有符号 / 原始 JSON 与账号侧无符号），"
        "静态判不出安不安全。**要么把两边归一**（`to_unsigned` / `to_signed` / `hash_variants`），"
        "**要么**在 `tests/test_hash_domains.py` 的 `LEDGER` 里补一条并写清为什么安全：\n  "
        + "\n  ".join(
            f"{key}  [{fresh[key].detail}]  ({fresh[key].path}:{fresh[key].line})"
            for key in unreviewed
        )
    )
    assert not stale, (
        "台账里有对不上的条目（代码已经归一 / 删掉 / 挪走了）：请从 LEDGER 里删掉，"
        "别让它烂在那儿 —— 过期条目会让这道闸看着很严、实际早就没在守了：\n  "
        + "\n  ".join(stale)
    )


def test_known_unfixed_entries_are_still_real() -> None:
    """`KNOWN_UNFIXED` 是"确实有问题、这一轮不修"：修好之后必须把条目删掉。"""
    unresolved = {
        f"{site.path}::{site.expr}"
        for site in _scan_tree()
        if site.kind == "unresolved"
    }
    gone = sorted(key for key in KNOWN_UNFIXED if key not in unresolved)
    assert not gone, (
        "好消息：这些已知问题已经不在扫描结果里了（修好 / 搬走 / 换了写法），"
        "请把 `KNOWN_UNFIXED` 与 `LEDGER` 里对应的条目删掉，并把这条消息一起更新：\n  "
        + "\n  ".join(gone)
    )
    both = sorted(key for key in KNOWN_UNFIXED if key not in LEDGER)
    assert not both, f"`KNOWN_UNFIXED` 的每一条都要在 `LEDGER` 里写明原因：{both}"


def test_no_provably_impossible_hash_comparison() -> None:
    """两边**静态可证**落在不同值域 —— 这种比较永远不成立，直接判红。"""
    broken = sorted(
        f"{site.path}:{site.line}  {site.expr}  [{site.detail}]"
        for site in _scan_tree()
        if site.kind == "mismatch"
    )
    assert not broken, (
        "这些比较的两边一个只可能是 Manifest 的有符号写法、一个只可能是无符号写法，"
        "结果恒为 False（真机上这个病已经咬了三次：愿望单查表、规模闸门的指定金装收窄、"
        "碎片插槽兼容性）。归一之后再比：\n  " + "\n  ".join(broken)
    )


def test_normalization_is_positively_recognized() -> None:
    """归一过的地方要**认得出来**（不是「扫不到」），否则这道闸只是在自欺欺人。"""
    sites = {f"{site.path}::{site.expr}": site for site in _scan_tree()}
    expected = {
        # `hash_variants` 把这一颗的两种写法都算上（2026-10 修复第三次咬人的那一处）
        "destiny_mcp/services/build_fragments.py::entry.get('singleInitialItemHash', 0) in variants": "widened",
        # 同一个 hash 的 `h` 与 `to_unsigned(h)` 都进集合（第一、二次咬人都是这个修法）
        "destiny_mcp/services/weapon_compare_service.py::raw.get('itemHash') in target_hashes": "widened",
        "destiny_mcp/services/inventory_service.py::item.item_hash in match_hashes": "widened",
        # 两边都过 `to_unsigned`
        "destiny_mcp/services/loadout_mod_sockets.py::plug_hash == target": "aligned",
        # 回读核对的两条（2026-10-03 真机：社区模板给有符号、账号侧给无符号 → 恒为 False，
        # 两次回读窗口白烧 147.8 秒；修好之后必须**认得出来**，不是"扫不到"）
        "destiny_mcp/services/loadout_matches.py::to_unsigned(installed.get('plugHash', 0) or 0) != to_unsigned(plug_hash)": "aligned",
        "destiny_mcp/services/loadout_matches.py::to_unsigned(subclass_item.get('itemHash', 0) or 0) != expected_item_hash": "aligned",
    }
    missing = sorted(key for key in expected if key not in sites)
    wrong = sorted(
        f"{key}: 期望 {expected[key]}，实际 {sites[key].kind}"
        for key in expected
        if key in sites and sites[key].kind != expected[key]
    )
    assert not missing, (
        "这些**已经归一**的比较点认不出来了（采集或定域退化了 —— 那会让别的点被误报，"
        f"甚至漏报）。如果它们确实被搬走/改写了，请同步改这张表：{missing}"
    )
    assert not wrong, f"归一的判定变了：{wrong}"


# 注入样本：**修复前**的碎片配置代码（2026-10 真机第三次咬人的原样）。
# 名字刻意起得不会和仓库里任何函数撞车 —— 否则实参回填会把两边混起来。
_PRE_FIX_SAMPLE = '''
class HashGuardProbeService:
    def hash_guard_probe_find(self, request):
        details = []
        for name in request.fragment_names:
            results = self._manifest.search(name, limit=10)
            for result in results:
                details.append({"name": result["name"], "hash": result["itemHash"]})
        requested = [detail["hash"] for detail in details if detail.get("hash")]
        return self._hash_guard_probe_replace(self._current, requested)

    def _hash_guard_probe_replace(self, current, fragment_hashes):
        definition = self._manifest.get_item_definition(current.subclass_item_hash) or {}
        entries = (definition.get("sockets") or {}).get("socketEntries", [])
        updated = {}
        for socket_index, plug_hash in zip(current.fragment_indices, fragment_hashes):
            entry = entries[socket_index]
            accepted = entry.get("singleInitialItemHash", 0) == plug_hash
            for plug_set_hash in {entry.get("reusablePlugSetHash", 0)}:
                plug_set = self._manifest.get_definition("DestinyPlugSetDefinition", plug_set_hash) or {}
                if any(
                    item.get("plugItemHash", 0) == plug_hash
                    for item in plug_set.get("reusablePlugItems", [])
                ):
                    accepted = True
            updated[socket_index] = plug_hash
        return updated
'''

# 同一段代码**修好之后**的样子（`hash_variants` 两套都算上）。
_FIXED_SAMPLE = '''
class HashGuardProbeFixedService:
    def hash_guard_probe_fixed_find(self, request):
        details = []
        for name in request.fragment_names:
            results = self._manifest.search(name, limit=10)
            for result in results:
                details.append({"name": result["name"], "hash": result["itemHash"]})
        requested = [detail["hash"] for detail in details if detail.get("hash")]
        return self._hash_guard_probe_fixed_replace(self._current, requested)

    def _hash_guard_probe_fixed_replace(self, current, fragment_hashes):
        definition = self._manifest.get_item_definition(current.subclass_item_hash) or {}
        entries = (definition.get("sockets") or {}).get("socketEntries", [])
        updated = {}
        for socket_index, plug_hash in zip(current.fragment_indices, fragment_hashes):
            variants = hash_variants(plug_hash)
            entry = entries[socket_index]
            accepted = entry.get("singleInitialItemHash", 0) in variants
            updated[socket_index] = plug_hash
        return updated
'''


def test_guard_bites_on_the_fragment_bug_shape() -> None:
    """守门咬过一次才算数：把修复前后的碎片代码塞进索引，看它红不红。

    注入方式是把源码当**额外源文件**加进同一份索引（不往仓库里写文件、不用改
    `destiny_mcp/`）—— 这样 `hash_variants`、`self._manifest.search` 这些线索都按真实
    仓库解析，判定结果和真代码一致。2026-10 实测：修复前是 `mismatch`，修复后是 `widened`。
    """
    pre = _scan_tree({Path("/virtual/hash_guard_pre.py"): _PRE_FIX_SAMPLE})
    pre_hits = [site for site in pre if site.kind == "mismatch" and "hash_guard_pre" in site.path]
    assert len(pre_hits) == 2, (
        "修复前的碎片代码（`entry.get('singleInitialItemHash', 0) == plug_hash` 这类）"
        f"必须被判红，实际只抓到 {[site.expr for site in pre_hits]}。"
        "判据退化了 —— 这道闸现在拦不住第三次咬人的那个 bug。"
    )

    fixed = _scan_tree({Path("/virtual/hash_guard_fixed.py"): _FIXED_SAMPLE})
    fixed_hits = [site for site in fixed if "hash_guard_fixed" in site.path]
    assert fixed_hits and all(site.kind in {"widened", "aligned"} for site in fixed_hits), (
        "`hash_variants` 版本必须被认成归一过，不能判红也不能判可疑："
        f"{[(site.expr, site.kind) for site in fixed_hits]}"
    )


def test_scan_is_deterministic() -> None:
    """净扫描结果不许依赖文件遍历顺序 / 字典顺序（干净 HOME、任何机器都一样）。"""
    first = _scan_tree()
    second = _scan_tree()
    assert first == second
    assert first == sorted(set(first))
