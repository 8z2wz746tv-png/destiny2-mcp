"""Weapon analysis service — aggregate static weapon data and owned rolls."""

from __future__ import annotations

from ..exceptions import ConfigError, DestinyMCPError, ItemNotFoundError
from ..logging_config import get_logger
from ..models import WeaponComparison, WeaponPerkPool
from .manifest_query_service import ManifestQueryService
from .perk_service import PerkService
from .weapon_compare_service import WeaponCompareService

logger = get_logger(__name__)


class WeaponAnalysisService:
    """Orchestrate weapon lookups for one user-facing weapon question."""

    def __init__(
        self,
        perk_service: PerkService,
        weapon_compare_service: WeaponCompareService,
        manifest_query_service: ManifestQueryService,
    ) -> None:
        self._perk_svc = perk_service
        self._compare_svc = weapon_compare_service
        self._manifest_query_svc = manifest_query_service

    async def analyze_weapon(
        self,
        weapon_name: str,
        *,
        player_name: str | None = None,
        include_inventory: bool = True,
    ) -> dict:
        """Build a compact weapon analysis payload.

        Static manifest data is required. Inventory comparison is optional:
        if a player does not own the weapon, the tool still returns static
        info, perk pool, and god-roll data with a warning.
        """
        weapon_query = weapon_name.strip()
        if not weapon_query:
            raise ConfigError("请提供 weapon_name。")

        logger.info(
            "Analyzing weapon '%s' (include_inventory=%s, player=%s)",
            weapon_query,
            include_inventory,
            player_name or "<none>",
        )

        weapon_info = self._manifest_query_svc.get_weapon_full_info(weapon_query)
        resolved_weapon_name = weapon_info.get("name") or weapon_query

        warnings: list[str] = []
        next_actions: list[dict[str, str]] = []

        perk_pool = await self._load_perk_pool(weapon_query, warnings)
        god_roll = await self._load_god_roll(weapon_query, warnings)
        inventory = await self._load_inventory_comparison(
            resolved_weapon_name,
            player_name,
            include_inventory,
            warnings,
            next_actions,
        )

        slot_count = len(perk_pool.slots) if perk_pool else 0
        instance_count = len(inventory.instances) if inventory else 0
        summary_parts = [
            f"已分析「{resolved_weapon_name}」",
            f"{slot_count} 个 perk 栏位",
        ]
        if include_inventory:
            summary_parts.append(f"{instance_count} 个账号副本")

        return {
            "summary": "，".join(summary_parts) + "。",
            "weapon": weapon_info,
            "perk_pool": _dump_model(perk_pool),
            "god_roll": god_roll,
            "inventory": _dump_model(inventory),
            "warnings": warnings,
            "next_actions": next_actions,
        }

    async def _load_perk_pool(
        self,
        weapon_name: str,
        warnings: list[str],
    ) -> WeaponPerkPool | None:
        try:
            return await self._perk_svc.get_weapon_perks(weapon_name)
        except DestinyMCPError as exc:
            logger.warning("Weapon perk pool lookup failed for '%s': %s", weapon_name, exc)
            warnings.append(f"perk 池查询失败：{exc}")
            return None

    async def _load_god_roll(
        self,
        weapon_name: str,
        warnings: list[str],
    ) -> str:
        try:
            return await self._perk_svc.get_god_roll(weapon_name)
        except DestinyMCPError as exc:
            logger.warning("God roll lookup failed for '%s': %s", weapon_name, exc)
            warnings.append(f"god roll 查询失败：{exc}")
            return ""

    async def _load_inventory_comparison(
        self,
        weapon_name: str,
        player_name: str | None,
        include_inventory: bool,
        warnings: list[str],
        next_actions: list[dict[str, str]],
    ) -> WeaponComparison | None:
        if not include_inventory:
            return None

        if not player_name:
            warnings.append("未提供 Bungie 玩家名，已跳过账号内副本对比。")
            next_actions.append({
                "label": "提供 player_name 后重新分析",
                "tool": "weapon_assistant",
                "arguments": {
                    "intent": "analyze",
                    "weapon_name": weapon_name,
                    "include_inventory": True,
                },
            })
            return None

        try:
            return await self._compare_svc.compare_weapon_instances(player_name, weapon_name)
        except ItemNotFoundError as exc:
            logger.info(
                "Weapon '%s' not found in player '%s' inventory: %s",
                weapon_name,
                player_name,
                exc,
            )
            warnings.append("账号内未找到这把武器，已保留静态信息和 perk 池结果。")
            next_actions.append({
                "label": "缩短武器名或检查是否在其他账号",
                "tool": "inventory_assistant",
                "arguments": {"intent": "search", "item_name": weapon_name},
            })
            return None
        except DestinyMCPError as exc:
            logger.warning(
                "Inventory comparison failed for '%s' / '%s': %s",
                player_name,
                weapon_name,
                exc,
            )
            warnings.append(f"账号副本对比失败：{exc}")
            next_actions.append({
                "label": "稍后重试账号副本对比",
                "tool": "weapon_assistant",
                "arguments": {"intent": "compare", "weapon_name": weapon_name},
            })
            return None


def _dump_model(model) -> dict | None:
    if model is None:
        return None
    return model.model_dump(mode="json")
