"""同名多版本：默认必须取**最新**那一版（判据 `index`）。

2026-10-10 真机：用户问「鹰月」，工具给了 index **23927** 的旧版，而新版是 index **35200**
（`235827225` vs `386864872`）—— 老版本游戏里已经刷不到。根因：`find_weapon` 取 `search`
结果的**第一条**，而 search 只按匹配分排序。
"""

from __future__ import annotations

from typing import Any

from destiny_mcp.services.weapon_profile import find_weapon, weapon_versions

OLD, NEW, OTHER = 235827225, 386864872, 999_999_999


def _manifest() -> Any:
    """形状照真实现：`search` 返回全部同名条目，`get_item_definition` 给定义。"""

    def definition(hash_: int, name: str, index: int) -> dict:
        return {"hash": hash_, "index": index, "displayProperties": {"name": name},
                "itemType": 3}

    defs = {
        OLD: definition(OLD, "鹰月", 23927),          # 旧版（就是被挑错的那版）
        NEW: definition(NEW, "鹰月", 35200),          # 新版
        OTHER: definition(OTHER, "鹰月之影", 99999),   # 名字相近的另一把枪，index 更高
    }

    class _Manifest:
        def search(self, _name: str, limit: int = 0, item_type: int = 0):
            return [
                {"itemHash": OTHER, "itemType": 3},
                {"itemHash": OLD, "itemType": 3},
                {"itemHash": NEW, "itemType": 3},
            ]

        def get_item_definition(self, hash_: int):
            return defs.get(int(hash_))

    return _Manifest()


def test_newest_version_wins() -> None:
    hash_, definition = find_weapon(_manifest(), "鹰月")
    assert hash_ == NEW, f"要取最新版（index 35200），实际取了 {hash_}"
    assert definition["index"] == 35200


def test_versions_are_newest_first_and_exact_name_only() -> None:
    versions = weapon_versions(_manifest(), "鹰月")
    assert [v["item_hash"] for v in versions] == [NEW, OLD], versions
    assert [v["index"] for v in versions] == [35200, 23927]
    assert OTHER not in [v["item_hash"] for v in versions], "名字相近的其他枪不许混进来"


def test_unknown_name_raises() -> None:
    import pytest

    from destiny_mcp.exceptions import ManifestError

    class _Empty:
        def search(self, *_a, **_k):
            return []

    with pytest.raises(ManifestError):
        find_weapon(_Empty(), "不存在的枪")
