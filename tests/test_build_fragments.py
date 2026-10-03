"""碎片配置替换的守门测试：hash 的两套值域（有符号/无符号）都必须匹配上。

真机 2026-10-03（棱镜术士，槽 9–14 共用碎片池 3916244727）：社区配装的碎片里有 4 颗
永远传不进去，报「碎片 -1668045176 与当前子职业插槽 9 不兼容」。根因不是"槽不对" ——
请求里的 hash 来自 `manifest.search()`（库里 `id` 列是**有符号**），插槽的
`singleInitialItemHash` / `reusablePlugItems` 在物品 JSON 体内（**无符号**），
只有 hash < 2^31 的碎片碰巧相等（使命 124726498 / 毁灭 124726499 能过就是证据）；
真机上换序重试时**报错跟着碎片走**，也说明问题在碎片自己身上。

夹具用的是真机那对值：`保护琢面` = -1668045176（有符号）↔ 2626922120（无符号）。
"""

from __future__ import annotations

import re

import pytest

from destiny_mcp.exceptions import BuildValidationError
from destiny_mcp.models import LoadoutSubclassConfig
from destiny_mcp.services.build_fragments import replace_fragment_config
from destiny_mcp.utils.hash_utils import to_unsigned

# ── 真机夹具：棱镜术士（Manifest 库 id 是 -401854346，账号侧读到的是 3893112950）──────
SUBCLASS_HASH = 3893112950
FRAGMENT_PLUG_SET = 3916244727      # 槽 9–14 共用的碎片池（物品 JSON 里就是这个无符号值）
EMPTY_FRAGMENT_SOCKET = 2808665197  # 「空碎片插槽」：这些槽的 `singleInitialItemHash`
SUPER = 3636300854                  # 0 号槽（超能）：碎片这条路不该碰到它
ASPECT = 790664814                  # 「地狱火」：负向对照（星象永远不在碎片池里）

# 真机碎片池里的六颗（池子总共 22 颗，这里只留这六颗）。元组 = (有符号, 无符号)；
# 前四颗 > 2^31、两种写法不同 —— 真机上就是它们被判"不兼容"；后两颗 < 2^31，写法同值。
PROTECTION = (-1668045176, 2626922120)  # 保护琢面
HOPE = (-1668045174, 2626922122)        # 希望琢面
COURAGE = (-1668045172, 2626922124)     # 勇气琢面
DAWN = (-1668045170, 2626922126)        # 黎明琢面
MISSION = (124726498, 124726498)        # 使命琢面
RUIN = (124726499, 124726499)           # 毁灭琢面
FRAGMENTS = (PROTECTION, HOPE, COURAGE, DAWN, MISSION, RUIN)

#: 报错里要念名字（"9=保护琢面"），替身得按 hash 给得出官方中文名。
_NAMES = {
    "保护琢面": PROTECTION, "希望琢面": HOPE, "勇气琢面": COURAGE,
    "黎明琢面": DAWN, "使命琢面": MISSION, "毁灭琢面": RUIN,
}

SIGNED, UNSIGNED = 0, 1  # 元组下标：两种写法


def _counts(message: str) -> tuple[int, int]:
    """从报错里抠出「能写 N 颗」与「可写的碎片槽 M 个」。

    两个数必须是同一个数 —— 它们来自同一份判据（`fragment_sockets`），分开写就会漂移。
    """
    can = re.search(r"能写 (\d+) 颗", message)
    listed = re.search(r"可写的碎片槽 (\d+) 个", message)
    assert can and listed, message
    return int(can.group(1)), int(listed.group(1))


def _as_signed(value: int) -> int:
    """无符号写法 → 库里（Manifest `id` 列）的有符号写法。"""
    return value - 2**32 if value >= 2**31 else value


class _Manifest:
    """真 Manifest 用到的两张口；`signed_manifest` 让整份定义换个值域写。"""

    def __init__(self, *, signed_manifest: bool = False) -> None:
        self._signed = signed_manifest
        self._definition = self._subclass_definition()
        self._plug_set = self._fragment_pool()

    def _write(self, value: int) -> int:
        return _as_signed(value) if self._signed else value

    def _fragment_pool(self) -> dict:
        return {
            "reusablePlugItems": [
                {"plugItemHash": self._write(unsigned)} for _, unsigned in FRAGMENTS
            ]
        }

    def _subclass_definition(self) -> dict:
        entries: list[dict] = [{} for _ in range(15)]
        entries[0] = {"singleInitialItemHash": self._write(SUPER)}
        entries[8] = {"singleInitialItemHash": self._write(ASPECT)}
        for index in range(9, 15):
            entries[index] = {
                "singleInitialItemHash": self._write(EMPTY_FRAGMENT_SOCKET),
                "reusablePlugSetHash": self._write(FRAGMENT_PLUG_SET),
            }
        return {"sockets": {"socketEntries": entries}}

    def get_item_definition(self, item_hash: int):
        assert item_hash == SUBCLASS_HASH, "子职业定义是按账号侧那份 hash 取的"
        return self._definition

    def get_definition(self, table: str, hash_id: int):
        assert table == "DestinyPlugSetDefinition"
        assert hash_id in (FRAGMENT_PLUG_SET, _as_signed(FRAGMENT_PLUG_SET))
        return self._plug_set

    def get_item_name(self, item_hash: int) -> str:
        """报错里要念出槽里现在装的是哪颗（"9=保护琢面"），所以替身得给得出名字。"""
        unsigned = to_unsigned(item_hash)
        for name, (signed, plain) in _NAMES.items():
            if unsigned == to_unsigned(plain) or unsigned == to_unsigned(signed):
                return name
        return ""


