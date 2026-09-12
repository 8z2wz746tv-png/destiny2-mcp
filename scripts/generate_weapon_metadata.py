#!/usr/bin/env python3
"""从 Manifest 生成武器元数据（目前是"基础 perk ↔ 强化 perk"配对表）。

为什么要生成物而不是每次查库：强化 perk 是**独立条目**，Manifest 里没有任何字段写
"我和谁是一对"。配对只能按规则推，实测两种：

- 严格一对一（基础 1 条 + 强化 1 条）：254 组，其中 **49 组描述完全一样** —— 那是同名的
  另一份副本而不是强化版，属于误配；
- **现在采用**：同 plug 类别 + 同名字 + 强化版唯一（tierType=3 只有一条）+ 描述不同。
  得 228 组 / 216 个 perk 名，覆盖"狂暴"这类基础有多个副本的常见 perk，也排除误配。

基础有多个副本时都指向同一个强化 hash（按基础 hash 查即可）；强化版不唯一或描述相同的
一律跳过，不猜。整表扫描一次落盘，运行时就只是查字典。

生成物里记录当时的 Manifest 指纹；`destiny_mcp.manifest_fingerprint.verify` 用它判断
"Manifest 换过了，生成物过期了没"。

用法：
    .venv/bin/python scripts/generate_weapon_metadata.py
    .venv/bin/python scripts/generate_weapon_metadata.py --check   # 只校验是否最新
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from destiny_mcp import manifest_fingerprint  # noqa: E402
from destiny_mcp.manifest import ManifestManager  # noqa: E402

OUTPUT = ROOT / "data" / "weapon_enhanced_pairs.json"
MANIFEST_DIR = ROOT / "manifest"
MANIFEST_ZH = MANIFEST_DIR / "destiny_manifest_zh.sqlite3"

# 只看真正的武器 perk 类别（枪管/弹匣/特性/瞄具/框架…），避免把外观、模组算进来。
PERK_CATEGORY_TAILS = {
    "barrels", "barrel", "magazines", "magazine", "batteries", "battery",
    "frames", "perks", "traits", "scopes", "sights", "grips", "stocks",
}

BASE_TIER = 2   # 普通
ENHANCED_TIER = 3  # 罕见


def build_pairs(manifest: ManifestManager) -> tuple[dict[str, dict], list[str]]:
    """返回 ({基础 hash: 配对信息}, 跳过的组说明)。

    规则：同 plug 类别 + 同名字；强化版（tierType=3）必须唯一，且描述与基础版不同。
    基础版可以有多条（同名不同武器族的副本），它们都指向同一个强化 hash。
    """
    groups: dict[tuple[str, str], dict[int, list[dict]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    # 注意：iter_definitions 的 limit 会被夹到 ≥1，不是"0 = 全部"。
    for row in manifest.iter_definitions("DestinyInventoryItemDefinition", limit=200000):
        if not isinstance(row, dict) or row.get("itemType") != 19:  # 19 = Mod（plug）
            continue
        plug = row.get("plug") or {}
        category = str(plug.get("plugCategoryIdentifier") or "")
        if category.split(".")[-1] not in PERK_CATEGORY_TAILS:
            continue
        name = str((row.get("displayProperties") or {}).get("name") or "").strip()
        tier = (row.get("inventory") or {}).get("tierType")
        item_hash = row.get("hash")
        if not name or tier not in (BASE_TIER, ENHANCED_TIER) or not isinstance(item_hash, int):
            continue
        groups[(name, category)][tier].append(
            {
                "hash": _unsigned(item_hash),
                "name": name,
                "category": category,
                "description": str((row.get("displayProperties") or {}).get("description") or ""),
            }
        )

    pairs: dict[str, dict] = {}
    skipped: list[str] = []
    for (name, category), tiers in sorted(groups.items()):
        bases = tiers.get(BASE_TIER) or []
        enhanced = tiers.get(ENHANCED_TIER) or []
        if not bases or not enhanced:
            continue
        if len(enhanced) > 1:
            skipped.append(f"{name}({category})：强化版有 {len(enhanced)} 条，配不出唯一的一条")
            continue
        upgraded = enhanced[0]
        matched = [base for base in bases if base["description"] != upgraded["description"]]
        if not matched:
            skipped.append(f"{name}({category})：基础与强化描述完全相同，疑似同名的另一份副本")
            continue
        for base in matched:
            pairs[str(base["hash"])] = {
                "enhanced": upgraded["hash"],
                "name": name,
                "category": category,
                "base_description": base["description"],
                "enhanced_description": upgraded["description"],
            }
    return pairs, skipped


def _unsigned(value: int) -> int:
    return value + 4294967296 if value < 0 else value


def build_document(manifest: ManifestManager) -> dict:
    pairs, skipped = build_pairs(manifest)
    return {
        "schema_version": 1,
        "generated_from": "manifest/destiny_manifest_zh.sqlite3",
        "generator": "scripts/generate_weapon_metadata.py",
        "rules": (
            "同 plugCategoryIdentifier + 同显示名；强化版(inventory.tierType=3)唯一且描述与基础版不同；"
            "基础版可有多个副本，都指向同一强化 hash；强化版不唯一或描述相同的跳过，不猜。"
        ),
        "manifest_fingerprint": manifest_fingerprint.read(MANIFEST_DIR),
        "pair_count": len(pairs),
        "perk_count": len({entry["name"] for entry in pairs.values()}),
        "skipped": skipped,
        "pairs": pairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验生成物是否与当前 Manifest 一致")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    existing = json.loads(args.out.read_text(encoding="utf-8")) if args.out.is_file() else None
    ok, message = manifest_fingerprint.verify(MANIFEST_DIR, (existing or {}).get("manifest_fingerprint"))
    if args.check:
        print(f"{'一致' if ok else '过期'}：{message}")
        return 0 if ok else 1

    manifest = ManifestManager()
    manifest._load_from_file()
    document = build_document(manifest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    size_kb = args.out.stat().st_size // 1024
    print(
        f"已写入 {args.out.relative_to(ROOT)}：{document['pair_count']} 组配对 / "
        f"{document['perk_count']} 个 perk 名，跳过 {len(document['skipped'])} 组，{size_kb} KB"
    )
    for note in document["skipped"][:5]:
        print(f"  跳过：{note}")
    if not ok:
        print(f"（注意：{message}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
