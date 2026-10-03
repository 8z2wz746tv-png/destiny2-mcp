"""配装快照里的"账号护甲现场"：存快照时把**没穿着**的件也拍下来，还原时逐格写回。

真机 2026-10-03 的坑：`save` 只拍了当时穿着的 5 件，而测试里被 `equip_build` 从仓库搬进来并改写的
「黎明副歌」与「光芒领主胸甲」不在快照里 —— 用 `equip_loadout` 还原时它们"回不去"，有 3 格模组
只能手工近似恢复（结果不是逐格原样）。两条守门：①存快照时账号里的护甲全拍下来；②还原时按快照
逐格比对、只写对不上的格，且**只动这一位角色身上**的件（仓库/别的角色上的要如实点名没动）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from destiny_mcp.models import Loadout, LoadoutArmorState, LoadoutItem, LoadoutOperationResult
from destiny_mcp.services.loadout_armor_state import restore_after_equip
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService
from destiny_mcp.services.loadout_service import LoadoutService

HELMET_BUCKET = 3448274439
VAULT_BUCKET = 138197802   # 仓库里的件 `bucketHash` 是"仓库"桶，不是头盔/胸甲桶（真机实测）
CHEST_BUCKET = 14239492
WEAPON_BUCKET = 1498876634
SUBCLASS_BUCKET = 3284755031
GENERAL_MOD_CATEGORY = 2487827355   # enhancements.v2_general（属性模组）
HELMET_MOD_CATEGORY = 2912171003    # enhancements.v2_head（头盔模组）

# 真机那几个实例号（2026-10-03 那次运行）：面具是当时穿着的，黎明副歌在仓库里、
# 后被 `equip_build` 搬进来改写
WORN_MASK = "6917530188460608169"
DAWN_CHORUS = "6917530195784809558"
WORN_MASK_HASH = 1025347717
DAWN_CHORUS_HASH = 3767088557
VAULT_CHEST = "6917530185677806611"
VAULT_CHEST_HASH = 3094263125
GRENADE_MOD = 1435557120     # 手雷模组（属性模组，3 点）
HELMET_MOD = 856936828       # 头盔模组（现装着）
SPECIAL_AMMO_FINDER = 2620835322  # 特殊武器弹药搜寻者：测试里被写进黎明副歌的那一颗


def _mod(category: int, cost: int) -> dict:
    return {"plug": {"plugCategoryHash": category, "energyCost": {"energyCost": cost}}}


class _Manifest:
    """按真形状给：护甲定义带 `sockets.socketEntries`，插件带 `plug.plugCategoryHash` + 能量。"""

    def __init__(self) -> None:
        self.definitions = {
            WORN_MASK_HASH: {"sockets": {"socketEntries": [{"singleInitialItemHash": 0}]}},
            DAWN_CHORUS_HASH: {"sockets": {"socketEntries": [{"singleInitialItemHash": 0}]}},
            VAULT_CHEST_HASH: {"sockets": {"socketEntries": [{"singleInitialItemHash": 0}]}},
            GRENADE_MOD: _mod(GENERAL_MOD_CATEGORY, 3),
            HELMET_MOD: _mod(HELMET_MOD_CATEGORY, 1),
            SPECIAL_AMMO_FINDER: _mod(HELMET_MOD_CATEGORY, 3),
        }
        self.items = {
            WORN_MASK_HASH: {"itemType": 2, "tier": 5, "bucketTypeHash": HELMET_BUCKET, "name": "光芒领主面具"},
            DAWN_CHORUS_HASH: {"itemType": 2, "tier": 5, "bucketTypeHash": HELMET_BUCKET, "name": "黎明副歌"},
            VAULT_CHEST_HASH: {"itemType": 2, "tier": 5, "bucketTypeHash": CHEST_BUCKET, "name": "光芒领主胸甲"},
            201: {"itemType": 3, "tier": 5, "bucketTypeHash": WEAPON_BUCKET, "name": "测试手炮"},
            301: {"itemType": 16, "tier": 0, "bucketTypeHash": SUBCLASS_BUCKET, "name": "棱镜"},
            GRENADE_MOD: {"itemType": 19, "name": "手雷模组"},
            HELMET_MOD: {"itemType": 19, "name": "头盔模组"},
            SPECIAL_AMMO_FINDER: {"itemType": 19, "name": "特殊武器弹药搜寻者"},
        }

    def get_item_info(self, item_hash: int) -> dict | None:
        return self.items.get(item_hash)

    def get_item_name(self, item_hash: int) -> str:
        return (self.items.get(item_hash) or {}).get("name", f"#{item_hash}")

    def get_english_name(self, item_hash: int) -> str:
        return f"Item {item_hash}"

    def bucket_name(self, bucket_hash: int) -> str:
        return {HELMET_BUCKET: "Helmet", CHEST_BUCKET: "Chest Armor"}.get(bucket_hash, "")

    def item_type_name(self, item_type: int) -> str:
        return {2: "Armor", 3: "Weapon", 16: "Subclass", 19: "Mod"}.get(item_type, "Unknown")

    def get_item_definition(self, item_hash: int) -> dict | None:
        return self.definitions.get(item_hash)

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return {
            GRENADE_MOD: "enhancements.v2_general",
            HELMET_MOD: "enhancements.v2_head",
            SPECIAL_AMMO_FINDER: "enhancements.v2_head",
        }.get(plug_hash, "weapon.frame")


def _resolver(profile: dict) -> SimpleNamespace:
    return SimpleNamespace(
        resolve_player=AsyncMock(
            return_value={"membership_id": "player", "membership_type": 3}
        ),
        resolve_character_id=AsyncMock(return_value="char-1"),
        get_profile=AsyncMock(return_value=profile),
    )


def _save_profile() -> dict:
    """存快照那一刻：身上穿着一套（面具），仓库里躺着会被测试搬走的那两件。"""
    return {
        "characters": {"data": {"char-1": {"classType": 2}}},   # 2 = warlock
        "profileInventory": {"data": {"items": [
            {"itemInstanceId": DAWN_CHORUS, "itemHash": DAWN_CHORUS_HASH, "bucketHash": VAULT_BUCKET},
            {"itemInstanceId": VAULT_CHEST, "itemHash": VAULT_CHEST_HASH, "bucketHash": VAULT_BUCKET},
            {"itemInstanceId": "weapon-1", "itemHash": 201, "bucketHash": VAULT_BUCKET},
        ]}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "characterEquipment": {"data": {"char-1": {"items": [
            {"itemInstanceId": WORN_MASK, "itemHash": WORN_MASK_HASH, "bucketHash": HELMET_BUCKET},
            {"itemInstanceId": "subclass-1", "itemHash": 301, "bucketHash": SUBCLASS_BUCKET},
        ]}}},
        "itemComponents": {"sockets": {"data": {
            WORN_MASK: {"sockets": [{"plugHash": GRENADE_MOD}, {"plugHash": HELMET_MOD}]},
            # 仓库里那件的现场：0 号格是头盔模组，1 号格是空的
            DAWN_CHORUS: {"sockets": [{"plugHash": HELMET_MOD}, {"plugHash": 0}]},
            VAULT_CHEST: {"sockets": [{"plugHash": GRENADE_MOD}]},
        }}},
    }


@pytest.mark.asyncio
async def test_save_snapshots_armor_that_is_not_worn(tmp_path) -> None:
    """**没穿着**的件也要在快照里（真机那次丢的就是仓库里被搬进来的那两件）。

    注入验证：把 `collect_armor_state` 的调用去掉（快照只拍身上那几件），这条立刻变红 ——
    `armor_state` 里没有 `DAWN_CHORUS`。
    """
    service = LoadoutService(
        MagicMock(), _Manifest(), _resolver(_save_profile()), tmp_path / "loadouts.json"
    )

    result = await service.save_loadout("player", "装备测试前快照", "warlock")

    assert result.success is True
    # 从盘上读回来：形状要能落盘再读回（这是"快照"能不能用的前提）
    saved = service._load_local()[0]
    state = {row.item_instance_id: row for row in saved.armor_state}
    assert DAWN_CHORUS in state, "仓库里那件（当时没穿着）必须在快照里，否则还原时回不去"
    assert state[DAWN_CHORUS].item_hash == DAWN_CHORUS_HASH, "留着 hash 才认得出是哪一件"
    assert state[DAWN_CHORUS].mod_sockets == {0: HELMET_MOD}, (
        "空插槽要按默认插件记着（'这一格本来是空的'也是要还原的状态）"
    )
    assert VAULT_CHEST in state
    # 身上那几件在 `items` 里：同一个事实不许存两遍
    assert WORN_MASK not in state
    assert [item.item_instance_id for item in saved.items] == [WORN_MASK]
    assert "weapon-1" not in state, "武器不进护甲现场（这条路只写护甲模组）"


def _restore_loadout() -> Loadout:
    """快照：面具（items）+ 仓库里那两件的现场（armor_state）。"""
    return Loadout(
        id="snap-1",
        name="装备测试前快照",
        character="warlock",
        items=[LoadoutItem(
            item_hash=WORN_MASK_HASH, name="光芒领主面具", slot="helmet",
            item_instance_id=WORN_MASK, mod_sockets={0: GRENADE_MOD, 1: HELMET_MOD},
        )],
        armor_state=[
            LoadoutArmorState(
                item_instance_id=DAWN_CHORUS, item_hash=DAWN_CHORUS_HASH,
                mod_sockets={0: HELMET_MOD},
            ),
            LoadoutArmorState(
                item_instance_id=VAULT_CHEST, item_hash=VAULT_CHEST_HASH,
                mod_sockets={0: GRENADE_MOD},
            ),
        ],
    )


def _live_profile() -> dict:
    """还原那一刻：黎明副歌被测试搬上身并改写过（0 号格换成了搜寻者），仓库那件没被动。"""
    return {
        "characters": {"data": {"char-1": {"classType": 2}}},
        "profileInventory": {"data": {"items": [
            # 还在仓库里（真机那次还原时它已经不在这一位角色身上）
            {"itemInstanceId": VAULT_CHEST, "itemHash": VAULT_CHEST_HASH, "bucketHash": VAULT_BUCKET},
        ]}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "characterEquipment": {"data": {"char-1": {"items": [
            {"itemInstanceId": WORN_MASK, "itemHash": WORN_MASK_HASH, "bucketHash": HELMET_BUCKET},
            {"itemInstanceId": DAWN_CHORUS, "itemHash": DAWN_CHORUS_HASH, "bucketHash": HELMET_BUCKET},
        ]}}},
        "itemComponents": {
            "sockets": {"data": {
                WORN_MASK: {"sockets": [{"plugHash": GRENADE_MOD}, {"plugHash": HELMET_MOD}]},
                DAWN_CHORUS: {"sockets": [{"plugHash": SPECIAL_AMMO_FINDER}, {"plugHash": 0}]},
                VAULT_CHEST: {"sockets": [{"plugHash": GRENADE_MOD}]},
            }},
            "instances": {"data": {
                DAWN_CHORUS: {"energy": {"energyCapacity": 11, "energyUsed": 1}},
            }},
        },
    }


def _equipment_service(profile: dict) -> LoadoutEquipmentService:
    class _Host(LoadoutEquipmentService):
        pass

    service = _Host(MagicMock(), _Manifest(), _resolver(profile))  # type: ignore[arg-type]
    service._insert_armor_mod = AsyncMock(return_value={"ErrorCode": 1})  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_restore_writes_back_the_piece_that_was_not_worn() -> None:
    """被改动、当时没穿着的那一件，还原时按快照逐格写回（只写对不上的那一格）。

    注入验证：把 `_restore_all` 里"比对后写回"改成不比对（或直接返回空），这条立刻变红
    （既没有 `按快照还原` 的那一步，`_insert_armor_mod` 也没被调用）。
    """
    service = _equipment_service(_live_profile())
    result = LoadoutOperationResult(success=True, loadout_name="装备测试前快照", message="已装备")

    out = await restore_after_equip(service, "player", _restore_loadout(), result)

    writes = [
        (call.args[1], call.args[2]) for call in service._insert_armor_mod.await_args_list
    ]
    assert writes == [(HELMET_MOD, 0)], (
        "只该写黎明副歌那 0 号格（被换成了搜寻者）；仓库里那件与快照一致，不该动"
    )
    details = [step.detail for step in out.steps]
    assert any("按快照还原" in detail and "黎明副歌" in detail for detail in details), details
    assert all(step.success for step in out.steps), details
    assert out.success is True, "还原做全了就不该把整套配装的结论降级"


@pytest.mark.asyncio
async def test_restore_does_not_touch_armor_that_is_elsewhere() -> None:
    """快照里与现场不一致、但**不在这一位角色身上**的件：不动它，而且要如实点名。

    这条守的是"别把整个账号的护甲改一遍"：穿一套旧快照不该动仓库与别的角色上的件。
    """
    profile = _live_profile()
    # 仓库那件也被改过（与快照不一致），但它不在这一位角色身上
    profile["itemComponents"]["sockets"]["data"][VAULT_CHEST] = {
        "sockets": [{"plugHash": SPECIAL_AMMO_FINDER}]
    }
    service = _equipment_service(profile)
    result = LoadoutOperationResult(success=True, loadout_name="装备测试前快照", message="已装备")

    out = await restore_after_equip(service, "player", _restore_loadout(), result)

    written_ids = {call.args[0] for call in service._insert_armor_mod.await_args_list}
    assert VAULT_CHEST not in written_ids, "仓库里的件不许动 —— 那不是'穿配装'，是'回滚账号'"
    assert written_ids == {DAWN_CHORUS}
    skipped = [step for step in out.steps if step.action == "armor_restore_skipped"]
    assert len(skipped) == 1 and skipped[0].success is False
    assert "光芒领主胸甲" in skipped[0].detail and "vault" in skipped[0].detail
    assert out.success is False, "还原没做全就要如实降级（别报成'已装备'）"
    # 话术必须**分开说**：主装备生效了、附加还原没做全 —— 两件事，别写成"配装未完全生效"
    # （真机 2026-10-03：装备确实穿上了，那句读起来却像"没穿上"）。
    assert "装备已生效" in out.message, out.message
    assert "附加还原" in out.message and "没做全" in out.message, out.message
    assert "未完全生效" not in out.message


@pytest.mark.asyncio
async def test_restore_is_a_noop_without_a_snapshot_state() -> None:
    """没有护甲现场的快照（官方槽位、求解候选）：一步都不写、也不多读一次 profile。"""
    service = _equipment_service(_live_profile())
    result = LoadoutOperationResult(success=True, loadout_name="官方槽", message="已装备")
    loadout = Loadout(id="x", name="官方槽", character="warlock", items=[])

    out = await restore_after_equip(service, "player", loadout, result)

    assert out is result and out.steps == []
    service._insert_armor_mod.assert_not_awaited()
    service._resolver.get_profile.assert_not_awaited()
