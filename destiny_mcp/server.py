"""Destiny MCP Server — 提供 Destiny 2 物品管理的 MCP 工具。

默认工具面使用聚合模式，避免 Agent 同时看到几十个低层工具：
  normal: 8 个 assistant 聚合工具，推荐给普通个人部署
  expert: 聚合工具 + 常用只读低层工具，适合排查查询问题
  full: 聚合工具 + 全部历史低层工具，适合兼容旧提示词和调试

通过环境变量 DESTINY_MCP_TOOL_PROFILE 切换，默认 normal。

架构 (Rule 1): server.py 只做生命周期管理 + 工具注册。不含业务逻辑。
工具定义在 tools/ 子模块中，每个领域一个文件。

这是纯粹的个人版，不含多用户认证逻辑。
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .audit import AuditLogger
from .bungie_client import BungieClient
from .config import resolve_resource_dir
from .logging_config import get_logger, setup_logging
from .manifest import ManifestManager
from .player_resolver import PlayerResolver
from .services.build_service import BuildService
from .services.build_import_service import BuildImportService
from .services.collection_service import CollectionService
from .services.inventory_analysis_service import InventoryAnalysisService
from .services.inventory_service import InventoryService
from .services.loadout_service import LoadoutService
from .services.player_service import PlayerService
from .services.subclass_service import SubclassService
from .services.transfer_service import TransferService
from .services.vendor_service import VendorService
from .services.perk_service import PerkService
from .services.weapon_analysis_service import WeaponAnalysisService
from .services.weapon_compare_service import WeaponCompareService
from .services.weapon_detail_service import WeaponDetailService
from .services.weapon_roll_filter_service import WeaponRollFilterService
from .services.weapon_service import WeaponService
from .services.wishlist_service import WishListService
from .services.weekly_service import WeeklyService
from .services.weekly_analysis_service import WeeklyAnalysisService
from .services.profile_cache import ProfileCache
from .services.activity_service import ActivityService
from .services.manifest_query_service import ManifestQueryService
from .services.fragment_service import FragmentService
from .services.artifact_service import ArtifactService
from .services.set_bonus_service import SetBonusService
from .wishlist_data import ensure_wishlist_data

# ── Logging ──────────────────────────────────────────────────────────

setup_logging()
logger = get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Application lifecycle: init and cleanup.

    纯个人版：直接创建所有服务实例。
    """
    logger.info("Starting Destiny MCP server (standalone mode)...")
    manifest = ManifestManager()

    # Try to load manifest from disk (pre-uploaded to /data/manifest/)
    try:
        await manifest.ensure_loaded(BungieClient())
    except Exception as e:
        logger.warning("Manifest load from disk failed: %s", e)

    wishlist_svc = WishListService(ensure_wishlist_data())

    # Single-user BungieClient — reads token from BUNGIE_API_KEY / .env
    bungie = BungieClient()
    try:
        await bungie.start()
    except Exception as e:
        logger.warning("BungieClient start failed: %s", e)

    resolver = PlayerResolver(bungie, manifest)
    profile_cache = ProfileCache(resolver, manifest)
    player_svc = PlayerService(bungie, manifest, resolver)
    inventory_svc = InventoryService(bungie, manifest, resolver)
    inventory_analysis_svc = InventoryAnalysisService(manifest, resolver)
    transfer_svc = TransferService(bungie, manifest, resolver)
    subclass_svc = SubclassService(bungie, manifest, resolver)
    perk_svc = PerkService(manifest, wishlist_svc)
    weapon_compare_svc = WeaponCompareService(manifest, resolver, perk_svc, wishlist_svc, profile_cache)
    weapon_detail_svc = WeaponDetailService(manifest, resolver)
    weapon_roll_filter_svc = WeaponRollFilterService(manifest)
    manifest_query_svc = ManifestQueryService(manifest)
    weapon_analysis_svc = WeaponAnalysisService(
        perk_svc, weapon_compare_svc, manifest_query_svc
    )
    weapon_svc = WeaponService(bungie, manifest, resolver, wishlist_svc, profile_cache)
    build_svc = BuildService(bungie, manifest, resolver)
    vendor_svc = VendorService(bungie, manifest, resolver, perk_svc)
    weekly_svc = WeeklyService(bungie, manifest)
    weekly_analysis_svc = WeeklyAnalysisService(weekly_svc)
    loadout_svc = LoadoutService(bungie, manifest, resolver)
    build_import_svc = BuildImportService(manifest)
    activity_svc = ActivityService(bungie, manifest, resolver)
    fragment_svc = FragmentService(manifest)
    artifact_svc = ArtifactService(bungie, manifest, resolver)
    set_bonus_svc = SetBonusService(manifest)
    collection_svc = CollectionService(bungie, manifest, resolver)

    logger.info("Destiny MCP server ready (standalone)")
    try:
        yield {
            "manifest": manifest,
            "bungie": bungie,
            "resolver": resolver,
            "profile_cache": profile_cache,
            "player_svc": player_svc,
            "inventory_svc": inventory_svc,
            "inventory_analysis_svc": inventory_analysis_svc,
            "transfer_svc": transfer_svc,
            "subclass_svc": subclass_svc,
            "perk_svc": perk_svc,
            "weapon_compare_svc": weapon_compare_svc,
            "weapon_detail_svc": weapon_detail_svc,
            "weapon_roll_filter_svc": weapon_roll_filter_svc,
            "weapon_analysis_svc": weapon_analysis_svc,
            "weapon_svc": weapon_svc,
            "build_svc": build_svc,
            "vendor_svc": vendor_svc,
            "weekly_svc": weekly_svc,
            "weekly_analysis_svc": weekly_analysis_svc,
            "loadout_svc": loadout_svc,
            "build_import_svc": build_import_svc,
            "activity_svc": activity_svc,
            "manifest_query_svc": manifest_query_svc,
            "fragment_svc": fragment_svc,
            "artifact_svc": artifact_svc,
            "set_bonus_svc": set_bonus_svc,
            "collection_svc": collection_svc,
        }
    finally:
        logger.info("Shutting down...")
        await profile_cache.stop_refresh()
        await loadout_svc.stop_refresh()
        manifest.close()
        await bungie.close()
        logger.info("Destiny MCP server stopped")


