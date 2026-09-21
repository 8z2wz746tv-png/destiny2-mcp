"""装备编排的四种预检、计划顺序、中间件挑选与"无确认不写"。

只碰替身：`equip_planner` 是纯逻辑 + 一次 profile 读取，manifest 只要
`get_item_definition`/`get_item_name` 两个方法，profile 就是一个字典。
"""

from __future__ import annotations

import pytest

from destiny_mcp.models import InventoryItem
from destiny_mcp.services.equip_planner import (
    BucketCapacity,
    EquipPlanRequest,
    _bucket_capacity,
    load_plan_request,
    plan_equip,
    plan_for_player,
)
from destiny_mcp.services.item_parser import parse_items_from_profile

EXOTIC = 6
LEGENDARY = 5

# 装备槽 hash —— 物品定义的 `equippingBlock.equipmentSlotTypeHash`（2026-09-21 从 Manifest 实录）。
SLOT_HASH: dict[str, int] = {
    "helmet": 3448274439,
    "gauntlets": 3551918588,
    "chest": 14239492,
    "legs": 20886954,
    "class_item": 1585787867,
    "kinetic": 1498876634,
    "energy": 2465295065,
    "power": 953998645,
}
WEAPON_SLOT_KEYS = frozenset({"kinetic", "energy", "power"})
# 互斥组（Manifest 的 `uniqueLabel`）：异域武器与异域护甲分属两组，**互不冲突**（ADR-011）。
EXOTIC_ARMOR = "exotic_armor"
EXOTIC_WEAPON = "exotic_weapon"


class FakeManifest:
    """给 `_traits` 读的两样：`equippingBlock.uniqueLabel` 与 `equipmentSlotTypeHash`。

    `tiers` 仍按稀有度写（6=异域），替身把它翻成 Manifest 真实的互斥组 —— 测试因此与代码走
    **同一套说法**（ADR-011），而不是自己判"是不是异域"。`slots` 是 hash → 槽位键；没登记的
    hash 不给 `equippingBlock`，等于"定义里读不到槽位"，用来验证退回实例 `slot` 的那条路。
    """

    def __init__(
        self,
        tiers: dict[int, int] | None = None,
        names: dict[int, str] | None = None,
        slots: dict[int, str] | None = None,
    ):
        self.tiers = tiers or {}
        self.names = names or {}
        self.slots = slots or {}
        self.definition_calls: list[int] = []

    def get_item_definition(self, item_hash: int) -> dict | None:
        self.definition_calls.append(item_hash)
        if item_hash not in self.tiers:
            return None
        definition: dict = {"inventory": {"tierType": self.tiers[item_hash]}}
        slot_key = self.slots.get(item_hash)
        if slot_key:
            exotic = self.tiers[item_hash] == EXOTIC
            definition["equippingBlock"] = {
                "uniqueLabel": (
                    (EXOTIC_WEAPON if slot_key in WEAPON_SLOT_KEYS else EXOTIC_ARMOR)
                    if exotic
                    else ""
                ),
                "equipmentSlotTypeHash": SLOT_HASH[slot_key],
            }
        return definition

    def get_item_name(self, item_hash: int) -> str:
        return self.names.get(item_hash, f"物品{item_hash}")


def item(
    instance_id: str,
    name: str,
    *,
    slot: str = "chest",
    location: str = "warlock",
    character_id: str = "char-warlock",
    equipped: bool = False,
    power: int | None = 1800,
    item_hash: int = 1000,
) -> InventoryItem:
    return InventoryItem(
        item_instance_id=instance_id,
        item_hash=item_hash,
        name=name,
        power=power,
        location=location,
        character_id=character_id,
        is_equipped=equipped,
        slot=slot,
        slot_display={"chest": "胸部护甲", "gauntlets": "臂铠"}.get(slot, slot),
    )


def request_for(
    target: InventoryItem,
    *,
    inventory: list[InventoryItem] | None = None,
    capacity: dict[str, BucketCapacity] | None = None,
    character: str = "warlock",
) -> EquipPlanRequest:
    """按真机口径拼请求：`character_inventory` = 装备位 + 背包，谁装备着看 `equipped_keys`。"""
    on_character = list(inventory or [])
    if target.character_id == "char-warlock":
        on_character.append(target)
    return EquipPlanRequest(
        character=character,
        character_id="char-warlock",
        target=target,
        equipped_keys=frozenset(
            entry.item_instance_id for entry in on_character if entry.is_equipped
        ),
        character_inventory=on_character,
        bucket_capacity=capacity or {},
    )


