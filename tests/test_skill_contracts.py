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
