"""② `inventory_assistant(intent="mods")`：**一次调用**读回已装备护甲的插槽。

为什么要它（真机/语料实测）：测试协议里的"独立回读"以前是逐件
`inventory_assistant(intent="item")` —— 每件都要重读整份 profile（实测 13.9 秒/次），
五件 ≈ 70 秒。而组件 305 一次就覆盖**全账号**的已装插槽（实测 3.47 MB / 0.85 秒），
"哪五件在装备位上"也在同一次响应的组件 205 里 —— 所以这件事本来一次调用就够。

与两条已有路径的分工（不许混）：
- 写入路径的 `steps[].verify` 是**写入流程内部的自证**；这里是不信任回执时的**独立回读**；
- `intent="item"` 是**一件**的完整载荷（能量/三层属性/词条/能不能调谐）；这里是**多件**的
  插槽快照，槽行形状与它相同（同一个形状工厂 `armor_payload.socket_rows`）。

这个文件钉四件事：一次调用（不是 N+1）、覆盖五件、形状与 `item` 一致、参数归属正确。
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.services.inventory_service import InventoryService  # noqa: E402
from destiny_mcp.tools import _armor_branches  # noqa: E402

HELMET_BUCKET = 3448274439
GAUNTLET_BUCKET = 3551918588
CHEST_BUCKET = 14239492
LEGS_BUCKET = 20886954
CLASS_BUCKET = 1585787867
WEAPON_BUCKET = 1498876634

#: 各部位的物品 hash → 名称；金装一件（头盔）用来钉 `is_exotic`
ARMOR_NAMES = {101: "测试头盔", 102: "测试臂铠", 103: "测试胸甲", 104: "测试腿甲", 105: "测试职业物品"}
EXOTIC_HELMET = 101
#: 插槽里装着的两颗：一颗属性模组（一般插槽）+ 一颗部位功能模组（头盔插槽）
STAT_MOD = 5001
HEAD_MOD = 5002
PLUG_CATEGORY = {STAT_MOD: "enhancements.v2_general", HEAD_MOD: "enhancements.v2_head"}


class _Manifest:
    """够 `get_equipped_armor_mods` 用的最小 manifest 面。"""

    def get_item_info(self, item_hash: int) -> dict:
        slots = {
            101: HELMET_BUCKET, 102: GAUNTLET_BUCKET, 103: CHEST_BUCKET,
            104: LEGS_BUCKET, 105: CLASS_BUCKET,
        }
        if item_hash in slots:
            return {
                "classType": 2,
                "tier": 6 if item_hash == EXOTIC_HELMET else 5,
                "bucketTypeHash": slots[item_hash],
                "itemType": 2,
                "icon": "",
            }
        return {"classType": 2, "tier": 5, "bucketTypeHash": WEAPON_BUCKET, "itemType": 3}

    def get_item_name(self, item_hash: int) -> str:
        return ARMOR_NAMES.get(item_hash, f"#插件{item_hash}")

    def get_item_definition(self, item_hash: int) -> dict:
        category = PLUG_CATEGORY.get(item_hash, "")
        return {
            "plug": {
                "plugCategoryIdentifier": category,
                "energyCost": {"energyCost": 3 if item_hash == STAT_MOD else 1},
            },
            "displayProperties": {"name": self.get_item_name(item_hash)},
            "sockets": {"socketEntries": []},
        }


def _equipped_item(slot_hash: int, instance: str, item_hash: int) -> dict:
    return {"itemInstanceId": instance, "itemHash": item_hash, "bucketHash": slot_hash}


def _profile(*, characters: dict | None = None) -> dict:
    """两位角色：术士身上五件护甲 + 一把武器；猎人身上只有一件护甲（用来钉"不是 5 件"）。"""
    warlock = [
        _equipped_item(HELMET_BUCKET, "w-helm", EXOTIC_HELMET),
        _equipped_item(GAUNTLET_BUCKET, "w-gaunt", 102),
        _equipped_item(CHEST_BUCKET, "w-chest", 103),
        _equipped_item(LEGS_BUCKET, "w-legs", 104),
        _equipped_item(CLASS_BUCKET, "w-class", 105),
        _equipped_item(WEAPON_BUCKET, "w-gun", 999),
    ]
    hunter = [_equipped_item(HELMET_BUCKET, "h-helm", 101)]
    return {
        # `_resolve_and_fetch` 会判"只有装备位、没有背包"= 缺 scope：给一件背包物品，
        # 让这份夹具看起来像真响应（读回本身不依赖背包）。
        "profileInventory": {"data": {"items": []}},
        "characterInventories": {"data": {
            "char-warlock": {"items": [_equipped_item(HELMET_BUCKET, "w-spare", 101)]},
            "char-hunter": {"items": []},
        }},
        "characters": {"data": characters or {
            "char-warlock": {"classType": 2},
            "char-hunter": {"classType": 1},
        }},
        "characterEquipment": {"data": {
            "char-warlock": {"items": warlock},
            "char-hunter": {"items": hunter},
        }},
        "itemComponents": {
            "instances": {"data": {
                "w-helm": {"energy": {"energyCapacity": 11, "energyUsed": 4},
                           "primaryStat": {"value": 2010}},
                "w-gaunt": {"energy": {"energyCapacity": 10, "energyUsed": 0}},
            }},
            "sockets": {"data": {
                "w-helm": {"sockets": [
                    {"plugHash": STAT_MOD},
                    {"plugHash": HEAD_MOD},
                    {"plugHash": 0},
                ]},
                "w-gaunt": {"sockets": [{"plugHash": 0}]},
            }},
        },
    }


class _Resolver:
    """`InventoryService._resolve_and_fetch` 走的就是这三个方法；计数按 profile 读取次数。"""

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.profile_reads = 0

    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "46116860", "membership_type": 3}

    async def get_profile(self, mid, mtype, components) -> dict:
        self.profile_reads += 1
        return self.profile


def _service(profile: dict | None = None) -> tuple[InventoryService, _Resolver]:
    resolver = _Resolver(_profile() if profile is None else profile)
    service = InventoryService(MagicMock(), _Manifest(), resolver)  # type: ignore[arg-type]
    return service, resolver


# ── 一次调用拿全（这是 ② 的全部意义）────────────────────────────────────


@pytest.mark.asyncio
async def test_one_profile_read_covers_every_equipped_piece() -> None:
    """五件护甲的插槽**一次 profile 读取**就够 —— 不许退化成 N+1。

    注入验证：把 `get_equipped_armor_mods` 改成逐件调 `get_armor_item`（或每件读一次 profile）
    → 本用例红（`profile_reads` 不再是 1）。
    """
    service, resolver = _service()

    payload = await service.get_equipped_armor_mods("Tester#1234", "warlock")

    assert resolver.profile_reads == 1, "一次调用 = 一次 profile 读取"
    block = payload["characters"][0]
    assert block["character"] == "warlock" and block["class_display"] == "术士"
    assert block["item_count"] == 5, "身上五件护甲，武器不算"
    assert [row["slot"] for row in block["items"]] == [
        "helmets", "gauntlets", "chests", "legs", "class_items"
    ]
    assert [row["slot_key"] for row in block["items"]] == [
        "helmet", "gauntlets", "chest", "legs", "class_item"
    ]


@pytest.mark.asyncio
async def test_socket_rows_match_the_single_item_shape() -> None:
    """槽行必须与 `intent="item"` 的 `armor.sockets` 同形状（同一个形状工厂）。

    对不上的后果：核对要用两套读法读同一件事，两边迟早给出不同答案。
    """
    service, _ = _service()

    payload = await service.get_equipped_armor_mods("Tester#1234", "warlock")
    helmet = payload["characters"][0]["items"][0]

    assert helmet["is_exotic"] is True
    assert helmet["energy"] == {"capacity": 11, "used": 4}
    assert helmet["power"] == 2010
    mods = helmet["mods"]
    assert [row["index"] for row in mods] == [0, 1, 2]
    assert mods[0] == {
        "index": 0, "kind": "general", "editable": True, "plug_hash": STAT_MOD,
        "name": "#插件5001", "energy_cost": 3, "empty": False,
    }
    assert mods[1]["kind"] == "helmet", "部位功能模组按 plugCategoryIdentifier 分类"
    assert mods[2]["empty"] is True and mods[2]["plug_hash"] is None


@pytest.mark.asyncio
async def test_without_a_character_it_reads_every_character() -> None:
    """不给 `character` = 三位角色都读（各带职业标签，调用方自己挑）。"""
    service, resolver = _service()

    payload = await service.get_equipped_armor_mods("Tester#1234")

    assert resolver.profile_reads == 1, "读全部角色同样只读一次"
    assert [block["character"] for block in payload["characters"]] == ["warlock", "hunter"]
    assert payload["characters"][1]["item_count"] == 1, "猎人身上只装备了一件护甲"


@pytest.mark.asyncio
async def test_unknown_character_is_a_clean_error() -> None:
    """账号里没有这一位角色：报 CharacterNotFoundError，不是返回空块。"""
    from destiny_mcp.exceptions import CharacterNotFoundError

    service, _ = _service()

    with pytest.raises(CharacterNotFoundError):
        await service.get_equipped_armor_mods("Tester#1234", "titan")


# ── 工具面 ────────────────────────────────────────────────────────────


def _tool_svc(payload: dict) -> dict:
    service = MagicMock()
    service.get_equipped_armor_mods = AsyncMock(return_value=payload)
    return {"inventory_svc": service}


@pytest.mark.asyncio
async def test_tool_branch_reports_what_it_read_and_warns_on_missing_pieces() -> None:
    """工具面：说清"一次调用读回了几件"，某位角色不足 5 件要点名。"""
    service = _service()[0]
    payload = await service.get_equipped_armor_mods("Tester#1234")

    response = await _armor_branches.equipped_armor_mods(
        _tool_svc(payload), "Tester#1234", ""
    )

    assert response["ok"] is True
    assert "一次调用" in response["summary"]
    assert "6 件" in response["summary"], "术士 5 件 + 猎人 1 件"
    assert response["data"]["equipped_armor"]["characters"][0]["item_count"] == 5
    assert any("猎人 1 件" in warning for warning in response["warnings"])


@pytest.mark.asyncio
async def test_dispatch_routes_mods_to_the_readback_and_item_stays_single_piece(monkeypatch) -> None:
    """分派：`mods` 走独立回读、`item` 仍走单件载荷；互不串台。"""
    service = _service()[0]
    payload = await service.get_equipped_armor_mods("Tester#1234")
    svc = _tool_svc(payload)

    single = MagicMock()
    single.get_armor_item = AsyncMock()
    svc["inventory_svc"] = SimpleNamespace(
        get_equipped_armor_mods=AsyncMock(return_value=payload),
        get_armor_item=single.get_armor_item,
    )

    await _armor_branches.armor_read(svc, "Tester#1234", "mods", "", "warlock")
    svc["inventory_svc"].get_equipped_armor_mods.assert_awaited_once_with("Tester#1234", "warlock")

    # `item` 那条路不进回读（`armor_item` 会自己报缺 ID）
    from destiny_mcp.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        await _armor_branches.armor_read(svc, "Tester#1234", "item", "   ", "")
    svc["inventory_svc"].get_equipped_armor_mods.assert_awaited_once()
