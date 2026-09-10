"""Manifest 命名辅助的特征测试。

`bucket_name` 与 `item_type_name` 被 `utils/item_parser.py` 当静态方法调用。
这里除了行为，还显式断言「可以不带实例调用」——一次拆分中这两个装饰器被误删，
而当时没有任何测试发现，所以把调用方式本身也钉住。
"""

from __future__ import annotations

import pytest

from destiny_mcp.manifest import BUCKET_NAMES, ITEM_TYPE_NAMES, ManifestManager
from destiny_mcp.utils.hash_utils import to_unsigned


def test_bucket_name_is_callable_without_an_instance() -> None:
    """调用方写的是 manifest.bucket_name(hash)，少一个 @staticmethod 就会 TypeError。"""
    assert ManifestManager.bucket_name(1498876634) == "Kinetic Weapons"


def test_item_type_name_is_callable_without_an_instance() -> None:
    assert ManifestManager.item_type_name(3) == "Weapon"


def test_bucket_name_looks_up_the_table_directly() -> None:
    for bucket_hash, expected in BUCKET_NAMES.items():
        assert ManifestManager.bucket_name(bucket_hash) == expected


def test_bucket_name_accepts_the_unsigned_form_of_a_signed_key() -> None:
    """表里存的是有符号 hash，API 返回无符号，所以负数键要能用无符号形式命中。"""
    signed_hash = next(key for key in BUCKET_NAMES if key < 0)
    expected = BUCKET_NAMES[signed_hash]

    assert ManifestManager.bucket_name(signed_hash) == expected
    assert ManifestManager.bucket_name(to_unsigned(signed_hash)) == expected


def test_bucket_name_marks_unknown_hashes() -> None:
    assert ManifestManager.bucket_name(123456789) == "Bucket(123456789)"


@pytest.mark.parametrize("item_type", sorted(ITEM_TYPE_NAMES))
def test_item_type_name_covers_every_known_type(item_type: int) -> None:
    assert ManifestManager.item_type_name(item_type) == ITEM_TYPE_NAMES[item_type]


def test_item_type_name_marks_unknown_types() -> None:
    assert ManifestManager.item_type_name(999) == "Type(999)"
