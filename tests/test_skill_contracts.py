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
# 是一坨源码 —— 卡在最后一公里。
#
# ⚠️ 但包装是**宿主特有**的，而宿主不止一个：豆包验过，WorkBuddy / Codex 没有（用户明确说过
# 要适配不同 agent，格式可能各不相同）。所以这一组守门断言的是**机制**，不是某一个值：
# ① `html-conventions.md` §零 有一张**宿主表**（宿主 / 交付包装 / 依据）：至少一行实测、
#    没验过的宿主如实写"未知 + 先探测"（**不许编** —— 编一个格式比留空更坏）；
# ② 表下面要有**可操作的探测步骤**（最小一块、不带图、看"渲染还是吐源码"、定下来再发正式块）；
# ③ 要有**切换规矩**：不许跨宿主套用标记、换环境重探、结论要能复用（记回表）；
# ④ 要有**扩展位**：新宿主怎么加一行进来（加了行不用重写这一节）；
# ⑤ 豆包那行的值仍要写对 —— 但**允许的围栏是从表里实测行现推的**：以后给 WorkBuddy
#    加一行实测，示例就能用它的围栏，这一组守门都不用改。
#
# 为什么不能只断言"skill 里必须出现 type=\"renderer\""：那会把**豆包的值**写成**唯一答案**，
# 以后加 WorkBuddy 的格式反而把守门撞红 —— 守门开始阻碍扩展。断言机制则相反：表越全越绿。

#: html-conventions.md（§零 宿主表与探测步骤在这份里）。
RENDER_CONVENTIONS = "references/html-conventions.md"
#: 豆包实测的包装起始行。它是**表里的一行**，不是"唯一答案"：见 `_measured_wrappers()`。
RENDER_WRAPPER_INFO = 'html type="renderer"'
RENDER_WRAPPER = f"```{RENDER_WRAPPER_INFO}"
#: 标记本身（不带围栏）。凡是在文档里出现它的地方，都要和**宿主名**写在一起
#: —— 否则模型会把某宿主的值当成通用写法（这是"只许一个值"之外的另一半风险）。
RENDER_WRAPPER_MARK = 'type="renderer"'
#: "包装是宿主特有的"。两份文档各自的语言里都要有这句。
RENDER_WRAPPER_HOST_SPECIFIC = {
    "SKILL.md": "host-specific",
    RENDER_CONVENTIONS: "宿主特有",
}
#: 反面：信息串只有 `html` 的普通围栏（`type="renderer"` 不算），或干脆裸贴 HTML。
RENDER_WRAPPER_PLAIN = re.compile(r'```html(?!\s*type="renderer")')
#: "那样交付不会被渲染"——两份文档各自的语言里的写法。
RENDER_WRAPPER_FAILURE = {
    "SKILL.md": "shown as source text",
    RENDER_CONVENTIONS: "原样当源码显示",
}
#: 围栏起始行：`^```<信息串>`。行内代码用的 4 个以上反引号不算（信息串首字符不是字母）。
FENCE_LINE = re.compile(r"^\s*```(.*)$")
#: 宿主表单元格里的内联围栏：`` ```` ```html type="renderer" ```` `` → `html type="renderer"`。
FENCE_IN_CELL = re.compile(r"`{3,}\s*([A-Za-z][^`]*)`{3,}")
#: 实测依据里要有日期 —— 没有日期，下一个人没法判断它还算不算数。
RENDER_WRAPPER_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _flat(text: str) -> str:
    """折掉换行，好让"同一句话"能跨行匹配。"""
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """取 `## <heading>` 到下一个二级标题之间的正文。"""
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _subsection(text: str, heading: str) -> str:
    """取 `### <heading>` 到下一个二级/三级标题之间的正文。

    断言要打在**该在的那一小节**上：0.2 丢了探测步骤、别处还留着一句"探测"，
    整节扫 token 是抓不住的 —— 注入验证时踩到过（I2b/I2d 第一轮注入全绿）。
    """
    start = text.index(heading) + len(heading)
    rest = text[start:]
    ends = [pos for pos in (rest.find("\n## "), rest.find("\n### ")) if pos != -1]
    return rest[: min(ends)] if ends else rest


def _render_doc(name: str) -> str:
    return (RENDER_ROOT / name).read_text(encoding="utf-8")


