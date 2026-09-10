"""全部 MCP 工具的模拟调用测试。

`full` profile 注册了 77 个工具，其中 69 个历史工具此前只有「能注册」的检查，
没有任何行为覆盖。这里用一套记录型假服务把每个工具真调一遍。

覆盖的是「不崩、不返回 None、失败要走错误响应」，不是「业务成功」：
空参数下返回参数校验错误是正确结果。

过程中确认了一件事：**77 个工具并存三种返回契约**。
8 个 assistant 用 ok/error 信封（由 _responses 提供），部分历史工具用
ok_response，多数历史工具直接返回原始载荷，个别（如 find_players）返回纯字符串。
这里分别按各自契约断言，而不是假装它们统一。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from destiny_mcp import server
from destiny_mcp.service_context import ServiceContext
from destiny_mcp.tools._registry import mcp as registry

# 没有默认值的参数：给一个类型合适的占位值。
# 其余参数给内容是为了越过参数校验、真正走到服务层。
REQUIRED_ARGUMENTS: dict[str, Any] = {
    "mod_hash": 1,
    "name": "测试名称",
    "image_base64": "aGVsbG8=",
    "url": "https://example.invalid/page",
    "llm_response": "{}",
    "activity_id": "12345",
    "player_name": "Tester#1234",
    "character": "hunter",
    "item_name": "测试物品",
    "weapon_name": "测试武器",
    "perk_name": "测试 Perk",
    "type_name": "手炮",
    "vendor_name": "班西-44",
    "query": "测试",
    "loadout_id": "local:1",
    "item_instance_id": "1",
    "item_instance_ids": ["1", "2"],
    "group_id": "12345",
    "path": "Destiny2/Milestones/",
    "set_name": "埃希恩记忆",
    "armor_name": "测试护甲",
    "set_bonus_name": "埃希恩记忆",
    "exotic_name": "测试金装",
    "fragment_name": "测试碎片",
    "weapon_type": "手炮",
    "mode": "raid",
    "statid": "activitiesCleared",
    "destination": "vault",
    "to_character": "hunter",
    "from_character": "hunter",
}

ASSISTANT_NAMES = {
    "player_assistant",
    "inventory_assistant",
    "weapon_assistant",
    "build_assistant",
    "loadout_assistant",
    "subclass_assistant",
    "activity_assistant",
    "world_assistant",
}


class _Reply:
    """假服务返回值：任意键、任意属性都返回一个空载荷，并且可被 await。

    刻意**不继承 dict**：真实响应模型（如 InventoryResponse）上有 `.items`
    这样的列表属性，而 dict 的 `.items` 是方法，继承会让工具拿到方法而不是列表。
    因此这里用自动填充对象，`.items` 之类的属性访问返回空载荷。
    """

    def __getattr__(self, name: str) -> "_Reply":
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Reply()

    def __getitem__(self, key: object) -> "_Reply":
        return _Reply()

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

    def model_dump(self, *args: object, **kwargs: object) -> "_Reply":
        """让 `_dump()` 原样返回本对象，从而保留对缺失键的宽容。"""
        return self

    def __await__(self):
        if False:  # pragma: no cover - 让它成为生成器
            yield
        return self


class _Recorder:
    """可无限下钻的可调用对象，把每次调用记进共享的 sink。

    下钻是必须的：`bungie.rest.static_request(...)` 这类两级访问如果只返回
    一个普通函数，`.static_request` 就会以 mock 假象报 AttributeError。
    """

    def __init__(self, label: str, sink: list[str]) -> None:
        self.label = label
        self._sink = sink

    def __getattr__(self, name: str) -> "_Recorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _Recorder(f"{self.label}.{name}", self._sink)

    def __call__(self, *args: Any, **kwargs: Any) -> _Reply:
        self._sink.append(self.label)
        return _Reply()


class _Context:
    """构造 get_ctx 需要的 ctx 形状，并记录服务调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.services = {
            key: _Recorder(key, self.calls) for key in ServiceContext.__annotations__
        }
        self.request_context = type(
            "_RequestContext", (), {"lifespan_context": self.services}
        )()

    @property
    def called(self) -> list[str]:
        return self.calls


def _definitions() -> list:
    server.create_server("full")
    return sorted(registry._definitions, key=lambda d: d.function.__name__)


def _arguments(function, ctx: _Context) -> dict[str, Any]:
    signature = inspect.signature(function)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name == "ctx":
            continue
        if parameter.default is inspect.Parameter.empty:
            assert name in REQUIRED_ARGUMENTS, f"{function.__name__} 缺少占位值: {name}"
            kwargs[name] = REQUIRED_ARGUMENTS[name]
        elif name == "confirmed":
            # 写操作要走到底，而不是停在确认步骤
            kwargs[name] = True
        elif name in REQUIRED_ARGUMENTS:
            # 按名字填，而不是按注解：很多参数是 Annotated[str, Field(...)]
            # 或 str | None，`annotation is str` 匹配不到，会静默留空。
            kwargs[name] = REQUIRED_ARGUMENTS[name]
    kwargs["ctx"] = ctx
    return kwargs


