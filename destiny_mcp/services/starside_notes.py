"""把实体层的一条记录成形为响应里的"社区块"。

三条口径（计划文档第五节的硬规矩）在这里落地：

1. **两套评级各自成字段、各自带作者与刻度**，永不合并成一个"评分"；LGpig 的分场景（清怪/高难/输出）
   也分开列。
2. **缺失给 `None` + 原因**（`gaps`）：只有 748/2,208 把武器有 Aegis 推荐、339 把有 LGpig、28 个框架有
   帧表 —— 没评过就说没评过，不编。
3. **社区块永远带出处**（`attribution`：source/snapshot_at/authors/unofficial），数值附适用条件。

文本一律过 `starside_markup.render`：`{perk|…}` 翻成我们 Manifest 的官方中文名，`{unsure|…}` 保留，
`{pvp|…}` 标成 PvP 专用，解不开的原样留着并由 `unresolved` 报出来。
"""

from __future__ import annotations

import re
from typing import Any

from ..logging_config import get_logger

from . import starside_markup as markup
from .starside_entities import StarsideEntities

#: 站点帧表里的哨兵值：`INF` = 无上限（真机语料里 boss_total/minor_total 就是它）
_SENTINELS = {"INF": "∞", "-INF": "-∞"}


def _snake(key: str) -> str:
    """站点的 camelCase 键名 → 我们的 snake_case（响应信封规则，全量语料抓到的违规）。"""
    # 注意：**不要**用"大写边界插下划线"那个正则 —— 它专属 error_codes 的类名→码推导，
    # tests/test_error_codes.py 会判红（我第一版就撞了）。这里按"小写/数字后接大写"插。
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()


def _normalize_frame(row: dict[str, Any]) -> dict[str, Any]:
    """帧表一行：键名统一 snake_case + 哨兵值翻译（`INF` → `∞`）。"""
    out: dict[str, Any] = {}
    for key, value in (row or {}).items():
        out[_snake(str(key))] = _SENTINELS.get(value, value) if isinstance(value, str) else value
    return out


#: 帧表是站点实测口径 —— 只给数字不给条件就是误导（用户拍板的口径之一）
logger = get_logger(__name__)

FRAME_CONDITION = (
    "站点实测口径：数值来自作者的帧级测试（弹药类型、是否含增伤、打哪类目标见该帧表字段），"
    "不是我们对游戏机制的断言；换配装/换 perk 会变。"
)
COMMUNITY_TAGS_NOTE = "以下标签是 Starside 整理（非 Bungie 官方字段），以我们本地 Manifest 为准的项已注明。"

#: Aegis 的栏位推荐（每项都可能是"多选"，用 `\\` 分隔）
AEGIS_SLOTS = ("barrel", "magazine", "origin", "perk1", "perk2")
#: LGpig 的实测数值字段
LGPIG_NUMBERS = ("dps", "total_damage", "switch_dps")


def _name_resolver(manifest: Any):
    def resolve(name: str) -> str | None:
        try:
            found = manifest.search(name, limit=1)
        except Exception as exc:  # 结论路径不许静默：退回原名，但**留下痕迹**
            # 注意"查名失败"和"库里没有这个名字"是两件事：下面 unresolved_names 只会说明后者，
            # 所以这里必须打日志，否则一次上游/索引故障会被读成"我们库里没有"。
            logger.warning("Starside 注记：解析引用名 %r 失败：%s", name, exc)
            return None
        if not found:
            return None
        definition = manifest.get_item_definition(int(found[0].get("itemHash") or 0)) or {}
        official = (definition.get("displayProperties") or {}).get("name")
        return official or None

    return resolve


def _render_field(value: object, names) -> dict[str, Any]:
    """一个可能是"多选"的字段 → `{"options": [...], "text": "A／B"}`。"""
    options = [markup.render(part, names=names) for part in markup.alternatives(value)]
    return {"options": options, "text": "／".join(options)}


