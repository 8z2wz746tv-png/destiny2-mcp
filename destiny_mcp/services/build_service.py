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
from difflib import SequenceMatcher
from typing import Any, Literal, cast

from ..build.analyzer import (
    analyze_from_probes,
    ensure_within_combination_limit,
    execution_blockers,
    probe_stat,
)
from ..build.constants import MAIN_STAT_HASHES, STAT_NAMES, SUBCLASS_BONUSES
from ..build.functional_mods import resolve_functional_mods
from ..build.constraints import parse as _parse_constraints
from ..build.farm_target import find_farm_targets
from ..build.models import (
    BuildAnalysis,
    BuildRecommendation,
    BuildRequest,
    BuildResult,
    InventorySnapshot,
)
from ..bungie_client import BungieClient
from ..build_contracts import CanonicalBuild, ExecutableBuild
from . import profile_components
from .build_analysis_guards import early_analysis
from .build_preparation import annotate_analysis, annotate_preparation, preparation_index
from ..error_codes import ErrorCode
from ..exceptions import BuildValidationError
from ..logging_config import get_logger
from ..utils.arg_text import split_items
from ..manifest import ManifestManager, class_type_name, resolve_character_name
from ..models import Loadout, LoadoutSubclassConfig
from ..player_resolver import PlayerResolver
from .account_action_lock import account_action_lock, serialized_account_action
from ..build.process_types import SearchDiagnostics
from ..build.ranking import rank_results
from ..build.snapshot_version import snapshot_version
from .build_tuning import apply_local_tuning, solve_with_tuning
from .build_candidates import BuildCandidateStore
from .candidate_messages import candidate_failure, describe_candidate
from .build_baseline import rebaseline_note
from .make_room import equip_with_make_room_retry, make_room_for_build, make_room_steps, prepare_build_write
from .build_execution_guard import recheck_confirmed_build
from .build_fragments import replace_fragment_config
from .build_results import (
    canonical_subclass,
    LOADOUT_SLOT_NAMES as _LOADOUT_SLOT_NAMES,
    ResultContext,
    build_results as _build_results,
)
from .loadout_equipment_service import LoadoutEquipmentService
from .build_compute import BuildCompute

# 单项上限探测的并发档：纯 CPU、只读，4 个够用且不会把机器压满
_PROBE_CONCURRENCY = 4

logger = get_logger(__name__)


