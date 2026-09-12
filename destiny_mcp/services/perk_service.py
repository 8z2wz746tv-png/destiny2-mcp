"""Perk service — weapon perk pool lookups from manifest.

Pure manifest-based queries, no Bungie API calls.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..exceptions import ManifestError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..models import PerkInfo, WeaponPerkPool, WeaponPerkSlot
from .wishlist_service import WishListService
from .weapon_popularity_service import WeaponPopularityService

logger = get_logger(__name__)

# Socket category hashes
_CAT_WEAPON_PERKS = 4241085061   # WEAPON PERKS (barrels, magazines, perks)

# Plug categories to include in perk pool output
_PERK_CATEGORIES = {
    "barrels", "barrel", "sights", "scopes", "magazines", "magazine",
    "batteries", "grips", "stocks", "perks", "frames",
}


def _categorize_slot(plug_category: str) -> str:
    """Map a plugCategoryIdentifier to a human-readable slot name."""
    cat = plug_category.lower().split(".")[-1] if plug_category else ""
    if cat in ("barrels", "barrel"):
        return "barrel"
    if cat in ("sights", "scopes"):
        return "sight"
    if cat in ("magazines", "magazine", "batteries"):
        return "magazine"
    if cat in ("grips", "stocks"):
        return "grip"
    if cat in ("frames",):
        return "intrinsic"
    return "perk"


class PerkService:
    """Manifest-only weapon perk pool queries."""

    def __init__(
        self,
        manifest: ManifestManager,
        wishlist: WishListService | None = None,
        popularity: WeaponPopularityService | None = None,
    ) -> None:
        self._manifest = manifest
        self._wishlist = wishlist
        self._popularity = popularity or WeaponPopularityService(manifest)

    def get_weapon_popularity(self, weapon_name: str) -> dict | None:
        """Return a recorded selection-rate snapshot, if one exists."""
        return self._popularity.get_weapon_popularity(weapon_name)

    def annotate_god_roll(
        self, item_hash: int, plug_hash: int, perk: PerkInfo
    ) -> None:
        """Annotate a PerkInfo with god roll flags if wish list data exists."""
        if not self._wishlist:
            return
        god_roll = self._wishlist.is_god_roll_perk(item_hash, plug_hash)
        perk.god_roll_pve = god_roll["pve"]
        perk.god_roll_pvp = god_roll["pvp"]

    async def get_weapon_perks(self, weapon_name: str) -> WeaponPerkPool:
        """Get the full perk pool for a weapon by name.

        Looks up the weapon definition in the manifest and extracts all
        possible perks from randomizedPlugSetHash / reusablePlugSetHash.

        Args:
            weapon_name: Partial or full weapon name (Chinese or English).

        Returns:
            WeaponPerkPool with perks grouped by slot.

        Raises:
            ManifestError: If no weapon matches the name.
        """
        logger.info("Looking up perk pool for: %s", weapon_name)

        # Step 1: Search manifest for weapon
        results = self._manifest.search(weapon_name, limit=10)
        weapon = None
        for r in results:
            if r["itemType"] == 3:  # Weapon
                weapon = r
                break
        if not weapon:
            # 名字在 Manifest 里找不到武器 —— 这是 manifest_error，不是
            # item_not_found_error（后者表示"账号里的东西没了"，用在这里会误导）。
            raise ManifestError(f"找不到武器: {weapon_name}")

        item_hash = weapon["itemHash"]
        weapon_display_name = weapon["name"]

        # Step 2: Get full weapon definition
        definition = self._manifest.get_item_definition(item_hash)
        if not definition:
            raise ManifestError(f"找不到武器定义: {weapon_name}")

        weapon_type = definition.get("itemTypeDisplayName", "")

        # Step 3: Extract socket entries
        socket_entries = (
            definition.get("sockets", {}).get("socketEntries", [])
        )
        socket_categories = (
            definition.get("sockets", {}).get("socketCategories", [])
        )

        # Find which socket indexes belong to WEAPON PERKS category
        perk_socket_indexes: set[int] = set()
        for cat in socket_categories:
            if cat.get("socketCategoryHash") == _CAT_WEAPON_PERKS:
                perk_socket_indexes.update(cat.get("socketIndexes", []))

        # Step 4: Extract perk pool from each relevant socket
        slots: list[WeaponPerkSlot] = []
        for idx in sorted(perk_socket_indexes):
            if idx >= len(socket_entries):
                continue
            socket = socket_entries[idx]

            plug_set_hash = socket.get("randomizedPlugSetHash") or socket.get("reusablePlugSetHash")
            if not plug_set_hash:
                continue

            plug_items = self._manifest.get_plug_set_plugs(plug_set_hash)
            if not plug_items:
                continue

            # Sub-group plugs by their plugCategoryIdentifier
            grouped: dict[str, list[PerkInfo]] = {}
            seen_hashes: set[int] = set()
            for plug in plug_items:
                ph = plug["plugItemHash"]
                if ph in seen_hashes:
                    continue
                seen_hashes.add(ph)

                cat_id = plug.get("plugCategoryIdentifier", "")
                slot_label = _categorize_slot(cat_id)

                desc = ""
                sandbox_info = self._manifest.get_sandbox_perk_description(ph)
                if sandbox_info:
                    desc = sandbox_info.get("description", "")
                if not desc:
                    item_description = self._manifest.get_item_description(ph)
                    if isinstance(item_description, str):
                        desc = item_description

                perk = PerkInfo(
                    plug_hash=ph,
                    name=plug["name"],
                    description=desc,
                    plug_category=cat_id,
                    icon_url=_item_icon_url(self._manifest, ph),
                )
                self.annotate_god_roll(item_hash, ph, perk)
                grouped.setdefault(slot_label, []).append(perk)

            for slot_label, perks in grouped.items():
                slots.append(WeaponPerkSlot(slot_name=slot_label, plugs=perks))

        logger.info(
            "Perk pool for '%s': %d slot(s), %d total perks",
            weapon_display_name,
            len(slots),
            sum(len(s.plugs) for s in slots),
        )

        return WeaponPerkPool(
            weapon_name=weapon_display_name,
            weapon_type=weapon_type,
            item_hash=item_hash,
            icon_url=str(weapon.get("icon") or ""),
            slots=slots,
        )

    async def get_god_roll(self, weapon_name: str) -> str:
        """Get god roll recommendation for a weapon from DIM wish list.

        Args:
            weapon_name: Weapon name to look up.

        Returns:
            Formatted string with PvE/PvP god roll perks, or "暂无推荐".
        """
        results = self._manifest.search(weapon_name, limit=10)
        weapon = None
        for r in results:
            if r.get("itemType") == 3:
                weapon = r
                break

        if not weapon:
            # 「武器不存在」和「武器存在但本地愿单没收录」是两件事：
            # 前者走错误信封（与 analyze/info/catalyst 一致），后者才是成功的说明文字。
            # 以前两者都当成功返回，用户分不清是打错名字还是真没数据。
            raise ManifestError(f"找不到武器: {weapon_name}")

        item_hash = weapon["itemHash"]
        display_name = weapon["name"]

        if not self._wishlist or not self._wishlist.has_data(item_hash):
            return f"「{display_name}」暂无社区推荐 god roll 数据。"

        grp = self._wishlist.get_god_roll_perks(item_hash)
        if not grp:
            return f"「{display_name}」暂无社区推荐 god roll 数据。"

        def _perk_names(perk_hashes: set[int]) -> list[str]:
            names = []
            for h in perk_hashes:
                info = self._manifest.get_item_info(h)
                name = info.get("name", f"#{h}") if info else f"#{h}"
                names.append(name)
            return sorted(names)

        pve_names = _perk_names(grp.pve_perks)
        pvp_names = _perk_names(grp.pvp_perks)
        sources = ", ".join(grp.sources[:5]) if grp.sources else "未知"

        lines = [f"=== {display_name} God Roll 推荐 ==="]
        lines.append(f"来源: {sources}")
        lines.append("")

        if not pve_names and not pvp_names:
            # 愿单里"有这条记录"但解析不出任何 Perk：以前只回一个标题，
            # 看起来像"这把枪没有推荐"。要明说是本地条目解析不出内容。
            return (
                f"「{display_name}」在本地愿单里有记录（来源: {sources}），"
                "但这条记录解析不出 PvE/PvP 推荐 Perk。这不代表它没有推荐，"
                "只是本地这条数据不完整。"
            )

        if pve_names:
            lines.append(f"【PvE】推荐 perk ({len(pve_names)} 个):")
            for name in pve_names:
                lines.append(f"  • {name}")
            lines.append("")

        if pvp_names:
            lines.append(f"【PvP】推荐 perk ({len(pvp_names)} 个):")
            for name in pvp_names:
                lines.append(f"  • {name}")

        return "\n".join(lines)


def _item_icon_url(manifest: ManifestManager, item_hash: int) -> str:
    info = manifest.get_item_info(item_hash)
    if not isinstance(info, dict):
        return ""
    return str(info.get("icon") or "")
