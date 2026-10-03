"""一套配装**能不能写进账号**：两条求解阶段就该判死的执行前提。

真机证据（2026-10-03，同一条「社区配装 → 一次性装备」链路上两次白跑，各 **0 颗模组落地**）：

① 求解器挑了一件**在仓库里**的臂铠，而该角色臂铠格 **10/10 满** → 上游 500
   `DestinyNoRoomInDestination: There are no item slots available to transfer this item.`
   链路在写第一颗模组之前就中止了。搬运**必须先于装备**（`loadout_transfer_step`：批量装备
   要求东西已经在角色身上），所以格子满时仓库件根本进不来 —— 这不是"运气差"，是注定；
② 同一批里要换上异域头盔，而角色身上还穿着异域胸甲（星火协议）→ `equipStatus=1641
   `DestinyItemUniqueEquipRestricted`，全量回滚。批量装备是**一次上游调用**，先装哪一件由它
   决定（实测那一次先算了异域头盔，而胸甲还穿着），所以"另一个部位还穿着同类异域"不能赌。

两条判据要的账号事实**都在同一次 profile 读取里**（组件 200/201/205 + Manifest 桶定义），
零额外请求、零额外 IO：

- 件在不在角色身上 → `Armor.source_location`（`vault` = 要搬）；
- 角色还穿着哪件金装 → 已装备那几件的 `is_exotic`；
- 对应格满没满 → Manifest `DestinyInventoryBucketDefinition.itemCount` 减去该角色该桶的件数，
  `used` **含正装备那件**（与 `equip_planner._bucket_capacity` 同口径：少算一件就会假报
  "还能放一件"，然后去撞 `NoRoomInDestination`）。

结论**落在件上**（`Armor.execution_blocker`）：求解器只从没有这一项的件里挑，分析器按同一份
标注数组合规模 —— 判一次、谁读都一样，也就不存在"求解器挑了一件、写入层才发现装不上"。

**缺数据 ≠ 允许、也 ≠ 拦**：桶容量查不到（Manifest 桶定义缺失）、认不出唯一目标角色
（`character_class` 为空时三个角色混在一份快照里）时，这一条**不下结论**、不拦任何件 ——
让上游去判，别编一个满/不满。反过来，只要判出来了就一定要拦（"多算顶多让用户白清一格，
少算会去撞上游"）。

**判据、以及每条约束的"出路"都在这儿**：出路是判据的反面（满格 → 腾一格；同类异域冲突 →
先顶下那一件），写进件上的 `execution_blocker` 就是完整一句"哪条约束 + 怎么办"。
**叙述**那一层（0 候选怎么解释、确认那一刻怎么拒绝）在 `execution_diagnosis` 与
`services/build_execution_guard`：它们只读这一句，不另写判据、也不另写出路。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..vocabulary import CLASS_LABELS_ZH, CLASS_TYPE_KEYS
from .constants import ARMOR_SLOT_NAMES, SOLVER_SLOTS

if TYPE_CHECKING:  # 只用于标注：models.py 要 import 本模块，运行时不能反向依赖
    from ..manifest import ManifestManager
    from .models import Armor, InventorySnapshot

__all__ = [
    "BucketUsage",
    "ExecutionFacts",
    "annotate",
    "blocked_pieces",
    "exotic_way_out",
    "read_facts",
    "vault_full_way_out",
]

#: 仓库在 `from_profile` 里的位置标记（`Armor.source_location`）。
VAULT_LOCATION = "vault"


class BucketUsage(BaseModel):
    """该角色某一格背包的占用。容量只有 Manifest 桶定义一个权威来源。"""

    capacity: int = 0
    used: int = 0

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.used)

    @property
    def full(self) -> bool:
        """确知放不下第 11 件。容量读不到时 `capacity == 0`，这里给 False（不下结论）。"""
        return self.capacity > 0 and self.free <= 0


class ExecutionFacts(BaseModel):
    """一次"装备这五件"能不能落地所依赖的账号现场。

    整块放在快照上（`InventorySnapshot.execution`），因为它与护甲列表来自**同一次读取**：
    分开取就是两次 profile、还可能互相不一致。
    """

    #: 目标角色的职业键（`warlock`…）；空 = 认不出唯一角色 → 下面几条一律不下结论
    character: str = ""
    #: 求解器槽位 → 该角色这一格的占用（缺键 = 容量读不到）
    buckets: dict[str, BucketUsage] = Field(default_factory=dict)
    #: 求解器槽位 → 部位中文名（Manifest 桶定义；读不到就用该部位那件的类型名）。
    #: 话术里的"臂铠格"必须说中文，而**名字只有 Manifest 有** —— 不在代码里另抄一张表。
    slot_labels: dict[str, str] = Field(default_factory=dict)
    #: 角色当前穿着的**护甲**金装（部位 + 名字）；空 = 没穿 / 没读到
    worn_exotic_slot: str = ""
    worn_exotic_name: str = ""

    @property
    def character_label(self) -> str:
        return CLASS_LABELS_ZH.get(self.character, self.character)

    def blocks_vault_piece(self, slot: str) -> bool:
        bucket = self.buckets.get(slot)
        return bool(bucket and bucket.full)

    def allows_exotic_in(self, slot: str) -> bool:
        """这个部位能不能放金装：穿着另一部位的同类异域时不能（上游 1641）。"""
        return not self.worn_exotic_slot or slot == self.worn_exotic_slot

    def label(self, slot: str) -> str:
        """部位中文名；连 Manifest 都读不到时退回槽位键（宁可难看，不编名字）。"""
        return self.slot_labels.get(slot) or slot


def _bucket_definition(manifest: ManifestManager, slot: str) -> dict:
    """该部位的桶定义（`DestinyInventoryBucketDefinition`）。不给就算没读到。

    桶 hash 用**无符号**传（与 `equip_planner.ARMOR_BUCKET_BY_SLOT` 同一写法）：
    `ManifestManager.get_bucket_definition` 内部对 uint32 直查失败会做 `to_signed()` 回退，
    但替身只认一种写法 —— 两处传的不是同一个值，测试就会假绿。
    """
    lookup = getattr(manifest, "get_bucket_definition", None)
    bucket_hash = ARMOR_SLOT_NAMES.get(slot, 0)
    if not callable(lookup) or not bucket_hash:
        return {}
    definition = lookup(bucket_hash & 0xFFFFFFFF)
    return definition if isinstance(definition, dict) else {}


def read_facts(
    snapshot: InventorySnapshot, target_class_type: int, manifest: ManifestManager
) -> ExecutionFacts:
    """从**已经解析好的快照**里读出执行现场（不再碰 profile、不再发请求）。

    `target_class_type < 0`（没按职业过滤）时三个角色混在一份快照里，"目标角色"本身不成立，
    所以只回一个空现场：宁可让上游去判，也不许拿别的角色的格子数当判据。
    """
    if target_class_type < 0:
        return ExecutionFacts()
    facts = ExecutionFacts(character=CLASS_TYPE_KEYS.get(target_class_type, ""))
    for slot in SOLVER_SLOTS:
        on_character = [
            armor for armor in snapshot.get_slot(slot) if armor.source_location != VAULT_LOCATION
        ]
        definition = _bucket_definition(manifest, slot)
        capacity = int(definition.get("itemCount") or 0)
        if capacity > 0:
            facts.buckets[slot] = BucketUsage(capacity=capacity, used=len(on_character))
        label = str((definition.get("displayProperties") or {}).get("name") or "")
        facts.slot_labels[slot] = label or _piece_type_label(manifest, on_character)
        worn = next(
            (armor for armor in on_character if armor.is_equipped and armor.is_exotic), None
        )
        if worn is not None and not facts.worn_exotic_slot:
            facts.worn_exotic_slot, facts.worn_exotic_name = slot, worn.name
    return facts


def _piece_type_label(manifest: ManifestManager, pieces: list[Armor]) -> str:
    """桶定义读不到时的兜底部位名：该部位第一件的物品类型名（"臂铠"这种）。"""
    for armor in pieces:
        info = manifest.get_item_info(armor.item_hash) or {}
        label = str(info.get("itemTypeNameDisplay") or "")
        if label:
            return label
    return ""


def vault_full_way_out(facts: ExecutionFacts, slot: str) -> str:
    """格子满的**出路**（唯一出处：判据与出路是一件事的两面，所以在判据旁边）。

    三个出口共用这一句：求解器的 0 候选诊断、分析器的结论、**确认那一刻的复检**
    （`services/build_execution_guard` 直接把 `execution_blocker` 整句带出去）。
    各写一句的后果是同一件事三种说法，改了一处另两处还留着旧出路。
    """
    return (
        f"先在游戏里腾出{facts.label(slot)}格的一格（分解或转移到仓库别的位置），"
        "再重新求解"
    )


def exotic_way_out(facts: ExecutionFacts) -> str:
    """金装冲突的出路（同上：唯一出处）。说清"先顶下"这个动作，别只说"不能装"。"""
    return (
        f"①先用一件非异域{facts.label(facts.worn_exotic_slot)}把它顶下来"
        '（inventory_assistant(intent="equip") 会给"先顶下、再装目标"的两步计划），'
        "再重新求解；②或改选一件别的金装/不带金装"
    )


def _piece_blocker(armor: Armor, slot: str, facts: ExecutionFacts) -> str:
    """这件为什么这次装不上、以及怎么办（空串 = 能装）。

    带上出路是有意的：这一句会被**确认那一刻**原样拿去当拒绝理由（写入前复检），
    "只报装不上"会把读的人指去降属性目标 —— 那是错的方向。
    """
    if armor.source_location == VAULT_LOCATION and facts.blocks_vault_piece(slot):
        bucket = facts.buckets[slot]
        return (
            f"「{armor.name}」在仓库里，而{facts.character_label}的{facts.label(slot)}"
            f"格已经满了（{bucket.used}/{bucket.capacity}）：搬运会撞上游 "
            "DestinyNoRoomInDestination（装备要求东西先在角色身上）。"
            f"出路：{vault_full_way_out(facts, slot)}。"
        )
    if armor.is_exotic and not facts.allows_exotic_in(slot):
        return (
            f"「{armor.name}」是异域{facts.label(slot)}，而{facts.character_label}"
            f"正穿着异域「{facts.worn_exotic_name}」（{facts.label(facts.worn_exotic_slot)}）："
            "同类异域只能装备一件，批量装备会回 1641 DestinyItemUniqueEquipRestricted。"
            f"出路：{exotic_way_out(facts)}。"
        )
    return ""


def annotate(
    snapshot: InventorySnapshot, target_class_type: int, manifest: ManifestManager
) -> ExecutionFacts:
    """给每一件写上"这次装不装得上"，并把现场挂回快照（`InventorySnapshot.execution`）。

    在 `from_profile` 末尾调一次：那正是"账号事实已经解析完、请求还没进来的时刻"。
    """
    facts = read_facts(snapshot, target_class_type, manifest)
    for slot in SOLVER_SLOTS:
        for armor in snapshot.get_slot(slot):
            armor.execution_blocker = _piece_blocker(armor, slot, facts)
    return facts


def blocked_pieces(snapshot: InventorySnapshot, slot: str) -> list[Armor]:
    """这一部位里"这次装不上"的件（给分析器数"砍掉了多少件"用）。"""
    return [armor for armor in snapshot.get_slot(slot) if armor.execution_blocker]