mcp = FastMCP(
    "Destiny MCP",
    json_response=True,
    lifespan=app_lifespan,
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8000")),
)


# ── Health endpoint ──────────────────────────────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Lightweight readiness endpoint for Docker/Nginx/server checks."""
    from starlette.responses import JSONResponse

    return JSONResponse({
        "status": "ok",
        "service": "destiny-mcp",
        "transport": os.environ.get("MCP_TRANSPORT", "stdio"),
        "tool_profile": os.environ.get("DESTINY_MCP_TOOL_PROFILE", "normal"),
        "mode": "standalone",
    })


# ── Audit logging ───────────────────────────────────────────────────

_audit = AuditLogger()
_original_call_tool = mcp.call_tool


async def _audited_call_tool(name: str, arguments: dict):
    """Wrap mcp.call_tool with audit logging."""
    start = time.time()
    try:
        result = await _original_call_tool(name, arguments)
        _audit.log(name, arguments, result, (time.time() - start) * 1000)
        return result
    except Exception as e:
        _audit.log(name, arguments, None, (time.time() - start) * 1000, str(e))
        raise


mcp.call_tool = _audited_call_tool


# ── Register prompts ─────────────────────────────────────────────────

_PROMPT_DIR = resolve_resource_dir("prompts")


@mcp.prompt()
def destiny_agent_prompt() -> str:
    """Destiny Agent 系统提示词 — 游戏规则、工具使用策略、交互规范。

    MCP 客户端启动时应读取此 prompt 作为 Agent 的行为指引。
    """
    prompt_path = _PROMPT_DIR / "system_prompt.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    logger.warning("system_prompt.md not found at %s", prompt_path)
    return ""