def weapon_note(entity: StarsideEntities, manifest: Any, item_hash: int) -> dict[str, Any]:
    """武器（或任何有社区记录的东西）的社区块；没有记录时给 `available=False` + 原因。"""
    attribution = entity.attribution()
    entry = entity.item(item_hash)
    if not entry:
        return {
            "attribution": attribution,
            "available": False,
            "reason": "Starside 没有这件装备的社区记录（覆盖率见 attribution 里的 counts）。",
        }

    names = _name_resolver(manifest)
    authors = entity.authors_of(item_hash)
    block: dict[str, Any] = {"attribution": attribution, "available": True, "authors": {}, "gaps": []}
    unresolved: list[str] = []
    unresolved_names: set[str] = set()

    def collect(text: object) -> None:
        """把一段站点文本里的记号与查不到的名字都记下来（留痕，不静默）。"""
        unresolved.extend(markup.unknown_tokens(str(text)))
        for perk_name in markup.perk_names(text):
            if not names(perk_name):
                unresolved_names.add(perk_name)

    aegis = authors.get("Aegis")
    if aegis:
        slots = {slot: _render_field(aegis.get(slot), names) for slot in AEGIS_SLOTS if aegis.get(slot)}
        block["authors"]["Aegis"] = {
            "tier": aegis.get("aegis_tier") or None,
            "scale": "Aegis 总榜：S/A/B/C/D/E/F",
            **slots,
            "masterwork": markup.render(aegis.get("masterwork"), names=names) or None,
            "explanation": markup.render(aegis.get("explanation_1"), names=names) or None,
            "source": markup.render(aegis.get("aegis_source"), names=names) or None,
            "alias": aegis.get("name") or None,
        }
        for value in aegis.values():
            collect(value)
    else:
        block["gaps"].append("Aegis 没有评过这把（748/2208 把武器有）")

    lgpig = authors.get("LGpig")
    if lgpig:
        numbers = {}
        for key in LGPIG_NUMBERS:
            if lgpig.get(key) in (None, ""):
                continue
            text, numeric = markup.number(lgpig[key])
            numbers[key] = text
            if not numeric:
                block["gaps"].append(f"LGpig 的 {key} 不是纯数值（原文 {text!r}），照原样给出")
        block["authors"]["LGpig"] = {
            "tier": str(lgpig.get("lgpig_tier") or "").strip() or None,
            "scale": "LGpig 分场景榜：T0/T0.5/…/T4，按 清怪／高难／输出 分别给",
            "tier_explanation": markup.render(lgpig.get("lgpig_tier_explanation"), names=names) or None,
            "role": [part for part in markup.alternatives(lgpig.get("role"))] or None,
            "numbers": numbers or None,
            "perk1": _render_field(lgpig.get("perk1"), names) if lgpig.get("perk1") else None,
            "perk2": _render_field(lgpig.get("perk2"), names) if lgpig.get("perk2") else None,
            "explanations": [
                text
                for key in ("explanation_1", "explanation_2", "explanation_3")
                if (text := markup.render(lgpig.get(key), names=names))
            ],
            "notes": markup.render(lgpig.get("notes"), names=names) or None,
            "source": markup.render(lgpig.get("lgpig_source"), names=names) or None,
        }
        if not block["authors"]["LGpig"]["tier"]:
            block["gaps"].append("LGpig 只写了说明、没给评级（按未评级展示）")
        for value in lgpig.values():
            collect(value)
    else:
        block["gaps"].append("LGpig 没有评过这把（339/2208 把武器有）")

    zh = entry.get("zh") or {}
    if zh.get("realgame_details"):
        block["mechanism"] = markup.render(zh["realgame_details"], names=names)
        collect(zh["realgame_details"])
    if zh.get("site_source"):
        block["source_text"] = markup.render(zh["site_source"], names=names)

    frames = entity.frame_stats_for_weapon(item_hash)
    if frames:
        rows = [_normalize_frame(row) for row in frames]
        row = rows[0]
        block["frame"] = {
            "headline": {
                key: row.get(key)
                for key in ("typical_mdps", "typical_edps", "max_mdps", "body_mdps",
                            "boss_total", "minor_total", "base_damage", "base_interval", "ammo_type")
                if row.get(key) is not None
            },
            "rows": rows,
            "condition": FRAME_CONDITION,
        }
    else:
        block["gaps"].append("这把的框架没有帧级实测表（28 个框架有）")

    derived = entry.get("derived") or {}
    tags = {_snake(k): derived.get(k) for k in ("release", "season", "foundry", "craftable", "tierable") if derived.get(k)}
    for key in ("site_elements", "site_season", "site_weaponTypes"):
        if zh.get(key):
            tags[_snake(key)] = zh[key]
    for key in ("isAdept", "isHolofoil"):
        if entry.get(key):
            tags[_snake(key)] = True
    if entry.get("sameAs"):
        tags["same_as"] = entry["sameAs"]
    if tags:
        block["tags"] = {**tags, "note": COMMUNITY_TAGS_NOTE}
    if unresolved:
        block["unresolved_tokens"] = sorted(set(unresolved))
    if unresolved_names:
        # 站点用了我们库里没有的名字（真机语料实测占 2.4%，多为品牌/瞄具叫法）：
        # 原样保留，但要说清"这不是我们查到的官方名"。
        block["unresolved_names"] = sorted(unresolved_names)
    return block


