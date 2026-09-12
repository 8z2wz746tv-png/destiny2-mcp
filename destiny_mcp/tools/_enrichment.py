"""社区资料富集：官方数据查询的"附加参考"。

单独放一个模块的原因：武器/护甲/配装等 intent 分支都要用它，而分支代码正在往外搬，
留在 `assistants.py` 里会形成"分支模块 ← 主模块"的反向依赖。

铁律：**社区数据永远不能让官方数据查询失败**，查不到就返回可识别的空结果。
"""

from __future__ import annotations

from typing import Any

from ..exceptions import DestinyMCPError

EMPTY_ENRICHMENT: dict[str, Any] = {
    "archive_available": False,
    "matched_count": 0,
    "results": [],
}


def community_enrichment(service: Any, query: str, category: str) -> dict:
    """读社区资料；没有服务、没查询词、读取失败都返回空结构而不是抛错。"""
    if service is None or not query.strip():
        return dict(EMPTY_ENRICHMENT)
    try:
        return service.search_knowledge(query, category=category, limit=3)
    except DestinyMCPError as exc:
        return {
            "archive_available": False,
            "matched_count": 0,
            "results": [],
            "error": str(exc),
            "coverage_scope": "community_enrichment_unavailable",
        }


def community_read(
    service: Any,
    *,
    query: str = "",
    category: str = "",
    knowledge_id: str = "",
    section: str = "text",
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """按 id 读详情，否则按关键词搜；两种读法共用一个入口。"""
    if knowledge_id:
        return service.get_knowledge(
            knowledge_id, section=section, limit=limit, offset=offset
        )
    return service.search_knowledge(query, category=category, limit=limit, offset=offset)
