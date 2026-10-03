"""回读判据的守门：**"装好了"不许被报成"对不上"**，而且只许读一轮窗口、该收手时立刻收手。

真机 2026-10-03（audit `/123435`，`equip_build`）现场：五件装备、22 颗模组、2 条调谐**全部落盘**，
两次 `verify` 却都报 false，整条调用 224.2 秒。根因是判据拿两套 hash 写法直接比：

- 账号侧（profile 组件 305 的 `plugHash`）一律**无符号**；
- 社区配装模板 / 求解方案给的是**有符号**（`../123035` 的 `functional_mods.mods[].variants`
  里就写着 `-1847517590`、`-7144743`）；
- 写入那条路只在**发请求时**归一（`bungie_client.insert_socket_plug_free` 的 `to_unsigned`），
  所以账号是对的，只有回读核对恒为 False。

对照组（同一晚、同一进程、同一账号）：`equip_loadout` 的配装是从 profile 读出来的
（无符号），回读一次就通过，76.4 秒。所以这里钉的不只是"能通过"，还有**为什么能通过** ——
夹具里的 hash 对是上面那份真机审计里的原值，不是编的。

③④ 钉的是反方向：**注定等不到的那颗别再等**（预检判死的那颗与上游拒绝的那颗一样，账号上永远
不会成立），⑤ 钉**不该收手时不许收手**（照抄模组落不下 `skipped`、腾能量被拒 `clear` 都不在计划
目标态里）—— "算不算计划目标态"只有 `loadout_blocked_mods` 一处判。
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.models import Loadout, LoadoutItem, LoadoutSubclassConfig, ModOperation
from destiny_mcp.services import loadout_verify, write_readback
from destiny_mcp.services.loadout_blocked_mods import BlockedMods
from destiny_mcp.services.loadout_equipment_service import LoadoutEquipmentService
from destiny_mcp.services.loadout_matches import sockets_match
from destiny_mcp.utils.hash_utils import to_unsigned

# 真机原值（audit /123035 的 variants 对 /123508 的账号回读）：
# 有符号那一侧来自社区模板，无符号那一侧是账号现在报的，同一个插件的两种写法。
IMPACT_INDUCTION_SIGNED = -1847517590      # 回天掌法
IMPACT_INDUCTION_ACCOUNT = 2447449706
# 这一颗社区模板给了**两个**版本（`[-1699128059, -519166499]`），账号上落的是前一个 ——
# 第一版夹具配错了对（拿了后一个），正是下面那条自证断言抓出来的。
SPECIAL_AMMO_SIGNED = -1699128059         # 特殊武器弹药搜寻者
SPECIAL_AMMO_ACCOUNT = 2595839237
TUNING_SIGNED = 3122197216 - 2**32         # 平衡调整（账号侧 3122197216）
TUNING_ACCOUNT = 3122197216
FRAGMENT_SIGNED = -1668045176              # 保护琢面（manifest.search 给的有符号）
FRAGMENT_ACCOUNT = 2626922120


def test_the_fixture_pairs_are_the_same_number_in_two_domains() -> None:
    """夹具自证：每一对都是**同一个编号**的两种写法（不是两个不同的插槽值）。

    这条先是给我自己看的 —— 2026-10 已经有过一次"算错的 hash 转换被照抄进守卫夹具"
    （见 AGENTS.md 的干活方式），所以配对关系要由断言钉住，不能只写在注释里。
    """
    assert to_unsigned(IMPACT_INDUCTION_SIGNED) == IMPACT_INDUCTION_ACCOUNT
    assert to_unsigned(SPECIAL_AMMO_SIGNED) == SPECIAL_AMMO_ACCOUNT
    assert to_unsigned(TUNING_SIGNED) == TUNING_ACCOUNT
    assert to_unsigned(FRAGMENT_SIGNED) == FRAGMENT_ACCOUNT


SUBCLASS_HASH = 3893112950


def _manifest() -> MagicMock:
    """`itemType == 16` 只给子职业本体（找子职业那一步就是按它认的），别的件给护甲。"""
    manifest = MagicMock()
    manifest.get_item_info = MagicMock(
        side_effect=lambda h: {"itemType": 16 if h == SUBCLASS_HASH else 3}
    )
    manifest.get_item_name = MagicMock(return_value="")
    manifest.get_english_name = MagicMock(return_value="")
    manifest.item_type_name = MagicMock(return_value="")
    manifest.bucket_name = MagicMock(return_value="")
    return manifest


def _owner(profile: dict, manifest: MagicMock | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        _manifest=manifest or _manifest(),
        _resolver=SimpleNamespace(
            resolve_player=AsyncMock(
                return_value={"membership_id": "mid", "membership_type": 3}
            ),
            resolve_character_id=AsyncMock(return_value="char-1"),
            get_profile=AsyncMock(return_value=profile),
        ),
    )


def _profile(
    *,
    item_sockets: dict[str, list[int]] | None = None,
    subclass_instance: str = "sub-1",
    subclass_hash: int = 3893112950,
    subclass_sockets: list[int] | None = None,
) -> dict:
    equipped = [
        {"itemHash": item_hash, "itemInstanceId": instance_id}
        for instance_id, item_hash in (
            ("gloves-1", 1371860250),
            (subclass_instance, subclass_hash),
        )
    ]
    sockets = {
        instance_id: {"sockets": [{"plugHash": h} for h in plugs]}
        for instance_id, plugs in (item_sockets or {}).items()
    }
    if subclass_sockets is not None:
        sockets[subclass_instance] = {
            "sockets": [{"plugHash": h} for h in subclass_sockets]
        }
    return {
        "characters": {"data": {"char-1": {"classType": 2}}},
        "characterEquipment": {"data": {"char-1": {"items": equipped}}},
        "itemComponents": {"sockets": {"data": sockets}},
    }


def _loadout(*, mod_sockets: dict[int, int], mods: list[int]) -> Loadout:
    return Loadout(
        id="exact",
        name="已确认的精确配装",
        character="warlock",
        items=[LoadoutItem(
            item_hash=1371860250,
            name="光芒领主手套",
            slot="gauntlets",
            item_instance_id="gloves-1",
            mod_sockets=mod_sockets,
            mods=mods,
        )],
    )


# ── ① 误报：判据必须认两套写法 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_readback_accepts_plan_hashes_in_the_signed_domain() -> None:
    """方案给的是**有符号**写法、账号报的是**无符号**：同一颗，回读必须判"对得上"。

    这就是 audit `/123435` 那两次 false 的复现（夹具逐字取自那次调用）：`mod_sockets` 里记的
    `回天掌法` 是 `-1847517590`，而账号上那个槽是 `2447449706`。改回"两边直接比"，
    这条立刻变红 —— 而真机上的表现是：写入全成功、回读报对不上、再白读一整轮。
    """
    owner = _owner(_profile(item_sockets={
        "gloves-1": [4183296050, IMPACT_INDUCTION_ACCOUNT, SPECIAL_AMMO_ACCOUNT],
    }))
    loadout = _loadout(
        mod_sockets={1: IMPACT_INDUCTION_SIGNED},
        mods=[SPECIAL_AMMO_SIGNED],
    )

    assert await loadout_verify.verify_loadout(owner, "Alpha#0100", loadout) is True


@pytest.mark.asyncio
async def test_readback_still_rejects_a_slot_that_really_differs() -> None:
    """负对照：槽里装的**真是另一颗**时不许放过（归一不是"永远通过"的开关）。"""
    owner = _owner(_profile(item_sockets={
        "gloves-1": [4183296050, 3685945823, SPECIAL_AMMO_ACCOUNT],  # 专注打击，不是回天掌法
    }))
    loadout = _loadout(
        mod_sockets={1: IMPACT_INDUCTION_SIGNED},
        mods=[SPECIAL_AMMO_SIGNED],
    )

    assert await loadout_verify.verify_loadout(owner, "Alpha#0100", loadout) is False


@pytest.mark.asyncio
async def test_readback_compares_subclass_plugs_in_one_domain() -> None:
    """子职业那一半同理：`plug_sockets` 与碎片两组 hash 都要能对上账号侧的无符号值。"""
    owner = _owner(_profile(
        subclass_sockets=[1444664836, IMPACT_INDUCTION_ACCOUNT, FRAGMENT_ACCOUNT],
    ))
    loadout = Loadout(
        id="exact",
        name="已确认的精确配装",
        character="warlock",
        items=[],
        subclass=LoadoutSubclassConfig(
            subclass_item_hash=3893112950,
            subclass_instance_id="sub-1",
            plug_sockets={1: IMPACT_INDUCTION_SIGNED},
            fragment_hashes=[FRAGMENT_SIGNED],
        ),
    )

    assert await loadout_verify.verify_loadout(owner, "Alpha#0100", loadout) is True


@pytest.mark.asyncio
async def test_rollback_check_accepts_plan_hashes_in_the_signed_domain() -> None:
    """回滚核对（`verify_restored_items`）共用同一份逐槽判据 —— 那边恒为 False 的表现是
    "回滚明明成功却报自动恢复不完整"，所以同一个夹具要在这一侧也钉一遍。"""
    profile = _profile(item_sockets={"item-1": [
        4183296050, IMPACT_INDUCTION_ACCOUNT, SPECIAL_AMMO_ACCOUNT,
    ]})
    profile["characters"] = {"data": {"char-1": {"classType": 1}}}  # hunter
    profile["characterEquipment"] = {
        "data": {"char-1": {"items": [
            {"itemHash": 1371860250, "itemInstanceId": "item-1"},
        ]}}
    }
    owner = _owner(profile)
    target_states = {"item-1": LoadoutItem(
        item_hash=1371860250,
        name="光芒领主手套",
        slot="gauntlets",
        item_instance_id="item-1",
        source_location="hunter",
        was_equipped=True,
        mod_sockets={1: IMPACT_INDUCTION_SIGNED},
    )}

    assert await loadout_verify.verify_restored_items(owner, "Alpha#0100", target_states) is True


@pytest.mark.asyncio
async def test_slot_matcher_accepts_both_domains_and_nothing_else() -> None:
    """判据本身的边界：同域（无符号）照旧通过、跨域通过、**真不一致仍然不通过**、
    槽位不存在不通过。"""
    sockets = [{"plugHash": 0}, {"plugHash": IMPACT_INDUCTION_ACCOUNT}]

    assert sockets_match(sockets, {1: IMPACT_INDUCTION_ACCOUNT}) is True
    assert sockets_match(sockets, {1: IMPACT_INDUCTION_SIGNED}) is True
    assert sockets_match(sockets, {0: IMPACT_INDUCTION_SIGNED}) is False
    assert sockets_match(sockets, {5: IMPACT_INDUCTION_SIGNED}) is False


# ── ② 窗口：只有一处、只开一轮 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_readback_window_is_retried_and_stops_at_the_first_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """窗口仍然按 `write_readback` 重试（同步窗口是真的），且对上就立刻停 —— 不再多读。"""
    monkeypatch.setattr(write_readback, "ATTEMPTS", 4)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)
    owner = _owner(_profile())
    reads = []

    async def third_time(*args):
        reads.append(1)
        return len(reads) >= 3

    monkeypatch.setattr(loadout_verify, "verify_loadout", third_time)

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]), blocked=BlockedMods()
    )

    assert verified is True
    assert len(reads) == 3, f"要对上为止（实际 {len(reads)} 次），对上之后不许再读"
    assert "已回读核对" in detail


@pytest.mark.asyncio
async def test_readback_verdict_says_the_window_it_actually_waited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没对上时那句话要报**真实等待**：`write_readback` 现读，而不是各写一份 `ATTEMPTS×延误`。

    2026-10-03 之前外层自己措辞，写成"回读重试 12 秒后仍对不上"——12 = 8×1.5，而循环里
    sleep 只有 7 次（10.5 秒，第一次读是立刻发生的）；真机上一轮窗口的实际代价更是约 78 秒
    （10.5 秒等待 + 8 次整份档案回读）。这里把 `ATTEMPTS` 压到 3：文案要跟着变。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 3)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)
    owner = _owner(_profile())
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(loadout_verify, "verify_loadout", verify)

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]), blocked=BlockedMods()
    )

    assert verified is False
    assert verify.await_count == 3, "**没有** blocked 时窗口照跑满，不许跟着一起提前收手"
    assert "重试 3 次" in detail, detail
    assert "别当成没装上" in detail, detail


@pytest.mark.asyncio
async def test_readback_verdict_keeps_a_crash_out_of_the_write_conclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回读自己炸了 = **没有证据**，不是"写入失败"：`TimeoutError()` 的空 str 也要有原因。"""
    owner = _owner(_profile())
    monkeypatch.setattr(
        loadout_verify, "verify_loadout", AsyncMock(side_effect=TimeoutError())
    )

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]), blocked=BlockedMods()
    )

    assert verified is False
    assert "回读核对没做成" in detail, detail
    assert detail.strip() != "回读核对没做成：", "空原因等于没解释（TimeoutError 的 str 是空的）"


