#!/usr/bin/env python
"""Starside 实体层的**真机语料**：拿账号里真实的武器与模组跑一遍按 hash 的社区实体查询。

和单元测试的分工：`tests/test_starside_entities.py` 只用仓库里已提交的文件、任何机器都能跑；
这个脚本走**真机**（需要 OAuth + 本地 Manifest），回答的是"这份数据接进来之后，在我自己的账号上
到底能不能用、用得对不对"：

- 出处元数据齐不齐（社区资料没有出处就不许出去）；
- **我账号里的武器命中率**（有多少把有 Aegis / LGpig 的推荐）—— 这是"这套数据对我到底有多大用"的
  唯一诚实答案；
- 字段完整性：Aegis 的评级/选定 perk 名、LGpig 的评级与实测数值能不能解析；
- **闭环**：推荐里写的 `{perk|名字}` 能不能用我们 Manifest 的名字索引反查出 hash，再反查出
  "哪些枪有这颗 perk"；
- 武器 → 框架（`derived.archetype`）→ 帧级 DPS 表这条 join 通不通；
- 缺数据时给 None（不编），不认识的名字不硬凑。

标记 token 表的守门在 P1（解析器落地时），这里只检查"推荐里的 perk 名能不能被我们查到"。

    .venv/bin/python scripts/run_corpus_starside_entities.py

退出码 0 = 全部成立；非 0 = 有行不成立（看 FAIL 行）。
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from destiny_mcp.server import app_lifespan, create_server  # noqa: E402
from destiny_mcp.services.starside_entities import StarsideEntities  # noqa: E402
from destiny_mcp.tools._helpers import resolve_player_name  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
TIERS = {"S", "A", "B", "C", "D", "E", "F"}
PERK_TOKEN = re.compile(r"\{perk\|([^{}|]+)\}")
NUM_TOKEN = re.compile(r"\{num\|([^{}]*)\}")
#: 站点把"多个值/带标签的值"写成 `{num|13514\\11384}`、`{num|7087\\(级联点)}`，还有 `∞`
#: （真机语料挖出来的）——解析器必须先解包再判数值，不能拿原串当数字。
PAREN_SUFFIX = re.compile(r"（[^）]*）\s*$")


def unwrap_number(value: object) -> tuple[str, bool]:
    """把站点数值写法解包成 `(文本, 是否可当数值看)`。`∞` 也算合法（作者就这么写的）。"""
    text = str(value or "").strip()
    text = NUM_TOKEN.sub(lambda m: m.group(1), text)
    text = text.split("\\")[0].strip()
    text = PAREN_SUFFIX.sub("", text).strip()
    if text in ("∞", "-∞"):
        return text, True
    try:
        float(text)
    except ValueError:
        return text, False
    return text, True


def check(row: str, ok: bool, evidence: str) -> None:
    RESULTS.append((row, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'} | {row}\n       {evidence}")


def field(obj: object, key: str):
    """响应对象可能是 pydantic、dict 或普通对象 —— 三样都取一遍。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def rows_of(response: object) -> list:
    items = field(response, "items")
    return list(items) if isinstance(items, (list, tuple)) else []