#: perk 层的注记栏目（站点自己的栏名，照原样收；值过标记解析器）
PERK_TEXT_FIELDS = (
    "realgame_details", "realgame_details#2", "效果", "属性变化",
    "冷却与槽位", "基础冷却", "冷却", "费用", "来源", "碎片槽位", "异域 PERK", "右栏",
)


def perk_note(entity: StarsideEntities, manifest: Any, perk_hash: int) -> dict[str, Any]:
    """perk / 模组 / 碎片层的社区注记（实机细节、口语效果、属性变化、冷却、来源…）。

    另外给两样**结构化的关联**：`sets`（这颗 perk 属于哪些套装，2/4 件效果）与
    `on_items`（作者的反查索引：出现在哪些物品上；名字一律回我们 Manifest 取，
    因为其中一部分物品没有社区条目 —— 见 `StarsideEntities.items_with_perk` 的说明）。
    """
    attribution = entity.attribution()
    entry = entity.perk(perk_hash)
    if not entry:
        return {
            "attribution": attribution,
            "available": False,
            "reason": "Starside 没有这颗 perk 的社区注记（perk 层覆盖 2,676/5,200）。",
        }

    names = _name_resolver(manifest)
    zh = entry.get("zh") or {}
    block: dict[str, Any] = {"attribution": attribution, "available": True, "gaps": []}
    unresolved: list[str] = []
    unresolved_names: set[str] = set()

    def collect(text: object) -> None:
        unresolved.extend(markup.unknown_tokens(str(text)))
        for perk_name in markup.perk_names(text):
            if not names(perk_name):
                unresolved_names.add(perk_name)

    texts = {}
    for field in PERK_TEXT_FIELDS:
        if zh.get(field):
            texts[field] = markup.render(zh[field], names=names)
            collect(zh[field])
    if texts:
        block["annotations"] = texts

    authors = entry.get("authors") or zh.get("authors") or {}
    notes = []
    for author, payload in (authors or {}).items():
        if not isinstance(payload, dict):
            continue
        for field, value in payload.items():
            if field in ("应用场景", "获取地点", "realgame_details"):
                notes.append(f"[{author}·{field}] {markup.render(value, names=names)}")
                collect(value)
    if notes:
        block["author_notes"] = notes

    set_names = []
    for set_hash in entity.sets_of_perk(perk_hash):
        info = manifest.get_set_bonus_by_hash(set_hash) if hasattr(manifest, "get_set_bonus_by_hash") else None
        info = info or {}
        # 套装名可能在几个键里；都拿不到时**别把裸 hash 当名字发出去**（那是把内部编号当答案），
        # 说明"名字没查到"并留痕。
        name = info.get("name") or info.get("set_name") or info.get("display_name")
        if name:
            set_names.append(str(name))
        else:
            block.setdefault("gaps", []).append(f"这套装的名字没查到（hash {set_hash}）")
    if set_names:
        block["sets"] = set_names

    weapon_types = (entry.get("site_weapons") or {}).get("itemSubType") if isinstance(entry.get("site_weapons"), dict) else None
    if weapon_types:
        block["weapon_types"] = weapon_types

    owners = entity.items_with_perk(perk_hash)
    if owners:
        examples = []
        for owner in owners[:3]:
            definition = manifest.get_item_definition(owner) or {}
            examples.append((definition.get("displayProperties") or {}).get("name") or owner)
        block["on_items"] = {"count": len(owners), "examples": examples}

    if not texts and not notes:
        block["gaps"].append("这颗 perk 只有反查索引、没有文字注记")
    if unresolved:
        block["unresolved_tokens"] = sorted(set(unresolved))
    if unresolved_names:
        block["unresolved_names"] = sorted(unresolved_names)
    return block


def _entity_of(svc: Any) -> StarsideEntities | None:
    """从服务上下文取实体层；**装配里没有它时不要炸**（替身/老上下文），交给调用方降级。"""
    return svc.get("starside_entities_svc") if hasattr(svc, "get") else None


