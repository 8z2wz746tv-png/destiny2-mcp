"""站点标记解析器与响应成形块的守门（只用仓库里已提交的数据）。"""

from __future__ import annotations

import json
import re
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
            "weapon": {"item_hash": weapon_hash, "name": "测试武器"},  # 键名以真实载荷为准
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


def test_perk_note_renders_annotations_and_reverse_index() -> None:
    """perk 层：注记要渲染、套装与反查要给名字、缺数据要有原因。"""
    from destiny_mcp.services.starside_notes import perk_note

    entity = StarsideEntities()
    perks = json.loads((ENTITIES / "perks.json").read_text(encoding="utf-8"))["perks"]
    manifest = _Manifest()

    annotated = next(k for k, v in perks.items() if (v.get("zh") or {}).get("效果"))
    block = perk_note(entity, manifest, int(annotated))
    assert block["available"] is True
    assert block["annotations"]["效果"], "效果文本要渲染出来"
    assert block["attribution"]["unofficial"] is True
    blob = json.dumps(block, ensure_ascii=False)
    for token in ("{perk|", "{num|", "{el-", "{note|", "{unsure|"):
        assert token not in blob, f"perk 块里不许残留站点标记：{token}"

    with_index = next(k for k, v in perks.items() if v.get("onItems"))
    assert perk_note(entity, manifest, int(with_index))["on_items"]["count"] > 0

    missing = perk_note(entity, manifest, 4294967295)
    assert missing["available"] is False and "没有这颗 perk" in missing["reason"]


def test_fragment_and_class_item_notes_render_without_markup() -> None:
    """P5 两条接线：碎片注记（属性变化/冷却）与异域职业物品双栏配对，都不许残留站点标记。"""
    from destiny_mcp.services.starside_notes import class_item_pairs, fragment_note

    entity = StarsideEntities()
    manifest = _Manifest()
    perks = json.loads((ENTITIES / "perks.json").read_text(encoding="utf-8"))["perks"]
    items = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))["items"]

    frag = next(k for k, v in perks.items() if (v.get("zh") or {}).get("属性变化"))
    note = fragment_note(entity, manifest, "测试碎片", int(frag))
    assert note["available"] is True and note["stat_changes"], "属性变化要给出来"

    pair_item = next(k for k, v in items.items() if v.get("site_perkColumns"))
    pairs = class_item_pairs(entity, manifest, int(pair_item))
    assert pairs["available"] is True and pairs["columns"] >= 1 and pairs["perks"]

    blob = json.dumps({"frag": note, "pairs": pairs}, ensure_ascii=False)
    for token in ("{perk|", "{num|", "{el-", "{unsure|", "{cd|", "{spirit|", "{slot|"):
        assert token not in blob, f"不许残留站点标记：{token}"

    missing = class_item_pairs(entity, manifest, 4294967295)
    assert missing["available"] is False and "没有异域职业物品的双栏数据" in missing["reason"]


def test_perk_description_falls_back_when_the_manifest_lookup_raises() -> None:
    """套装 perk 不在我们 Manifest 的名字索引里，`get_perk_description` 会**抛异常**而不是返回空。

    真机调用测试抓到的回归：不接住这个异常，整次 `perk_description` 直接 `manifest_error`，
    后面"按归档名字回退"永远跑不到 —— 而集体之力正是用户那套配装的套装效果。
    """
    from destiny_mcp.tools import _perk_branches as perk_branches

    class _RaisingQuery:
        def get_perk_description(self, name: str):
            raise RuntimeError("找不到 perk：集体之力")

    svc = {
        "manifest_query_svc": _RaisingQuery(),
        "manifest": _Manifest(),
        "starside_entities_svc": StarsideEntities(),
        "starside_svc": None,
    }
    response = perk_branches.perk_description_payload(svc, "集体之力")

    assert response["ok"] is True, response
    block = response["data"]["starside"]
    assert block["available"] is True, block.get("reason")
    assert block["annotations"] or block["author_notes"], "回退命中就该有注记"
    assert response["data"]["perk"]["fallback"] == "starside_entity_name"


def test_frame_block_uses_snake_case_and_translates_sentinels() -> None:
    """帧表来自站点：键名必须 snake_case（信封规则），哨兵值 `INF` 要翻成 `∞`。

    全量语料抓到的违规：`frame.rows[].ammoType` / `itemSubType` 是 camelCase；
    同时 `boss_total`/`minor_total` 的 `"INF"` 直接露给玩家不好看。
    """
    import re as _re

    entity = StarsideEntities()
    manifest = _Manifest()
    items = json.loads((ENTITIES / "items.json").read_text(encoding="utf-8"))["items"]
    weapon = next(
        k for k, v in items.items()
        if (v.get("derived") or {}).get("archetype") and entity.frame_stats_for_weapon(k)
    )
    note = weapon_note(entity, manifest, int(weapon))
    frame = note["frame"]
    keys = set(frame["headline"]) | {k for row in frame["rows"] for k in row}
    bad = {k for k in keys if _re.search(r"[A-Z]", k)}
    assert not bad, f"帧表键名不是 snake_case：{sorted(bad)}"
    blob = json.dumps(frame, ensure_ascii=False)
    assert '"INF"' not in blob, "哨兵值必须翻译成 ∞"


