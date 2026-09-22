"""站点标记解析器与响应成形块的守门（只用仓库里已提交的数据）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from destiny_mcp.services import starside_markup as markup
from destiny_mcp.services.starside_entities import StarsideEntities
from destiny_mcp.services.starside_notes import weapon_note

ENTITIES = Path(__file__).parents[1] / "data" / "starside" / "entities"


class _Manifest:
    """只认几个名字的替身（真机查名在语料脚本里验）。"""

    NAMES = {"槽化枪管": "槽化枪管", "冲击支撑": "冲击支撑", "猛攻": "猛攻"}

    def search(self, name: str, limit: int = 1):
        return [{"itemHash": 1}] if name in self.NAMES else []

    def get_item_definition(self, item_hash: int):
        return {"displayProperties": {"name": "槽化枪管"}}


@pytest.fixture(scope="module")
def payloads() -> tuple[dict, dict]:
    return (
        json.loads((ENTITIES / "items.json").read_text(encoding="utf-8")),
        json.loads((ENTITIES / "perks.json").read_text(encoding="utf-8")),
    )


def test_token_table_covers_every_token_in_the_data(payloads) -> None:
    """数据里出现过的 token 必须都在表里 —— 冒出新的就红，逼着显式处理。"""
    unknown: dict[str, int] = {}
    for payload in payloads:
        for entry in (payload.get("items") or payload.get("perks") or {}).values():
            for text in json.dumps(entry, ensure_ascii=False).split('"'):
                for token in markup.unknown_tokens(text):
                    unknown[token] = unknown.get(token, 0) + 1
    assert not unknown, f"表里没有的 token：{sorted(unknown)}（要么加进 TOKENS，要么明确忽略）"


def test_render_rewrites_tokens_without_losing_unknown_ones() -> None:
    """认识的翻好；不认识的**原样留着**并把记号报出来（不许静默丢）。"""
    text = "{perk|槽化枪管}\\\\{perk|B 计划}｜{el-void|虚空}｜{unsure|弹药量?}｜{pvp|[20]}｜{num|13514\\\\11384}"
    rendered = markup.render(text, names=lambda n: _Manifest.NAMES.get(n))
    assert "／" in rendered and "B 计划" in rendered, "多选要分开、查不到的名字要保留（去掉记号壳）"
    assert "{perk|" not in rendered, "记号壳不许进响应"
    assert "（作者标注：不确定）" in rendered, "作者标的不确定必须保留"
    assert "（PvP）[20]" in rendered, "PvP 数值要标出来，不能混进 PvE"
    assert "13514" in rendered and "11384" not in rendered, "多值取第一个（站点写法）"
    assert markup.unknown_tokens("{weird|x}") == ("weird",)
    assert markup.unknown_tokens(rendered) == (), "认识的不该被报成不认识"


def test_number_unwraps_the_site_spellings() -> None:
    """语料挖出来的三种写法 + `∞`。"""
    assert markup.number("{num|2576}") == ("2576", True)
    assert markup.number("{num|13514\\\\11384}") == ("13514", True)
    assert markup.number("{num|7087\\\\(级联点)}") == ("7087", True)
    assert markup.number("∞") == ("∞", True)
    assert markup.number("很快") == ("很快", False)


def test_weapon_note_keeps_scales_apart_and_names_the_gaps() -> None:
    """两套评级分开、带出处、缺什么写什么；文本里不许残留已知 token。"""
    entity = StarsideEntities()
    manifest = _Manifest()
    items = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))["items"]
    aegis_key = next(k for k, v in items.items() if "Aegis" in ((v.get("zh") or {}).get("site_authors") or {}))

    note = weapon_note(entity, manifest, int(aegis_key))
    assert note["available"] is True
    attribution = note["attribution"]
    assert attribution["source"] == "starside.work" and attribution["unofficial"] is True
    assert attribution["snapshot_at"] and attribution["authors"]
    block = note["authors"]["Aegis"]
    assert "Aegis 总榜" in block["scale"] and block["tier"] in set("SABCDEF")
    assert block["perk1"]["options"], "3 号位推荐要成列表"
    if "LGpig" not in note["authors"]:
        assert any("LGpig" in gap for gap in note["gaps"]), "没评过要说清是哪位作者没评"
    known = " ".join(json.dumps(note, ensure_ascii=False).split())
    for token in ("{perk|", "{el-", "{unsure|", "{pvp|", "{num|", "{src|"):
        assert token not in known, f"响应里不许残留站点标记：{token}"

    missing = weapon_note(entity, manifest, 4294967295)
    assert missing["available"] is False and "没有这件装备的社区记录" in missing["reason"]


def test_analyze_payload_carries_the_community_block(monkeypatch) -> None:
    """工具层接线：`analyze` 的响应里必须带 `starside` 块（有数据给内容、没数据给原因）。

    这里把 `_local`/`_attach_local` 打成空实现，只验接线本身；真机渲染由
    `scripts/run_corpus_starside_entities.py` 负责。
    """
    from destiny_mcp.tools import _weapon_branches as branches

    items = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))["items"]
    aegis_key = next(k for k, v in items.items() if "Aegis" in ((v.get("zh") or {}).get("site_authors") or {}))
    monkeypatch.setattr(branches, "_local", lambda svc, name: {})
    monkeypatch.setattr(branches, "_attach_local", lambda *a, **k: [])

    def payload(weapon_hash: int) -> dict:
        result = {
            "weapon": {"hash": weapon_hash, "name": "测试武器"},
            "sockets": {}, "stats": {}, "god_roll": {}, "inventory": {},
            "inventory_status": {}, "summary": "x", "next_actions": [], "warnings": [],
        }
        svc = {"manifest": _Manifest(), "starside_entities_svc": StarsideEntities()}
        return branches.analyze_payload(svc, result, "测试武器")

    hit = payload(int(aegis_key))["data"]["starside"]
    assert hit["available"] is True and hit["authors"]["Aegis"]["tier"]
    assert hit["attribution"]["source"] == "starside.work" and hit["attribution"]["unofficial"] is True

    miss = payload(4294967295)["data"]["starside"]
    assert miss["available"] is False and "没有这件装备的社区记录" in miss["reason"]
