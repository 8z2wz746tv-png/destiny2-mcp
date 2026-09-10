"""参数与 intent 的对应关系：没人认的参数直接拒绝，而不是静默忽略。

八个 assistant 各自只有一个宽签名，任何 intent 都能收到全部参数。结果是
`inventory_assistant(intent="get", item_instance_id="123")` 会安静地丢掉
item_instance_id，然后像没事一样返回整包清单 —— 信封完整、内容答非所问，
调用方没有任何线索发现自己问错了入口。

这里登记的是「定位对象」的参数：回答的是"问的是哪一件"。只有声明认领它的
intent 收得下，其余 intent 收到就返回 ignored_parameter，并指出该换成哪个入口。

有意不登记的部分（不是遗漏，是决定）：

- limit/offset/location/rarity 这类范围与分页参数。它们被忽略只是范围不对，
  不会把答案指到别的对象上；而且没有等价的替代 intent，拒绝只会把一次本来
  可用的调用变成一次拒绝。
- 表按 (工具, 参数) 逐项声明，不是从代码里推导出来的。没登记的 (工具, 参数)
  组合目前仍然不拦。这一层靠持续登记推进，不假装已经全覆盖；登记的顺序按
  实际踩到的坑走，不追求一次填满。

新增 intent 或给某个 intent 增加参数时：把它加进对应的 intents 集合，
否则新 intent 会拒绝这个参数（这是故意的方向 —— 先吵架，再静默答错）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import wraps
from inspect import signature
from typing import Any, Literal, NamedTuple, get_args, get_origin

from ._responses import error_response


class ParameterContract(NamedTuple):
    """一个「定位对象」的参数：谁认它、被拒时怎么改。"""

    intents: frozenset[str]
    hint: str
    # (工具, intent)：最可能想走的那条路。改动在参数上而不是 intent 上时留 None，
    # 因为把 next_actions 指到一个不对的 intent 比不给建议更糟。
    suggestion: tuple[str, str] | None


_ITEM_NAME = ParameterContract(
    intents=frozenset(
        {
            "search", "find_item",
            "duplicates", "duplicate_weapons", "find_duplicates", "重复武器",
            "move",
        }
    ),
    hint='按名字找物品用 intent="search"；查重复武器用 intent="duplicates"；移动物品用 intent="move"。',
    suggestion=("inventory_assistant", "search"),
)

PARAMETER_OWNERS: dict[tuple[str, str], ParameterContract] = {
    ("inventory_assistant", "item_name"): _ITEM_NAME,
    ("inventory_assistant", "item_instance_id"): ParameterContract(
        intents=frozenset(
            {
                "move", "transfer", "equip", "pull_postmaster",
                "lock", "track_quest", "quest_tracking",
            }
        ),
        hint=(
            '读某个副本当前 Perk 用 weapon_assistant(intent="compare", weapon_name=..., '
            'item_instance_id=...)；移动/装备/锁定/取回分别用 intent="move"/"equip"/"lock"/"pull_postmaster"。'
        ),
        suggestion=("weapon_assistant", "compare"),
    ),
    ("inventory_assistant", "item_instance_ids"): ParameterContract(
        intents=frozenset({"equip_many", "equip_items"}),
        hint='只有批量装备 intent="equip_many" 使用这个参数；单个副本用 item_instance_id。',
        suggestion=("inventory_assistant", "equip_many"),
    ),
    ("inventory_assistant", "type_name"): ParameterContract(
        intents=frozenset(
            {
                "duplicates", "duplicate_weapons", "find_duplicates", "重复武器",
                "type", "search_type",
            }
        ),
        hint='按类型查询用 intent="type"（不是 item_type）；查某类型的重复武器用 intent="duplicates"。',
        suggestion=("inventory_assistant", "type"),
    ),
    ("inventory_assistant", "item_type"): ParameterContract(
        intents=frozenset(
            {
                "summary", "summarize", "概况",
                "get", "inventory", "list",
                "type", "search_type",
            }
        ),
        hint='按类型查询用 intent="type"；按名字查用 intent="search"。duplicates 用的是 type_name。',
        suggestion=("inventory_assistant", "type"),
    ),
    ("weapon_assistant", "item_instance_id"): ParameterContract(
        intents=frozenset({"compare", "compare_duplicates"}),
        hint='对比同名副本用 intent="compare"；只看武器本体用 intent="analyze"（不接受副本 ID）。',
        suggestion=("weapon_assistant", "compare"),
    ),
    ("weapon_assistant", "weapon_name"): ParameterContract(
        intents=frozenset(
            {
                "analyze", "catalog", "search_catalog", "all_weapons", "global", "search_all",
                "filter_rolls", "compare", "compare_duplicates",
                "perk_pool", "perks", "god_roll",
                "popularity", "selection_rates", "perk_selection", "selection", "usage_rates",
                "info", "stats", "catalyst", "community",
            }
        ),
        hint=(
            '按武器类型查要传 weapon_type（intent="type"，不是 weapon_name）；'
            '查武器基础信息用 intent="info"；查单个 Perk 用 perk_name（intent="perk_description"）。'
        ),
        suggestion=None,
    ),
}


def _is_supplied(value: Any) -> bool:
    """空值等于没传：客户端把默认值一起发过来不应该被拒。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def intent_accepts_parameter(tool: str, intent: str, parameter: str) -> bool:
    """这个 intent 是否收得下这个参数。没登记的参数一律放行。"""
    contract = PARAMETER_OWNERS.get((tool, parameter))
    return contract is None or intent in contract.intents


