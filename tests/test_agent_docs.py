"""`AGENTS.md` 三件套 + ADR 台账的守门测试。

文档漂移都是慢慢发生的：加了模块忘了补职责、删了文档忘了删索引、写完 ADR 忘了编号。
四条都做成可执行断言：

1. 代码地图标记区 == `scripts/gen_code_map.py` 现算的结果（忘了跑生成脚本 → 红）；
2. 生成清单 ↔ 手写职责表一一对应（漏写一行、或表里有清单外的模块 → 红）；
3. `## 文档索引` 里的路径都存在，`docs/**/*.md`（`docs/adr/` 除外）都登记了；
4. `docs/adr/` 编号连续无重复、每条有 Status/Date、README 与实际文件双向一致。

这里只读文件、不写文件：生成脚本要人（或 CI）显式跑，测试只负责判定它跑没跑。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
AGENTS_DOC = ROOT / "AGENTS.md"
GEN_CODE_MAP = ROOT / "scripts" / "gen_code_map.py"
DOCS_ROOT = ROOT / "docs"
ADR_DIR = DOCS_ROOT / "adr"

CODE_MAP_START = "<!-- code-map:begin -->"
CODE_MAP_END = "<!-- code-map:end -->"
RESPONSIBILITY_HEADING = "### 各模块一句话职责"
DOC_INDEX_HEADING = "## 文档索引"

_LISTED_MODULE_RE = re.compile(r"^- `([^`]+)`$")
_INDEX_PATH_RE = re.compile(r"`([^`]+\.md)`")
_ADR_FILE_RE = re.compile(r"^(?:ADR-)?(\d{3})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
# ADR 台账里"被引用到的 ADR 文件"；占位符写成 NNN-… 正是为了不落进这个模式。
_ADR_MENTION_RE = re.compile(r"\b(\d{3}-[a-z0-9-]+\.md)\b")
_ADR_NON_ENTRIES = {"README.md", "TEMPLATE.md"}


def _agents_text() -> str:
    return AGENTS_DOC.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """取 heading 到下一个 `##` / `###` 标题之间的正文。"""
    assert heading in text, f"AGENTS.md 缺标题行：{heading}"
    start = text.index(heading)
    lines = []
    for line in text[start + len(heading) :].splitlines():
        if line.startswith("## ") or line.startswith("### "):
            break
        lines.append(line)
    return "\n".join(lines)


def _code_map_body() -> str:
    text = _agents_text()
    assert CODE_MAP_START in text, f"AGENTS.md 缺标记 {CODE_MAP_START}"
    assert CODE_MAP_END in text, f"AGENTS.md 缺标记 {CODE_MAP_END}"
    return text.split(CODE_MAP_START, 1)[1].split(CODE_MAP_END, 1)[0].strip("\n")


def _rendered_code_map() -> str:
    spec = importlib.util.spec_from_file_location("_d2_gen_code_map", GEN_CODE_MAP)
    assert spec is not None and spec.loader is not None, f"加载不了生成脚本：{GEN_CODE_MAP}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_code_map()


def _listed_modules() -> list[str]:
    modules = []
    for line in _code_map_body().splitlines():
        match = _LISTED_MODULE_RE.match(line.strip())
        if match:
            modules.append(match.group(1))
    return modules


def _responsibility_modules() -> list[str]:
    modules = []
    for line in _section(_agents_text(), RESPONSIBILITY_HEADING).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"模块", ""} or set(cells[0]) <= set("-: "):
            continue
        modules.append(cells[0].strip("`"))
    return modules


def test_code_map_block_is_what_the_generator_produces() -> None:
    """忘了跑 `scripts/gen_code_map.py`（或手改了清单）就在这里红。"""
    body = _code_map_body()
    assert body.strip(), "代码地图标记区是空的：跑 .venv/bin/python scripts/gen_code_map.py"
    assert body == _rendered_code_map(), (
        "AGENTS.md 的代码地图与 scripts/gen_code_map.py 现算结果不一致："
        "跑 .venv/bin/python scripts/gen_code_map.py 重新生成（不要手改清单）"
    )


def test_every_module_has_exactly_one_responsibility_line() -> None:
    listed = _listed_modules()
    documented = _responsibility_modules()
    assert listed, "代码地图里一个模块都没有，生成脚本是不是坏了"
    assert not set(listed) - set(documented), (
        "这些模块在代码地图里但没写一句话职责，去 AGENTS.md 的"
        f"「{RESPONSIBILITY_HEADING}」补一行：{sorted(set(listed) - set(documented))}"
    )
    assert not set(documented) - set(listed), (
        "职责表里有代码地图清单外的模块（模块删了还是名字写错了）："
        f"{sorted(set(documented) - set(listed))}"
    )
    duplicated = sorted({name for name in documented if documented.count(name) > 1})
    assert not duplicated, f"职责表里同一模块写了两行：{duplicated}"


def test_doc_index_covers_every_document() -> None:
    indexed = []
    for line in _section(_agents_text(), DOC_INDEX_HEADING).splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        match = _INDEX_PATH_RE.search(line)
        assert match, f"文档索引的条目没有反引号包起来的 .md 路径：{line}"
        indexed.append(match.group(1))

    assert indexed, "文档索引是空的"
    for relative in indexed:
        assert (ROOT / relative).is_file(), f"文档索引里的路径不存在：{relative}"

    actual = {
        path.relative_to(ROOT).as_posix()
        for path in DOCS_ROOT.rglob("*.md")
        if ADR_DIR not in path.parents
    }
    assert not actual - set(indexed), (
        f"docs/ 下这些文档没登记进「{DOC_INDEX_HEADING}」：{sorted(actual - set(indexed))}"
    )


def _adr_entries() -> dict[int, Path]:
    entries: dict[int, Path] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if path.name in _ADR_NON_ENTRIES:
            continue
        match = _ADR_FILE_RE.match(path.name)
        assert match, f"ADR 文件名不合规矩（`NNN-kebab-title.md`）：{path.name}"
        number = int(match.group(1))
        assert number not in entries, f"ADR 编号重复：{number}（{entries[number].name} 与 {path.name}）"
        entries[number] = path
    return entries


def test_adr_numbering_and_metadata() -> None:
    entries = _adr_entries()
    assert entries, "docs/adr/ 下一条 ADR 都没有"
    numbers = sorted(entries)
    # 现存编号不要求从 001 起（正文是从 ADR-007 开始落盘的），要求的是**不留缺口**：
    # 7 → 下一个必须是 8。删文件留空号、抄错号都会在这里红。
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), (
        f"ADR 编号必须连续、不复用（推翻旧决定也留文件标 superseded；现存：{numbers}）"
    )
    for number, path in entries.items():
        text = path.read_text(encoding="utf-8")
        assert text.startswith(f"# ADR-{number:03d}:"), (
            f"{path.name} 的标题行必须写成 `# ADR-{number:03d}: 标题`"
        )
        assert re.search(r"^- Status: \S+", text, re.MULTILINE), f"{path.name} 缺 `- Status: …`"
        assert re.search(r"^- Date: \d{4}-\d{2}-\d{2}$", text, re.MULTILINE), (
            f"{path.name} 缺 `- Date: YYYY-MM-DD`"
        )
        for heading in ("## Context", "## Decision", "## Consequences"):
            assert heading in text, f"{path.name} 缺 `{heading}` 段（格式照 docs/adr/TEMPLATE.md）"


def test_adr_readme_matches_actual_files() -> None:
    readme = ADR_DIR / "README.md"
    assert readme.is_file(), "缺 docs/adr/README.md（ADR 索引与编号规矩）"
    mentioned = set(_ADR_MENTION_RE.findall(readme.read_text(encoding="utf-8")))
    actual = {path.name for path in _adr_entries().values()}
    assert not actual - mentioned, f"这些 ADR 没进 docs/adr/README.md 的索引：{sorted(actual - mentioned)}"
    assert not mentioned - actual, f"README 索引指向不存在的 ADR 文件：{sorted(mentioned - actual)}"
