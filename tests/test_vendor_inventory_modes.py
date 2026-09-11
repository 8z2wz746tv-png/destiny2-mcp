"""商人查询的两种形态：菜单（选商人）与详情（看货架）。

这些测试用与线上同形的假 payload（vendors/sales/categories/itemComponents/
vendorGroups），验证三件事：
1. 不点名时只给菜单，不返回上千件商品；
2. 点名一个商人时给出分类、等级、可买状态，并老实标 truncated；
3. 查不到、查得到但本次没返回、有歧义这三种情况都给出下一步，而不是空数组。
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from destiny_mcp.services import vendor_service as vendor_service_module
from destiny_mcp.services.vendor_service import VendorService

# 用真实 hash，让别名表（VENDOR_HASHES）这条路径也真的被走到
_VANGUARD = 69482069      # 指挥官萨瓦拉：有等级、有子页面、有装饰性按钮
_FOCUS = 2484291326       # 子页面目标（武器聚焦），本次也在返回里
_EVERVERSE_PAGE = 3000    # 无名商人，只能靠 identifier 认人
_GUNSMITH = 672118013     # 别名表里的商人
_XUR = 2190858386         # 需要按时间判断在不在

_WEAPON_ITEM = 700
_PLACEHOLDER_ITEM = 701
_CURRENCY = 3159615086

_RESPONSE = {
    "vendors": {
        "data": {
            str(_VANGUARD): {"vendorHash": _VANGUARD, "nextRefreshDate": "2026-07-26T17:00:00Z",
                             "progression": {"progressionHash": 500, "level": 11, "levelCap": 16,
                                             "currentProgress": 5180, "progressToNextLevel": 530,
                                             "nextLevelAt": 1050, "dailyLimit": 0, "weeklyProgress": 120,
                                             "weeklyLimit": 500}},
            str(_FOCUS): {"vendorHash": _FOCUS, "nextRefreshDate": "2026-07-26T17:00:00Z"},
            str(_EVERVERSE_PAGE): {"vendorHash": _EVERVERSE_PAGE, "nextRefreshDate": "2026-07-26T17:00:00Z"},
            str(_GUNSMITH): {"vendorHash": _GUNSMITH, "nextRefreshDate": "2026-07-26T17:00:00Z"},
            str(_XUR): {"vendorHash": _XUR, "nextRefreshDate": "2026-07-26T17:00:00Z"},
        }
    },
    "sales": {
        "data": {
            str(_VANGUARD): {
                "saleItems": {
                    "10": {"vendorItemIndex": 10, "itemHash": _WEAPON_ITEM, "costs": [{"itemHash": _CURRENCY, "quantity": 25}],
                           "failureIndexes": [], "augments": 1, "saleStatus": 0},
                    "11": {"vendorItemIndex": 11, "itemHash": 702, "costs": [], "failureIndexes": [0], "augments": 0,
                           "saleStatus": 8},
                    "12": {"vendorItemIndex": 12, "itemHash": _PLACEHOLDER_ITEM, "costs": [], "failureIndexes": [],
                           "augments": 0, "saleStatus": 0},
                    "13": {"vendorItemIndex": 13, "itemHash": 707, "costs": [], "failureIndexes": [0], "augments": 0,
                           "saleStatus": 8},
                }
            },
            str(_FOCUS): {
                "saleItems": {
                    "1": {"vendorItemIndex": 1, "itemHash": 703, "costs": [], "failureIndexes": [], "augments": 0,
                          "saleStatus": 0}
                }
            },
            str(_EVERVERSE_PAGE): {
                "saleItems": {
                    "1": {"vendorItemIndex": 1, "itemHash": 704, "costs": [], "failureIndexes": [], "augments": 0,
                          "saleStatus": 2}
                }
            },
            str(_GUNSMITH): {
                "saleItems": {
                    "1": {"vendorItemIndex": 1, "itemHash": 705, "costs": [], "failureIndexes": [], "augments": 0,
                          "saleStatus": 0}
                }
            },
            str(_XUR): {
                "saleItems": {
                    "1": {"vendorItemIndex": 1, "itemHash": 706, "costs": [], "failureIndexes": [], "augments": 0,
                          "saleStatus": 0}
                }
            },
        }
    },
    "categories": {
        "data": {
            str(_VANGUARD): {
                "categories": [
                    {"displayCategoryIndex": 1, "itemIndexes": [10, 11]},
                    {"displayCategoryIndex": 2, "itemIndexes": [12]},
                    {"displayCategoryIndex": 4, "itemIndexes": [13]},
                ]
            }
        }
    },
    "itemComponents": {},
    "failureStrings": [],
    "vendorGroups": {"data": {"groups": []}},
}

_ITEM_DEFINITIONS = {
    _WEAPON_ITEM: {
        "displayProperties": {"name": "Live Roll Weapon", "icon": "https://cdn/weapon.jpg"},
        "itemType": 3,
        "itemTypeName": "Weapon",
        "inventory": {"tierType": 5},
    },
    _PLACEHOLDER_ITEM: {
        "displayProperties": {"name": "聚焦破译"},
        "itemType": 0,
        "preview": {"previewVendorHash": _FOCUS},
    },
}


class _Resolver:
    async def resolve_player(self, player_name: str) -> dict:
        return {"membership_id": "membership-id", "membership_type": 3}

    async def resolve_character_id(
        self, membership_id: str, membership_type: int, character_name: str
    ) -> str:
        return "hunter-id"


class _Manifest:
    """只实现 vendor 服务真正会用到的那几个查询。"""

    _vendor_names = {
        _VANGUARD: "指挥官萨瓦拉",
        _FOCUS: "武器聚焦",
        _EVERVERSE_PAGE: "",
        _GUNSMITH: "班西-44",
        _XUR: "仄",
    }
    _vendor_identifiers = {
        _VANGUARD: "VANGUARD",
        _FOCUS: "GUNSMITH_WEAPON_FOCUSING",
        _EVERVERSE_PAGE: "EVERVERSE_ARCHIVE",
        _GUNSMITH: "GUNSMITH",
        _XUR: "TOWER_NINE",
    }
    _display_categories = {
        _VANGUARD: [
            {"index": 1, "identifier": "category.rank_rewards_seasonal", "displayProperties": {"name": "等级奖励"}},
            {"index": 2, "identifier": "category.vendor_engram_purchase", "displayProperties": {"name": "聚焦破译"}},
            {"index": 4, "identifier": "vanguard.help.name", "displayProperties": {"name": "先锋行动"}},
        ]
    }
    _item_names = {
        _WEAPON_ITEM: "Live Roll Weapon",
        702: "不可买的材料",
        _PLACEHOLDER_ITEM: "聚焦破译",
        703: "聚焦武器",
        704: "档案物品",
        705: "枪匠货",
        706: "仄的货",
        707: "先锋行动",
        900: "Verified Barrel",
    }

    def _vendor_def(self, vendor_hash: int) -> dict:
        if vendor_hash not in self._vendor_names:
            return {}
        return {
            "displayProperties": {"name": self._vendor_names[vendor_hash], "icon": "https://cdn/vendor.jpg"},
            "vendorIdentifier": self._vendor_identifiers[vendor_hash],
            "displayCategories": self._display_categories.get(vendor_hash, []),
            "failureStrings": ["材料不足"],
        }

    def get_vendor_definition(self, vendor_hash: int) -> dict:
        return self._vendor_def(vendor_hash)

    def get_definition(self, table: str, hash_id: int) -> dict:
        if table == "DestinyInventoryItemDefinition":
            return _ITEM_DEFINITIONS.get(hash_id, {})
        if table == "DestinyProgressionDefinition":
            return {"displayProperties": {"name": "先锋等级"}}
        return {}

    def get_item_info(self, item_hash: int) -> dict | None:
        definition = _ITEM_DEFINITIONS.get(item_hash)
        if definition is not None:
            return {
                "name": self._item_names.get(item_hash, ""),
                "itemType": definition.get("itemType", 0),
                "itemTypeName": "Weapon" if definition.get("itemType") == 3 else "",
                "tier": (definition.get("inventory") or {}).get("tierType", 0),
                "icon": (definition.get("displayProperties") or {}).get("icon", ""),
            }
        if item_hash in self._item_names:
            return {"name": self._item_names[item_hash], "itemType": 0, "tier": 0, "icon": ""}
        return None

    def get_item_name(self, item_hash: int) -> str:
        return self._item_names.get(item_hash, f"#{item_hash}")

    def get_plug_category_identifier(self, plug_hash: int) -> str:
        return "barrels"

    def get_sandbox_perk_description(self, plug_hash: int) -> dict:
        return {"description": "verified"}


class _Bungie:
    def __init__(self, response: dict | None = None) -> None:
        self._response = response if response is not None else _RESPONSE
        self.component_calls: list[int] = []

    async def fetch_vendors(self, membership_id: str, membership_type: int, character_id: str) -> dict:
        return deepcopy(self._response)

    async def fetch_vendor_components(
        self, membership_id: str, membership_type: int, character_id: str, vendor_hash: int
    ) -> dict:
        self.component_calls.append(vendor_hash)
        return {
            "itemComponents": {
                "sockets": {
                    "data": {
                        "10": {"sockets": [{"plugHash": 900, "isEnabled": True}]},
                    }
                }
            }
        }


@pytest.fixture(autouse=True)
def _xur_is_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """把这些用例与真实星期几解耦；Xur 不在的情况另有专门用例。"""
    monkeypatch.setattr(vendor_service_module, "_is_xur_available", lambda: True)


def _service(response: dict | None = None) -> tuple[VendorService, _Bungie]:
    bungie = _Bungie(response)
    return VendorService(bungie, _Manifest(), _Resolver()), bungie


@pytest.mark.asyncio
async def test_unnamed_query_returns_a_menu_without_items() -> None:
    service, bungie = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter")

    assert result.mode == "menu"
    assert result.question
    assert all(vendor.sale_items == [] for vendor in result.vendors)
    # 菜单不该去逐商人拉 socket 详情
    assert bungie.component_calls == []
    # 有等级的商人排前面
    assert result.vendors[0].vendor_hash == _VANGUARD
    assert result.vendors[0].rank is not None
    assert result.vendors[0].rank.name == "先锋等级"
    assert result.vendors[0].rank.reset_hint == "每周重置"
    # 无名商人也能有名字
    labels = {vendor.vendor_hash: vendor.name for vendor in result.vendors}
    assert labels[_EVERVERSE_PAGE] == "EVERVERSE_ARCHIVE"
    assert result.next_actions


@pytest.mark.asyncio
async def test_menu_reports_totals_and_truncation() -> None:
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", limit=2)

    assert result.returned_vendors == 2
    assert result.total_vendors == 5
    assert result.truncated is True


@pytest.mark.asyncio
async def test_named_vendor_returns_tabs_rank_and_buyability() -> None:
    service, bungie = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", "萨瓦拉")

    assert result.mode == "detail"
    assert len(result.vendors) == 1
    vendor = result.vendors[0]
    assert vendor.vendor_hash == _VANGUARD
    assert vendor.total_items == 3          # 13 号那条挂在装饰性 tab 下，不算商品
    assert vendor.purchasable_items == 2
    assert vendor.hidden_items == 1
    assert vendor.truncated is False
    # 分类：等级奖励 / 子菜单 / 帮助按钮被丢掉
    assert [(category.index, category.kind) for category in vendor.categories] == [
        (1, "rewards"),
        (2, "submenu"),
    ]
    submenu = vendor.categories[1]
    assert submenu.target_vendor_hash == _FOCUS
    assert submenu.target_available is True
    # 子页面作为下一步给出，而不是把它的货架并进来
    assert any(str(_FOCUS) in action for action in result.next_actions)
    # 只要问到一个商人，就只为这一个商人取 socket 详情
    assert bungie.component_calls == [_VANGUARD]


@pytest.mark.asyncio
async def test_sale_items_keep_hash_cost_category_and_failure_reason() -> None:
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))
    items = {item.name: item for item in result.vendors[0].sale_items}

    buyable = items["Live Roll Weapon"]
    assert buyable.item_hash == _WEAPON_ITEM          # 详情模式仍可拿去查武器
    assert buyable.can_be_sold is True
    assert buyable.category_index == 1
    assert [(cost.item_name, cost.quantity) for cost in buyable.costs] == [("微光", 25)]
    assert buyable.owned is True                      # augments 标记已拥有
    assert buyable.perks and buyable.perks[0].name    # compact 只留名字

    blocked = items["不可买的材料"]
    assert blocked.can_be_sold is False
    assert blocked.failure_reasons == ["材料不足"]

    submenu_item = items["聚焦破译"]
    assert submenu_item.category_index == 2
    assert submenu_item.sale_status == 0


@pytest.mark.asyncio
async def test_detail_mode_caps_items_and_says_so() -> None:
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD), limit=1)

    vendor = result.vendors[0]
    assert len(vendor.sale_items) == 1
    assert vendor.total_items == 3
    assert vendor.truncated is True


@pytest.mark.asyncio
async def test_unpurchasable_reason_falls_back_when_upstream_strings_are_missing() -> None:
    response = deepcopy(_RESPONSE)
    response["vendors"]["data"][str(_VANGUARD)]  # keep shape explicit
    manifest = _Manifest()

    def no_failure_strings(vendor_hash: int) -> dict:
        definition = _Manifest._vendor_def(manifest, vendor_hash)
        definition.pop("failureStrings", None)
        return definition

    manifest.get_vendor_definition = no_failure_strings  # type: ignore[method-assign]
    service = VendorService(_Bungie(response), manifest, _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))

    blocked = next(item for item in result.vendors[0].sale_items if item.name == "不可买的材料")
    assert blocked.can_be_sold is False
    assert blocked.failure_reasons and "0" in blocked.failure_reasons[0]


@pytest.mark.asyncio
async def test_ambiguous_name_lists_candidates_instead_of_guessing() -> None:
    response = deepcopy(_RESPONSE)
    # 两个同名的“传承装备”页，只给名字没法确定是哪一个
    for twin_hash in (4000, 4001):
        response["vendors"]["data"][str(twin_hash)] = {"vendorHash": twin_hash}
        response["sales"]["data"][str(twin_hash)] = {
            "saleItems": {"1": {"vendorItemIndex": 1, "itemHash": 800, "costs": [], "failureIndexes": []}}
        }
    manifest = _Manifest()
    original = manifest.get_vendor_definition

    def with_twin(vendor_hash: int) -> dict:
        if vendor_hash in (4000, 4001):
            return {
                "displayProperties": {"name": "传承装备"},
                "vendorIdentifier": f"LEGACY_FOCUS_{vendor_hash}",
                "displayCategories": [],
                "failureStrings": [],
            }
        return original(vendor_hash)

    manifest.get_vendor_definition = with_twin  # type: ignore[method-assign]
    service = VendorService(_Bungie(response), manifest, _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", "传承装备")

    assert result.mode == "menu"
    assert result.question and "匹配到" in result.question
    assert {vendor.vendor_hash for vendor in result.vendors} == {4000, 4001}
    assert all(vendor.sale_items == [] for vendor in result.vendors)
    assert any("4000" in action for action in result.next_actions)


@pytest.mark.asyncio
async def test_unknown_name_suggests_nearby_vendors() -> None:
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", "萨瓦拉副官")

    assert result.mode == "menu"
    assert result.vendors == []
    assert result.warnings
    assert "萨瓦拉" in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_known_vendor_missing_from_payload_is_explained() -> None:
    response = deepcopy(_RESPONSE)
    del response["vendors"]["data"][str(_XUR)]
    del response["sales"]["data"][str(_XUR)]
    service = VendorService(_Bungie(response), _Manifest(), _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", "仄")

    assert result.vendors == []
    assert any("没有返回" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_xur_absence_is_reported_instead_of_returning_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vendor_service_module, "_is_xur_available", lambda: False)
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_XUR))

    assert result.vendors == []
    assert any("不在" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_warning_list_is_capped() -> None:
    response = deepcopy(_RESPONSE)
    service = VendorService(_Bungie(response), _Manifest(), _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))

    assert len(result.warnings) <= 9


@pytest.mark.asyncio
async def test_blank_failure_string_falls_back_to_index_message() -> None:
    """上游原因文案是空串时，不能回一个空字符串当"原因"。"""
    response = deepcopy(_RESPONSE)
    response["vendors"]["data"][str(_VANGUARD)]["progression"]  # keep shape explicit
    manifest = _Manifest()

    def blank_reason(vendor_hash: int) -> dict:
        definition = _Manifest._vendor_def(manifest, vendor_hash)
        definition["failureStrings"] = [""]
        return definition

    manifest.get_vendor_definition = blank_reason  # type: ignore[method-assign]
    service = VendorService(_Bungie(response), manifest, _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))

    blocked = next(item for item in result.vendors[0].sale_items if item.name == "不可买的材料")
    assert blocked.can_be_sold is False
    assert blocked.failure_reasons == ["上游标记为不可购买（原因索引 [0]）"]


@pytest.mark.asyncio
async def test_sale_item_category_index_always_points_at_a_listed_category() -> None:
    """sale_items[].category_index 不能指向一个没列出来的分类。"""
    service, _ = _service()

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))

    vendor = result.vendors[0]
    listed = {category.index for category in vendor.categories}
    assert listed == {1, 2}
    for item in vendor.sale_items:
        assert item.category_index is None or item.category_index in listed
    assert "先锋行动" not in {item.name for item in vendor.sale_items}


@pytest.mark.asyncio
async def test_uncapped_rank_reports_no_cap_instead_of_minus_one() -> None:
    """上游用 -1 表示无上限，不能把它当数字吐给调用方。"""
    response = deepcopy(_RESPONSE)
    response["vendors"]["data"][str(_VANGUARD)]["progression"]["levelCap"] = -1
    service = VendorService(_Bungie(response), _Manifest(), _Resolver())

    result = await service.get_vendor_inventory("玩家#1234", "hunter", str(_VANGUARD))

    rank = result.vendors[0].rank
    assert rank is not None
    assert rank.level_cap is None
    assert rank.level == 11
