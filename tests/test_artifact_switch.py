"""换神器（`intent="equip_artifact"`）的守门。

实采事实（`docs/plans/SUBCLASS_ARTIFACT_PLAN.md` §10.3 + P0-b）：
- 神器**不可转移**（`transferStatus` 背包=2、装备位=3），所以"能换的"只能是同一个角色
  背包里那几件；仓库/邮政长里一件都没有；
- 目录 hash ≠ 玩家实例 hash（好奇之器：目录 -1600062152、实例 23349941），
  所以匹配只能按**实例**的定义名；
- 名字精确匹配，不模糊匹配：猜错神器比报错更坑。
"""

from __future__ import annotations

import pytest

from destiny_mcp.services import write_readback
from destiny_mcp.exceptions import InvalidArgumentError
from destiny_mcp.services.artifact_service import ArtifactService
from destiny_mcp.utils.hash_utils import to_unsigned

ARTIFACT_BUCKET = 1506418338
NAMES = {111: "废墟石板", 222: "好奇之器", 333: "NPA斥力调节器"}
INSTANCES = {"inst-a": 111, "inst-b": 222, "inst-c": 333}


@pytest.fixture(autouse=True)
def _no_readback_delay(monkeypatch):
    """回读重试的等待在测试里压到 0：行为要测，5 秒真等不要。"""
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)


class _Manifest:
    def get_item_name(self, item_hash: int) -> str:
        return NAMES.get(to_unsigned(item_hash), f"#{item_hash}")


class _Resolver:
    """共享账号状态：equip 会改它，所以回读拿到的是写入后的状态。"""

    def __init__(self, equipped: str, bag: list[str], other_char_bag: list[str] | None = None,
                 real_shape: bool = False):
        self.equipped = equipped
        self.bag = bag
        self.other_char_bag = other_char_bag or []
        # real_shape=True 模拟真机形状：205 的条目**不带 isEquipped**，装备状态只在组件 300
        self.real_shape = real_shape

    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def resolve_character_id(self, mid, mtype, character: str) -> str:
        return "c1"

    async def get_profile(self, mid, mtype, components):
        def entry(instance: str) -> dict:
            item = {
                "bucketHash": ARTIFACT_BUCKET,
                "itemHash": INSTANCES[instance],
                "itemInstanceId": instance,
            }
            if not self.real_shape:
                item["isEquipped"] = instance == self.equipped
            return item

        profile = {
            "characterEquipment": {"data": {"c1": {"items": [entry(self.equipped)]}}},
            "characterInventories": {
                "data": {
                    "c1": {"items": [entry(i) for i in self.bag]},
                    "c2": {"items": [entry(i) for i in self.other_char_bag]},
                }
            },
        }
        if self.real_shape:
            held = [self.equipped, *self.bag, *self.other_char_bag]
            profile["itemComponents"] = {
                "instances": {
                    "data": {
                        i: {"isEquipped": i == self.equipped} for i in held
                    }
                }
            }
        return profile


class _Bungie:
    def __init__(self, resolver: _Resolver, error_code: int = 1, message: str = "Ok",
                 keep_old: bool = False) -> None:
        self._resolver = resolver
        self._error_code = error_code
        self._message = message
        self._keep_old = keep_old
        self.equips: list[str] = []

    async def equip_item(self, item_instance_id: str, character_id: str, membership_type: int):
        self.equips.append(item_instance_id)
        if self._error_code == 1 and not self._keep_old:
            self._resolver.equipped = item_instance_id
        return {"ErrorCode": self._error_code, "Message": self._message}


def _service(equipped: str = "inst-a", bag: list[str] | None = None, **kwargs):
    resolver = _Resolver(equipped, bag if bag is not None else ["inst-b"])
    bungie = _Bungie(resolver, **kwargs)
    return ArtifactService(bungie, _Manifest(), resolver), bungie


async def test_equipped_state_comes_from_the_instances_component() -> None:
    """真机形状：205 的条目没有 isEquipped，装备状态只在组件 300 的 instances 上。

    照 item 上的字段读会永远读成"没装备"，于是把正装着的那件也列进候选、还可能重装一次。
    """
    resolver = _Resolver("inst-a", ["inst-b"], real_shape=True)
    service = ArtifactService(_Bungie(resolver), _Manifest(), resolver)

    state = await service.artifact_state("p", "hunter")

    assert state["equipped"]["instance_id"] == "inst-a"
    assert [i["name"] for i in state["available"]] == ["好奇之器"]


async def test_state_reports_the_equipped_instance_and_what_can_be_swapped_in() -> None:
    service, _ = _service()

    state = await service.artifact_state("p", "hunter")

    assert state["equipped"] == {
        "name": "废墟石板", "hash": 111, "instance_id": "inst-a", "is_equipped": True
    }
    assert [i["name"] for i in state["available"]] == ["好奇之器"]


async def test_switch_equips_the_other_instance_and_reads_back() -> None:
    service, bungie = _service()

    result = await service.switch_artifact("p", "hunter", "好奇之器")

    assert result["success"] is True
    assert bungie.equips == ["inst-b"]
    assert result["from"]["name"] == "废墟石板"
    assert result["to"] == {
        "name": "好奇之器", "hash": 222, "instance_id": "inst-b"
    }
    assert "已换上「好奇之器」" in result["message"]


async def test_switch_is_idempotent_when_already_equipped() -> None:
    service, bungie = _service()

    result = await service.switch_artifact("p", "hunter", "废墟石板")

    assert result["success"] is True
    assert bungie.equips == [], "已经装着就不该再写一次"
    assert "不用换" in result["message"]


async def test_artifact_held_by_another_character_is_not_a_candidate() -> None:
    """神器不能跨角色/仓库转移，别人身上的那件不算能换的。"""
    resolver = _Resolver("inst-a", [], other_char_bag=["inst-c"])
    service = ArtifactService(_Bungie(resolver), _Manifest(), resolver)

    with pytest.raises(InvalidArgumentError) as excinfo:
        await service.switch_artifact("p", "hunter", "NPA斥力调节器")

    assert "不在这个角色身上" in str(excinfo.value)
    assert "废墟石板" in str(excinfo.value), "报错要列出他身上实际有什么"


async def test_unknown_name_lists_what_the_character_has() -> None:
    service, bungie = _service()

    with pytest.raises(InvalidArgumentError) as excinfo:
        await service.switch_artifact("p", "hunter", "根本没有这个神器")

    assert bungie.equips == []
    assert "废墟石板" in str(excinfo.value) and "好奇之器" in str(excinfo.value)


async def test_blank_name_is_rejected_before_any_read() -> None:
    service, bungie = _service()

    with pytest.raises(InvalidArgumentError):
        await service.switch_artifact("p", "hunter", "   ")

    assert bungie.equips == []


async def test_upstream_failure_is_reported_verbatim() -> None:
    service, bungie = _service(error_code=1642, message="DestinyItemUniqueEquipRestricted")

    result = await service.switch_artifact("p", "hunter", "好奇之器")

    assert result["success"] is False
    assert "DestinyItemUniqueEquipRestricted" in result["message"]
    assert bungie.equips == ["inst-b"]


async def test_read_back_mismatch_is_a_failure() -> None:
    service, _ = _service(keep_old=True)

    result = await service.switch_artifact("p", "hunter", "好奇之器")

    assert result["success"] is False
    assert result["unverified"] is True, "上游说成功、回读没看到：是没确认，不是没换成"
    assert "没确认" in result["message"]
    assert "inst-a" in result["message"], "要说清回读到的是什么"
