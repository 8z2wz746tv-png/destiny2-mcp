"""武器 intent 的载荷组装：数据形状 + 人话摘要 + 警告，集中在一处。

`assistants.py` 只负责认参数、选分支、调服务；"这次返回什么结构、摘要怎么说"
放这里，原因是：形状统一之后每个 intent 的差别只剩"带哪几块"，
继续散在 1400 行的工具文件里就没法一眼比对（这也正是这把武器以前有十处形状的原因）。

规矩：
- 数据形状一律来自 `services.weapon_payload`，这里不自己拼字段；
- 摘要说人话（说清是定义级还是副本级、是没读到还是没有）；
- 列表类必须带 `total`/`returned`/`truncated`。
"""

from __future__ import annotations

from typing import Any

from ..services import weapon_local_data, weapon_payload, weapon_profile
from ..services.weapon_payload import schema_block
from ._enrichment import community_enrichment
from ._farming import farming_reference, harvest_names
from ._responses import error_response, ok_response


def _collect_notes(items: list[dict[str, Any]]) -> list[str]:
    """把逐条的说明汇总成一条警告，避免 50 把武器刷 50 条一样的提示。"""
    counts: dict[str, int] = {}
    for item in items:
        for note in item.get("notes") or []:
            counts[note] = counts.get(note, 0) + 1
    return [f"{note}（{count} 件）" if count > 1 else note for note, count in counts.items()]


def _farming(svc: dict[str, Any], names: Any) -> dict[str, Any]:
    """列表类：一份清单覆盖整张列表（逐把武器读会刷屏，也慢）。"""
    return farming_reference(svc.get("starside_svc"), names)


def _local(svc: dict[str, Any], weapon_name: str) -> weapon_local_data.LocalWeaponData:
    return weapon_local_data.collect(
        weapon_name,
        starside=svc.get("starside_svc"),
        popularity_svc=svc.get("perk_svc"),
    )


def _attach_local(
    svc: dict[str, Any],
    local: weapon_local_data.LocalWeaponData,
    *,
    weapon: dict[str, Any],
    sockets: list[dict[str, Any]],
    weapon_name: str,
    include_popularity: bool = True,
    include_community: bool = True,
    mode: str = "full",
) -> list[str]:
    """挂本地资料 + 每个选项的 recommended，并返回要冒泡的警告。

    `include_*` 按覆盖表（计划 §3.4）逐 intent 指定：不该带的块会写明"这个 intent 不查"，
    而不是悄悄少一个键（少键会让调用方以为"没有数据"）。
    """
    lookup = svc["perk_svc"].god_roll_lookup(int(weapon.get("item_hash") or 0))
    weapon_local_data.attach(
        local,
        weapon=weapon,
        sockets=sockets,
        weapon_name=weapon_name,
        wishlist_lookup=lookup,
        mode=mode,
        include_popularity=include_popularity,
        include_community=include_community,
    )
    return list(local.warnings)


# ── 定义级：info / analyze / perk_pool ───────────────────────────────────


