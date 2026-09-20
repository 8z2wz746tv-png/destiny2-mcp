"""锻造图样查询：替身测试，不碰真机。

盯住的是几条实测出来、错了就会答错的边界：

1. **图样的解锁进度只在组件 900**（`profileRecords`）：组件 800 里一条都没有、1300 只给
   "能塑形哪些 perk"。所以"组件没返回"必须报错，**不能**当成"图样都没解锁"；
2. **目录判据是"记录名 == 可锻造武器名（精确）"**：用模糊匹配会让「糖果生意催化」命中
   「糖果生意」，把 141 条催化混进图样；变体（专家/失时/痛苦）没有自己的记录，要指回基础版；
3. **「未开始」是没有记录，不是 0/5**：行里 `progress` 是 `None`，话术与警告都不许写成"进度 0"；
4. **来源是社区参考**：读失败只降级成 `available=false` + warning，不影响图样本身的账号结果。

真机核对（149/183、累积救赎 4/5）写在 docs/plans/PATTERN_QUERY_PLAN.md，不进这个文件：
单测用替身、任何机器能跑（docs/testing/TESTING_CORPUS.md 第一张表）。
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from destiny_mcp.exceptions import APIError, InvalidArgumentError
from destiny_mcp.services import profile_components
from destiny_mcp.services.pattern_service import (
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_UNLOCKED,
    PatternService,
    name_key,
)
from destiny_mcp.services.starside_crafting_sources import PAGE_ID
from destiny_mcp.tools import _patterns_branches, _requests
from destiny_mcp.tools._param_contracts import PARAMETER_OWNERS

PLAYER = "TestGuardian#1234"
MID = "4611686018000000001"
MTYPE = 3

ROOT = 2642502414
WRAPPER = 3442838224
SLOT_PRIMARY = 127506319
SLOT_CATALYST = 2744330515
TYPE_HANDCANNON = 2174454731
TYPE_KINETIC = 2538646043

FATEBRINGER = 1001          # 可锻造：传说，要 5 次
RETROFIT = 1002             # 可锻造：异域，要 1 次（同时有催化记录，用来验"催化不能混进来"）
SUFFERANCE = 1003           # 不可锻造的异域（有记录也没用）
INHERITANCE = 1004          # 可锻造：传说，要 3 次
UNRELATED_RECORD = 999      # 记录组件里的无关记录（证明"组件非空"）

ITEM_FATEBRINGER = {"hash": FATEBRINGER, "inventory": {"recipeItemHash": 9001, "tierTypeName": "传说", "tierType": 5},
                    "displayProperties": {"name": "惩戒措施"}}
ITEM_RETROFIT = {"hash": RETROFIT, "inventory": {"recipeItemHash": 9002, "tierTypeName": "异域", "tierType": 6},
                 "displayProperties": {"name": "糖果生意"}}
ITEM_SUFFERANCE = {"hash": SUFFERANCE, "inventory": {"tierTypeName": "异域"},
                   "displayProperties": {"name": "苦痛"}}
ITEM_INHERITANCE = {"hash": INHERITANCE, "inventory": {"recipeItemHash": 9003, "tierTypeName": "传说", "tierType": 5},
                    "displayProperties": {"name": "继承"}}

# 变体（失时）：可锻造、但塑形配置只覆盖 3 个栏位，插槽里也没有深视插槽。
# 实测（2026-09-20，36 件变体无一例外）就是这个形状。
VARIANT = 1005
ITEM_VARIANT = {
    "hash": VARIANT,
    "inventory": {"recipeItemHash": 9004, "tierTypeName": "传说", "tierType": 5},
    "displayProperties": {"name": "惩戒措施（失时）"},
    "sockets": {"socketEntries": [{"socketTypeHash": 4251072212}]},
}
ITEMS = {
    item["hash"]: item
    for item in (ITEM_FATEBRINGER, ITEM_RETROFIT, ITEM_SUFFERANCE, ITEM_INHERITANCE, ITEM_VARIANT)
}
# 图样条目：`crafting.requiredSocketTypeHashes` 决定"塑形能选哪几个栏位"。
ITEMS[9001] = {"hash": 9001, "crafting": {"requiredSocketTypeHashes": [
    3868679925, 3694362576, 2316004942, 3036227398, 3036227399]}}
ITEMS[9004] = {"hash": 9004, "crafting": {"requiredSocketTypeHashes": [
    3868679925, 3694362576, 2316004942]}}


def _node(name: str, *, records: tuple[int, ...] = (), nodes: tuple[int, ...] = ()) -> dict:
    return {
        "displayProperties": {"name": name},
        "children": {
            "records": [{"recordHash": value} for value in records],
            "presentationNodes": [{"presentationNodeHash": value} for value in nodes],
        },
    }


def nodes(*, with_catalyst: bool = True) -> dict[int, dict]:
    tree = {
        ROOT: _node("模式和催化", nodes=(WRAPPER,)),
        WRAPPER: _node(
            "模式和催化",
            nodes=(SLOT_PRIMARY, SLOT_CATALYST) if with_catalyst else (SLOT_PRIMARY,),
        ),
        SLOT_PRIMARY: _node("主武器模式", nodes=(TYPE_HANDCANNON,)),
        TYPE_HANDCANNON: _node("手炮", records=(501, 502, 503, 504, 505, 506)),
    }
    if with_catalyst:
        tree[SLOT_CATALYST] = _node("异域催化", nodes=(TYPE_KINETIC,))
        tree[TYPE_KINETIC] = _node("动能武器", records=(601,))
    return tree


def records() -> dict[int, dict]:
    return {
        501: {"displayProperties": {"name": "惩戒措施"}, "objectiveHashes": [701]},
        # 催化记录：名字带「催化」后缀，模糊匹配会命中同名武器，靠精确比对挡掉
        502: {"displayProperties": {"name": "糖果生意催化"}, "objectiveHashes": [702]},
        503: {"displayProperties": {"name": "没这把枪"}, "objectiveHashes": [703]},
        504: {"displayProperties": {"name": "糖果生意"}, "objectiveHashes": [704]},
        # 名字对得上，但那件东西不可锻造 → 不是图样
        505: {"displayProperties": {"name": "苦痛"}, "objectiveHashes": [705]},
        506: {"displayProperties": {"name": "继承"}, "objectiveHashes": [706]},
        601: {"displayProperties": {"name": "苦痛催化"}, "objectiveHashes": [707]},
    }


OBJECTIVES = {
    701: {"completionValue": 5}, 702: {"completionValue": 2}, 703: {"completionValue": 1},
    704: {"completionValue": 1}, 705: {"completionValue": 1}, 706: {"completionValue": 3},
}


class FakeManifest:
    """只实现 PatternService 用到的那三个方法。"""

    def __init__(
        self,
        *,
        tree: dict[int, dict] | None = None,
        search: dict[str, int] | None = None,
        records_override: dict[int, dict] | None = None,
    ) -> None:
        self._nodes = tree if tree is not None else nodes()
        self._records = records_override if records_override is not None else records()
        self._search = search if search is not None else {
            "惩戒措施": FATEBRINGER,
            # 模糊命中：催化记录名 → 同名武器（必须被精确比对挡掉）
            "糖果生意催化": RETROFIT,
            "糖果生意": RETROFIT,
            "苦痛": SUFFERANCE,
            "继承": INHERITANCE,
            "惩戒措施（失时）": VARIANT,
            # 角色级记录那条（只在 character_tree() 的目录里出现）
            "面纱威胁": CHARACTER_WEAPON,
        }

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        if table == "DestinyPresentationNodeDefinition":
            return self._nodes.get(hash_id)
        if table == "DestinyRecordDefinition":
            return self._records.get(hash_id)
        if table == "DestinyObjectiveDefinition":
            return OBJECTIVES.get(hash_id)
        return None

    def search(self, query: str, *, limit: int = 20, item_type: int | None = None) -> list[dict]:
        hit = self._search.get(query)
        if hit is None:
            return []
        item = ITEMS[hit]
        return [{"itemHash": hit, "name": item["displayProperties"]["name"], "itemType": 3}]

    def get_item_definition(self, item_hash: int) -> dict | None:
        return ITEMS.get(item_hash)


class FakeBungie:
    def __init__(self, profile: dict | None = None) -> None:
        self.profile = profile if profile is not None else profile_with({})
        self.calls = 0

    async def get_profile(self, membership_id: str, membership_type: int, components: list[int]) -> dict:
        self.calls += 1
        assert components == profile_components.PATTERNS
        return self.profile


class FakeResolver:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def resolve_player(self, player_name: str) -> dict:
        self.asked.append(player_name)
        return {"membership_id": MID, "membership_type": MTYPE, "display_name": PLAYER}


def profile_with(
    records_map: dict[int, dict] | None = None,
    *,
    character_records: list[dict[int, dict]] | None = None,
) -> dict:
    """组件 900 的响应形状：`Response.profileRecords` + `Response.characterRecords`。

    真实账号的记录组件里总有几千条别的记录（实测 5299 条 + 每个角色 869 条），
    这里补一条无关记录 —— "组件不是空的"和"这条图样没解锁"是两件事，替身也要能同时表达。
    """
    entries: dict[int, dict] = {UNRELATED_RECORD: record_component(1, 1)}
    entries.update(records_map or {})
    profile = {"profileRecords": {"data": {"records": {str(k): v for k, v in entries.items()}}}}
    if character_records is not None:
        profile["characterRecords"] = {"data": {
            f"char{index}": {"records": {str(k): v for k, v in per_char.items()}}
            for index, per_char in enumerate(character_records)
        }}
    return profile


def record_component(progress: int, need: int, state: int = 4) -> dict:
    return {"objectives": [{"progress": progress, "completionValue": need}], "state": state}


def service(
    profile: dict | None = None,
    *,
    tree: dict[int, dict] | None = None,
    starside: Any = None,
    records_override: dict[int, dict] | None = None,
) -> tuple[PatternService, FakeBungie]:
    bungie = FakeBungie(profile)
    svc = PatternService(
        bungie, FakeManifest(tree=tree, records_override=records_override), FakeResolver(), starside,
    )  # type: ignore[arg-type]
    return svc, bungie


class FakeStarside:
    """`CraftingSources.from_service` 只用 get_knowledge 这一个入口。"""

    def __init__(self, rows: list[dict] | None = None, text: str = "", *, error: Exception | None = None) -> None:
        self._rows = rows if rows is not None else [
            {"heading": "突袭", "cells": [{"text": "玻璃拱顶"}, {"text": "惩戒措施、糖果生意"}]},
        ]
        self._text = text
        self._error = error

    def get_knowledge(self, knowledge_id: str, *, section: str = "text", offset: int = 0, limit: int = 10) -> dict:
        if self._error is not None:
            raise self._error
        assert knowledge_id == PAGE_ID
        source = {"title": "锻造武器来源 · Starside", "url": "https://starside.work/crafting/index.html",
                  "updated_at": "2026.8.30", "trust": "untrusted_reference", "sha256": "ignored"}
        if section == "tables":
            return {"rows": self._rows if offset == 0 else [], "next_offset": None, "source": source}
        return {"text": self._text if offset == 0 else "", "next_offset": None, "source": source}


# ── 目录 ─────────────────────────────────────────────────────────────


def test_catalog_only_keeps_records_named_like_a_craftable_weapon() -> None:
    """三条留下（惩戒措施/糖果生意/继承），催化记录、无同名物品、对得上但不可锻造的都出局。"""
    svc, _ = service()
    catalog = svc._catalog()

    assert [entry["name"] for entry in catalog] == ["惩戒措施", "糖果生意", "继承"]
    entry = catalog[0]
    assert entry["need"] == 5
    assert entry["weapon_type"] == "手炮"
    assert entry["group"] == "主武器模式"
    assert entry["tier"] == "传说"
    assert entry["item_hash"] == FATEBRINGER
    assert catalog[1]["need"] == 1 and catalog[1]["tier"] == "异域"


def test_catalog_rejects_fuzzy_name_hits_instead_of_mixing_in_catalysts() -> None:
    """催化记录名是「糖果生意催化」，模糊匹配会命中可锻造的「糖果生意」—— 必须靠精确比对挡掉。"""
    svc, _ = service()

    names = [entry["name"] for entry in svc._catalog()]

    assert "糖果生意催化" not in names and "苦痛催化" not in names
    assert "糖果生意" in names, "同名武器本身是图样，别把整条名字一起误杀"


def test_catalog_skips_the_catalyst_branch_entirely() -> None:
    """没有催化分支时目录不变：分组只影响标签，不影响收录判据。"""
    svc_with, _ = service()
    svc_without, _ = service(tree=nodes(with_catalyst=False))

    assert svc_with._catalog() == svc_without._catalog()


def test_catalog_is_cached_per_process() -> None:
    svc, _ = service()

    assert svc._catalog() is svc._catalog()


# ── 账号进度 ─────────────────────────────────────────────────────────


async def test_status_covers_unlocked_in_progress_and_not_started() -> None:
    svc, _ = service(profile_with({
        501: record_component(5, 5, state=67),
        506: record_component(2, 3),
    }))

    rows = (await svc.patterns(PLAYER, limit=10))["rows"]

    assert [row["name"] for row in rows] == ["继承", "糖果生意", "惩戒措施"]
    assert [row["status"] for row in rows] == [STATUS_IN_PROGRESS, STATUS_NOT_STARTED, STATUS_UNLOCKED]


async def test_not_started_row_has_no_progress_and_keeps_the_requirement() -> None:
    svc, _ = service(profile_with({}))

    row = (await svc.patterns(PLAYER, limit=10))["rows"][0]

    assert row["status"] == STATUS_NOT_STARTED
    assert row["progress"] is None, "没有记录 ≠ 进度 0"
    assert row["need"] == 5, "需求次数来自 Manifest，缺进度也照样给"


async def test_missing_or_empty_component_is_an_error_not_zero_unlocked() -> None:
    """组件缺失或整块为空：如实报"拿不到"，绝不能当成"图样都没解锁"。"""
    payloads = (
        {},
        {"profileRecords": {"data": {}}},
        {"profileRecords": {"data": {"records": {}}}},
    )
    for payload in payloads:
        svc, _ = service(payload)
        with pytest.raises(APIError) as excinfo:
            await svc.patterns(PLAYER)
        assert "不能把未返回当成未解锁" in str(excinfo.value)


# ── 角色级记录（真机上栽过的那一条） ──────────────────────────────────


def character_tree() -> dict[int, dict]:
    """目录里放一条**角色级**记录：实测 183 条模式记录里有 32 条是这样。"""
    tree = nodes(with_catalyst=False)
    tree[TYPE_HANDCANNON] = _node("手炮", records=(501, 507))
    return tree


CHARACTER_SCOPED = 507
CHARACTER_WEAPON = 1006


def character_records_override() -> dict[int, dict]:
    out = records()
    out[CHARACTER_SCOPED] = {
        "displayProperties": {"name": "面纱威胁"},
        "objectiveHashes": [708],
        # scope=1：角色级。只读 profileRecords 会把它判成"未开始"。
        "scope": 1,
    }
    return out


ITEMS[CHARACTER_WEAPON] = {
    "hash": CHARACTER_WEAPON,
    "inventory": {"recipeItemHash": 9006, "tierTypeName": "传说", "tierType": 5},
    "displayProperties": {"name": "面纱威胁"},
}
ITEMS[9006] = {"hash": 9006, "crafting": {"requiredSocketTypeHashes": [
    3868679925, 3694362576, 2316004942, 3036227398, 3036227399]}}
OBJECTIVES[708] = {"completionValue": 5}


async def test_character_scoped_record_is_not_reported_as_not_started() -> None:
    """真机踩过：32 条模式记录是角色级（scope=1），只读 profileRecords 会把它们全判成"未开始"。

    用户拿游戏截图当场抓出来（那 32 把其实三个角色都 5/5，账号里还有对应的已锻造副本）。
    """
    svc, _ = service(
        profile_with(character_records=[
            {CHARACTER_SCOPED: record_component(5, 5, state=67)},
            {CHARACTER_SCOPED: record_component(5, 5, state=67)},
            {CHARACTER_SCOPED: record_component(5, 5, state=67)},
        ]),
        tree=character_tree(),
        records_override=character_records_override(),
        starside=FakeStarside(),
    )

    rows = (await svc.patterns(PLAYER, limit=10))["rows"]
    row = next(item for item in rows if item["name"] == "面纱威胁")

    assert row["status"] == STATUS_UNLOCKED
    assert (row["progress"], row["need"]) == (5, 5)
    assert (await svc.patterns(PLAYER, limit=10))["read"]["record_scopes"] == ["profile", "character"]


async def test_character_scoped_record_takes_the_best_character() -> None:
    """模式解锁是账号级的：任一角色满就算解锁，进度取最靠前的那个角色。"""
    svc, _ = service(
        profile_with(character_records=[
            {CHARACTER_SCOPED: record_component(2, 5)},
            {CHARACTER_SCOPED: record_component(5, 5, state=67)},
            {CHARACTER_SCOPED: record_component(1, 5)},
        ]),
        tree=character_tree(),
        records_override=character_records_override(),
        starside=FakeStarside(),
    )

    row = next(item for item in (await svc.patterns(PLAYER, limit=10))["rows"] if item["name"] == "面纱威胁")

    assert (row["status"], row["progress"], row["remaining"]) == (STATUS_UNLOCKED, 5, 0)


async def test_record_absent_from_both_scopes_is_still_not_started() -> None:
    """两个作用域都没有 ⇒ 才是真的「未开始」（不能因为读了角色级就把缺的也说成有）。"""
    svc, _ = service(
        profile_with(character_records=[{}, {}]),
        tree=character_tree(),
        records_override=character_records_override(),
        starside=FakeStarside(),
    )

    row = next(item for item in (await svc.patterns(PLAYER, limit=10))["rows"] if item["name"] == "面纱威胁")

    assert row["status"] == STATUS_NOT_STARTED
    assert row["progress"] is None and row["remaining"] is None


async def test_state_is_cached_and_read_block_stays_stable() -> None:
    svc, bungie = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())

    first = await svc.patterns(PLAYER)
    second = await svc.patterns(PLAYER)

    assert bungie.calls == 1, "5 分钟 TTL 内不重复拉 1.44 MB 的记录组件"
    # `read` 只能是"每次都一样"的口径：别名等价那条语料比对 data 逐字节相同，
    # 放"这次读了几毫秒"进去会让同组别名跑出不同 data（真机被抓到过）。
    assert first["read"] == second["read"] == {
        "component": 900, "record_scopes": ["profile", "character"], "ttl_seconds": 300,
    }


async def test_progress_uses_the_component_value_when_it_differs_from_the_manifest() -> None:
    """组件里的 completionValue 是权威值：上游改过需求次数时不能被 Manifest 的旧值盖掉。"""
    svc, _ = service(profile_with({501: record_component(3, 3)}))

    row = next(row for row in (await svc.patterns(PLAYER, limit=10))["rows"] if row["name"] == "惩戒措施")

    assert (row["need"], row["progress"], row["status"]) == (3, 3, STATUS_UNLOCKED)


# ── 排序、筛选、翻页 ──────────────────────────────────────────────────


async def test_rows_are_sorted_by_what_to_do_next() -> None:
    svc, _ = service(
        profile_with({501: record_component(5, 5), 506: record_component(2, 3)}),
        starside=FakeStarside(),
    )

    rows = (await svc.patterns(PLAYER, limit=10))["rows"]

    assert [row["status"] for row in rows] == [STATUS_IN_PROGRESS, STATUS_NOT_STARTED, STATUS_UNLOCKED]


async def test_counts_and_groups_follow_the_filter() -> None:
    svc, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())

    result = await svc.patterns(PLAYER, weapon_type="手炮")

    assert result["counts"] == {
        "total": 3, "unlocked": 1, "not_unlocked": 2, "in_progress": 0, "not_started": 2,
    }
    assert result["catalog_total"] == 3
    assert result["by_group"][0]["group"] == "主武器模式"
    assert result["by_group"][0]["by_type"] == [{"weapon_type": "手炮", "total": 3, "unlocked": 1}]


async def test_rarity_filter_returns_only_that_tier_and_counts_by_tier() -> None:
    """「我的金枪图样」必须一次拿全：默认一页只有 20 条，靠翻页数金枪会漏（真机发生过）。"""
    svc, _ = service(profile_with({}), starside=FakeStarside())

    exotic = await svc.patterns(PLAYER, rarity="异域", limit=50)
    legendary = await svc.patterns(PLAYER, rarity="传说", limit=50)
    all_rows = await svc.patterns(PLAYER, limit=50)

    assert [row["name"] for row in exotic["rows"]] == ["糖果生意"]
    assert exotic["counts"]["total"] == 1 and exotic["filtered"] is True
    assert exotic["by_tier"] == {"异域": 1}
    assert legendary["by_tier"] == {"传说": 2}
    assert all_rows["by_tier"] == {"传说": 2, "异域": 1}, "汇总按稀有度分开，别只给总数"


async def test_rarity_accepts_player_words_and_rejects_nonsense() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    for word in ("金枪", "金装", "exotic", "异域"):
        result = await svc.patterns(PLAYER, rarity=word)
        assert [row["name"] for row in result["rows"]] == ["糖果生意"], word

    with pytest.raises(InvalidArgumentError):
        await svc.patterns(PLAYER, rarity="金光闪闪")


async def test_type_filter_that_matches_nothing_still_reports_the_types_available() -> None:
    svc, _ = service()

    result = await svc.patterns(PLAYER, weapon_type="不存在的类型")

    assert result["counts"]["total"] == 0
    assert result["available_types"] == ["手炮"]


async def test_pagination_reports_next_offset_only_when_there_is_more() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    first = await svc.patterns(PLAYER, limit=1)
    second = await svc.patterns(PLAYER, limit=1, offset=first["next_offset"] or 0)
    third = await svc.patterns(PLAYER, limit=1, offset=second["next_offset"] or 0)

    assert (first["returned"], first["next_offset"]) == (1, 1)
    assert (second["returned"], second["next_offset"]) == (1, 2)
    assert (third["returned"], third["next_offset"]) == (1, None)
    assert len({first["rows"][0]["name"], second["rows"][0]["name"], third["rows"][0]["name"]}) == 3


async def test_name_filter_exact_prefix_substring_and_unknown() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    exact = await svc.patterns(PLAYER, weapon_name="惩戒措施")
    prefix = await svc.patterns(PLAYER, weapon_name="惩戒")
    unknown = await svc.patterns(PLAYER, weapon_name="不存在的一把枪")

    assert [row["name"] for row in exact["rows"]] == ["惩戒措施"]
    assert exact["filtered"] is True
    assert [row["name"] for row in prefix["rows"]] == ["惩戒措施"]
    assert unknown["rows"] == [] and unknown["candidates"] == []


async def test_variant_name_points_back_to_the_base_pattern() -> None:
    """（失时）这类变体没有自己的图样记录，问它要答基础版，而不是"没找到"。"""
    svc, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())

    result = await svc.patterns(PLAYER, weapon_name="惩戒措施（失时）")

    assert result["variant_of"] == "惩戒措施（失时）"
    assert [row["name"] for row in result["rows"]] == ["惩戒措施"]


async def test_name_key_ignores_case_and_separators() -> None:
    assert name_key("IKELOS_SMG_v1.0.3") == name_key("ikelos_smg_v1.0.3")
    assert name_key("伊尔·约特之歌") == name_key("伊尔 约特之歌")


# ── 来源（社区资料，可选） ────────────────────────────────────────────


async def test_sources_are_attached_per_row_with_page_reference() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    result = await svc.patterns(PLAYER)

    assert result["rows"][0]["source"] == "突袭｜玻璃拱顶"
    assert result["sources"]["page"]["updated_at"] == "2026.8.30"
    assert result["sources"]["page"]["trust"] == "untrusted_reference"
    assert result["sources"]["page"].get("sha256") is None, "出处只带对调用方有意义的字段"


async def test_prose_line_is_used_when_the_tables_miss() -> None:
    svc, _ = service(
        profile_with({}),
        starside=FakeStarside(
            rows=[{"heading": "地牢", "cells": [{"text": "二象性"}, {"text": "另一把"}]}],
            text="地牢\n二象性另会掉落惩戒措施（尾王），掉率较低。",
        ),
    )

    result = await svc.patterns(PLAYER)

    assert result["rows"][0]["source"].startswith("正文｜")
    assert "惩戒措施" in result["rows"][0]["source"]


async def test_broken_community_data_degrades_without_breaking_the_account_result() -> None:
    svc, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside(error=RuntimeError("坏了")))

    result = await svc.patterns(PLAYER, weapon_name="惩戒措施")

    assert result["rows"][0]["status"] == STATUS_UNLOCKED, "本地资料坏了不能弄坏账号结果"
    assert result["sources"]["available"] is False
    assert any("失败" in warning for warning in result["sources"]["warnings"])


async def test_no_starside_service_is_not_an_error() -> None:
    svc, _ = service(profile_with({501: record_component(5, 5)}))

    result = await svc.patterns(PLAYER)

    assert result["sources"]["available"] is False
    assert result["rows"][0]["source"] is None


# ── 载荷与话术 ───────────────────────────────────────────────────────


async def test_overview_summary_names_what_is_in_progress() -> None:
    svc, _ = service(profile_with({501: record_component(4, 5), 502: record_component(2, 5)}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER))

    assert payload["summary"] == (
        "锻造武器模式（红框）：已解锁 0 / 3；未解锁 3（进行中 1、还没开始 2）。进行中：惩戒措施 4/5。"
    )
    assert payload["data"]["counts"]["total"] == 3
    assert payload["data"]["patterns"]["items"][0]["status"] == STATUS_IN_PROGRESS


async def test_terms_teach_the_agent_the_player_words() -> None:
    """玩家说红框、游戏说模式、工具说图样 —— 三个词的关系必须随响应给出去。"""
    svc, _ = service(profile_with({}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER))

    terms = payload["data"]["terms"]
    assert set(terms) == {"红框", "模式", "塑形"}
    assert "深视共振" in terms["红框"] and "pattern" in terms["模式"]


async def test_single_weapon_summary_says_how_many_are_left() -> None:
    svc, _ = service(profile_with({501: record_component(4, 5)}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER, weapon_name="惩戒措施"))

    assert payload["summary"] == "「惩戒措施」：进行中 4/5，还差 1 个红框萃取。"


async def test_unlocked_and_not_started_summaries_do_not_lie() -> None:
    unlocked, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())
    started, _ = service(profile_with({}), starside=FakeStarside())

    done = _patterns_branches.patterns_payload(await unlocked.patterns(PLAYER, weapon_name="惩戒措施"))
    todo = _patterns_branches.patterns_payload(await started.patterns(PLAYER, weapon_name="惩戒措施"))

    assert "可以在圣物塑形" in done["summary"]
    assert "没有这条模式的进度记录" in todo["summary"]
    assert todo["data"]["patterns"]["items"][0]["progress"] is None


async def test_not_started_rows_carry_an_explicit_warning() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER))

    assert any("不能读成「进度 0」" in warning for warning in payload["warnings"])
    assert any("Starside" in warning for warning in payload["warnings"])


async def test_variant_summary_points_at_the_base_weapon() -> None:
    svc, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER, weapon_name="惩戒措施（失时）"))

    assert payload["summary"] == (
        "「惩戒措施（失时）」没有单独的模式：模式是基础版「惩戒措施」的，已解锁 5/5。"
        "变体可塑形的栏位更少：只有 框架/枪管/弹夹（基础版 框架/枪管/弹夹/特征1/特征2），三四号特性固定。"
        "变体自己不带深视插槽（带的是强化插槽，升级用），红框（深视共振）掉的是基础版。"
    )


async def test_variant_facts_come_from_the_manifest_not_from_a_hardcoded_sentence() -> None:
    """变体能不能塑形、能选哪些栏位、有没有深视插槽，全部按查到的那一件现算。"""
    svc, _ = service(profile_with({501: record_component(5, 5)}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER, weapon_name="惩戒措施（失时）"))

    assert payload["data"]["variant"] == {
        "name": "惩戒措施（失时）",
        "base_name": "惩戒措施",
        "shapeable_columns": ["框架", "枪管", "弹夹"],
        "base_shapeable_columns": ["框架", "枪管", "弹夹", "特征1", "特征2"],
        "traits_fixed": True,
        "has_deepsight_socket": False,
        "has_upgrade_socket": True,
    }
    assert "变体可塑形的栏位更少" in payload["data"]["note"]


async def test_unknown_name_says_the_catalog_size_instead_of_guessing() -> None:
    svc, _ = service(profile_with({}), starside=FakeStarside())

    payload = _patterns_branches.patterns_payload(await svc.patterns(PLAYER, weapon_name="不存在的一把枪"))

    assert "没有找到匹配的可锻造武器" in payload["summary"]
    assert "共 3 条" in payload["summary"]


# ── 契约 ─────────────────────────────────────────────────────────────


def test_pattern_aliases_match_the_declared_literal() -> None:
    assert set(_requests.WEAPON_PATTERN_INTENTS) <= set(get_args(_requests.WeaponIntent))


def test_parameter_ownership_is_registered_for_every_alias() -> None:
    for parameter in ("weapon_name", "weapon_type", "limit", "offset", "player_name"):
        owned = PARAMETER_OWNERS[("weapon_assistant", parameter)].intents
        assert set(_requests.WEAPON_PATTERN_INTENTS) <= owned, parameter
    for parameter in ("perk_name", "required_perks", "item_instance_id"):
        assert not set(_requests.WEAPON_PATTERN_INTENTS) & PARAMETER_OWNERS[("weapon_assistant", parameter)].intents
