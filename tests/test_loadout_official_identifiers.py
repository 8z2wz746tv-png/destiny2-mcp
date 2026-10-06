"""官方配装槽标识补齐（`services/loadout_official_identifiers.py`）的守门测试。

这里钉的是 2026-10-06 真机量出来的上级事实，不是我们的偏好：

- `SnapshotLoadout` / `UpdateLoadoutIdentifiers` **三个标识必须都给**，少一个就是
  HTTP 500 `DestinyInvalidRequest`；
- 空槽（三个标识都是哨兵 `2166136261`）**继承不到**任何东西，必须让调用方自己选；
- 三个都给了就**不该多读一次 profile**（真机 profile 要几百毫秒到一秒）。
"""

from __future__ import annotations

import pytest

from destiny_mcp.bungie_loadouts import UNSET_LOADOUT_IDENTIFIER
from destiny_mcp.services.loadout_official_identifiers import (
    NEED_ALL_THREE,
    current_identifiers,
    resolve_identifiers,
)

CHAR = "2305843009679355779"
FILLED = {"nameHash": 2755629633, "iconHash": 1143786719, "colorHash": 1693821585}
EMPTY = {
    "nameHash": UNSET_LOADOUT_IDENTIFIER,
    "iconHash": UNSET_LOADOUT_IDENTIFIER,
    "colorHash": UNSET_LOADOUT_IDENTIFIER,
}


def _profile(*rows: dict) -> dict:
    return {"characterLoadouts": {"data": {CHAR: {"loadouts": list(rows)}}}}


class FakeResolver:
    def __init__(self, profile: dict) -> None:
        self._profile = profile
        self.profile_reads = 0

    async def get_profile(self, mid, mtype, components):  # noqa: ANN001
        self.profile_reads += 1
        return self._profile


def test_current_identifiers_reads_a_filled_slot() -> None:
    got = current_identifiers(_profile(EMPTY, FILLED), CHAR, 1)
    assert got == {"name_hash": 2755629633, "icon_hash": 1143786719, "color_hash": 1693821585}


@pytest.mark.parametrize("index", [0, 5])
def test_current_identifiers_treats_empty_slots_as_nothing_to_inherit(index: int) -> None:
    """空槽三个都是哨兵 —— 不是"继承一个空名字"，是**没有可继承的东西**。"""
    assert current_identifiers(_profile(*([EMPTY] * 6)), CHAR, index) is None


def test_current_identifiers_ignores_out_of_range_and_unknown_character() -> None:
    assert current_identifiers(_profile(EMPTY), CHAR, 19) is None
    assert current_identifiers(_profile(FILLED), "999", 0) is None
    assert current_identifiers({}, CHAR, 0) is None


@pytest.mark.asyncio
async def test_resolve_with_all_three_does_not_read_the_profile() -> None:
    """三个都给齐 → 一次 profile 都不读（这是省下来的真机往返）。"""
    resolver = FakeResolver(_profile(FILLED))
    ids, problem = await resolve_identifiers(
        resolver, mid="1", mtype=3, char_id=CHAR, loadout_index=0,
        name_hash=1, icon_hash=2, color_hash=3,
    )
    assert ids == {"name_hash": 1, "icon_hash": 2, "color_hash": 3}
    assert problem == ""
    assert resolver.profile_reads == 0


@pytest.mark.asyncio
async def test_resolve_inherits_missing_identifiers_from_the_slot() -> None:
    """「只改名字」= 名字用新的，另外两个带旧值 —— 这正是 API 要求的那种表达。"""
    resolver = FakeResolver(_profile(FILLED))
    ids, problem = await resolve_identifiers(
        resolver, mid="1", mtype=3, char_id=CHAR, loadout_index=0,
        name_hash=752612103, icon_hash=None, color_hash=None,
    )
    assert problem == ""
    assert ids == {
        "name_hash": 752612103,
        "icon_hash": 1143786719,
        "color_hash": 1693821585,
    }
    assert resolver.profile_reads == 1


@pytest.mark.asyncio
async def test_resolve_inherits_all_three_when_caller_gives_none() -> None:
    resolver = FakeResolver(_profile(FILLED))
    ids, problem = await resolve_identifiers(
        resolver, mid="1", mtype=3, char_id=CHAR, loadout_index=0,
        name_hash=None, icon_hash=None, color_hash=None,
    )
    assert problem == ""
    assert ids == {"name_hash": 2755629633, "icon_hash": 1143786719, "color_hash": 1693821585}


@pytest.mark.asyncio
async def test_resolve_refuses_an_empty_slot_instead_of_sending_a_doomed_request() -> None:
    """空槽 + 没给标识 → 如实报"要自己选"，**不发**那个必然 500 的请求。"""
    resolver = FakeResolver(_profile(EMPTY))
    ids, problem = await resolve_identifiers(
        resolver, mid="1", mtype=3, char_id=CHAR, loadout_index=0,
        name_hash=752612103, icon_hash=None, color_hash=None,
    )
    assert ids is None
    assert problem == NEED_ALL_THREE
    assert "search_identifiers" in problem, "错误话术要把下一步说清"


@pytest.mark.asyncio
async def test_resolve_never_emits_the_unset_sentinel() -> None:
    """任何一条成功路径都不许把哨兵原样发出去（实测会被 Bungie 拒）。"""
    resolver = FakeResolver(_profile(FILLED))
    for kwargs in (
        {"name_hash": None, "icon_hash": None, "color_hash": None},
        {"name_hash": 7, "icon_hash": None, "color_hash": None},
        {"name_hash": None, "icon_hash": 8, "color_hash": 9},
    ):
        ids, problem = await resolve_identifiers(
            resolver, mid="1", mtype=3, char_id=CHAR, loadout_index=0, **kwargs
        )
        assert problem == ""
        assert UNSET_LOADOUT_IDENTIFIER not in ids.values()