async def _call(definition) -> tuple[Any, _Context]:
    ctx = _Context()
    return await definition.function(**_arguments(definition.function, ctx)), ctx


def test_full_profile_exposes_every_registered_tool() -> None:
    definitions = _definitions()

    assert len(definitions) == 77
    assert len({d.function.__name__ for d in definitions}) == 77


@pytest.mark.parametrize("definition", _definitions(), ids=lambda d: d.function.__name__)
async def test_tool_never_raises_or_returns_none(definition) -> None:
    """含 69 个历史工具在内的全部工具，用假服务调用一次不能抛异常。"""
    result, _ = await _call(definition)

    assert result is not None, f"{definition.function.__name__} 返回了 None"


@pytest.mark.parametrize(
    "definition",
    [d for d in _definitions() if d.function.__name__ in ASSISTANT_NAMES],
    ids=lambda d: d.function.__name__,
)
async def test_assistants_always_return_the_response_envelope(definition) -> None:
    """8 个 assistant 的契约是统一的信封；历史工具不适用这一条。"""
    result, _ = await _call(definition)

    assert isinstance(result, dict), f"{definition.function.__name__} 不是 dict"
    assert isinstance(result.get("ok"), bool), f"{definition.function.__name__} 缺少 ok"
    if result["ok"] is False:
        assert result.get("error", {}).get("code"), "失败时没有给出 error.code"


async def test_failed_tools_return_a_structured_error_not_a_traceback() -> None:
    """空参数下失败是正常的，但失败必须是响应里的错误，而不是异常。"""
    failures = []
    for definition in _definitions():
        result, _ = await _call(definition)
        if isinstance(result, dict) and result.get("ok") is False:
            if not result.get("error", {}).get("code"):
                failures.append(definition.function.__name__)

    assert not failures, f"这些工具失败时没有给出 error.code: {failures}"


async def test_most_tools_reach_the_service_layer() -> None:
    """多数工具要真的走到服务层，而不是停在参数校验。

    当前是 73/77；未进入的 4 个都卡在模拟环境造不出的前置条件
    （apply_mod、equip_build、get_collectible_node_status、modify_subclass）。
    下限设为 70：掉下去说明有工具连服务层都进不去了。
    """
    reached: list[str] = []
    stuck: list[str] = []
    for definition in _definitions():
        _, ctx = await _call(definition)
        (reached if ctx.called else stuck).append(definition.function.__name__)

    assert len(reached) >= 70, f"只有 {len(reached)} 个工具进入服务层；未进入: {stuck}"


async def test_every_assistant_reaches_the_service_layer() -> None:
    """8 个 assistant 是默认工具面，一个都不能卡在校验上。"""
    stuck: list[str] = []
    for definition in _definitions():
        if definition.function.__name__ not in ASSISTANT_NAMES:
            continue
        _, ctx = await _call(definition)
        if not ctx.called:
            stuck.append(definition.function.__name__)

    assert not stuck, f"这些 assistant 没有进入服务层: {stuck}"


async def test_write_intents_do_not_reach_the_service_layer_unconfirmed() -> None:
    """写入 intent 在 confirmed=False 时必须停在确认，不能碰服务层。

    intent 清单取自 _requests.WRITE_INTENTS（写入意图的单一来源）；
    工具不认这个 intent 时会回 unsupported_intent，那种情况跳过。
    读 intent 不受 confirmed 约束，所以这里只遍历写入 intent。
    """
    from destiny_mcp.tools._requests import WRITE_INTENTS

    violations: list[str] = []
    checked = 0
    for definition in _definitions():
        parameters = inspect.signature(definition.function).parameters
        if "confirmed" not in parameters or "intent" not in parameters:
            continue
        for intent in sorted(WRITE_INTENTS):
            ctx = _Context()
            kwargs = _arguments(definition.function, ctx)
            kwargs["confirmed"] = False
            kwargs["intent"] = intent
            result = await definition.function(**kwargs)
            if isinstance(result, dict) and result.get("error", {}).get("code") == "unsupported_intent":
                continue
            checked += 1
            if ctx.called:
                violations.append(f"{definition.function.__name__}({intent}) -> {ctx.called[0]}")

    assert checked >= 15, f"只检查到 {checked} 个写入 intent，覆盖不足"
    assert not violations, f"未确认就调用了服务层: {violations}"