@mcp.prompt()
def weapon_perk_lookup(weapon_name: str) -> str:
    """武器 Perk 查询引导 — 帮助查找和对比武器 Perk。

    Args:
        weapon_name: 武器名称（中文或英文）。
    """
    return (
        f"请帮我查询武器「{weapon_name}」的 Perk 信息。\n\n"
        "步骤：\n"
        f"1. 用 weapon_assistant(intent='perk_pool') 查询「{weapon_name}」的 perk 池\n"
        f"2. 用 weapon_assistant(intent='compare') 对比我背包中所有该武器的副本\n"
        "3. 告诉我哪把 perk 最好，推荐什么搭配\n\n"
        "如果我知道 god roll，可以进一步用 wishlist 相关功能标注。"
    )


@mcp.prompt()
def player_lookup(player_name: str) -> str:
    """玩家查找引导 — 帮助定位和查看玩家信息。

    Args:
        player_name: 玩家名称（可以不完整）。
    """
    return (
        f"请帮我查找玩家「{player_name}」的信息。\n\n"
        "步骤：\n"
        "1. 如果名称不完整（没有 #数字ID），先用 player_assistant(intent='find') 模糊搜索\n"
        "2. 确认完整名称后，用 player_assistant(intent='search') 获取 membership_id\n"
        "3. 用 player_assistant(intent='profile') 查看角色信息\n"
        "4. 用 inventory_assistant(intent='summary') 查看背包概况\n\n"
        "注意：如果搜索不到，可能需要用户提供完整的 Bungie 名称（如 'husky#1234'）。"
    )


@mcp.prompt()
def build_optimizer() -> str:
    """配装优化引导 — 帮助找到最优护甲组合。

    适用于用户想要优化属性（韧性、纪律等）时。
    """
    return (
        "请帮我优化配装。\n\n"
        "步骤：\n"
        "1. 确认用户想要的目标属性值（如 100 韧性、100 纪律）\n"
        "2. 确认是否需要指定金装（exotic armor）\n"
        "3. 确认目标角色（猎人/术士/泰坦）\n"
        "4. 优先用 build_assistant(intent='recommend') 查找最优配装方案\n"
        "5. 展示 Top 5 方案，包含属性分布和推荐模组\n"
        "6. 用户选定并确认后，把候选的 canonical_build 原样传给 "
        "build_assistant(intent='equip_build', confirmed=true)\n\n"
        "提示：用户明确只要候选列表时才用 build_assistant(intent='find')；"
        "无解时不得自动降低硬目标。"
    )


@mcp.prompt()
def global_tool_strategy() -> str:
    """全局工具调用策略 — 工具路由、参数推断、错误处理、展示格式。

    MCP 客户端启动时应读取此 prompt 作为 Agent 的工具使用指引。
    """
    strategy_path = _PROMPT_DIR.parent / "skills" / "global_tool_strategy.md"
    if strategy_path.exists():
        return strategy_path.read_text(encoding="utf-8")
    logger.warning("global_tool_strategy.md not found at %s", strategy_path)
    return ""


@mcp.prompt()
def build_diagnosis() -> str:
    """配装失败诊断 — 当 find_build 失败时，系统性分析原因并提供解决方案。

    适用于用户问"为什么找不到配装"、"配装失败"时。
    """
    skill_path = _PROMPT_DIR.parent / "skills" / "build_diagnosis_skill.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    logger.warning("build_diagnosis_skill.md not found at %s", skill_path)
    return ""


@mcp.prompt()
def weapon_comparison() -> str:
    """武器对比分析 — 对比同一武器的不同副本，分析 perk 差异，推荐最优选择。

    适用于用户问"我有两把千语哪个好"、"对比我的武器"时。
    """
    skill_path = _PROMPT_DIR.parent / "skills" / "weapon_comparison_skill.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    logger.warning("weapon_comparison_skill.md not found at %s", skill_path)
    return ""