def _host_table() -> tuple[list[str], list[tuple[str, str, str]]]:
    """§零 的宿主表：表头三列 + 每一行（宿主 / 交付包装 / 依据）。

    这张表是包装协议的**唯一出处**：守门从它推"哪些宿主验过、哪些还没有"，
    而不是在测试里另抄一份宿主清单（抄一份 = 加宿主时两边都要改，早晚不一致）。
    """
    lines = _section(_render_doc(RENDER_CONVENTIONS), "## 零").splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[:1] != ["宿主"]:
            continue
        rows: list[tuple[str, str, str]] = []
        for row_line in lines[index + 2:]:  # 跳过表头与 `| --- |` 分隔行
            if not row_line.strip().startswith("|"):
                break
            row = [cell.strip() for cell in row_line.strip().strip("|").split("|")]
            assert len(row) == 3, f"§零 宿主表的行必须是三列：{row_line}"
            rows.append((row[0], row[1], row[2]))
        return cells, rows
    raise AssertionError(
        f"{RENDER_CONVENTIONS} §零 没有「宿主 / 交付包装 / 依据」这张表 —— "
        "包装是宿主特有的，没有表就没有可切换的机制"
    )


def _host_row(keyword: str) -> tuple[str, str, str] | None:
    return next((row for row in _host_table()[1] if keyword in row[0]), None)


def _wrappers_in(cell: str) -> list[str]:
    """取出单元格里内联围栏的信息串：`` ```` ```html type="renderer" ```` `` → `html type="renderer"`。"""
    return [match.group(1).strip() for match in FENCE_IN_CELL.finditer(cell)]


def _measured_wrappers() -> set[str]:
    """宿主表里**实测**行（依据带日期）的围栏 —— 允许出现在示例里的起始行集合。

    从表里现推，不写死：给 WorkBuddy 加一行实测，示例就能用它的围栏。
    """
    return {
        wrapper
        for _host, cell, basis in _host_table()[1]
        if RENDER_WRAPPER_DATE.search(basis)
        for wrapper in _wrappers_in(cell)
    }


def test_render_skill_host_table_has_measured_and_unknown_rows() -> None:
    """§零 的宿主表：宿主 / 交付包装 / 依据，验过的与没验过的都要如实标。

    - 表里必须**至少有一行实测**（依据带日期）—— 否则这张表全是猜的；
    - 没验过的宿主必须写"未知 + 先探测"：**编一个格式比留空更坏**（模型会照着用；
      本仓的铁律是"没查到" ≠ "没有"、"没探成" ≠ "试过没有"）；
    - 但**验过的宿主可以直接改成实测行**（值 + 日期）—— 那是扩展路径，不是违规：
      这条守门卡的是"没验过却写了个值"，不是"只许豆包有值"。
    """
    header, rows = _host_table()
    assert header == ["宿主", "交付包装", "依据"], (
        f"§零 宿主表的三列应是 宿主 / 交付包装 / 依据，实际 {header}"
    )
    hosts = [row[0] for row in rows]
    for keyword in ("豆包", "WorkBuddy", "Codex", "未知"):
        assert any(keyword in host for host in hosts), (
            f"§零 的宿主表缺「{keyword}」那一行 —— 包装是宿主特有的，至少要有"
            "豆包（实测）、WorkBuddy 与 Codex（各家格式）、未知宿主这几类"
        )

    doubao = _host_row("豆包")
    assert doubao is not None
    assert RENDER_WRAPPER in doubao[1], (
        f"豆包那一行的交付包装写错了：实测值是 {RENDER_WRAPPER}"
    )
    assert "实测" in doubao[2] and RENDER_WRAPPER_DATE.search(doubao[2]), (
        "豆包那一行必须把依据写成「实测 + 日期 + 怎么测的」—— 它是表里唯一验过的值，"
        "没有日期别人无法判断它还算不算数"
    )

    for keyword in ("WorkBuddy", "Codex"):
        row = _host_row(keyword)
        assert row is not None
        if RENDER_WRAPPER_DATE.search(row[2]):  # 验过了：值 + 日期，走扩展路径
            assert _wrappers_in(row[1]), (
                f"「{keyword}」那一行标了实测日期，却没写它认的围栏起始行 ——"
                "日期是给一个**具体的值**作保的"
            )
            continue
        assert "未知" in row[1] and "探测" in row[1], (
            f"「{keyword}」那一行的依据里没有实测日期，包装就必须如实写「未知 —— 先探测」，"
            "不许给它编一个格式（编了比留空更坏：模型会照着用）"
        )
        assert RENDER_WRAPPER_MARK not in row[1], (
            f"「{keyword}」那一行不许把豆包的标记写成它的值 ——"
            "「不许跨宿主套用标记」正是要防这件事"
        )

    catch_all = _host_row("未知")
    assert catch_all is not None
    assert "探测" in catch_all[1] and not RENDER_WRAPPER_DATE.search(catch_all[2]), (
        "「未知宿主」那一行要**永远**留成「先探测」：没人验过的宿主是**开放集合**，"
        "它不可能变成一行实测值（没有日期，也没有围栏可写）"
    )
    assert RENDER_WRAPPER_MARK not in catch_all[1], (
        "「未知宿主」那一行不许把豆包的标记写成未知宿主的答案（换宿主 = 回到未知）"
    )


