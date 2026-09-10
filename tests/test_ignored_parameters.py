"""参数与 intent 的对应关系：表里每一行都要能被验证。

`_param_contracts.PARAMETER_OWNERS` 是一份声明。这里用两条断言把它变成事实：

1. 没认领这个参数的 intent 收到它必须**拒绝**，不能静默忽略；
2. 认领了它的 intent 必须真把这个值**带到服务层**——用哨兵值穿透假服务，
   在记录下来的调用参数里找它。表说谁认，谁就得真认。

第 2 条是这张表不会腐烂的原因：以后有人把某个 intent 改成不读这个参数了，
这里会红；而不是等到某次问答答非所问才发现。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from destiny_mcp import server
from destiny_mcp.tools import _param_contracts as contracts
from destiny_mcp.tools._registry import mcp as registry

SENTINEL = "哨兵值-9f3a"

# 每个 intent 的必需参数先补齐，再单独加上被测参数（后者覆盖前者）。
# 目的是让调用走到「这个参数有没有人认」这一步，而不是停在别的校验错误上。
EXTRA_ARGS: dict[tuple[str, str], dict[str, Any]] = {
    ("inventory_assistant", "move"): {
        "item_name": "测试物品", "destination": "vault", "item_instance_id": "1",
    },
    ("inventory_assistant", "transfer"): {"item_instance_id": "1", "to_character": "hunter"},
    ("inventory_assistant", "equip"): {"item_instance_id": "1", "character": "hunter"},
    ("inventory_assistant", "equip_many"): {"character": "hunter", "item_instance_ids": ["a", "b"]},
    ("inventory_assistant", "equip_items"): {"character": "hunter", "item_instance_ids": ["a", "b"]},
    ("inventory_assistant", "pull_postmaster"): {"item_instance_id": "1"},
    ("inventory_assistant", "lock"): {"item_instance_id": "1"},
    ("inventory_assistant", "track_quest"): {"item_instance_id": "1"},
    ("inventory_assistant", "quest_tracking"): {"item_instance_id": "1"},
}


class _Reply:
    """假服务返回值：任意键、任意属性都返回空载荷，并且可被 await。"""

    def __getattr__(self, name: str) -> "_Reply":
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Reply()

    def __getitem__(self, key: object) -> "_Reply":
        return _Reply()

    def __setitem__(self, key: object, value: object) -> None:
        pass

    def __or__(self, other: object) -> object:
        return other

    def __contains__(self, key: object) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def get(self, key: object, default: object = None) -> "_Reply":
        return _Reply()

    def keys(self) -> tuple:
        """`dict(回复)` 这类转换要能过，空载荷视为没有字段。"""
        return ()

    def model_dump(self, *args: object, **kwargs: object) -> "_Reply":
        return self

    def __await__(self):
        if False:  # pragma: no cover - 让它成为生成器
            yield
        return self


class _Recorder:
    """可无限下钻的可调用对象，把每次调用的参数记进 sink。"""

    def __init__(self, label: str, sink: list) -> None:
        self.label = label
        self._sink = sink

    def __getattr__(self, name: str) -> "_Recorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _Recorder(f"{self.label}.{name}", self._sink)

    def __call__(self, *args: Any, **kwargs: Any) -> _Reply:
        self._sink.append((self.label, args, kwargs))
        return _Reply()


class _Context:
    def __init__(self) -> None:
        from destiny_mcp.service_context import ServiceContext

        self.calls: list = []
        self.services = {
            key: _Recorder(key, self.calls) for key in ServiceContext.__annotations__
        }
        self.request_context = type(
            "_RequestContext", (), {"lifespan_context": self.services}
        )()


def _tools() -> dict[str, Any]:
    server.create_server("full")
    return {d.function.__name__: d.function for d in registry._definitions}


TOOLS = _tools()


def _arguments(tool: str, intent: str, **passed: Any) -> dict[str, Any]:
    available = inspect.signature(TOOLS[tool]).parameters
    kwargs: dict[str, Any] = {"intent": intent}
    if "player_name" in available:
        kwargs["player_name"] = "Tester#1234"
    if "confirmed" in available:
        kwargs["confirmed"] = True
    kwargs.update(EXTRA_ARGS.get((tool, intent), {}))
    kwargs.update(passed)
    return kwargs


async def _call(tool: str, intent: str, **passed: Any):
    ctx = _Context()
    result = await TOOLS[tool](**_arguments(tool, intent, **passed), ctx=ctx)
    return result, ctx.calls


def _values(calls: list) -> list[Any]:
    found: list[Any] = []
    for _label, args, kwargs in calls:
        found.extend(args)
        found.extend(kwargs.values())
    return found


def _carries(calls: list, sentinel: str) -> bool:
    """服务层有没有拿到这个哨兵值（可能在位置参数、关键字参数或列表里）。"""
    stack = _values(calls)
    while stack:
        value = stack.pop()
        if isinstance(value, str) and sentinel in value:
            return True
        if isinstance(value, (list, tuple, set)):
            stack.extend(value)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
    return False


@pytest.mark.parametrize("tool,parameter", sorted(contracts.PARAMETER_OWNERS))
async def test_non_owner_intents_reject_the_parameter(tool: str, parameter: str) -> None:
    """没认领这个参数的 intent 必须拒绝，而不是静默丢掉它。"""
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    function = TOOLS[tool]
    value: Any = [SENTINEL, "另一个"] if parameter.endswith("_ids") else SENTINEL

    silently_ignored = []
    for intent in sorted(contracts.declared_intents(function) - contract.intents):
        result, calls = await _call(tool, intent, **{parameter: value})
        if result.get("error", {}).get("code") != "ignored_parameter":
            silently_ignored.append(
                f'{intent} -> {result.get("error", {}).get("code") or "接受了"}'
                f"{'（已调用服务层）' if calls else ''}"
            )

    assert not silently_ignored, (
        f"{tool} 的 {parameter} 在这些 intent 上没有被拒绝：{silently_ignored}"
    )


# 模型只看得见 schema 的那几个参数：以前它们一个字说明都没有。
DESCRIBED_PARAMETERS = (
    {parameter for _, parameter in contracts.PARAMETER_OWNERS}
    | {"location", "character", "player_name"}
)


def _description(annotation: object) -> str:
    for item in getattr(annotation, "__metadata__", ()):
        text = getattr(item, "description", None)
        if text:
            return str(text)
    return ""


def test_high_risk_parameters_are_described_in_the_schema() -> None:
    """说明必须挂在每个使用点上：新加的同名参数不能再变成没说明的状态。

    只查 8 个 assistant，也就是默认工具面。69 个历史工具里同名参数同样没有说明，
    但它们只在 full/expert profile 下出现，属于另一件事（见 README 的工具面说明）。
    """
    missing = []
    for tool, function in sorted(TOOLS.items()):
        if not tool.endswith("_assistant"):
            continue
        parameters = inspect.signature(function, eval_str=True).parameters
        for name, parameter in parameters.items():
            if name in DESCRIBED_PARAMETERS and not _description(parameter.annotation):
                missing.append(f"{tool}.{name}")

    assert not missing, f"这些参数在 schema 里没有说明：{missing}"


@pytest.mark.parametrize("tool,parameter", sorted(contracts.PARAMETER_OWNERS))
async def test_owner_intents_really_read_the_parameter(tool: str, parameter: str) -> None:
    """认领了就必须真读：哨兵值要出现在服务层的调用参数里。

    这里只看「值有没有到服务层」，不看这一步的业务成败 ——
    假服务下写入类 intent 失败是正常的，静默丢参数才是不正常的。
    """
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    value: Any = [SENTINEL, "另一个"] if parameter.endswith("_ids") else SENTINEL

    not_read = []
    for intent in sorted(contract.intents):
        result, calls = await _call(tool, intent, **{parameter: value})
        if not _carries(calls, SENTINEL):
            code = result.get("error", {}).get("code", "无错误")
            not_read.append(f"{intent} -> {code}（服务层没收到这个值）")

    assert not not_read, (
        f"{tool} 声明这些 intent 认 {parameter}，但实际没读到：{not_read}"
    )


def test_owners_and_suggestions_name_real_intents() -> None:
    """表里写的 intent 和推荐的替代路径必须真实存在，否则拒绝等于把人指进死路。"""
    for (tool, parameter), contract in contracts.PARAMETER_OWNERS.items():
        assert tool in TOOLS, f"{tool} 不是已注册的工具"
        intents = contracts.declared_intents(TOOLS[tool])
        assert contract.intents <= intents, (
            f"{tool} 的 {parameter} 声明了不存在的 intent：{sorted(contract.intents - intents)}"
        )
        suggestion = contract.suggestion
        if suggestion is None:  # 建议的改法在参数上，不在 intent 上
            continue
        suggestion_tool, suggestion_intent = suggestion
        assert suggestion_tool in TOOLS, f"{parameter} 推荐的 {suggestion_tool} 不存在"
        assert suggestion_intent in contracts.declared_intents(TOOLS[suggestion_tool]), (
            f"{parameter} 推荐的 {suggestion_tool}(intent={suggestion_intent}) 不存在"
        )


async def test_empty_values_are_not_treated_as_passed() -> None:
    """客户端把默认值一起发过来（item_name=""）不算传了参数，不能因此被拒。"""
    result, _ = await _call("inventory_assistant", "summary", item_name="", type_name="  ")

    assert result["ok"] is True, result.get("error")


async def test_unknown_intent_keeps_the_unsupported_diagnosis() -> None:
    """intent 本身就不存在时，原有的 unsupported_intent 说得更准，不要抢答。"""
    result, _ = await _call("inventory_assistant", "不存在的意图", item_name=SENTINEL)

    assert result["error"]["code"] == "unsupported_intent"


async def test_real_session_failures_are_now_rejected() -> None:
    """⑰⑱ 来自一次真实会话：模型传了参数，工具安静地忽略了它。

    - get + item_instance_id：想读某个副本，工具却返回了整包清单；
    - summary + item_name：问的是刚玉战锤，工具返回了背包概况。
    """
    result, calls = await _call("inventory_assistant", "get", item_instance_id=SENTINEL)
    assert result["error"]["code"] == "ignored_parameter"
    assert not calls, "拒绝要发生在调用服务层之前"
    assert "compare" in result["error"]["message"]

    result, _ = await _call("inventory_assistant", "summary", item_name="刚玉战锤")
    assert result["error"]["code"] == "ignored_parameter"
    assert 'intent="search"' in result["error"]["message"]
