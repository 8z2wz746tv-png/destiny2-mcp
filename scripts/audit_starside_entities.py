"""只读审计：入库的 Starside 实体数据与我们 Manifest 的 hash 是否仍然条条对得上。

为什么单独一个脚本：导入时已经校验过一次（`import_starside_entities._check`），但**Manifest 会更新**
（游戏内容变了、我们重新下载），那时"社区数据指向的 hash"可能在新 Manifest 里消失。这件事要在
真机上能一条命令查出来（退出码 0/1），而不是等某个工具在玩家面前报错。

用法：

    .venv/bin/python scripts/audit_starside_entities.py          # 校验 + 打印覆盖
    .venv/bin/python scripts/audit_starside_entities.py --quiet  # 只给结论与退出码
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from import_starside_entities import ManifestIndex, _check  # noqa: E402

ENTITIES = ROOT / "data" / "starside" / "entities"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    items_payload = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))
    perks_payload = json.loads((ENTITIES / "perks.json").read_text(encoding="utf-8"))
    items, perks = items_payload["items"], perks_payload["perks"]
    meta = items_payload["_meta"]

    if not args.quiet:
        print(f"实体数据：物品 {len(items)}、perk {len(perks)}")
        print(f"来源：{meta.get('source')}（快照 {meta.get('snapshot_at')}，作者 {meta.get('authors')}）")
        coverage = meta.get("coverage") or {}
        for group, counts in coverage.items():
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
            print(f"  {group} 覆盖前几：{top}")

    try:
        _check(ManifestIndex(), items, perks)
    except SystemExit as exc:
        print(f"审计失败：{exc}")
        return 1
    print("全部对得上 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
