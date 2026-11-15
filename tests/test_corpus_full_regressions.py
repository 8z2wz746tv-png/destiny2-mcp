"""全面语料实跑抓到的 7 条问题的回归锁（见 `docs/testing/TESTING_CORPUS_FULL.md` 的「已知问题」）。

这些行为以前一条测试都没有，所以才能悄悄漂移：
`analyze` 用一句英文断言"配不出来"、`exotic_armor` 自己一套 camelCase 键、
收藏品 hash 有正有负、`artifact` 不带名字就没有当前神器、报错出现「。。」、
`search` 未命中不说"没找到"、`item` 传 None 会 AttributeError。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from destiny_mcp.build import analyzer
from destiny_mcp.build.models import STAT_NAMES
from destiny_mcp.exceptions import APIError, InvalidArgumentError, SubclassError
from destiny_mcp.services.armor_payload import (
    armor_definition_payload,
    class_key_of,
    slot_key_from_display,
)
from destiny_mcp.services.artifact_service import ArtifactService
from destiny_mcp.services.collection_service import CollectionService
from destiny_mcp.services.manifest_query_service import ManifestQueryService
from destiny_mcp.tools import _armor_branches as branches
from destiny_mcp.tools.assistants import inventory_assistant
from destiny_mcp.utils.hash_utils import to_signed, to_unsigned


# ── ① analyze 不许在没有证据时断言"配不出来" ────────────────────────────


class _Piece:
    def __init__(self, item_hash: int) -> None:
        self.item_hash = item_hash


class _Snapshot:
    def __init__(self, size: int = 2) -> None:
        for slot in ("helmets", "gauntlets", "chests", "legs", "class_items"):
            setattr(self, slot, [_Piece(i) for i in range(size)])


def _constraints(**targets: int):
    values = {f"{name}_min": 0 for name in STAT_NAMES}
    values.update({f"{name}_min": value for name, value in targets.items()})
    return type("C", (), {"exotic_hashes": set(), **values})()


def test_analyze_reason_never_claims_infeasibility_within_ceilings(monkeypatch) -> None:
    """所有目标都在单项上限内时，只能说"上限解释不了"，不能说"没有合法组合"。

    实机出现过：analyze 回这句英文，而同一组约束 recommend 给出 completion_rate=1.0。
    """
    monkeypatch.setattr(
        analyzer, "_max_possible_stats", lambda snapshot, constraints: {k: 134 for k in STAT_NAMES}
    )

    result = analyzer.analyze(_Snapshot(), _constraints(health=100))

    assert result.precision == "exact"
    assert "No valid armor combination" not in result.reason
    assert "单项上限" in result.reason
    assert "recommend" in result.reason, "要告诉调用方结论该去哪儿拿"


def test_analyze_failure_reason_is_chinese_and_gives_the_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        analyzer, "_max_possible_stats", lambda snapshot, constraints: {k: 134 for k in STAT_NAMES}
    )

    result = analyzer.analyze(_Snapshot(), _constraints(health=200))

    assert "生命值要 200" in result.reason and "只有 134" in result.reason and "差 66" in result.reason
    assert "need 200" not in result.reason
    assert result.suggested_farm and all("机灵模组" in line for line in result.suggested_farm)


# ── ② exotic_armor 与 intent=item 同一套身份块 ─────────────────────────


_EXOTIC_DEF = {
    2782999717: {
        "displayProperties": {"name": "星火协议", "description": "描述", "icon": "/x.png"},
        "itemTypeDisplayName": "胸部护甲",
        "flavorText": "风味",
        "classType": 2,
    }
}


def test_armor_definition_payload_matches_the_instance_identity_block() -> None:
    payload = armor_definition_payload(
        item_hash=to_signed(2782999717),
        lookup=lambda h: _EXOTIC_DEF.get(to_unsigned(h)),
        name_en="Starfire Protocol",
        class_type=2,
    )

    identity = payload["identity"]
    assert identity["item_hash"] == 2782999717, "对外统一无符号"
    assert identity["name_en"] == "Starfire Protocol"
    assert identity["slot"] == "chest" and identity["slot_display"] == "胸部护甲"
    assert identity["class_type"] == "warlock" and identity["class_display"] == "术士"
    assert identity["rarity"] == "异域" and identity["rarity_tier"] == 6
    # 定义级没有实例：不许编 T 级/分族，但要说清为什么
    assert identity["gear_tier"] is None and identity["armor_system"] is None
    assert "定义级" in identity["gear_tier_note"]
    assert {"nameEn", "flavorText", "classType", "tierType"} & set(identity) == set()
    assert payload["armor_schema_version"] == 1


def test_slot_and_class_helpers_fall_back_to_empty_string() -> None:
    assert slot_key_from_display("腿部护甲") == "legs"
    assert slot_key_from_display("乱写的部位") == ""
    assert class_key_of(0) == "titan" and class_key_of(None) == ""


class _ExoticManifest:
    def search(self, name: str, limit: int = 5, item_type: int | None = None):
        return [{"itemHash": to_signed(2782999717), "name": "星火协议", "tier": 6, "itemType": 2}]

    def get_item_definition(self, item_hash: int):
        return _EXOTIC_DEF.get(to_unsigned(item_hash))

    def get_english_name(self, item_hash: int) -> str:
        return "Starfire Protocol"

    def get_plug_set_plugs(self, plug_set_hash: int):
        return []


def test_exotic_armor_details_use_the_unified_identity_block() -> None:
    service = object.__new__(ManifestQueryService)
    service._manifest = _ExoticManifest()

    payload = service.get_exotic_armor_details("星火协议")

    assert set(payload["identity"]) >= {"name_en", "slot_display", "gear_tier", "class_display"}
    assert payload["intrinsic_perks"] == []
    assert "nameEn" not in payload["identity"]


# ── ③ 收藏品 hash 统一无符号 ──────────────────────────────────────────


class _CollectionManifest:
    def search(self, name: str, limit: int = 10, item_type: int | None = None):
        return [{"itemHash": to_signed(2230338236), "name": "无感"}]

    def get_item_definition(self, item_hash: int):
        return {"collectibleHash": to_signed(2980545307), "displayProperties": {"name": "无感"}}

    def get_definition(self, table: str, item_hash: int):
        return {"hash": item_hash, "itemHash": to_signed(2230338236),
                "displayProperties": {"name": "无感"}}

    def find_collectible_by_item_hash(self, item_hash: int):
        return None

    def get_item_name(self, item_hash: int) -> str:
        return "无感"


class _CollectionResolver:
    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def resolve_character_id(self, mid, mtype, character: str) -> str:
        return "77"

    async def get_profile(self, mid, mtype, components):
        # 组件用**无符号**做键（Bungie 的约定），这正是修之前查不中的那种情况
        return {
            "profileCollectibles": {
                "data": {"collectibles": {str(to_unsigned(to_signed(2980545307))): {"state": 0}}}
            }
        }


@pytest.mark.asyncio
async def test_collectible_item_hashes_are_unsigned_and_state_still_resolves() -> None:
    service = CollectionService(None, _CollectionManifest(), _CollectionResolver())

    payload = await service.get_collectible_item_status("p", "无感", character="hunter")

    row = payload["items"][0]
    assert row["item_hash"] == to_unsigned(to_signed(2230338236)) > 0
    assert row["collectible_hash"] == to_unsigned(to_signed(2980545307)) > 0
    assert row["acquired"] is True, "无符号化之后仍要能查中状态"


class _NodeBungie:
    async def get_collectible_node_details(self, mtype, mid, char_id, node_hash, components=None):
        return {
            "ErrorCode": 1,
            "Response": {
                "collectibles": {
                    "data": {"collectibles": {str(to_unsigned(to_signed(2980545307))): {"state": 0}}}
                }
            },
        }


@pytest.mark.asyncio
async def test_collectible_node_hashes_are_unsigned() -> None:
    service = CollectionService(_NodeBungie(), _CollectionManifest(), _CollectionResolver())

    payload = await service.get_collectible_node_status("p", 2157285411, character="hunter")

    row = payload["items"][0]
    assert row["item_hash"] == to_unsigned(to_signed(2230338236)) > 0
    assert row["collectible_hash"] == to_unsigned(to_signed(2980545307)) > 0
    assert payload["counts"]["acquired"] == 1


# ── ④ artifact 不带名字也要给 current_artifact ─────────────────────────


class _ArtifactManifest:
    def get_all_artifacts(self):
        return [{"name": "NPA斥力调节器", "hash": 1}]

    def get_current_artifact(self):
        return {"name": "好奇之器", "tiers": [{"mods": [{"name": "反屏障手炮", "hash": 2}]}]}


def test_artifact_list_includes_the_current_artifact() -> None:
    service = object.__new__(ArtifactService)
    service._manifest = _ArtifactManifest()

    payload = service.get_seasonal_artifact("")

    assert payload["artifacts"] and payload["current_artifact"]["name"] == "好奇之器"


# ── ⑤ 报错不叠句号 ───────────────────────────────────────────────────


def test_error_messages_do_not_double_the_period() -> None:
    subclass = SubclassError("职业缺失或不认识（给的是 ''），支持 hunter/warlock/titan 或中文职业名。")

    assert "。。" not in str(subclass)
    assert str(subclass).endswith("名。")
    assert str(SubclassError("没有句号")) == "子职业操作失败：没有句号。"
    assert "。。" not in str(APIError("读取收藏品。", "补充说明。"))


# ── ⑥ search 未命中要说"没找到" ────────────────────────────────────────


def _inventory_ctx(rows: list[dict]):
    service = SimpleNamespace(search_items=AsyncMock(return_value={"query": "x", "items": rows}))
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"inventory_svc": service, "starside_svc": None}
        )
    )


@pytest.mark.asyncio
async def test_inventory_search_says_not_found_for_zero_hits() -> None:
    response = await inventory_assistant(
        intent="search", item_name="绝对不存在的物品", ctx=_inventory_ctx([])
    )

    assert response["ok"] is True
    assert "没找到" in response["summary"] and "绝对不存在的物品" in response["summary"]


@pytest.mark.asyncio
async def test_inventory_search_keeps_the_generic_summary_when_it_hits() -> None:
    response = await inventory_assistant(
        intent="search", item_name="无感", ctx=_inventory_ctx([{"item_instance_id": "1"}])
    )

    assert response["summary"] == "已搜索物品。"


# ── ⑦ item 传 None 不能裸抛 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_armor_item_rejects_none_instance_id_with_a_clean_error() -> None:
    with pytest.raises(InvalidArgumentError):
        await branches.armor_item(SimpleNamespace(), "p", None)