def capacity_for(
    slot: str = "chest", *, capacity: int = 176, used: int = 12
) -> dict[str, BucketCapacity]:
    return {slot: BucketCapacity(capacity=capacity, used=used, equipped=1)}


# ── 预检一：已经在目标角色身上 ────────────────────────────────────────────


def test_already_equipped_is_a_no_op() -> None:
    target = item("i-target", "星火协议", equipped=True, location="warlock")
    plan = plan_equip(request_for(target), FakeManifest())

    assert plan.status == "already_equipped"
    assert plan.steps == []
    assert plan.blockers == []
    assert "无需操作" in plan.message
    assert plan.success is False  # 计划不是执行结果


# ── 预检二：不在目标角色身上 → 先搬 ──────────────────────────────────────


def test_vault_item_gets_a_move_step_before_the_equip() -> None:
    target = item("i-target", "星火协议", location="vault", character_id="")
    manifest = FakeManifest({target.item_hash: LEGENDARY}, {target.item_hash: target.name})

    plan = plan_equip(request_for(target, capacity=capacity_for()), manifest)

    assert plan.status == "ready"
    assert [step.action for step in plan.steps] == ["move", "equip"]
    move, equip = plan.steps
    assert (move.item, move.from_location, move.to_location) == (
        "星火协议", "vault", "warlock",
    )
    assert move.item_instance_id == "i-target"
    assert "只接受在该角色身上" in move.why
    assert equip.to_location == "equipped"
    assert plan.message.startswith("计划：步骤1：")
    assert "步骤2：把「星火协议」穿上" in plan.message


def test_equipped_on_another_character_is_told_before_any_write() -> None:
    target = item(
        "i-target", "星火协议", location="titan", character_id="char-titan",
        equipped=True,
    )
    manifest = FakeManifest({target.item_hash: LEGENDARY}, {target.item_hash: target.name})

    plan = plan_equip(request_for(target, capacity=capacity_for()), manifest)

    assert plan.status == "blocked"
    assert [block.reason for block in plan.blockers] == ["item_equipped"]
    assert plan.steps == []
    assert "正装备在泰坦身上" in plan.message
    assert "不允许移动已装备的物品" in plan.message


# ── 预检三：背包满 → 给数字 ──────────────────────────────────────────────


def test_full_backpack_blocks_with_numbers_and_a_way_out() -> None:
    target = item("i-target", "星火协议", location="vault", character_id="")
    manifest = FakeManifest({target.item_hash: LEGENDARY}, {target.item_hash: target.name})

    plan = plan_equip(
        request_for(target, capacity=capacity_for(capacity=176, used=176)), manifest
    )

    assert plan.status == "blocked"
    block = plan.blockers[0]
    assert block.reason == "inventory_full"
    assert block.numbers == {"used": 176, "capacity": 176, "free": 0}
    assert "176/176" in plan.message
    assert 'intent="get"' in plan.message  # 出路：看该角色背包里现有的
    assert plan.steps == []


def test_unknown_capacity_does_not_invent_a_block() -> None:
    """buckets 组件没给这一类目容量时不拦：让上游去判，别拿默认值编一个"满"。"""
    target = item("i-target", "星火协议", location="vault", character_id="")
    manifest = FakeManifest({target.item_hash: LEGENDARY}, {target.item_hash: target.name})

    plan = plan_equip(request_for(target, capacity={}), manifest)

    assert plan.status == "ready"
    assert [step.action for step in plan.steps] == ["move", "equip"]


# ── 预检四：异域冲突 → 先顶下再装 ────────────────────────────────────────


