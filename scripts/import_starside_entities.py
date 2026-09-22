"""把 Starside 作者给的新归档转成我们要入库的紧凑实体文件（P0）。

输入（作者导出，**不进 git**）：`归档/inventory-items.json`、`归档/sandbox-perks.json`。
输出（进 git，运行时要读的就是它们）：

    data/starside/entities/items.json
    data/starside/entities/perks.json

并把来源信息写进 `data/starside/index.json` 的 `entities` 段（sha256/字节数/快照时间/作者/覆盖数）。

三条硬规矩（见 `docs/plans/STARSIDE_ENTITY_PLAN.md`）：

1. **只留我们要用的字段**：物品/ perk 的定义、名字、图标一律不抄 —— 那些以我们自己的 Manifest 为准
   （单一出处）。这里只收社区层：作者推荐、评语、机制数值、神器/套装/催化等关联、站点标签。
2. **hash 一律无符号**：导出是无符号的（真机 `+超能 / -生命值` = 4026414261，有符号写法是 -268553035），
   我们库里存的是有符号 —— 所以存储与比较都统一成无符号，比较处不再各自记得转换。
3. **全有或全无**：每个 hash 都要能在对应的 Manifest 表里解出（物品→`DestinyInventoryItemDefinition`、
   perk→`DestinySandboxPerkDefinition`、神器→物品表 + `DestinySeasonDefinition.artifactItemHash`、
   套装→`DestinyEquipableItemSetDefinition`、引用→item/perk 表），**有一个解不出就退出**，
   不静默丢数据（那是"数据缩水看不出来"的老毛病）。

输出是**确定性**的（排序 + 固定分隔符 + 不写时间戳）——同样输入逐字节同样输出，脚本可反复跑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from destiny_mcp import config  # noqa: E402
from destiny_mcp.utils.hash_utils import to_unsigned  # noqa: E402

DEFAULT_SOURCE = ROOT / "归档"
OUT_DIR = ROOT / "data" / "starside" / "entities"
INDEX_PATH = ROOT / "data" / "starside" / "index.json"

#: 物品层要留的站点字段（其余定义类字段一律不抄，见模块 docstring 第 1 条）
KEEP_ITEM = (
    "site_artifact", "site_tier", "site_superTier", "site_cooldownSeconds",
    "site_recoveryMultiplier", "site_weapons", "site_perkColumns",
    "isAdept", "isHolofoil", "sameAs", "enhanced",
)
#: `derived.*` 里我们真正会用的（`archetype` 是框架 hash → 帧表 join 的桥）
KEEP_DERIVED = (
    "release", "season", "archetype", "breakerType", "foundry",
    "craftable", "catalyst", "tierable", "tiers",
)
#: 物品的中文社区文本
KEEP_ITEM_ZH = (
    "realgame_details", "site_authors", "site_source", "site_frameStats",
    "site_elements", "site_season", "site_weaponTypes",
)
KEEP_PERK = ("onItems", "onSets", "authors", "damageTypeHash")
#: perk 的中文社区文本（键名原文照留：`效果`/`属性变化`… 是站点自己的栏目名）
KEEP_PERK_ZH = (
    "realgame_details", "realgame_details#2", "效果", "属性变化", "冷却与槽位",
    "基础冷却", "冷却", "费用", "来源", "碎片槽位", "异域 PERK", "右栏",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} 不是 {{hash: 条目}} 形状，导入中止")
    return data


def _item_entry(raw: dict) -> dict:
    keep: dict = {}
    for field in KEEP_ITEM:
        if raw.get(field) not in (None, False, [], {}):
            keep[field] = raw[field]
    derived = {k: v for k, v in (raw.get("derived") or {}).items() if k in KEEP_DERIVED and v}
    if derived:
        keep["derived"] = derived
    zh = {k: v for k, v in ((raw.get("i18n") or {}).get("zh-CN") or {}).items() if k in KEEP_ITEM_ZH and v}
    if zh:
        keep["zh"] = zh
    return keep


def _perk_entry(raw: dict) -> dict:
    keep: dict = {}
    for field in KEEP_PERK:
        if raw.get(field):
            keep[field] = raw[field]
    zh = {k: v for k, v in ((raw.get("i18n") or {}).get("zh-CN") or {}).items() if k in KEEP_PERK_ZH and v}
    if zh:
        keep["zh"] = zh
    return keep


def _coverage(entries: dict, fields: tuple[str, ...], *, nested: str = "") -> dict[str, int]:
    counts = {}
    for field in fields:
        if nested:
            counts[field] = sum(1 for e in entries.values() if (e.get(nested) or {}).get(field))
        else:
            counts[field] = sum(1 for e in entries.values() if e.get(field))
    return counts


class ManifestIndex:
    """给导入做 hash 校验用（只读）。缺 Manifest 就明确失败，不静默跳过校验。"""

    def __init__(self) -> None:
        path = config.DESTINY_MANIFEST_PATH / "destiny_manifest.sqlite3"
        if not path.exists():
            raise SystemExit(f"找不到本地 Manifest（{path}）：导入必须能逐条校验 hash，先跑一次下载")
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._cache: dict[str, set[int]] = {}

    def ids(self, table: str) -> set[int]:
        if table not in self._cache:
            rows = self._conn.execute(f"SELECT id FROM {table}").fetchall()
            self._cache[table] = {to_unsigned(int(r[0])) for r in rows}
        return self._cache[table]

    def season_artifacts(self) -> dict[int, tuple[int, str]]:
        out: dict[int, tuple[int, str]] = {}
        for _, raw in self._conn.execute("SELECT id, json FROM DestinySeasonDefinition"):
            row = json.loads(raw)
            item_hash = row.get("artifactItemHash")
            if item_hash:
                out[to_unsigned(int(item_hash))] = (
                    int(row.get("seasonNumber") or 0),
                    (row.get("displayProperties") or {}).get("name", ""),
                )
        return out


def _check(manifest: ManifestIndex, items: dict, perks: dict) -> None:
    """逐类校验 + 任何一条不通就退出。"""
    problems: list[str] = []

    def missing(label: str, hashes, known: set[int]) -> None:
        bad = sorted({to_unsigned(int(h)) for h in hashes} - known)
        if bad:
            problems.append(f"{label}: {len(bad)} 个 hash 解不出（例：{bad[:5]}）")

    item_ids = manifest.ids("DestinyInventoryItemDefinition")
    perk_ids = manifest.ids("DestinySandboxPerkDefinition")
    set_ids = manifest.ids("DestinyEquipableItemSetDefinition")
    season_artifacts = manifest.season_artifacts()

    missing("物品", items.keys(), item_ids)
    missing("perk", perks.keys(), perk_ids)
    missing("onItems（perk→物品）", (h for p in perks.values() for h in (p.get("onItems") or [])), item_ids)
    missing("onSets（perk→套装）", (s for p in perks.values() for s in (p.get("onSets") or [])), set_ids)
    missing("site_artifact（神器物品）", (i.get("site_artifact") for i in items.values() if i.get("site_artifact")), item_ids)
    missing("sameAs 目标", (i.get("sameAs") for i in items.values() if i.get("sameAs")), item_ids)
    missing("derived.catalyst", (c for i in items.values() for c in ((i.get("derived") or {}).get("catalyst") or [])), item_ids)
    missing("site_perkColumns", (p for i in items.values() for col in (i.get("site_perkColumns") or []) for p in col), perk_ids | item_ids)
    missing("enhanced.by", (b for i in items.values() for e in (i.get("enhanced") or []) for b in (e.get("by") or [])), perk_ids | item_ids)

    # 神器必须能指到赛季（否则"哪一季的神器"这条能力是假的）
    artifacts = {to_unsigned(int(i["site_artifact"])) for i in items.values() if i.get("site_artifact")}
    unlinked = sorted(artifacts - set(season_artifacts))
    if unlinked:
        problems.append(f"神器没在 DestinySeasonDefinition.artifactItemHash 里出现：{unlinked}")

    if problems:
        raise SystemExit("hash 校验失败（全有或全无，不写入）：\n  - " + "\n  - ".join(problems))
    print(f"hash 校验通过：物品 {len(items)}、perk {len(perks)}；神器 {len(artifacts)} 个全部指到赛季")


def _dump(payload: dict, path: Path) -> int:
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return len(text.encode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="作者导出的目录")
    parser.add_argument("--skip-verify", action="store_true", help="跳过 Manifest hash 校验（仅排错用）")
    args = parser.parse_args()

    items_raw = _load(args.source / "inventory-items.json")
    perks_raw = _load(args.source / "sandbox-perks.json")

    items = {str(to_unsigned(int(k))): e for k, v in items_raw.items() if (e := _item_entry(v))}
    perks = {str(to_unsigned(int(k))): e for k, v in perks_raw.items() if (e := _perk_entry(v))}
    print(f"过滤后：物品 {len(items)}/{len(items_raw)} 条、perk {len(perks)}/{len(perks_raw)} 条")

    if not args.skip_verify:
        _check(ManifestIndex(), items, perks)

    authors = sorted({
        name
        for entry in items.values()
        for name in ((entry.get("zh") or {}).get("site_authors") or {})
    })
    sources = []
    snapshot = 0.0
    for name in ("inventory-items.json", "sandbox-perks.json"):
        path = args.source / name
        sources.append({"name": name, "sha256": _sha256(path), "bytes": path.stat().st_size})
        snapshot = max(snapshot, path.stat().st_mtime)
    meta = {
        "schema": 1,
        "source": "starside.work",
        "unofficial": True,
        "snapshot_at": datetime.fromtimestamp(snapshot, tz=timezone.utc).isoformat(timespec="seconds"),
        "authors": authors,
        "generated_by": "scripts/import_starside_entities.py",
        "source_files": sources,
        "counts": {
            "items": len(items),
            "perks": len(perks),
            "items_with_author": sum(
                1 for e in items.values() if (e.get("zh") or {}).get("site_authors")
            ),
            "perks_with_reverse_index": sum(1 for e in perks.values() if e.get("onItems")),
            # 引用完整性：**原始导出里 0 条越界**（导入前实测 6,134/6,134 都在），但我们只收
            # "有社区字段"的物品，所以过滤后一部分边指向的物品在本文件里没有条目 —— 那些靠
            # 我们自己的 Manifest 解释（真机审计 `audit_starside_entities.py` 验的就是这条）。
            # 把"文件内可解析的边数"记下来，缩水就必须是一次显式修改。
            "onItems_edges": sum(len(e.get("onItems") or []) for e in perks.values()),
            "onItems_edges_in_file": sum(
                1
                for e in perks.values()
                for h in (e.get("onItems") or [])
                if str(to_unsigned(int(h))) in items
            ),
            "sameAs_in_file": sum(
                1
                for e in items.values()
                if e.get("sameAs") and str(to_unsigned(int(e["sameAs"]))) in items
            ),
        },
        "coverage": {
            "items": _coverage(items, KEEP_ITEM) | _coverage(items, KEEP_DERIVED, nested="derived")
            | _coverage(items, KEEP_ITEM_ZH, nested="zh"),
            "perks": _coverage(perks, KEEP_PERK) | _coverage(perks, KEEP_PERK_ZH, nested="zh"),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    size_items = _dump({"_meta": meta, "items": items}, OUT_DIR / "items.json")
    size_perks = _dump({"_meta": meta, "perks": perks}, OUT_DIR / "perks.json")

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    index["entities"] = {
        "snapshot_at": meta["snapshot_at"],
        "authors": authors,
        "source_files": sources,
        "counts": meta["counts"],
        "files": {"items.json": size_items, "perks.json": size_perks},
        "note": "Starside 站点作者给的导出，经 scripts/import_starside_entities.py 转换；非 Bungie 官方数据。",
    }
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"写入：items.json {size_items/1024/1024:.2f} MB、perks.json {size_perks/1024/1024:.2f} MB；"
          f"index.json 的 entities 段已更新（快照 {meta['snapshot_at']}，作者 {authors}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
