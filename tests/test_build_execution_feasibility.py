"""① 求解阶段就吃掉两个真机失败模式（2026-10-03 两次白跑，各 0 颗模组落地）。

真机证据（同一条「社区配装 → 一次性装备」链路）：

- **格满**：求解器挑了一件在**仓库**里的臂铠，而该角色臂铠格 **10/10 满** → 上游 500
  `DestinyNoRoomInDestination`，链路在写第一颗模组之前中止。搬运必须先于装备，
  所以格子满时仓库件**根本进不来**；
- **金装冲突**：同一批里要换上异域头盔，而角色身上还穿着异域胸甲 → `equipStatus=1641`
  `DestinyItemUniqueEquipRestricted`，全量回滚。

两条判据现在落在**件上**（`Armor.execution_blocker`，出处 `build/execution_feasibility`）：
求解器只从没有这一项的件里挑，分析器与规模闸门读同一份标注。这个文件钉五件事：

1. 那两个场景下**求解器不会再给出注定失败的候选**（有解时选能装的，无解时如实报原因）；
2. 原因要说清**是哪条约束卡的**（1641 / NoRoomInDestination + 是哪两件/哪一格）；
3. 读不到就不下结论（桶定义缺失、认不出唯一角色时**一件都不许拦**）；
4. 闸门数的是**同一个空间**（装不上的件不计入组合规模）；
5. **确认那一刻要复检**（(c) 段）：求解时判过的两条前提，到"用户点确认"时不必然还成立 ——
   写入层以前只比库存指纹，而 `snapshot.execution` 刻意不进指纹（见 `build/snapshot_version`），
   指纹一样证明不了它们还成立；撞上游就是整批回滚、0 颗模组落地。
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.build import analyzer  # noqa: E402
from destiny_mcp.build.constraints import parse as parse_constraints  # noqa: E402
from destiny_mcp.build.execution_feasibility import annotate  # noqa: E402
from destiny_mcp.build.models import BuildConstraints, BuildRequest, InventorySnapshot  # noqa: E402
from destiny_mcp.build.snapshot_version import snapshot_version  # noqa: E402
from destiny_mcp.build.solver import solve  # noqa: E402
from destiny_mcp.services.build_service import BuildService  # noqa: E402
from destiny_mcp.tools import _build_flow  # noqa: E402

#: 部位的桶 hash（真机口径：Manifest 的 `DestinyInventoryBucketDefinition`，护甲各 10 格）
SLOT_BUCKETS: dict[str, int] = {
    "helmets": 3448274439,
    "gauntlets": 3551918588,
    "chests": 14239492,
    "legs": 20886954,
    "class_items": 1585787867,
}
#: 仓库里的件 `bucketHash` 报的是"仓库桶"，部位只能从定义读（真机实测，多处同源）
VAULT_BUCKET = 138197802
SLOT_LABELS = {
    "helmets": "头盔",
    "gauntlets": "臂铠",
    "chests": "胸甲",
    "legs": "腿部护甲",
    "class_items": "职业护甲",
}
GRENADE_HASH = 1735777505

# 夹具用的物品 hash（每个部位至少一件普通件 + 需要时的金装）
WORN_EXOTIC_CHEST = 900
PLAIN_GAUNTLETS = 901
VAULT_GAUNTLET = 902
EXOTIC_HELMET = 903
PLAIN_HELMET = 904
PLAIN_LEGS = 905
PLAIN_CLASS_ITEM = 906
PLAIN_CHEST = 907

_DEFS: dict[int, tuple[str, int, str]] = {
    WORN_EXOTIC_CHEST: ("chests", 6, "星火协议"),
    PLAIN_GAUNTLETS: ("gauntlets", 5, "普通臂铠"),
    VAULT_GAUNTLET: ("gauntlets", 5, "仓库臂铠"),
    EXOTIC_HELMET: ("helmets", 6, "光耀之冠"),
    PLAIN_HELMET: ("helmets", 5, "普通头盔"),
    PLAIN_LEGS: ("legs", 5, "普通腿甲"),
    PLAIN_CLASS_ITEM: ("class_items", 5, "普通职业物品"),
    PLAIN_CHEST: ("chests", 5, "普通胸甲"),
}


class _Manifest:
    """`from_profile` / `execution_feasibility` / 约束解析 要的最小 manifest 面。

    `bucket_counts=False` 用来演"桶定义读不到"（缺数据 ≠ 拦）。
    """

    def __init__(self, *, bucket_counts: bool = True) -> None:
        self._bucket_counts = bucket_counts

    def get_item_info(self, item_hash: int) -> dict:
        slot, tier, _name = _DEFS[item_hash]
        return {
            "classType": 2,
            "tier": tier,
            "bucketTypeHash": SLOT_BUCKETS[slot],
            "itemType": 2,
            "itemTypeNameDisplay": SLOT_LABELS[slot] if tier == 5 else SLOT_LABELS[slot],
            "icon": "",
        }

    def get_item_name(self, item_hash: int) -> str:
        return _DEFS.get(item_hash, ("", 0, str(item_hash)))[2]

    def get_item_definition(self, item_hash: int) -> dict:
        return {"sockets": {"socketEntries": []}}

    def get_set_bonus_info(self, item_hash: int) -> None:
        return None

    def get_bucket_definition(self, bucket_hash: int) -> dict | None:
        if not self._bucket_counts:
            return None
        for slot, bucket in SLOT_BUCKETS.items():
            if bucket_hash in (bucket, bucket - (1 << 32)):
                return {
                    "itemCount": 10,
                    "displayProperties": {"name": SLOT_LABELS[slot]},
                }
        return None

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """约束解析按名字找金装：只认「光耀之冠」。"""
        if "光耀之冠" in query:
            return [{
                "itemHash": EXOTIC_HELMET,
                "name": "光耀之冠",
                "nameEn": "Helm of Saint-14",
                "itemType": 2,
                "tier": 6,
                "classType": 2,
            }]
        return []


def _snapshot(
    *,
    on_character_gauntlets: int = 10,
    vault_gauntlet: bool = True,
    exotic_helmet: bool = True,
    plain_chest: bool = True,
    vault_gauntlet_grenade: int = 60,
    character_class: str = "warlock",
    bucket_counts: bool = True,
) -> InventorySnapshot:
    """一份"该角色臂铠格已满、身上穿着异域胸甲"的真机现场。

    `on_character_gauntlets=10` 就是真机的 10/10（**含正装备那件**，与 DIM 同口径）——
    这时仓库里那件臂铠搬不进来，求解器只能从身上/背包里已有的臂铠里挑。
    """
    equipment = [
        {"itemInstanceId": "chest-worn", "itemHash": WORN_EXOTIC_CHEST,
         "bucketHash": SLOT_BUCKETS["chests"]},
        {"itemInstanceId": "gauntlet-worn", "itemHash": PLAIN_GAUNTLETS,
         "bucketHash": SLOT_BUCKETS["gauntlets"]},
    ]
    carried = [
        {"itemInstanceId": f"gauntlet-{index}", "itemHash": PLAIN_GAUNTLETS,
         "bucketHash": SLOT_BUCKETS["gauntlets"]}
        for index in range(max(0, on_character_gauntlets - 1))
    ]
    if exotic_helmet:
        carried.append({"itemInstanceId": "helmet-exotic", "itemHash": EXOTIC_HELMET,
                        "bucketHash": SLOT_BUCKETS["helmets"]})
    if plain_chest:
        # 普通胸甲是"能不能绕开身上那件异域"的关键：没有它，胸甲槽只有异域那一件，
        # "异域头盔 + 普通胸甲"这套根本不存在，测出来的就不是执行前提那条规则了。
        carried.append({"itemInstanceId": "chest-plain", "itemHash": PLAIN_CHEST,
                        "bucketHash": SLOT_BUCKETS["chests"]})
    vault = [
        {"itemInstanceId": "helmet-plain", "itemHash": PLAIN_HELMET, "bucketHash": VAULT_BUCKET},
        {"itemInstanceId": "legs-plain", "itemHash": PLAIN_LEGS, "bucketHash": VAULT_BUCKET},
        {"itemInstanceId": "class-plain", "itemHash": PLAIN_CLASS_ITEM, "bucketHash": VAULT_BUCKET},
    ]
    if vault_gauntlet:
        vault.append({"itemInstanceId": "gauntlet-vault", "itemHash": VAULT_GAUNTLET,
                      "bucketHash": VAULT_BUCKET})

    stats: dict[str, dict] = {}
    for entry in [*equipment, *carried, *vault]:
        grenade = vault_gauntlet_grenade if entry["itemInstanceId"] == "gauntlet-vault" else 10
        stats[entry["itemInstanceId"]] = {"stats": {str(GRENADE_HASH): {"value": grenade}}}
    instances = {
        entry["itemInstanceId"]: {"energy": {"energyCapacity": 10, "energyUsed": 0}}
        for entry in [*equipment, *carried, *vault]
    }
    profile = {
        "profileInventory": {"data": {"items": vault}},
        "characters": {"data": {"char-warlock": {"classType": 2}}},
        "characterInventories": {"data": {"char-warlock": {"items": carried}}},
        "characterEquipment": {"data": {"char-warlock": {"items": equipment}}},
        "itemComponents": {
            "instances": {"data": instances},
            "stats": {"data": stats},
            "sockets": {"data": {}},
            "reusablePlugs": {"data": {}},
        },
    }
    return InventorySnapshot.from_profile(
        profile, _Manifest(bucket_counts=bucket_counts), character_class
    )


def _instance_ids(armor_sets) -> list[list[str]]:
    return [[armor.item_instance_id for armor in armor_set.armor] for armor_set in armor_sets]


# ── (a) 仓库件遇上满格 ────────────────────────────────────────────────────


def test_vault_piece_in_a_full_bucket_is_never_selected() -> None:
    """臂铠格 10/10 时，仓库里那件臂铠（哪怕属性最好）不进任何候选。

    注入验证：把 `solver` 里那句 `if not armor.execution_blocker` 去掉 → 本用例红
    （那件 60 点手雷的仓库臂铠会立刻成为首选，而写入会在搬运时撞 NoRoomInDestination）。
    """
    snapshot = _snapshot()
    blocked = [armor for armor in snapshot.gauntlets if armor.execution_blocker]

    assert [armor.item_instance_id for armor in blocked] == ["gauntlet-vault"]
    assert "DestinyNoRoomInDestination" in blocked[0].execution_blocker
    assert "10/10" in blocked[0].execution_blocker, "话术里要有数字：满到什么程度"

    result = solve(snapshot, BuildConstraints(class_type=2))

    assert result.sets, "格满只该砍掉搬不进来的件，不该整套配不出来"
    assert "gauntlet-vault" not in {
        instance_id for ids in _instance_ids(result.sets) for instance_id in ids
    }
    assert result.combos == 20, (
        "组合数只数能装的件：1 头盔（异域那件被挡）× 10 臂铠（仓库那件被挡）× 2 胸甲 × 1 腿 × 1 职业"
    )


def test_vault_piece_is_still_selectable_when_the_bucket_has_room() -> None:
    """反方向：格子有空位时，仓库件照常参与（不许把"在仓库里"当成一律不可用）。"""
    snapshot = _snapshot(on_character_gauntlets=9)

    assert not any(armor.execution_blocker for armor in snapshot.gauntlets)
    result = solve(snapshot, BuildConstraints(class_type=2, grenade_min=50))

    assert result.sets, "仓库那件 60 点手雷的臂铠正是唯一能满足下限的件"
    assert any(
        "gauntlet-vault" in ids for ids in _instance_ids(result.sets)
    )


# ── (b) 与角色正穿着的金装冲突 ───────────────────────────────────────────


def test_exotic_is_confined_to_the_slot_of_the_worn_exotic() -> None:
    """身上穿着异域胸甲时，异域头盔不进候选；同部位换金装仍然可以。"""
    snapshot = _snapshot()

    helmet = next(a for a in snapshot.helmets if a.item_hash == EXOTIC_HELMET)
    assert "1641" in helmet.execution_blocker
    assert "星火协议" in helmet.execution_blocker, "要说清是与哪一件冲突"
    assert not any(a.execution_blocker for a in snapshot.chests), "冲突的是别的部位，不是胸甲本身"

    result = solve(snapshot, BuildConstraints(class_type=2))

    assert result.sets
    chosen_exotics = [
        (armor.slot, armor.name)
        for armor_set in result.sets
        for armor in armor_set.armor
        if armor.is_exotic
    ]
    assert chosen_exotics, "身上正穿着的那件金装胸甲本来就在候选池里（同部位替换，不冲突）"
    assert {slot for slot, _ in chosen_exotics} == {"chests"}, (
        "金装只允许出现在当前穿着金装的那个部位：别处戴上去就撞 1641"
    )


def test_exotic_in_the_worn_slot_is_still_allowed() -> None:
    """同部位换金装是"装上就把它替换掉"，不是冲突 —— 不许把这条也拦掉。"""
    snapshot = _snapshot(exotic_helmet=False)
    # 把金装胸甲放进候选池（身上正穿着的那件本身就在快照里）
    wanted = next(a for a in snapshot.chests if a.item_hash == WORN_EXOTIC_CHEST)
    snapshot.chests = [wanted]

    assert not wanted.execution_blocker
    result = solve(snapshot, BuildConstraints(class_type=2))

    assert result.sets
    assert any(
        any(a.item_hash == WORN_EXOTIC_CHEST for a in armor_set.armor)
        for armor_set in result.sets
    )


# ── 无解时必须说清是哪条约束卡的 ─────────────────────────────────────────


def test_conflicting_requested_exotic_reports_the_constraint_instead_of_silence() -> None:
    """指定了一件装不上的金装：0 候选 + 原因点名"1641 + 是哪两件"（不许静默返回空）。"""
    snapshot = _snapshot()
    constraints = parse_constraints(
        BuildRequest(character_class="warlock", exotic_name="光耀之冠"), _Manifest()
    )

    result = solve(snapshot, constraints)
    reasons = analyzer.execution_blockers(snapshot, constraints, _Manifest())

    assert result.sets == [], "指定的金装被挡在候选外 → 一套都出不来"
    assert result.combos == 0
    assert len(reasons) == 2, "金装冲突 + 满格各一条"
    assert "1641" in reasons[0] and "光耀之冠" in reasons[0] and "星火协议" in reasons[0]
    assert "DestinyNoRoomInDestination" in reasons[1]


def test_full_bucket_reason_is_reported_even_when_other_slots_are_fine() -> None:
    """只有臂铠格满时，原因只讲那一格（别把没满的部位也念一遍）。"""
    reasons = analyzer.execution_blockers(
        _snapshot(), BuildConstraints(class_type=2), _Manifest()
    )

    assert len(reasons) == 1
    assert "臂铠" in reasons[0] and "10/10" in reasons[0]


# ── 缺数据不许下结论 ─────────────────────────────────────────────────────


def test_missing_bucket_definitions_block_nothing() -> None:
    """桶定义读不到 → 一件都不拦（"没探成"不能写成"装不上"）。"""
    snapshot = _snapshot(bucket_counts=False, exotic_helmet=False)

    assert snapshot.execution.buckets == {}
    assert not any(
        armor.execution_blocker
        for slot in SLOT_BUCKETS
        for armor in snapshot.get_slot(slot)
    )
    assert solve(snapshot, BuildConstraints(class_type=2)).sets


def test_unknown_target_character_blocks_nothing() -> None:
    """没按职业过滤（三个角色混在一份快照里）时，"目标角色"不成立 → 不下结论。"""
    snapshot = _snapshot(character_class="")

    assert snapshot.execution.character == ""
    assert not any(
        armor.execution_blocker
        for slot in SLOT_BUCKETS
        for armor in snapshot.get_slot(slot)
    )


# ── 闸门数的是同一个空间 ─────────────────────────────────────────────────


def test_combination_gate_counts_only_usable_pieces() -> None:
    """规模闸门必须数求解器真正会枚举的那些件，否则会拿一个不会跑的数字拒绝请求。"""
    snapshot = _snapshot()
    total, counts = analyzer.estimate_combinations(snapshot, BuildConstraints(class_type=2))

    assert counts[1] == 10, "臂铠 11 件里有 1 件搬不进来"
    assert total == 20


# ── 服务与工具面：0 候选要说"装不上"，不是"配不出来" ─────────────────────


@pytest.mark.asyncio
async def test_find_build_reports_blockers_in_diagnostics_and_never_ships_a_doomed_piece() -> None:
    """`find_build` 的候选里不会出现装不上的件，且诊断里带着原因。"""
    snapshot = _snapshot()
    service = BuildService(MagicMock(), _Manifest(), MagicMock())
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    diagnostics: list = []

    results = await service.find_build(
        "Tester#1234", BuildRequest(character_class="warlock"), diagnostics
    )

    assert results, "格满不等于配不出来"
    shipped = {
        item.item_instance_id
        for result in results
        for item in result.canonical_build.items
    }
    assert "gauntlet-vault" not in shipped
    report = diagnostics[0].to_dict()
    assert report["blockers"], "砍掉了件就要在诊断里说清为什么"
    assert "DestinyNoRoomInDestination" in report["blockers"][0]


@pytest.mark.asyncio
async def test_analyze_build_leads_with_the_execution_reason() -> None:
    """`analyze_build` 先说执行前提，别把"装不上"归因到属性上。"""
    snapshot = _snapshot()
    service = BuildService(MagicMock(), _Manifest(), MagicMock())
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)

    analysis = await service.analyze_build(
        "Tester#1234", BuildRequest(character_class="warlock", exotic_name="光耀之冠")
    )

    assert analysis.blocked_by, "结构化字段要给出来（工具层靠它换话术）"
    assert "1641" in analysis.reason
    assert analysis.precision == "exact", "这不算「没算」：执行前提是确定性结论"


@pytest.mark.asyncio
async def test_find_branch_says_unusable_not_unsatisfiable(monkeypatch) -> None:
    """0 候选时工具面说的是"能装上的候选是 0"，并把原因放进 warnings。"""
    from destiny_mcp.build.process_types import SearchCoverage, SearchDiagnostics

    reason = "术士的臂铠格已经满了（10/10），仓库里那 1 件搬不进来（上游会回 DestinyNoRoomInDestination）"

    class _Build:
        async def find_build(self, player_name, request, coverage=None, **kwargs):
            coverage.append(SearchDiagnostics(
                coverage=SearchCoverage(exhaustive=True, combos=0), blocked_by=[reason]
            ))
            return []

    async def _no_ladder(*args, **kwargs):
        return {}

    monkeypatch.setattr(_build_flow.armor_ladder, "no_solution_ladder", _no_ladder)
    response = await _build_flow.find(
        {"build_svc": _Build()}, "Tester#1234",
        SimpleNamespace(**_request_fields()), {"character": "warlock"}, lambda value: value,
    )

    assert "能装上" in response["summary"]
    assert "DestinyNoRoomInDestination" in response["summary"]
    assert response["data"]["search"]["blockers"] == [reason]
    assert reason in response["warnings"]
    assert "执行前提" in response["next_actions"][0]


@pytest.mark.asyncio
async def test_recommend_branch_says_unusable_not_unsatisfiable(monkeypatch) -> None:
    """`recommend` 的 0 候选同样要分清"装不上"与"配不出来"。"""
    from destiny_mcp.build.models import BuildAnalysis

    reason = "指定的金装「光耀之冠」是头盔部位的，而术士当前穿着异域「星火协议」（胸甲）……1641"

    from destiny_mcp.build.models import BuildRecommendation

    class _Build:
        async def recommend_build(self, player_name, request, functional_mods=None):
            return BuildRecommendation(
                results=[], analysis=BuildAnalysis(reason=reason, blocked_by=[reason])
            )

    async def _no_ladder(*args, **kwargs):
        return {}

    monkeypatch.setattr(_build_flow.armor_ladder, "no_solution_ladder", _no_ladder)
    response = await _build_flow.recommend(
        {"build_svc": _Build()}, "Tester#1234",
        SimpleNamespace(**_request_fields()), {"character": "warlock"},
        lambda value: value.model_dump(mode="json"),
    )

    assert "能装上" in response["summary"] and "1641" in response["summary"]
    assert reason in response["warnings"]


def _request_fields() -> dict:
    """`_has_hard_targets` 会按 `REQUEST_TARGET_FIELDS` 读这些字段。"""
    return {
        "weapons_target": None, "health_target": None, "class_target": None,
        "grenade_target": 50, "melee_target": None, "super_target": None,
        "character_class": "warlock",
    }


# ── (c) 确认那一刻的复检：ADR-022 只覆盖了"求解那一刻" ─────────────────────
#
# 真机代价（同上两次白跑）：求解阶段判过的两条前提，到了用户点确认时可能已经不成立，
# 而写入层以前只比"库存指纹"——`snapshot.execution` 刻意不进指纹，指纹一样证明不了
# 它们还成立；撞上游的结果是整批回滚、0 颗模组落地。
#
# 复检的判据就是求解那一刻写在件上的 `execution_blocker`（唯一出处
# `build/execution_feasibility`，连同"哪条约束 + 出路"整句），写入前换一份**刚重取**的
# 现场重读一遍；判决落不落地的顺序与理由在 `services/build_execution_guard` 的 docstring。


def _confirm_service(manifest: _Manifest, snapshot: InventorySnapshot) -> BuildService:
    service = BuildService(MagicMock(), manifest, MagicMock())
    service._inventory.get_armor_snapshot = AsyncMock(return_value=snapshot)
    service._equipment.equip_with_recovery = AsyncMock()
    return service


async def _candidate_containing(
    service: BuildService, request: BuildRequest, instance_id: str
) -> object:
    """跑一次真 `find_build`，取回包含指定实例的那份候选（顺带验证它真会签发出来）。"""
    results = await service.find_build("Tester#1234", request, [])
    return next(
        result.canonical_build
        for result in results
        if any(item.item_instance_id == instance_id for item in result.canonical_build.items)
    )


@pytest.mark.asyncio
async def test_equip_build_rechecks_the_premises_the_find_stage_could_not_judge() -> None:
    """求解读不到桶容量时不拦件（"没探成"≠"装不上"）→ 确认时读到了就必须自己判。

    这一条**只能**由写入前的复检抓到：两次读的是**同一份账号现场**，护甲行逐字段相同，
    `snapshot_version` 也相同（`execution` 刻意不进指纹）—— 指纹比对给不出任何信号。

    注入验证：去掉 `build_execution_guard.recheck_confirmed_build` 里 `refusals` 那段判断
    → 本用例红（会一路走到 `equip_with_recovery`，真机上就是上游 500 + 整批回滚、0 颗模组落地）。
    """
    at_find = _snapshot(bucket_counts=False)
    service = _confirm_service(_Manifest(bucket_counts=False), at_find)
    build = await _candidate_containing(
        service,
        BuildRequest(character_class="warlock", grenade_target=50),
        "gauntlet-vault",
    )

    assert build.snapshot_version == snapshot_version(_snapshot()), (
        "两次读的是同一份账号现场：指纹一样，所以这条只能靠写入前的复检"
    )

    service._manifest = _Manifest()  # 确认这一刻桶定义读得到（现场本身没变）
    service._inventory.get_armor_snapshot = AsyncMock(return_value=_snapshot())
    result = await service.equip_build("Tester#1234", build, "warlock")

    assert result["code"] == "execution_precondition_failed"
    assert "DestinyNoRoomInDestination" in result["message"]
    assert "10/10" in result["message"], "要说清是哪一格、满到什么程度"
    assert "先在游戏里腾出" in result["message"], (
        "要给出路（腾一格）：只报装不上，下一步会被指去降属性目标"
    )
    assert result["blockers"]
    service._equipment.equip_with_recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_equip_build_refuses_when_the_bucket_filled_up_after_find() -> None:
    """求解时臂铠格 9/10、确认时 10/10：拒绝理由是"腾一格"，不是泛泛的"重新求解"。

    这件事用户真的会做（求解完回游戏里捡东西/挪装备）。指纹这时**也**对不上，而复检排在
    指纹比对之前 —— 两个信号都指向"别写"，但只有前者说得清是哪条约束、出路是什么。
    """
    at_find = _snapshot(on_character_gauntlets=9)
    service = _confirm_service(_Manifest(), at_find)
    build = await _candidate_containing(
        service,
        BuildRequest(character_class="warlock", grenade_target=50),
        "gauntlet-vault",
    )

    service._inventory.get_armor_snapshot = AsyncMock(
        return_value=_snapshot(on_character_gauntlets=10)
    )
    result = await service.equip_build("Tester#1234", build, "warlock")

    assert result["code"] == "execution_precondition_failed"
    assert "臂铠" in result["message"] and "10/10" in result["message"]
    assert "先在游戏里腾出" in result["message"]
    service._equipment.equip_with_recovery.assert_not_awaited()


@pytest.mark.asyncio
async def test_equip_build_refuses_when_the_worn_exotic_changed() -> None:
    """确认前换上了另一件金装：写入前按当时的现场复检，报 1641 与"先顶下"的出路。

    求解那一刻他身上没有异域（胸前是普通胸甲），异域头盔合法；确认时胸口已经是「星火协议」，
    同一件头盔就撞 1641 —— 上游的答案是整批回滚，所以必须在这里拦住。
    """
    at_find = _snapshot()
    worn = next(a for a in at_find.chests if a.item_hash == WORN_EXOTIC_CHEST)
    plain = next(a for a in at_find.chests if a.item_hash == PLAIN_CHEST)
    worn.is_equipped, plain.is_equipped = False, True
    # 现场变了就得重算标注：判据只有一份（`execution_feasibility.annotate`），别自己写一遍
    at_find.execution = annotate(at_find, 2, _Manifest())
    service = _confirm_service(_Manifest(), at_find)
    build = await _candidate_containing(
        service,
        BuildRequest(character_class="warlock", exotic_name="光耀之冠"),
        "helmet-exotic",
    )

    service._inventory.get_armor_snapshot = AsyncMock(return_value=_snapshot())
    result = await service.equip_build("Tester#1234", build, "warlock")

    assert result["code"] == "execution_precondition_failed"
    assert "1641" in result["message"] and "光耀之冠" in result["message"]
    assert "星火协议" in result["message"], "要说清是与哪一件冲突"
    assert "先用一件非异域" in result["message"], "出路"
    service._equipment.equip_with_recovery.assert_not_awaited()
