"""Build MCP tools — find_build, analyze_build, equip_build, list_set_bonuses.

Rule 1: Thin wrappers. Business logic in BuildService / SetBonusService.
Output formatting helpers live at module level (allowed by Rule 1).
"""

from __future__ import annotations

from mcp.server.fastmcp import Context
from pydantic import ValidationError

from ..build.models import BuildRequest
from ..build_contracts import ExecutableBuild
from ..exceptions import DestinyMCPError
from ._registry import mcp
from ._farm_target import serialize_farm_target_analysis
from ._helpers import (
    get_ctx,
    handle_tool_error,
    resolve_player_name,
)
from ._responses import (
    confirmation_required_response,
    error_response,
    ok_response,
)


# ── Output formatting helpers (Rule 1: formatting is allowed in tools) ──

_STAT_CN = {
    "weapons": "武器", "health": "生命", "class_stat": "职业",
    "grenade": "手雷", "melee": "近战", "super_stat": "超能",
}


def _mod_recommendation(bonus_stats: dict[str, int]) -> list[str]:
    """Convert bonus_stats into human-readable mod recommendations."""
    recs = []
    for stat, bonus in bonus_stats.items():
        if bonus <= 0:
            continue
        cn = _STAT_CN.get(stat, stat)
        major = bonus // 10
        minor = (bonus % 10) // 5
        artifice = (bonus % 5) // 3
        parts = []
        if major:
            parts.append(f"{cn}模组 ×{major}")
        if minor:
            parts.append(f"{cn}小型模组 ×{minor}")
        if artifice:
            parts.append(f"{cn}精工模组 ×{artifice}")
        if parts:
            recs.append(" + ".join(parts) + f" (=+{bonus})")
    return recs


def _parse_fragment_names(fragment_names: str | None) -> list[str]:
    """Parse comma-separated fragment names into a list."""
    if not fragment_names:
        return []
    return [f.strip() for f in fragment_names.split(",") if f.strip()]


def _build_request(
    *,
    character: str = "",
    exotic_name: str | None = None,
    weapons_target: int | None = None,
    health_target: int | None = None,
    class_target: int | None = None,
    grenade_target: int | None = None,
    melee_target: int | None = None,
    super_target: int | None = None,
    fragment_names: str | None = None,
    include_subclass_fragment: bool = False,
    set_bonus_name: str | None = None,
    set_bonus_count: int | None = None,
    priority_stats: list[str] | None = None,
    priority_stat: str | None = None,
    top_n: int = 5,
) -> BuildRequest:
    """Create a BuildRequest from MCP tool arguments."""
    return BuildRequest(
        character_class=character,
        exotic_name=exotic_name,
        weapons_target=weapons_target,
        health_target=health_target,
        class_target=class_target,
        grenade_target=grenade_target,
        melee_target=melee_target,
        super_target=super_target,
        fragment_names=_parse_fragment_names(fragment_names),
        include_subclass_fragment=include_subclass_fragment,
        set_bonus_name=set_bonus_name,
        set_bonus_count=set_bonus_count,
        priority_stats=priority_stats or [],
        priority_stat=priority_stat,
        top_n=top_n,
    )


def _auth_required_response(tool: str) -> dict:
    """Return a stable auth error for build tools."""
    return error_response(
        "auth_required",
        "请先登录 Bungie，或显式提供 player_name。",
        next_actions=[
            {
                "label": "登录 Bungie 后重试",
                "tool": tool,
            }
        ],
    )