# ── ③ 该收手时收手：计划目标态里的那颗写不成 → 一次都不读 ────────────────────


def _blocked(*, upstream: int = 0, preflight: int = 0, skipped: int = 0) -> BlockedMods:
    """凑一份"写不成的模组"账 —— 三种写法就是生产里那三条路各自记进 `BlockedMods.add` 的值。

    `mod` = 写完被上游拒 / `blocked` = 组件 207 预检判死 / `skipped` = 照抄模组落不下。
    """
    blocked = BlockedMods()
    for index in range(upstream):
        blocked.add("mod", f"护甲{index}", 100 + index, "要材料（1675）")
    for index in range(preflight):
        blocked.add("blocked", f"护甲{index}", 200 + index, "不在可插入清单里（组件 207）")
    for index in range(skipped):
        blocked.add("skipped", f"护甲{index}", 300 + index, "能量不够（要 12/11）")
    return blocked


@pytest.mark.asyncio
async def test_readback_with_a_mod_rejected_upstream_never_opens_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游拒绝的那颗：**一次都不读**，两类原因还要分开报数。

    被拒绝的那颗永远不在账号上，判据只可能给 False；跑满窗口的代价是真机实测的 70.13 秒
    （8 次读 59.63 秒 + 7 次 sleep 10.5 秒，`ATTEMPTS=8`/`DELAY_SECONDS=1.5` 就是这一档）。
    这里把窗口留在**真档位**上钉：只要读了一次，`await_count` 就不再是 0。

    口径是"没有回读核对"、不是"核对过、只是没对上"：判据在**第一个不一致处**就返回 False，
    我们并不知道那一处是不是这颗模组，所以说"其余项核对过了"是句无证据的话。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 8)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 1.5)
    owner = _owner(_profile())
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(loadout_verify, "verify_loadout", verify)

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]),
        blocked=_blocked(upstream=2),
    )

    assert verified is False
    assert verify.await_count == 0, f"有 blocked 就不许开窗口（读了 {verify.await_count} 次）"
    assert "这次没有回读核对" in detail, detail
    assert "2 颗被上游拒绝" in detail, detail
    assert "0 颗在组件 207 预检就被判死" in detail, f"两类要分开报数，缺一类就是含糊：{detail}"
    assert "mod_blocked" in detail, detail
    assert "可能是同步窗口" not in detail, detail


