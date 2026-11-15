"""锻造图样查询：图鉴「模式和催化」里**武器模式**那一半（183 条）。

两个来源、两种性质，绝不能混：

- **Manifest**：图样目录 —— 展示树（根节点 `PATTERN_ROOT_NODE`）→ 槽位分组 → 武器类型 → 记录，
  每条记录的名字就是武器名、目标 `completionValue` 就是要萃取几次；静态，随 Manifest 变。
- **账号**：profile 组件 900（`profileRecords`）里那条记录的 `objectives[0].progress`
  —— 就是游戏里那条「图样进度 4/5」。账号数据，每次现读（5 分钟 TTL 缓存提取后的状态）。

为什么不走另外两个组件（实测，见 `docs/plans/PATTERN_QUERY_PLAN.md`）：

- 800（收藏品）里图样解锁状态**一条都没有**：拿「模式和催化」的节点去查 `collectible_node`
  只能回 `total=0`（响应里那句"条目的解锁状态不在这个组件里"说的就是这件事）；
- 1300（Craftables）有 219 条但 `visible` 全是 true，只回答"这把能塑形哪些 perk"，没有进度。

目录判据不认本地化类型字符串（中文「武器模式」/英文 `Weapon Pattern` 都不当判据）：
**记录名必须与某件可锻造武器（`inventory.recipeItemHash` 非空）的名字精确相等**。
实测 183/183 命中、零歧义，而且顺带把 141 条异域催化挡在门外 —— 催化记录名带「催化」后缀，
用模糊匹配会误中同名武器。同一把武器的（专家）/（失时）/（痛苦）变体不单列图样（图样记录挂在
基础版上），单独问变体名时指回基础版。
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

from ..bungie_client import BungieClient
from ..exceptions import APIError, InvalidArgumentError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver
from ..utils.hash_utils import to_unsigned
from .. import vocabulary
from . import profile_components
from .starside_crafting_sources import CraftingSources

logger = get_logger(__name__)

# 图鉴「模式和催化」的根展示节点；子结构：根 → 分组容器 → 槽位分组 → 武器类型 → 记录。
PATTERN_ROOT_NODE = 2642502414
_MAX_TREE_DEPTH = 4
# 记录进度读一次 1.44 MB / 约 2.5 s（实测），所以缓存**提取后的状态**（几百条小字典），
# 不缓存那 1.44 MB 原文；5 分钟足够避免"连着问几把"重复拉整包。
_STATE_TTL_SECONDS = 300

# `read` 块：只放**每次调用都一样**的口径（数据来源 + 缓存时长）。命中缓存与现读必须返回
# 同一个块 —— 别名等价那条语料比对 `data` 逐字节相同，两个分支给不同键就会当场判红。
_READ_BLOCK = {
    "component": 900,
    "record_scopes": ["profile", "character"],
    "ttl_seconds": _STATE_TTL_SECONDS,
}

STATUS_UNLOCKED = "已解锁"
STATUS_IN_PROGRESS = "进行中"
# 「未开始」= 账号里**没有这条记录**。不是"0/5 确认"、更不是"这把没有图样"——
# 措辞见 `_pattern_branches`，这里只给状态码。
STATUS_NOT_STARTED = "未开始"
_STATUS_ORDER = {STATUS_IN_PROGRESS: 0, STATUS_NOT_STARTED: 1, STATUS_UNLOCKED: 2}

# （专家）/（失时）/（痛苦）这类变体后缀：它们不单列图样，问到了要指回基础版。
_VARIANT_SUFFIX = re.compile(r"[（(](?:专家|失时|痛苦|adept|timelost|harrowed)[）)]$", re.IGNORECASE)
_NAME_NOISE = re.compile(r"[\s·・]+")

# 塑形栏位：`crafting.requiredSocketTypeHashes` 里出现哪几个插槽类型，就代表塑形时能选哪几个栏位
# （栏位名取自槽里的占位 plug「空枪管插槽」这类）。实测 2026-09-20：基础版都是这 5 个，
# 而 36 件变体只有前 3 个 —— 两个特征栏位不在里面，所以变体的 3/4 号特性固定。
SHAPING_SOCKETS: dict[int, str] = {
    3868679925: "框架",
    3694362576: "枪管",
    2316004942: "弹夹",
    3036227398: "特征1",
    3036227399: "特征2",
}
TRAIT_SOCKETS = frozenset({3036227398, 3036227399})
# 「空深视插槽」：只有基础版有；36 件变体全都没有，所以红框（深视共振）只会掉基础版，
# 变体自己涨不了进度。变体那个位置是「空强化插槽」（专家/失时版的升级槽，玩家说的"只能升级"）。
DEEPSIGHT_SOCKET_TYPE = 1085237186
UPGRADE_SOCKET_TYPE = 4251072212


def name_key(value: str) -> str:
    """名字比对口径：去空白与间隔号、大小写折叠（用户输入 vs Manifest 名字）。"""
    return _NAME_NOISE.sub("", (value or "").strip().casefold())


def _cell(component: Any) -> dict[str, Any]:
    """一条记录的状态：只取第一个目标的进度与需求（实测模式记录就是这个形状）。"""
    objectives = (component or {}).get("objectives") or []
    first = objectives[0] if objectives and isinstance(objectives[0], dict) else {}
    return {
        "progress": first.get("progress"),
        "need": first.get("completionValue"),
        "state": (component or {}).get("state"),
    }


def _merge_cell(state: dict[int, dict], key: Any, component: Any) -> None:
    """把一条记录并进状态表：同一记录号出现在多处（档案级 + 角色级）时取进度更靠前的那个。

    模式解锁是账号级的，所以"角色 A 满了、角色 B 没满"应当算已解锁（实测那 32 条角色级记录
    三个角色数值一致，这条规则只是为了不把话说反）。
    """
    if not isinstance(component, dict):
        return
    try:
        record_hash = to_unsigned(int(key))
    except (TypeError, ValueError):
        return
    candidate = _cell(component)
    current = state.get(record_hash)
    if current is None or (candidate.get("progress") or -1) > (current.get("progress") or -1):
        state[record_hash] = candidate


class PatternService:
    """锻造图样：目录（Manifest）+ 进度（账号）+ 来源（本地社区资料，可选）。"""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
        starside: Any = None,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._starside = starside
        self._catalog_cache: list[dict[str, Any]] | None = None
        self._state_cache: dict[str, Any] | None = None

    # ── 目录（Manifest，进程内缓存一次） ──────────────────────────────
    def _walk(self, node_hash: int, *, depth: int = 0, slot: str = "") -> Iterator[tuple[str, str, int]]:
        if depth > _MAX_TREE_DEPTH:
            return
        node = self._manifest.get_definition("DestinyPresentationNodeDefinition", node_hash) or {}
        name = str((node.get("displayProperties") or {}).get("name") or "")
        children = node.get("children") or {}
        records = [
            item.get("recordHash")
            for item in children.get("records") or []
            if isinstance(item, dict) and isinstance(item.get("recordHash"), int)
        ]
        if records:
            for record_hash in records:
                yield slot, name, record_hash
            return
        # 第 2 层才是槽位分组（主武器模式/特殊武器模式/重武器模式），拿它当分组标签。
        next_slot = name if depth == 2 else slot
        for child in children.get("presentationNodes") or []:
            if isinstance(child, dict) and isinstance(child.get("presentationNodeHash"), int):
                yield from self._walk(child["presentationNodeHash"], depth=depth + 1, slot=next_slot)

    def _craftable_item(self, name: str) -> dict[str, Any] | None:
        """按名字找**可锻造武器本体**：同名条目里只认 `recipeItemHash` 非空的那个。

        同名条目有两类：武器本体（type 3，有 `recipeItemHash`）与图样条目（type 30，没有）；
        精确比名字是必须的 —— 模糊匹配会让「糖果生意催化」命中「糖果生意」。
        """
        for hit in self._manifest.search(name, limit=5, item_type=3):
            if str(hit.get("name") or "").strip() != name:
                continue
            definition = self._manifest.get_item_definition(int(hit.get("itemHash") or 0)) or {}
            if (definition.get("inventory") or {}).get("recipeItemHash"):
                return definition
        return None

    def _catalog(self) -> list[dict[str, Any]]:
        """183 条图样：名字、记录 hash、需要的次数、对应武器、类型、分组。"""
        if self._catalog_cache is not None:
            return self._catalog_cache
        entries: list[dict[str, Any]] = []
        for slot, weapon_type, record_hash in self._walk(PATTERN_ROOT_NODE):
            definition = self._manifest.get_definition("DestinyRecordDefinition", record_hash) or {}
            name = str((definition.get("displayProperties") or {}).get("name") or "").strip()
            if not name:
                continue
            item = self._craftable_item(name)
            if item is None:
                continue
            objective_hash = next(iter(definition.get("objectiveHashes") or []), None)
            objective = (
                self._manifest.get_definition("DestinyObjectiveDefinition", objective_hash)
                if isinstance(objective_hash, int)
                else None
            ) or {}
            inventory = item.get("inventory") or {}
            entries.append({
                "name": name,
                "record_hash": to_unsigned(record_hash),
                "need": objective.get("completionValue"),
                "item_hash": to_unsigned(int(item.get("hash") or 0)),
                "weapon_type": weapon_type,
                "group": slot,
                # 稀有度：标签给用户看，tierType 给筛选用（词表在 vocabulary.rarity_key）
                "tier": inventory.get("tierTypeName") or "",
                "tier_type": inventory.get("tierType"),
            })
        self._catalog_cache = entries
        return entries

    # ── 账号进度（组件 900） ─────────────────────────────────────────
    async def _state(self, membership_id: str, membership_type: int) -> tuple[dict[int, dict], dict]:
        # `read` 只放**每次调用都一样**的口径（数据来源与缓存时长）。"这次读了几毫秒/有没有命中缓存"
        # 不能放进 data：别名等价那条语料比对的是 `data` 逐字节相同，而它每次都变（真机上被抓到过：
        # 同一组 10 个别名跑出 2 种 data）。诊断信息进日志。
        cached = self._state_cache
        now = time.monotonic()
        if cached and now - cached["at"] < _STATE_TTL_SECONDS:
            return cached["state"], dict(_READ_BLOCK)
        started = time.perf_counter()
        profile = await self._bungie.get_profile(
            membership_id, membership_type, profile_components.PATTERNS
        )
        data = (profile.get("profileRecords") or {}).get("data")
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, dict) or not records:
            # 拿不到就说拿不到："未返回"绝不能读成"图样都没解锁"。
            raise APIError(
                "查询锻造图样",
                "Bungie 未返回记录组件（900），这次拿不到图样进度；"
                "不能把未返回当成未解锁，请稍后重试。",
            )
        state: dict[int, dict] = {}
        for key, component in records.items():
            _merge_cell(state, key, component)
        # **角色级记录也必须读**：实测 183 条模式记录里 151 条是档案级（scope=0）、
        # 32 条是角色级（scope=1），只读 `profileRecords` 会把那 32 条全判成「未开始」
        # （真机被用户拿游戏截图当场抓出来：那 32 把其实三个角色都 5/5、
        # 账号里还有 33 件对应的已锻造副本）。模式解锁是**账号级**的，
        # 所以同一记录号取"进度最靠前的那个角色"。
        character = (profile.get("characterRecords") or {}).get("data")
        for holder in (character or {}).values():
            for key, component in ((holder or {}).get("records") or {}).items():
                _merge_cell(state, key, component)
        self._state_cache = {"at": now, "state": state}
        logger.info(
            "锻造武器模式：读组件 900 用了 %d ms（档案级 %d 条 + 角色级 %d 条 → 合并 %d 条，缓存 %d 秒）",
            int((time.perf_counter() - started) * 1000), len(records),
            sum(len(((h or {}).get("records") or {})) for h in (character or {}).values()),
            len(state), _STATE_TTL_SECONDS,
        )
        return state, dict(_READ_BLOCK)

    @staticmethod
    def _row(entry: dict[str, Any], state: dict[int, dict]) -> dict[str, Any]:
        info = state.get(entry["record_hash"])
        need = entry["need"]
        if info is None:
            status, progress = STATUS_NOT_STARTED, None
        else:
            need = info.get("need") or need
            progress = info.get("progress")
            complete = bool(progress is not None and need and progress >= need)
            status = STATUS_UNLOCKED if complete else STATUS_IN_PROGRESS
        # 「还差几个」在服务层算好：工具层只排版面，不碰数字（那里的服务可能被替身顶掉）。
        remaining = need - progress if (progress is not None and need) else None
        return {
            "name": entry["name"],
            "weapon_type": entry["weapon_type"],
            "group": entry["group"],
            "tier": entry["tier"],
            "tier_type": entry["tier_type"],
            "need": need,
            "progress": progress,
            "remaining": remaining,
            "status": status,
            "item_hash": entry["item_hash"],
            "record_hash": entry["record_hash"],
        }

    # ── 变体：能不能塑形、能选哪些栏位（从 Manifest 现算，不写死结论） ────
    def _variant_info(self, variant_name: str, base_name: str) -> dict[str, Any]:
        """问（专家）/（失时）/（痛苦）这类变体时，把它的塑形配置一起给出去。

        实测（2026-09-20，36 件变体无一例外）：基础版塑形栏位 5 个（框架/枪管/弹夹/特征1/特征2），
        变体只有 3 个（框架/枪管/弹夹）—— 两个特征栏位不在配置里，3/4 号特性固定；
        而且变体的插槽里没有「空深视插槽」，所以红框（深视共振）只会掉基础版。
        """
        variant = self._craftable_item(variant_name)
        if variant is None:
            return {}
        base = self._craftable_item(base_name)
        variant_types = self._shaping_sockets(variant)
        base_types = self._shaping_sockets(base) if base else []
        sockets = {
            entry.get("socketTypeHash")
            for entry in ((variant.get("sockets") or {}).get("socketEntries") or [])
            if isinstance(entry, dict)
        }
        return {
            "name": variant_name,
            "base_name": base_name,
            "shapeable_columns": [SHAPING_SOCKETS.get(t, f"栏位{t}") for t in variant_types],
            "base_shapeable_columns": [SHAPING_SOCKETS.get(t, f"栏位{t}") for t in base_types],
            "traits_fixed": not TRAIT_SOCKETS <= set(variant_types),
            "has_deepsight_socket": DEEPSIGHT_SOCKET_TYPE in sockets,
            "has_upgrade_socket": UPGRADE_SOCKET_TYPE in sockets,
        }

    def _shaping_sockets(self, item: dict[str, Any] | None) -> list[int]:
        """这一件（可锻造物品）的图样条目里，可塑形的插槽类型。"""
        if item is None:
            return []
        recipe_hash = int((item.get("inventory") or {}).get("recipeItemHash") or 0)
        pattern_item = self._manifest.get_item_definition(recipe_hash) or {}
        return [
            value
            for value in ((pattern_item.get("crafting") or {}).get("requiredSocketTypeHashes") or [])
            if isinstance(value, int)
        ]

    # ── 来源（本地社区资料，可选、可失败） ────────────────────────────
    def _sources(self, names: list[str]) -> dict[str, Any]:
        """按名字查「锻造武器来源」；这一路失败只降级成 `available=false` + warning。"""
        if self._starside is None:
            return {
                "available": False,
                "results": [],
                "unmatched": list(names),
                "page": {},
                "warnings": ["本次没有接上本地社区资料；图样进度不受影响。"],
            }
        try:
            return CraftingSources.from_service(self._starside).lookup(names)
        except Exception as exc:  # noqa: BLE001 - 本地资料是附加项，坏了不能弄坏主结果
            logger.warning("锻造武器来源资料读取失败：%s", exc)
            return {
                "available": False,
                "results": [],
                "unmatched": list(names),
                "page": {},
                "error": str(exc),
                "warnings": [f"本地「锻造武器来源」资料读取失败：{exc}"],
            }

    # ── 查询 ─────────────────────────────────────────────────────────
    async def patterns(
        self,
        player_name: str = "",
        *,
        weapon_name: str = "",
        weapon_type: str = "",
        rarity: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        catalog = self._catalog()
        if not catalog:
            raise APIError("查询锻造图样", "Manifest 里没有找到图样记录，无法查询。")
        player = await self._resolver.resolve_player(player_name)
        state, read = await self._state(player["membership_id"], player["membership_type"])
        rows = [self._row(entry, state) for entry in catalog]

        variant_of = ""
        variant_info: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        if weapon_name.strip():
            rows, candidates, variant_of = self._by_name(rows, weapon_name.strip())
        if variant_of and rows:
            variant_info = self._variant_info(variant_of, rows[0]["name"])
        available_types = sorted({row["weapon_type"] for row in rows if row["weapon_type"]})
        if weapon_type.strip():
            rows = self._by_type(rows, weapon_type.strip())
        if rarity.strip():
            target_tier = vocabulary.rarity_key(rarity)
            if target_tier is None:
                raise InvalidArgumentError(
                    f"rarity={rarity!r} 不受支持。可用：exotic/异域/金枪、legendary/传说、rare/稀有；"
                    "不传或传 all = 不筛。"
                )
            rows = [row for row in rows if row.get("tier_type") == target_tier]

        rows.sort(key=lambda row: (_STATUS_ORDER.get(row["status"], 9),))
        sources = self._sources([row["name"] for row in rows])
        source_map = {item["name"]: item["source"] for item in sources.get("results") or []}
        for row in rows:
            row["source"] = source_map.get(row["name"])

        total = len(rows)
        limit = max(1, limit)
        page = rows[offset : offset + limit]
        next_offset = offset + len(page) if offset + len(page) < total else None
        return {
            "catalog_total": len(catalog),
            "counts": self._counts(rows),
            "by_group": self._by_group(rows),
            "rows": page,
            "total": total,
            "returned": len(page),
            "offset": offset,
            "next_offset": next_offset,
            "by_tier": self._by_tier(rows),
            "filtered": bool(weapon_name.strip() or weapon_type.strip() or rarity.strip()),
            "variant_of": variant_of,
            "variant": variant_info,
            "candidates": candidates,
            "available_types": available_types,
            "sources": sources,
            "read": read,
        }

    def _by_name(
        self, rows: list[dict[str, Any]], query: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        key = name_key(query)
        exact = [row for row in rows if name_key(row["name"]) == key]
        if exact:
            return exact, [], ""
        for match in (
            [row for row in rows if name_key(row["name"]).startswith(key)],
            [row for row in rows if key in name_key(row["name"])],
        ):
            if len(match) == 1:
                return match, [], ""
            if len(match) > 1:
                return [], [{k: row[k] for k in ("name", "weapon_type", "status", "item_hash")} for row in match[:8]], ""
        # 变体（专家/失时/痛苦）：图样挂在基础版上，问变体名就指回基础版。
        base = _VARIANT_SUFFIX.sub("", query)
        if base != query:
            base_key = name_key(base)
            for row in rows:
                if name_key(row["name"]) == base_key:
                    return [row], [], query
        return [], [], ""

    @staticmethod
    def _by_type(rows: list[dict[str, Any]], weapon_type: str) -> list[dict[str, Any]]:
        key = name_key(weapon_type)
        return [row for row in rows if key and key in name_key(row["weapon_type"])]

    @staticmethod
    def _by_tier(rows: list[dict[str, Any]]) -> dict[str, int]:
        """按稀有度汇总（异域/传说…）：默认一页只有 20 条，没有这个汇总，调用方会把
        "第一页里看到 2 把金枪" 当成"一共 2 把"（真机发生过）。"""
        counts: dict[str, int] = {}
        for row in rows:
            label = row.get("tier") or "未知"
            counts[label] = counts.get(label, 0) + 1
        return counts

    @staticmethod
    def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        # `not_unlocked` 是玩家口径的「未解锁」= 进行中 + 还没开始（游戏里两者都显示未解锁）；
        # 在服务层算好，工具层不碰数字。
        counts = {
            "total": len(rows), "unlocked": 0, "not_unlocked": 0,
            "in_progress": 0, "not_started": 0,
        }
        for row in rows:
            key = {
                STATUS_UNLOCKED: "unlocked",
                STATUS_IN_PROGRESS: "in_progress",
                STATUS_NOT_STARTED: "not_started",
            }.get(row["status"])
            if key:
                counts[key] += 1
        counts["not_unlocked"] = counts["in_progress"] + counts["not_started"]
        return counts

    @staticmethod
    def _by_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = groups.setdefault(
                row["group"] or "未分组",
                {"group": row["group"] or "未分组", "total": 0, "unlocked": 0,
                 "in_progress": 0, "not_started": 0, "types": {}},
            )
            bucket["total"] += 1
            status_key = {
                STATUS_UNLOCKED: "unlocked",
                STATUS_IN_PROGRESS: "in_progress",
                STATUS_NOT_STARTED: "not_started",
            }.get(row["status"])
            if status_key:
                bucket[status_key] += 1
            type_bucket = bucket["types"].setdefault(row["weapon_type"], {"total": 0, "unlocked": 0})
            type_bucket["total"] += 1
            if row["status"] == STATUS_UNLOCKED:
                type_bucket["unlocked"] += 1
        return [
            bucket | {"by_type": [{"weapon_type": name} | value for name, value in bucket.pop("types").items()]}
            for bucket in groups.values()
        ]