def test_render_skill_states_the_probe_and_the_cross_host_ban() -> None:
    """§零 的重点是**换宿主时的机制**：怎么探、结论怎么记、不许做什么。

    旧写法只对"其他宿主"说一句"先确认，别猜"—— 治不了换宿主：模型不知道该**怎么**确认、
    确认完**怎么记**。这条把机制钉成可执行断言（机制缺了，换宿主就退回猜）。

    断言分别打在**该在的那一小节**上（0.2 探测 / 0.3 切换规矩 / 0.4 扩展位）：整节扫 token
    抓不住"探测步骤被删、别处还留着一句探测"这种退化 —— 注入验证时第一版就是栽在这上面。
    """
    zero = _section(_render_doc(RENDER_CONVENTIONS), "## 零")
    probe = _flat(_subsection(zero, "### 0.2"))
    switch = _flat(_subsection(zero, "### 0.3"))
    extension = _flat(_subsection(zero, "### 0.4"))

    for token, why in (
        ("最小一块", "0.2 没写「先发最小一块」：模型会在不确定时先渲染一整张卡，错了就是一大坨源码"),
        ("一整张卡", "0.2 没写「别先渲染一整张卡」这条禁令"),
        ("不带图", "0.2 没写探测块不带图：图挂了会和包装不对混在一起，看不出是哪一种"),
        ("原样", "0.2 没写探测的判据（渲染成卡片 vs 原样吐源码）"),
    ):
        assert token in probe, why

    assert re.search(r"不许[^。]{0,60}带到[^。]{0,60}没验过", switch), (
        "0.3 没写反面：不许把某个宿主的标记带到没验过的宿主"
        "（豆包的 type=\"renderer\" 不是 HTML 规范属性，换宿主 = 回到未知）"
    )
    assert "换了环境" in switch and "重走" in switch, (
        "0.3 没写「换了环境要重走一遍探测」—— 上次验过、在别处验过，都不算数"
    )
    assert "不在这个环境验过就当作未知" in switch, (
        "0.3 没写清「不在这个环境验过就当作未知」这条判据"
    )
    assert "记回" in switch, (
        "0.3 没写探测结果记在哪、下次怎么用（探测结果要能被复用：记回宿主表）"
    )

    for token, why in (
        ("探测", "0.4 没写扩展位的第一步（在那个环境跑一遍探测）"),
        ("加一行", "0.4 没写「在表里加一行」—— 表会变成封闭清单，加一行就得重写这一节"),
        ("依据", "0.4 没写新那一行的依据怎么填（实测 + 日期 / 未知 —— 先探测）"),
    ):
        assert token in extension, why


def test_render_skill_wires_the_delivery_wrapper_into_the_host_tiers() -> None:
    """SKILL.md 三档表的"能渲染 HTML"那一档要接上 §零 的机制（不是只给豆包一个值）。

    只写在别的章节不够：模型是照着那一档决定"这个宿主该怎么发"的，那里没写就等于没定协议；
    而那里若只写"其他宿主先确认"，模型不知道该**怎么**确认 —— 换宿主照样卡住。
    """
    section = _section(_render_doc("SKILL.md"), "## 1.")
    flat = _flat(section)

    assert "html-conventions.md" in section and "§0" in section, (
        "SKILL.md §1 没指到 html-conventions.md §0 的宿主表 / 探测步骤"
    )
    assert RENDER_WRAPPER_HOST_SPECIFIC["SKILL.md"] in flat, (
        "SKILL.md §1 没说明包装是宿主特有的 —— 模型会把它当成 HTML 的通用写法带到别的宿主"
    )
    assert RENDER_WRAPPER in section, (
        f"SKILL.md §1 没写豆包实测的那一行（{RENDER_WRAPPER}）"
    )
    # 值不许"挂空"：每一个 type="renderer" 的**附近**（±100 字）都要出现宿主名。
    # 用带围栏的整串去找会漏 —— 正文里提到这个标记时通常不带 ```html（注入验证踩到过）。
    for match in re.finditer(re.escape(RENDER_WRAPPER_MARK), flat):
        window = flat[max(0, match.start() - 100): match.end() + 100]
        assert "Doubao" in window or "豆包" in window, (
            "SKILL.md §1 把 type=\"renderer\" 写成了没人认领的值 —— 它是**豆包**的值，"
            "要和宿主名写在一起，模型才不会当成通用写法（更不能写成唯一答案）"
        )
    for token, why in (
        ("probe", "§1 没写「没验过的宿主先探测」—— 换宿主时模型没有出路"),
        ("minimal block", "§1 没写探测要发最小一块（拿整张卡试，错了就是一大坨源码）"),
        ("no image", "§1 没写探测块不带图（图挂了会和包装不对混在一起）"),
        ("source text", "§1 没写探测判据（渲染 vs 原样吐源码）"),
        ("nobody checked", "§1 没写「不许把标记带到没人验过的宿主」"),
        ("record the result", "§1 没写探测结果要记回 §0 的表（探测结果要能被复用）"),
    ):
        assert token in flat, why
    assert RENDER_WRAPPER_PLAIN.search(flat), (
        "SKILL.md §1 没写反面：普通的 ```html / 裸 HTML 在豆包里被当源码显示（不写就会重犯）"
    )


