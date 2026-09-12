"""Perk service — weapon perk pool lookups from manifest.

Pure manifest-based queries, no Bungie API calls.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ManifestError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..manifest_names import names_for
from . import weapon_payload, weapon_profile
from ..models import PerkInfo
from .wishlist_service import WishListService
from .weapon_popularity_service import WeaponPopularityService

logger = get_logger(__name__)

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

    def god_roll_lookup(self, item_hash: int):
        """给 `weapon_payload` 用的愿单查询：定义 → (pve, pvp)。没有愿单就返回 None。"""
        if not self._wishlist:
            return None

        def lookup(plug_hash: int) -> tuple[bool, bool]:
            verdict = self._wishlist.is_god_roll_perk(item_hash, plug_hash)
            return bool(verdict.get("pve")), bool(verdict.get("pvp"))

        return lookup

    async def get_weapon_perks(self, weapon_name: str) -> dict:
        """武器 perk 池：`{weapon, sockets}`（与 info/analyze 同一形状）。

        插槽不再只挑 WEAPON PERKS 两类，也不再把 `slot_name` 写成英文：
        所有插槽都在 `sockets` 里（含大师杰作/模组/纪念物），`kind` 是稳定枚举。

        Raises:
            ManifestError: If no weapon matches the name.
        """
        logger.info("Looking up perk pool for: %s", weapon_name)
        item_hash, definition = weapon_profile.find_weapon(self._manifest, weapon_name)
        names = names_for(self._manifest)
        sockets = weapon_payload.socket_list(
            self._manifest,
            definition,
            names=names,
            god_roll_lookup=self.god_roll_lookup(item_hash),
        )
        logger.info(
            "Perk pool for '%s': %d socket(s), %d option(s)",
            definition.get("displayProperties", {}).get("name", weapon_name),
            len(sockets),
            sum(socket.get("option_count", 0) for socket in sockets),
        )
        return {
            "weapon": weapon_payload.weapon_block(
                self._manifest, definition, sockets=sockets, names=names
            ),
            "sockets": sockets,
        }

    async def get_god_roll(self, weapon_name: str) -> dict:
        """社区推荐 roll，返回**结构化**结果（以前是一段文字）。

        `kind` 三态，三种情况必须分得清：
        - `fixed`：固定 roll 武器（多数异域、蓝绿白），没有"推荐 roll"这回事，
          列出它真正固定的内容（固有特性 + 只有一个选项的特性栏）；
        - `recommended`：随机 roll 武器且本地愿单有可解析条目；
        - `none`：随机 roll 武器但本地愿单没收录／条目解析不出内容（`note` 说明是哪一种）。

        Args:
            weapon_name: Weapon name to look up.

        Raises:
            ManifestError: 武器在 Manifest 里不存在（与 analyze/info/catalyst 一致）。
        """
        results = self._manifest.search(weapon_name, limit=10)
        weapon = None
        for r in results:
            if r.get("itemType") == 3:
                weapon = r
                break

        if not weapon:
            # 「武器不存在」和「武器存在但本地愿单没收录」是两件事：
            # 前者走错误信封（与 analyze/info/catalyst 一致），后者才是成功的说明。
            raise ManifestError(f"找不到武器: {weapon_name}")

        item_hash = weapon["itemHash"]
        display_name = weapon["name"]
        definition = self._manifest.get_item_definition(item_hash) or {}
        names = names_for(self._manifest)

        base: dict[str, Any] = {
            "weapon_name": display_name,
            "kind": "none",
            "source": "dim_wishlist",
            "pve": [],
            "pvp": [],
        }

        def _perks(perk_hashes: set[int]) -> list[dict]:
            entries = []
            for perk_hash in sorted(perk_hashes):
                info = self._manifest.get_item_info(perk_hash) or {}
                entries.append({
                    "plug_hash": int(perk_hash),
                    "name": str(info.get("name") or f"#{perk_hash}"),
                })
            return entries

        if weapon_profile.roll_kind(definition) == "fixed":
            sockets = weapon_profile.socket_options(
                self._manifest, definition, names=names
            )
            return base | {
                "kind": "fixed",
                "source": "manifest",
                "fixed_perks": [
                    {"plug_hash": perk["plug_hash"], "name": perk["name"]}
                    for perk in weapon_profile.fixed_roll_perks(sockets)
                ],
                "note": (
                    "固定 roll 武器：没有可推荐的随机 roll，"
                    "上面列的是它固定的固有特性与特性栏内容。"
                ),
            }

        if not self._wishlist or not self._wishlist.has_data(item_hash):
            return base | {
                "note": "本地愿单没有收录这把武器；没收录不等于不值得留。",
            }

        grp = self._wishlist.get_god_roll_perks(item_hash)
        if not grp:
            return base | {"note": "本地愿单里这条记录解析不出内容。"}

        pve = _perks(grp.pve_perks)
        pvp = _perks(grp.pvp_perks)
        sources = ", ".join(grp.sources[:5]) if grp.sources else ""
        if not pve and not pvp:
            return base | {
                "source_detail": sources,
                "note": (
                    "本地愿单里有这条记录，但解析不出 PvE/PvP 推荐 Perk；"
                    "不代表这把枪没有推荐，只是本地这条数据不完整。"
                ),
            }
        return base | {
            "kind": "recommended",
            "source_detail": sources,
            "pve": pve,
            "pvp": pvp,
        }


def _item_icon_url(manifest: ManifestManager, item_hash: int) -> str:
    info = manifest.get_item_info(item_hash)
    if not isinstance(info, dict):
        return ""
    return str(info.get("icon") or "")