@mcp.prompt()
def vendor_shopping() -> str:
    """商人购物助手 — 分析商人库存，推荐值得购买的物品（god roll 武器、稀缺模组、稀有装备）。

    适用于用户问"枪匠今天有什么好东西"、"商人卖什么"时。
    """
    skill_path = _PROMPT_DIR.parent / "skills" / "vendor_shopping_skill.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    logger.warning("vendor_shopping_skill.md not found at %s", skill_path)
    return ""


@mcp.prompt()
def mod_recommendation() -> str:
    """模组推荐 — 根据配装目标推荐合适的模组组合，并协助安装。

    适用于用户问"配什么模组"、"帮我装模组"时。
    """
    skill_path = _PROMPT_DIR.parent / "skills" / "mod_recommendation_skill.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    logger.warning("mod_recommendation_skill.md not found at %s", skill_path)
    return ""


@mcp.prompt()
def activity_review() -> str:
    """战绩回顾引导 — 帮助查看和分析游戏表现。

    适用于用户想看近期战绩、突袭记录、PvP 表现等。
    """
    return (
        "请帮我查看近期战绩。\n\n"
        "步骤：\n"
        "1. 确认用户想看的活动类型（突袭/熔炉/日落/试炼/地牢等）\n"
        "2. 确认时间范围或数量（最近 10 场？最近的突袭？）\n"
        "3. 用 activity_assistant(intent='history') 查询活动列表\n"
        "4. 如果用户想看某场详细数据，用 activity_assistant(intent='pgcr') 查询\n"
        "5. 用 activity_assistant(intent='stats') 查看生涯总览\n\n"
        "分析维度：完成率、K/D 比、击杀效率、游戏时长。"
    )


# ── Register tools ───────────────────────────────────────────────────
# Import tool modules to trigger @mcp.tool() registration.
# Must be AFTER mcp is created. Circular import is safe because
# server.py's module-level code (mcp) runs before these imports.
#
# IMPORTANT: When running as `python -m destiny_mcp.server`, Python loads
# this file as both __main__ and destiny_mcp.server (two module objects).
# Tool modules do `from ..server import mcp` which creates a SECOND mcp
# instance with 0 tools. We fix this by ensuring sys.modules has a single
# module object before tool imports.

if __name__ == "__main__" and "destiny_mcp.server" not in sys.modules:
    sys.modules["destiny_mcp.server"] = sys.modules["__main__"]

_NORMAL_TOOL_MODULES = (
    "assistants",
)

_EXPERT_TOOL_MODULES = _NORMAL_TOOL_MODULES + (
    "player_tools",
    "inventory_tools",
    "item_tools",
    "fragment_tools",
    "set_tools",
    "collection_tools",
    "weapon_tools",
    "vendor_tools",
    "build_import_tools",
    "activity_tools",
)

_FULL_TOOL_MODULES = _NORMAL_TOOL_MODULES + (
    "player_tools",
    "inventory_tools",
    "item_tools",
    "fragment_tools",
    "set_tools",
    "artifact_tools",
    "collection_tools",
    "transfer_tools",
    "subclass_tools",
    "weapon_tools",
    "build_tools",
    "vendor_tools",
    "loadout_tools",
    "build_import_tools",
    "api_tools",
    "activity_tools",
)


def _tool_profile() -> str:
    profile = os.environ.get("DESTINY_MCP_TOOL_PROFILE", "normal").strip().lower()
    if profile in {"normal", "expert", "full"}:
        return profile
    logger.warning("Unknown DESTINY_MCP_TOOL_PROFILE=%r; falling back to normal", profile)
    return "normal"


def _register_tool_modules() -> None:
    profile = _tool_profile()
    modules = {
        "normal": _NORMAL_TOOL_MODULES,
        "expert": _EXPERT_TOOL_MODULES,
        "full": _FULL_TOOL_MODULES,
    }[profile]
    package = __package__ or "destiny_mcp"

    for module_name in modules:
        importlib.import_module(f"{package}.tools.{module_name}")

    logger.info(
        "Registered Destiny MCP tool profile=%s modules=%s",
        profile,
        ", ".join(modules),
    )


_register_tool_modules()


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════


def main():
    """Entry point for `destiny-mcp` CLI command and `python -m`."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
