"""Weapon detail service — comprehensive weapon details by type.

Requires Bungie API for profile data.
Extracted from weapon_service.py during refactoring.
"""

from __future__ import annotations

from ..exceptions import AuthenticationError, ConfigError
from ..logging_config import get_logger
from . import profile_components
from ..manifest import ManifestManager, class_type_name
from ..models import WeaponDetail, WeaponDetailResponse
from . import weapon_payload, weapon_profile, weapon_stats_payload
from ..manifest_names import names_for
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .profile_cache import ProfileCache
from .inventory_service import (
    MISSING_INVENTORY_SCOPE_MESSAGE,
    looks_like_missing_inventory_scope,
    require_complete_inventory_components,
)

logger = get_logger(__name__)


class WeaponDetailService:
    """Comprehensive weapon detail queries by weapon type."""

    def __init__(
        self,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        profile_cache: ProfileCache | None = None,
        lookup_factory=None,
    ) -> None:
        self._manifest = manifest
        self._resolver = resolver
        # 武器详情要组件 310（这一个副本能换什么），真机实测整个 profile 从 3.3 MB 涨到 10.2 MB。
        # 不接缓存就等于每次 type 查询都重新拉一遍；接上共享缓存后由 TTL 与后台刷新兜住。
        self._profile_cache = profile_cache
        # 愿单标注（god_roll_pve/pvp）：由 perk 服务提供，这里不直接依赖它
        self._lookup_factory = lookup_factory

    async def get_weapon_details_by_type(
        self,
        player_name: str,
        type_name: str,
        limit: int | None = None,
        include_selectable_plugs: bool = False,
        offset: int = 0,
        *,
        list_view: bool = False,
    ) -> WeaponDetailResponse:
        """Get comprehensive details for all weapons of a given type.

        Args:
            player_name: Bungie name.
            type_name: Weapon type display name; empty means all owned weapons.
            limit: Max weapons returned; None or <= 0 returns everything.
            offset: Skip this many weapons (after排序) —— 列表类翻页用。
            list_view: 只服务"我有哪些"的列表行：**不取 305/310、不造 sockets/options**。
                插槽池与可换项是单件的问题（`compare`），列表带着它们曾经占到载荷的 2/3。

        Returns:
            WeaponDetailResponse with matching weapons and their full details, plus
            total/returned counts so a cut list is never mistaken for a short one.

        实现要点：**先排完序、切出这一页，再逐件造明细**。以前是先给全部命中件造完明细
        再截断 —— 命中 124 件只返回 20 件时，那 104 件的插槽池白造。
        """
        if list_view and include_selectable_plugs:
            # 两者互斥：列表行不带插槽，而 selectable plugs 要挂到插槽上。放过去就会
            # 静默返回"没有可选 perk"，让社区配装核对得出**错误结论**（比报错糟得多）。
            raise ValueError("list_view 不带 sockets，不能同时要 include_selectable_plugs")

        logger.info("Getting weapon details: player=%s, type=%s", player_name, type_name)

        # Type matching must not use the capped general Manifest search.
        type_name = type_name.strip()
        target_hashes = (
            {to_unsigned(r["itemHash"]) for r in self._manifest.list_weapon_catalog(type_name)}
            if type_name else None
        )
        hash_to_info: dict[int, dict] = {}

        # Step 2: Resolve player and fetch profile
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        # 列表行不展开插槽池，就不请求 305/310 —— 两个分支必须用同一个掩码，
        # 只改带缓存那条会让"没缓存时"多拉 305/310（测试当场抓到过）。
        components = (
            profile_components.INVENTORY if list_view else profile_components.WEAPON_DETAIL
        )
        if self._profile_cache:
            profile = await self._profile_cache.get_profile(player_name, components)
        else:
            profile = await self._resolver.get_profile(mid, mtype, components)
        if looks_like_missing_inventory_scope(profile):
            raise AuthenticationError(MISSING_INVENTORY_SCOPE_MESSAGE)
        require_complete_inventory_components(profile)

        chars_data = profile.get("characters", {}).get("data", {})
        inv_data = profile.get("characterInventories", {}).get("data", {})
        equip_data = profile.get("characterEquipment", {}).get("data", {})
        vault_items = profile.get("profileInventory", {}).get("data", {}).get("items")

        # Step 3: Extract component data
        instances_data = (
            profile.get("itemComponents", {}).get("instances", {}).get("data", {})
        )
        sockets_data = (
            profile.get("itemComponents", {}).get("sockets", {}).get("data", {})
        )
        stats_data = (
            profile.get("itemComponents", {}).get("stats", {}).get("data", {})
        )
        reusable_data = (
            profile.get("itemComponents", {}).get("reusablePlugs", {}).get("data", {})
        )

        # Step 4: Collect all matching weapon instances
        weapon_instances: list[dict] = []
        seen_instances: dict[str, int] = {}

        def add_weapon(raw: dict, location: str, is_equipped: bool) -> None:
            item_hash = to_unsigned(raw["itemHash"])
            info = self._manifest.get_item_info(item_hash)
            if not info:
                if raw.get("itemInstanceId"):
                    raise ConfigError("库存实例缺少 Manifest 定义，无法确认完整武器范围；请更新 Manifest 后重试。")
                return
            if info.get("itemType") != 3:
                return
            if target_hashes is not None and item_hash not in target_hashes:
                return
            inst_id = str(raw.get("itemInstanceId") or "")
            if inst_id in {"", "0"}:
                raise ConfigError("库存武器缺少实例 ID，无法可靠筛选 Perk；请稍后重试。")
            if inst_id in seen_instances:
                if seen_instances[inst_id] != item_hash:
                    raise ConfigError("库存实例 ID 对应多个武器定义；请稍后重试。")
                return
            seen_instances[inst_id] = item_hash
            hash_to_info[item_hash] = info
            weapon_instances.append({
                "instance_id": inst_id,
                "item_hash": item_hash,
                "location": location,
                "is_equipped": is_equipped,
                "state": raw.get("state"),
                # 排序要用光等与名字：先记下来，免得排完序回头再查一遍组件/定义
                "power": (instances_data.get(inst_id) or {}).get("primaryStat", {}).get("value"),
                "name": weapon_profile.display_name_of(
                    self._manifest, self._manifest.get_item_definition(item_hash)
                ),
            })

        # From vault
        for raw in vault_items:
            if raw.get("bucketHash") == 138197802:
                add_weapon(raw, "仓库", False)

        # From characters
        for char_id, char_info in chars_data.items():
            ct = char_info.get("classType", -1)
            loc_name = class_type_name(ct)

            for raw in inv_data.get(char_id, {}).get("items", []):
                add_weapon(raw, loc_name, False)

            for raw in equip_data.get(char_id, {}).get("items", []):
                add_weapon(raw, loc_name, True)

        # Step 5: 排序 → 切页 → 只给这一页造明细（顺序与旧的"先全造再截断"一致）
        # 末位用 instance_id 兜底：同名同光等的副本否则没有稳定次序，翻页会漏件或重件。
        total = len(weapon_instances)
        weapon_instances.sort(
            key=lambda wi: (
                not wi["is_equipped"],
                -int(wi.get("power") or 0),
                str(wi.get("name") or ""),
                wi["instance_id"],
            )
        )
        page = weapon_instances[offset:] if not limit or limit <= 0 else weapon_instances[offset:offset + limit]

        names = names_for(self._manifest)
        # 同一个定义会被多个副本复用：能滚几栏的计数按 item_hash 缓存一次
        summary_cache: dict[int, dict] = {}
        details: list[WeaponDetail] = []
        for wi in page:
            inst_id = wi["instance_id"]
            item_hash = wi["item_hash"]

            inst_info = instances_data.get(inst_id, {})
            weapon_def = self._manifest.get_item_definition(item_hash)

            raw_sockets = [] if list_view else sockets_data.get(inst_id, {}).get("sockets", [])
            plug_hashes = [int(socket.get("plugHash") or 0) for socket in raw_sockets]
            perks_complete = bool(raw_sockets)
            for plug_hash in plug_hashes:
                if plug_hash and not self._manifest.get_item_info(plug_hash) and not isinstance(
                    self._manifest.get_item_definition(plug_hash), dict
                ):
                    perks_complete = False

            if item_hash not in summary_cache:
                summary_cache[item_hash] = weapon_profile.roll_summary_from_columns(
                    weapon_profile.socket_columns(self._manifest, weapon_def)
                )
            # 列表类只放"这一件能换什么"（组件 310）+ 现在装的；完整池是单把武器的问题
            # （`info`/`perk_pool`）。实测把整张池子塞进列表：5 把武器 597 KB。
            # `list_view` 连"这一件能换什么"都不要：那是单件的问题，列表行不带 sockets/options。
            sockets = [] if list_view else weapon_payload.column_list(
                self._manifest, weapon_def, equipped=plug_hashes
            )
            if include_selectable_plugs:
                # 只在社区配装的 roll 核对里开：给每栏挂上"这一件能换成哪些 plug"的
                # **hash**（不是展开成名字的 options）——列表体积几乎不变，
                # 足够回答"模板要的 perk 是现在装着、还是能换到、还是压根没有"。
                selectable = weapon_profile.instance_selectable_plug_hashes(
                    self._manifest, weapon_def, reusable_data.get(inst_id)
                )
                # 没有这一件的 310 数据就**不挂这个键**：下游据此区分"读到了、确实没有"
                # 与"这次没读"（挂成空列表会把前者冒充成后者，或反过来）。
                if selectable:
                    for socket in sockets:
                        socket["selectable_plug_hashes"] = selectable.get(
                            int(socket.get("socket_index", -1)), []
                        )
            options = [] if list_view else weapon_payload.socket_list(
                self._manifest,
                weapon_def,
                scope="instance",
                reusable=reusable_data.get(inst_id),
                equipped=plug_hashes,
                names=names,
                # 列表类只给"能换成什么"的名字与结论；描述与图标在单把武器的
                # info/analyze/compare 里给（实测每件武器因此省下约 10 KB）
                include_descriptions=False,
                include_icons=False,
            )

            inst_stats = (stats_data.get(inst_id, {}) or {}).get("stats", {})
            stats = weapon_stats_payload.stat_list(self._manifest, weapon_def, inst_stats)
            weapon = weapon_payload.weapon_block(
                self._manifest,
                weapon_def,
                roll_summary=summary_cache[item_hash],
                instance=inst_info,
                names=names,
            )

            instance_meta = weapon_profile.instance_fields(inst_info)
            state_flags = weapon_profile.item_state_flags(wi.get("state"))

            notes: list[str] = []
            if instance_meta.get("legacy_tier"):
                notes.append(
                    "这件装备没有分级（组件里的 gearTier=0，旧装备或不属于分级体系），"
                    "不是 T0；gear_tier 输出 null。"
                )
            if instance_meta["missing"]:
                notes.append(
                    "该副本缺少实例信息（组件 300 未返回）："
                    + "、".join(instance_meta["missing"])
                    + " 为 null"
                )
            if state_flags["locked"] is None:
                notes.append("库存条目未带 state 字段：locked/tracked 为 null")
            if not list_view and not options:
                # list_view 这次**没请求** 310，说"没有可换部件"就是把"没读"写成"没有"。
                notes.append(
                    "该副本没有可更换部件数据（组件 310 未返回）：options 为空，"
                    "只能看 sockets 里已装的内容"
                )

            weapon["instance"] = {
                "instance_id": inst_id,
                "location": wi["location"],
                "power": inst_info.get("primaryStat", {}).get("value"),
                "is_equipped": wi["is_equipped"],
                "locked": state_flags["locked"],
                "tracked": state_flags["tracked"],
            }

            details.append(WeaponDetail(
                weapon=weapon,
                sockets=sockets,
                options=options,
                stats=stats,
                perks_complete=perks_complete and bool(raw_sockets),
                notes=notes,
            ))

        # 明细已经在页内构建，这里不再排序（排序在切页之前做完，两处排序迟早会分叉）

        logger.info(
            "Weapon details for '%s': %d of %d instance(s) returned",
            type_name, len(details), total,
        )

        return WeaponDetailResponse(
            weapon_type_query=type_name,
            weapons=details,
            total_weapons=total,
            returned_weapons=len(details),
            # "还有更多"要按**整张列表**算：翻了页之后 len(details) < total 不代表后面还有
            truncated=offset + len(details) < total,
        )
