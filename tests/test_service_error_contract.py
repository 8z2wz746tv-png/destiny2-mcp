"""服务层的参数错误必须抛异常，不能返回 `{"error": ...}`。

曾经有过这样一条路径：`fragment_service` 和 `set_bonus_service` 用
`return {"error": "..."}` 表示"参数不合法 / 查不到"，上层 assistant 把它当普通数据
包进 `ok=true` 的信封。于是模型看到的是**成功**：要么把错误原文当结果念出来，
要么理解成"没有可选项"，回答"猎人现在没有可选的技能"。

信封层（`handle_tool_error`）只会把**异常**转成 `ok=false` + `error.code`，
所以判据很简单：服务层不许返回顶层 error 字典。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from destiny_mcp.exceptions import DefinitionNotFoundError, ItemNotFoundError, SubclassError
from destiny_mcp.services.fragment_service import FragmentService
from destiny_mcp.services.set_bonus_service import SetBonusService

SERVICES_DIR = Path(__file__).parents[1] / "destiny_mcp" / "services"


class _EmptyManifest:
    """只提供「什么都搜不到」的 Manifest 替身。"""

    def search(self, query: str, limit: int = 10) -> list:
        return []

    def get_all_set_bonuses(self) -> dict:
        return {}


def test_no_service_returns_a_top_level_error_dict() -> None:
    """这条是类级别的：任何服务再写 `return {"error": ...}` 都会红。"""
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(SERVICES_DIR.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if 'return {"error"' in line
    ]

    assert not offenders, (
        f"这些地方仍用返回错误字典表示失败，会被包进 ok=true：{offenders}"
    )


def test_fragments_reject_an_unknown_element() -> None:
    service = FragmentService(_EmptyManifest())

    with pytest.raises(SubclassError, match="不支持的元素"):
        service.list_fragments("不存在的元素")


def test_subclass_options_reject_unknown_filters() -> None:
    service = FragmentService(_EmptyManifest())

    with pytest.raises(SubclassError, match="不支持的职业"):
        service.list_subclass_options("不存在的职业", "solar", "super")
    with pytest.raises(SubclassError, match="不支持的元素"):
        service.list_subclass_options("hunter", "不存在的元素", "super")
    with pytest.raises(SubclassError, match="不支持的组件"):
        service.list_subclass_options("hunter", "solar", "不存在的组件")


def test_fragment_details_raises_when_nothing_matches() -> None:
    service = FragmentService(_EmptyManifest())

    with pytest.raises(DefinitionNotFoundError, match="没有匹配的碎片"):
        service.get_fragment_details("不存在的碎片")


def test_set_bonus_lookup_raises_when_nothing_matches() -> None:
    service = SetBonusService(_EmptyManifest())

    with pytest.raises(ItemNotFoundError, match="找不到套装或护甲"):
        service.lookup_armor_set("不存在的套装")