@pytest.mark.asyncio
async def test_readback_stops_for_a_mod_the_preflight_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**预检判死**的那颗（计划目标态）同样一次都不读 —— 这是修掉的那个活口。

    构造用例量到的原文：预检挡下 1 颗 → 回读仍跑满 8 次，`verify` 步还是"…可能是同步窗口，
    过十几秒再看一次…"（**对这颗是错的话术**：它不是没同步，是永远不会成立），`success=True`。
    判据按"这颗是不是计划要写进某个槽的"定，不按"上游拒了几颗"。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 8)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 1.5)
    owner = _owner(_profile())
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(loadout_verify, "verify_loadout", verify)

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]),
        blocked=_blocked(preflight=1),
    )

    assert verify.await_count == 0, "预检判死的也要提前收手（真机白等 70 秒的就是这一档）"
    assert verified is False
    assert "0 颗被上游拒绝" in detail, f"预检判死 ≠ 上游拒绝（那颗连请求都没发过）：{detail}"
    assert "1 颗在组件 207 预检就被判死" in detail, detail


@pytest.mark.asyncio
async def test_a_skipped_functional_mod_does_not_stop_the_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反面：照抄模组落不下（`skipped`）**不许**拉进来 —— 台账记错档就白丢一次能过的核对。

    `_plan_functional_mods` 对没写成的槽**不写 `mod_sockets`**（只有写进去的才记，见那边的
    夹具自证），回读根本不看它；所以它没资格让核对提前收手。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 3)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)
    owner = _owner(_profile())
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(loadout_verify, "verify_loadout", verify)

    detail, verified = await loadout_verify.readback_verdict(
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]),
        blocked=_blocked(skipped=1),
    )

    assert verify.await_count == 1, "照抄跳过不在目标态里，回读必须照跑"
    assert verified is True
    assert "已回读核对" in detail, detail


