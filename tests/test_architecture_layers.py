"""单向依赖与「工具↔服务」契约的守门测试。

三条规则，每条都对应一次真实的架构审查发现：

1. **分层单向**（`_LAYERS`）：只能从上往下 import。审查时抓到 `utils/item_parser.py`
   从最底层反向 import `build.constants` / `manifest` / `services.armor_payload`，
   而它自己的 docstring 写着"no service logic" —— 名字（utils）与依赖（领域解析器）矛盾，
   已挪到 `services/item_parser.py`。分层表里没登记的模块会让测试失败：加新模块时要先想清楚它属于哪层。
2. **没有环**：任何两个模块不许互相 import（Tarjan SCC 必须全是单点）。
3. **`TYPE_CHECKING` 块里的 import 不算**：那是类型标注用的，运行时没有耦合
   （`service_context.py` 就是靠这个把 29 个服务的类型收在一处而不到处 import）。
4. **服务定位器的 key 必须声明过**：tools 用 `svc["xxx_svc"]` 取服务（160+ 次引用、
   24 个 key 全是字符串）。写错一个字母以前要到运行时才炸，现在必须在
   `service_context.ServiceContext` 里声明过。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "destiny_mcp"

# 层号越小越底层：只能从大数字 import 小数字（同层可以互相 import）。
_LAYERS: dict[str, int] = {
    # 0 纯基础：不依赖项目里任何东西（除了彼此）
    "config": 0,
    "error_codes": 0,
    "exceptions": 0,
    "logging_config": 0,
    "models": 0,
    "utils": 0,
    "vocabulary": 0,
    # 1 基础设施：Manifest / Bungie 客户端 / 账号解析 / 类型容器 / 实测事实表
    "activity_stats": 1,
    "audit": 1,
    "build_contracts": 1,
    "bungie_client": 1,
    # 随包分发的**事实表**（计数器 hash → 模式/周期、选取率快照…）：没有逻辑、要被各层共用，
    # 所以放这一层。`data/` 里只许放"实测出来的对照关系"，有判断的仍然归 services/。
    "data": 1,
    "manifest": 1,  # manifest*.py 全部按前缀归到这一层
    "oauth_setup": 1,
    "player_resolver": 1,
    "service_context": 1,
    "wishlist_data": 1,
    # 2 领域层（纯计算，可被服务和工具复用）
    "build": 2,
    "build_import": 2,
    "rag": 2,
    # 3 服务层：账号读写、外部数据、形状工厂
    "services": 3,
    # 4 工具层：MCP 门面（只做分派、守卫与话术）
    "tools": 4,
    # 5 装配层
    "server": 5,
    "__main__": 5,
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SOURCE_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer(module: str) -> int | None:
    parts = module.split(".")
    if len(parts) == 1:  # destiny_mcp/__init__.py
        return 0
    head = parts[1]
    if head in _LAYERS:
        return _LAYERS[head]
    if head.startswith("manifest"):  # manifest_armor.py / manifest_lookup.py …
        return _LAYERS["manifest"]
    return None


def _modules() -> dict[str, Path]:
    return {
        _module_name(path): path
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _type_checking_lines(tree: ast.AST) -> list[tuple[int, int]]:
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def _resolve(current: str, node: ast.ImportFrom, is_package: bool) -> str | None:
    if node.level == 0:
        module = node.module or ""
        return module if module.startswith("destiny_mcp") else None
    base = current.split(".") if is_package else current.split(".")[:-1]
    up = node.level - 1
    prefix = base[: len(base) - up] if up else base
    if node.module:
        prefix = prefix + node.module.split(".")
    return ".".join(prefix)


def _import_graph() -> dict[str, set[str]]:
    """模块级依赖图；`TYPE_CHECKING` 块里的导入按规则 3 排除。"""
    modules = _modules()
    packages = {
        module for module in modules if (modules[module].name == "__init__.py")
    }
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _type_checking_lines(tree)
        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.ImportFrom):
                if any(start <= node.lineno <= end for start, end in skip):
                    continue
                target = _resolve(module, node, module in packages)
            elif isinstance(node, ast.Import):
                if any(start <= node.lineno <= end for start, end in skip):
                    continue
                for alias in node.names:
                    if alias.name.startswith("destiny_mcp"):
                        graph[module].add(alias.name)
                continue
            if not target or not target.startswith("destiny_mcp"):
                continue
            if target in modules or any(m.startswith(target + ".") for m in modules):
                graph[module].add(target)
    return graph


def test_every_module_is_assigned_a_layer() -> None:
    """新模块必须登记层号：没登记的会让规则 1 悄悄失效。"""
    unassigned = sorted(
        module for module in _modules() if _layer(module) is None
    )

    assert not unassigned, (
        "这些模块还没有分层（请在 tests/test_architecture_layers.py 的 _LAYERS 里登记）："
        f"{unassigned}"
    )


def test_imports_only_go_downwards() -> None:
    """规则 1：依赖只能从上层指向下层。"""
    violations = []
    for source, targets in _import_graph().items():
        source_layer = _layer(source)
        for target in targets:
            target_layer = _layer(target)
            if target_layer is None or source_layer is None:
                continue
            if target_layer > source_layer:
                violations.append(
                    f"{source}（层 {source_layer}）→ {target}（层 {target_layer}）"
                )

    assert not violations, "反向依赖（下层 import 上层）：\n  " + "\n  ".join(violations)


def test_no_dependency_cycles() -> None:
    """规则 2：模块之间不许互相 import。"""
    graph = _import_graph()
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    counter = [0]
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.get(node, ())):
            if neighbour not in index:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component = []
            while True:
                popped = stack.pop()
                on_stack.discard(popped)
                component.append(popped)
                if popped == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    sys.setrecursionlimit(10000)
    for module in sorted(graph):
        if module not in index:
            visit(module)

    assert not cycles, "存在依赖环：\n  " + "\n  ".join(" ↔ ".join(c) for c in cycles)


def _service_context_keys() -> set[str]:
    tree = ast.parse((SOURCE_ROOT / "service_context.py").read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ServiceContext":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    keys.add(statement.target.id)
    return keys


def test_tools_only_use_declared_service_keys() -> None:
    """规则 4：`svc["xxx_svc"]` 的 key 必须在 ServiceContext 里声明过。"""
    declared = _service_context_keys()
    pattern = re.compile(r'(?:svc|services|context|ctx)\s*(?:\[|\.get\(\s*)["\']([a-z_]+)["\']')
    used: dict[str, list[str]] = {}
    for path in sorted((SOURCE_ROOT / "tools").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for key in pattern.findall(line):
                if key.endswith("_svc") or key in declared:
                    used.setdefault(key, []).append(f"{path.name}:{line_number}")

    undeclared = sorted(key for key in used if key not in declared)

    assert not undeclared, (
        "tools 用了 ServiceContext 里没声明的服务 key（写错了要到运行时才炸）：\n  "
        + "\n  ".join(f"{key} ← {used[key][:3]}" for key in undeclared)
    )
