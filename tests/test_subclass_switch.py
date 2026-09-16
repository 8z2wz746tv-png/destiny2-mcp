"""换子职业（`changes={"subclass": …}`）的行为守门。

背景与根因见 `docs/plans/SUBCLASS_ARTIFACT_PLAN.md` P2：
- 元素**不在子职业物品的定义里**（实采：`defaultDamageType` 全 0、无 element 字段），
  只在 plug 的 `plugCategoryIdentifier` 上（`warlock.solar.supers`），所以元素靠已装 plug 读；
- 换元素 = 装备**另一件**子职业物品（每件各自保留自己的插槽配置），一次 equip 就够；
- 顺序有要求：先换物品、回读，再改槽——槽索引属于具体那件物品；
- 叫法只查表 + 拆一次职业尾缀，不模糊匹配；官方名按 Manifest 在该角色的物品里精确匹配。
"""

from __future__ import annotations

import pytest

from destiny_mcp.services import write_readback
from destiny_mcp.services.subclass_service import SubclassService
from destiny_mcp.utils.hash_utils import to_unsigned

@pytest.fixture(autouse=True)
def _no_readback_delay(monkeypatch):
    """回读重试的等待在测试里压到 0：行为要测，5 秒真等不要。"""
    monkeypatch.setattr(write_readback, "DELAY_SECONDS", 0)


# 子职业物品定义：hash → (官方中文名, 元素)
SOLAR_WARLOCK = 111  # 破晓
PRISM_WARLOCK = 222  # 棱镜术士
SOLAR_HUNTER = 333  # 枪手
NAMES = {SOLAR_WARLOCK: "破晓", PRISM_WARLOCK: "棱镜术士", SOLAR_HUNTER: "枪手"}

INSTANCES = {
    "inst-solar": SOLAR_WARLOCK,
    "inst-prism": PRISM_WARLOCK,
    "inst-hunter": SOLAR_HUNTER,
}

# plug → plugCategoryIdentifier（元素 + 槽类型都从这里读）
CATEGORIES = {
    1001: "warlock.solar.supers",
    1002: "shared.solar.grenades",
    2001: "warlock.prism.supers",
    2002: "warlock.prism.grenades",
    3001: "hunter.solar.supers",
    3002: "shared.solar.grenades",
}


class _Manifest:
    def get_item_definition(self, item_hash: int):
        name = NAMES.get(to_unsigned(item_hash))
        return {"displayProperties": {"name": name}} if name else None

    def get_item_name(self, item_hash: int) -> str:
        return NAMES.get(to_unsigned(item_hash), "")

    def get_item_info(self, item_hash: int):
        name = NAMES.get(to_unsigned(item_hash))
        return {"name": name, "itemType": 0} if name else None

    def get_plug_category_identifier(self, plug_hash: int):
        return CATEGORIES.get(to_unsigned(plug_hash), "")

    def get_plug_set_plugs(self, plug_set_hash: int):
        return []

    def search(self, name: str, limit: int = 10):
        return []


class _Resolver:
    """共享一份账号状态：equip 会改它，所以回读拿到的是写入后的状态。"""

    def __init__(self, equipped: str, bag: list[str]) -> None:
        self.equipped = equipped
        self.bag = bag
        self.reads = 0

    async def resolve_player(self, player_name: str):
        return {"membership_id": "1", "membership_type": 3}

    async def resolve_character_id(self, mid, mtype, character: str) -> str:
        return "c1"

    async def get_profile(self, mid, mtype, components):
        self.reads += 1
        items = [{"bucketHash": 3284755031, "itemHash": INSTANCES[self.equipped],
                  "itemInstanceId": self.equipped}]
        items += [
            {"bucketHash": 3284755031, "itemHash": INSTANCES[i], "itemInstanceId": i}
            for i in self.bag
        ]
        sockets = {self.equipped: {"sockets": _sockets(INSTANCES[self.equipped])}}
        for instance in self.bag:
            sockets[instance] = {"sockets": _sockets(INSTANCES[instance])}
        return {
            "characterEquipment": {"data": {"c1": {"items": [items[0]]}}},
            "characterInventories": {"data": {"c1": {"items": items[1:]}}},
            "itemComponents": {"sockets": {"data": sockets}},
        }


def _sockets(item_hash: int) -> list[dict]:
    if item_hash == SOLAR_WARLOCK:
        return [{"plugHash": 1001}, {"plugHash": 1002}]
    if item_hash == PRISM_WARLOCK:
        return [{"plugHash": 2001}, {"plugHash": 2002}]
    return [{"plugHash": 3001}, {"plugHash": 3002}]