def _serialize_build_results(results) -> dict:
    """Serialize BuildResult objects using the legacy find_build shape."""
    return {
        "count": len(results),
        "builds": [
            {
                "score": r.score,
                "completion_rate": round(r.completion_rate, 2),
                "stats": {
                    "weapons": r.build.weapons,
                    "health": r.build.health,
                    "class": r.build.class_stat,
                    "grenade": r.build.grenade,
                    "melee": r.build.melee,
                    "super": r.build.super_stat,
                    "total": r.build.total_stats,
                },
                "bonus_stats": r.build.bonus_stats,
                "mod_recommendation": _mod_recommendation(r.build.bonus_stats),
                "fragment_details": r.fragment_details if r.fragment_details else None,
                "active_set_bonuses": r.active_set_bonuses if r.active_set_bonuses else None,
                "canonical_build": (
                    r.canonical_build.model_dump(mode="json")
                    if r.canonical_build
                    else None
                ),
                "armor": [
                    {
                        "name": a.name,
                        "item_instance_id": a.item_instance_id,
                        "slot": a.slot,
                        "power": a.power,
                        "set_bonus_name": a.set_bonus_name or None,
                        "icon_url": a.icon_url or None,
                        "stats": {
                            "weapons": a.stats.weapons,
                            "health": a.stats.health,
                            "class": a.stats.class_stat,
                            "grenade": a.stats.grenade,
                            "melee": a.stats.melee,
                            "super": a.stats.super_stat,
                        },
                    }
                    for a in r.build.items
                ],
                "missing_requirements": r.missing_requirements,
            }
            for r in results
        ],
    }


def _serialize_build_analysis(analysis) -> dict | None:
    """Serialize general build analysis without changing its reason contract."""
    if analysis is None:
        return None
    public_farm = serialize_farm_target_analysis(analysis)
    return {
        "reason": analysis.reason,
        "max_possible": analysis.max_possible,
        "suggested_farm": analysis.suggested_farm,
        "farm_options": public_farm["farm_options"],
        "assumptions": analysis.assumptions,
    }


def _request_summary(request: BuildRequest) -> dict:
    """Return non-sensitive request details for AI/front-end rendering."""
    return {
        "character": request.character_class,
        "exotic_name": request.exotic_name,
        "targets": {
            "weapons": request.weapons_target,
            "health": request.health_target,
            "class": request.class_target,
            "grenade": request.grenade_target,
            "melee": request.melee_target,
            "super": request.super_target,
        },
        "fragments": request.fragment_names,
        "include_subclass_fragment": request.include_subclass_fragment,
        "set_bonus_name": request.set_bonus_name,
        "set_bonus_count": request.set_bonus_count,
        "priority_stats": request.priority_stats,
        "priority_stat": request.priority_stat,
        "top_n": request.top_n,
    }


