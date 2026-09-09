"""Normalizer — BuildDraft → CanonicalBuild。

名称→Hash 的确定性转换逻辑。匹配优先级：
1. Exact Match（manifest 精确命中）
2. Community Terms（community_terms.yaml）
3. Fuzzy Match（编辑距离匹配）
4. Failure（标记失败，禁止猜测）

参见 ADR-007: Canonical Build Strategy。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import yaml

from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..build_contracts import BuildRecipe
from .models import BuildDraft

logger = get_logger(__name__)

# Renegades 属性名（代码名 → 中文名映射）
# 从 manifest 中文版查询的官方翻译
STAT_NAME_ALIASES: dict[str, list[str]] = {
    "weapons": ["weapons", "武器", "weapon"],
    "health": ["health", "生命值", "生命", "韧性", "resilience"],
    "class_stat": ["class_stat", "class", "职业", "恢复", "recovery"],
    "grenade": ["grenade", "手雷", "纪律", "discipline"],
    "melee": ["melee", "近战", "力量", "strength"],
    "super_stat": ["super_stat", "super", "超能", "智慧", "intellect"],
}

# 职业名映射
CLASS_ALIASES: dict[str, str] = {
    "猎人": "hunter",
    "术士": "warlock",
    "泰坦": "titan",
    "hunter": "hunter",
    "warlock": "warlock",
    "titan": "titan",
}

# Fuzzy match 阈值
_FUZZY_THRESHOLD = 0.75


class Normalizer:
    """BuildDraft → CanonicalBuild。"""

    def __init__(self, manifest: ManifestManager) -> None:
        self._manifest = manifest
        self._community_terms = self._load_community_terms()

    def _load_community_terms(self) -> dict:
        """加载 community_terms.yaml。"""
        terms_path = Path(__file__).parent / "community_terms.yaml"
        if not terms_path.exists():
            logger.warning("community_terms.yaml not found at %s", terms_path)
            return {}
        with open(terms_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if data else {}

    def normalize(self, draft: BuildDraft) -> tuple[BuildRecipe, list[str]]:
        """转换 BuildDraft 为 CanonicalBuild。

        Returns:
            (canonical_build, errors) — errors 中包含所有解析失败的描述。
        """
        errors: list[str] = []
        build = BuildRecipe()

        # Class type
        if draft.class_name:
            class_type = self._resolve_class(draft.class_name)
            if class_type:
                build.class_type = class_type
            else:
                errors.append(f"无法识别职业: {draft.class_name}")

        # Exotic
        if draft.exotic_name:
            exotic_hash = self._resolve_item(draft.exotic_name, "exotic")
            if exotic_hash:
                build.exotic_hash = exotic_hash
            else:
                errors.append(f"无法识别金装: {draft.exotic_name}")

        # Weapons
        for weapon_name in draft.weapon_names:
            weapon_hash = self._resolve_item(weapon_name, "weapon")
            if weapon_hash:
                build.weapon_hashes.append(weapon_hash)
            else:
                errors.append(f"无法识别武器: {weapon_name}")

        # Super
        if draft.super_name:
            super_hash = self._resolve_item(draft.super_name, "super")
            if super_hash:
                build.super_hash = super_hash
            else:
                errors.append(f"无法识别超能: {draft.super_name}")

        # Grenade
        if draft.grenade_name:
            grenade_hash = self._resolve_item(draft.grenade_name, "grenade")
            if grenade_hash:
                build.grenade_hash = grenade_hash
            else:
                errors.append(f"无法识别手雷: {draft.grenade_name}")

        # Melee
        if draft.melee_name:
            melee_hash = self._resolve_item(draft.melee_name, "melee")
            if melee_hash:
                build.melee_hash = melee_hash
            else:
                errors.append(f"无法识别近战: {draft.melee_name}")

        # Class ability
        if draft.class_ability_name:
            ability_hash = self._resolve_item(draft.class_ability_name, "class_ability")
            if ability_hash:
                build.class_ability_hash = ability_hash
            else:
                errors.append(f"无法识别职业技能: {draft.class_ability_name}")

        # Movement
        if draft.movement_name:
            movement_hash = self._resolve_item(draft.movement_name, "movement")
            if movement_hash:
                build.movement_hash = movement_hash
            else:
                errors.append(f"无法识别移动技能: {draft.movement_name}")

        # Aspects
        for aspect_name in draft.aspect_names:
            aspect_hash = self._resolve_item(aspect_name, "aspect")
            if aspect_hash:
                build.aspect_hashes.append(aspect_hash)
            else:
                errors.append(f"无法识别星象: {aspect_name}")

        # Fragments
        for fragment_name in draft.fragment_names:
            fragment_hash = self._resolve_item(fragment_name, "fragment")
            if fragment_hash:
                build.fragment_hashes.append(fragment_hash)
            else:
                errors.append(f"无法识别碎片: {fragment_name}")

        # Target stats
        for raw_name, value in draft.target_stats.items():
            canonical_name = self._resolve_stat_name(raw_name)
            if canonical_name:
                build.target_stats[canonical_name] = value
            else:
                errors.append(f"无法识别属性: {raw_name}")

        return build, errors

    def _resolve_class(self, name: str) -> str | None:
        """解析职业名。"""
        lower = name.strip().lower()
        return CLASS_ALIASES.get(lower)

    def _resolve_item(self, name: str, item_type: str) -> int | None:
        """解析物品/技能名称为 hash。按优先级尝试。"""
        name = name.strip()
        if not name:
            return None

        # 1. Exact match in manifest
        hash_id = self._exact_match(name)
        if hash_id:
            return hash_id

        # 2. Community terms
        hash_id = self._community_terms_match(name, item_type)
        if hash_id:
            return hash_id

        # 3. Fuzzy match
        hash_id = self._fuzzy_match(name)
        if hash_id:
            return hash_id

        return None

    def _exact_match(self, name: str) -> int | None:
        """精确匹配 manifest。"""
        results = self._manifest.search(name, limit=5)
        for item in results:
            item_name = item.get("name", "")
            if item_name.lower() == name.lower():
                return item.get("itemHash")
        return None

    def _community_terms_match(self, name: str, item_type: str) -> int | None:
        """社区术语匹配。"""
        # Check compound terms (e.g., "金枪猎" → class + super)
        compound = self._community_terms.get("compound", {})
        if name in compound:
            entry = compound[name]
            # For compound terms, return the relevant hash based on item_type
            if item_type == "super" and "super" in entry:
                return self._resolve_from_manifest_by_name(entry["super"], "super")
            if item_type == "exotic" and "exotic" in entry:
                return self._resolve_from_manifest_by_name(entry["exotic"], "exotic")
            return None

        # Check direct aliases (case-insensitive)
        aliases = self._community_terms.get("aliases", {})
        name_lower = name.lower()
        for alias_key, canonical in aliases.items():
            if alias_key.lower() == name_lower:
                return self._resolve_from_manifest_by_name(canonical, item_type)

        return None

    def _resolve_from_manifest_by_name(self, name: str, item_type: str) -> int | None:
        """通过名称从 manifest 解析 hash。"""
        results = self._manifest.search(name, limit=5)
        for item in results:
            item_name = item.get("name", "")
            if item_name.lower() == name.lower():
                return item.get("itemHash")
        # Fallback: try fuzzy
        return self._fuzzy_match(name)

    def _fuzzy_match(self, name: str) -> int | None:
        """模糊匹配（编辑距离）。"""
        results = self._manifest.search(name, limit=10)
        best_ratio = 0.0
        best_hash = None
        for item in results:
            item_name = item.get("name", "")
            ratio = SequenceMatcher(None, name.lower(), item_name.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_hash = item.get("itemHash")
        if best_ratio >= _FUZZY_THRESHOLD and best_hash:
            logger.info("Fuzzy match: '%s' → '%s' (%.2f)", name, best_hash, best_ratio)
            return best_hash
        return None

    def _resolve_stat_name(self, raw_name: str) -> str | None:
        """解析属性名为 Renegades 代码名。"""
        lower = raw_name.strip().lower()
        for canonical, aliases in STAT_NAME_ALIASES.items():
            if lower in aliases:
                return canonical
        return None
