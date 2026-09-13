"""P0 护甲基线与差异闸门：护甲重构期间"消失字段必须有理由"。

和 `tests/test_weapon_baseline.py` 同一套规则，换成护甲面：
`scripts/capture_weapon_baseline.py --surface armor` 录的 14 例（库存列表/检索/概况、
异域护甲、套装效果、护甲模组、社区核对、反推待刷件、装备确认回显）就是护甲重构的
对照物；P1–P5 只要动了护甲序列化，就必须让这里的 diff 保持"零无理由消失"。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "tests" / "baselines" / "armor_responses"
ALLOWLIST = ROOT / "tests" / "baselines" / "armor_response_allowlist.json"
CAPTURE = ROOT / "scripts" / "capture_weapon_baseline.py"
DIFF = ROOT / "scripts" / "diff_weapon_baseline.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diff_module():
    return _load(DIFF, "_diff_weapon_baseline_for_armor")


def _declared_cases() -> set[str]:
    module = _load(CAPTURE, "_capture_baseline_for_armor")
    return {case[0] for case in module._as_cases(module.ARMOR_CASES)}


def test_every_declared_armor_case_has_a_baseline_file() -> None:
    declared = _declared_cases()
    recorded = {path.stem for path in BASELINE.glob("*.json")} - {"index"}

    assert not missing_extra(declared, recorded, "missing"), (
        f"这些护甲用例还没有基线文件：{sorted(declared - recorded)}"
        "（跑 scripts/capture_weapon_baseline.py --surface armor）"
    )
    assert not missing_extra(declared, recorded, "extra"), (
        f"这些基线文件已不在用例清单里：{sorted(recorded - declared)}"
    )
    assert len(declared) >= 12, "护甲基线用例太少，盖不住护甲的只读面"


def missing_extra(declared: set[str], recorded: set[str], which: str) -> set[str]:
    return declared - recorded if which == "missing" else recorded - declared


def test_armor_baseline_index_lists_every_case() -> None:
    index = json.loads((BASELINE / "index.json").read_text(encoding="utf-8"))
    assert {item["case"] for item in index["cases"]} == _declared_cases()


def test_armor_baseline_has_the_shapes_we_plan_to_touch() -> None:
    """基线必须真的盖住要改的形状，否则闸门是空的。"""
    legs = json.loads((BASELINE / "armor_get_legs.json").read_text(encoding="utf-8"))
    rows = ((legs.get("data") or {}).get("inventory") or {}).get("items") or []
    assert rows, "armor_get_legs 没有拿到护甲行"
    assert {"item_instance_id", "power", "bucket_type", "stats"} <= set(rows[0])

    echo = json.loads((BASELINE / "equip_confirm_echo.json").read_text(encoding="utf-8"))
    assert (echo.get("error") or {}).get("code") == "confirmation_required"
    candidate = (echo.get("candidates") or [{}])[0]
    canonical = candidate.get("canonical_build") or {}
    assert len(canonical.get("items") or []) == 5, "装备确认回显里应有五个部位"

    community = json.loads((BASELINE / "community_build_inventory.json").read_text(encoding="utf-8"))
    match = (((community.get("data") or {}).get("selected_build") or {}).get("inventory_match")) or {}
    assert match.get("requirements"), "社区核对里应有护甲需求项"


def test_armor_baseline_against_itself_has_no_unexplained_removals(diff_module) -> None:
    report, ok = diff_module.diff(BASELINE, BASELINE, ALLOWLIST)

    assert ok, report
    assert "无理由消失" in report


def test_armor_allowlist_entries_explain_where_the_field_went() -> None:
    allowlist = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    removed = allowlist.get("removed", {})

    empty = [path for path, reason in removed.items() if not str(reason).strip()]
    assert not empty, f"这些登记没写去向：{empty}"


def test_armor_diff_gate_detects_a_removed_field(diff_module, tmp_path: Path) -> None:
    """把闸门自己测一遍：删掉一个未登记的字段必须被抓到。"""
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    for source in BASELINE.glob("*.json"):
        text = source.read_text(encoding="utf-8")
        (after / source.name).write_text(text, encoding="utf-8")
        payload = json.loads(text)
        if source.name == "armor_get_legs.json":
            payload["data"]["inventory"]["items"][0]["__probe__"] = "闸门探针"
        (before / source.name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    report, ok = diff_module.diff(before, after, ALLOWLIST)

    assert not ok, "删掉未登记的字段必须让闸门失败"
    assert "__probe__" in report
