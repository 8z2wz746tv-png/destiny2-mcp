"""商人货架整形规则的单元测试（vendor_menu）。

这里测的是「怎么解释货架」：名字怎么匹配、分类算什么类型、等级怎么读、
以及条数上限。全部是纯函数，不需要网络和 manifest 文件。
"""

from __future__ import annotations

from destiny_mcp.models import VendorInfo, VendorRank
from destiny_mcp.services import vendor_menu as vm


def _identity(vendor_hash: int, name: str = "", identifier: str = "") -> vm.VendorIdentity:
    return vm.VendorIdentity(vendor_hash=vendor_hash, name=name, identifier=identifier)


def _defs(mapping: dict[int, dict]):
    return lambda vendor_hash: mapping.get(vendor_hash)


def test_unnamed_vendor_falls_back_to_identifier_then_hash() -> None:
    identities = vm.vendor_identities(
        [1, 2, 3],
        _defs({
            1: {"displayProperties": {"name": "班西-44"}, "vendorIdentifier": "GUNSMITH"},
            2: {"displayProperties": {}, "vendorIdentifier": "EVERVERSE_ARCHIVE"},
            3: {"displayProperties": {}},
        }),
    )

    assert [identity.label for identity in identities] == ["班西-44", "EVERVERSE_ARCHIVE", "#3"]


def test_label_override_wins_over_manifest_name() -> None:
    identities = vm.vendor_identities(
        [1],
        _defs({1: {"displayProperties": {"name": "Banshee-44"}}}),
        label_overrides={1: "班西-44"},
    )

    assert identities[0].label == "班西-44"


def test_match_prefers_hash_alias_then_exact_then_partial() -> None:
    identities = [
        _identity(1000, "指挥官萨瓦拉", "VANGUARD"),
        _identity(2000, "萨瓦拉副官", "VANGUARD_AIDE"),
        _identity(3000, "", "EVERVERSE_ARCHIVE"),
    ]
    aliases = {"Zavala": 1000, "萨瓦拉": 1000}

    assert vm.match_vendors("1000", identities).how == "hash"
    assert vm.match_vendors("zavala", identities, aliases).how == "alias"
    assert vm.match_vendors("EVERVERSE_ARCHIVE", identities).how == "exact"
    partial = vm.match_vendors("萨瓦拉", identities, {})
    assert partial.how == "contains"
    assert [identity.vendor_hash for identity in partial.identities] == [1000, 2000]


def test_known_hash_missing_from_payload_is_absent_not_unknown() -> None:
    identities = [_identity(1000, "指挥官萨瓦拉")]
    aliases = {"Xur": 2190858386}

    absent = vm.match_vendors("2190858386", identities, aliases)
    assert absent.how == "absent"
    assert absent.hash_hint == 2190858386

    by_alias = vm.match_vendors("xur", identities, aliases)
    assert by_alias.how == "absent"
    assert by_alias.hash_hint == 2190858386

    unknown = vm.match_vendors("查无此人", identities, aliases)
    assert unknown.how == "none"
    assert unknown.hash_hint is None


def test_close_names_suggests_instead_of_shrugging() -> None:
    identities = [
        _identity(1, "泰斯·艾夫瑞斯", "EVERVERSE"),
        _identity(2, "仄", "TOWER_NINE"),
    ]

    assert vm.close_vendor_names("泰斯艾夫瑞斯", identities)
    assert vm.close_vendor_names("完全无关的名字", identities)


def test_category_kinds_come_from_identifier_and_placeholder() -> None:
    # 等级奖励是普通分类，靠 identifier 认出来
    assert vm.classify_category("category.rank_rewards_seasonal", 6, None) == "rewards"
    # 帮助按钮不是货架
    assert vm.classify_category("xur_help_name", 1, None) is None
    assert vm.classify_category("category.vanguard_help.name", 1, None) is None
    # 单个占位商品指向另一个商人页 = 子菜单
    assert vm.classify_category("category.vendor_engram_purchase", 1, 2484291326) == "submenu"
    # 其余就是普通货架
    assert vm.classify_category("category_class_misc", 2, None) == "sale"