def _strings(node: object):
    """递归取出一个 JSON 结构里的所有字符串（只用于遍历语料，不做判断）。"""
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


_ICON_RE = re.compile(r"!\[\]\(([^)]*)\)")


# ── 站点文本里的图标：保留成归档里的**相对路径** ──────────────────────────
# 这一组只用仓库里已提交的数据（`data/starside/index.json` 与 `entities/perks.json` 都在 git 里），
# `assets/` 本身不进 git —— 所以这里断言的是"路径指向归档里的哪一项"，不 stat 磁盘。

def test_site_icon_markup_becomes_an_archive_relative_path() -> None:
    """`![](icons/<hash>.webp)` 不再被抹掉，而是补成 `assets/<主题>/icons/<hash>.webp`。

    为什么不给 `https://starside.work/...`：那是第三方站点热链，归档的
    `redistribution_license` 是 `not_established`（见 `docs/community/COMMUNITY_DATA_NOTICE.md`）；
    而图标文件本来就在归档里 —— 给相对路径两头都占。判据与理由在 `services/starside_icons.py`。
    """
    rendered = markup.render("{slot|![](icons/cf6c8a6a75.webp)}")

    assert rendered.startswith("![](assets/"), rendered
    assert rendered.endswith("/icons/cf6c8a6a75.webp)"), rendered
    assert "starside.work" not in rendered, "外链不许出现在响应文本里"


def test_every_icon_in_the_archive_is_resolved_to_a_real_asset(payloads) -> None:
    """数据里出现的每个图标，都要在归档索引里查到落点（不然就是给了个打不开的路径）。"""
    index = json.loads(
        (Path(__file__).parents[1] / "data" / "starside" / "index.json").read_text(encoding="utf-8")
    )
    archived = {asset["path"] for asset in index["assets"]}

    seen: set[str] = set()
    for payload in payloads:
        for text in _strings(payload):
            for match in _ICON_RE.finditer(markup.render(text)):
                seen.add(match.group(1))
    assert seen, "样本里一个图标都没有？夹具或解析坏了"
    unresolved = sorted(path for path in seen if path not in archived)
    assert not unresolved, f"这些图标路径不在归档索引里：{unresolved}"
    assert not [path for path in seen if "://" in path], "响应里不许出现外链或绝对地址"


def test_a_non_perk_answer_offers_near_perks_instead_of_stopping() -> None:
    """问错名字时要给「你是不是想找 X」，不能只回「它不是 perk」就断掉。

    盲测 2026-10-06：玩家问「亡者复仇」这个 perk，工具（正确地）说它不是 perk，
    但**没有给任何近似候选**，这一问就到此为止 —— 而玩家多半只是记错了名字。
    """
    from destiny_mcp.tools import _perk_branches as perk_branches

    class _Query:
        def get_perk_description(self, name: str) -> dict:
            return {
                "name": name, "hash": 2452241573, "plug_category": "v510_new_scout_rifle0_skins",
                "description": "装备此武器皮肤以改变外观。",
            }

    class _Manifest:
        def search(self, query: str, limit: int = 8) -> list[dict]:
            return [
                {"itemHash": 1, "name": f"{query}（皮肤）"},
                {"itemHash": 2, "name": "亡者复仇者"},
            ]

        def get_item_definition(self, item_hash: int) -> dict:
            if item_hash == 1:  # 皮肤：要排除
                return {"itemType": 19, "plug": {"plugCategoryIdentifier": "v510_new_scout_rifle0_skins"}}
            return {"itemType": 19, "plug": {"plugCategoryIdentifier": "frames"}}

    svc = {
        "manifest_query_svc": _Query(),
        "manifest": _Manifest(),
        "starside_entities_svc": None,
        "starside_svc": None,
    }
    response = perk_branches.perk_description_payload(svc, "亡者复仇")

    assert "不是一个 perk" in response["summary"]
    near = response["data"]["near_matches"]
    assert [entry["name"] for entry in near] == ["亡者复仇者"], (
        f"只该给**真 perk**（皮肤要排除），实际 {near}"
    )
    assert "亡者复仇者" in response["summary"], "摘要要说出来，别让调用方自己去 data 里翻"
