"""把「只发标量」的宿主发来的字符串，还原成签名里声明的结构化参数。

背景（真机，2026-09-23）：豆包 connector 转发工具调用时只序列化标量，
`list[str]` / `dict` 参数根本送不到服务端 —— `required_perks` 到服务端变成 `[[]]`、
`changes` 直接消失，调用方只看到一句 `invalid_arguments` 却猜不出错在哪。
这不是我们要拦的错，是宿主的能力边界，所以每个结构化参数额外收**一种文本写法**
（列表 `A,B`、映射 `k=v,k2=v2`），schema 里两种形态并列，任何宿主都看得见能用哪种。

三条规矩：

- **不是兼容层**：`list` / `dict` 仍是一等公民，字符串只是同一参数的第二种写法，
  两种形态走同一段解析；解析规则（分隔符、键值写法）唯一出处是
  `destiny_mcp/utils/arg_text.py`，这里只登记"哪个参数是什么类型"。
- **表必须与 schema 对得上**：登记的每个参数都要求签名里真的开出 `string` 分支，
  `tests/test_connector_scalar_params.py` 拿 `create_server().list_tools()` 逐个核对，
  所以这份表没办法跟签名分叉。
- **只解析一次**：还原发生在 `@handle_tool_error` 之内、`@check_intent_parameters`
  与模型校验之前，下游（分组校验、参数归属、函数体、服务层）看到的都是解析后的值。
"""

from __future__ import annotations

from functools import wraps
from inspect import signature
from typing import Any, NamedTuple

from ..error_codes import ErrorCode
from ..utils.arg_text import split_items, split_pairs
from ._responses import error_response

_ITEMS, _INTS = "items", "ints"  # list[str] / dict[str, int]
_TEXTS = "texts"  # dict[str, str]


class ScalarParameter(NamedTuple):
    """一个参数的文本写法：怎么拆，以及报错时给调用方看哪个例子。"""

    kind: str
    example: str


# 参数名 → 文本写法。参数名在八个工具里唯一，所以不必再按工具分组。
# 新增结构化参数时**必须**登记：`tests/test_connector_scalar_params.py` 会扫签名，
# 发现 `list[...] | str` 这种"想收文本却没登记"的形态就判红。
SCALAR_PARAMETERS: dict[str, ScalarParameter] = {
    "required_perks": ScalarParameter(_ITEMS, "亡者复仇,速射"),
    "any_perks": ScalarParameter(_ITEMS, "亡者复仇,速射"),
    "excluded_perks": ScalarParameter(_ITEMS, "亡者复仇,速射"),
    "priority_stats": ScalarParameter(_ITEMS, "weapons,grenade"),
    "fragment_names": ScalarParameter(_ITEMS, "保护之光,聚焦打击"),
    "functional_mods": ScalarParameter(_ITEMS, "充沛,特殊武器弹药搜寻者"),
    "item_instance_ids": ScalarParameter(_ITEMS, "6917530188460608169,6917530188460608170"),
    "stat_caps": ScalarParameter(_INTS, "grenade=100,melee=90"),
    "changes": ScalarParameter(_TEXTS, "super=金色枪"),
}


class ScalarArgumentError(ValueError):
    """文本写法解析失败。只在本模块的装饰器里被接住，不会冒到 `handle_tool_error`。"""


def coerce_value(parameter: str, value: Any) -> Any:
    """按登记的类型还原一个参数值；不是文本的原样返回。

    `""` 按"没传"处理（与 `null` 同一条哨兵规则）：宿主把没填的参数发成空串时，
    它不该变成"筛一个叫空字符串的 perk"。
    """
    spec = SCALAR_PARAMETERS.get(parameter)
    if spec is None or not isinstance(value, str):
        return value
    if not value.strip():
        return None
    if spec.kind == _ITEMS:
        return split_items(value)
    try:
        pairs = split_pairs(value)
    except ValueError as exc:
        raise ScalarArgumentError(
            f"{parameter} 要键值对：{exc}"
            f'正确写法是"{spec.example}"（多项用逗号、顿号、分号或换行分隔）。'
        ) from exc
    if spec.kind == _INTS:
        return {key: _as_int(parameter, key, item) for key, item in pairs}
    return dict(pairs)


def _as_int(parameter: str, key: str, item: str) -> int:
    try:
        return int(item)
    except ValueError as exc:
        raise ScalarArgumentError(
            f'{parameter} 的 {key} 要整数，收到 "{item}"；例如 "{key}=100"。'
        ) from exc


def coerce_scalar_arguments(func):
    """把登记过的结构化参数从文本形态还原出来。

    挂在 `@handle_tool_error` 之内：解析失败要走 `invalid_arguments` + 中文说明，
    而不是让 `ValueError` 冒到框架层变成一句 500。
    """
    parameters = signature(func)
    targets = [name for name in parameters.parameters if name in SCALAR_PARAMETERS]

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if targets:
            bound = parameters.bind(*args, **kwargs)
            try:
                for name in targets:
                    if name in bound.arguments:
                        bound.arguments[name] = coerce_value(name, bound.arguments[name])
            except ScalarArgumentError as exc:
                return error_response(ErrorCode.INVALID_ARGUMENTS, str(exc))
            args, kwargs = bound.args, bound.kwargs
        return await func(*args, **kwargs)

    return wrapper