# ── ④ 端到端：预检判死的那颗真的让执行流程收手（真机 70 秒就是这一条） ────────

MOD_CATEGORY = 2487827355      # enhancements.v2_general（属性模组）
PLUG_SET = 2000000001          # 这一位角色的可插入清单（组件 207 的 plug set）
OTHER_MOD = 25154119           # 清单里**有**的另一颗：钉住"判死的是这一颗、不是清单为空"
HELMET_HASH = 3091179819       # 光芒领主面具（真机 hash）
HELMET_INSTANCE = "6917530188460608169"
BLOCKED_MOD = 1180408010       # 生命值模组


class _Manifest:
    """只答这条链要问的定义：护甲的插槽（带 plug set）、那颗模组的类别/能量/插入条件。"""

    def get_item_definition(self, item_hash: int) -> dict:
        if item_hash == HELMET_HASH:
            return {"sockets": {"socketEntries": [
                {"reusablePlugSetHash": PLUG_SET}, {"reusablePlugSetHash": PLUG_SET},
            ]}}
        if item_hash == BLOCKED_MOD:
            return {"plug": {
                "plugCategoryHash": MOD_CATEGORY,
                "energyCost": {"energyCost": 2},
                "insertionRules": [{"failureMessage": "必须在赛季神器中选择"}],
            }}
        return {}

    def get_item_info(self, item_hash: int) -> dict:
        return {}

    def get_item_name(self, item_hash: int) -> str:
        return {BLOCKED_MOD: "生命值模组"}.get(item_hash, "")


