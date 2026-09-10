"""武器目录与条目列举：按类型、稀有度、职业、属性、弹药筛选条目。

以 mixin 挂在 ManifestManager 上，通过 self 使用 hash 索引与名字索引。
`list_weapon_catalog` 依赖 SearchIndexMixin 的 `_canonical_item_entry`，
`list_items` 的 limit 语义与它**不一致**（见 tests/test_manifest_weapon_catalog.py）。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

from .exceptions import ManifestError
from .manifest_data import CHARACTER_CLASS_MAP
from .utils.hash_utils import to_signed


class ItemCatalogMixin:
    """目录侧查询：只读索引，不写任何状态。"""

    def get_exotic_armor_by_class(self, class_name: str) -> list[dict]:
        """Get all exotic armor for a specific class.

        Args:
            class_name: 'hunter', 'warlock', 'titan' (or Chinese: 猎人, 术士, 泰坦)

        Returns:
            List of item dicts with name, itemHash, slot, etc.
        """
        class_type = CHARACTER_CLASS_MAP.get(class_name.lower(), -1)
        if class_type < 0:
            return []

        # 用名字去重，保留第一个
        seen_names = set()
        results = []
        for item_hash, item_info in self._hash_index.items():
            if (item_info.get('itemType') == 2 and      # 护甲
                item_info.get('tier') == 6 and          # 异域
                item_info.get('classType') == class_type):
                name = item_info.get('name', '')
                if name not in seen_names:
                    seen_names.add(name)
                    results.append(item_info)

        # 按名称排序
        results.sort(key=lambda x: x.get('name', ''))
        return results

    def list_weapon_catalog(
        self,
        weapon_type: str = "",
        *,
        weapon_name: str = "",
        limit: int = 0,
    ) -> list[dict]:
        """List unique weapon definitions from the loaded Manifest.

        This is separate from profile queries and therefore includes weapons
        the configured player does not own. ``limit=0`` returns all matches.
        """
        if not self._name_index:
            raise ManifestError("Manifest not loaded. Call ensure_loaded() first.")

        type_query = weapon_type.strip().casefold()
        name_query = weapon_name.strip().casefold()
        seen_hashes: set[int] = set()
        results: list[dict] = []

        for raw_hash, item in self._hash_index.items():
            if item.get("itemType") != 3:
                continue
            canonical = self._canonical_item_entry(item)
            item_hash = int(canonical.get("itemHash", raw_hash))
            signed_hash = to_signed(item_hash)
            if item_hash in seen_hashes or signed_hash in seen_hashes:
                continue
            seen_hashes.update((item_hash, signed_hash))

            display_types = (
                str(canonical.get("itemTypeNameDisplay", "")),
                str(canonical.get("itemTypeNameDisplayEn", "")),
            )
            if type_query and not any(
                self._weapon_type_matches(type_query, display_type)
                for display_type in display_types
            ):
                continue
            names = (str(canonical.get("name", "")), str(canonical.get("nameEn", "")))
            if name_query and not any(name_query in name.casefold() for name in names):
                continue
            results.append(canonical)

        results.sort(key=lambda value: (-value.get("tier", 0), value.get("name", "")))
        return results if limit <= 0 else results[:limit]

    @staticmethod

    def _weapon_type_matches(query: str, display_type: str) -> bool:
        """Match common Chinese/English weapon-type names."""
        candidate = display_type.strip().casefold()
        if not candidate:
            return False
        if query in candidate or candidate in query:
            return True
        aliases = {
            "火箭筒": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "火箭发射器": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "rocket launcher": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
            "rocket launchers": {"火箭筒", "火箭发射器", "rocket launcher", "rocket launchers"},
        }
        return any(alias in candidate or candidate in alias for alias in aliases.get(query, {query}))

    def list_items(
        self,
        item_type: str = "",
        tier: str = "",
        class_name: str = "",
        damage_type: str = "",
        ammo_type: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """List items from manifest with filters.

        Args:
            item_type: 'weapon', 'armor', 'mod', 'ghost', 'ship', 'sparrow', etc.
            tier: 'exotic', 'legendary', 'rare', 'common', 'uncommon'
            class_name: 'hunter', 'warlock', 'titan' (for armor)
            damage_type: 'kinetic', 'solar', 'arc', 'void', 'stasis', 'strand'
            ammo_type: 'primary', 'special', 'heavy'
            limit: Max results to return

        Returns:
            List of item dicts with name, itemHash, etc.
        """
        # 类型映射
        type_map = {
            "weapon": 3, "武器": 3,
            "armor": 2, "护甲": 2,
            "mod": 19, "模组": 19,
            "ghost": 24, "机灵": 24,
            "ship": 21, "飞船": 21,
            "sparrow": 22, "快雀": 22,
            "emblem": 14, "徽章": 14,
            "consumable": 9, "消耗品": 9,
            "material": 10, "材料": 10,
            "subclass": 16, "子职业": 16,
        }

        # 稀有度映射
        tier_map = {
            "exotic": 6, "异域": 6,
            "legendary": 5, "传说": 5,
            "rare": 4, "稀有": 4,
            "uncommon": 3, "罕见": 3,
            "common": 2, "普通": 2,
        }

        # 职业映射
        class_type = CHARACTER_CLASS_MAP.get(class_name.lower(), -1) if class_name else -1

        # 伤害类型映射
        damage_map = {
            "kinetic": 1, "动能": 1,
            "solar": 2, "烈日": 2,
            "arc": 3, "电弧": 3,
            "void": 4, "虚空": 4,
            "stasis": 5, "冰影": 5,
            "strand": 6, "编织": 6,
        }

        # 弹药类型映射
        ammo_map = {
            "primary": 1, "主要": 1,
            "special": 2, "特殊": 2,
            "heavy": 3, "威能": 3,
        }

        target_type = type_map.get(item_type.lower(), -1) if item_type else -1
        target_tier = tier_map.get(tier.lower(), -1) if tier else -1
        target_damage = damage_map.get(damage_type.lower(), -1) if damage_type else -1
        target_ammo = ammo_map.get(ammo_type.lower(), -1) if ammo_type else -1

        # 用名字去重
        seen_names = set()
        results = []

        for item_hash, item_info in self._hash_index.items():
            # 类型过滤
            if target_type >= 0 and item_info.get('itemType') != target_type:
                continue

            # 稀有度过滤
            if target_tier >= 0 and item_info.get('tier') != target_tier:
                continue

            # 职业过滤（仅护甲）
            if class_type >= 0 and item_info.get('itemType') == 2:
                if item_info.get('classType') != class_type:
                    continue

            # 伤害类型过滤（仅武器）
            if target_damage >= 0 and item_info.get('itemType') == 3:
                if item_info.get('damageType') != target_damage:
                    continue

            # 弹药类型过滤（仅武器）
            if target_ammo >= 0 and item_info.get('itemType') == 3:
                if item_info.get('ammoType') != target_ammo:
                    continue

            name = item_info.get('name', '')
            if not name or name in seen_names:
                continue

            seen_names.add(name)
            results.append(item_info)

            if len(results) >= limit:
                break

        # 按名称排序
        results.sort(key=lambda x: x.get('name', ''))
        return results
