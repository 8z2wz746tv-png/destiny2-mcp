"""本地资料挂载：把愿单、选取率、刷取清单、社区资料汇总到**每个 plug** 上。

以前这四份资料在响应里是四个平行的兄弟字段（`farming_list` / `community_references` /
`popularity` / 愿单标注散在选项上），调用方要判断"这个 perk 值不值得留"就得按名字跨四段拼。
现在：

- `weapon.farming` / `weapon.popularity` / `weapon.community` / `weapon.sources`
  收进模板（`sources` 把"这条结论哪来的、什么时候的、可不可信"合并成一张表）；
- 每个插槽选项上挂 `recommended`，四路结论就地汇总，名字不用再对。

两条铁律：
1. **本地资料永远不能弄坏官方数据查询**：任何一路读失败都只是缺一块 + warning；
2. 本地资料是**参考**（`trust` 标出来），不是官方事实，措辞里不许当成结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 本地清单里"必刷特性"的栏位名（清单自己的写法）
FARMING_COLUMNS = ("三号位", "四号位")


@dataclass
class LocalWeaponData:
    """一把武器的本地资料汇总结果。"""

    farming: dict[str, Any] = field(default_factory=dict)
    popularity: dict[str, Any] | None = None
    community: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    perk_index: dict[int, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    available: bool = False

    def recommended_for(self, plug_hash: int, *, wishlist: tuple[bool, bool] | None) -> dict[str, Any] | None:
        """这一颗 plug 的本地结论（没有就返回 None，不编空壳）。"""
        entry = dict(self.perk_index.get(int(plug_hash)) or {})
        if wishlist and (wishlist[0] or wishlist[1]):
            entry["wishlist"] = {"pve": bool(wishlist[0]), "pvp": bool(wishlist[1])}
        return entry or None


def _first_matched_row(farming: dict[str, Any], weapon_name: str) -> dict[str, Any] | None:
    rows = [row for row in farming.get("results") or [] if isinstance(row, dict)]
    if not rows:
        return None
    exact = [row for row in rows if str(row.get("name") or "").strip() == weapon_name.strip()]
    return (exact or rows)[0]


def farming_block(
    farming: dict[str, Any],
    weapon_name: str,
    *,
    weapon: dict[str, Any] | None = None,
    sockets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """刷取清单块：评级 + 来源 + 必刷特性 + 与 Manifest 的交叉核对。

    清单是社区评级（`trust=untrusted_reference`），所以既要给 `recommended_perks`，
    也要给 `cross_check`：清单里写的特性现在还滚不滚得到、框架/伤害类型对不对得上。
    """
    block: dict[str, Any] = {
        "available": bool(farming.get("available")),
        "matched": False,
        "coverage_scope": farming.get("coverage_scope", "indexed_rated_lists_only"),
    }
    row = _first_matched_row(farming, weapon_name)
    if row is None:
        block["note"] = "本地刷取清单没有收录这把武器；未收录不等于不值得刷。"
        return block

    block.update({
        "matched": True,
        "list": row.get("list", ""),
        "tier": row.get("tier", ""),
        "scale": row.get("scale", ""),
        "remark": row.get("remark", ""),
        "note": row.get("note", ""),
        "source": row.get("source", ""),
        "source_ref": row.get("source_ref") or {},
        "recommended_perks": {
            column: list(row.get("perks", {}).get(column) or [])
            for column in FARMING_COLUMNS
            if row.get("perks", {}).get(column)
        },
    })
    block["cross_check"] = _cross_check(row, weapon or {}, sockets or [])
    return block


def _cross_check(
    row: dict[str, Any],
    weapon: dict[str, Any],
    sockets: list[dict[str, Any]],
) -> dict[str, Any]:
    """清单 vs Manifest：只报"对得上/对不上/查不到"，不替调用方下结论。"""
    listed_frame = str(row.get("frame") or "").strip()
    manifest_frame = str(weapon.get("frame") or weapon.get("intrinsic") or "").strip()
    listed_element = str(row.get("element") or "").strip()
    manifest_element = str(weapon.get("damage_type") or "").strip()

    pool: dict[str, dict[str, Any]] = {}
    for socket in sockets:
        for option in socket.get("options") or []:
            name = str(option.get("name") or "").strip()
            if name:
                pool.setdefault(name, option)

    perks: list[dict[str, Any]] = []
    for column, names in (block_names(row) or {}).items():
        for name in names or []:
            found = pool.get(str(name).strip())
            perks.append({
                "name": str(name),
                "column": column,
                "in_manifest_pool": found is not None,
                "can_roll": bool(found.get("can_roll")) if found else None,
            })

    return {
        "frame": {
            "listed": listed_frame,
            "manifest": manifest_frame,
            "agrees": _loose_match(listed_frame, manifest_frame),
        },
        "element": {
            "listed": listed_element,
            "manifest": manifest_element,
            "agrees": bool(listed_element and listed_element == manifest_element),
        },
        "perks": perks,
        "note": (
            "cross_check 只说明清单与当前 Manifest 是否对得上；"
            "can_roll=false 表示这个 perk 已退役（清单可能早于退役）。"
        ),
    }


def block_names(row: dict[str, Any]) -> dict[str, list[str]]:
    perks = row.get("perks")
    return perks if isinstance(perks, dict) else {}


def _loose_match(listed: str, manifest: str) -> bool:
    """清单写「精确重击 65」，Manifest 写「精确重击框架」：按前缀算对得上。"""
    if not listed or not manifest:
        return False
    listed_head = listed.split()[0]
    return listed.startswith(manifest) or manifest.startswith(listed_head) or listed_head in manifest


def popularity_index(popularity: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    """选取率快照 → `plug_hash → {selection_rate, rank, column}`。"""
    index: dict[int, dict[str, Any]] = {}
    if not isinstance(popularity, dict):
        return index
    for column in popularity.get("perk_columns") or []:
        entries = [entry for entry in (column.get("items") or []) if isinstance(entry, dict)]
        ranked = sorted(
            entries,
            key=lambda entry: float(entry.get("selection_rate") or 0.0),
            reverse=True,
        )
        for rank, entry in enumerate(ranked, start=1):
            plug_hash = entry.get("plug_hash")
            if not plug_hash:
                continue
            index[int(plug_hash)] = {
                "selection_rate": entry.get("selection_rate"),
                "rank": rank,
                "column": column.get("label") or column.get("slot_name") or "",
            }
    return index


def farming_index(farming_block_data: dict[str, Any]) -> dict[str, list[str]]:
    """`perk 名 → 清单里的栏位`（按名字对齐；清单没有 plug_hash）。"""
    index: dict[str, list[str]] = {}
    for column, names in (farming_block_data.get("recommended_perks") or {}).items():
        for name in names:
            index.setdefault(str(name).strip(), []).append(column)
    return index


def community_matches(community: dict[str, Any], perk_name: str) -> list[str]:
    """社区资料里**提到过**这颗 perk 的条目 id（标题/分组里出现名字）。

    是按名字子串匹配的粗定位，所以只当"去哪儿看"的线索，不当结论；
    查不到就是没匹配上，不代表社区没讨论过。
    """
    needle = perk_name.strip().casefold()
    if not needle:
        return []
    hits: list[str] = []
    for entry in community.get("results") or []:
        if not isinstance(entry, dict):
            continue
        knowledge_id = str(entry.get("knowledge_id") or "")
        haystack = f"{entry.get('title') or ''} {entry.get('group') or ''}".casefold()
        if knowledge_id and needle in haystack:
            hits.append(knowledge_id)
    return hits[:1]


def sources_block(
    *,
    farming: dict[str, Any],
    popularity: dict[str, Any] | None,
    community: dict[str, Any],
) -> list[dict[str, Any]]:
    """把三处来源合成一张表：谁给的、什么时候的、可不可信。"""
    sources: list[dict[str, Any]] = []
    if farming.get("matched"):
        ref = farming.get("source_ref") or {}
        sources.append({
            "kind": "farming_list",
            "name": farming.get("list", ""),
            "updated_at": ref.get("updated_at", ""),
            "path": ref.get("local_path", ""),
            "trust": ref.get("trust", "untrusted_reference"),
        })
    if isinstance(popularity, dict):
        snapshot = popularity.get("source") or {}
        sources.append({
            "kind": "popularity_snapshot",
            "name": snapshot.get("label", ""),
            "updated_at": snapshot.get("captured_at", ""),
            "trust": snapshot.get("kind", "untrusted_reference"),
            "note": snapshot.get("note", ""),
        })
    if community.get("results"):
        sources.append({
            "kind": "community_archive",
            "name": "Starside 社区资料",
            "updated_at": str(community.get("archive_updated_at") or ""),
            "matched_count": community.get("matched_count", 0),
            "trust": "untrusted_reference",
        })
    return sources


def collect(
    weapon_name: str,
    *,
    starside: Any = None,
    popularity_svc: Any = None,
    farming_limit: int = 3,
) -> LocalWeaponData:
    """读四路本地资料（全部 fail-soft）。"""
    data = LocalWeaponData()

    # 故意捕宽：本地资料是**附加项**，损坏的数据文件可能抛 OSError/ValueError/json 错，
    # 任何一种都不该让官方数据查询失败（语料横切规则：坏的可选资料不能弄坏主结果）。
    if starside is not None:
        try:
            data.farming = starside.lookup_farming(weapon_name, limit=farming_limit)
        except Exception as exc:  # noqa: BLE001
            data.warnings.append(f"本地刷取清单读取失败：{exc}")
        try:
            data.community = starside.search_knowledge(
                weapon_name, category="weapons", limit=3
            )
        except Exception as exc:  # noqa: BLE001
            data.warnings.append(f"本地社区资料读取失败：{exc}")

    if popularity_svc is not None:
        try:
            data.popularity = popularity_svc.get_weapon_popularity(weapon_name)
        except Exception as exc:  # noqa: BLE001
            data.warnings.append(f"本地选取率快照读取失败：{exc}")

    data.available = bool(data.farming or data.popularity or data.community)
    return data


# 列表类不逐个 weapon 读本地资料：给统一的"没查"块，保证键集合处处一致
NOT_CHECKED_NOTE = "列表类不逐个武器读本地资料；要看这把的本地结论用 info/analyze。"
# 覆盖表（计划 §3.4）：每个 intent 只带它该带的块，别的一律明说"这个 intent 不查"
NOT_IN_THIS_INTENT = "这个 intent 不带这项本地资料；需要时用对应的 intent 查。"


def attach(
    data: LocalWeaponData,
    *,
    weapon: dict[str, Any],
    sockets: list[dict[str, Any]],
    weapon_name: str,
    wishlist_lookup: Any = None,
    mode: str = "full",
    include_popularity: bool = True,
    include_community: bool = True,
) -> None:
    """把本地资料挂进 `weapon` 块，并给每个选项补 `recommended`（就地修改）。

    `mode="full"`：单把武器（info/analyze/perk_pool/god_roll/popularity）——
    清单块 + 选取率摘要 + 社区条目 + 来源表 + 每个选项的 `recommended`。
    `mode="lean"`：列表类（type/catalog/filter_rolls）—— 只留清单摘要，
    其余两路明说"没查"，键集合与 full 一致（调用方不用为每个 intent 学一套）。
    """
    if not isinstance(weapon, dict):
        # 形状不对（替身/异常上游）时不要在这里炸：本地资料是附加项，不是必需项
        data.warnings.append("武器身份块不是字典，已跳过本地资料挂载。")
        return
    farming = farming_block(data.farming, weapon_name, weapon=weapon, sockets=sockets)
    full = mode == "full"
    with_popularity = full and include_popularity
    with_community = full and include_community
    perk_index = popularity_index(data.popularity) if with_popularity else {}
    farm_index = farming_index(farming)

    if full:
        for socket in sockets:
            for option in socket.get("options") or []:
                plug_hash = int(option.get("plug_hash") or 0)
                name = str(option.get("name") or "").strip()
                entry: dict[str, Any] = {}
                if plug_hash in perk_index:
                    entry["popularity"] = perk_index[plug_hash]
                columns = farm_index.get(name)
                if columns:
                    entry["farming"] = {"columns": columns, "must_farm": True}
                hits = community_matches(data.community, name) if with_community else []
                if hits:
                    entry["community"] = {"knowledge_id": hits[0], "match": "name_substring"}
                if wishlist_lookup is not None and plug_hash:
                    pve, pvp = wishlist_lookup(plug_hash)
                    if pve or pvp:
                        entry["wishlist"] = {"pve": bool(pve), "pvp": bool(pvp)}
                if entry:
                    option["recommended"] = entry

    weapon["farming"] = farming if full else farming_summary(data, weapon_name)
    weapon["popularity"] = (
        _popularity_summary(data.popularity, perk_index)
        if with_popularity
        else {"available": False, "note": NOT_CHECKED_NOTE if not full else NOT_IN_THIS_INTENT}
    )
    weapon["community"] = (
        {
            "archive_available": bool(data.community.get("results")),
            "matched_count": data.community.get("matched_count", 0),
            "results": data.community.get("results") or [],
            "error": data.community.get("error"),
        }
        if with_community
        else {
            "archive_available": False,
            "matched_count": 0,
            "results": [],
            "note": NOT_CHECKED_NOTE if not full else NOT_IN_THIS_INTENT,
        }
    )
    weapon["sources"] = sources_block(
        farming=farming,
        popularity=data.popularity if with_popularity else None,
        community=data.community if with_community else {},
    )


def _popularity_summary(
    popularity: dict[str, Any] | None, perk_index: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(popularity, dict):
        return {"available": False, "note": "本地没有这把武器的选取率快照；没有不等于没人用。"}
    return {
        "available": True,
        "indexed_perks": len(perk_index),
        "top_combinations": (popularity.get("popular_combinations") or [])[:3],
        "columns": [
            {
                "label": column.get("label") or column.get("slot_name") or "",
                "items": [
                    {
                        "name": entry.get("name"),
                        "plug_hash": entry.get("plug_hash"),
                        "selection_rate": entry.get("selection_rate"),
                    }
                    for entry in (column.get("items") or [])[:5]
                ],
            }
            for column in popularity.get("perk_columns") or []
        ],
        "source": popularity.get("source") or {},
    }


def farming_summary(data: LocalWeaponData, weapon_name: str) -> dict[str, Any]:
    """列表类用的精简刷取块（一把武器只留一行）。"""
    farming = farming_block(data.farming, weapon_name)
    if not farming.get("matched"):
        return {"available": farming.get("available", False), "matched": False}
    return {
        "available": farming.get("available", False),
        "matched": True,
        "tier": farming.get("tier", ""),
        "list": farming.get("list", ""),
        "source": farming.get("source", ""),
        "recommended_perks": farming.get("recommended_perks") or {},
        "updated_at": (farming.get("source_ref") or {}).get("updated_at", ""),
    }