def test_exotic_conflict_plans_a_downgrade_before_the_target() -> None:
    worn_exotic = item(
        "i-exotic-gloves", "逃逸艺术家", slot="gauntlets", equipped=True,
        item_hash=2000,
    )
    spare_legendary = item(
        "i-legend-gloves", "光芒领主手套", slot="gauntlets", equipped=False,
        power=1750, item_hash=3000,
    )
    target = item("i-target", "星火协议", location="vault", character_id="", item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 3000: LEGENDARY, 4000: EXOTIC},
        {2000: "逃逸艺术家", 3000: "光芒领主手套", 4000: "星火协议"},
        {2000: "gauntlets", 3000: "gauntlets", 4000: "chest"},
    )

    plan = plan_equip(
        request_for(
            target,
            inventory=[worn_exotic, spare_legendary],
            capacity=capacity_for(),
        ),
        manifest,
    )

    assert plan.status == "ready"
    # 顺序是硬性的：先搬（还在仓库）、再顶下、最后才装目标。
    assert [step.action for step in plan.steps] == ["move", "downgrade", "equip"]
    downgrade = plan.steps[1]
    assert downgrade.item == "光芒领主手套"
    assert downgrade.replaces == "逃逸艺术家"
    assert downgrade.to_location == "equipped"
    assert "同类的异域只能装备一件" in downgrade.why
    assert plan.steps[2].item == "星火协议"


def test_downgrade_picks_the_highest_power_legendary_in_the_slot() -> None:
    worn_exotic = item("i-exotic", "逃逸艺术家", slot="gauntlets", equipped=True, item_hash=2000)
    weak = item("i-weak", "弱手套", slot="gauntlets", power=1600, item_hash=3001)
    strong = item("i-strong", "强手套", slot="gauntlets", power=1790, item_hash=3002)
    other_slot = item("i-helmet", "别人的头盔", slot="helmet", power=1800, item_hash=3003)
    target = item("i-target", "星火协议", equipped=False, item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 3001: LEGENDARY, 3002: LEGENDARY, 3003: LEGENDARY, 4000: EXOTIC},
        {},
        {2000: "gauntlets", 3001: "gauntlets", 3002: "gauntlets", 3003: "helmet",
         4000: "chest"},
    )

    plan = plan_equip(
        request_for(target, inventory=[worn_exotic, weak, strong, other_slot]),
        manifest,
    )

    assert plan.status == "ready"
    assert [step.item for step in plan.steps] == ["强手套", "星火协议"]


def test_exotic_vs_exotic_in_the_same_slot_needs_no_downgrade() -> None:
    """同部位换金装不冲突（换下去的那件自然回背包），别凭空多一步。"""
    target = item("i-target", "星火协议", equipped=False, item_hash=4000)
    manifest = FakeManifest({4000: EXOTIC}, {})

    plan = plan_equip(request_for(target), manifest)

    assert plan.status == "ready"
    assert [step.action for step in plan.steps] == ["equip"]


def test_no_legendary_in_that_slot_says_so_and_points_somewhere() -> None:
    worn_exotic = item("i-exotic", "逃逸艺术家", slot="gauntlets", equipped=True, item_hash=2000)
    exotic_spare = item("i-spare", "另一件金手套", slot="gauntlets", item_hash=2001)
    wrong_slot = item("i-helmet", "普通头盔", slot="helmet", item_hash=3003)
    target = item("i-target", "星火协议", equipped=False, item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 2001: EXOTIC, 3003: LEGENDARY, 4000: EXOTIC},
        {},
        {2000: "gauntlets", 2001: "gauntlets", 3003: "helmet", 4000: "chest"},
    )

    plan = plan_equip(
        request_for(target, inventory=[worn_exotic, exotic_spare, wrong_slot]), manifest
    )

    assert plan.status == "blocked"
    assert [block.reason for block in plan.blockers] == ["exotic_conflict"]
    assert plan.steps == []
    assert "没有可用的非异域臂铠" in plan.message
    assert 'armor_slot="gauntlets"' in plan.message
    # 金装不能当中间件：日志里挑候选时看过的定义只该包含同部位的两件
    assert set(manifest.definition_calls) >= {2000, 2001, 4000}


def test_unknown_rarity_does_not_claim_a_conflict() -> None:
    """manifest 查不到定义时不能说"是异域"——没有证据就不加顶下步骤。"""
    target = item("i-target", "星火协议", equipped=False, item_hash=4000)
    manifest = FakeManifest({}, {})  # 任何 hash 都查不到

    plan = plan_equip(request_for(target), manifest)

    assert plan.status == "ready"
    assert [step.action for step in plan.steps] == ["equip"]


# ── ADR-011：冲突按 Manifest 的 uniqueLabel 判，武器与护甲互不冲突 ──────────


