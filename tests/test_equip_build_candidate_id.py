"""`equip_build` 也认 `execution_id`：只发标量的宿主不必搬运整块 canonical_build。

真机（2026-09-23，豆包 connector）发不出结构体参数，于是"确认后装备"这条路整个用不了。
修法：候选 ID（标量）也能当凭据 —— 服务端凭它取回**自己签发的**那份方案，
内容根本不经过调用方，比回传整块 JSON 更不可能被篡改。

这个文件钉住四件事，缺一条这条路就会退回去：

1. `canonical_build` 与 `execution_id` 二选一，两个都没给要给出下一步；
2. 只给 ID 时确认回显里带 `execution_id`（宿主照抄就能再发一次），且**零写入**；
3. `confirmed=true` 后装的是服务端取回来的那份，不是调用方给的内容；
4. 取不到候选（不存在/已过期/不是你的）时指向重新求解，而不是一句"失败了"。
"""

from __future__ import annotations

from typing import Any

import pytest

from destiny_mcp.build.models import Armor, ArmorStats, BuildCandidate, BuildResult
from destiny_mcp.build_contracts import ExecutableBuild
from destiny_mcp.models import LoadoutItem
from destiny_mcp.tools import _armor_branches

SLOTS = ("helmets", "gauntlets", "chests", "legs", "class_items")
ARMOR_SLOTS = ("helmet", "gauntlets", "chest", "legs", "class_item")


def _candidate(execution_id: str = "cand-1") -> dict[str, Any]:
    return {
        "class_type": "hunter",
        "snapshot_version": "快照-1",
        "execution_id": execution_id,
        "items": [
            {
                "item_hash": 100 + index,
                "slot": slot,
                "item_instance_id": f"实例-{slot}",
                "mods": [1],
                "name": slot,
            }
            for index, slot in enumerate(ARMOR_SLOTS)
        ],
    }


class _BuildService:
    """记录"取候选"与"真装备"两次调用，顺序也记下来。"""

    def __init__(self, candidate: dict[str, Any]) -> None:
        self.candidate = candidate
        self.calls: list[tuple] = []

    # 同步方法：候选暂存在进程内，不碰网络（真机语料 ⑯b 抓到过工具层误 await）
    def get_build_candidate(self, player_name: str, execution_id: str) -> dict[str, Any]:
        self.calls.append(("get_build_candidate", player_name, execution_id))
        return self.candidate

    async def equip_build(self, player_name: str, build: Any, character: str) -> dict[str, Any]:
        self.calls.append(("equip_build", player_name, build, character))
        return {"success": True, "message": "已装备", "steps": []}


@pytest.fixture
def preview_stub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """逐件预览要读账号，这里换成空表：本文件测的是"用哪份方案"，不是预览本身。"""
    seen: list[dict[str, Any]] = []

    async def _preview(svc: Any, player_name: str, canonical_build: dict[str, Any]) -> list[dict]:
        seen.append(canonical_build)
        return []

    monkeypatch.setattr(_armor_branches, "equip_preview", _preview)
    return seen


async def test_equip_build_needs_a_candidate_or_its_id() -> None:
    result = await _armor_branches.equip_build({}, "Tester#1234", None, "", "hunter", False)

    assert result["error"]["code"] == "exact_build_required"
    assert "execution_id" in result["error"]["message"]
    assert "canonical_build" in result["error"]["message"]


async def test_a_bare_id_gets_a_confirmation_that_echoes_the_id(
    preview_stub: list[dict[str, Any]],
) -> None:
    service = _BuildService({"success": True, "build": _candidate()})

    result = await _armor_branches.equip_build(
        {"build_svc": service}, "Tester#1234", None, "cand-1", "hunter", False
    )

    assert result["error"]["code"] == "confirmation_required"
    echoed = result["candidates"][0]
    assert echoed["execution_id"] == "cand-1"
    assert echoed["character"] == "hunter"
    assert len(echoed["canonical_build"]["items"]) == 5
    assert [call[0] for call in service.calls] == ["get_build_candidate"], "确认阶段不许写账号"
    assert preview_stub and preview_stub[0]["execution_id"] == "cand-1"


async def test_confirmed_equip_uses_the_server_side_candidate(
    preview_stub: list[dict[str, Any]],
) -> None:
    service = _BuildService({"success": True, "build": _candidate()})

    result = await _armor_branches.equip_build(
        {"build_svc": service}, "Tester#1234", None, "cand-1", "hunter", True
    )

    assert result["ok"] is True
    kinds = [call[0] for call in service.calls]
    assert kinds == ["get_build_candidate", "equip_build"], "先取回签发的那份，再执行"
    _, player_name, build, character = service.calls[1]
    assert player_name == "Tester#1234"
    assert character == "hunter"
    # 服务端取回的那份（不是调用方传的内容）才进执行器
    assert isinstance(build, ExecutableBuild)
    assert build.execution_id == "cand-1"
    assert [item.item_instance_id for item in build.items] == [
        f"实例-{slot}" for slot in ARMOR_SLOTS
    ]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("unknown_execution_id", "该配装候选已失效或不属于当前玩家，请重新求解并确认。"),
        ("expired_execution_id", "该配装候选已过期，请重新求解并确认。"),
    ],
)
async def test_a_dead_candidate_id_says_how_to_recover(code: str, message: str) -> None:
    service = _BuildService({"success": False, "code": code, "message": message})

    result = await _armor_branches.equip_build(
        {"build_svc": service}, "Tester#1234", None, "cand-1", "hunter", True
    )

    assert result["error"]["code"] == code
    assert result["error"]["message"] == message
    assert result["next_actions"], "取不到候选时必须给下一步"
    assert [call[0] for call in service.calls] == ["get_build_candidate"], "没候选就不该执行"


async def test_a_full_canonical_build_does_not_need_a_lookup(
    preview_stub: list[dict[str, Any]],
) -> None:
    """两条路等价：给了整块候选就不查 ID（多一次查询是白花的往返）。"""
    service = _BuildService({"success": True, "build": _candidate()})

    result = await _armor_branches.equip_build(
        {"build_svc": service}, "Tester#1234", _candidate("cand-9"), "", "hunter", True
    )

    assert result["ok"] is True
    assert [call[0] for call in service.calls] == ["equip_build"]
    assert service.calls[0][2].execution_id == "cand-9"


def test_result_rows_expose_the_candidate_id_as_a_scalar() -> None:
    """候选行顶层再露一份 execution_id：只发标量的宿主一眼就能取到它。"""
    items = [
        LoadoutItem(item_hash=1, slot=slot, item_instance_id=f"实例-{slot}", mods=[1], name=slot)
        for slot in ARMOR_SLOTS
    ]
    armor = [
        Armor(item_instance_id=f"实例-{slot}", item_hash=1, name=slot, slot=slot,
              stats=ArmorStats(), tier=5)
        for slot in SLOTS
    ]
    build = BuildResult(
        score=1.0,
        completion_rate=1.0,
        build=BuildCandidate(class_type="hunter", items=armor),
        canonical_build=ExecutableBuild(
            class_type="hunter", snapshot_version="快照-1", execution_id="cand-1", items=items
        ),
    )
    assert build.model_dump(mode="json")["execution_id"] == "cand-1"

    without_canonical = BuildResult(
        score=0.0, completion_rate=0.0, build=BuildCandidate(class_type="hunter", items=armor)
    )
    assert without_canonical.model_dump()["execution_id"] == "", "没候选时给空串，不编一个 ID"
