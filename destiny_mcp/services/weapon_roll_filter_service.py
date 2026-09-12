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
from . import weapon_payload, weapon_profile
from .weapon_payload import LEAN_IDENTITY_KEYS

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
            identity = weapon.get("weapon") or {}
            instance = identity.get("instance") or {}
            if name_key and name_key not in str(identity.get("name", "")).lower():
                continue
            weapon_location = str(instance.get("location", "")).lower()
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
                for socket in self._equipped_plugs(weapon):
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
            definition = self._manifest.get_item_definition(int(candidate.get("itemHash", 0)))
            if not isinstance(definition, dict):
                continue
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
                definition,
                matched_perks,
                matched_perk_details,
                fallback_name=str(candidate.get("name") or ""),
            ))

        result_limit = max(1, min(limit, 200))
        returned = matched[:result_limit]
        return {
            "scope": "manifest_catalog",
            "scope_label": "全量武器定义候选（未读取账号持有情况）",
            "checked_count": len(candidates),
            "matched_count": len(matched),
            "returned_count": len(returned),
            # matched 是裁过的，matched_count 才是真实命中数：不标出来，
            # 调用方会把列表长度当成"全游戏只有这么多把"。
            "truncated": len(matched) > len(returned),
            "matched": returned,
            "filters": {
                "weapon_name": weapon_name,
                "weapon_type": weapon_type,
                "required_perks": required,
                "any_perks": any_terms,
                "excluded_perks": excluded,
            },
        }

    def _catalog_perk_details(self, weapon: dict[str, Any]) -> list[dict[str, Any]]:
        """池子里的每个 plug（走 weapon_payload，与 sockets/options 同一套解析）。

        旧实现自己遍历 socketEntries、自己读描述 —— 于是目录里的 perk 与
        `perk_pool` 里的 perk 是两套字段。现在两边同源。
        """
        if self._manifest is None:
            return []
        definition = self._manifest.get_item_definition(int(weapon.get("itemHash", 0)))
        if not isinstance(definition, dict):
            return []

        # 只把**命中**的 perk 明细带回响应，所以这里要带描述与图标；
        # 池子整体（perk_pool/info）默认不带，见 weapon_payload 的体积口径。
        details: list[dict[str, Any]] = []
        for socket in weapon_payload.socket_list(
            self._manifest, definition, include_descriptions=True, include_icons=True
        ):
            for option in socket.get("options") or []:
                details.append(option | {"slot": socket.get("slot", ""), "kind": socket.get("kind", "")})
        return details

    def _compact_catalog_weapon(
        self,
        definition: dict[str, Any],
        matched_perks: list[str],
        matched_perk_details: list[dict[str, Any]],
        *,
        fallback_name: str = "",
    ) -> dict[str, Any]:
        """目录命中行：精简身份块 + 命中 perk（不读账号，所以 owned 明说是猜的）。"""
        row = weapon_payload.lean_identity(
            self._manifest,
            definition,
            roll_kind=weapon_profile.roll_kind(definition),
            fallback_name=fallback_name,
        )
        row.update({
            "matched_perks": matched_perks,
            "matched_perk_details": matched_perk_details,
            "owned": False,
            "ownership_checked": False,
            "source": "manifest_catalog",
        })
        return row

    @staticmethod
    def _split_terms(values: list[str] | str | None) -> list[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [part.strip().lower() for part in values.split(",") if part.strip()]
        return [str(part).strip().lower() for part in values if str(part).strip()]

    @staticmethod
    def _equipped_plugs(weapon: dict[str, Any]) -> list[dict[str, Any]]:
        """这一件**当前装着**的 plug（P4 形状：定义级 sockets 上的 `equipped`）。"""
        return [
            socket["equipped"]
            for socket in weapon.get("sockets") or []
            if isinstance(socket.get("equipped"), dict) and socket["equipped"].get("plug_hash")
        ]

    @classmethod
    def _perk_names(cls, weapon: dict[str, Any]) -> list[str]:
        return [
            str(plug.get("name") or "")
            for plug in cls._equipped_plugs(weapon)
            if plug.get("name")
        ]

    @staticmethod
    def _compact_weapon(weapon: dict[str, Any], perk_names: list[str]) -> dict[str, Any]:
        """列表行：精简身份块 + 副本位置/光等 + 当前 perk。"""
        identity = weapon.get("weapon") or {}
        instance = identity.get("instance") or {}
        row = {key: identity.get(key) for key in LEAN_IDENTITY_KEYS}
        row.update({
            "instance_id": instance.get("instance_id", ""),
            "location": instance.get("location", ""),
            "power": instance.get("power"),
            "is_equipped": instance.get("is_equipped", False),
            "locked": instance.get("locked"),
            "perks": perk_names,
        })
        return row
