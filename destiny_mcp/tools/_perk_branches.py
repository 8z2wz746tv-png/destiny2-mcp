"""`weapon_assistant(intent="perk_description")` 的载荷：Perk 说明 + 页面正文社区检索 + Starside 结构化注记。

从 `_weapon_branches.py` 抽出来（那文件贴着 412 上限）：perk 层的东西按域放这里，
与"武器分支"分开，以后加字段也不用去动那个上限。
"""

from __future__ import annotations

from typing import Any

from ..services.starside_notes import perk_note_for_plug_from_svc
from ._enrichment import community_enrichment
from ._responses import ok_response
from ..services.weapon_payload import schema_block


def perk_description_payload(svc: dict[str, Any], perk_name: str) -> dict[str, Any]:
    perk = svc["manifest_query_svc"].get_perk_description(perk_name)
    if not perk:
        # 套装 perk（集体之力这类）不在"plug perk"那套查找里，我们 Manifest 的名字索引也没有它
        # —— 按名字回退到归档（真机调用测试挖出来的缺口）。**注**：回退路径目前只在裸进程里
        # 验通过，工具上下文里还没打通（见 STARSIDE_ENTITY_PLAN 末节"仍未解决"），
        # 所以这里不许假设一定有命中，也不许崩。
        try:
            hits = svc["starside_entities_svc"].perk_by_name(perk_name)
        except Exception:  # 回退失败不该把整次调用打挂
            hits = ()
        if hits:
            perk = {"name": perk_name, "hash": int(hits[0]), "fallback": "starside_entity_name"}
    # perk 不是武器，没有 weapon 块可挂；社区资料单独给一块，且必须**照实说**
    # 资料可不可用（语料里"腐坏的可选资料不能静默消失"这条横切规则）。
    community = community_enrichment(svc.get("starside_svc"), perk_name, "weapons")
    # 结构化注记（实机细节/效果/属性变化/冷却/来源 + 套装与反查）按 hash 直接查，
    # 与上面的**文本**社区检索分工不同：那边是页面正文，这边是实体字段。
    perk_hash = int((perk or {}).get("hash") or 0)
    # 载荷可能给 plug 物品 hash 或 sandbox hash：走单一出处（沙盒查不到就退回原 hash）
    starside = perk_note_for_plug_from_svc(svc, perk_hash)
    return ok_response(
        f"已读取 Perk「{(perk or {}).get('name') or perk_name}」的说明。",
        {
            "perk": perk,
            "community_references": community,
            "starside": starside,
            **schema_block(),
        },
    )
