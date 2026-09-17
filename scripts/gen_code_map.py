"""把 `destiny_mcp/` 的顶层模块按分层表分组，写进 `AGENTS.md` 的代码地图标记区。

清单**只有路径、不写职责**：职责那一半（`### 各模块一句话职责`）是手写的，给人看；
这一半是机器算的，给守门测试咬人用 —— 忘了跑生成脚本，`tests/test_agent_docs.py` 会红。

分层号不在这里再抄一份：直接 import `tests/test_architecture_layers.py` 的 `_LAYERS`
与 `_modules()`，那是「新顶层模块该属于哪一层」的唯一出处。层标题也现读源码里
`_LAYERS` 上方那几行 `# <数字> <标题>` 注释，改表就跟着改。

用法（在仓库根目录）：

    .venv/bin/python scripts/gen_code_map.py            # 写进 AGENTS.md
    .venv/bin/python scripts/gen_code_map.py --print    # 只打印，不落盘
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DOC = ROOT / "AGENTS.md"
LAYER_TEST = ROOT / "tests" / "test_architecture_layers.py"

BLOCK_START = "<!-- code-map:begin -->"
BLOCK_END = "<!-- code-map:end -->"

# 分层标题的兜底：源码注释被挪走时也不至于没有标题（不构成第二份分层表）。
_LAYER_FALLBACK = {0: "纯基础", 1: "基础设施", 2: "领域层", 3: "服务层", 4: "工具层", 5: "装配层"}


def _layer_module():
    """按路径加载分层表所在的测试模块（它只 import 标准库，加载无副作用）。"""
    spec = importlib.util.spec_from_file_location("_d2_architecture_layers", LAYER_TEST)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件被挪走时才可能
        raise SystemExit(f"✗ 读不到分层表：{LAYER_TEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _layer_titles() -> dict[int, str]:
    """从 `_LAYERS` 字面量范围内抓 `# 0 纯基础：…` 这类整行注释当层标题。"""
    text = LAYER_TEST.read_text(encoding="utf-8")
    start = text.index("_LAYERS")
    end = text.index("\n}", start)
    titles: dict[int, str] = {}
    for line in text[start:end].splitlines():
        match = re.match(r"\s*#\s*(\d)\s+(\S.*)$", line)
        if match:
            titles[int(match.group(1))] = match.group(2).strip()
    return {**_LAYER_FALLBACK, **titles}


def module_inventory() -> list[tuple[int, str, str]]:
    """返回 `(层号, 排序键, 仓库相对路径)`；一张真实存在的模块表，不是手抄的。"""
    layers = _layer_module()
    assert layers._LAYERS, "分层表为空：先看 tests/test_architecture_layers.py"
    seen: dict[str, tuple[int, str]] = {}
    for module, path in layers._modules().items():
        parts = module.split(".")
        if len(parts) == 1:  # destiny_mcp/__init__.py
            key, target = "destiny_mcp", path
        else:
            head = parts[1]
            key = head
            # 单文件模块给文件，包给目录；head 是 manifest_armor 这类前缀模块时同样命中。
            package = layers.SOURCE_ROOT / head
            target = path if path.parent == layers.SOURCE_ROOT else package
        layer = layers._layer(module)
        if layer is None:  # 分层表漏登记 → 架构测试已经会红，这里也不许悄悄跳过
            raise SystemExit(f"✗ {module} 没有登记层号，先去 tests/test_architecture_layers.py 补")
        # 包给目录（带斜杠，一眼看出不是单文件），单文件给文件路径。
        rel = f"destiny_mcp/{target.name}/" if target.is_dir() else f"destiny_mcp/{target.name}"
        seen[key] = (layer, rel)
    inventory = [(layer, rel, rel) for (layer, rel) in seen.values()]
    return sorted(inventory, key=lambda row: (row[0], row[1]))


def render_code_map() -> str:
    titles = _layer_titles()
    inventory = module_inventory()
    blocks: list[str] = []
    for layer in sorted({row[0] for row in inventory}):
        rows = [row for row in inventory if row[0] == layer]
        blocks.append(f"**第 {layer} 层：{titles.get(layer, f'第 {layer} 层')}**")
        blocks.extend(f"- `{rel}`" for _, _, rel in rows)
    return "\n".join(blocks)


def main(argv: list[str]) -> int:
    body = render_code_map()
    if "--print" in argv:
        print(body)
        return 0

    text = AGENTS_DOC.read_text(encoding="utf-8")
    if BLOCK_START not in text or BLOCK_END not in text:
        print(f"✗ AGENTS.md 里找不到标记：{AGENTS_DOC}")
        return 1
    head, rest = text.split(BLOCK_START, 1)
    _, tail = rest.split(BLOCK_END, 1)
    AGENTS_DOC.write_text(f"{head}{BLOCK_START}\n{body}\n{BLOCK_END}{tail}", encoding="utf-8")
    print(f"✔ 已更新 {AGENTS_DOC}（{len(module_inventory())} 个模块）")
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工运行
    raise SystemExit(main(sys.argv[1:]))
