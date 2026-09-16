"""神器模组：槽位从定义算、优选空槽、满了替换、写完回读。

背景（见 `docs/plans/SUBCLASS_ARTIFACT_PLAN.md`）：旧实现把 `socket_index=0` 写死，
而神器是按阶分槽的（同阶 2–3 个可互换槽），所以除了恰好落在 0 号槽的模组，其它都装不上。

这组测试用夹具复刻真实结构（实采：8 槽 4 组 plug set / 组件 305 没有 socketIndex 字段），
守住四件事：

1. **空槽优先**：同一阶有空槽就装空槽，不顶掉已装模组；
2. **满了能换**：同阶全满时替换，并在返回里写清 `from → to`（不静默顶掉）；
3. **不在候选就拒绝**：模组不在该神器的 plug set 里（未解锁/非本赛季）→ `InvalidArgumentError`；
4. **回读核对**：上游说成功但回读不一致 → 报失败，不许当成功。
"""

from __future__ import annotations

import pytest

from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.services.artifact_service import ArtifactService
from destiny_mcp.utils.hash_utils import to_unsigned

WARLOCK_CLASS_TYPE = 2
ARTIFACT_BUCKET = 1506418338
ARTIFACT_HASH = 1504442987
ARTIFACT_INSTANCE = "6917530000000000001"

EMPTY_PLUG = 999  # 「空神器模组」占位
MOD_A, MOD_A2 = 100, 101  # 同一阶（plug set A）
MOD_B = 200  # 另一阶（plug set B）

DEFS = {
    ARTIFACT_HASH: {
        "displayProperties": {"name": "测试神器"},
        "sockets": {
            "socketEntries": [
                {"reusablePlugSetHash": 111},  # 槽0 ┐ 同阶
                {"reusablePlugSetHash": 111},  # 槽1 ┘
                {"reusablePlugSetHash": 222},  # 槽2：另一阶
            ]
        },
    },
    EMPTY_PLUG: {"displayProperties": {"name": "空神器模组"}},
    MOD_A: {"displayProperties": {"name": "模组A"}},
    MOD_A2: {"displayProperties": {"name": "模组A2"}},
    MOD_B: {"displayProperties": {"name": "模组B"}},
}

PLUG_SETS = {111: [MOD_A, MOD_A2, EMPTY_PLUG], 222: [MOD_B, EMPTY_PLUG]}


class _Manifest:
    def get_item_definition(self, item_hash: int):
        return DEFS.get(to_unsigned(item_hash))

    def get_item_name(self, item_hash: int) -> str:
        definition = self.get_item_definition(item_hash) or {}
        return (definition.get("displayProperties") or {}).get("name", "")

    def get_plug_set_plugs(self, plug_set_hash: int):
        return [{"plugItemHash": h} for h in PLUG_SETS.get(plug_set_hash, [])]


class _Resolver:
    """共享一份"账号状态"：写入会改它，所以回读拿到的是**写入后**的状态。

    传 `fresh` 时用一份冻结的副本（用来模拟"上游说成功、回读却没变"）。
    """

    def __init__(self, state: list[int], fresh: list[int] | None = None) -> None:
        self._state = state
        self._frozen = list(fresh) if fresh is not None else None
        self.calls = 0

    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def get_profile(self, mid, mtype, components):
        self.calls += 1
        plugs = self._frozen if (self._frozen is not None and self.calls > 1) else self._state
        return _profile(list(plugs))


def _profile(plugs: list[int]) -> dict:
    return {
        "characters": {"data": {"c1": {"classType": WARLOCK_CLASS_TYPE}}},
        "profile": {"data": {"membershipType": 3}},
        "characterInventories": {
            "data": {
                "c1": {
                    "items": [
                        {
                            "bucketHash": ARTIFACT_BUCKET,
                            "itemInstanceId": ARTIFACT_INSTANCE,
                            "itemHash": ARTIFACT_HASH,
                        }
                    ]
                }
            }
        },
        "itemComponents": {
            "sockets": {"data": {ARTIFACT_INSTANCE: {"sockets": [{"plugHash": p} for p in plugs]}}}
        },
    }


