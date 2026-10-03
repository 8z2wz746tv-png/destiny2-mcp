"""候选指纹（`build/snapshot_version`）：哪些账号变化该作废手里的候选，哪些不该。

求解不只看六维：**功能模组占掉的能量**决定这件还剩多少能给属性模组。指纹以前只认六维
（组件 304），于是"在游戏里换一颗不改六维的功能模组"（弹药搜寻者这种）不会作废候选 ——
`equip_build` 按**过期的能量预算**腾模组：多清一格，或者属性模组装不下（12/11 预检失败并
整批回滚，2026-09-22 真机踩过）。这个文件钉三件事：

1. **换一颗不改六维的功能模组 → 旧候选必须作废**（`installed_mod_energy` 进指纹）。
   注入验证：把指纹里那行 `installed_mod_energy` 去掉 → 第 1 条红；
2. **只加这一项**：请求参数里的"预留额度"不进指纹 —— 它来自求解请求，`equip_build` 那一次
   读取没有它（预算是 `max(已装, 预留)`），进去会让**每一套带功能模组的配装**在确认时被
   自己判成"库存变了"。注入验证：把指纹那行换成 `energy_used_by_other_mods` → 第 2 条红；
3. **其余执行现场不进指纹**（各格占用、当前穿着的金装、`execution_blocker`）：它们决定
   "这件这次装不装得上"，写入前按当时的现场复检（`services/build_execution_guard`）。
   进来的代价是"刚求解完、捡到一件护甲 / 换件金装就被告知库存变了、请重新求解"。
"""

from __future__ import annotations

from destiny_mcp.build.execution_feasibility import BucketUsage, ExecutionFacts
from destiny_mcp.build.models import InventorySnapshot
from destiny_mcp.build.snapshot_version import snapshot_version

#: 部位桶 hash（`DestinyInventoryBucketDefinition`，真机口径）
HELMET_BUCKET = 3448274439
#: 「充沛」的两个同名版本（1 点 / 3 点），真机 hash（见 tests/test_functional_mods_copied.py）
CHEAP_MOD = 2414626352
PRICEY_MOD = 4048902980
_MOD_COSTS = {CHEAP_MOD: 1, PRICEY_MOD: 3}


class _Manifest:
    """读一件护甲 + 一颗部位模组要的最小面。"""

    def get_item_info(self, item_hash: int) -> dict:
        return {
            "classType": 1,
            "tier": 5,
            "bucketTypeHash": HELMET_BUCKET,
            "itemType": 2,
            "itemTypeNameDisplay": "头盔",
            "icon": "",
        }

    def get_item_name(self, item_hash: int) -> str:
        return "测试头盔"

    def get_item_definition(self, item_hash: int) -> dict:
        return {
            "plug": {
                "plugCategoryIdentifier": "enhancements.v2_head",
                "energyCost": {"energyCost": _MOD_COSTS.get(item_hash, 1)},
            },
            "displayProperties": {"name": "#"},
            "sockets": {"socketEntries": [{"singleInitialItemHash": 0}]},
        }

    def get_set_bonus_info(self, item_hash: int) -> None:
        return None

    def get_bucket_definition(self, bucket_hash: int) -> dict:
        return {"itemCount": 10, "displayProperties": {"name": "头盔"}}


def _snapshot(*, installed_mod: int = CHEAP_MOD, reserved_mod_energy: int | None = None):
    """一件**已装着一颗功能模组**的 T5 头盔（六维固定，只有那颗模组不同）。"""
    profile = {
        "profileInventory": {"data": {"items": []}},
        "characters": {"data": {"char-1": {"classType": 1}}},
        "characterInventories": {"data": {"char-1": {"items": []}}},
        "characterEquipment": {"data": {"char-1": {"items": [
            {"itemInstanceId": "helmet-1", "itemHash": 100, "bucketHash": HELMET_BUCKET},
        ]}}},
        "itemComponents": {
            "instances": {"data": {"helmet-1": {"energy": {"energyCapacity": 11, "energyUsed": 0}}}},
            "stats": {"data": {"helmet-1": {"stats": {}}}},
            "sockets": {"data": {"helmet-1": {"sockets": [{"plugHash": installed_mod}]}}},
        },
    }
    return InventorySnapshot.from_profile(
        profile,
        _Manifest(),
        "hunter",
        reserved_mod_energy=(
            None if reserved_mod_energy is None else {"helmet": reserved_mod_energy}
        ),
    )


def test_swapping_a_non_stat_mod_invalidates_the_candidate() -> None:
    """在游戏里换一颗**不改六维**的功能模组 → 旧候选必须作废。

    不作废的后果（真机 2026-09-22）：`equip_build` 拿求解时的旧能量预算去腾模组 ——
    多清一格，或者属性模组装不下（12/11 预检失败 → 整批回滚）。
    """
    cheap = _snapshot(installed_mod=CHEAP_MOD)
    pricey = _snapshot(installed_mod=PRICEY_MOD)

    assert cheap.helmets[0].stats == pricey.helmets[0].stats, "换的这颗功能模组不改六维"
    assert cheap.helmets[0].installed_mod_energy == 1
    assert pricey.helmets[0].installed_mod_energy == 3
    assert snapshot_version(cheap) != snapshot_version(pricey), (
        "换了一颗功能模组却没作废候选：写入会按过期的能量预算腾模组"
    )


def test_reserved_energy_from_the_request_is_not_part_of_the_fingerprint() -> None:
    """请求里的预留额度**不进**指纹：`equip_build` 那一刻复算不出来它。

    预算是 `max(已装, 预留)`，而预留来自求解请求参数。指纹若认那个 max，`find`（带预留 9）
    与 `equip_build`（只读账号，已装 1）算出来的就是两个值 —— 每一套带功能模组的社区配装
    都会在确认时被判成"库存变了、请重新求解"。
    """
    plain = _snapshot(reserved_mod_energy=None)
    reserved = _snapshot(reserved_mod_energy=9)

    assert plain.helmets[0].energy_used_by_other_mods == 1, "已装那颗占 1 点"
    assert reserved.helmets[0].energy_used_by_other_mods == 9, "求解预算取 max(1, 9)"
    assert snapshot_version(plain) == snapshot_version(reserved), (
        "预留额度进了指纹：带功能模组的候选会在确认时被自己判成过期"
    )


def test_execution_context_changes_do_not_invalidate_the_candidate() -> None:
    """格满 / 当前金装这类**执行现场**不进指纹：它们由写入前的复检负责。

    进指纹的代价是"刚求解完、捡到一件护甲就被告知库存变了、请重新求解"——而这两条前提本来
    就有明确出路（腾一格 / 先顶下冲突的金装），写入前按当时的现场再说一遍就够了。
    注入验证：把 `model_dump()` 整个快照写进指纹 → 本用例红。
    """
    snapshot = _snapshot()
    before = snapshot_version(snapshot)

    snapshot.execution = ExecutionFacts(
        character="hunter",
        worn_exotic_slot="chests",
        worn_exotic_name="星火协议",
        buckets={"helmets": BucketUsage(capacity=10, used=10)},
    )
    snapshot.helmets[0].execution_blocker = "「测试头盔」是异域头盔，而猎人正穿着异域「星火协议」"

    assert snapshot_version(snapshot) == before, (
        "执行现场进了指纹：捡到一件护甲 / 换件金装就会作废手里的候选"
    )