def _write_profile() -> dict:
    return {
        "characters": {"data": {"char-1": {"classType": 2}}},
        "itemComponents": {
            "instances": {"data": {
                HELMET_INSTANCE: {"energy": {"energyCapacity": 11, "energyUsed": 0}},
            }},
            "sockets": {"data": {
                HELMET_INSTANCE: {"sockets": [{"plugHash": 0}, {"plugHash": 0}]},
            }},
        },
        "characterPlugSets": {"data": {"char-1": {"plugs": {
            str(PLUG_SET): [{"plugItemHash": OTHER_MOD}],
        }}}},
    }


def _equipment_service() -> LoadoutEquipmentService:
    service = LoadoutEquipmentService(
        MagicMock(name="bungie"), _Manifest(), MagicMock(name="resolver"),
    )
    service._resolver.resolve_player = AsyncMock(
        return_value={"membership_id": "mid", "membership_type": 3}
    )
    service._resolver.resolve_character_id = AsyncMock(return_value="char-1")
    service._resolver.get_profile = AsyncMock(return_value=_write_profile())
    service._transfer.transfer_item = AsyncMock(return_value=SimpleNamespace(success=True))
    service._transfer.equip_items = AsyncMock(return_value={"success": True})
    service._capture_recovery_state = AsyncMock(return_value={
        "membership_id": "mid",
        "membership_type": 3,
        "character_id": "char-1",
        "previous_loadout": Loadout(id="prev", name="执行前配装", character="warlock", items=[]),
        "target_states": {},
    })
    return service