class _Bungie:
    def __init__(self, resolver: _Resolver, error_code: int = 1, message: str = "Ok",
                 keep_old: bool = False) -> None:
        self._resolver = resolver
        self._error_code = error_code
        self._message = message
        self._keep_old = keep_old
        self.equips: list[tuple[str, str]] = []
        self.writes: list[tuple[str, int, int]] = []

    async def equip_item(self, item_instance_id: str, character_id: str, membership_type: int):
        self.equips.append((item_instance_id, character_id))
        if self._error_code == 1 and not self._keep_old:
            self._resolver.equipped = item_instance_id
        return {"ErrorCode": self._error_code, "Message": self._message}

    async def insert_socket_plug_free(
        self, item_instance_id, plug_item_hash, socket_index, socket_array_type,
        character_id, membership_type,
    ):
        self.writes.append((item_instance_id, plug_item_hash, socket_index))
        return {"ErrorCode": 1, "Message": "Ok"}


def _service(equipped: str = "inst-prism", bag: list[str] | None = None, **bungie_kwargs):
    resolver = _Resolver(equipped, bag if bag is not None else ["inst-solar"])
    bungie = _Bungie(resolver, **bungie_kwargs)
    manifest = _Manifest()
    return SubclassService(bungie, manifest, resolver), bungie, resolver


async def test_switch_by_element_equips_the_other_item_and_reads_back() -> None:
    service, bungie, _ = _service()

    result = await service.modify_subclass("p", "warlock", {"subclass": "烈日"})

    assert result.success is True
    assert bungie.equips == [("inst-solar", "c1")]
    switch = result.subclass_switch
    assert (switch.from_name, switch.from_element) == ("棱镜术士", "prism")
    assert (switch.to_name, switch.to_element) == ("破晓", "solar")
    assert switch.item_instance_id == "inst-solar"
    assert result.subclass_name == "破晓"


async def test_switch_accepts_spoken_aliases_and_the_official_name() -> None:
    for spoken in ("火术", "火", "solar", "破晓"):
        service, bungie, _ = _service()
        result = await service.modify_subclass("p", "warlock", {"subclass": spoken})
        assert result.success is True, spoken
        assert bungie.equips == [("inst-solar", "c1")], spoken


async def test_switch_is_idempotent_when_already_there() -> None:
    service, bungie, _ = _service(equipped="inst-solar")

    result = await service.modify_subclass("p", "warlock", {"subclass": "烈日"})

    assert result.success is True
    assert bungie.equips == [], "已经在目标子职业上不该再写一次"
    assert "不用换" in result.message


async def test_another_class_spelling_refuses_instead_of_switching() -> None:
    """「火术」是术士的叫法：猎人说它要报错，不能悄悄切成烈日猎人。"""
    service, bungie, _ = _service(equipped="inst-hunter", bag=[])

    result = await service.modify_subclass("p", "hunter", {"subclass": "火术"})

    assert result.success is False
    assert bungie.equips == []
    assert "术士" in result.message and "猎人" in result.message


async def test_official_name_of_another_class_is_not_found_and_lists_what_exists() -> None:
    service, bungie, _ = _service(equipped="inst-hunter", bag=[])

    result = await service.modify_subclass("p", "hunter", {"subclass": "破晓"})

    assert result.success is False
    assert bungie.equips == []
    assert "找不到「破晓」" in result.message
    assert "枪手" in result.message, "报错要列出这个角色实际有哪些子职业"


async def test_element_the_character_does_not_have_fails_with_the_available_list() -> None:
    service, bungie, _ = _service(equipped="inst-solar", bag=["inst-prism"])

    result = await service.modify_subclass("p", "warlock", {"subclass": "虚空"})

    assert result.success is False
    assert bungie.equips == []
    assert "破晓（烈日）" in result.message
    assert "棱镜术士（棱镜）" in result.message


async def test_upstream_equip_failure_is_reported_verbatim_and_plugins_are_not_touched() -> None:
    service, bungie, _ = _service(error_code=1642, message="DestinyItemUniqueEquipRestricted")

    result = await service.modify_subclass(
        "p", "warlock", {"subclass": "烈日", "super": "某某超能"}
    )

    assert result.success is False
    assert "DestinyItemUniqueEquipRestricted" in result.message
    assert bungie.writes == [], "子职业没换成功就不该再改插槽"


async def test_read_back_mismatch_is_a_failure_not_a_success() -> None:
    service, bungie, _ = _service(keep_old=True)

    result = await service.modify_subclass("p", "warlock", {"subclass": "烈日"})

    assert result.success is False
    assert result.subclass_switch.unverified is True, "上游说成功、回读没看到：是没确认"
    assert "没确认" in result.message


async def test_switch_then_plug_change_writes_on_the_new_item() -> None:
    """一次请求里既换子职业又改槽：插槽必须写在新物品上，且 changes 里不含 subclass。"""
    service, bungie, _ = _service()

    result = await service.modify_subclass(
        "p", "warlock", {"subclass": "烈日", "super": "某某超能"}
    )

    switch = result.subclass_switch
    assert switch.success is True
    assert [c.socket_type for c in result.changes] == ["super"], "subclass 不是插槽，不该出现在 changes 里"
    assert all(w[0] == "inst-solar" for w in bungie.writes), "插槽要写在新物品上"
