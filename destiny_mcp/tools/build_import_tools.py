"""Build Import MCP tools — import_build_from_image, import_build_from_url, parse_build_from_article.

Rule 1: Thin wrappers. Business logic in BuildImportService.
@handle_tool_error (via _helpers) already catches DestinyMCPError (parent of BuildImportError),
so per-try/except blocks here are redundant. The service logs errors internally.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from ..server import mcp
from ._helpers import get_ctx, handle_tool_error


@mcp.tool()
@handle_tool_error
async def import_build_from_image(
    image_base64: str,
    ctx: Context = None,
) -> dict:
    """从配装截图导入 Build。

    何时使用：用户发了一张 DIM 配装截图、游戏截图、社交媒体截图时。
    何时跳过：用户想手动指定配装参数（用 find_build）。
    何时跳过：用户想导入文章（Phase 2，暂不支持）。

    支持的截图类型：
    - DIM 配装页面截图
    - 游戏内装备界面截图
    - Bilibili/NGA/Reddit 配装截图

    Args:
        image_base64: 截图的 base64 编码（PNG/JPG）。

    Returns:
        {
            "build": { ... },           # CanonicalBuild（全 Hash）
            "validation": {
                "status": "complete",   # complete / partial / failed
                "errors": [],
                "warnings": []
            },
            "draft": { ... },           # BuildDraft（全 Name，调试用）
            "skill_prompt": "..."       # Skill 提示词（用于 VLM 调用）
        }

    注意：
    - Phase 1 仅支持截图导入。
    - 需要配合 VLM（如 Claude Vision）使用 skill_prompt 提取 BuildDraft。
    - 返回的 build 是 CanonicalBuild（全 Hash），可直接传给 find_build 使用。
    """
    svc = get_ctx(ctx)
    result = await svc['build_import_svc'].import_from_image(image_base64)

    return {
        "success": True,
        "build": result["build"].model_dump(),
        "validation": result["validation"].model_dump(),
        "draft": result["draft"].model_dump(),
        "skill_prompt": result["skill_prompt"],
    }


@mcp.tool()
@handle_tool_error
async def import_build_from_url(
    url: str,
    ctx: Context = None,
) -> dict:
    """从配装文章链接导入 Build。

    何时使用：用户发了一个配装文章链接（Bilibili/NGA/Reddit/博客等）。
    何时跳过：用户想手动指定配装参数（用 find_build）。
    何时跳过：用户发的是截图（用 import_build_from_image）。

    流程：
    1. 本工具抓取文章，返回正文和 skill_prompt
    2. 你用 skill_prompt 指导自己解析文章，输出 JSON
    3. 你调用 parse_build_from_article 将 JSON 转为 CanonicalBuild

    Args:
        url: 文章 URL。

    Returns:
        {
            "success": true,
            "article_text": "...",     # 文章正文（截断到 8000 字）
            "skill_prompt": "..."      # 解析提示词
        }
        或
        {
            "success": false,
            "error": "..."
        }

    注意：
    - 返回 article_text 后，你需要用 skill_prompt 解析它，再调用 parse_build_from_article。
    - 不要直接把 article_text 当作最终结果返回给用户。
    """
    svc = get_ctx(ctx)
    result = await svc['build_import_svc'].import_from_url(url)

    return {
        "success": True,
        "article_text": result["article_text"],
        "skill_prompt": result["skill_prompt"],
    }


@mcp.tool()
@handle_tool_error
async def parse_build_from_article(
    llm_response: str,
    ctx: Context = None,
) -> dict:
    """将 LLM 的文章解析结果转为 CanonicalBuild。

    何时使用：你已经用 import_build_from_url 获取了文章，用 skill_prompt 解析后，
    调用本工具将你的 JSON 输出转为系统可用的 CanonicalBuild。

    流程：
    1. import_build_from_url → 拿到 article_text + skill_prompt
    2. 你用 skill_prompt 解析文章 → 输出 JSON
    3. 本工具 → JSON 转 CanonicalBuild → 可传给 find_build

    Args:
        llm_response: 你解析文章后输出的 JSON 字符串（符合 BuildDraft schema）。

    Returns:
        {
            "success": true,
            "build": { ... },           # CanonicalBuild（全 Hash）
            "validation": { ... },      # 校验结果
            "draft": { ... }            # BuildDraft（全 Name，调试用）
        }
        或
        {
            "success": false,
            "error": "..."
        }
    """
    svc = get_ctx(ctx)
    result = svc['build_import_svc'].import_from_article_response(llm_response)

    return {
        "success": True,
        "build": result["build"].model_dump(),
        "validation": result["validation"].model_dump(),
        "draft": result["draft"].model_dump(),
    }