class _Bungie:
    """假的写入端：成功时把状态也改掉（这样回读才对得上）。"""

    def __init__(self, state: list[int], error_code: int = 1) -> None:
        self.calls: list[dict] = []
        self._state = state
        self._error_code = error_code

    async def insert_socket_plug_free(self, **kwargs):
        self.calls.append(kwargs)
        if self._error_code == 1:
            self._state[kwargs["socket_index"]] = kwargs["plug_item_hash"]
        return {"ErrorCode": self._error_code, "Message": "上游拒绝"}


def _service(current: list[int], fresh: list[int] | None = None, error_code: int = 1):
    state = list(current)
    bungie = _Bungie(state, error_code)
    service = ArtifactService(bungie, _Manifest(), _Resolver(state, fresh))
    return service, bungie


@pytest.mark.asyncio
async def test_empty_slot_is_preferred_over_replacing() -> None:
    # 槽0 装着模组A，槽1 空
    service, bungie = _service([MOD_A, EMPTY_PLUG, EMPTY_PLUG])

    result = await service.equip_artifact_mod("p", MOD_A2, "术士")

    assert result["success"] is True
    assert bungie.calls[0]["socket_index"] == 1, "该装空槽，不能顶掉槽0"
    assert bungie.calls[0]["plug_item_hash"] == MOD_A2
    assert "from" not in result
    assert result["to"]["name"] == "模组A2"


@pytest.mark.asyncio
async def test_full_tier_replaces_and_reports_from_to() -> None:
    # 同阶两个槽都占着（槽0=模组A，槽1=模组A2）→ 装模组A2 需要替换
    service, bungie = _service([MOD_A, MOD_A2, EMPTY_PLUG])

    result = await service.equip_artifact_mod("p", MOD_A2, "术士")

    assert result["success"] is True
    assert result["slot_index"] == 0
    assert result["from"]["name"] == "模组A", "要写清顶掉了哪个"
    assert "模组A" in result["message"] and "模组A2" in result["message"]


@pytest.mark.asyncio
async def test_mod_not_in_candidates_is_rejected_with_guidance() -> None:
    service, bungie = _service([EMPTY_PLUG, EMPTY_PLUG, EMPTY_PLUG])

    with pytest.raises(InvalidArgumentError) as excinfo:
        await service.equip_artifact_mod("p", 555, "术士")

    assert "不在" in str(excinfo.value) and "解锁" in str(excinfo.value)
    assert not bungie.calls, "被拒绝时不该发出写入请求"


@pytest.mark.asyncio
async def test_readback_mismatch_is_a_failure_not_a_success() -> None:
    # 上游说成功，但回读时槽0 仍是空的 → 必须报失败
    service, _ = _service([EMPTY_PLUG, EMPTY_PLUG, EMPTY_PLUG], fresh=[EMPTY_PLUG, EMPTY_PLUG, EMPTY_PLUG])

    result = await service.equip_artifact_mod("p", MOD_A, "术士")

    assert result["success"] is False
    assert "回读" in result["message"] and "复核" in result["message"]


@pytest.mark.asyncio
async def test_upstream_error_is_reported_verbatim() -> None:
    service, _ = _service([EMPTY_PLUG, EMPTY_PLUG, EMPTY_PLUG], error_code=1663)

    result = await service.equip_artifact_mod("p", MOD_A, "术士")

    assert result["success"] is False
    assert "上游拒绝" in result["message"]


@pytest.mark.asyncio
async def test_slot_count_comes_from_the_definition_not_hardcoded() -> None:
    """换一件"只有 1 个槽"的神器定义，也能正确工作（证明没写死槽数/槽号）。"""
    defs = dict(DEFS)
    defs[ARTIFACT_HASH] = {
        "displayProperties": {"name": "单槽神器"},
        "sockets": {"socketEntries": [{"reusablePlugSetHash": 222}]},
    }

    class _SingleSlotManifest(_Manifest):
        def get_item_definition(self, item_hash: int):
            return defs.get(to_unsigned(item_hash))

    state = [EMPTY_PLUG]
    bungie = _Bungie(state)
    service = ArtifactService(bungie, _SingleSlotManifest(), _Resolver(state))

    result = await service.equip_artifact_mod("p", MOD_B, "术士")

    assert result["success"] is True
    assert bungie.calls[0]["socket_index"] == 0
