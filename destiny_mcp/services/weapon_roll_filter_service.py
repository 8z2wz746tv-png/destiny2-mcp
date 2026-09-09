"""Weapon roll filtering service.

This keeps user-specified perk filtering out of MCP tool wrappers. It does
not judge god rolls or dismantle candidates; it only applies explicit filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..exceptions import ManifestError
from ..manifest import class_type_name, resolve_character_name

if TYPE_CHECKING:
    from ..manifest import ManifestManager

class WeaponRollFilterService:
    """Filter weapon instances by explicit name, location, and perk terms."""

    def __init__(self, manifest: "ManifestManager | None" = None) -> None:
        self._manifest = manifest

    def filter_rolls(
        self,
        weapons: list[dict[str, Any]],
        *,
        weapon_name: str = "",
        location: str = "",
        required_perks: list[str] | str | None = None,
        any_perks: list[str] | str | None = None,
        excluded_perks: list[str] | str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return weapons that match user-provided perk filters."""
        required = self._split_terms(required_perks)
        any_terms = self._split_terms(any_perks)
        excluded = self._split_terms(excluded_perks)
        location_key = location.strip().lower()
        if location_key in {"", "all", "全部", "account", "账号"}:
            location_key = ""
        elif location_key in {"vault", "仓库"}:
            location_key = "vault"
        else:
            location_key = class_type_name(resolve_character_name(location_key)).lower()
        name_key = weapon_name.strip().lower()
        matched: list[dict[str, Any]] = []
        not_matched: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        english_perks: dict[int, str] = {}
        has_perk_filters = bool(required or any_terms or excluded)
        needs_english_names = any(
            term.isascii() for term in required + any_terms + excluded
        )

        for weapon in weapons:
            if name_key and name_key not in str(weapon.get("name", "")).lower():
                continue
            weapon_location = str(weapon.get("location", "")).lower()
            if weapon_location == "仓库":
                weapon_location = "vault"
            if location_key and location_key != weapon_location:
                continue

            perk_names = self._perk_names(weapon)
            item = self._compact_weapon(weapon, perk_names)
            if has_perk_filters and not weapon.get("perks_complete", bool(perk_names)):
                item["reason"] = "当前 Perk 插槽数据缺失或无法解析，不能判断是否匹配"
                unknown.append(item)
                continue
            perk_keys = [name.lower() for name in perk_names]
            if needs_english_names and self._manifest is not None:
                for socket in weapon.get("sockets", []) or []:
                    plug_hash = socket.get("plug_hash")
                    if plug_hash:
                        if plug_hash not in english_perks:
                            english_perks[plug_hash] = self._manifest.get_english_name(plug_hash).lower()
                        if english_perks[plug_hash]:
                            perk_keys.append(english_perks[plug_hash])
            has_required = all(any(term in perk for perk in perk_keys) for term in required)
            has_any = not any_terms or any(any(term in perk for perk in perk_keys) for term in any_terms)
            has_excluded = any(any(term in perk for perk in perk_keys) for term in excluded)

            if has_required and has_any and not has_excluded:
                item["reason"] = "命中筛选条件"
                matched.append(item)
            else:
                item["reason"] = "未满足 required/any/excluded 条件"
                not_matched.append(item)

        result_limit = max(1, min(limit, 200))
        return {
            "scope": "owned_inventory",
            "scope_label": "账号持有武器实例",
            "perk_scope": "current_sockets",
            "coverage_complete": not unknown,
            "scoped_count": len(matched) + len(not_matched) + len(unknown),
            "checked_count": len(matched) + len(not_matched),
            "matched_count": len(matched),
            "unknown_count": len(unknown),
            "matched": matched[:result_limit],
            "not_matched": not_matched[:result_limit],
            "unknown": unknown[:result_limit],
            "returned_count": min(len(matched), result_limit),
            "truncated": len(matched) > result_limit,
            "filters": {
                "weapon_name": weapon_name,
                "location": location,
                "required_perks": required,
                "any_perks": any_terms,
                "excluded_perks": excluded,
            },
        }

    def filter_catalog(
        self,
        *,
        weapon_name: str = "",
        weapon_type: str = "",
        required_perks: list[str] | str | None = None,
        any_perks: list[str] | str | None = None,
        excluded_perks: list[str] | str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Find weapon definitions in the complete Manifest catalog.

        This path intentionally does not read a player profile.  It is used
        for questions such as "all rocket launchers with Demolitionist" and
        marks each result as not ownership-checked.
        """
        if self._manifest is None:
            raise ManifestError("全量武器查询需要已加载的 Manifest。")

        required = self._split_terms(required_perks)
        any_terms = self._split_terms(any_perks)
        excluded = self._split_terms(excluded_perks)
        candidates = self._manifest.list_weapon_catalog(
            weapon_type,
            weapon_name=weapon_name,
        )
        matched: list[dict[str, Any]] = []

        for candidate in candidates:
            perk_details = self._catalog_perk_details(candidate)
            perk_names = [str(perk["name"]) for perk in perk_details if perk.get("name")]
            perk_keys = [name.casefold() for name in perk_names]
            has_required = all(
                any(term in perk for perk in perk_keys) for term in required
            )
            has_any = not any_terms or any(
                any(term in perk for perk in perk_keys) for term in any_terms
            )
            has_excluded = any(
                any(term in perk for perk in perk_keys) for term in excluded
            )
            if not (has_required and has_any and not has_excluded):
                continue

            matched_perks = sorted({
                name
                for name in perk_names
                if any(term in name.casefold() for term in required + any_terms)
            })
            matched_perk_details = [
                perk for perk in perk_details if perk.get("name") in matched_perks
            ]
            matched.append(self._compact_catalog_weapon(
                candidate,
                matched_perks,
                matched_perk_details,
            ))

        result_limit = max(1, min(limit, 200))
        return {
            "scope": "manifest_catalog",
            "scope_label": "全量武器定义候选（未读取账号持有情况）",
            "checked_count": len(candidates),
            "matched_count": len(matched),
            "matched": matched[:result_limit],
            "filters": {
                "weapon_name": weapon_name,
                "weapon_type": weapon_type,
                "required_perks": required,
                "any_perks": any_terms,
                "excluded_perks": excluded,
            },
        }

    def _catalog_perk_details(self, weapon: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract possible plugs and localized Manifest details."""
        if self._manifest is None:
            return []

        definition = self._manifest.get_item_definition(int(weapon.get("itemHash", 0)))
        if not isinstance(definition, dict):
            return []

        entries = (definition.get("sockets") or {}).get("socketEntries", [])
        details: list[dict[str, Any]] = []
        seen_hashes: set[int] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("randomizedPlugSetHash", "reusablePlugSetHash"):
                plug_set_hash = entry.get(key)
                if not plug_set_hash:
                    continue
                for plug in self._manifest.get_plug_set_plugs(int(plug_set_hash)) or []:
                    plug_hash = int(plug.get("plugItemHash", 0))
                    if not plug_hash or plug_hash in seen_hashes:
                        continue
                    name = str(plug.get("name") or "")
                    if not name:
                        info = self._manifest.get_item_info(plug_hash)
                        name = str(info.get("name") or "") if isinstance(info, dict) else ""
                    if name:
                        details.append(self._build_catalog_perk_detail(
                            plug_hash,
                            name,
                            str(plug.get("plugCategoryIdentifier") or ""),
                        ))
                    seen_hashes.add(plug_hash)

            initial_hash = entry.get("singleInitialItemHash")
            if initial_hash:
                plug_hash = int(initial_hash)
                if plug_hash in seen_hashes:
                    continue
                info = self._manifest.get_item_info(plug_hash)
                name = str(info.get("name") or "") if isinstance(info, dict) else ""
                if name:
                    details.append(self._build_catalog_perk_detail(plug_hash, name, ""))
                seen_hashes.add(plug_hash)
        return details

    def _build_catalog_perk_detail(
        self,
        plug_hash: int,
        name: str,
        plug_category: str,
    ) -> dict[str, Any]:
        if self._manifest is None:
            return {
                "name": name,
                "plug_hash": plug_hash,
                "plug_category": plug_category,
                "description": "",
                "icon_url": "",
            }
        info = self._manifest.get_item_info(plug_hash)
        sandbox = self._manifest.get_sandbox_perk_description(plug_hash)
        description = str(sandbox.get("description") or "") if isinstance(sandbox, dict) else ""
        if not description:
            item_description = self._manifest.get_item_description(plug_hash)
            if isinstance(item_description, str):
                description = item_description
        if not plug_category:
            category = self._manifest.get_plug_category_identifier(plug_hash)
            plug_category = category if isinstance(category, str) else ""
        return {
            "name": name,
            "plug_hash": plug_hash,
            "plug_category": plug_category,
            "description": description,
            "icon_url": str(info.get("icon") or "") if isinstance(info, dict) else "",
        }

    @classmethod
    def _compact_catalog_weapon(
        cls,
        weapon: dict[str, Any],
        matched_perks: list[str],
        matched_perk_details: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tier = int(weapon.get("tier") or 0)
        return {
            "name": weapon.get("name", ""),
            "nameEn": weapon.get("nameEn", ""),
            "item_hash": weapon.get("itemHash", 0),
            "weapon_type": weapon.get("itemTypeNameDisplay", ""),
            "tier": {5: "传说", 6: "异域"}.get(tier, ""),
            "damage_type": {
                0: "", 1: "动能", 2: "电弧", 3: "烈日", 4: "虚空",
                5: "冰影", 6: "编织", 7: "棱镜",
            }.get(weapon.get("damageType", 0), ""),
            "ammo_type": {1: "主要", 2: "特殊", 3: "威能"}.get(
                weapon.get("ammoType", 0), ""
            ),
            "matched_perks": matched_perks,
            "matched_perk_details": matched_perk_details,
            "icon_url": weapon.get("icon", ""),
            "owned": False,
            "ownership_checked": False,
            "source": "manifest_catalog",
        }

    @staticmethod
    def _split_terms(values: list[str] | str | None) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [part.strip().lower() for part in values.split(",") if part.strip()]
        return [str(part).strip().lower() for part in values if str(part).strip()]

    @staticmethod
    def _perk_names(weapon: dict[str, Any]) -> list[str]:
        sockets = weapon.get("sockets", []) or []
        return [
            str(socket.get("plug_name", ""))
            for socket in sockets
            if socket.get("plug_name")
        ]

    @staticmethod
    def _compact_weapon(weapon: dict[str, Any], perk_names: list[str]) -> dict[str, Any]:
        return {
            "name": weapon.get("name", ""),
            "instance_id": weapon.get("instance_id", ""),
            "item_hash": weapon.get("item_hash", 0),
            "weapon_type": weapon.get("weapon_type", ""),
            "location": weapon.get("location", ""),
            "power": weapon.get("power"),
            "is_equipped": weapon.get("is_equipped", False),
            "perks": perk_names,
            "icon_url": weapon.get("icon_url", ""),
        }