def _write_loadout() -> Loadout:
    """一件护甲、一颗**计划要写进插槽 1** 的模组 —— 而组件 207 的清单里没有它。"""
    return Loadout(
        id="local-1",
        name="猎套",
        character="warlock",
        items=[LoadoutItem(
            item_hash=HELMET_HASH,
            name="光芒领主面具",
            slot="helmet",
            item_instance_id=HELMET_INSTANCE,
            mod_sockets={1: BLOCKED_MOD},
        )],
    )


@pytest.mark.asyncio
async def test_preflight_blocked_mod_never_opens_the_window_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """走完 `equip_local` 全程：这一颗由**预检**判死 → 回读一次都不读，且三个副作用都没发生。

    这条是端到端的：`_mod_preflight`、`_prepare_mod_operations`、`readback_verdict` 全是真代码，
    只有上游写入与档案读取是替身。窗口留在**真档位**（8 × 1.5 秒）上钉 —— 真机那 70.13 秒
    就是这么来的；只要读了一次，`await_count` 就不再是 0。

    三个副作用各自一条断言：`success` 不许翻 False（装备那半确实成功了）、同一颗不许二次上报
    （聚合的 `mod_blocked` 只收上游拒绝那一类）、话术不许说成"被上游拒绝"（预检判死那颗连请求
    都没发过 —— 说成上游拒绝就是替上游编回执）。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 8)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 1.5)
    reads = AsyncMock(return_value=False)
    monkeypatch.setattr(loadout_verify, "verify_loadout", reads)
    service = _equipment_service()
    service._insert_armor_mod = AsyncMock()

    result = await service.equip_local("Alpha#0100", _write_loadout())

    assert reads.await_count == 0, f"预检判死的那颗不许再开窗口（读了 {reads.await_count} 次）"
    service._insert_armor_mod.assert_not_awaited()
    blocked_steps = [step for step in result.steps if step.action == "mod_blocked"]
    assert len(blocked_steps) == 1, f"同一颗只许报一条：{[s.detail for s in blocked_steps]}"
    assert "生命值模组" in blocked_steps[0].detail, blocked_steps[0].detail
    assert "写入之前" in blocked_steps[0].detail, "预检口径要说清还没往上写过"
    assert "被上游拒绝" not in blocked_steps[0].detail, "预检判死 ≠ 上游拒绝"
    verify_step = next(step for step in result.steps if step.action == "verify")
    assert "这次没有回读核对" in verify_step.detail, verify_step.detail
    assert "1 颗在组件 207 预检就被判死" in verify_step.detail, verify_step.detail
    assert "0 颗被上游拒绝" in verify_step.detail, verify_step.detail
    assert "可能是同步窗口" not in verify_step.detail, "对这颗那是错的话术"
    assert result.verified is False
    assert result.success is True, "装备那半确实成功了：预检判死一颗不许把 success 翻成 False"
    assert "装备已经换上" in result.message, result.message
    assert "1 颗模组在预检就被判死" in result.message, "回执要点名这一颗（它是账里唯一写不成的）"
    assert "被上游拒绝写入" not in result.message, "这句只留给上游真拒过的那一类"


# ── ⑤ 反面：**不算**计划目标态的那几类不许拦回读（真拒了照旧要报） ──────────


def _rejected_write() -> dict:
    """上游真回过 1676（插入规则没过）—— `mod_write_blocker` 认它的那条路。"""
    return {"ErrorCode": 1676, "ErrorStatus": "DestinyFailedPlugInsertionRules"}


@pytest.mark.asyncio
async def test_a_rejected_energy_clear_does_not_stop_the_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """②：`clear`（腾能量）被上游拒绝**不许**拦住回读 —— 那一格本来就不是计划目标态。

    `plan_energy_clearing` 只在**没被计划占用**的槽里挑（`assigned_sockets` 之外），所以腾不腾
    得出来都不影响"计划要的那套"；把它算成 blocked，代价是白丢一次本来能通过的核对 ——
    报"没有回读核对"而不是"核对通过"（原来只有代码阅读，这条用例把它钉住）。

    "不拦回读"**不等于**"不报"：被拒的事实照旧进 `mod_clear` 步与回执尾句。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 3)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)
    reads = AsyncMock(return_value=True)
    monkeypatch.setattr(loadout_verify, "verify_loadout", reads)
    service = _equipment_service()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("clear", OTHER_MOD, 0)]
    )
    service._insert_armor_mod = AsyncMock(return_value=_rejected_write())

    result = await service.equip_local("Alpha#0100", _write_loadout())

    assert reads.await_count == 1, "腾能量被拒不是计划目标态：回读必须照跑"
    clear_steps = [step for step in result.steps if step.action == "mod_clear"]
    assert clear_steps and clear_steps[0].success is False, "被拒的事实仍要进回执"
    verify_step = next(step for step in result.steps if step.action == "verify")
    assert "已回读核对" in verify_step.detail, verify_step.detail
    assert "没有回读核对" not in verify_step.detail, verify_step.detail
    assert "1 颗模组被上游拒绝写入" in result.message, "不拦回读 ≠ 不报：尾句照旧"
    assert result.success is False, "上游真拒过一颗：success 仍按老口径为 False"