def info_payload(svc: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    template = svc["manifest_query_svc"].get_weapon_full_info(
        weapon_name, lookup_factory=svc["perk_svc"].god_roll_lookup
    )
    weapon = template["weapon"]
    local = _local(svc, weapon_name)
    warnings = _attach_local(
        svc, local, weapon=weapon, sockets=template["sockets"], weapon_name=weapon_name,
        include_popularity=False,  # 覆盖表：info 不带选取率，要看用 popularity
    )
    columns = (weapon.get("roll_summary") or {}).get("random_columns") or []
    farming = weapon.get("farming") or {}
    tier = f"，清单 {farming.get('tier')}" if farming.get("matched") else ""
    summary = (
        f"已读取「{weapon.get('name') or weapon_name}」"
        f"（{weapon.get('rarity') or '稀有度未知'}"
        f"{'，可滚 ' + str(len(columns)) + ' 栏' if columns else '，固定 roll'}{tier}）。"
    )
    return ok_response(
        summary,
        {**template, **schema_block()},
        warnings=warnings + _fixed_roll_notes(weapon),
    )


def _fixed_roll_notes(weapon: dict[str, Any]) -> list[str]:
    """固定 roll 武器：把"别指望推荐 roll"这句话说清楚（计划 §3.5）。"""
    if (weapon.get("roll_summary") or {}).get("roll_kind") == "fixed":
        return ["这把是固定 roll 武器：可换的只有部件（枪管/弹匣/模组），特性栏没有随机池。"]
    return []


async def perk_pool_payload(svc: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    result = await svc["perk_svc"].get_weapon_perks(weapon_name)
    weapon = result["weapon"]
    local = _local(svc, weapon_name)
    warnings = _attach_local(
        svc, local, weapon=weapon, sockets=result["sockets"], weapon_name=weapon_name
    )
    columns = (weapon.get("roll_summary") or {}).get("random_columns") or []
    summary = (
        f"已读取「{weapon.get('name') or weapon_name}」的 {len(result['sockets'])} 个插槽"
        + (f"，其中可滚 {len(columns)} 栏。" if columns else "（固定 perk：只有部件可选）。")
    )
    if (weapon.get("roll_summary") or {}).get("roll_kind") == "fixed":
        warnings.append("固定 perk 武器（仅部件可选），不是随机池。")
    return ok_response(summary, {**result, **schema_block()}, warnings=warnings)


def analyze_payload(svc: dict[str, Any], result: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    local = _local(svc, weapon_name)
    warnings = _attach_local(
        svc, local, weapon=result["weapon"], sockets=result["sockets"], weapon_name=weapon_name
    )
    return ok_response(
        result["summary"],
        {
            "weapon": result["weapon"],
            "sockets": result["sockets"],
            "stats": result["stats"],
            "god_roll": result["god_roll"],
            "inventory": result["inventory"],
            "inventory_status": result["inventory_status"],
            **schema_block(),
        },
        next_actions=result["next_actions"],
        warnings=list(result["warnings"]) + warnings,
    )


def stats_payload(svc: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    result = svc["manifest_query_svc"].get_weapon_stats(weapon_name)
    return ok_response(
        f"已读取「{result['weapon'].get('name') or weapon_name}」的 {len(result['stats'])} 项属性。",
        {**result, **schema_block()},
    )


# ── 副本级：type / compare ───────────────────────────────────────────────


def type_payload(
    svc: dict[str, Any], result: Any, weapon_type: str
) -> dict[str, Any]:
    """按类型列持有武器：每件=完整模板（定义级 sockets + 实例级 options）。"""
    items: list[dict[str, Any]] = []
    lean_warnings: list[str] = []
    for weapon in result.weapons:
        item = weapon.model_dump(mode="json")
        block = item.get("weapon")
        if isinstance(block, dict):
            # 列表类口径：逐件只查清单摘要（本地索引在内存里，代价很小），
            # 但四个键必须都在 —— 少键会被读成"这把没有本地资料"。
            name = str(block.get("name") or "")
            local = _local(svc, name) if name else weapon_local_data.LocalWeaponData()
            lean_warnings += _attach_local(
                svc, local, weapon=block, sockets=[], weapon_name=name, mode="lean"
            )
        items.append(item)
    label = weapon_type or "全部武器"
    summary = (
        f"已读取「{label}」持有 {result.total_weapons} 件，本次返回 {result.returned_weapons} 件"
        + ("（已截断）。" if result.truncated else "。")
    )
    warnings = _collect_notes(items) + lean_warnings
    if result.truncated:
        warnings.append(
            f"只返回了前 {result.returned_weapons} 件；提高 limit 或缩小 weapon_type 才能看全。"
        )
    return ok_response(
        summary,
        {
            "weapons": {
                "query": weapon_type,
                "total": result.total_weapons,
                "returned": result.returned_weapons,
                "truncated": result.truncated,
                "items": items,
            },
            "farming_list": _farming(svc, harvest_names(items)),
            **schema_block(),
        },
        warnings=warnings,
    )


def compare_payload(svc: dict[str, Any], result: Any, weapon_name: str) -> dict[str, Any]:
    comparison = result.model_dump(mode="json")
    instances = comparison.get("instances") or []
    weapon = comparison.get("weapon") or {}
    # 副本的身份块也补齐本地四块（列表类口径：只给清单摘要），保证形状一致
    local = _local(svc, weapon_name)
    for instance in instances:
        block = instance.get("weapon")
        if isinstance(block, dict):
            weapon_local_data.attach(
                local, weapon=block, sockets=[], weapon_name=weapon_name, mode="lean"
            )
    scores = [
        instance["weapon"]["instance"].get("god_roll_score") or ""
        for instance in instances
        if isinstance(instance.get("weapon", {}).get("instance"), dict)
    ]
    summary = (
        f"已对比「{weapon.get('name') or weapon_name}」的 {len(instances)} 个副本"
        + (f"，有 {len(comparison.get('differences') or [])} 处已装 perk 差异。" if len(instances) > 1 else "。")
    )
    warnings = []
    if any(not score for score in scores):
        warnings.append("部分副本没有可用的愿单评分；空字符串表示本地愿单没收录，不是 0 分。")
    if any(not instance.get("options") for instance in instances):
        warnings.append("部分副本没有可更换部件数据（组件 310 未返回）：options 为空。")
    return ok_response(
        summary,
        {
            "comparison": comparison,
            "farming_list": _farming(svc, weapon_name),
            **schema_block(),
        },
        warnings=warnings,
    )


# ── 列表类：catalog / filter_rolls ───────────────────────────────────────


def catalog_payload(
    svc: dict[str, Any], filtered: dict[str, Any], *, include_inventory: bool, intent: str
) -> dict[str, Any]:
    if include_inventory:
        summary = (
            f"范围内 {filtered['scoped_count']} 把武器，已检查 {filtered['checked_count']} 把，"
            f"当前插槽命中 {filtered['matched_count']} 把，"
            f"无法判断 {filtered['unknown_count']} 把。"
        )
        warnings = []
        if not filtered["coverage_complete"]:
            warnings.append(
                f"有 {filtered['unknown_count']} 把武器缺少完整的当前 Perk 数据；"
                "结果不完整，不能据此断言没有符合条件的武器。"
            )
    else:
        summary = (
            f"全量武器定义检查 {filtered['checked_count']} 把，命中 {filtered['matched_count']} 把。"
        )
        warnings = ["include_inventory=false：这些是 Manifest 全量候选，未读取账号持有情况。"]
    fixed = [
        row.get("name")
        for row in (filtered.get("matched") or [])
        if isinstance(row, dict) and row.get("roll_kind") == "fixed"
    ]
    if fixed:
        warnings.append(
            f"命中的 {len(fixed)} 把是固定 roll 武器（如 {fixed[0]}）：没有 roll 可变，"
            "按 perk 筛只是确认它自带什么。"
        )
    return ok_response(
        summary,
        {
            **filtered,
            "farming_list": _farming(svc, harvest_names(filtered)),
            **schema_block(),
        },
        warnings=warnings,
    )


# ── 单块：god_roll / popularity / catalyst / perk_description ────────────


def god_roll_payload(svc: dict[str, Any], result: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    name = result.get("weapon_name") or weapon_name
    if result.get("kind") == "fixed":
        summary = f"「{name}」是固定 perk 武器，没有可推荐的随机 roll。"
    elif result.get("kind") == "recommended":
        summary = f"已读取「{name}」的社区推荐 roll。"
    else:
        summary = f"「{name}」本地没有可用的推荐 roll 数据。"
    template = svc["manifest_query_svc"].get_weapon_full_info(
        weapon_name, lookup_factory=svc["perk_svc"].god_roll_lookup
    )
    weapon = template["weapon"]
    local = _local(svc, weapon_name)
    warnings = _attach_local(
        svc, local, weapon=weapon, sockets=template["sockets"], weapon_name=weapon_name,
        include_popularity=False,  # 覆盖表：god_roll 不带选取率
    )
    if (weapon.get("roll_summary") or {}).get("roll_kind") == "fixed":
        summary = f"「{name}」是固定 roll 武器，没有可推荐的随机 roll。"
    if result.get("note"):
        warnings.append(result["note"])
    return ok_response(
        summary,
        {"weapon": weapon, "sockets": template["sockets"], "god_roll": result, **schema_block()},
        warnings=warnings,
    )


def popularity_payload(svc: dict[str, Any], result: dict[str, Any] | None, weapon_name: str) -> dict[str, Any]:
    if result is None:
        template = svc["manifest_query_svc"].get_weapon_full_info(weapon_name)
        weapon = template["weapon"]
        local = _local(svc, weapon_name)
        warnings = _attach_local(
            svc, local, weapon=weapon, sockets=template["sockets"], weapon_name=weapon_name,
            include_community=False,  # 覆盖表：popularity 不带社区条目
        )
        warnings.append("未录入不代表 0%，不应据此推断 Perk 热度。")
        return ok_response(
            f"「{weapon.get('name') or weapon_name}」暂无录入的选取率快照。",
            {"weapon": weapon, "popularity": None, **schema_block()},
            warnings=warnings,
        )
    payload = dict(result)
    warnings = list(payload.pop("warnings", []) or [])
    # 顶层 weapon 一律是 **Manifest 身份块**（与其它 intent 同形状）；
    # 快照自己记的那份（版本标签/状态/当时属性）留在 popularity.weapon 里，不混用。
    template = svc["manifest_query_svc"].get_weapon_full_info(weapon_name)
    weapon = template["weapon"]
    local = _local(svc, weapon_name)
    warnings += _attach_local(
        svc, local, weapon=weapon, sockets=template["sockets"], weapon_name=weapon_name,
        include_community=False,  # 覆盖表：popularity 不带社区条目
    )
    snapshot = payload.get("weapon") or {}
    if snapshot.get("stats"):
        warnings.append(
            "选取率快照里的属性是当时记录的，不是当前 Manifest 数值；两者不可混用。"
        )
    if (weapon.get("roll_summary") or {}).get("roll_kind") == "fixed":
        warnings.append("固定 roll 武器：选取率只反映谁在用，不反映哪套 roll 更值得刷。")
    return ok_response(
        f"已读取「{weapon.get('name') or weapon_name}」已录入的选取率快照。",
        {"weapon": weapon, "popularity": payload, **schema_block()},
        warnings=warnings,
    )


def catalyst_payload(svc: dict[str, Any], weapon_name: str) -> dict[str, Any]:
    result = svc["manifest_query_svc"].get_catalyst_details(weapon_name)
    weapon = result.get("weapon") or {}
    count = result.get("count") or 0
    name = weapon.get("name") or weapon_name
    summary = (
        f"已读取「{name}」的 {count} 个催化剂定义。"
        if count
        else f"「{name}」没有可用的催化剂定义。"
    )
    warnings = [result["note"]] if result.get("note") else []
    warnings.append(
        "本地不读账号的催化剂解锁记录：unlock_state=not_checked 表示没查，不是没解锁。"
    )
    return ok_response(
        summary,
        {"catalyst": result, **schema_block()},
        warnings=warnings,
    )


def perk_description_payload(svc: dict[str, Any], perk_name: str) -> dict[str, Any]:
    perk = svc["manifest_query_svc"].get_perk_description(perk_name)
    # perk 不是武器，没有 weapon 块可挂；社区资料单独给一块，且必须**照实说**
    # 资料可不可用（语料里"腐坏的可选资料不能静默消失"这条横切规则）。
    community = community_enrichment(svc.get("starside_svc"), perk_name, "weapons")
    return ok_response(
        f"已读取 Perk「{perk.get('name') or perk_name}」的说明。",
        {
            "perk": perk,
            "community_references": community,
            **schema_block(),
        },
    )


def missing_weapon_name(intent: str) -> dict[str, Any]:
    return error_response("missing_weapon_name", f"{intent} 需要提供 weapon_name。")

def inventory_type_payload(
    svc: dict[str, Any],
    result: Any,
    type_name: str,
    location: str = "",
) -> dict[str, Any]:
    """`inventory_assistant(intent="type")`：按类型列**持有**的物品。

    边界（与 `weapon.type` 的分工）：这里只请求库存+实例组件，不请求 305/310，
    所以给的是精简身份块 + 位置/光等，**没有 perk 与可换部件**；要看"能换成什么"
    用 `weapon_assistant(intent="type")`。物品不是武器时原样返回，不硬套武器字段。
    """
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    manifest = svc.get("manifest")
    rows: list[dict[str, Any]] = []
    weapons = 0
    for item in payload.get("items") or []:
        definition = (
            manifest.get_item_definition(item.get("item_hash", 0)) if manifest else None
        )
        if not isinstance(definition, dict) or definition.get("itemType") != 3:
            rows.append({"kind": "item", **item})
            continue
        weapons += 1
        row = weapon_payload.lean_identity(
            manifest,
            definition,
            roll_kind=weapon_profile.roll_kind(definition),
            fallback_name=str(item.get("name") or ""),
        )
        row.update({
            "kind": "weapon",
            "instance_id": item.get("item_instance_id", ""),
            "location": item.get("location", ""),
            "power": item.get("power"),
            "is_equipped": item.get("is_equipped", False),
            "bucket_type": item.get("bucket_type", ""),
            "icon_url": row.get("icon_url") or item.get("icon_url", ""),
        })
        rows.append(row)

    label = type_name or "全部"
    scope = f"（{location}）" if location else ""
    return ok_response(
        f"「{label}」{scope}持有 {len(rows)} 件（其中武器 {weapons} 件）；"
        "这里只有身份与位置，要看可换部件请用 weapon_assistant(intent=\"type\")。",
        {
            "result": {
                "query": payload.get("query", type_name),
                "location": location,
                "total": len(rows),
                "returned": len(rows),
                "truncated": False,
                "items": rows,
                "weapon_count": weapons,
            },
            **schema_block(),
        },
    )
