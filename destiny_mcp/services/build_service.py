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

import hashlib
import json
import secrets
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from typing import Literal

from ..build import solver as _solver
from ..build.analyzer import analyze
from ..build.constants import MAIN_STAT_HASHES, STAT_NAMES, SUBCLASS_BONUSES
from ..build.constraints import parse as _parse_constraints
from ..build.farm_target import find_farm_targets
from ..build.models import (
    BuildAnalysis,
    BuildCandidate,
    BuildRecommendation,
    BuildRequest,
    BuildResult,
)
from ..build.scorer import score as _score
from ..bungie_client import BungieClient
from ..build_import.models import CanonicalBuild
from ..exceptions import BuildValidationError
from ..logging_config import get_logger
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import Loadout, LoadoutItem, LoadoutSubclassConfig
from ..player_resolver import PlayerResolver
from .account_action_lock import account_action_lock, serialized_account_action
from .loadout_equipment_service import LoadoutEquipmentService

logger = get_logger(__name__)


_LOADOUT_SLOT_NAMES = {
    "helmets": "helmet",
    "helmet": "helmet",
    "gauntlets": "gauntlets",
    "chests": "chest",
    "chest": "chest",
    "legs": "legs",
    "class_items": "class_item",
    "class_item": "class_item",
}
_EXPECTED_ARMOR_SLOTS = {"helmet", "gauntlets", "chest", "legs", "class_item"}
_MAX_BUILD_CANDIDATES = 200
_BUILD_CANDIDATE_TTL_SECONDS = 10 * 60