def test_exotic_weapon_does_not_conflict_with_worn_exotic_armor() -> None:
    """装异域武器时，身上那件异域护甲**不是**冲突 —— 两类异域可以同时穿。

    这就是 2026-09-21 复发的那个 bug：旧判据"另一个槽的异域就算冲突"会凭空多出一步
    顶下护甲，而真冲突（另一把异域武器）因为武器没有 slot 反而整个被跳过。
    """
    worn_exotic_armor = item(
        "i-exotic-chest", "星火协议", slot="chest", equipped=True, item_hash=2000
    )
    target = item("i-target", "狼毒", slot="power", equipped=False, item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 4000: EXOTIC}, {}, {2000: "chest", 4000: "power"}
    )

    plan = plan_equip(request_for(target, inventory=[worn_exotic_armor]), manifest)

    assert plan.status == "ready"
    assert [step.action for step in plan.steps] == ["equip"]
    assert plan.target_slot == "power"


def test_exotic_weapon_conflicts_with_another_exotic_weapon() -> None:
    """两把异域武器是真冲突：要先用**同槽位**的非异域武器顶下正穿着那把。"""
    worn = item("i-worn", "需求层级", slot="energy", equipped=True, item_hash=2000)
    spare = item("i-spare", "信任", slot="energy", equipped=False, item_hash=3000)
    target = item("i-target", "狼毒", slot="power", equipped=False, item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 3000: LEGENDARY, 4000: EXOTIC},
        {},
        {2000: "energy", 3000: "energy", 4000: "power"},
    )

    plan = plan_equip(request_for(target, inventory=[worn, spare]), manifest)

    assert [step.action for step in plan.steps] == ["downgrade", "equip"]
    assert plan.steps[0].slot == "energy"
    assert plan.steps[0].item == "信任"
    assert plan.steps[0].replaces == "需求层级"


def test_exotic_class_item_conflicts_with_exotic_armor() -> None:
    """异域职业物品在 Manifest 里也是 `exotic_armor`，所以与异域护甲**同组互斥**：
    要装它，得先把正穿着的异域胸甲换成**非异域胸甲**（拿另一件披风顶不了事）。"""
    worn = item("i-worn", "星火协议", slot="chest", equipped=True, item_hash=2000)
    spare_chest = item("i-spare", "光泽胸甲", slot="chest", equipped=False, item_hash=3000)
    target = item("i-target", "相对主义", slot="class_item", equipped=False, item_hash=4000)
    manifest = FakeManifest(
        {2000: EXOTIC, 3000: LEGENDARY, 4000: EXOTIC},
        {},
        {2000: "chest", 3000: "chest", 4000: "class_item"},
    )

    plan = plan_equip(request_for(target, inventory=[worn, spare_chest]), manifest)

    assert [step.action for step in plan.steps] == ["downgrade", "equip"]
    assert plan.steps[0].slot == "chest"
    assert plan.steps[0].replaces == "星火协议"


def test_slot_comes_from_the_definition_not_the_bucket() -> None:
    """槽位由定义给 —— 认不出部位（仓库格 / 武器）时也能判，不必给武器补 `slot`。"""
    target = item("i-target", "狼毒", slot="", equipped=False, item_hash=4000)
    manifest = FakeManifest({4000: EXOTIC}, {}, {4000: "power"})

    plan = plan_equip(request_for(target), manifest)

    assert plan.target_slot == "power"
    assert plan.steps[0].slot == "power"


# ── 无确认不写：预检只读，计划里没有任何写入副作用 ──────────────────────


def test_planning_touches_nothing_writable() -> None:
    target = item("i-target", "星火协议", location="vault", character_id="")
    worn_exotic = item("i-exotic", "逃逸艺术家", slot="gauntlets", equipped=True, item_hash=2000)
    spare = item("i-spare", "光芒领主手套", slot="gauntlets", item_hash=3000)
    manifest = FakeManifest({2000: EXOTIC, 3000: LEGENDARY, 4000: EXOTIC}, {})
    request = request_for(target, inventory=[worn_exotic, spare], capacity=capacity_for())
    before = (request.character, request.target, tuple(request.character_inventory))

    plan = plan_equip(request, manifest)

    # 请求对象没被改：同一份输入再规划一次，结果逐字相同。
    assert (request.character, request.target, tuple(request.character_inventory)) == before
    assert plan.model_dump() == plan_equip(request, manifest).model_dump()
    # 计划本身不宣称成功，也不带 verified（没执行过就没有回读结论）。
    assert plan.success is False
    assert plan.verified is None
    assert plan.unverified_reason == ""
    # 每一步的 success 只表示"这一步可执行"，与执行结果不是一回事。
    assert all(step.success for step in plan.steps)


