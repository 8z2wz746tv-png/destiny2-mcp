"""`weapon_assistant(intent="perk_description")` 的载荷：Perk 说明 + 页面正文社区检索 + Starside 结构化注记。

从 `_weapon_branches.py` 抽出来（那文件贴着 412 上限）：perk 层的东西按域放这里，
与"武器分支"分开，以后加字段也不用去动那个上限。
"""

from __future__ import annotations

from typing import Any

from ..services.starside_notes import perk_note_for_plug_from_svc
from ._enrichment import community_enrichment
from ._responses import ok_response
from ..logging_config import get_logger
from ..services.weapon_payload import schema_block


logger = get_logger(__name__)


def perk_description_payload(svc: dict[str, Any], perk_name: str) -> dict[str, Any]:
    try:
        perk = svc["manifest_query_svc"].get_perk_description(perk_name)
    except Exception as exc:
        logger.warning("perk_description：Manifest 查 %r 失败，回退到归档名字：%s", perk_name, exc)
        # **查不到是抛异常（ManifestError），不是返回空** —— 真机调用测试才发现：
        # 套装 perk（集体之力这类）就落在这条路上，不接住的话整次调用直接报 manifest_error，
        # 下面那段"按归档名字回退"永远跑不到。
        perk = None
    # 结构化注记（实机细节/效果/属性变化/冷却/来源 + 套装与反查）按 hash 直接查。
    # 坑：载荷可能给 plug 物品 hash，而注记在 sandbox perk 空间；套装 perk（集体之力这类）
    # 更是连我们 Manifest 的名字索引里都没有 —— 所以"查不到就按归档的名字再来一次"，两步都留着。
    perk_hash = int((perk or {}).get("hash") or 0)
    starside = perk_note_for_plug_from_svc(svc, perk_hash)
    if not (starside or {}).get("available"):
        hits = ()
        entity = svc.get("starside_entities_svc") if hasattr(svc, "get") else None
        if entity is not None:
            try:
                hits = entity.perk_by_name(perk_name)
            except Exception as exc:  # 回退失败不该把整次调用打挂，但要留痕
                logger.warning("perk_description：按归档名字回退 %r 失败：%s", perk_name, exc)
                hits = ()
        if hits:
            retry = perk_note_for_plug_from_svc(svc, int(hits[0]))
            if (retry or {}).get("available"):
                starside = retry
                perk = {**(perk or {}), "name": (perk or {}).get("name") or perk_name,
                        "hash": int(hits[0]), "fallback": "starside_entity_name"}
    # perk 不是武器，没有 weapon 块可挂；社区资料单独给一块，且必须**照实说**
    # 资料可不可用（语料里"腐坏的可选资料不能静默消失"这条横切规则）。
    community = community_enrichment(svc.get("starside_svc"), perk_name, "weapons")
    # **不是 perk 就点破**（盲测 2026-10-06）：用户问「亡者复仇」这个 perk，实际它是「亡者传说」的
    # 一件**武器皮肤**（类别 `*_skins`）；老话术照样回"已读取 Perk「亡者复仇」的说明"，
    # 用户会以为拿到了 perk 效果。皮肤/外观/着色器一律照实说。
    label = (perk or {}).get("name") or perk_name
    category = str((perk or {}).get("plug_category") or "")
    cosmetic = any(word in category for word in ("skin", "ornament", "shader"))
    summary = (
        f"「{label}」**不是一个 perk**，是武器的外观（类别 `{category}`）—— 下面是它的说明。"
        if cosmetic
        else f"已读取 Perk「{label}」的说明。"
    )
    return ok_response(
        summary,
        {
            "perk": perk,
            "community_references": community,
            "starside": starside,
            **schema_block(),
        },
    )