def _snapshot_version(snapshot) -> str:
    """Return a deterministic version for the armor state used by the solver."""
    rows = []
    for collection in (
        snapshot.helmets,
        snapshot.gauntlets,
        snapshot.chests,
        snapshot.legs,
        snapshot.class_items,
    ):
        for armor in collection:
            rows.append({
                "instance_id": armor.item_instance_id,
                "item_hash": armor.item_hash,
                "slot": armor.slot,
                "stats": armor.stats.model_dump(),
                "energy_capacity": armor.energy_capacity,
                "source_location": getattr(armor, "source_location", ""),
                "source_character_id": getattr(armor, "source_character_id", ""),
                "is_equipped": getattr(armor, "is_equipped", False),
            })
    mod_definitions = sorted(
        (
            definition.model_dump()
            for definition in getattr(snapshot, "stat_mod_definitions", [])
        ),
        key=lambda definition: definition["hash"],
    )
    payload = json.dumps(
        {
            "armor": sorted(rows, key=lambda row: row["instance_id"]),
            "stat_mod_definitions": mod_definitions,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_subclass(build: CanonicalBuild) -> LoadoutSubclassConfig | None:
    values = {
        "subclass_item_hash": build.subclass_item_hash or 0,
        "subclass_instance_id": build.subclass_instance_id,
        "super_hash": build.super_hash or 0,
        "grenade_hash": build.grenade_hash or 0,
        "melee_hash": build.melee_hash or 0,
        "class_ability_hash": build.class_ability_hash or 0,
        "movement_hash": build.movement_hash or 0,
        "aspect_hashes": build.aspect_hashes,
        "fragment_hashes": build.fragment_hashes,
        "plug_sockets": build.subclass_plug_sockets,
    }
    if not any([
        values["super_hash"],
        values["grenade_hash"],
        values["melee_hash"],
        values["class_ability_hash"],
        values["movement_hash"],
        values["aspect_hashes"],
        values["fragment_hashes"],
        values["plug_sockets"],
    ]):
        return None
    return LoadoutSubclassConfig(**values)


class BuildService:
    """Orchestrates the full Build Engine pipeline.

    Depends on InventoryService for data fetching. The solver/scorer/
    analyzer are stateless pure functions — no instantiation needed.
    """

    def __init__(self, bungie: BungieClient, manifest: ManifestManager, resolver: PlayerResolver) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._account_action_lock = account_action_lock(bungie)
        from .inventory_service import InventoryService

        self._inventory = InventoryService(bungie, manifest, resolver)
        self._equipment = LoadoutEquipmentService(bungie, manifest, resolver)
        self._build_candidates: OrderedDict[str, CanonicalBuild] = OrderedDict()
        self._build_candidate_issued_at: dict[str, float] = {}
        self._build_candidate_players: dict[str, str] = {}

    def _evict_expired_build_candidates(self, now: float) -> None:
        """Drop expired or incomplete one-time execution candidates."""
        for execution_id, issued_at in tuple(self._build_candidate_issued_at.items()):
            if now - issued_at >= _BUILD_CANDIDATE_TTL_SECONDS:
                self._build_candidate_issued_at.pop(execution_id, None)
                self._build_candidate_players.pop(execution_id, None)
                self._build_candidates.pop(execution_id, None)
        for execution_id in tuple(self._build_candidates):
            if execution_id not in self._build_candidate_issued_at:
                self._build_candidate_players.pop(execution_id, None)
                self._build_candidates.pop(execution_id, None)

    def _register_build_candidate(
        self,
        build: CanonicalBuild,
        player_name: str = "",
    ) -> None:
        """Store one exact build for a short, one-time execution window."""
        if not build.execution_id:
            return
        now = time.monotonic()
        self._evict_expired_build_candidates(now)
        self._build_candidates[build.execution_id] = build.model_copy(deep=True)
        self._build_candidates.move_to_end(build.execution_id)
        self._build_candidate_issued_at[build.execution_id] = now
        self._build_candidate_players[build.execution_id] = player_name.casefold()
        while len(self._build_candidates) > _MAX_BUILD_CANDIDATES:
            execution_id, _ = self._build_candidates.popitem(last=False)
            self._build_candidate_issued_at.pop(execution_id, None)
            self._build_candidate_players.pop(execution_id, None)

    async def _get_subclass_and_fragment_stats(
        self, player_name: str, character_class: str | None = None
    ) -> tuple[list[int], list[int], LoadoutSubclassConfig | None]:
        """Get subclass and fragment stat bonuses for the player's equipped subclass.

        Args:
            player_name: Bungie name.
            character_class: Target class (hunter/warlock/titan). If None, uses first found.

        Returns stat vectors plus the exact equipped subclass configuration.
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
            return [0] * 6, [0] * 6, None

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
        subclass_config = self._equipment.read_subclass_config(
            subclass_inst_id, subclass_hash, sockets_map
        )
        return subclass_vector, fragment_vector, subclass_config

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
                found = True
                details.append({"name": r["name"], "hash": h, "stats": frag_stats})
                logger.info("Fragment '%s' (hash=%d): %s", r["name"], h, frag_stats)
                break
            if not found:
                logger.warning("Fragment '%s': no stat bonuses found in manifest", name)
                details.append({"name": name, "stats": {}, "warning": "未找到"})
        return vector, details

    def _replace_fragment_config(
        self,
        current: LoadoutSubclassConfig | None,
        fragment_hashes: list[int],
    ) -> LoadoutSubclassConfig:
        """Replace a complete fragment set while preserving exact socket indices."""
        if current is None or not current.subclass_item_hash:
            raise BuildValidationError("无法读取当前子职业，不能安全应用碎片。")
        old_fragment_hashes = set(current.fragment_hashes)
        fragment_indices = sorted(
            index
            for index, plug_hash in current.plug_sockets.items()
            if plug_hash in old_fragment_hashes
        )
        if len(fragment_hashes) != len(fragment_indices):
            raise BuildValidationError(
                "必须提供完整碎片配置："
                f"当前子职业需要 {len(fragment_indices)} 个，收到 {len(fragment_hashes)} 个。"
            )

        subclass_definition = self._manifest.get_item_definition(
            current.subclass_item_hash
        ) or {}
        socket_entries = (subclass_definition.get("sockets") or {}).get(
            "socketEntries", []
        )
        updated_sockets = dict(current.plug_sockets)
        for socket_index, plug_hash in zip(fragment_indices, fragment_hashes):
            if socket_index >= len(socket_entries):
                raise BuildValidationError("子职业碎片插槽定义已变化，请刷新 Manifest。")
            entry = socket_entries[socket_index]
            accepted = entry.get("singleInitialItemHash", 0) == plug_hash
            for plug_set_hash in {
                entry.get("reusablePlugSetHash", 0),
                entry.get("randomizedPlugSetHash", 0),
            }:
                if not plug_set_hash:
                    continue
                plug_set = self._manifest.get_definition(
                    "DestinyPlugSetDefinition", plug_set_hash
                ) or {}
                if any(
                    item.get("plugItemHash", 0) == plug_hash
                    for item in plug_set.get("reusablePlugItems", [])
                ):
                    accepted = True
                    break
            if not accepted:
                raise BuildValidationError(
                    f"碎片 {plug_hash} 与当前子职业插槽 {socket_index} 不兼容。"
                )
            updated_sockets[socket_index] = plug_hash

        return current.model_copy(update={
            "fragment_hashes": fragment_hashes,
            "plug_sockets": updated_sockets,
        })

    def resolve_exotic_armor(
        self,
        exotic_name: str,
        character_class: str,
        limit: int = 5,
    ) -> dict:
        """Resolve an exotic armor name without auto-selecting fuzzy hits."""
        query = exotic_name.strip()
        if not query:
            return {"status": "not_found", "query": query, "matches": []}

        class_type = resolve_character_name(character_class)

        def normalize(value: str) -> str:
            return " ".join(value.casefold().split())

        query_key = normalize(query)

        def public_match(candidate: dict) -> dict:
            names = {
                normalize(str(candidate.get("name", ""))),
                normalize(str(candidate.get("nameEn", ""))),
            }
            if query_key in names:
                raw_score = 1.0
            else:
                raw_score = candidate.get("match_score")
                if raw_score is None:
                    raw_score = max(
                        (
                            SequenceMatcher(None, query_key, name).ratio()
                            for name in names
                            if name
                        ),
                        default=0.0,
                    )
            return {
                "name": candidate.get("name", ""),
                "nameEn": candidate.get("nameEn", ""),
                "item_hash": candidate.get("itemHash", 0),
                "icon_url": candidate.get("icon", ""),
                "class_type": candidate.get("classType", -1),
                "score": round(float(raw_score), 3),
            }

        def unique_candidates(candidates: list[dict]) -> list[dict]:
            filtered = [
                candidate
                for candidate in candidates
                if candidate.get("itemType") == 2
                and candidate.get("tier") == 6
                and candidate.get("classType", -1) in {-1, class_type}
            ]
            unique: list[dict] = []
            seen_names: set[tuple[str, str, int]] = set()
            for candidate in filtered:
                identity = (
                    normalize(str(candidate.get("name", ""))),
                    normalize(str(candidate.get("nameEn", ""))),
                    int(candidate.get("classType", -1)),
                )
                if identity in seen_names:
                    continue
                seen_names.add(identity)
                unique.append(candidate)
                if len(unique) >= max(1, limit):
                    break
            return unique

        candidates = unique_candidates(
            self._manifest.search(query, limit=max(20, limit * 10))
        )
        exact = next(
            (
                candidate
                for candidate in candidates
                if query_key
                in {
                    normalize(str(candidate.get("name", ""))),
                    normalize(str(candidate.get("nameEn", ""))),
                }
            ),
            None,
        )
        if exact is not None:
            return {
                "status": "exact",
                "query": query,
                "canonical_name": exact.get("name", query),
                "matches": [public_match(exact)],
            }
        if candidates:
            return {
                "status": "confirmation_required",
                "query": query,
                "matches": [public_match(candidate) for candidate in candidates],
            }

        candidates = unique_candidates(
            self._manifest.search_fuzzy(
                query,
                limit=max(20, limit * 10),
                item_type=2,
                tier=6,
                class_type=class_type,
            )
        )
        if candidates:
            return {
                "status": "confirmation_required",
                "query": query,
                "matches": [public_match(candidate) for candidate in candidates],
            }
        return {"status": "not_found", "query": query, "matches": []}

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
        if not request.character_class:
            raise BuildValidationError("必须指定 hunter、warlock 或 titan。")

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
        snapshot_version = _snapshot_version(snapshot)
        logger.info("Snapshot: %d pieces across 5 slots", snapshot.total_pieces)

        # Step 2: Parse constraints + subclass/fragment stats
        parsed = _parse_constraints(request, self._manifest)
        bonus_vector = [0] * 6
        fragment_details: list[dict] = []
        execution_subclass: LoadoutSubclassConfig | None = None
        if request.fragment_names:
            # User specified fragments by name — look up their stats from manifest
            fragment_stats, fragment_details = self._get_fragment_stats_by_names(request.fragment_names)
            missing_fragments = [
                detail["name"]
                for detail in fragment_details
                if not detail.get("hash")
            ]
            if missing_fragments:
                raise BuildValidationError(
                    f"无法解析碎片：{', '.join(missing_fragments)}"
                )
            # Also get subclass base stats (not fragments)
            subclass_stats, _, execution_subclass = (
                await self._get_subclass_and_fragment_stats(
                    player_name, request.character_class
                )
            )
            requested_fragment_hashes = [
                detail["hash"]
                for detail in fragment_details
                if detail.get("hash")
            ]
            execution_subclass = self._replace_fragment_config(
                execution_subclass,
                requested_fragment_hashes,
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
            subclass_stats, fragment_stats, execution_subclass = (
                await self._get_subclass_and_fragment_stats(
                    player_name, request.character_class
                )
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
                    canonical_build=CanonicalBuild(
                        class_type=request.character_class,
                        exotic_hash=next(
                            (
                                item.item_hash
                                for item in armor_set.armor
                                if item.is_exotic
                            ),
                            None,
                        ),
                        subclass_item_hash=(
                            execution_subclass.subclass_item_hash
                            if execution_subclass
                            else None
                        ),
                        subclass_instance_id=(
                            execution_subclass.subclass_instance_id
                            if execution_subclass
                            else ""
                        ),
                        subclass_plug_sockets=(
                            execution_subclass.plug_sockets
                            if execution_subclass
                            else {}
                        ),
                        super_hash=(
                            execution_subclass.super_hash
                            if execution_subclass
                            else None
                        ),
                        grenade_hash=(
                            execution_subclass.grenade_hash
                            if execution_subclass
                            else None
                        ),
                        melee_hash=(
                            execution_subclass.melee_hash
                            if execution_subclass
                            else None
                        ),
                        class_ability_hash=(
                            execution_subclass.class_ability_hash
                            if execution_subclass
                            else None
                        ),
                        movement_hash=(
                            execution_subclass.movement_hash
                            if execution_subclass
                            else None
                        ),
                        aspect_hashes=(
                            execution_subclass.aspect_hashes
                            if execution_subclass
                            else []
                        ),
                        fragment_hashes=(
                            execution_subclass.fragment_hashes
                            if execution_subclass
                            else []
                        ),
                        target_stats={
                            name: value
                            for name, value in {
                                "weapons": request.weapons_target,
                                "health": request.health_target,
                                "class_stat": request.class_target,
                                "grenade": request.grenade_target,
                                "melee": request.melee_target,
                                "super_stat": request.super_target,
                            }.items()
                            if value is not None
                        },
                        items=[
                            LoadoutItem(
                                item_hash=armor.item_hash,
                                name=armor.name,
                                slot=_LOADOUT_SLOT_NAMES.get(armor.slot, armor.slot),
                                item_instance_id=armor.item_instance_id,
                                mods=list(
                                    armor_set.stat_mod_assignments.get(
                                        armor.item_instance_id, []
                                    )
                                ),
                                source_location=getattr(
                                    armor, "source_location", ""
                                ),
                                source_character_id=getattr(
                                    armor, "source_character_id", ""
                                ),
                                was_equipped=getattr(
                                    armor, "is_equipped", False
                                ),
                            )
                            for armor in armor_set.armor
                        ],
                        snapshot_version=snapshot_version,
                        execution_id=secrets.token_urlsafe(18),
                    ),
                )
            )

        priority_indices = parsed.ordered_priority_indices
        if priority_indices:
            results.sort(
                key=lambda result: (
                    result.completion_rate,
                    *(
                        result.build.stat(STAT_NAMES[index])
                        for index in priority_indices
                    ),
                    result.score,
                ),
                reverse=True,
            )
        else:
            results.sort(key=lambda result: result.score, reverse=True)

        for result in results:
            if result.canonical_build:
                self._register_build_candidate(result.canonical_build, player_name)

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
            subclass_stats, fragment_stats, _ = (
                await self._get_subclass_and_fragment_stats(
                    player_name, request.character_class
                )
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
        return analyze(snapshot, parsed)

    async def infer_required_armor(
        self,
        player_name: str,
        request: BuildRequest,
        *,
        replacement_slot: str | None = None,
        baseline: Literal["equipped", "inventory"] = "equipped",
        max_replacements: Literal[1, 2] = 1,
    ) -> BuildAnalysis:
        """Return minimum one- or two-piece farm targets without an equip plan."""
        if not request.character_class:
            raise BuildValidationError("必须指定 hunter、warlock 或 titan。")

        logger.info(
            "infer_required_armor: player=%s baseline=%s replacement_slot=%s max_replacements=%d",
            player_name,
            baseline,
            replacement_slot or "any",
            max_replacements,
        )
        snapshot = await self._inventory.get_armor_snapshot(
            player_name,
            request.character_class,
        )
        parsed = _parse_constraints(request, self._manifest)
        if request.fragment_names:
            fragment_stats, fragment_details = self._get_fragment_stats_by_names(
                request.fragment_names
            )
            missing_fragments = [
                detail["name"]
                for detail in fragment_details
                if not detail.get("hash")
            ]
            if missing_fragments:
                raise BuildValidationError(
                    f"无法解析碎片：{', '.join(missing_fragments)}"
                )
            subclass_stats, _, _ = await self._get_subclass_and_fragment_stats(
                player_name, request.character_class
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
        elif request.include_subclass_fragment:
            subclass_stats, fragment_stats, _ = (
                await self._get_subclass_and_fragment_stats(
                    player_name, request.character_class
                )
            )
            parsed.subclass_stats = subclass_stats
            parsed.fragment_stats = fragment_stats
        return find_farm_targets(
            snapshot,
            parsed,
            baseline=baseline,
            replacement_slot=replacement_slot,
            max_replacements=max_replacements,
            top_n=request.top_n,
        )

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

    @serialized_account_action
    async def equip_build(
        self,
        player_name: str,
        build: CanonicalBuild,
        target_character: str,
    ) -> dict:
        """Preflight and apply the exact CanonicalBuild confirmed by the user."""
        normalized_character = class_type_name(
            resolve_character_name(target_character)
        ).lower()
        logger.info(
            "equip_build: player=%s target=%s pieces=%d snapshot=%s",
            player_name,
            normalized_character,
            len(build.items),
            build.snapshot_version[:12],
        )
        if not build.execution_id:
            return {
                "success": False,
                "code": "missing_execution_id",
                "message": "配装缺少服务端候选 ID，请重新运行 find_build 后再确认。",
            }
        now = time.monotonic()
        trusted = self._build_candidates.get(build.execution_id)
        issued_at = self._build_candidate_issued_at.get(build.execution_id)
        candidate_player = self._build_candidate_players.get(build.execution_id)
        if (
            trusted is None
            or issued_at is None
            or candidate_player is None
            or (candidate_player and candidate_player != player_name.casefold())
        ):
            return {
                "success": False,
                "code": "unknown_execution_id",
                "message": "该配装候选已失效或不属于当前玩家，请重新求解并确认。",
            }
        if now - issued_at >= _BUILD_CANDIDATE_TTL_SECONDS:
            self._build_candidates.pop(build.execution_id, None)
            self._build_candidate_issued_at.pop(build.execution_id, None)
            self._build_candidate_players.pop(build.execution_id, None)
            return {
                "success": False,
                "code": "expired_execution_id",
                "message": "该配装候选已过期，请重新求解并确认。",
            }
        self._evict_expired_build_candidates(now)
        if trusted.model_dump(mode="json") != build.model_dump(mode="json"):
            return {
                "success": False,
                "code": "canonical_build_mismatch",
                "message": "确认后的配装内容发生变化，已拒绝执行。请重新选择候选。",
            }
        self._build_candidates.pop(build.execution_id, None)
        self._build_candidate_issued_at.pop(build.execution_id, None)
        self._build_candidate_players.pop(build.execution_id, None)
        build = trusted
        if build.class_type:
            build_character = class_type_name(
                resolve_character_name(build.class_type)
            ).lower()
            if build_character != normalized_character:
                return {
                    "success": False,
                    "code": "character_mismatch",
                    "message": "确认的配装职业与目标角色不一致，请重新生成配装。",
                }
        if not build.snapshot_version:
            return {
                "success": False,
                "code": "missing_snapshot_version",
                "message": "配装缺少库存快照版本，请重新运行 find_build 后再确认。",
            }
        if len(build.items) != 5:
            return {
                "success": False,
                "code": "invalid_item_count",
                "message": "精确配装必须包含五件护甲。",
            }

        instance_ids = [item.item_instance_id for item in build.items]
        slots = {_LOADOUT_SLOT_NAMES.get(item.slot, item.slot) for item in build.items}
        if (
            any(not instance_id for instance_id in instance_ids)
            or len(set(instance_ids)) != 5
            or slots != _EXPECTED_ARMOR_SLOTS
        ):
            return {
                "success": False,
                "code": "invalid_exact_items",
                "message": "配装实例或护甲槽位不完整，请重新生成配装。",
            }
        if any(mod_hash <= 0 for item in build.items for mod_hash in item.mods):
            return {
                "success": False,
                "code": "invalid_mod_hash",
                "message": "配装包含无效模组 Hash，请重新生成配装。",
            }

        snapshot = await self._inventory.get_armor_snapshot(
            player_name, normalized_character
        )
        current_version = _snapshot_version(snapshot)
        if current_version != build.snapshot_version:
            return {
                "success": False,
                "code": "stale_inventory_snapshot",
                "message": "生成配装后库存或护甲状态已变化；为避免装备另一套，请重新求解并确认。",
                "expected_snapshot_version": build.snapshot_version,
                "current_snapshot_version": current_version,
            }

        current_items = {
            armor.item_instance_id: armor
            for collection in (
                snapshot.helmets,
                snapshot.gauntlets,
                snapshot.chests,
                snapshot.legs,
                snapshot.class_items,
            )
            for armor in collection
        }
        for item in build.items:
            current = current_items.get(item.item_instance_id)
            if current is None or current.item_hash != item.item_hash:
                return {
                    "success": False,
                    "code": "exact_item_missing",
                    "message": f"确认的装备实例 '{item.item_instance_id}' 已不存在或发生变化。",
                }

        loadout = Loadout(
            id=f"build:{build.snapshot_version}",
            name="已确认的精确配装",
            character=normalized_character,
            items=build.items,
            subclass=_canonical_subclass(build),
            source="build",
        )
        result = await self._equipment.equip_exact(player_name, loadout)
        return {
            "success": result.success,
            "character": normalized_character,
            "snapshot_version": build.snapshot_version,
            "message": result.message,
            "steps": [step.model_dump() for step in result.steps],
        }

    async def equip_by_score(
        self,
        player_name: str,
        request: BuildRequest,
        target_character: str,
        score: float = 0,
    ) -> dict:
        """Reject the unsafe legacy score-based execution path."""
        logger.warning(
            "Rejected score-based build execution for player=%s target=%s score=%s",
            player_name,
            target_character,
            score,
        )
        return {
            "success": False,
            "code": "exact_build_required",
            "message": "不能再按浮点 score 重新求解并装备；请传回 find_build 返回的 canonical_build。",
        }


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
