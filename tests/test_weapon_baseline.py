"""P0 武器基线与差异闸门：让"消失字段必须有理由"这条规则一直有效。

重构武器返回结构时，键名一定会变。这个测试保证三件事：

1. 基线用例仍然齐全（`scripts/capture_weapon_baseline.py` 里列了哪些，就必须有哪几份）；
2. 基线自身对比必须"零差异"（差异脚本本身没坏）；
3. allowlist 里登记的每一条都要写明去向，不能拿空理由糊过去。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "tests" / "baselines" / "weapon_responses"
ALLOWLIST = ROOT / "tests" / "baselines" / "weapon_response_allowlist.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diff_module():
    return _load(ROOT / "scripts" / "diff_weapon_baseline.py", "_diff_weapon_baseline")


def _declared_cases() -> set[str]:
    module = _load(ROOT / "scripts" / "capture_weapon_baseline.py", "_capture_weapon_baseline")
    return {case_id for case_id, _tool, _args in module.CASES}


def test_every_declared_case_has_a_baseline_file() -> None:
    """用例清单与落盘文件必须一一对应：加了用例就要重新录基线。"""
    declared = _declared_cases()
    recorded = {path.stem for path in BASELINE.glob("*.json")} - {"index"}
    missing = sorted(declared - recorded)
    extra = sorted(recorded - declared)

    assert not missing, f"这些用例还没有基线文件：{missing}（跑 scripts/capture_weapon_baseline.py）"
    assert not extra, f"这些基线文件已不在用例清单里：{extra}（删掉或补进清单）"
    assert len(declared) >= 25, "基线用例太少，盖不住武器的全部只读 intent"


def test_baseline_index_lists_every_case() -> None:
    index = json.loads((BASELINE / "index.json").read_text(encoding="utf-8"))
    cases = {item["case"] for item in index["cases"]}
    assert cases == _declared_cases()


def test_baseline_against_itself_has_no_unexplained_removals(diff_module) -> None:
    """差异脚本的自我一致性：同一份基线对比必须无差异、退出码为 0。"""
    report, ok = diff_module.diff(BASELINE, BASELINE, ALLOWLIST)

    assert ok, report
    assert "无理由消失" in report


def test_allowlist_entries_explain_where_the_field_went() -> None:
    allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    removed = allowlist.get("removed", {})

    empty = [path for path, reason in removed.items() if not str(reason).strip()]
    assert not empty, f"这些登记没写去向：{empty}"


def test_diff_gate_detects_a_removed_field(diff_module, tmp_path: Path) -> None:
    """把闸门自己测一遍：删掉一个字段必须被抓到、且退出码非 0。

    做法是**先注入一个探针字段**再删掉它 —— 不能直接删基线里的字段：
    P4 之后基线里几乎每个字段都已登记为"有意消失"，删它们闸门本来就该放行，
    于是这条自测会变成"测过期的东西"（这正是这轮踩到的坑）。
    """
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    for source in BASELINE.glob("*.json"):
        text = source.read_text(encoding="utf-8")
        (after / source.name).write_text(text, encoding="utf-8")
        payload = json.loads(text)
        if source.name == "stats_legendary.json":
            payload["data"]["stats"]["__probe__"] = "闸门探针"
        (before / source.name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    report, ok = diff_module.diff(before, after, ALLOWLIST)

    assert not ok, "删掉未登记的字段必须让闸门失败"
    assert "data.stats.__probe__" in report
