"""Parse community templates without turning prose into executable commands."""

from __future__ import annotations

import re
from typing import Any

CLASS_ALIASES = {
    "猎人": "hunter",
    "hunter": "hunter",
    "术士": "warlock",
    "warlock": "warlock",
    "泰坦": "titan",
    "titan": "titan",
}
STAT_KEYS = {
    "生命": "health",
    "生命值": "health",
    "近战": "melee",
    "手雷": "grenade",
    "超能": "super",
    "职业": "class",
    "职业技能": "class",
    "武器": "weapons",
}
ARMOR_SLOTS = {
    "头盔": "helmet",
    "护臂": "gauntlets",
    "胸甲": "chest",
    "腿部": "legs",
    "职业物品": "class_item",
}
SKILL_KEYS = {
    "超能": "super",
    "星相": "aspects",
    "碎片": "fragments",
    "手雷": "grenade",
    "近战": "melee",
    "职业技能": "class_ability",
    "移动": "movement",
}
META_KEYS = {
    "推荐人": "author",
    "更新": "updated_at",
    "场景": "scenario",
    "定位": "role",
    "类别": "category",
    "分支": "subclass",
    "核心": "core",
    "描述": "description",
}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def split_values(value: str) -> list[str]:
    return [clean(part) for part in re.split(r"[、,，]", value) if clean(part)]


def parse_stat_targets(value: str) -> tuple[dict, list[str]]:
    targets, unknown = {}, []
    for part in re.split(r"[｜|]", value):
        match = re.fullmatch(r"\s*(\S+)\s+(.+?)\s*", part)
        if not match or match[1] not in STAT_KEYS:
            unknown.append(part)
            continue
        key, raw = STAT_KEYS[match[1]], match[2]
        target: dict[str, Any] = {"raw": raw, "kind": "unresolved"}
        numbers = re.fullmatch(r"(\d+)(?:([+＋])|\s*[-~～至]\s*(\d+))?", raw)
        if raw in {"~", "～", "不限", "任意"}:
            target["kind"] = "unspecified"
        elif numbers:
            minimum = int(numbers[1])
            maximum = int(numbers[3]) if numbers[3] else minimum
            if 0 <= minimum <= maximum <= 200:
                target.update(
                    kind="range"
                    if numbers[3]
                    else "minimum"
                    if numbers[2]
                    else "target",
                    min=minimum,
                )
                if numbers[3]:
                    target["max"] = maximum
        if key in targets or target["kind"] == "unresolved":
            unknown.append(part)
        else:
            targets[key] = target
    return targets, unknown


def parse_sets(value: str) -> tuple[list[dict], list[str]]:
    requirements, unknown = {}, []
    for part in re.split(r"\s*×\s*|\s+[xX]\s+", value):
        match = re.fullmatch(r"\s*(.+?)\s+(\d+)\s*件\s*", part)
        if not match or not 1 <= int(match[2]) <= 5:
            unknown.append(part)
            continue
        name, count = clean(match[1]), int(match[2])
        requirements[name] = max(requirements.get(name, 0), count)
    return [
        {"name": name, "count": count} for name, count in requirements.items()
    ], unknown


def parse_build(block: str, *, build_id: str, source: dict) -> dict:
    parsed = {
        "build_id": build_id,
        "title": source["title"],
        **{key: "" for key in META_KEYS.values()},
        "class": {},
        "weapons": [],
        "armor": {"exotic": "", "set": "", "set_requirements": [], "mods": {}},
        "artifact": {"name": "", "mods": []},
        "stat_targets": {},
        "notes": [],
        "review_notes": [],
        "unparsed": [],
        "source": dict(source),
        "raw_text": block,
        "executable": False,
    }
    section = ""
    seen = set()
    for raw_line in block.splitlines():
        line = clean(raw_line)
        if not line:
            continue
        if line.startswith("## "):
            section = line[3:]
            continue
        if line.startswith("# "):
            parsed["title"] = line[2:]
            continue
        if section in {"注解", "审核意见"}:
            parsed["notes" if section == "注解" else "review_notes"].append(line)
            continue
        match = re.fullmatch(r"([^：:]{1,24})[：:]\s*(.*)", line)
        if not match:
            parsed["unparsed"].append(line)
            continue
        label, value = clean(match[1]), clean(match[2])
        if not value:
            parsed["unparsed"].append(line)
            continue
        # Duplicate scalar fields are evidence of ambiguity, never an overwrite.
        if (section, label) in seen and label not in {"异域武器", "传说武器"}:
            parsed["unparsed"].append(line)
            continue
        seen.add((section, label))
        if not section and label in META_KEYS:
            parsed[META_KEYS[label]] = value
        elif section == "职业" and label == "职业":
            parsed["class"] |= {
                "name": value,
                "id": CLASS_ALIASES.get(value.casefold(), ""),
            }
        elif section == "职业" and label in SKILL_KEYS:
            key = SKILL_KEYS[label]
            parsed["class"][key] = (
                split_values(value) if key in {"aspects", "fragments"} else value
            )
        elif section == "武器" and label in {"异域武器", "传说武器"}:
            name, separator, perks = value.partition("|")
            parsed["weapons"].append(
                {
                    "name": clean(name),
                    "perks": split_values(perks) if separator else [],
                    "tier": "exotic" if label == "异域武器" else "legendary",
                    "raw": value,
                }
            )
        elif section == "护甲" and label == "异域护甲":
            parsed["armor"]["exotic"] = value
        elif section == "护甲" and label == "套装":
            parsed["armor"]["set"] = value
            sets, unknown = parse_sets(value)
            parsed["armor"]["set_requirements"] = sets
            parsed["unparsed"].extend(unknown)
        elif section == "护甲" and label in ARMOR_SLOTS:
            parsed["armor"]["mods"][ARMOR_SLOTS[label]] = split_values(value)
        elif section == "神器" and label == "神器":
            parsed["artifact"]["name"] = value
        elif section == "神器" and label == "模组":
            parsed["artifact"]["mods"] = split_values(value)
        elif section == "六维" and label == "六维":
            targets, unknown = parse_stat_targets(value)
            parsed["stat_targets"] = targets
            parsed["unparsed"].extend(unknown)
        else:
            parsed["unparsed"].append(line)
    parsed["source"]["build_updated_at"] = parsed["updated_at"]
    parsed["source"]["content_scope"] = "community_build_template"
    return parsed