@mcp.tool()
@handle_tool_error
async def find_build(
    player_name: str | None = None,
    character: str = "",
    exotic_name: str | None = None,
    weapons_target: int | None = None,
    health_target: int | None = None,
    class_target: int | None = None,
    grenade_target: int | None = None,
    melee_target: int | None = None,
    super_target: int | None = None,
    fragment_names: str | None = None,
    include_subclass_fragment: bool = False,
    set_bonus_name: str | None = None,
    set_bonus_count: int | None = None,
    priority_stats: list[str] | None = None,
    priority_stat: str | None = None,
    top_n: int = 5,
    ctx: Context = None,
) -> dict:
    """查找最优护甲配装方案（Build Engine）。

    根据属性目标约束，从玩家仓库中搜索满足条件的护甲组合。
    使用 Branch and Bound 算法求解，返回 Top-K 排序结果。

    何时使用：用户说"帮我配一套200生命200手雷的build"、"找一个GM配装"时。
    何时使用：用户想优化某个属性到特定值时。
    何时使用：用户指定了碎片（如"用保护琢面和黎明琢面"）时。
    何时跳过：用户只想看当前护甲（用 get_inventory）。
    何时跳过：用户想了解为什么找不到配装（用 analyze_build）。

    六维属性（Renegades 更新后）：
      weapons — 枪械操控和换弹速度
      health  — 生命值和生存能力
      class   — 职业技能冷却
      grenade — 手雷冷却
      melee   — 近战冷却
      super   — 大招冷却
    上限为 200，超过 100 有额外收益。

    重要规则：
    - 命运2中，护甲5个部位中最多只能有1件异域（金色）护甲。配装引擎会自动处理此约束。
    - 只有显式传 include_subclass_fragment=true 时，才计入当前子职业和碎片属性。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 角色职业（hunter/warlock/titan，或中文：猎人/术士/泰坦）。
                  必填；引擎只使用该职业可穿戴的护甲。
        exotic_name: 金装名称（中英文均可），如 '天穹夜鹰'、'Star-Eater Scales'。
        weapons_target: 武器属性目标（≥该值，0-200）。
        health_target: 生命属性目标。
        class_target: 职业技能属性目标。
        grenade_target: 手雷属性目标。
        melee_target: 近战属性目标。
        super_target: 大招属性目标。
        fragment_names: 指定碎片名称（逗号分隔），如 '保护琢面,黎明琢面'。
                       引擎会查找这些碎片的属性加成并纳入计算。
                       不填不会自动计入当前碎片。
        include_subclass_fragment: 是否计入当前子职业和碎片属性。
        set_bonus_name: 套装名称（中英文均可），如 '兴旺幸存者'、'Thriving Survivor'。
                       只包含该套装的护甲。
        set_bonus_count: 需要的套装件数（2 或 4）。默认: 2。
        priority_stats: 硬目标达标后按列表顺序严格最大化属性。
        priority_stat: dump stat — 满足所有 ≥ 目标后，剩余模组容量全堆该属性。
                       接受：weapons/health/class/grenade/melee/super
                       或中文：武器/生命/职业/手雷/近战/超能。
                       例如用户说"其余全堆近战"，传 "melee"。
        top_n: 返回前 N 个最优方案（默认 5）。

    Examples:
        find_build(player_name="husky#1234", character="hunter", health_target=200, grenade_target=200)
        find_build(player_name="husky#1234", character="hunter", exotic_name="天穹夜鹰", weapons_target=200)
        find_build(player_name="husky#1234", character="hunter", melee_target=150, fragment_names="保护琢面,黎明琢面")
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return _auth_required_response("find_build")

    request = _build_request(
        character=character,
        exotic_name=exotic_name,
        weapons_target=weapons_target,
        health_target=health_target,
        class_target=class_target,
        grenade_target=grenade_target,
        melee_target=melee_target,
        super_target=super_target,
        fragment_names=fragment_names,
        include_subclass_fragment=include_subclass_fragment,
        set_bonus_name=set_bonus_name,
        set_bonus_count=set_bonus_count,
        priority_stats=priority_stats,
        priority_stat=priority_stat,
        top_n=top_n,
    )

    results = await svc['build_svc'].find_build(player_name, request)

    return _serialize_build_results(results)


@mcp.tool()
@handle_tool_error
async def recommend_build(
    player_name: str | None = None,
    character: str = "",
    exotic_name: str | None = None,
    weapons_target: int | None = None,
    health_target: int | None = None,
    class_target: int | None = None,
    grenade_target: int | None = None,
    melee_target: int | None = None,
    super_target: int | None = None,
    fragment_names: str | None = None,
    include_subclass_fragment: bool = False,
    set_bonus_name: str | None = None,
    set_bonus_count: int | None = None,
    priority_stats: list[str] | None = None,
    priority_stat: str | None = None,
    top_n: int = 5,
    ctx: Context = None,
) -> dict:
    """AI 配装推荐入口：查找候选；找不到时自动返回诊断和下一步。

    何时使用：用户用大白话说"帮我配一套..."、"怎么堆到..."、"我这套
    属性能不能达标"时，优先使用这个工具。
    何时跳过：用户明确只想要底层候选列表时，使用 find_build。
    何时跳过：用户已经选好某套并确认穿上时，使用 equip_build。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return _auth_required_response("recommend_build")

    request = _build_request(
        character=character,
        exotic_name=exotic_name,
        weapons_target=weapons_target,
        health_target=health_target,
        class_target=class_target,
        grenade_target=grenade_target,
        melee_target=melee_target,
        super_target=super_target,
        fragment_names=fragment_names,
        include_subclass_fragment=include_subclass_fragment,
        set_bonus_name=set_bonus_name,
        set_bonus_count=set_bonus_count,
        priority_stats=priority_stats,
        priority_stat=priority_stat,
        top_n=top_n,
    )

    try:
        recommendation = await svc['build_svc'].recommend_build(player_name, request)
    except DestinyMCPError as exc:
        return error_response(
            "build_recommendation_failed",
            str(exc),
            next_actions=[
                {
                    "label": "检查参数或 Bungie 登录状态后重试",
                    "tool": "recommend_build",
                }
            ],
        )

    serialized = _serialize_build_results(recommendation.results)
    analysis = _serialize_build_analysis(recommendation.analysis)
    if recommendation.results:
        summary = f"找到 {len(recommendation.results)} 套候选配装。"
        next_actions = [
            {
                "label": "确认后穿上第一套",
                "tool": "equip_build",
                "arguments": {
                    "character": character,
                    "canonical_build": serialized["builds"][0]["canonical_build"],
                },
            }
        ]
    else:
        summary = "没有找到满足条件的配装；已返回当前库存诊断。"
        next_actions = [
            {
                "label": "降低目标属性后重试",
                "tool": "recommend_build",
            },
            {
                "label": "查看背包护甲概况",
                "tool": "summarize_inventory",
                "arguments": {"item_type": "armor"},
            },
        ]

    return ok_response(
        summary,
        {
            "query": _request_summary(request),
            "builds": serialized["builds"],
            "analysis": analysis,
        },
        candidates=[
            {
                "id": f"build_{index}",
                "label": f"第 {index} 套 · score {build['score']}",
                "score": build["score"],
                "stats": build["stats"],
                "canonical_build": build["canonical_build"],
            }
            for index, build in enumerate(serialized["builds"], 1)
        ],
        next_actions=next_actions,
        warnings=[],
    )


