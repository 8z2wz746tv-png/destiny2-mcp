"""回读判据的守门：**"装好了"不许被报成"对不上"**，而且只许读一轮窗口。

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
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.models import Loadout, LoadoutItem, LoadoutSubclassConfig
from destiny_mcp.services import loadout_verify, write_readback
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
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[])
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
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[])
    )

    assert verified is False
    assert verify.await_count == 3, "**没有** blocked 时窗口照跑满，不许跟着一起提前收手"
    assert "重试 3 次" in detail, detail
    assert "别当成没装上" in detail, detail


@pytest.mark.asyncio
async def test_readback_with_blocked_mods_never_opens_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有模组被上游拒绝时**一次都不读**，并且必须**明说没核对**。

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
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[]), blocked_count=2
    )

    assert verified is False
    assert verify.await_count == 0, f"有 blocked 就不许开窗口（读了 {verify.await_count} 次）"
    assert "这次没有回读核对" in detail, detail
    assert "2 颗模组被上游拒绝写入" in detail, detail
    assert "mod_blocked" in detail, detail
    assert "可能是同步窗口" not in detail, detail


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
        owner, "Alpha#0100", _loadout(mod_sockets={}, mods=[])
    )

    assert verified is False
    assert "回读核对没做成" in detail, detail
    assert detail.strip() != "回读核对没做成：", "空原因等于没解释（TimeoutError 的 str 是空的）"