def weapon_note_from_svc(svc: Any, item_hash: int) -> dict[str, Any] | None:
    """工具层一行调用：从服务上下文取实体层与 Manifest（`hash` 为 0 时给 `None`）。"""
    entity = _entity_of(svc)
    return weapon_note(entity, svc["manifest"], item_hash) if (entity and item_hash) else None


def perk_note_from_svc(svc: Any, perk_hash: int) -> dict[str, Any] | None:
    """同上，perk 层。"""
    return perk_note(svc["starside_entities_svc"], svc["manifest"], perk_hash) if perk_hash else None


def artifact_mod_notes(entity: StarsideEntities, manifest: Any, artifact: dict[str, Any]) -> dict[str, Any]:
    """神器 → 它那些模组的社区注记（档位/效果/实机细节/冷却），只收有社区数据的。

    神器模组**不是背包物品**（真机语料踩过：拿背包查永远 0 命中），要从我们 Manifest 的神器定义里
    取模组列表再查归档；当季覆盖不全（60%），所以 `coverage` 一起给出去。
    """
    names = _name_resolver(manifest)
    mods: list[dict[str, Any]] = []
    total = 0
    for tier_index, tier in enumerate(artifact.get("tiers") or [], start=1):
        for mod in tier.get("mods") or tier.get("items") or []:
            if not isinstance(mod, dict) or not mod.get("hash"):
                continue
            total += 1
            note = perk_note_for_plug(entity, manifest, int(mod["hash"]))
            if not note.get("available"):
                continue
            annotations = note.get("annotations") or {}
            entry = entity.item(int(mod["hash"])) or {}
            mods.append({
                "tier": tier_index,
                "hash": int(mod["hash"]),
                "name": mod.get("name") or names(str(mod.get("name") or "")) or str(mod["hash"]),
                "tier_label": mod.get("tier_label"),
                # 站点对神器模组的档位与冷却（入库时保留的字段，这里补进响应）
                "community_tier": entry.get("site_tier"),
                "community_super_tier": entry.get("site_superTier"),
                "cooldown_seconds": entry.get("site_cooldownSeconds"),
                "recovery_multiplier": entry.get("site_recoveryMultiplier"),
                "effect": annotations.get("效果") or annotations.get("realgame_details"),
                "cooldown": annotations.get("冷却与槽位") or annotations.get("基础冷却") or annotations.get("冷却"),
                "source": annotations.get("来源"),
            })
    return {
        "attribution": entity.attribution(),
        "available": bool(mods),
        "mods": mods,
        "coverage": {
            "with_community_notes": len(mods),
            "total_mods": total,
            "note": "Starside 对神器模组的整理不是逐季齐全（当前归档覆盖到赛季 28）；没注记的模组不在这里。",
        },
        "reason": "" if mods else "这件神器的模组没有社区注记（归档未覆盖本季）。",
    }


def set_notes(entity: StarsideEntities, manifest: Any, set_hash: int, set_name: str = "") -> dict[str, Any]:
    """套装 → 属于它的那些 perk 的社区评语（`onSets` 反查 + `realgame_details`）。"""
    names = _name_resolver(manifest)
    items: list[dict[str, Any]] = []
    for perk_hash in entity.perks_of_set(int(set_hash)):
        entry = entity.perk(perk_hash) or {}
        zh = entry.get("zh") or {}
        text = markup.render(zh.get("realgame_details"), names=names) or markup.render(
            zh.get("效果"), names=names
        )
        if not text:
            continue
        definition = manifest.get_item_definition(int(perk_hash)) or {}
        items.append({
            "perk_hash": int(perk_hash),
            "perk": (definition.get("displayProperties") or {}).get("name") or zh.get("name") or int(perk_hash),
            "note": text,
        })
    return {
        "attribution": entity.attribution(),
        "available": bool(items),
        "set": set_name or set_hash,
        "perks": items,
        "reason": "" if items else "这套装的 perk 没有社区评语。",
    }


