"""DIM 愿望单查询：哈希约定必须有测试盯着。

这张表曾经**整张查不中**：DIM 的 JSON 用无符号 32 位哈希做键，而 manifest 和库存
返回的是有符号（`2263462407` vs `-2031504889`）。查不中的表现是 `god_roll_pve/pvp`
全部为 `false` —— 不报错、不告警，只是每条 perk 都说"不是推荐"，所以没人发现。

这里用一份小样本把约定钉住，并在本机有真实数据时抽查整张表的可达性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from destiny_mcp.services.wishlist_service import WishListService
from destiny_mcp.utils.hash_utils import to_signed, to_unsigned
from destiny_mcp.wishlist_data import default_wishlist_path

# 用真实哈希形状：物品超过 2^31（两种约定不同值），perk 一超一不超。
UNSIGNED_ITEM = 2263462407
SIGNED_ITEM = to_signed(UNSIGNED_ITEM)
UNSIGNED_PERK = 3230963543
SIGNED_PERK = to_signed(UNSIGNED_PERK)
SMALL_PERK = 839105230  # 小于 2^31，两种约定同值

WISHLIST = {
    "version": "test",
    "source": "test",
    "weapon_count": 1,
    "rolls": {
        str(UNSIGNED_ITEM): [
            {"p": f"{UNSIGNED_PERK},{SMALL_PERK}", "t": ["pve"], "s": "示例来源"},
            {"p": "4090651448", "t": ["pvp"], "s": "示例来源"},
        ]
    },
}


def _service(tmp_path: Path, payload: dict | None = None) -> WishListService:
    path = tmp_path / "wishlist.json"
    path.write_text(json.dumps(payload if payload is not None else WISHLIST), encoding="utf-8")
    return WishListService(path)


def test_signed_and_unsigned_item_hashes_both_resolve(tmp_path: Path) -> None:
    """manifest 给有符号、DIM 存无符号：两种写法都必须查得到。"""
    service = _service(tmp_path)

    assert service.has_data(SIGNED_ITEM)
    assert service.has_data(UNSIGNED_ITEM)
    assert service.get_god_roll_perks(SIGNED_ITEM) is not None
    assert service.get_god_roll_perks(UNSIGNED_ITEM) is not None


def test_perk_flags_survive_both_hash_conventions(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service.is_god_roll_perk(SIGNED_ITEM, SIGNED_PERK) == {"pve": True, "pvp": False}
    assert service.is_god_roll_perk(UNSIGNED_ITEM, UNSIGNED_PERK) == {"pve": True, "pvp": False}
    assert service.is_god_roll_perk(SIGNED_ITEM, SMALL_PERK) == {"pve": True, "pvp": False}
    assert service.is_god_roll_perk(SIGNED_ITEM, 4090651448) == {"pve": False, "pvp": True}


def test_unknown_weapon_is_false_not_an_error(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service.has_data(12345) is False
    assert service.is_god_roll_perk(12345, SMALL_PERK) == {"pve": False, "pvp": False}
    assert service.get_god_roll_perks(12345) is None


def test_score_roll_counts_hits_with_manifest_hashes(tmp_path: Path) -> None:
    service = _service(tmp_path)

    score = service.score_roll(SIGNED_ITEM, [UNSIGNED_PERK])

    assert score.pve_hits == 1
    assert score.pve_total == 2
    assert service.score_roll(UNSIGNED_ITEM, [SIGNED_PERK]).pve_hits == 1


def test_missing_file_is_inert(tmp_path: Path) -> None:
    """数据没装时不能抛异常，只能说"没有数据"。"""
    service = WishListService(tmp_path / "不存在.json")

    assert service.has_data(SIGNED_ITEM) is False
    assert service.is_god_roll_perk(SIGNED_ITEM, SMALL_PERK) == {"pve": False, "pvp": False}


def test_empty_file_is_inert(tmp_path: Path) -> None:
    service = _service(tmp_path, {"version": "test", "rolls": {}})

    assert service.has_data(SIGNED_ITEM) is False


@pytest.mark.skipif(
    not default_wishlist_path().exists(),
    reason="本机没有 DIM 愿望单数据（首次启动会尝试下载）",
)
def test_downloaded_wishlist_is_reachable_from_manifest_hashes() -> None:
    """真实数据抽查：表里每个键转成有符号后都必须查得到。

    这条如果红了，说明约定又对不上了 —— 表现会是整表静默失效。
    """
    path = default_wishlist_path()
    rolls = json.loads(path.read_text(encoding="utf-8"))["rolls"]
    service = WishListService(path)

    sample = list(rolls)[:20]
    unreachable = [key for key in sample if not service.has_data(to_signed(int(key)))]

    assert not unreachable, f"这些键转成有符号后查不到：{unreachable}"
    # 反向也要成立：无符号写法同样能查到（调用方两种都可能传）
    assert service.has_data(to_unsigned(to_signed(int(sample[0]))))