def test_render_skill_states_the_delivery_wrapper_in_both_entrypoints() -> None:
    """两个入口口径一致：SKILL.md（§1 三档）与 html-conventions.md（§零 宿主表 + 探测）。

    只改一个入口 = 模型从另一个入口进来时又退回"先确认，别猜"。blocks.md 的骨架是照它交付的，
    也要指得到这条（它的 5 段示例本身由下面那条守门盯着）。
    """
    for name in ("SKILL.md", RENDER_CONVENTIONS):
        flat = _flat(_render_doc(name))
        assert RENDER_WRAPPER_HOST_SPECIFIC[name] in flat, (
            f"{name} 没说明包装是宿主特有的（不是 HTML 的通用写法）"
        )
        assert RENDER_WRAPPER_PLAIN.search(flat), (
            f"{name} 没写反面：普通的 ```html / 裸 HTML 在那个宿主不会渲染"
        )
        assert RENDER_WRAPPER_FAILURE[name] in flat, (
            f"{name} 没写清那样交付的后果（会被原样当源码显示）"
        )
        assert "探测" in flat or "probe" in flat, (
            f"{name} 没写探测这条机制 —— 换到没验过的宿主时模型没有出路"
        )

    blocks = _render_doc("references/blocks.md")
    assert RENDER_WRAPPER in blocks and "html-conventions.md" in blocks, (
        "blocks.md 的骨架就是交付内容：要写明按 html-conventions.md 的包装交付"
    )
    # 骨架是**豆包环境**的写法：别的地方提到"豆包"不算，这句得说清示例的适用面。
    assert "豆包环境" in blocks and "§零" in blocks and "探测" in blocks, (
        "blocks.md 要说清那 5 段示例是**豆包环境**的写法：换宿主按 §零 的机制重来"
        "（示例只对豆包成立，照抄到别处就是吐源码）"
    )


def test_render_skill_html_examples_use_a_wrapper_the_host_table_measured() -> None:
    """skill 里每一段 HTML 示例的起始行，都必须是**宿主表里某个实测行**的围栏。

    模型是照抄示例的：示例写成普通的 ```html，豆包就把整段当源码显示 ——
    「示例本身错了比没示例更糟」。

    ⚠️ 这条**不是**"只许 type=\"renderer\" 这一个值"：允许的围栏是从 §零 宿主表的实测行**现推**
    出来的 —— 以后给 WorkBuddy 加一行实测（§0.4 的三步），示例就能写它的围栏，这条守门不用改。
    """
    measured = _measured_wrappers()
    assert RENDER_WRAPPER_INFO in measured, (
        f"§零 宿主表里没有任何实测行能推出 {RENDER_WRAPPER} —— "
        "先看 0.1 的表是不是被写散了（守门的允许集就是从这张表来的）"
    )

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
            if info in measured:
                examples += 1
            else:
                offenders.append(f"{path.relative_to(RENDER_ROOT)}:{lineno}: ```{info}")

    assert not offenders, (
        "渲染 skill 里的 HTML 示例只能用宿主表**实测过**的那一行起始行"
        f"（示例会被照抄，写错就吐源码）。实测值：{sorted(f'```{m}' for m in measured)}\n"
        + "\n".join(offenders)
    )
    assert examples >= 5, f"只解析到 {examples} 段 HTML 示例，先看示例结构是不是变了"


def test_the_skill_points_at_the_actionable_part_of_the_handoff() -> None:
    """`solver_handoff` **不能只写"它不是可执行方案"** —— 那是负向描述，会把人劝退。

    2026-10-06 真机：skill 里只有"不是可执行方案"，没有"照它的参数去跑 `find`"。
    我读了 skill，记住的是「别拿它去装备」，于是自己拼参数、白跑一轮 0 候选。
    **只划红线、不给用法** 和 **货在深处、出口空着** 是同一个病的两面。
    """
    routing = _routing_text()
    assert "solver_handoff.arguments" in routing, (
        "要点名**可执行的那个子字段**：`solver_handoff.arguments` 就是「该拿什么参数跑 find」"
    )
    skill = (Path(__file__).resolve().parents[1] / "skills/destiny2-mcp/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "solver_handoff.arguments" in skill, "SKILL.md 的简短清单里也要给出正向用法"