# ── 读 profile：容量与实例位置 ───────────────────────────────────────────


def _profile_items():
    return [
        {"itemHash": 2000, "itemInstanceId": "i-exotic", "bucketHash": 3551918588},
        {"itemHash": 3000, "itemInstanceId": "i-spare", "bucketHash": 3551918588},
        {
            # 仓库格（138197802）：真机上仓库物品的 bucketHash 就是这个，部位要看定义
            "itemHash": 4000, "itemInstanceId": "i-target", "bucketHash": 138197802,
            "isEquipped": False,
        },
    ]


class _ProfileManifest:
    """`parse_items_from_profile` 要的最小 manifest 面。"""

    # 部位由定义给：2000/3000 是臂铠，4000 是胸甲（真机上仓库条的 bucketHash 是仓库格，
    # 部位只能从定义读，所以这里必须按 hash 分开答）
    _DISPLAY = {2000: "臂铠", 3000: "臂铠", 4000: "胸部护甲"}

    # 容量从桶定义读（真机口径：Manifest 的 DestinyInventoryBucketDefinition.itemCount）。
    # 这里把臂铠类目给成 2 —— 与夹具里"该角色 2 件臂铠（1 穿 1 备）"对齐，free=0。
    _BUCKET_CAPACITY = {3551918588: 2, 14239492: 2}

    def get_bucket_definition(self, bucket_hash: int) -> dict | None:
        total = self._BUCKET_CAPACITY.get(bucket_hash)
        return {"itemCount": total} if total else None

    def get_item_info(self, item_hash: int) -> dict:
        return {
            "itemType": 20,
            "itemTypeNameDisplay": self._DISPLAY.get(item_hash, "胸部护甲"),
            "classType": 2,
        }

    def item_type_name(self, item_type: int) -> str:
        return "Armor"

    def get_item_name(self, item_hash: int) -> str:
        return {2000: "逃逸艺术家", 3000: "光芒备用", 4000: "星火协议"}.get(
            item_hash, f"物品{item_hash}"
        )

    def get_english_name(self, item_hash: int) -> str:
        return ""

    def bucket_name(self, bucket_hash: int) -> str:
        return "Gauntlets"

    def get_item_definition(self, item_hash: int) -> dict | None:
        slot_key = {2000: "gauntlets", 3000: "gauntlets", 4000: "chest"}.get(item_hash, "chest")
        exotic = item_hash in {2000, 4000}
        return {
            "inventory": {"tierType": 6 if exotic else 5},
            # 仓库里的护甲认不出 bucket（bucketHash 是仓库格），部位只能从定义读 ——
            # 真机也是这条路，所以这里给 `equippingBlock.equipmentSlotTypeHash`（ADR-011）。
            "itemTypeDisplayName": self._DISPLAY.get(item_hash, "胸部护甲"),
            "equippingBlock": {
                "uniqueLabel": EXOTIC_ARMOR if exotic else "",
                "equipmentSlotTypeHash": SLOT_HASH[slot_key],
            },
        }


def _profile() -> dict:
    return {
        "characters": {"data": {"char-warlock": {"classType": 2}}},
        "characterEquipment": {
            "data": {"char-warlock": {"items": [_profile_items()[0]]}}
        },
        "characterInventories": {
            "data": {"char-warlock": {"items": [_profile_items()[1]]}}
        },
        "profileInventory": {"data": {"items": [_profile_items()[2]]}},
        "itemComponents": {
            "buckets": {
                "data": {
                    3551918588: {"capacity": 176, "count": 176},
                    14239492: {"capacity": 176, "count": 3},
                }
            },
            # 真机上 isEquipped 只在这里：characterEquipment 的条目没有这个字段
            "instances": {"data": {"i-exotic": {"isEquipped": True}}},
        },
    }