async def main() -> int:
    async with app_lifespan(create_server()) as svc:
        manifest = svc["manifest"]
        entity = StarsideEntities()
        player = resolve_player_name("")

        meta = entity.meta()
        check(
            "① 出处元数据：source/unofficial/snapshot_at/authors/sha256 齐全",
            meta.get("source") == "starside.work" and meta.get("unofficial") is True
            and bool(meta.get("snapshot_at")) and bool(meta.get("authors"))
            and all(f.get("sha256") for f in meta.get("source_files") or []),
            f"快照={meta.get('snapshot_at')} 作者={meta.get('authors')} 条目={meta.get('counts')}",
        )

        inventory = await svc["inventory_svc"].get_inventory(player, "all", item_type="weapon", limit=500)
        weapons = [w for w in rows_of(inventory) if field(w, "item_hash")]
        hashes = [int(field(w, "item_hash")) for w in weapons]
        known = [h for h in hashes if entity.item(h)]
        aegis = [h for h in hashes if "Aegis" in entity.authors_of(h)]
        lgpig = [h for h in hashes if "LGpig" in entity.authors_of(h)]
        check(
            "② 我账号的武器命中率：实体层能查到的比例，以及有多少把带作者推荐",
            bool(hashes) and bool(known) and (bool(aegis) or bool(lgpig)),
            f"我身上/仓库武器 {len(hashes)} 把 → 实体层有社区数据 {len(known)} 把"
            f"（{len(known) / max(len(hashes), 1):.0%}）；其中 Aegis {len(aegis)} 把、LGpig {len(lgpig)} 把",
        )

        # Aegis：字段完整性 + 推荐里的 perk 名能不能被我们查到（P1 解析器的前提）
        unresolved: list[str] = []
        incomplete: list[str] = []
        checked_names = 0
        for h in aegis[:40]:
            block = entity.authors_of(h).get("Aegis") or {}
            name = (manifest.get_item_definition(h) or {}).get("displayProperties", {}).get("name", str(h))
            if block.get("aegis_tier") not in TIERS or not block.get("explanation_1"):
                incomplete.append(name)
            if not (block.get("barrel") and (block.get("perk1") or block.get("perk2"))):
                incomplete.append(name)
            for token in ("barrel", "magazine", "origin", "perk1", "perk2"):
                for perk_name in PERK_TOKEN.findall(str(block.get(token) or "")):
                    checked_names += 1
                    if not manifest.search(perk_name, limit=1) and not manifest.search(
                        PAREN_SUFFIX.sub("", perk_name).strip(), limit=1
                    ):
                        unresolved.append(perk_name)
        gap = len(unresolved) / max(checked_names, 1)
        check(
            "③ Aegis 推荐：评级在 S–F、有理由与选定栏位；推荐里的 perk 名我们查得到的比例 ≥90%",
            not incomplete and gap <= 0.10,
            f"抽 {min(len(aegis), 40)} 把：字段不全 {len(incomplete)} 把；perk 名 {checked_names} 个，"
            f"我们库里查不到 {len(unresolved)} 个（{gap:.1%}）"
            + (f"；例：{sorted(set(unresolved))[:4]}" if unresolved else ""),
        )

        # LGpig：分场景评级 + 实测数值（站点写法要能解包）
        lg_rows, lg_bad, lg_unrated = 0, [], 0
        for h in lgpig[:40]:
            block = entity.authors_of(h).get("LGpig") or {}
            name = (manifest.get_item_definition(h) or {}).get("displayProperties", {}).get("name", str(h))
            if not str(block.get("lgpig_tier") or "").strip():
                lg_unrated += 1  # 作者写了解释但没给评级：合法，展示时按"未评级"
                continue
            lg_rows += 1
            for key in ("dps", "total_damage", "switch_dps"):
                if block.get(key) in (None, ""):
                    continue
                text, numeric = unwrap_number(block[key])
                if not numeric:
                    lg_bad.append(f"{name}.{key}={text!r} 解包后仍不是数值")
        check(
            "④ LGpig 推荐：评级非空、实测数值解包（{num|…}/∞）后可解析；无评级但有理有据的算合法",
            lg_rows > 0 and not lg_bad,
            f"有评级 {lg_rows} 把、只写解释没评级 {lg_unrated} 把；数值异常 {len(lg_bad)} 条"
            + (f"：{lg_bad[:3]}" if lg_bad else ""),
        )

        # 武器 → 框架 → 帧表
        frame_hits, frame_keys = 0, set()
        for h in hashes:
            stats = entity.frame_stats_for_weapon(h)
            if stats:
                frame_hits += 1
                frame_keys |= set(stats[0].keys())
        need = {"base_damage", "base_interval", "body_mdps", "boss_total", "ammoType"}
        check(
            "⑤ 武器 → 框架（archetype）→ 帧级 DPS 表 这条 join 通",
            frame_hits > 0 and need <= frame_keys,
            f"我账号里 {frame_hits}/{len(hashes)} 把能 join 到帧表；帧表键 {len(frame_keys)} 个"
            f"（缺 {sorted(need - frame_keys)}）" if need - frame_keys else
            f"我账号里 {frame_hits}/{len(hashes)} 把能 join 到帧表；帧表键 {len(frame_keys)} 个",
        )

        # 闭环：推荐 perk 名 → plug 物品 → 它的 sandbox perk hash → 反查哪些枪有它
        # 注意这条链的**关键区分**：`manifest.search` 给的是 **plug 物品** hash，而归档的
        # `onItems` 键在 **`DestinySandboxPerkDefinition`** 空间里（plug 物品的 `perks[].perkHash`
        # 才是它）。第一版拿 plug hash 直接查，闭环率 0% —— 这正是语料要抓的东西。
        opened = closed = 0
        sample = None
        for h in aegis:
            block = entity.authors_of(h).get("Aegis") or {}
            for token in ("barrel", "perk1", "perk2"):
                for perk_name in PERK_TOKEN.findall(str(block.get(token) or "")):
                    found = manifest.search(perk_name, limit=1) or manifest.search(
                        PAREN_SUFFIX.sub("", perk_name).strip(), limit=1
                    )
                    if not found:
                        continue
                    plug_hash = int(found[0].get("itemHash") or 0)
                    definition = manifest.get_item_definition(plug_hash) or {}
                    sandbox = [int(p.get("perkHash") or 0) for p in (definition.get("perks") or [])]
                    opened += 1
                    for perk_hash in sandbox:
                        owners = entity.items_with_perk(perk_hash)
                        if owners:
                            closed += 1
                            if sample is None:
                                owner_name = (
                                    manifest.get_item_definition(owners[0]) or {}
                                ).get("displayProperties", {}).get("name")
                                sample = (perk_name, plug_hash, perk_hash, len(owners), owner_name)
                            break
        check(
            "⑥ 闭环：推荐 perk 名 → plug 物品 → sandbox perk hash → 反查「哪些枪有它」并解出名字",
            closed > 0 and sample is not None and bool(sample[4]),
            (f"{closed}/{opened} 个推荐 perk 走了通（例：{sample[0]} → plug {sample[1]} → sandbox {sample[2]} "
             f"→ {sample[3]} 件物品，例：{sample[4]}）") if sample
            else f"闭环率 0%（{opened} 个名都没走到 onItems）",
        )

        # 神器：拿我们 Manifest 里当前这件神器的模组去查归档（神器模组不是背包物品 —— 第一版
        # 拿背包物品查，命中 0，属检查写错）
        artifact = manifest.get_current_artifact() or {}
        mods = [
            int(mod.get("hash") or 0)
            for tier in (artifact.get("tiers") or [])
            for mod in (tier.get("mods") or tier.get("items") or [])
            if isinstance(mod, dict)
        ]
        linked = [(h, entity.artifact_of(h)) for h in mods if entity.artifact_of(h)]
        check(
            "⑦ 神器关联：本季神器的模组能在归档里指到神器（覆盖率为准，缺口要在响应里说明）",
            bool(mods) and bool(linked),
            f"本季神器「{artifact.get('name')}」模组 {len(mods)} 个 → 归档指到 {len(linked)} 个"
            f"（{len(linked) / max(len(mods), 1):.0%}）",
        )

        check(
            "⑧ 缺值行为：不在表里的 hash 给 None/空，不编也不炸",
            entity.item(1) is None and entity.perk(1) is None
            and entity.items_with_perk(1) == () and entity.frame_stats_for_weapon(1) == ()
            and entity.artifact_of(1) is None,
            "item/perk/反查/帧表/神器 五条查询对未知 hash 都返回空",
        )

        # 人眼看一眼 P1 会输出成什么样
        print("\n—— 渲染样例（P1 接入后会长的样子）——")
        for h in (aegis + lgpig)[:3]:
            name = (manifest.get_item_definition(h) or {}).get("displayProperties", {}).get("name", str(h))
            author_blocks = entity.authors_of(h)
            a = author_blocks.get("Aegis") or {}
            lg = author_blocks.get("LGpig") or {}
            if a:
                print(f"  {name}｜Aegis {a.get('aegis_tier')}｜枪管 {a.get('barrel')}｜"
                      f"3 号位 {a.get('perk1')}｜4 号位 {a.get('perk2')}｜{a.get('explanation_1')}")
                print(f"      来源 {a.get('aegis_source')}")
            if lg:
                print(f"  {name}｜LGpig {str(lg.get('lgpig_tier'))[:40]}｜DPS {lg.get('dps')}｜总伤 {lg.get('total_damage')}｜{lg.get('role')}")

    failed = [row for row, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 行成立")
    if failed:
        print("不成立的行：" + "；".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