def check_parameter_ownership(
    tool: str,
    intent: str,
    supplied: Mapping[str, Any],
) -> dict[str, Any] | None:
    """有参数没人认就返回错误响应，否则返回 None。"""
    rejected: list[tuple[str, ParameterContract]] = [
        (name, contract)
        for name, value in supplied.items()
        if _is_supplied(value)
        and (contract := PARAMETER_OWNERS.get((tool, name))) is not None
        and intent not in contract.intents
    ]
    if not rejected:
        return None

    details = " ".join(f"{name}（{contract.hint}）" for name, contract in rejected)
    names = "、".join(name for name, _ in rejected)
    suggestions: list[dict[str, Any]] = [
        {
            "label": f'改用 {contract.suggestion[0]} intent="{contract.suggestion[1]}"',
            "tool": contract.suggestion[0],
            "arguments": {"intent": contract.suggestion[1]},
        }
        for _, contract in rejected
        if contract.suggestion is not None
    ]
    return error_response(
        "ignored_parameter",
        f'{tool}(intent="{intent}") 不读 {names}，传进来的值会被忽略，不会被当成查询条件。{details}',
        next_actions=suggestions,
    )


def declared_intents(function: Callable) -> frozenset[str]:
    """从签名上的 Literal 注解读出这个工具声明支持的 intent。

    注解是字符串（模块有 `from __future__ import annotations`），所以要走
    eval_str；取不到就返回空集合，表示"不校验 intent 名单"。
    """
    try:
        annotation = signature(function, eval_str=True).parameters["intent"].annotation
    except Exception:  # 注解解析不了就不拦，交给原有的 unsupported_intent 分支
        return frozenset()
    if hasattr(annotation, "__metadata__"):  # Annotated[Literal[...], Field(...)]
        annotation = annotation.__origin__
    if get_origin(annotation) is Literal:
        return frozenset(str(item) for item in get_args(annotation))
    return frozenset()


def check_intent_parameters(function: Callable) -> Callable:
    """拦住「intent 不读这个参数」。装在每个 assistant 上。"""
    tool = function.__name__
    parameters = signature(function)
    intents = declared_intents(function)

    @wraps(function)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            bound = parameters.bind(*args, **kwargs)
        except TypeError:  # 参数本身就不对，让原函数照常报错
            return await function(*args, **kwargs)
        intent = bound.arguments.get("intent")
        if intent is None and "intent" in parameters.parameters:
            intent = parameters.parameters["intent"].default
        intent = str(intent or "").strip().lower()
        # intent 不在声明名单里时不插嘴：那种情况原有的 unsupported_intent 说得更准
        if intent and (not intents or intent in intents):
            rejected = check_parameter_ownership(
                tool, intent, {k: v for k, v in bound.arguments.items() if k != "intent"}
            )
            if rejected is not None:
                return rejected
        return await function(*args, **kwargs)

    return wrapped
