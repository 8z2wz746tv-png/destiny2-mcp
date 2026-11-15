"""只发标量的宿主（豆包 connector）也能把结构化参数送进来。

真机事故（2026-09-23，用户实测）：豆包转发工具调用时只序列化标量 ——
`required_perks` 到服务端变成 `[[]]`、`changes` 直接消失，调用方只看到
`invalid_arguments` 却猜不出错在哪。修法是给每个结构化参数再收**一种文本写法**，
schema 里两种形态并列。

这个文件钉住四件事，缺一条修法就会退回去：

1. 登记表里的参数在 schema 里真的开出 `string` 分支（宿主看得见才用得上）；
2. 反过来：签名里"想做文本却又收 list/dict"的参数必须登记（不然就是悄悄少一个入口）；
3. 文本怎么拆只有一份规则，且半角/全角写法都要认；
4. 还原发生在工具函数体之前、出错走 `invalid_arguments`（而不是 500 或静默吞掉）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import pytest

from destiny_mcp import server
from destiny_mcp.tools import _coerce
from destiny_mcp.tools import assistants
from destiny_mcp.tools._coerce import SCALAR_PARAMETERS, coerce_scalar_arguments, coerce_value
from destiny_mcp.utils.arg_text import split_items, split_pairs

# 工具 → 该工具真的收文本写法的参数。写死一份是为了让"少接一个入口"变成一次
# 有意识的改动：schema 守门会拿它和实际 schema 逐字对。
EXPECTED = {
    "weapon_assistant": {"required_perks", "any_perks", "excluded_perks"},
    "build_assistant": {"priority_stats", "fragment_names", "functional_mods", "stat_caps"},
    "inventory_assistant": {"item_instance_ids"},
    "subclass_assistant": {"changes"},
}


def _assistant_functions() -> dict[str, Any]:
    return {
        name: value
        for name, value in vars(assistants).items()
        if name.endswith("_assistant") and callable(value)
    }


def _unannotated(hint: Any) -> Any:
    return get_args(hint)[0] if get_origin(hint) is Annotated else hint


def _is_text_capable(hint: Any) -> bool:
    """`list[...] | str` / `dict[...] | str` 这类"两种形态都收"的注解。"""
    parts = get_args(_unannotated(hint))
    if str not in parts:
        return False
    return any(get_origin(part) in {list, dict} for part in parts)


async def _schemas() -> dict[str, dict[str, Any]]:
    return {
        tool.name: tool.inputSchema.get("properties", {})
        for tool in await server.create_server().list_tools()
    }


async def test_text_form_parameters_publish_a_string_branch() -> None:
    """登记过的参数必须在 schema 里开出 string 分支 —— 宿主只看得见 schema。"""
    schemas = await _schemas()
    actual: dict[str, set[str]] = {}
    for tool, parameters in schemas.items():
        hits = {name for name in parameters if name in SCALAR_PARAMETERS}
        if hits:
            actual[tool] = hits

    assert actual == EXPECTED, "哪个工具收哪些文本参数变了；同步 EXPECTED 与 _param_docs 说明"

    for tool, parameters in EXPECTED.items():
        for name in parameters:
            branch = schemas[tool][name]
            kinds = {option.get("type") for option in branch.get("anyOf", [branch])}
            assert "string" in kinds, f"{tool}.{name} 没有 string 分支：{branch}"


async def test_the_nested_build_has_a_scalar_substitute() -> None:
    """`canonical_build` 是结构体，只发标量的宿主改用 `execution_id`（同一件事的标量形态）。"""
    schemas = await _schemas()
    branch = schemas["build_assistant"]["execution_id"]
    kinds = {option.get("type") for option in branch.get("anyOf", [branch])}

    assert kinds == {"string"}, branch


def test_text_capable_parameters_are_registered() -> None:
    """签名里"既收 list/dict 又收 str"的参数必须登记，否则文本写法写不出去。"""
    found = {
        name
        for tool in _assistant_functions().values()
        for name, hint in get_type_hints(tool, include_extras=True).items()
        if _is_text_capable(hint)
    }
    assert found == set(SCALAR_PARAMETERS), (
        "有参数想收文本却没登记（或登记了却没人收）："
        f"签名 {sorted(found)} vs 登记 {sorted(SCALAR_PARAMETERS)}"
    )


def test_split_items_accepts_every_documented_separator() -> None:
    for separator in ("，", ",", "、", ";", "；", "\n"):
        assert split_items(f"亡者复仇{separator}速射") == ["亡者复仇", "速射"], separator


def test_split_items_leaves_a_sequence_alone() -> None:
    """已经分好词的序列不再拆一次：带逗号的英文名不会被拆坏。"""
    assert split_items(["Field Prep, 亡者复仇"]) == ["Field Prep, 亡者复仇"]
    assert split_items("") == []
    assert split_items(["", "  "]) == []


def test_split_pairs_accepts_every_documented_operator() -> None:
    assert split_pairs("grenade=100，melee:90、super：80") == [
        ("grenade", "100"),
        ("melee", "90"),
        ("super", "80"),
    ]


def test_split_pairs_rejects_a_fragment_without_a_value() -> None:
    with pytest.raises(ValueError, match="k=v"):
        split_pairs("grenade")
    with pytest.raises(ValueError, match="k=v"):
        split_pairs("grenade=")


def test_the_service_layer_uses_the_same_separator_rule() -> None:
    """武器筛选服务以前自己写了一份 `split(",")`：两处规则分叉过一次，别再分叉。"""
    from destiny_mcp.services.weapon_roll_filter_service import WeaponRollFilterService

    assert WeaponRollFilterService._split_terms("亡者复仇、速射；Quickdraw") == [
        "亡者复仇",
        "速射",
        "quickdraw",
    ]


def test_coerce_value_leaves_native_forms_untouched() -> None:
    assert coerce_value("required_perks", ["亡者复仇"]) == ["亡者复仇"]
    assert coerce_value("stat_caps", {"grenade": 100}) == {"grenade": 100}
    assert coerce_value("changes", {"super": "金色枪"}) == {"super": "金色枪"}
    assert coerce_value("stat_caps", None) is None
    assert coerce_value("weapon_name", "亡者复仇") == "亡者复仇"


def test_an_empty_string_counts_as_not_provided() -> None:
    """空串与 null 同一条哨兵规则：它不该变成"筛一个空名字"。"""
    assert coerce_value("required_perks", "") is None
    assert coerce_value("changes", "   ") is None


def test_coerce_value_parses_both_text_shapes() -> None:
    assert coerce_value("fragment_names", "保护之光,聚焦打击") == ["保护之光", "聚焦打击"]
    assert coerce_value("stat_caps", "grenade=100，melee=90") == {"grenade": 100, "melee": 90}
    assert coerce_value("changes", "super=金色枪") == {"super": "金色枪"}


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("changes", "super"), ("stat_caps", "grenade=abc"), ("stat_caps", "=100")],
)
def test_bad_text_is_invalid_arguments_not_a_traceback(parameter: str, value: str) -> None:
    """写法不对要给中文说明与正确例子，不能冒到框架层变成 500。"""
    with pytest.raises(_coerce.ScalarArgumentError) as excinfo:
        coerce_value(parameter, value)
    message = str(excinfo.value)
    assert parameter in message
    assert "0" in message or "=" in message  # 给了可照抄的例子


async def test_decorator_parses_before_the_body_and_reports_bad_text() -> None:
    """还原在函数体之前发生一次；坏写法在这里就结束，不进函数体。"""
    seen: list[Any] = []

    @coerce_scalar_arguments
    async def tool(changes: dict[str, str] | str | None = None) -> Any:
        seen.append(changes)
        return "ok"

    assert await tool(changes="super=金色枪") == "ok"
    assert seen == [{"super": "金色枪"}]

    result = await tool(changes="super")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_arguments"
    assert "changes" in result["error"]["message"]
    assert seen == [{"super": "金色枪"}], "坏写法不该进函数体"


class _EmptyServices:
    """只到确认那一步的假 ctx：分支在确认前不碰服务层。"""

    request_context = SimpleNamespace(lifespan_context={})


async def test_equip_many_accepts_a_text_list() -> None:
    result = await assistants.inventory_assistant(
        intent="equip_many",
        item_instance_ids="1,2",
        character="hunter",
        player_name="Tester#1234",
        ctx=_EmptyServices(),
    )
    assert result["error"]["code"] == "confirmation_required"
    assert result["candidates"][0]["item_instance_ids"] == ["1", "2"]


async def test_subclass_modify_accepts_text_changes() -> None:
    result = await assistants.subclass_assistant(
        intent="modify",
        character="hunter",
        changes="super=金色枪",
        player_name="Tester#1234",
        ctx=_EmptyServices(),
    )
    assert result["error"]["code"] == "confirmation_required"
    assert result["candidates"][0]["changes"] == {"super": "金色枪"}
