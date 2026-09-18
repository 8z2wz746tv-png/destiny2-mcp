"""语料脚本自身的健康检查：**先保证 runner 能跑完，再谈结果**。

为什么单开一个：`scripts/run_corpus_*.py` 不在 pytest 覆盖范围内，而它们要打真机、
一次跑十几分钟。脚本自己写坏（例如布尔条件里混进一个字符串，把 `check()` 的位置参数
从 4 个撑成 5 个）时，表现是**跑到第 40 行才崩**，前面几十行的结果全废，而且崩的是
`TypeError` 不是断言失败 —— 2026-09-18 真机上就是这么崩的（`run_corpus_all_rows.py:1487`
的 career 三档那一行，脚本自带 bug，从未跑到过）。

这里只做**静态**检查（不联网、不需要账号）：
1. 每个 `scripts/run_corpus_*.py` 都能被 AST 解析（语法错误、写坏的条件先在这里红）；
2. 脚本里每一次 `check(...)` 调用的位置参数不超过 4 个（`group/title/ok/evidence`），
   其余只能走关键字 —— 这正是那次崩溃的形态；
3. 真机 runner 明确不带 `pytest` 依赖（它们要 `.env` 与 OAuth，不该被 CI 收集）。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_SCRIPTS = sorted((ROOT / "scripts").glob("run_corpus_*.py"))

# `check()` 的签名：group, title, ok, evidence, *, warn/info/seconds
CHECK_MAX_POSITIONAL = 4


def test_corpus_scripts_exist() -> None:
    names = {path.name for path in CORPUS_SCRIPTS}
    assert {"run_corpus_all_rows.py", "run_corpus_pvp_rows.py"} <= names, names


def test_every_corpus_script_parses() -> None:
    broken: list[str] = []
    for path in CORPUS_SCRIPTS:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - 只在脚本写坏时触发
            broken.append(f"{path.name}:{exc.lineno}: {exc.msg}")
    assert broken == [], "语料脚本有语法错误，真机跑到那里才会崩：\n" + "\n".join(broken)


def _check_arity_offenders(path: Path) -> list[str]:
    """扫描一个脚本里 `check()` 的位置参数个数（抽出来是为了能用临时文件验证它会咬人）。"""
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name != "check":
            continue
        if len(node.args) > CHECK_MAX_POSITIONAL:
            offenders.append(
                f"{path.name}:{node.lineno}: check() 收到 {len(node.args)} 个位置参数"
            )
    return offenders


def test_check_calls_keep_their_positional_arity() -> None:
    """`check()` 多给一个位置参数 = 真机跑到那一行才 `TypeError`（整个 run 作废）。"""
    offenders = [line for path in CORPUS_SCRIPTS for line in _check_arity_offenders(path)]
    assert offenders == [], (
        f"check() 最多 {CHECK_MAX_POSITIONAL} 个位置参数（group/title/ok/evidence），"
        "其余用关键字：\n" + "\n".join(offenders)
    )


def test_the_arity_scan_actually_bites(tmp_path: Path) -> None:
    """注入一次违规，确认这条守门真的会红（不然它只是个装饰）。"""
    broken = tmp_path / "run_corpus_broken.py"
    broken.write_text(
        "def check(group, title, ok, evidence, *, seconds=None):\n"
        "    ...\n"
        "\n"
        "check(\n"
        '    "rows",\n'
        '    "标题",\n'
        '    True\n'
        '    and "value" not in {"a": 1}, "写坏的断言说明"\n'
        "    and True,\n"
        '    "evidence",\n'
        "    seconds=1.0,\n"
        ")\n",
        encoding="utf-8",
    )

    offenders = _check_arity_offenders(broken)

    assert len(offenders) == 1 and "5 个位置参数" in offenders[0], offenders


def test_real_machine_runners_are_not_collected_by_pytest() -> None:
    """真机脚本不进 CI：文件名不以 `test_` 开头，且不在 tests/ 目录里。"""
    assert all(not path.name.startswith("test_") for path in CORPUS_SCRIPTS)
    assert all(path.parent.name == "scripts" for path in CORPUS_SCRIPTS)
