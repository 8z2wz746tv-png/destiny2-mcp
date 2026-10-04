"""Skill 文档与代码的一致性。

`skills/destiny2-mcp/references/routing.md` 是给 Agent 看的决策表。文档最怕的是
「写得挺细，但已经跟代码不一样了」—— 这种错误没人会发现，因为文档不会报错。

这里把文档当成一份需要校验的数据来读：

1. 索引里每个工具列出的 intent，必须与代码里的 Literal 完全一致（不多不少）；
2. 参数归属表里的每一句断言，必须在 `_param_contracts.PARAMETER_OWNERS` 里成立；
3. 写入 intent 清单必须与 `_requests.WRITE_INTENTS` 完全一致；
4. 社区分类必须与 `starside_service.CATEGORIES` 完全一致；
5. 行为语料里的每条 (工具, intent) 都必须能在索引里查到。

任何一条红了都说明「该改文档了」，而不是「该放宽容差」。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import yaml

from destiny_mcp.services.starside_service import CATEGORIES
from destiny_mcp.tools import _param_contracts as contracts
from destiny_mcp.tools import _requests, assistants

ROOT = Path(__file__).parents[1]
SKILL_ROOT = ROOT / "skills" / "destiny2-mcp"
ROUTING = SKILL_ROOT / "references" / "routing.md"

TOOL_NAMES = (
    "player_assistant",
    "inventory_assistant",
    "weapon_assistant",
    "build_assistant",
    "loadout_assistant",
    "subclass_assistant",
    "activity_assistant",
    "world_assistant",
)

BACKTICKED = re.compile(r"`([^`]+)`")
TOOL_HEADING = re.compile(r"^###\s+`(\w+_assistant)`")
ANY_HEADING = re.compile(r"^#{2,3}\s+")


def _routing_text() -> str:
    return ROUTING.read_text(encoding="utf-8")


def _declared() -> dict[str, set[str]]:
    """代码里每个工具声明支持的 intent。"""
    return {
        name: set(contracts.declared_intents(getattr(assistants, name)))
        for name in TOOL_NAMES
    }


def _cell_tokens(cell: str) -> list[str]:
    return BACKTICKED.findall(cell)


def _first_cell_tokens(row: str) -> list[str]:
    return _cell_tokens(row.strip().strip("|").split("|")[0])


def _index_sections() -> dict[str, list[str]]:
    """按 `### \\`工具名\\`` 切开索引，返回每个工具名下的表格行。"""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in _routing_text().splitlines():
        heading = TOOL_HEADING.match(line)
        if heading:
            current = heading.group(1)
            sections.setdefault(current, [])
            continue
        if ANY_HEADING.match(line):
            current = None
            continue
        if current and line.startswith("|"):
            sections[current].append(line)
    return sections


def _index_intents() -> dict[str, set[str]]:
    return {
        tool: {token for row in rows for token in _first_cell_tokens(row)}
        for tool, rows in _index_sections().items()
    }


def _table_rows(section_prefix: str) -> list[list[str]]:
    """取某个 `##` 小节里的表格行，按 | 切成单元格。"""
    rows: list[list[str]] = []
    inside = False
    for line in _routing_text().splitlines():
        if line.startswith("## "):
            inside = line.startswith(section_prefix)
            continue
        if inside and line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def test_platform_neutral_skill_has_required_entrypoints() -> None:
    entry = SKILL_ROOT / "SKILL.md"
    assert entry.is_file()
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("---\nname: destiny2-mcp")
    for reference in (
        "routing.md",
        "evidence-and-completeness.md",
        "community-builds.md",
        "account-loadouts.md",
        "execution-safety.md",
    ):
        assert (SKILL_ROOT / "references" / reference).is_file()
        assert f"references/{reference}" in text


def test_routing_index_covers_every_declared_intent() -> None:
    """索引与代码一一对应：漏一个（Agent 不知道有这个入口）或者多一个
    （Agent 调到一个不存在的入口）都算失败。"""
    declared = _declared()
    index = _index_intents()

    assert set(index) == set(TOOL_NAMES), (
        f"索引里缺少这些工具的章节：{sorted(set(TOOL_NAMES) - set(index))}"
    )

    for tool, intents in declared.items():
        assert not intents - index[tool], (
            f"routing.md 的 {tool} 章节没有列出：{sorted(intents - index[tool])}"
        )
        assert not index[tool] - intents, (
            f"routing.md 的 {tool} 章节列了不存在的 intent：{sorted(index[tool] - intents)}"
        )


def test_routing_parameter_table_matches_the_generator() -> None:
    """参数表是生成出来的：文档里那一块必须与 `render_parameter_table()` 逐字一致。

    表格有 90 行、每行都在声明「谁读这个参数」，手写必然腐烂。所以它由代码生成、
    由这里比对；要改就改 `_param_contracts.py`，然后跑
    `python -m destiny_mcp.tools._param_contracts --write-doc` 重新生成。
    """
    text = _routing_text()
    assert contracts.BLOCK_START in text, "routing.md 缺少参数表起始标记"
    assert contracts.BLOCK_END in text, "routing.md 缺少参数表结束标记"
    block = text.split(contracts.BLOCK_START, 1)[1].split(contracts.BLOCK_END, 1)[0].strip()

    assert block == contracts.render_parameter_table(), (
        "routing.md 里的参数表和 _param_contracts.PARAMETER_OWNERS 不一致；"
        "运行 python -m destiny_mcp.tools._param_contracts --write-doc 重新生成"
    )


def test_routing_write_intents_match_the_single_source() -> None:
    """写入清单必须与 `_requests.WRITE_INTENTS` 完全一致：少写一个，
    文档就漏掉一次必须的确认提醒。"""
    marker = "都会改变账号状态"
    text = _routing_text()
    assert marker in text
    listed = set(_cell_tokens(text.split(marker, 1)[0].rsplit("\n\n", 1)[-1]))

    assert listed == set(_requests.WRITE_INTENTS), (
        f"routing.md 少写：{sorted(set(_requests.WRITE_INTENTS) - listed)}；"
        f"多写：{sorted(listed - set(_requests.WRITE_INTENTS))}"
    )


def test_routing_community_categories_match_the_service() -> None:
    listed = {
        tokens[0]
        for row in _table_rows("## 四、社区资料")
        if (tokens := _first_cell_tokens("|" + "|".join(row) + "|"))
    }

    assert listed == set(CATEGORIES), (
        f"routing.md 少写：{sorted(set(CATEGORIES) - listed)}；"
        f"多写：{sorted(listed - set(CATEGORIES))}"
    )


def _behavior_cases() -> list[dict[str, str]]:
    cases = yaml.safe_load(
        (ROOT / "tests" / "agent_behavior_cases.yaml").read_text(encoding="utf-8")
    )
    assert len(cases) >= 8
    return cases


def test_cross_agent_behavior_cases_are_machine_readable() -> None:
    declared = _declared()
    for case in _behavior_cases():
        assert {"id", "prompt", "expected_tool", "expected_intent"} <= case.keys()
        assert case["expected_tool"] in TOOL_NAMES
        assert case["expected_intent"] in declared[case["expected_tool"]], (
            f"用例 {case['id']} 期望的 {case['expected_tool']}({case['expected_intent']}) 不存在"
        )


def test_behavior_cases_are_covered_by_the_routing_index() -> None:
    """语料里期望的每条路由都要能在索引里查到 —— 新增用例会逼着文档跟上。"""
    index = _index_intents()
    missing = [
        f"{case['id']}: {case['expected_tool']}({case['expected_intent']})"
        for case in _behavior_cases()
        if case["expected_intent"] not in index.get(case["expected_tool"], set())
    ]

    assert not missing, f"这些语料用例的路由没有写进索引：{missing}"


# ── 渲染 skill：文档里的每个字段都得有出处，每条调用都得真的存在 ──────────────
# `skills/destiny2-render/` 教模型把工具结果渲染成卡片。它和 routing.md 是同一类风险：
# 文档写得挺细，但**字段名/调用写错了不会有任何报错** —— 模型会渲染出一张空白卡。
# 这里把"每个字段的出处"当成数据读：出处的调用必须真的存在，参数必须是该 intent 认领的。
# （字段路径本身能不能解析，只有真机知道：`scripts/verify_render_fields.py` 干那件事。）

RENDER_ROOT = ROOT / "skills" / "destiny2-render"
RENDER_CALLS = re.compile(r"\b(\w+_assistant)\(([^)\n]*)")
RENDER_KWARG = re.compile(r"(\w+)\s*=")
RENDER_INTENT = re.compile(r'intent="([^"]+)"')


def _render_docs() -> list[Path]:
    return sorted(RENDER_ROOT.rglob("*.md"))


def _render_reference_files() -> set[str]:
    return {path.name for path in (RENDER_ROOT / "references").glob("*.md")}


def test_render_skill_has_its_entrypoints() -> None:
    """渲染 skill 要能被宿主当成一份独立 skill 加载：frontmatter 对、参考文档都得从
    SKILL.md 指得到（加了文档忘了指 = 模型永远读不到它）。"""
    entry = RENDER_ROOT / "SKILL.md"
    assert entry.is_file(), "渲染 skill 缺少 SKILL.md"
    text = entry.read_text(encoding="utf-8")

    assert text.startswith("---\nname: destiny2-render"), "frontmatter 的 name 要和目录名一致"
    assert "description:" in text
    for name in _render_reference_files():
        assert f"references/{name}" in text, f"SKILL.md 没有指向 references/{name}"


def test_render_skill_only_names_real_intents_and_parameters() -> None:
    """文档里出现的每条 `tool(intent=…, 参数=…)` 都必须真实存在：

    - 工具名必须是八个聚合工具之一；
    - intent 必须是那个工具**声明**的；
    - 参数必须真的在工具的签名里（写一个不存在的参数，宿主在 schema 层就被拒）；
    - 每个参数必须是那个 intent **认领**的（传了不读的参数会被 `ignored_parameter` 拒掉，
      文档里却写着它 —— 模型照抄就会吃一个拒绝）。

    只解析文档（离线），不碰真机；字段路径能否解析由 `scripts/verify_render_fields.py` 负责。
    """
    declared = _declared()
    signatures = {
        name: set(inspect.signature(getattr(assistants, name)).parameters)
        for name in TOOL_NAMES
    }
    problems: list[str] = []
    seen = 0
    for path in _render_docs():
        for match in RENDER_CALLS.finditer(path.read_text(encoding="utf-8")):
            tool, raw = match.group(1), match.group(2)
            if tool not in TOOL_NAMES:
                problems.append(f"{path.name}: 不是八个聚合工具之一：{tool}")
                continue
            intent_match = RENDER_INTENT.search(raw)
            if intent_match is None:
                problems.append(f"{path.name}: 调用没写 intent：{tool}({raw})")
                continue
            intent = intent_match.group(1)
            seen += 1
            if intent not in declared[tool]:
                problems.append(f"{path.name}: {tool} 没有 intent={intent}")
                continue
            for name in RENDER_KWARG.findall(raw):
                if name == "intent":
                    continue
                if name not in signatures[tool]:
                    problems.append(f"{path.name}: {tool} 没有参数 {name}")
                elif not contracts.intent_accepts_parameter(tool, intent, name):
                    problems.append(
                        f"{path.name}: {tool}(intent={intent}) 不读参数 {name}"
                    )

    assert seen >= 20, f"只解析到 {seen} 条调用，正则或文档结构变了，先看这里"
    assert not problems, "渲染 skill 里的调用对不上代码：\n" + "\n".join(problems)


# ── 宿主交付包装：HTML 得先包对，宿主才渲染 ───────────────────────────────────
# 实测（2026-10-05，豆包客户端）：同一个模型、同一套卡片数据，只换代码块的起始行 ——
# 起始行写成 ` ```html type="renderer" ` 时渲染成可视化卡片；写成普通的 ` ```html `
# 或裸贴 HTML 时，整段 HTML 被**原样当源码显示**（用户亲眼看到另一个对话里吐出一大坨
# `<div style=…>`）。skill 把"渲染哪些块、用哪些字段、什么 HTML 合法、图挂了怎么办"
# 都教了，唯独没写"怎么把这个 HTML 交给宿主"，于是模型照 skill 做出来的卡片在豆包里
# 是一坨源码 —— 卡在最后一公里。这条把包装协议变成可执行断言：
# ① 两个入口（SKILL.md 的三档表、html-conventions.md 的约定）都要写出宿主认的起始行；
# ② 都要说明它是**宿主特有**的（缺了这句，模型会把它当成 HTML 的通用写法带到别的宿主）；
# ③ 都要写反面（普通 ```html / 裸 HTML 不渲染）—— 不写就会重犯；
# ④ skill 里的 HTML 示例**只允许**用正确的起始行：示例写错比不写示例更糟（模型会照抄）。

#: 宿主认的包装标记。改它之前先看 SKILL.md §1 与 html-conventions.md §零：
#: 这是**豆包**认的行，不是 HTML 的通用协议。
RENDER_WRAPPER = '```html type="renderer"'
#: "包装是宿主特有的"。两份文档各自的语言里都要有这句。
RENDER_WRAPPER_HOST_SPECIFIC = {
    "SKILL.md": "host-specific",
    "references/html-conventions.md": "宿主特有",
}
#: 反面：信息串只有 `html` 的普通围栏（`type="renderer"` 不算），或干脆裸贴 HTML。
RENDER_WRAPPER_PLAIN = re.compile(r'```html(?!\s*type="renderer")')
#: "那样交付不会被渲染"——两份文档各自的语言里的写法。
RENDER_WRAPPER_FAILURE = {
    "SKILL.md": "shown as source text",
    "references/html-conventions.md": "不渲染",
}
#: 围栏起始行：`^```<信息串>`。行内代码用的 4 个以上反引号不算（信息串首字符不是字母）。
FENCE_LINE = re.compile(r"^\s*```(.*)$")


def _flat(text: str) -> str:
    """折掉换行，好让"同一句话"能跨行匹配。"""
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """取 `## <heading>` 到下一个二级标题之间的正文。"""
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_render_skill_wires_the_delivery_wrapper_into_the_host_tiers() -> None:
    """SKILL.md 的三档表里，"能渲染 HTML"那一档必须先定交付包装，并给出豆包的值。

    只写在别的章节不够：模型是照着那一档决定"这个宿主该怎么发"的，那里没写就等于没定协议。
    """
    section = _section((RENDER_ROOT / "SKILL.md").read_text(encoding="utf-8"), "## 1.")
    flat = _flat(section)

    assert RENDER_WRAPPER in section, (
        f"SKILL.md §1 的三档里没写宿主包装协议（要出现 {RENDER_WRAPPER}）："
        "「能渲染 HTML」这一档不先定包装，模型照 skill 做出来的 HTML 会被宿主当源码显示"
    )
    assert RENDER_WRAPPER_HOST_SPECIFIC["SKILL.md"] in flat, (
        "SKILL.md §1 没说明包装是宿主特有的 —— 模型会把它当成 HTML 的通用写法带到别的宿主"
    )
    assert RENDER_WRAPPER_PLAIN.search(flat), (
        "SKILL.md §1 没写反面：普通的 ```html / 裸 HTML 在豆包里不渲染（不写就会重犯）"
    )


def test_render_skill_states_the_delivery_wrapper_in_both_entrypoints() -> None:
    """包装协议两个入口都要有，而且口径一致：SKILL.md（三档）与 html-conventions.md（约定）。

    blocks.md 的骨架是照它交付的，所以也得指得到这条（它的示例本身由下面那条守门盯着）。
    """
    documents = {
        name: (RENDER_ROOT / name).read_text(encoding="utf-8")
        for name in ("SKILL.md", "references/html-conventions.md")
    }
    for name, text in documents.items():
        flat = _flat(text)
        assert RENDER_WRAPPER in text, f"{name} 没写宿主认的包装起始行 {RENDER_WRAPPER}"
        assert RENDER_WRAPPER_HOST_SPECIFIC[name] in flat, (
            f"{name} 没说明包装是宿主特有的（不是 HTML 的通用写法）"
        )
        assert RENDER_WRAPPER_PLAIN.search(flat), (
            f"{name} 没写反面：普通的 ```html / 裸 HTML 在豆包里不会渲染"
        )
        assert RENDER_WRAPPER_FAILURE[name] in flat, (
            f"{name} 没写清那样交付的后果（会被原样当源码显示）"
        )

    blocks = (RENDER_ROOT / "references" / "blocks.md").read_text(encoding="utf-8")
    assert RENDER_WRAPPER in blocks and "html-conventions.md" in blocks, (
        "blocks.md 的骨架就是交付内容：要写明按 html-conventions.md 的包装交付，示例用正确的起始行"
    )


def test_render_skill_html_examples_use_the_host_wrapper() -> None:
    """skill 里每一段 HTML 示例的起始行都必须是宿主认的那一行。

    模型是照抄示例的：示例写成普通的 ```html，豆包就把整段当源码显示 ——
    「示例本身错了比没示例更糟」。
    """
    offenders: list[str] = []
    examples = 0
    for path in sorted(RENDER_ROOT.rglob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = FENCE_LINE.match(line)
            if match is None:
                continue
            info = match.group(1).strip()
            if not info or info.split()[0] != "html":
                continue
            if info == 'html type="renderer"':
                examples += 1
            else:
                offenders.append(f"{path.relative_to(RENDER_ROOT)}:{lineno}: ```{info}")

    assert not offenders, (
        "渲染 skill 里的 HTML 示例只能用它教的宿主包装起始行（示例会被照抄，写错就吐源码）：\n"
        + "\n".join(offenders)
    )
    assert examples >= 5, f"只解析到 {examples} 段 HTML 示例，先看示例结构是不是变了"