@pytest.mark.asyncio
async def test_a_rejected_plan_mod_does_still_stop_the_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """② 的对照：同样被上游拒，但那一颗是**计划要写的** → 回读一次都不读。

    两条并排才说明判据不是"拒了几颗"，而是"拒的这一颗在不在计划目标态里"。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 8)
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 1.5)
    reads = AsyncMock(return_value=True)
    monkeypatch.setattr(loadout_verify, "verify_loadout", reads)
    service = _equipment_service()
    service._prepare_mod_operations = AsyncMock(
        return_value=[ModOperation("mod", BLOCKED_MOD, 1)]
    )
    service._insert_armor_mod = AsyncMock(return_value=_rejected_write())

    result = await service.equip_local("Alpha#0100", _write_loadout())

    assert reads.await_count == 0, "计划要写的那颗被上游拒 → 回读注定不通过，别再开窗口"
    verify_step = next(step for step in result.steps if step.action == "verify")
    assert "没有回读核对" in verify_step.detail, verify_step.detail
    assert "1 颗被上游拒绝" in verify_step.detail, verify_step.detail
    assert "0 颗在组件 207 预检就被判死" in verify_step.detail, verify_step.detail


@pytest.mark.asyncio
async def test_a_skipped_functional_mod_is_not_reported_as_an_upstream_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`skipped` 那颗**两份账都不进**：回执不许说"被上游拒绝写入"（它连请求都没发过），
    也不许因此把 `success` 翻成 False —— 它自己那一条 `mod_blocked` 步照旧报。

    注入违规（把 `OPERATION_KINDS["skipped"]` 记成 `(UPSTREAM, False)`）时本条会红：
    尾句会多出"1 颗模组被上游拒绝写入"、`success` 也会跟着翻。
    """
    monkeypatch.setattr(write_readback, "ATTEMPTS", 1)
    reads = AsyncMock(return_value=True)
    monkeypatch.setattr(loadout_verify, "verify_loadout", reads)
    service = _equipment_service()
    service._prepare_mod_operations = AsyncMock(return_value=[
        ModOperation("skipped", OTHER_MOD, 1, "能量不够（要 12/11）：这一颗跳过"),
    ])
    service._insert_armor_mod = AsyncMock()

    result = await service.equip_local("Alpha#0100", _write_loadout())

    service._insert_armor_mod.assert_not_awaited()
    assert reads.await_count == 1, "照抄跳过不在目标态里：回读照跑"
    blocked_steps = [step for step in result.steps if step.action == "mod_blocked"]
    assert len(blocked_steps) == 1, [step.detail for step in blocked_steps]
    assert "能量不够" in blocked_steps[0].detail, blocked_steps[0].detail
    assert "被上游拒绝写入" not in result.message, result.message
    assert result.success is True, "照抄落不下不是上游拒绝：不许拿它翻 success"