async def test_every_step_carries_its_slot() -> None:
    """每一步都要带 `slot`：装备编排靠它回滚（真机上漏过 equip 那步，回滚就不知道动哪个部位）。"""
    manifest = _ProfileManifest()

    class Resolver:
        async def resolve_player(self, player_name: str) -> dict:
            return {"membership_id": "46116860", "membership_type": 3}

        async def resolve_character_id(self, mid, mtype, character) -> str:
            return "char-warlock"

        async def get_profile(self, mid, mtype, components):
            return _profile()

    request = await load_plan_request(
        "Tester#1234", "i-target", "warlock", manifest=manifest, resolver=Resolver()
    )
    plan = plan_equip(request, manifest)

    assert plan.steps, "这个夹具本来就该出计划"
    assert all(step.slot for step in plan.steps), [s.action for s in plan.steps if not s.slot]


def test_bucket_capacity_comes_from_the_manifest_bucket_definition() -> None:
    """容量只有一个权威来源：Manifest 的 `DestinyInventoryBucketDefinition.itemCount`。

    以前这里读 `itemComponents.buckets.data` —— 真机上**没有那个组件**，读出来永远是空，
    "背包满"这条预检于是静默失效（实采：warlock 的头盔/臂铠/胸甲都是 10/10，一个都没报出来）。
    """
    manifest = _ProfileManifest()
    profile = _profile()
    items = parse_items_from_profile(profile, manifest)

    capacity = _bucket_capacity(manifest, "char-warlock", items)

    assert capacity["gauntlets"].capacity == 2, "容量来自假 manifest 的桶定义"
    assert capacity["gauntlets"].used == 2, "used **含正装备那件**（与 DIM 同口径）"
    assert capacity["gauntlets"].free == 0
    assert capacity["gauntlets"].equipped == 1, "已装备的单独数一份，便于话术里说清"


def test_bucket_capacity_skips_buckets_the_manifest_does_not_know() -> None:
    """桶定义查不到就**不给这一类目下结论**：让上游去判，别编一个满/不满。"""
    class _NoBuckets(_ProfileManifest):
        def get_bucket_definition(self, bucket_hash: int) -> dict | None:
            return None

    manifest = _NoBuckets()
    items = parse_items_from_profile(_profile(), manifest)

    assert _bucket_capacity(manifest, "char-warlock", items) == {}


async def test_plan_for_player_reads_once_and_plans_the_conflict() -> None:
    manifest = _ProfileManifest()
    profile = _profile()

    class Resolver:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def resolve_player(self, player_name: str) -> dict:
            return {"membership_id": "46116860", "membership_type": 3}

        async def resolve_character_id(self, mid, mtype, character) -> str:
            return "char-warlock"

        async def get_profile(self, mid, mtype, components):
            self.calls.append((mid, mtype, tuple(components)))
            return profile

    resolver = Resolver()
    request = await load_plan_request(
        "Tester#1234", "i-target", "warlock", manifest=manifest, resolver=resolver
    )
    assert len(resolver.calls) == 1
    assert request.target.name == "星火协议"
    assert request.target.location == "vault"  # 仓库里的那件
    assert request.bucket_capacity["gauntlets"].free == 0  # 金臂铠类目已满
    # 装备位的口径：金臂铠是"穿着"的，光芒备用是背包里可顶下的
    assert [item.name for item in request.worn()] == ["逃逸艺术家"]
    assert [item.name for item in request.carried()] == ["光芒备用"]

    plan = plan_equip(request, manifest)
    assert plan.status == "ready"
    # 目标在仓库里 → 要搬；目标异域且金臂铠占着异域位、背包里有非异域臂铠 → 要顶下。
    assert [step.action for step in plan.steps] == ["move", "downgrade", "equip"]
    assert plan.steps[1].item == "光芒备用"
    assert plan.steps[1].replaces == "逃逸艺术家"


async def test_plan_for_player_raises_when_the_instance_is_gone() -> None:
    from destiny_mcp.exceptions import ItemNotFoundError

    class Resolver:
        async def resolve_player(self, player_name: str) -> dict:
            return {"membership_id": "46116860", "membership_type": 3}

        async def resolve_character_id(self, mid, mtype, character) -> str:
            return "char-warlock"

        async def get_profile(self, mid, mtype, components):
            return _profile()

    with pytest.raises(ItemNotFoundError):
        await plan_for_player(
            "Tester#1234", "i-missing", "warlock",
            manifest=_ProfileManifest(), resolver=Resolver(),
        )