def fragment_note(entity: StarsideEntities, manifest: Any, fragment_name: str, fragment_hash: int = 0) -> dict[str, Any]:
    """碎片/星象：属性变化（分职业）、效果、冷却、碎片槽位。

    碎片按**名字**给（`subclass_assistant(intent="fragment_details")` 就是这么收的），所以这里先按名
    找 hash（找不到就用调用方给的），再查归档 —— 查不到就说查不到。
    """
    target = int(fragment_hash or 0)
    if not target and fragment_name:
        found = manifest.search(fragment_name, limit=1)
        target = int(found[0].get("itemHash") or 0) if found else 0
    note = (
        perk_note_for_plug(entity, manifest, target)
        if target
        else {"available": False, "reason": "没找到这个碎片的 hash。"}
    )
    if not note.get("available"):
        return {**note, "attribution": entity.attribution()}
    annotations = note.get("annotations") or {}
    out: dict[str, Any] = {
        "attribution": note["attribution"],
        "available": True,
        "stat_changes": annotations.get("属性变化"),
        "effect": annotations.get("效果") or annotations.get("realgame_details"),
        "cooldown": annotations.get("冷却与槽位") or annotations.get("基础冷却") or annotations.get("冷却"),
        "slot": annotations.get("碎片槽位"),
        "source": annotations.get("来源"),
        "gaps": note.get("gaps") or [],
    }
    if not out["stat_changes"]:
        out["gaps"].append("这颗碎片没有社区整理的属性变化（按职业分列的那种）")
    for key in ("unresolved_tokens", "unresolved_names"):
        if note.get(key):
            out[key] = note[key]
    return out


def class_item_pairs(entity: StarsideEntities, manifest: Any, item_hash: int) -> dict[str, Any]:
    """异域职业物品的**双栏配对**（"之灵"两条一组的组合说明）。

    数据在 perk 层的 `右栏`（配对的另一条）与 `realgame_details#2`（组合后的实际效果）上；
    物品自己的 `site_perkColumns` 给出栏位分组。
    """
    entry = entity.item(item_hash) or {}
    columns = entry.get("site_perkColumns") or []
    perks: list[dict[str, Any]] = []
    if columns:
        for column in columns:
            for perk_hash in column:
                note = perk_note_for_plug(entity, manifest, int(perk_hash))
                zh_notes = note.get("annotations") or {}
                definition = manifest.get_item_definition(int(perk_hash)) or {}
                perks.append({
                    "hash": int(perk_hash),
                    "name": (definition.get("displayProperties") or {}).get("name") or int(perk_hash),
                    "paired_with": zh_notes.get("右栏"),
                    "effect": zh_notes.get("realgame_details#2") or zh_notes.get("realgame_details"),
                })
    return {
        "attribution": entity.attribution(),
        "available": bool(perks),
        "columns": len(columns),
        "perks": perks,
        "reason": "" if perks else "这件物品没有异域职业物品的双栏数据。",
    }


def sandbox_perk_hash(manifest: Any, plug_hash: int) -> int | None:
    """plug（插槽物品）hash → 它背后那颗 **sandbox perk** 的 hash。

    语料挖出来的关键区分：`manifest.search`／插槽池给的是 **plug 物品**，而归档 `sandbox-perks.json`
    的键在 `DestinySandboxPerkDefinition` 空间里，plug 的 `perks[].perkHash` 才是后者。
    第一版把它们当一回事，六个入口全查不到 —— 这条函数就是那个坑的单一出处。
    """
    definition = manifest.get_item_definition(int(plug_hash)) or {}
    for perk in definition.get("perks") or []:
        if isinstance(perk, dict) and perk.get("perkHash"):
            return int(perk["perkHash"])
    return None


def perk_note_for_plug(entity: StarsideEntities, manifest: Any, plug_hash: int) -> dict[str, Any]:
    """按 **plug hash** 拿 perk 注记：先解成 sandbox perk hash，解不出就退回 plug hash 本身。"""
    sandbox = sandbox_perk_hash(manifest, plug_hash)
    if sandbox and entity.perk(sandbox):
        return perk_note(entity, manifest, sandbox)
    return perk_note(entity, manifest, plug_hash)


def sandbox_of_plug_from_svc(svc: Any, plug_hash: int) -> int:
    """给工具层：plug hash → sandbox perk hash（拿不到就给原值，让查不到如实发生）。"""
    manifest = svc.get("manifest") if hasattr(svc, "get") else None
    return (sandbox_perk_hash(manifest, plug_hash) if manifest else None) or int(plug_hash)


def perk_note_for_plug_from_svc(svc: Any, plug_hash: int) -> dict[str, Any] | None:
    """工具层一行调用：**plug hash 或 sandbox hash 混着给都行**（沙盒查不到就退回原 hash）。

    这是那条坑的单一出处：`analyze`/`perk_description`/神器模组/护甲模组/碎片 都走它，
    别各自再写一遍"先解沙盒再查"。
    """
    entity = _entity_of(svc)
    return (
        perk_note_for_plug(entity, svc["manifest"], int(plug_hash))
        if (entity and plug_hash)
        else None
    )