def test_category_entries_drop_decorative_tabs_and_flag_live_subpages() -> None:
    api_categories = [
        {"displayCategoryIndex": 1, "itemIndexes": [10, 11]},
        {"displayCategoryIndex": 2, "itemIndexes": [12]},
        {"displayCategoryIndex": 4, "itemIndexes": [13]},
        {"displayCategoryIndex": 5, "itemIndexes": []},
    ]
    display_categories = [
        {"index": 1, "identifier": "category.rank_rewards_seasonal", "displayProperties": {"name": "等级奖励"}},
        {"index": 2, "identifier": "category.vendor_engram_purchase", "displayProperties": {"name": "聚焦破译"}},
        {"index": 4, "identifier": "vendor.help.name", "displayProperties": {"name": "商人"}},
        {"index": 5, "identifier": "category_empty", "displayProperties": {"name": "空分类"}},
    ]

    categories = vm.category_entries(
        api_categories,
        display_categories,
        {2: 2000},
        available_vendors=[2000],
        vendor_label=lambda vendor_hash: "武器聚焦",
    )

    assert [(category.index, category.kind, category.item_count) for category in categories] == [
        (1, "rewards", 2),
        (2, "submenu", 1),
    ]
    submenu = categories[1]
    assert submenu.target_vendor_hash == 2000
    assert submenu.target_vendor_name == "武器聚焦"
    assert submenu.target_available is True


def test_subpage_target_missing_from_payload_is_flagged() -> None:
    categories = vm.category_entries(
        [{"displayCategoryIndex": 1, "itemIndexes": [7]}],
        [{"index": 1, "identifier": "category.vendor_engram_purchase", "displayProperties": {"name": "传承聚焦破译"}}],
        {1: 908529654},
        available_vendors=[1000],
    )

    assert categories[0].target_available is False
    assert categories[0].target_vendor_hash == 908529654


def test_rank_reads_limits_and_names_the_reset_cycle() -> None:
    progression = {
        "progressionHash": 457612306,
        "level": 11,
        "levelCap": 16,
        "currentProgress": 5180,
        "progressToNextLevel": 530,
        "nextLevelAt": 1050,
        "dailyProgress": 0,
        "dailyLimit": 0,
        "weeklyProgress": 120,
        "weeklyLimit": 500,
    }

    rank = vm.build_rank(progression, lambda _hash: "先锋等级")

    assert rank is not None
    assert (rank.name, rank.level, rank.level_cap) == ("先锋等级", 11, 16)
    assert rank.reset_hint == "每周重置"
    assert vm.build_rank({}) is None
    assert vm.build_rank(None) is None


def test_daily_limits_are_labelled_daily() -> None:
    rank = vm.build_rank({"progressionHash": 1, "dailyLimit": 100})

    assert rank is not None
    assert rank.reset_hint == "每日重置"


def test_limit_clamping_uses_mode_defaults() -> None:
    assert vm.clamp_limit(None, "menu") == vm.MENU_LIMIT_DEFAULT
    assert vm.clamp_limit(None, "detail") == vm.DETAIL_LIMIT_DEFAULT
    assert vm.clamp_limit(0, "detail") == vm.DETAIL_LIMIT_DEFAULT
    assert vm.clamp_limit(-5, "detail") == vm.DETAIL_LIMIT_DEFAULT
    assert vm.clamp_limit(1000, "detail") == vm.LIMIT_MAX
    assert vm.clamp_limit(3, "menu") == 3


def test_menu_puts_ranked_vendors_first_then_buyable_count() -> None:
    ranked = VendorInfo(
        vendor_hash=1,
        name="有等级的",
        rank=VendorRank(progression_hash=1, level=3),
        total_items=2,
        purchasable_items=2,
    )
    rich = VendorInfo(vendor_hash=2, name="货多的", total_items=50, purchasable_items=40)
    poor = VendorInfo(vendor_hash=3, name="货少的", total_items=3, purchasable_items=1)

    ordered = sorted([rich, poor, ranked], key=vm.menu_sort_key)

    assert [vendor.name for vendor in ordered] == ["有等级的", "货多的", "货少的"]


def test_warnings_are_deduplicated_in_order_and_the_tail_is_admitted() -> None:
    assert vm.cap_warnings(["a", "b", "a", " "]) == ["a", "b"]

    capped = vm.cap_warnings([f"w{index}" for index in range(10)], cap=3)

    assert capped[:3] == ["w0", "w1", "w2"]
    assert len(capped) == 4
    assert "7 条" in capped[3]
