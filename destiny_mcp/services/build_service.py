"""Build Service — orchestration layer for the Build Engine.

This is the ONLY public entry point to the Build Engine. Per design
principle #6, no external caller (server, CLI, REST) may call the
solver/scorer/analyzer directly.

Workflow:
    1. Fetch armor snapshot (via InventoryService)
    2. Parse constraints (ConstraintParser)
    3. Solve with mod assignment (DIM algorithm)
    4. Convert results to BuildResult
    5. Analyze failures if no results (Analyzer)
"""

from __future__ import annotations

import asyncio
from ..build import solver as _solver
from ..build.analyzer import analyze
from ..build.constants import MAIN_STAT_HASHES, STAT_NAMES, SUBCLASS_BONUSES
from ..build.constraints import parse as _parse_constraints
from ..build.models import (
    BuildAnalysis,
    BuildCandidate,
    BuildRecommendation,
    BuildRequest,
    BuildResult,
)
from ..build.scorer import score as _score
from ..bungie_client import BungieClient
from ..exceptions import ItemNotFoundError, TransferError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver

logger = get_logger(__name__)


class BuildService:
    """Orchestrates the full Build Engine pipeline.

    Depends on InventoryService for data fetching. The solver/scorer/
    analyzer are stateless pure functions — no instantiation needed.
    """

    def __init__(self, bungie: BungieClient, manifest: ManifestManager, resolver: PlayerResolver) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        from .inventory_service import InventoryService
        from .transfer_service import TransferService

        self._inventory = InventoryService(bungie, manifest, resolver)
        self._transfer = TransferService(bungie, manifest, resolver)

    async def _get_subclass_and_fragment_stats(
        self, player_name: str, character_class: str | None = None
    ) -> tuple[list[int], list[int]]:
        """Get subclass and fragment stat bonuses for the player's equipped subclass.

        Args:
            player_name: Bungie name.
            character_class: Target class (hunter/warlock/titan). If None, uses first found.

        Returns (subclass_stats, fragment_stats) as vectors in STAT_NAMES order.
        """
        from ..manifest import resolve_character_name, class_type_name

        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        # Fetch equipment + socket data for all characters
        profile = await self._resolver.get_profile(mid, mtype, [200, 205, 305])
        chars = profile.get("characters", {}).get("data", {})
        equip = profile.get("characterEquipment", {}).get("data", {})
        sockets_map = profile.get("itemComponents", {}).get("sockets", {}).get("data", {})

        # Determine target class type
        target_class_type: int | None = None
        if character_class:
            target_class_type = resolve_character_name(character_class)

        # Find the equipped subclass for the target character
        subclass_inst_id = None
        subclass_hash = 0
        for char_id, char_info in chars.items():
            class_type = char_info.get("classType", -1)
            if target_class_type is not None and class_type != target_class_type:
                continue
            for item in equip.get(char_id, {}).get("items", []):
                h = item.get("itemHash", 0)
                info = self._manifest.get_item_info(h) or {}
                if info.get("itemType") == 16:  # Subclass
                    subclass_inst_id = str(item.get("itemInstanceId", "0"))
                    subclass_hash = h
                    logger.info(
                        "Found subclass for %s: %s (hash=%s)",
                        class_type_name(class_type),
                        self._manifest.get_item_name(h),
                        h,
                    )
                    break
            if subclass_inst_id:
                break

        if not subclass_inst_id:
            return [0] * 6, [0] * 6

        # Read socket data for the subclass
        sockets_data = sockets_map.get(subclass_inst_id, {}).get("sockets", [])

        # Look up subclass base stat bonus from constants
        subclass_vector = SUBCLASS_BONUSES.get(subclass_hash, [0] * 6)

        # Sum fragment stat bonuses from investmentStats.
        # Don't filter by fragment category — different elements use different
        # category hashes (Solar=3119191718, Prismatic=2696330562, etc).
        # Instead, read ALL sockets that contribute to the 6 main stats.
        # This automatically includes fragments and excludes aspects (which
        # contribute to non-main stats like "unknown(2223994109)").
        fragment_vector = [0] * 6
        for socket in sockets_data:
            plug_hash = socket.get("plugHash", 0)
            if not plug_hash:
                continue
            plug_def = self._manifest.get_item_definition(plug_hash)
            if not plug_def:
                continue
            for stat_entry in plug_def.get("investmentStats", []):
                stat_hash = stat_entry.get("statTypeHash", 0)
                value = stat_entry.get("value", 0)
                idx = MAIN_STAT_HASHES.get(stat_hash)
                if idx is not None:
                    fragment_vector[idx] += value

        logger.info(
            "Subclass stats: %s, Fragment stats: %s",
            dict(zip(STAT_NAMES, subclass_vector)),
            dict(zip(STAT_NAMES, fragment_vector)),
        )
        return subclass_vector, fragment_vector

    # ── Fragment lookup by name ──────────────────────────────────────

    def _get_fragment_stats_by_names(
        self, fragment_names: list[str]
    ) -> tuple[list[int], list[dict]]:
        """Look up fragment stat bonuses by name from manifest.

        Args:
            fragment_names: List of fragment names (Chinese or English).

        Returns:
            Tuple of (stat_vector, details) where details is a list of
            dicts with 'name' and 'stats' for each fragment.
        """
        vector = [0] * 6
        details = []
        for name in fragment_names:
            results = self._manifest.search(name, limit=10)
            found = False
            for r in results:
                if r.get("itemType") != 19:  # Not a fragment/plug
                    continue
                h = r["itemHash"]
                info = self._manifest.get_item_definition(h)
                if not info:
                    continue
                # Check if this fragment has stat bonuses
                frag_stats = {}
                for stat_entry in info.get("investmentStats", []):
                    stat_hash = stat_entry.get("statTypeHash", 0)
                    value = stat_entry.get("value", 0)
                    idx = MAIN_STAT_HASHES.get(stat_hash)
                    if idx is not None and value != 0:
                        vector[idx] += value
                        frag_stats[STAT_NAMES[idx]] = value
                if frag_stats:
                    found = True
                    details.append({"name": r["name"], "stats": frag_stats})
                    logger.info("Fragment '%s' (hash=%d): %s", r["name"], h, frag_stats)
                    break  # Take first match with stats
            if not found:
                logger.warning("Fragment '%s': no stat bonuses found in manifest", name)
                details.append({"name": name, "stats": {}, "warning": "未找到"})
        return vector, details

    # ── Public API ────────────────────────────────────────────────────

    async def find_build(
        self,
        player_name: str,
        request: BuildRequest,
    ) -> list[BuildResult]:
        """Find the best armor builds for the given request.

        Uses the DIM algorithm: 5-level nested loop + mod assignment.
        Results include mod bonuses in stat calculations.

        Args:
            player_name: Bungie name.
            request: User's build request (targets + constraints).

        Returns:
            Top-K BuildResults sorted by score (best first). Empty list
            if no build satisfies the constraints.
        """
        logger.info(
            "find_build: player=%s exotic=%s targets=(wep=%s hp=%s cls=%s gre=%s mel=%s sup=%s)",
            player_name,
            request.exotic_name,
            request.weapons_target,
            request.health_target,
            request.class_target,
            request.grenade_target,
            request.melee_target,
            request.super_target,
        )

        # Step 1: Fetch armor data (filtered by character class)
        snapshot = await self._inventory.get_armor_snapshot(player_name, request.character_class)
        logger.info("Snapshot: %d pieces across 5 slots", snapshot.total_pieces)

        # Step 2: Parse constraints + subclass/fragment stats
        parsed = _parse_constraints(request, self._manifest)
        bonus_vector = [0] * 6
        fragment_details: list[dict] = []
        if request.fragment_names:
            # User specified fragments by name — look up their stats from manifest
            fragment_stats, fragment_details = self._get_fragment_stats_by_names(request.fragment_names)
            # Also get subclass base stats (not fragments)
            subclass_stats, _ = await self._get_subclass_and_fragment_stats(
                player_name, request.character_class
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
            bonus_vector = parsed.subclass_and_fragment_vector()
            logger.info(
                "Using specified fragments: %s → subclass=%s, fragment=%s",
                request.fragment_names,
                dict(zip(STAT_NAMES, subclass_stats)),
                dict(zip(STAT_NAMES, fragment_stats)),
            )
        elif request.include_subclass_fragment:
            subclass_stats, fragment_stats = await self._get_subclass_and_fragment_stats(
                player_name, request.character_class
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
            bonus_vector = parsed.subclass_and_fragment_vector()

        # Step 3: Solve (DIM algorithm with mod assignment)
        process_result = _solver.solve(snapshot, parsed)
        logger.info("Solver: %d combos processed, %d valid sets", process_result.combos, len(process_result.sets))

        if not process_result.sets:
            logger.warning("No build satisfies constraints for %s", player_name)
            return []

        # Step 4: Convert ProcessArmorSet to BuildResult
        results: list[BuildResult] = []
        for armor_set in process_result.sets[:parsed.top_n]:
            # Build the final stats (base + bonus from mods + subclass + fragments)
            bonus_dict: dict[str, int] = {}
            for i, stat_name in enumerate(STAT_NAMES):
                if i < len(armor_set.bonus_stats):
                    bonus_dict[stat_name] = armor_set.bonus_stats[i]

            # Create BuildCandidate with final stats (armor + mod + subclass + fragment).
            # bonus_vector contains subclass + fragment bonuses.
            # armor_set.armor contains Armor objects from the snapshot
            stats = armor_set.stats
            candidate = BuildCandidate(
                items=list(armor_set.armor),
                weapons=(stats[0] + bonus_dict.get("weapons", 0) + bonus_vector[0]) if len(stats) > 0 else 0,
                health=(stats[1] + bonus_dict.get("health", 0) + bonus_vector[1]) if len(stats) > 1 else 0,
                class_stat=(stats[2] + bonus_dict.get("class_stat", 0) + bonus_vector[2]) if len(stats) > 2 else 0,
                grenade=(stats[3] + bonus_dict.get("grenade", 0) + bonus_vector[3]) if len(stats) > 3 else 0,
                melee=(stats[4] + bonus_dict.get("melee", 0) + bonus_vector[4]) if len(stats) > 4 else 0,
                super_stat=(stats[5] + bonus_dict.get("super_stat", 0) + bonus_vector[5]) if len(stats) > 5 else 0,
                bonus_stats=bonus_dict,
                stat_mods=armor_set.stat_mods,
                # Store subclass/fragment bonuses separately for reference
                subclass_fragment_bonus=bonus_vector,
            )

            # Calculate active set bonuses
            active_set_bonuses = _calculate_set_bonuses(
                list(armor_set.armor), self._manifest
            )

            # Score for ranking
            score = _score(candidate, parsed)
            missing = _missing_requirements(candidate, parsed)
            met = _count_met(candidate, parsed)
            targets = _count_targets(parsed)

            results.append(
                BuildResult(
                    score=round(score, 2),
                    completion_rate=met / max(1, targets),
                    build=candidate,
                    missing_requirements=missing,
                    fragment_details=fragment_details,
                    active_set_bonuses=active_set_bonuses,
                )
            )

        # Sort by score (best first)
        results.sort(key=lambda r: r.score, reverse=True)

        return results

    async def analyze_build(
        self,
        player_name: str,
        request: BuildRequest,
    ) -> BuildAnalysis:
        """Analyze why a build request fails with current inventory.

        Use this after find_build returns empty results to understand
        what armor is missing.

        Args:
            player_name: Bungie name.
            request: The failed build request.

        Returns:
            BuildAnalysis with failure reason and farming suggestions.
        """
        logger.info("analyze_build: player=%s", player_name)
        snapshot = await self._inventory.get_armor_snapshot(player_name, request.character_class)
        parsed = _parse_constraints(request, self._manifest)
        if request.include_subclass_fragment:
            subclass_stats, fragment_stats = await self._get_subclass_and_fragment_stats(
                player_name, request.character_class
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
        return analyze(snapshot, parsed)

    async def recommend_build(
        self,
        player_name: str,
        request: BuildRequest,
    ) -> BuildRecommendation:
        """Find builds and include diagnostics when no candidate is available."""
        results = await self.find_build(player_name, request)
        if results:
            return BuildRecommendation(results=results)

        analysis = await self.analyze_build(player_name, request)
        return BuildRecommendation(results=[], analysis=analysis)

    async def equip_build(
        self,
        player_name: str,
        result: BuildResult,
        target_character: str,
    ) -> dict:
        """Equip a full build result on a character.

        Transfers all 5 armor pieces to the target character (if not
        already there) and equips them. Uses TransferService for the
        actual operations (Rule 3: no direct BungieClient calls).

        Args:
            player_name: Bungie name.
            result: A BuildResult from find_build.
            target_character: hunter / warlock / titan.

        Returns:
            Dict with per-piece results and overall success status.
        """
        logger.info(
            "equip_build: player=%s target=%s pieces=%d",
            player_name, target_character, len(result.build.items),
        )

        piece_results: list[dict] = []
        all_ok = True

        for i, armor in enumerate(result.build.items):
            # Rate limit: ~1-2 req/s for TransferItem
            if i > 0:
                await asyncio.sleep(0.5)

            logger.info(
                "Equipping piece %d/5: '%s' (%s)",
                i + 1, armor.name, armor.slot,
            )
            try:
                move_result = await self._transfer.move_item(
                    player_name=player_name,
                    item_name=armor.name,
                    destination=target_character,
                    equip=True,
                    item_instance_id=armor.item_instance_id,
                )
                piece_results.append({
                    "slot": armor.slot,
                    "name": armor.name,
                    "success": move_result.success,
                    "message": move_result.message,
                })
                if not move_result.success:
                    all_ok = False
            except (ItemNotFoundError, TransferError, OSError) as e:
                logger.error("Failed to equip '%s': %s", armor.name, e)
                piece_results.append({
                    "slot": armor.slot,
                    "name": armor.name,
                    "success": False,
                    "message": str(e),
                })
                all_ok = False

        return {
            "success": all_ok,
            "character": target_character,
            "pieces": piece_results,
            "message": (
                f"Equipped full build on {target_character}."
                if all_ok
                else f"Some pieces failed to equip on {target_character}."
            ),
        }

    async def equip_by_score(
        self,
        player_name: str,
        request: BuildRequest,
        target_character: str,
        score: float = 0,
    ) -> dict:
        """Find builds matching the request, then equip the one closest to score.

        Combines find_build + equip_build into a single operation.
        If score is 0, equips the top-ranked build.

        Args:
            player_name: Bungie name.
            request: Build request with stat targets.
            target_character: hunter / warlock / titan.
            score: Target score to match (from find_build results).

        Returns:
            Dict with equip result.
        """
        results = await self.find_build(player_name, request)

        if not results:
            return {
                "success": False,
                "message": "没有找到满足条件的配装方案，请先用 find_build 查看可用方案。",
            }

        if score > 0:
            target = min(results, key=lambda r: abs(r.score - score))
        else:
            target = results[0]

        return await self.equip_build(player_name, target, target_character)


def _count_targets(parsed) -> int:
    """Count how many stat targets (non-zero minimums) are set."""
    return sum(1 for v in parsed.as_vector() if v > 0)


def _count_met(candidate, parsed) -> int:
    """Count how many stat targets are met."""
    met = 0
    for stat_name in STAT_NAMES:
        target = getattr(parsed, f"{stat_name}_min")
        if target > 0 and candidate.stat(stat_name) >= target:
            met += 1
    return met


def _missing_requirements(candidate, parsed) -> list[str]:
    """List stat targets that were not met."""
    missing: list[str] = []
    for stat_name in STAT_NAMES:
        target = getattr(parsed, f"{stat_name}_min")
        actual = candidate.stat(stat_name)
        if target > 0 and actual < target:
            missing.append(f"{stat_name}: {actual}/{target}")
    return missing


def _calculate_set_bonuses(armor_list: list, manifest) -> list[dict]:
    """Calculate active set bonuses for a list of armor pieces.

    Groups armor by set_bonus_hash, counts pieces per set, and looks up
    which perks are active based on required_set_count.

    Returns:
        List of {set_name, piece_count, perks: [{name, description}]}
    """
    # Count pieces per set
    set_counts: dict[int, int] = {}
    set_names: dict[int, str] = {}
    for armor in armor_list:
        if armor.set_bonus_hash:
            set_counts[armor.set_bonus_hash] = set_counts.get(armor.set_bonus_hash, 0) + 1
            set_names[armor.set_bonus_hash] = armor.set_bonus_name

    # Look up active perks for each set
    active_bonuses = []
    for set_hash, count in set_counts.items():
        if count < 2:
            continue  # Need at least 2 pieces for any bonus

        set_info = manifest.get_set_bonus_by_hash(set_hash)
        if not set_info:
            continue

        active_perks = []
        for perk in set_info.get("perks", []):
            required = perk.get("required_set_count", 0)
            if count >= required:
                active_perks.append({
                    "name": perk.get("perk_name", ""),
                    "description": perk.get("perk_description", ""),
                    "required_count": required,
                })

        if active_perks:
            active_bonuses.append({
                "set_name": set_names.get(set_hash, set_info.get("set_name", "")),
                "piece_count": count,
                "perks": active_perks,
            })

    return active_bonuses
