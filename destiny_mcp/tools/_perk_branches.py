"""`weapon_assistant(intent="perk_description")` 的载荷：Perk 说明 + 页面正文社区检索 + Starside 结构化注记。

从 `_weapon_branches.py` 抽出来（那文件贴着 412 上限）：perk 层的东西按域放这里，
与"武器分支"分开，以后加字段也不用去动那个上限。
"""

from __future__ import annotations

from typing import Any

from ..services.starside_notes import perk_note_from_svc
from ._enrichment import community_enrichment
from ._responses import ok_response
from ..services.weapon_payload import schema_block


def perk_description_payload(svc: dict[str, Any], perk_name: str) -> dict[str, Any]:
    perk = svc["manifest_query_svc"].get_perk_description(perk_name)
    # perk 不是武器，没有 weapon 块可挂；社区资料单独给一块，且必须**照实说**
    # 资料可不可用（语料里"腐坏的可选资料不能静默消失"这条横切规则）。
    community = community_enrichment(svc.get("starside_svc"), perk_name, "weapons")
    # 结构化注记（实机细节/效果/属性变化/冷却/来源 + 套装与反查）按 hash 直接查，
    # 与上面的**文本**社区检索分工不同：那边是页面正文，这边是实体字段。
    perk_hash = int((perk or {}).get("hash") or 0)
    starside = perk_note_from_svc(svc, perk_hash)
    return ok_response(
        f"已读取 Perk「{perk.get('name') or perk_name}」的说明。",
        {
            "perk": perk,
            "community_references": community,
            "starside": starside,
            **schema_block(),
        },
    )