def _current_config() -> LoadoutSubclassConfig:
    """账号侧读出来的当前配置：**全部无符号**（`read_subclass_config` 直读 profile）。

    槽 9–14 是**账号报出来的**一排碎片槽（`fragment_sockets`），其中 socket 14 的
    `isEnabled` 是 false（棱镜术士）。所以这份替身照现场写：六个碎片槽都记在
    `fragment_sockets` 里，但 `plug_sockets` 里只有开着的那些。
    """
    plugs = {0: SUPER, 8: ASPECT}
    plugs.update({9 + offset: pair[UNSIGNED] for offset, pair in enumerate(FRAGMENTS)})
    return LoadoutSubclassConfig(
        subclass_item_hash=SUBCLASS_HASH,
        subclass_instance_id="subclass-instance",
        super_hash=SUPER,
        aspect_hashes=[ASPECT],
        fragment_hashes=[pair[UNSIGNED] for pair in FRAGMENTS],
        plug_sockets=plugs,
        socket_states={**{0: True, 8: True}, **{9 + i: True for i in range(6)}},
        fragment_sockets=[9, 10, 11, 12, 13, 14],
    )


def _live_config() -> LoadoutSubclassConfig:
    """真机现场那份：socket 14 禁用，所以 `plug_sockets` 里只有 9–13（**5 个可写碎片槽**）。

    这就是"第 6 颗碎片被排进 socket 14"的起点 —— 14 不在可排槽位里，但仍在
    `fragment_sockets` 里（账号报过它是个碎片槽），所以报错能点名它、说明它被禁用。
    """
    live = _current_config()
    return live.model_copy(update={
        "plug_sockets": {i: h for i, h in live.plug_sockets.items() if i != 14},
        "fragment_hashes": [pair[UNSIGNED] for pair in FRAGMENTS[:5]],
        "socket_states": {0: True, 8: True, **{9 + i: True for i in range(5)}, 14: False},
    })


def _requested(writing: int) -> list[int]:
    """社区配装那六颗；`writing` 决定请求整体用哪种写法（真机那份来自 `manifest.search()`）。"""
    return [pair[writing] for pair in FRAGMENTS]


@pytest.mark.parametrize("writing", [SIGNED, UNSIGNED], ids=["请求有符号", "请求无符号"])
@pytest.mark.parametrize("signed_manifest", [False, True], ids=["清单无符号", "清单有符号"])
def test_碎片的两种写法都必须匹配上(writing: int, signed_manifest: bool) -> None:
    """有符号与无符号两种写法喂进来，都得算同一颗碎片。

    没归一的时候，`请求有符号` 这一路是真机上的常态（`manifest.search()` 给的就是有符号），
    而 `保护/希望/勇气/黎明` 四颗全部在这里被判"与插槽不兼容"。
    """
    manifest = _Manifest(signed_manifest=signed_manifest)

    result = replace_fragment_config(manifest, _current_config(), _requested(writing))

    assert result.fragment_hashes == [pair[UNSIGNED] for pair in FRAGMENTS], (
        "写回去的必须归到无符号：账号侧那份就是无符号，写完的回读核对（`_verify_loadout`）"
        "是拿插槽裸值逐位比的"
    )
    assert result.plug_sockets[9] == PROTECTION[UNSIGNED]
    assert result.plug_sockets[14] == RUIN[UNSIGNED]
    assert (result.plug_sockets[0], result.plug_sockets[8]) == (SUPER, ASPECT), (
        "超能与星象那两个槽不在碎片路上，不许被动"
    )


def test_空着的碎片槽也算一个能填的槽() -> None:
    """槽里躺着 `空碎片插槽` 占位：它是**一个空着的碎片槽**，不是"没有这个槽"。

    否则玩家换碎片时会被要求"少给一颗"，而那一颗本来就能填进去。
    """
    live = _live_config()
    empty = live.model_copy(update={
        "plug_sockets": {**live.plug_sockets, 13: EMPTY_FRAGMENT_SOCKET},
        "fragment_hashes": [pair[UNSIGNED] for pair in FRAGMENTS[:4]],
    })
    requested = [pair[UNSIGNED] for pair in FRAGMENTS[:5]]

    result = replace_fragment_config(_Manifest(), empty, requested)

    assert result.plug_sockets[13] == MISSION[UNSIGNED], (
        "第 5 颗（使命琢面）要落进那个空着的槽 13"
    )
    assert 14 not in result.plug_sockets, "禁用槽 14 不许被写"


