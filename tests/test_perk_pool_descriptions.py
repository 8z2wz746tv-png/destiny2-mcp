"""perk 池必须带描述（接线守门）。

2026-10-10 实测：玉兔的池子原来 11 个选项**一个描述都没有** —— 模型答不出"这个 perk 什么效果"，
还得再查一次（`perk_description`）。`socket_list` 本来就有 `include_descriptions` 开关，
只是定义级默认关；这条钉住"池子出口把它打开"。

为什么是接线守门而不是拿真 manifest 断言：真 manifest 不是每台机器都有（干净 HOME 也要能跑），
所以这里替换 `socket_list`，验的是"这条路径**确实传了**这个开关" —— 字段本身由
`weapon_payload` 的实测（玉兔 2.4 → 3.0 KB）保证。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from destiny_mcp.services import perk_service, weapon_payload


@pytest.mark.asyncio
async def test_perk_pool_asks_for_descriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_socket_list(manifest, definition, **kwargs):
        seen.update(kwargs)
        return [{"slot": "trait", "options": [{"name": "愚者宿命", "description": "连续精准射击…"}]}]

    monkeypatch.setattr(weapon_payload, "socket_list", fake_socket_list)
    monkeypatch.setattr(
        perk_service.weapon_profile, "find_weapon",
        lambda _m, _n: (123, {"hash": 123, "displayProperties": {"name": "玉兔"}}),
    )
    svc = perk_service.PerkService(MagicMock(), None, None)
    svc.god_roll_lookup = lambda _h: (False, False)

    result = await svc.get_weapon_perks("玉兔")

    assert seen.get("include_descriptions") is True, f"池子出口必须打开描述：{seen}"
    # 图标同样要开（2026-10-10 用户要求）：没图渲染不出卡片，还得再查一次。
    # 实测：玉兔 11/11 带图 4.9 KB；鹰月 89/89 带图 31.5 KB（不带图 22.3 KB）。
    assert seen.get("include_icons") is True, f"池子出口必须打开图标：{seen}"
    options = result["sockets"][0]["options"]
    assert options[0].get("description"), "描述必须一路带到出口"


@pytest.mark.asyncio
async def test_descriptions_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关要真的可控（有些出口不需要描述，别写死）。"""
    seen: dict[str, Any] = {}

    def fake_socket_list(manifest, definition, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(weapon_payload, "socket_list", fake_socket_list)
    monkeypatch.setattr(
        perk_service.weapon_profile, "find_weapon",
        lambda _m, _n: (123, {"hash": 123, "displayProperties": {"name": "玉兔"}}),
    )
    svc = perk_service.PerkService(MagicMock(), None, None)
    svc.god_roll_lookup = lambda _h: (False, False)

    await svc.get_weapon_perks("玉兔", include_descriptions=False)
    assert seen.get("include_descriptions") is False, seen

    await svc.get_weapon_perks("玉兔", include_icons=False)
    assert seen.get("include_icons") is False, seen