_EXPECTED_ARMOR_SLOTS = {"helmet", "gauntlets", "chest", "legs", "class_item"}


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
        self._compute = BuildCompute()
        # 只读诊断探测走这条：允许 4 个并发（见 _probe_single_stat_ceilings）
        self._probe_compute = BuildCompute(capacity=_PROBE_CONCURRENCY)
        # 候选暂存（execution_id → 签发的那份方案）：过期规则见 services/build_candidates.py。
        self._candidates = BuildCandidateStore()

    async def _get_subclass_and_fragment_stats(
        self, player_name: str, character_class: str | None = None
    ) -> tuple[list[int], list[int], LoadoutSubclassConfig | None]:
        """Get subclass and fragment stat bonuses for the player's equipped subclass.

        Args:
            player_name: Bungie name.
            character_class: Target class (hunter/warlock/titan). If None, uses first found.

        Returns stat vectors plus the exact equipped subclass configuration.
        """
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        profile = await self._resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
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

        sockets_data = sockets_map.get(subclass_inst_id, {}).get("sockets", [])

        # Look up subclass base stat bonus from constants
        subclass_vector = SUBCLASS_BONUSES.get(subclass_hash, [0] * 6)

        # 碎片属性加成**不按碎片类别筛**（不同元素类别 hash 不同），而是读所有对六维有贡献的
        # 插槽 —— 这样自动含碎片、排除星象（星象不走 investmentStats）。别改回按类别筛。
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
                "name_en": candidate.get("nameEn", ""),
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
        diagnostics: list[SearchDiagnostics] | None = None,
        *,
        compute: BuildCompute | None = None,
        register: bool = True,
        functional_mods: list[str] | str | None = None,
        search_args: dict[str, Any] | None = None,
    ) -> list[BuildResult]:
        """`compute` / `register` 只给**只读探测**用（阶梯的逐档试解）：

        - `compute=self._probe_compute` → 走并发档；`register=False` → 不把探测出的方案
          塞进候选暂存（一次求解 200 套，会把调用方真正要装备的那份挤出去）。
        """
        """Find the best armor builds for the given request.

        Uses the DIM algorithm: 5-level nested loop + mod assignment.

        Args:
            player_name: Bungie name.
            request: User's build request (targets + constraints).
            diagnostics: 可选收集器。给了就把"这次搜索到底搜完了没有 + 每项属性单独
                能顶到多少"塞进去（`build/process_types.SearchDiagnostics`）—— 空结果必须能
                自证"枚举完了"，否则调用方分不清"真没有"与"没搜完"。

        Returns:
            Top-K BuildResults sorted by score (best first)；空表 = 枚举完了但没有满足下限的方案。
        """
        if not request.character_class:
            raise BuildValidationError("必须指定 hunter、warlock 或 titan。")
        canonical_class = cast(
            Literal["hunter", "warlock", "titan"],
            class_type_name(resolve_character_name(request.character_class)).lower(),
        )

        logger.info(
            "find_build: player=%s exotic=%s targets=%s",
            player_name, request.exotic_name, request.target_vector(),
        )

        # 照抄来的功能模组（流派取向，不进求解器）只影响一件事：这件护甲还剩多少能量能给属性模组。
        plan = resolve_functional_mods(
            self._manifest, [] if functional_mods is None else split_items(functional_mods))
        # Step 1: Fetch armor data (filtered by character class)
        snapshot = await self._inventory.get_armor_snapshot(
            player_name, request.character_class, reserved_mod_energy=plan.energy_by_slot() or None)
        version = snapshot_version(snapshot)
        logger.info("Snapshot: %d pieces across 5 slots", snapshot.total_pieces)

        parsed = _parse_constraints(request, self._manifest)
        ensure_within_combination_limit(snapshot, parsed)  # 规模超限就别跑，先给收窄建议
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
            execution_subclass = replace_fragment_config(
                self._manifest,
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

        # Step 3: Solve（含调谐补齐，见 services/build_tuning.py）——达标就原样返回；
        # 没达标才用调谐额度复解一遍（把"差 5 点"变成可执行方案）并逐套精确复核。
        solved = await solve_with_tuning(compute or self._compute, snapshot, parsed, self._manifest)
        pool, tuning_map = solved.pool, solved.tuning_map
        # P4 便宜路径：达标之后把免费的调谐额度吃干净（0 能量、+5/−5）。局部搜索、每步过权威复核；
        # 只对进池的候选做，所以**不是全局最优的证明**（边界写在 build/tuning.local_tuning_improvement）。
        pool, tuning_map = apply_local_tuning(
            pool, tuning_map, snapshot, parsed, self._manifest, top_n=parsed.top_n
        )
        # 执行前提砍掉了哪些件（格满 / 金装冲突）：0 候选时要能分清"配不出来"与"这套装不上"。
        blocked_by = execution_blockers(snapshot, parsed, self._manifest)
        if diagnostics is not None:
            diagnostics.append(SearchDiagnostics(
                coverage=solved.coverage,
                reachable_ceilings=solved.reachable_ceilings,
                blocked_by=blocked_by,
            ))
        logger.info("Solver: %d sets (%d 靠调谐补齐)", len(pool), len(tuning_map))
        if not pool:
            logger.warning("No build satisfies constraints for %s", player_name)
            return []

        # Step 4: Convert ProcessArmorSet to BuildResult（翻译层在 services/build_results.py）
        # 调谐补齐在 Step 3.5 已经写进 tuning_map：这套要改哪几件的调谐才达标。
        results = _build_results(
            pool,
            ResultContext(
                parsed=parsed,
                request=request,
                manifest=self._manifest,
                class_type=canonical_class,
                snapshot_version=version,
                bonus_vector=bonus_vector,
                fragment_details=fragment_details,
                execution_subclass=execution_subclass,
                tuning=tuning_map,
                functional_mods=plan,
            ),
        )

        # 展示排序与**求解器内部**共用同一个比较器（`build/ranking.rank_results`）：以前这里是第三套
        # 口径（`completion_rate` 打头 → 优先级 → 加权总分），三套必然互相打架。
        results = rank_results(results, parsed)

        # 每套方案"要先准备什么"（ADR-027）：件现在也在池里，所以这句话要跟着方案走。
        annotate_preparation(results, preparation_index(snapshot))

        if register:
            for result in results:
                if result.canonical_build:
                    self._candidates.register(
                        result.canonical_build, player_name, search_args
                    )

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
        early = early_analysis(snapshot, parsed)
        if early is not None:
            return early
        # 执行前提**不再短路**（ADR-027 修订 ADR-022）：件现在照样进池，属性层就必须照算 ——
        # 短路会把"差多少"整个吞掉（真机 2026-10-06 泰坦：三个格 10/10 满，用户连数字都问不出来）。
        # 那几条仍然带出去，但身份变了：从"为什么没有解"降级成"这些件要先准备"。
        return annotate_analysis(
            analyze_from_probes(parsed, await self._probe_single_stat_ceilings(snapshot, parsed)),
            execution_blockers(snapshot, parsed, self._manifest),
        )

    async def probe_find_build(
        self, player_name: str, request: BuildRequest
    ) -> list[BuildResult]:
        """阶梯的逐档试解：与 `find_build` 同一条路，走只读并发档、不登记候选。"""
        return await self.find_build(
            player_name, request, compute=self._probe_compute, register=False
        )

    async def _probe_single_stat_ceilings(
        self, snapshot: InventorySnapshot, parsed: Any
    ) -> dict[str, int]:
        """六项单项上限：**并发**跑（互不依赖、都是只读纯计算）。

        真机实测（2026-09-23，真无解请求）：串行 6 次 = **227 秒**，占了整张阶梯 427 秒的
        一半；并发后 wall 时间约等于其中最慢的一次。写入路径一个字没碰 —— 并发只给这种
        只读探测用（`_probe_compute` 的档位固定 4，主链路仍是 `_compute` 的串行档）。
        """
        probes = await asyncio.gather(*[
            self._probe_compute.run(probe_stat, snapshot, parsed, index)
            for index in range(len(STAT_NAMES))
        ])
        return dict(probes)

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
        return await self._compute.run(
            find_farm_targets,
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
        functional_mods: list[str] | str | None = None,
    ) -> BuildRecommendation:
        """Find builds and include diagnostics when no candidate is available."""
        results = await self.find_build(player_name, request, functional_mods=functional_mods)
        if results:
            return BuildRecommendation(results=results)

        analysis = await self.analyze_build(player_name, request)
        return BuildRecommendation(results=[], analysis=analysis)

    def get_build_candidate(self, player_name: str, execution_id: str) -> dict:
        """按候选 ID 取回服务端签发的那份方案（只读，不焚烧）—— 给只发标量的宿主用。

        判定与话术在 `services/candidate_messages`（与四个失败的翻译同处），
        这里只转发。
        """
        return describe_candidate(self._candidates, player_name, execution_id)

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
                "code": ErrorCode.MISSING_EXECUTION_ID,
                "message": "配装缺少服务端候选 ID，请重新运行 find_build 后再确认。",
            }
        trusted, status = self._candidates.resolve(build.execution_id, player_name)
        if status != "ok" or trusted is None:
            # 四个状态各有各的话术（唯一出处 `services/candidate_messages`）。
            return candidate_failure(status) or candidate_failure("unknown") or {}
        if trusted.model_dump(mode="json") != build.model_dump(mode="json"):
            return {
                "success": False,
                "code": ErrorCode.CANONICAL_BUILD_MISMATCH,
                "message": "确认后的配装内容发生变化，已拒绝执行。请重新选择候选。",
            }
        # ⚠️ 焚烧**不在这里**：以前在这一行 `consume()`，于是写前复检拦下时账号没改、候选却烧掉了
        # → 同 ID 重试只能拿到 `unknown_execution_id`（ADR-025）。现在推迟到写成功之后。
        build = trusted
        if build.class_type:
            build_character = class_type_name(
                resolve_character_name(build.class_type)
            ).lower()
            if build_character != normalized_character:
                return {
                    "success": False,
                    "code": ErrorCode.CHARACTER_MISMATCH,
                    "message": "确认的配装职业与目标角色不一致，请重新生成配装。",
                }
        if not build.snapshot_version:
            return {
                "success": False,
                "code": ErrorCode.MISSING_SNAPSHOT_VERSION,
                "message": "配装缺少库存快照版本，请重新运行 find_build 后再确认。",
            }
        if len(build.items) != 5:
            return {
                "success": False,
                "code": ErrorCode.INVALID_ITEM_COUNT,
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
                "code": ErrorCode.INVALID_EXACT_ITEMS,
                "message": "配装实例或护甲槽位不完整，请重新生成配装。",
            }
        if any(mod_hash <= 0 for item in build.items for mod_hash in item.mods):
            return {
                "success": False,
                "code": ErrorCode.INVALID_MOD_HASH,
                "message": "配装包含无效模组 Hash，请重新生成配装。",
            }

        # 写账号之前的最后一段只读闸（顺序与理由见 `services/build_execution_guard`）。
        # 写前准备：格满腾一件 + 顶下冲突金装（ADR-029/030），都排在复检之前
        room_steps, room_prefix, room_refusal = await prepare_build_write(
            player_name=player_name, character=normalized_character, inventory=self._inventory, equipment=self._equipment, manifest=self._manifest, build=build, candidates=self._candidates)
        if room_refusal is not None:
            return room_refusal
        refusal = await recheck_confirmed_build(
            inventory=self._inventory,
            player_name=player_name,
            character=normalized_character,
            build=build,
        )
        if refusal is not None:
            return refusal

        build = ExecutableBuild.model_validate(build.model_dump())
        loadout = Loadout(
            id=f"build:{build.snapshot_version}",
            name="已确认的精确配装",
            character=normalized_character,
            items=build.items,
            subclass=canonical_subclass(build),
            source="build",
        )
        # 先试一次；撞上上游格满（NoRoomInDestination）就按回执点名的件腾一格、再试一次（ADR-029 P2）
        result, retry_steps, retry_note = await equip_with_make_room_retry(
            attempt=lambda: self._equipment.equip_with_recovery(player_name, loadout),
            make_room=make_room_for_build, room_args={  # 预判那份参数照传，重试时只补 names
                "player_name": player_name, "character": normalized_character, "inventory": self._inventory, "equipment": self._equipment, "manifest": self._manifest, "build": build, "candidates": self._candidates})
        # 焚烧的时刻：**写成功之后**。失败的执行（被拦、搬运失败、回滚过）不消耗候选 ——
        # 那种情况账号要么没动、要么已回到执行前；重放保护没削弱：写前复检每次重读现场。
        if result.success:
            self._candidates.consume(build.execution_id)
        else:  # 自己动过账号 → 推进候选基线，否则同一个 ID 重试必然 stale（见 build_baseline）
            result.message += await rebaseline_note(self._candidates, self._inventory, player_name, normalized_character, build)
        return {
            "success": result.success,
            "character": normalized_character,
            "snapshot_version": build.snapshot_version,
            "message": room_prefix + retry_note + result.message,  # 腾过就说（ADR-029 §5）
            "steps": room_steps + make_room_steps(retry_steps) + [s.model_dump() for s in result.steps],
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
            player_name, target_character, score,
        )
        return {
            "success": False,
            "code": ErrorCode.EXACT_BUILD_REQUIRED,
            "message": "不能再按浮点 score 重新求解并装备；请传回 find_build 返回的 canonical_build。",
        }