@mcp.tool()
@handle_tool_error
async def analyze_build(
    player_name: str | None = None,
    character: str = "",
    exotic_name: str | None = None,
    weapons_target: int | None = None,
    health_target: int | None = None,
    class_target: int | None = None,
    grenade_target: int | None = None,
    melee_target: int | None = None,
    super_target: int | None = None,
    fragment_names: str | None = None,
    include_subclass_fragment: bool = False,
    priority_stats: list[str] | None = None,
    priority_stat: str | None = None,
    ctx: Context = None,
) -> dict:
    """分析为什么找不到满足条件的配装（诊断工具）。

    当 find_build 返回空结果时，用此工具了解原因：
    - 哪些属性目标无法达到
    - 当前库存中各属性的最大可能值
    - 建议刷哪些活动来获取缺失的护甲

    何时使用：find_build 返回 0 个结果后，帮助用户理解原因。
    何时使用：用户问"我的护甲够不够达到100韧性"时。
    何时跳过：用户只想找配装（用 find_build）。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        exotic_name: 金装名称。
        weapons_target: 武器属性目标。
        health_target: 生命属性目标。
        class_target: 职业技能属性目标。
        grenade_target: 手雷属性目标。
        melee_target: 近战属性目标。
        super_target: 大招属性目标。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return _auth_required_response("analyze_build")

    request = _build_request(
        character=character,
        exotic_name=exotic_name,
        weapons_target=weapons_target,
        health_target=health_target,
        class_target=class_target,
        grenade_target=grenade_target,
        melee_target=melee_target,
        super_target=super_target,
        fragment_names=fragment_names,
        include_subclass_fragment=include_subclass_fragment,
        priority_stats=priority_stats,
        priority_stat=priority_stat,
    )

    analysis = await svc['build_svc'].analyze_build(player_name, request)

    return _serialize_build_analysis(analysis)


@mcp.tool()
@handle_tool_error
async def infer_required_armor(
    player_name: str | None = None,
    character: str = "",
    exotic_name: str | None = None,
    weapons_target: int | None = None,
    health_target: int | None = None,
    class_target: int | None = None,
    grenade_target: int | None = None,
    melee_target: int | None = None,
    super_target: int | None = None,
    fragment_names: str | None = None,
    include_subclass_fragment: bool = False,
    set_bonus_name: str | None = None,
    set_bonus_count: int | None = None,
    priority_stats: list[str] | None = None,
    priority_stat: str | None = None,
    replacement_slot: str | None = None,
    baseline: str = "equipped",
    max_replacements: int = 2,
    top_n: int = 5,
    ctx: Context = None,
) -> dict:
    """反推最少一件、最多两件游戏内可刷取的 Armor 3.0 护甲。

    先完整搜索单件替换；max_replacements=2 时，只在单件无解后
    再搜索两件。结果不是库存物品，不能传给 equip_build。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return _auth_required_response("infer_required_armor")
    if baseline not in {"equipped", "inventory"}:
        return error_response(
            "invalid_baseline",
            "baseline 只能是 equipped 或 inventory。",
        )
    if isinstance(max_replacements, bool) or max_replacements not in {1, 2}:
        return error_response(
            "invalid_max_replacements", "max_replacements 只能是 1 或 2。"
        )

    request = _build_request(
        character=character,
        exotic_name=exotic_name,
        weapons_target=weapons_target,
        health_target=health_target,
        class_target=class_target,
        grenade_target=grenade_target,
        melee_target=melee_target,
        super_target=super_target,
        fragment_names=fragment_names,
        include_subclass_fragment=include_subclass_fragment,
        set_bonus_name=set_bonus_name,
        set_bonus_count=set_bonus_count,
        priority_stats=priority_stats,
        priority_stat=priority_stat,
        top_n=top_n,
    )
    analysis = await svc["build_svc"].infer_required_armor(
        player_name,
        request,
        replacement_slot=replacement_slot,
        baseline=baseline,
        max_replacements=max_replacements,
    )
    serialized = serialize_farm_target_analysis(analysis)
    options = serialized["farm_options"]
    plans = serialized["farm_plans"]
    if options:
        summary = f"找到 {len(options)} 个单件合法待刷护甲目标。"
    elif plans:
        summary = f"单件无解；找到 {len(plans)} 个最少替换两件的合法方案。"
    else:
        summary = "在允许的替换件数内，合法五阶护甲仍无法满足当前硬约束。"
    return ok_response(
        summary,
        {
            "query": {
                **_request_summary(request),
                "replacement_slot": replacement_slot,
                "baseline": baseline,
                "max_replacements": max_replacements,
            },
            "farm_target": serialized,
        },
        warnings=serialized["assumptions"],
    )