def test_禁用槽不会被当成碎片槽() -> None:
    """**守门**：真机那 6 颗碎片里有 1 颗会被排进禁用的 socket 14 —— 必须拦住并说清怎么补。

    真机原文（`equip_build` 的 `subclass` 步骤）：
    HTTP 500 `DestinySocketActionNotAllowed`，
    `request.plug.socketIndex: The requested socket is disabled.`；
    同一颗碎片写进 socket 12 成功，所以碎片没问题，是槽不可写。
    现场是 5 个可用碎片槽（9–13）+ 1 个禁用槽（14），社区模板给 6 颗 → 多 1 颗。
    """
    with pytest.raises(BuildValidationError) as excinfo:
        replace_fragment_config(_Manifest(), _live_config(), _requested(UNSIGNED))

    message = str(excinfo.value)
    # 文案本身也钉住：旧版是「必须提供完整碎片配置：…需要 6 个，收到 5 个。」——
    # 只报数不报槽，玩家没法行动。断言前四个字是为了"退回旧文案"能被抓住。
    assert message.startswith("碎片配置对不上："), message
    assert "收到 6 颗" in message and "多 1 颗" in message, message
    assert _counts(message) == (5, 5), message
    assert "槽 14 被账号禁用" in message, message
    assert "补哪一颗由你定" in message, message
    assert "9=保护琢面" in message, message


def test_少了碎片时点名空着的槽() -> None:
    """少给几颗时也要说清"哪些槽空着、缺几颗"，而不是一句"收到 5 个"。"""
    live = _live_config()
    empty = live.model_copy(update={
        "plug_sockets": {**live.plug_sockets, 13: EMPTY_FRAGMENT_SOCKET},
        "fragment_hashes": [pair[UNSIGNED] for pair in FRAGMENTS[:4]],
    })

    with pytest.raises(BuildValidationError) as excinfo:
        replace_fragment_config(_Manifest(), empty, [pair[UNSIGNED] for pair in FRAGMENTS[:2]])

    message = str(excinfo.value)
    assert message.startswith("碎片配置对不上："), message
    assert "收到 2 颗" in message and "少 3 颗" in message, message
    assert _counts(message) == (5, 5), message
    assert "空着的是槽 13" in message, message


def test_禁用槽即使出现在槽表里也不算可写碎片槽() -> None:
    """**守门**：禁用槽到底算不算一个能排的碎片槽 —— 数数与报错必须是同一份判据。

    真机那条路（棱镜术士）是 `read_subclass_config` 先把禁用槽滤掉了；但**判据不能只活在
    读取那一处**：任何一份带着 `socket_states` 的现场（旧的候选方案、手拼的配置）到这里，
    禁用槽都不许被算成"一颗碎片的位置"—— 真机排进 socket 14 的后果就是上游 500
    `DestinySocketActionNotAllowed`（`request.plug.socketIndex: The requested socket is disabled.`）。
    """
    live = _live_config()
    stale = live.model_copy(update={
        # 旧方案把禁用槽 14 也写了进来（真机那份 canonical_build 就长这样）
        "plug_sockets": {**live.plug_sockets, 14: MISSION[UNSIGNED]},
        "fragment_hashes": [pair[UNSIGNED] for pair in FRAGMENTS[:5]],
        "socket_states": {**live.socket_states, 14: False},
    })

    with pytest.raises(BuildValidationError) as excinfo:
        replace_fragment_config(_Manifest(), stale, _requested(UNSIGNED))

    message = str(excinfo.value)
    assert message.startswith("碎片配置对不上："), message
    # 「能写几颗」与「列了几个槽」必须是同一个数：错开的话（注入一次就复现：5 vs 6）玩家照
    # 清单补一颗就正好补到禁用槽上，正是那个上游 500。
    assert _counts(message) == (5, 5), message
    assert "14=" not in message, "禁用槽 14 不许出现在可写清单里"
    assert "槽 14 被账号禁用" in message, message


@pytest.mark.parametrize(
    ("intruder", "why"),
    [
        (ASPECT, "星象根本不是碎片"),
        (2626922127, "这份替身池子里没有的碎片（真机池子更大，这里钉的是成员判断仍然成立）"),
        (_as_signed(2626922127), "同上，有符号写法"),
    ],
    ids=["星象", "池外碎片-无符号", "池外碎片-有符号"],
)
def test_不在池子里的插件仍然要拦(intruder: int, why: str) -> None:
    """归一是把两边放到同一值域，**不是一律放行** —— 真塞不进去的仍要报出来。"""
    requested = [intruder, *[pair[SIGNED] for pair in FRAGMENTS[1:]]]

    with pytest.raises(BuildValidationError) as excinfo:
        replace_fragment_config(_Manifest(), _current_config(), requested)

    assert "槽 9" in str(excinfo.value), why
