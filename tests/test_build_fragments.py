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

import pytest

from destiny_mcp.exceptions import BuildValidationError
from destiny_mcp.models import LoadoutSubclassConfig
from destiny_mcp.services.build_fragments import replace_fragment_config

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

SIGNED, UNSIGNED = 0, 1  # 元组下标：两种写法


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


def _current_config() -> LoadoutSubclassConfig:
    """账号侧读出来的当前配置：**全部无符号**（`read_subclass_config` 直读 profile）。"""
    plugs = {0: SUPER, 8: ASPECT}
    plugs.update({9 + offset: pair[UNSIGNED] for offset, pair in enumerate(FRAGMENTS)})
    return LoadoutSubclassConfig(
        subclass_item_hash=SUBCLASS_HASH,
        subclass_instance_id="subclass-instance",
        super_hash=SUPER,
        aspect_hashes=[ASPECT],
        fragment_hashes=[pair[UNSIGNED] for pair in FRAGMENTS],
        plug_sockets=plugs,
    )


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
