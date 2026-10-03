"""配置比对：**"要的那一套"与账号现在报的，是不是同一套**（装备插槽与子职业两半）。

从 `loadout_verify` 拆出来（那边贴着体量上限），拆的理由不止体量：这里放的是**判据本身**
（逐槽位比、集合比、子职业本体是不是那一个），而 `loadout_verify` 放的是"两次核对的入口"
（装备这一趟与回滚那一趟：去读 profile、拿实例号、调用这里的判据）。

**每一处 hash 比较都要两边归一 —— 这是 2026-10-03 真机那 147.8 秒的根因**：
账号侧（组件 305 的 `plugHash`）一律是无符号，而"要的是哪一颗"有两条来源 —— 存档配装读
profile（无符号），`manifest.search()` 与社区配装模板的 `functional_mod_groups` 给**有符号**。
真机 audit `/123435`（`equip_build`）：`光芒领主手套` 记下的 `回天掌法` 是 `-1847517590`，
同一颗在账号上是 `2447449706`；`黎明副歌` 记的 `特殊武器弹药搜寻者` 是 `-519166499`，
账号上是 `2595839237`。写入那条路只在**发请求时**归一
（`bungie_client.insert_socket_plug_free` 的 `to_unsigned`），所以账号是对的、只有判据错 ——
后果不是报错，是**恒为 False**：两次回读窗口各烧约 78 秒后报"对不上"，而事后独立回读证明
五件装备、22 颗模组、2 条调谐全部落盘；同一晚紧接着的 `equip_loadout`（存档配装 = 无符号）
回读一次就通过。`tests/test_hash_domains.py` 的横切扫描认得出这里的归一写法，别把它藏进
一个"取 hash 的小函数"里 —— 那样比较就不再被扫到，以后有人改回裸比也没人拦。

**判据只回答是/不是**：不解释为什么（哪一项不一致由调用方的话术去说）。
"""

from __future__ import annotations

from typing import Any

from ..models import Loadout
from ..utils.hash_utils import to_unsigned


def _installed_plug(sockets: list[dict], socket_index: int) -> dict:
    """某个槽的原始记录；槽不存在时给空字典（调用方一律先过 `to_unsigned`）。

    **故意不返回 hash**：比较要写成 `to_unsigned(d.get("plugHash", 0)) != to_unsigned(h)`
    的原地形状，`tests/test_hash_domains.py` 的横切扫描才看得见它、并认出它归过一
    （`_is_hashy` 只认 `…Hash` 字段读与 `to_unsigned`/`to_signed`/`hash_variants` 的调用）。
    """
    return sockets[socket_index] if socket_index < len(sockets) else {}


def sockets_match(sockets: list[dict], expected: dict[int, int]) -> bool:
    """这些槽位上装的，是不是要的那几颗 —— **位置也要对上**（`mod_sockets` 是"挑中的那一格"）。"""
    for socket_index, plug_hash in expected.items():
        installed = _installed_plug(sockets, socket_index)
        if to_unsigned(installed.get("plugHash", 0) or 0) != to_unsigned(plug_hash):
            return False
    return True


def plugs_present(sockets: list[dict], mods: list[int]) -> bool:
    """这几颗在不在这一件上装着 —— **不限定槽位**（求解器只说"这件要带这几颗"）。"""
    actual_plugs = {
        to_unsigned(socket.get("plugHash", 0) or 0) for socket in sockets
    }
    return all(to_unsigned(mod_hash) in actual_plugs for mod_hash in mods)


def subclass_matches(
    manifest: Any, equipped: list[dict], sockets_data: dict, loadout: Loadout
) -> bool:
    """子职业那一半：本体是不是要的那一个，技能/星象/碎片在不在。

    两判并列，因为求解读与存档读给的东西不一样：`plug_sockets` 是**逐槽位**的（换碎片是按
    槽位写进去的，位置错了就是错），而超能/手雷/近战/职业技能/跳跃/星象/碎片是**集合式**的
    （"这一件上装着"就够了，求解方案不保证槽号）。
    """
    subclass = loadout.subclass
    if subclass is None:
        return True
    subclass_item = next(
        (
            raw
            for raw in equipped
            if (manifest.get_item_info(raw.get("itemHash", 0)) or {}).get("itemType")
            == 16
        ),
        None,
    )
    if not subclass_item:
        return False
    subclass_id = str(subclass_item.get("itemInstanceId", ""))
    expected_item_hash = to_unsigned(subclass.subclass_item_hash)
    if (
        expected_item_hash
        and to_unsigned(subclass_item.get("itemHash", 0) or 0) != expected_item_hash
    ) or (
        subclass.subclass_instance_id
        and subclass_id != subclass.subclass_instance_id
    ):
        return False
    subclass_sockets = sockets_data.get(subclass_id, {}).get("sockets", [])
    if not sockets_match(subclass_sockets, subclass.plug_sockets):
        return False
    actual_plugs = {
        to_unsigned(socket.get("plugHash", 0) or 0) for socket in subclass_sockets
    }
    expected_plugs = {
        to_unsigned(plug_hash)
        for plug_hash in (
            subclass.super_hash,
            subclass.grenade_hash,
            subclass.melee_hash,
            subclass.class_ability_hash,
            subclass.movement_hash,
            *subclass.aspect_hashes,
            *subclass.fragment_hashes,
        )
    }
    expected_plugs.discard(0)
    return expected_plugs.issubset(actual_plugs)
