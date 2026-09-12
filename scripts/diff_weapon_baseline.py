#!/usr/bin/env python3
"""对比两份武器基线，产出"新增 / 重命名 / 消失 / 值变化"四类差异报告。

硬门槛：**任何消失的字段都要有理由** —— 允许消失的字段必须登记在
`tests/baselines/weapon_response_allowlist.json` 里，并写明去向；
没登记就退出码非 0，重构不许合并。

用法：
    .venv/bin/python scripts/diff_weapon_baseline.py \
        --before tests/baselines/weapon_responses --after /tmp/after
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tests" / "baselines" / "weapon_response_allowlist.json"

# 值本身会随时间/装备变化的字段：只报"出现了这个路径"，不把值差异算问题。
VOLATILE_HINTS = (
    "updated_at", "next_refresh", "captured_at", "generated_at", "snapshot_id",
    "instance_id", "power", "item_level", "elapsed_ms", "payload_chars",
    "expires_at", "reset", "date", "time", "duration", "progress",
)


def _norm(path: str) -> str:
    """把 list 索引归一成 []：数组多一项不该算"新增字段"。"""
    out = []
    for part in path.split("."):
        out.append("[]" if part.isdigit() else part)
    return ".".join(out)


def _leaves(node: Any, prefix: str = "") -> dict[str, Any]:
    """叶子路径 → 值（数组按 [] 归一，取最后一个元素的值）。"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("_") and prefix == "":
                continue
            out.update(_leaves(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, list):
        for item in node:
            out.update(_leaves(item, f"{prefix}[]"))
    else:
        out[prefix or "<root>"] = node
    return out


def _is_volatile(path: str) -> bool:
    lowered = path.casefold()
    return any(hint in lowered for hint in VOLATILE_HINTS)


def _load_cases(directory: Path) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name in {"index.json", "weapon_response_allowlist.json"}:
            continue
        cases[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return cases


def _suggest_renames(removed: list[str], added: list[str]) -> list[tuple[str, str, float]]:
    suggestions = []
    for old in removed:
        old_tail = old.split(".")[-1].replace("[]", "")
        parent = old.rsplit(".", 1)[0] if "." in old else ""
        for new in added:
            new_tail = new.split(".")[-1].replace("[]", "")
            new_parent = new.rsplit(".", 1)[0] if "." in new else ""
            if parent != new_parent:
                continue
            ratio = difflib.SequenceMatcher(None, old_tail, new_tail).ratio()
            if ratio >= 0.6:
                suggestions.append((old, new, round(ratio, 2)))
    return suggestions


def diff(before_dir: Path, after_dir: Path, allowlist_path: Path) -> tuple[str, bool]:
    before = _load_cases(before_dir)
    after = _load_cases(after_dir)
    allowlist = (
        json.loads(allowlist_path.read_text(encoding="utf-8"))
        if allowlist_path.is_file()
        else {}
    )
    allowed_removed: dict[str, str] = allowlist.get("removed", {})

    lines = ["# 武器基线差异报告", ""]
    lines.append(f"- 改动前：`{before_dir}`（{len(before)} 例）")
    lines.append(f"- 改动后：`{after_dir}`（{len(after)} 例）")
    lines.append("")

    unexplained_removals: list[tuple[str, str]] = []
    lines.append("## 用例级")
    lines.append("")
    lines.append("| 用例 | ok | error.code | 结论 |")
    lines.append("| --- | --- | --- | --- |")
    for case_id in sorted(set(before) | set(after)):
        if case_id not in after:
            lines.append(f"| {case_id} | — | — | **改动后缺失该用例** |")
            unexplained_removals.append((case_id, f"<{case_id} 整个用例缺失>"))
            continue
        if case_id not in before:
            lines.append(f"| {case_id} | {after[case_id].get('ok')} | — | 新增用例 |")
            continue
        b, a = before[case_id], after[case_id]
        b_code = (b.get("error") or {}).get("code")
        a_code = (a.get("error") or {}).get("code")
        notes = []
        if b.get("ok") != a.get("ok"):
            notes.append(f"ok 变化 {b.get('ok')} → {a.get('ok')}")
        if b_code != a_code:
            notes.append(f"code 变化 {b_code} → {a_code}")
        if (b.get("summary") or "") != (a.get("summary") or ""):
            notes.append("summary 措辞变化")
        lines.append(f"| {case_id} | {a.get('ok')} | {a_code} | {'；'.join(notes) or '—'} |")
    lines.append("")

    lines.append("## 路径级差异")
    for case_id in sorted(set(before) & set(after)):
        b_leaves, a_leaves = _leaves(before[case_id]), _leaves(after[case_id])
        removed = sorted(set(b_leaves) - set(a_leaves))
        added = sorted(set(a_leaves) - set(b_leaves))
        changed = [
            path
            for path in sorted(set(b_leaves) & set(a_leaves))
            if b_leaves[path] != a_leaves[path] and not _is_volatile(path)
        ]
        if not (removed or added or changed):
            continue
        lines.append("")
        lines.append(f"### `{case_id}`")
        if added:
            lines.append(f"- 新增 {len(added)} 个路径：" + "、" + "".join(f"`{p}`" for p in added[:12]) + ("…" if len(added) > 12 else ""))
        if changed:
            lines.append(f"- 值变化 {len(changed)} 个（非易变字段）：" + "、".join(f"`{p}`" for p in changed[:8]) + ("…" if len(changed) > 8 else ""))
        for path in removed:
            reason = allowed_removed.get(path)
            if reason:
                lines.append(f"- 消失（已登记）：`{path}` → {reason}")
            else:
                lines.append(f"- **消失（未登记）**：`{path}`")
                unexplained_removals.append((case_id, path))
        for old, new, ratio in _suggest_renames(removed, added):
            lines.append(f"- 疑似重命名：`{old}` → `{new}`（相似度 {ratio}）")

    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if unexplained_removals:
        lines.append(f"**有 {len(unexplained_removals)} 个字段消失且未登记理由**，不允许合并：")
        for case_id, path in unexplained_removals[:40]:
            lines.append(f"- `{case_id}`：`{path}`")
        lines.append("")
        lines.append(f"若确认是有意调整，写进 `{allowlist_path.relative_to(ROOT)}` 的 `removed` 并说明去向。")
    else:
        lines.append("没有「无理由消失」的字段。")
    lines.append("")
    return "\n".join(lines), not unexplained_removals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    report, ok = diff(args.before, args.after, args.allowlist)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.report}")
    else:
        print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