@mcp.tool()
@handle_tool_error
async def equip_build(
    player_name: str | None = None,
    character: str = "",
    canonical_build: dict | None = None,
    confirmed: bool = False,
    ctx: Context = None,
) -> dict:
    """穿上 find_build 返回并经用户确认的精确配装方案。

    何时使用：用户在 find_build 结果中选定一套方案后，说"穿上第X套"时。
    何时使用：用户确认要用某套配装时。
    何时跳过：用户只想看配装方案（用 find_build）。
    何时跳过：用户想修改某件装备（用 move_item）。

    必须原样传回候选中的 canonical_build。系统会校验库存快照、准确实例、
    逐件模组和子职业配置；库存变化时拒绝执行并要求重新确认。

    Args:
        player_name: Bungie 名称。不填则使用默认玩家。
        character: 穿到哪个角色（hunter/warlock/titan，或中文）。
        canonical_build: find_build 候选返回的完整 canonical_build 对象。
        confirmed: 用户确认后才设为 true；首次调用必须保持 false。
    """
    svc = get_ctx(ctx)
    player_name = resolve_player_name(svc, player_name)
    if not player_name:
        return _auth_required_response("equip_build")

    if not character:
        return {
            "success": False,
            "message": "必须指定 character 参数（hunter/warlock/titan）。",
        }

    if canonical_build is None:
        return error_response(
            "exact_build_required",
            "必须传回 find_build 返回的 canonical_build，不能按 score 重新求解。",
        )
    try:
        exact_build = ExecutableBuild.model_validate(canonical_build)
    except ValidationError as exc:
        return error_response("invalid_canonical_build", str(exc))
    if not confirmed:
        return confirmation_required_response(
            "equip_build",
            {
                "canonical_build": exact_build.model_dump(mode="json"),
                "character": character,
            },
        )

    build_svc = svc['build_svc']
    return await build_svc.equip_build(player_name, exact_build, character)


@mcp.tool()
@handle_tool_error
async def list_set_bonuses(ctx: Context = None) -> dict:
    """列出所有可用的护甲套装加成。

    返回所有套装的名称、件数要求和 perk 效果。
    用于查看有哪些套装可以选择，以及每个套装的加成效果。

    何时使用：用户问"有哪些套装加成"、"护甲套装效果是什么"时。
    何时使用：用户想选择套装但不知道有哪些选项时。
    何时跳过：用户已经知道套装名称（直接用 find_build 的 set_bonus_name 参数）。
    """
    svc = get_ctx(ctx)
    return svc['set_bonus_svc'].list_all_set_bonuses()
