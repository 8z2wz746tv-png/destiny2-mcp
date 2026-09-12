"""参数与 intent 的对应关系：表里每一行都要能被验证，而且是双向的。

`_param_contracts.PARAMETER_OWNERS` 是一份声明。这里用三条断言把它钉死在代码上：

1. **没认领的 intent 必须拒绝**（ignored_parameter），不能静默忽略；
2. **认领了就必须真读**：换一个值，服务层记录必须跟着变；
3. **没认领的就真没读**：换一个值，服务层记录必须一模一样。

第 2 条防的是「表写得比代码宽」（该拦的没拦），第 3 条防的是「表写得比代码窄」
（把本来能用的调用拒了）。少任何一条，这张表都会慢慢长歪。

判据用「换个值，调用记录变不变」而不是「在参数里找哨兵值」：后者会被同名的
布尔值、模型 dump 里的默认值误伤 —— 那些假阳性已经在这个文件的历史里出现过。

另外还钉住一条**默认值规则**（文件末尾两节）：签名默认值只能是"空值"
（None/""/0/False），有含义的默认值必须改成 None 哨兵、把默认值搬进函数体。
原因是守卫判断"这次调用算不算传了这个参数"用的是「值 == 签名默认值」——
默认值本身要是也是一个合法请求（limit 的 12、top_n 的 5、locked 的 true、
slot_number 的 1），就没法区分"显式传了默认值"和"没传"，参数会被静默吞掉：
历史上 `vendor` 的 limit=12 就是这样变成"返回默认 40 件"的。
"""

from __future__ import annotations

import inspect
from typing import Any, get_args, get_origin

import pytest

from destiny_mcp import server
from destiny_mcp.tools import _param_contracts as contracts
from destiny_mcp.tools import _requests
from destiny_mcp.tools import assistants as assistants_module
from destiny_mcp.tools._registry import mcp as registry

SENTINEL = "哨兵值-9f3a"
OTHER = "哨兵值-换个值"
# canonical_build 里放一个**不同**的探针：否则它会被后面每个字符串探针命中。
CANONICAL_PROBE = "候选凭据-哨兵"
CANONICAL_OTHER = "候选凭据-换一个"

# equip_build 需要的服务端候选形状：五个部位、实例 ID 唯一、模组为正。
def _canonical(execution_id: str) -> dict[str, Any]:
    return {
        "class_type": "hunter",
        "snapshot_version": "测试快照",
        "execution_id": execution_id,
        "items": [
            {
                "item_hash": 100 + index,
                "slot": slot,
                "item_instance_id": f"示例实例-{slot}",
                "mods": [1],
            }
            for index, slot in enumerate(
                ("helmet", "gauntlets", "chest", "legs", "class_item")
            )
        ],
    }


# 让每个 (工具, intent) 的调用能走到「这个参数有没有人认」这一步：
# 写入 intent 的必填项 + 分支里的前置条件（缺了会提前 return）。
BASELINE: dict[tuple[str, str], dict[str, Any]] = {
    ("player_assistant", "search"): {"player_name": "Tester#1234"},
    ("player_assistant", "search_player"): {"player_name": "Tester#1234"},
    ("player_assistant", "find"): {"name_prefix": "husky"},
    ("player_assistant", "find_players"): {"name_prefix": "husky"},
    ("player_assistant", "fuzzy"): {"name_prefix": "husky"},
    ("inventory_assistant", "search"): {"item_name": "测试物品"},
    ("inventory_assistant", "find_item"): {"item_name": "测试物品"},
    ("inventory_assistant", "type"): {"type_name": "手炮"},
    ("inventory_assistant", "search_type"): {"type_name": "手炮"},
    ("inventory_assistant", "move"): {"item_name": "测试物品", "destination": "vault"},
    ("inventory_assistant", "transfer"): {"item_instance_id": "1", "to_character": "hunter"},
    ("inventory_assistant", "equip"): {"item_instance_id": "1", "character": "hunter"},
    ("inventory_assistant", "equip_many"): {"item_instance_ids": ["a", "b"], "character": "hunter"},
    ("inventory_assistant", "equip_items"): {"item_instance_ids": ["a", "b"], "character": "hunter"},
    ("inventory_assistant", "pull_postmaster"): {"item_instance_id": "1"},
    ("inventory_assistant", "lock"): {"item_instance_id": "1"},
    ("inventory_assistant", "track_quest"): {"item_instance_id": "1"},
    ("inventory_assistant", "quest_tracking"): {"item_instance_id": "1"},
    ("weapon_assistant", "analyze"): {"weapon_name": "测试武器"},
    ("weapon_assistant", "popularity"): {"weapon_name": "测试武器"},
    ("weapon_assistant", "selection_rates"): {"weapon_name": "测试武器"},
    ("weapon_assistant", "perk_selection"): {"weapon_name": "测试武器"},
    ("weapon_assistant", "selection"): {"weapon_name": "测试武器"},
    ("weapon_assistant", "usage_rates"): {"weapon_name": "测试武器"},
    ("build_assistant", "equip_build"): {"canonical_build": _canonical(CANONICAL_PROBE)},
    ("loadout_assistant", "save"): {"name": "测试配装", "character": "hunter"},
    ("loadout_assistant", "delete"): {"loadout_id": "local:1"},
    ("loadout_assistant", "equip_loadout"): {"loadout_id": "local:1"},
    ("loadout_assistant", "snapshot_official"): {"character": "hunter"},
    ("loadout_assistant", "update_official_identifiers"): {"character": "hunter", "name_hash": 1},
    ("loadout_assistant", "clear_official"): {"character": "hunter"},
    ("subclass_assistant", "modify"): {"character": "hunter", "changes": {"超能": "新超能"}},
    ("subclass_assistant", "equip_artifact_mod"): {"character": "hunter", "artifact_mod_hash": 1},
    ("subclass_assistant", "artifact_mod"): {"artifact_mod_hash": 1},
    ("activity_assistant", "pgcr"): {"activity_id": "12345"},
    ("activity_assistant", "clan_leaderboards"): {"group_id": "12345"},
}

# 按 (工具, intent, 参数) 覆盖基线：有些参数只有在特定组合下才谈得上被读。
OVERRIDES: dict[tuple[str, str, str], dict[str, Any]] = {
    # move 的 equip 与"移到仓库"互斥，参数校验会直接拒绝
    ("inventory_assistant", "move", "equip"): {"destination": "hunter"},
    # item_type 只是 type 的回退值：要验证它被读，就不能同时给 type_name
    ("inventory_assistant", "type", "item_type"): {"type_name": ""},
    ("inventory_assistant", "search_type", "item_type"): {"type_name": ""},
    # community_section 只在带 knowledge_id 的读取分支里被用到
    ("weapon_assistant", "community", "community_section"): {
        "knowledge_id": "k1", "weapon_name": "测试武器",
    },
    ("subclass_assistant", "community", "community_section"): {"knowledge_id": "k1"},
    ("activity_assistant", "community", "community_section"): {"knowledge_id": "k1"},
    ("world_assistant", "community", "community_section"): {"knowledge_id": "k1"},
}

WRITE_INTENTS = set(_requests.WRITE_INTENTS) | {"equip_build"}

# 少数参数的约束不在工具签名上，而在下游模型里（例如 BuildRequest 限定 2–4 件），
# 探针要按下游的合法范围给。
PROBE_OVERRIDES: dict[tuple[str, str], tuple[Any, Any]] = {
    ("build_assistant", "set_bonus_count"): (4, 2),
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

    def get(self, key: object, default: object = None) -> Any:
        """像 dict 一样：给了非 None 的默认值就返回默认值。

        否则 `result.get("matched_count", 0) > 1` 会拿假对象比大小，
        工具以 TypeError 结束，测出来的就不是参数归属了。
        """
        return _Reply() if default is None else default

    def keys(self) -> tuple:
        return ()

    def model_dump(self, *args: object, **kwargs: object) -> "_Reply":
        return self

    def __await__(self):
        if False:  # pragma: no cover - 让它成为生成器
            yield
        return self


# 社区配装分支要能走到「读账号库存」那一步，否则玩家名永远不经过服务层。
# 这里给两个方法返回真实的字典（假对象是 falsy 的，`if selected` 会直接跳过）。
_SPECIAL_REPLIES: dict[str, Any] = {
    "starside_svc.search_builds": {
        "results": [{"build_id": "示例配装"}],
        "matched_count": 1,
        "build_count": 1,
        "archive_available": True,
    },
    "starside_svc.get_build": {"title": "示例配装", "build_id": "示例配装"},
}


class _Recorder:
    """可无限下钻的可调用对象，把每次调用的参数记进 sink。"""

    def __init__(self, label: str, sink: list) -> None:
        self.label = label
        self._sink = sink

    def __getattr__(self, name: str) -> "_Recorder":
        if name.startswith("__"):
            raise AttributeError(name)
        return _Recorder(f"{self.label}.{name}", self._sink)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._sink.append((self.label, args, kwargs))
        return _SPECIAL_REPLIES.get(self.label, _Reply())


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
ASSISTANT_NAMES = sorted(name for name in TOOLS if name.endswith("_assistant"))


# 金装确认凭据走的是纯函数校验（HMAC），不经过服务层。把那个函数包一层记录下来，
# 否则 confirmed_exotic_hash / exotic_confirmation_token 读没读根本观察不到。
_CURRENT_CALLS: list[list] = []
_ORIGINAL_VERIFY = assistants_module.verify_exotic_confirmation_token


def _record_verify(token: Any, arguments: Any, **kwargs: Any) -> bool:
    for sink in _CURRENT_CALLS:
        sink.append(("exotic_confirmation.verify", (token, arguments), kwargs))
    return _ORIGINAL_VERIFY(token, arguments, **kwargs)


assistants_module.verify_exotic_confirmation_token = _record_verify


def _raw(function: Any) -> Any:
    """剥掉全部装饰器，用来观察「不做拦截时这个 intent 会做什么」。"""
    return inspect.unwrap(function)


def _base_type(annotation: object) -> object:
    """剥掉 Annotated 与 None，留下真正的类型。"""
    if hasattr(annotation, "__metadata__"):
        annotation = annotation.__origin__
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    return args[0] if args else annotation


def _int_value(spec: inspect.Parameter, offset: int) -> int:
    """整数探针要合法（有些字段带 ge/le）且尽量不同于默认值。"""
    low = high = None
    for meta in getattr(spec.annotation, "__metadata__", ()):
        low = getattr(meta, "ge", low)
        high = getattr(meta, "le", high)
    candidate = spec.default + offset if isinstance(spec.default, int) else offset
    if low is not None:
        candidate = max(candidate, int(low))
    if high is not None:
        candidate = min(candidate, int(high))
    if candidate == spec.default:
        candidate = int(low or 0) + offset
    return candidate


def _probe(function: Any, parameter: str) -> tuple[Any, Any]:
    """返回 (值, 另一个值)。两次调用必须用不同的值，才能看出参数有没有被读。"""
    override = PROBE_OVERRIDES.get((function.__name__, parameter))
    if override is not None:
        return override
    spec = inspect.signature(function, eval_str=True).parameters[parameter]
    base = _base_type(spec.annotation)
    if base is bool:
        if spec.default is None:
            # 哨兵默认值：True/False 两个真实布尔都算"传了"，才能看出有没有被读。
            return True, False
        return (not spec.default), spec.default
    if base is int:
        first = _int_value(spec, 7)
        second = _int_value(spec, 13)
        return (first, second) if first != second else (first, first + 1)
    if get_origin(base) is list:
        return [SENTINEL], [OTHER]
    if base is dict or get_origin(base) is dict:
        if parameter == "changes":
            return {"哨兵插槽": SENTINEL}, {"哨兵插槽": OTHER}
        return _canonical(CANONICAL_PROBE), _canonical(CANONICAL_OTHER)
    return SENTINEL, OTHER


def _fingerprint(value: Any) -> Any:
    """把调用参数压成可比较的形状：只保留值，不认识的对象按类型名占位。

    直接 repr 不行：假服务的对象带内存地址，两次调用永远不同，
    那样"换个值记录会变"就成了恒真命题。
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_fingerprint(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _fingerprint(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if dumped is not value:
            return _fingerprint(dumped)
    return f"<{type(value).__name__}>"


def _trace(calls: list) -> list:
    return [[label, _fingerprint(args), _fingerprint(kwargs)] for label, args, kwargs in calls]


def _arguments(tool: str, intent: str, parameter: str = "", **passed: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"intent": intent}
    if intent in WRITE_INTENTS:
        kwargs["confirmed"] = True
    kwargs.update(BASELINE.get((tool, intent), {}))
    kwargs.update(OVERRIDES.get((tool, intent, parameter), {}))
    kwargs.update(passed)
    return kwargs


async def _call(function: Any, tool: str, intent: str, parameter: str = "", **passed: Any):
    ctx = _Context()
    _CURRENT_CALLS.append(ctx.calls)
    try:
        result = function(**_arguments(tool, intent, parameter, **passed), ctx=ctx)
        if inspect.isawaitable(result):  # 工具是 async；剥掉装饰器后可能是 sync
            result = await result
    finally:
        _CURRENT_CALLS.pop()
    return result, ctx.calls


async def _guarded(tool: str, intent: str, parameter: str = "", **passed: Any):
    return await _call(TOOLS[tool], tool, intent, parameter, **passed)


async def _unguarded(tool: str, intent: str, parameter: str = "", **passed: Any):
    """绕过 @check_intent_parameters 直接调原函数（validate_request 也一并绕过，
    所以参数必须自身是合法的）。"""
    try:
        return await _call(_raw(TOOLS[tool]), tool, intent, parameter, **passed)
    except Exception:  # noqa: BLE001 - 真实服务错误不影响"有没有读到参数"的判断
        return None, []


@pytest.mark.parametrize("tool,parameter", sorted(contracts.PARAMETER_OWNERS))
async def test_non_owner_intents_reject_the_parameter(tool: str, parameter: str) -> None:
    """没认领这个参数的 intent 必须拒绝，而不是静默丢掉它。"""
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    value, _other = _probe(TOOLS[tool], parameter)

    silently_ignored = []
    for intent in sorted(contracts.TOOL_INTENTS[tool] - contract.intents):
        result, calls = await _guarded(tool, intent, parameter, **{parameter: value})
        code = result.get("error", {}).get("code")
        if code != "ignored_parameter":
            silently_ignored.append(
                f"{intent} -> {code or '接受了'}{'（已调用服务层）' if calls else ''}"
            )

    assert not silently_ignored, (
        f"{tool} 的 {parameter} 在这些 intent 上没有被拒绝：{silently_ignored}"
    )


@pytest.mark.parametrize("tool,parameter", sorted(contracts.PARAMETER_OWNERS))
async def test_owner_intents_really_read_the_parameter(tool: str, parameter: str) -> None:
    """认领了就必须真读：换一个值，服务层的调用记录不能一模一样。"""
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    value, other = _probe(TOOLS[tool], parameter)

    not_read = []
    for intent in sorted(contract.intents):
        result, calls = await _guarded(tool, intent, parameter, **{parameter: value})
        _other_result, other_calls = await _guarded(
            tool, intent, parameter, **{parameter: other}
        )
        if _trace(calls) == _trace(other_calls):
            code = result.get("error", {}).get("code", "无错误")
            not_read.append(f"{intent} -> {code}（换个值结果完全一样）")

    assert not not_read, f"{tool} 声明这些 intent 认 {parameter}，但实际没读到：{not_read}"


@pytest.mark.parametrize("tool,parameter", sorted(contracts.PARAMETER_OWNERS))
async def test_non_owner_intents_really_ignore_the_parameter(tool: str, parameter: str) -> None:
    """反方向：没认领的 intent 就算放它过去，换值也不该有任何影响。

    这一条防的是「表写得比代码窄」—— 把本来会被读取的参数判成没人认，
    于是拒绝掉一次合法调用。拦截本身会挡住观察，所以这里绕过拦截调原函数。
    """
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    value, other = _probe(TOOLS[tool], parameter)

    actually_read = []
    for intent in sorted(contracts.TOOL_INTENTS[tool] - contract.intents):
        _result, calls = await _unguarded(tool, intent, parameter, **{parameter: value})
        _other_result, other_calls = await _unguarded(
            tool, intent, parameter, **{parameter: other}
        )
        if _trace(calls) != _trace(other_calls):
            actually_read.append(intent)

    assert not actually_read, (
        f"{tool} 的 {parameter} 被判成没人认，但这些 intent 其实会读它，"
        f"拒绝它们就是误伤：{actually_read}"
    )


def test_owners_and_suggestions_name_real_intents() -> None:
    """表里写的 intent 和推荐的替代路径必须真实存在，否则拒绝等于把人指进死路。"""
    for (tool, parameter), contract in contracts.PARAMETER_OWNERS.items():
        assert tool in TOOLS, f"{tool} 不是已注册的工具"
        declared = contracts.TOOL_INTENTS[tool]
        assert contract.intents <= declared, (
            f"{tool} 的 {parameter} 声明了不存在的 intent："
            f"{sorted(contract.intents - declared)}"
        )
        suggestion = contract.suggestion
        if suggestion is None:  # 建议的改法在参数上，不在 intent 上
            continue
        suggestion_tool, suggestion_intent = suggestion
        assert suggestion_tool in TOOLS, f"{parameter} 推荐的 {suggestion_tool} 不存在"
        assert suggestion_intent in contracts.TOOL_INTENTS[suggestion_tool], (
            f"{parameter} 推荐的 {suggestion_tool}(intent={suggestion_intent}) 不存在"
        )


def test_table_covers_every_parameter_that_changes_a_result() -> None:
    """表要么登记一个参数，要么在豁免名单里写清为什么 —— 不能悄悄漏掉。

    只排除三个：confirmed（写入门槛，读 intent 收到它不影响结果）、
    community_build_id（代码里有更贴切的错误说明）、ctx（运行时注入）。
    """
    exempt = {"confirmed", "community_build_id", "ctx", "intent"}
    missing = []
    for tool in ASSISTANT_NAMES:
        for name in inspect.signature(TOOLS[tool]).parameters:
            if name not in exempt and (tool, name) not in contracts.PARAMETER_OWNERS:
                missing.append(f"{tool}.{name}")

    assert not missing, f"这些参数既没有归属也没有豁免理由：{missing}"


async def test_empty_values_are_not_treated_as_passed() -> None:
    """客户端把默认值一起发过来（item_name=""、confirmed=false）不算传了参数。"""
    result, _ = await _guarded(
        "inventory_assistant", "summary", item_name="", type_name="  ", location=""
    )

    assert result["ok"] is True, result.get("error")


async def test_unknown_intent_keeps_the_unsupported_diagnosis() -> None:
    """intent 本身就不存在时，原有的 unsupported_intent 说得更准，不要抢答。"""
    result, _ = await _guarded("inventory_assistant", "不存在的意图", item_name=SENTINEL)

    assert result["error"]["code"] == "unsupported_intent"


async def test_real_session_failures_are_now_rejected() -> None:
    """⑰⑱ 来自一次真实会话：模型传了参数，工具安静地忽略了它。

    - get + item_instance_id：想读某个副本，工具却返回了整包清单；
    - summary + item_name：问的是刚玉战锤，工具返回了背包概况。
    """
    result, calls = await _guarded("inventory_assistant", "get", item_instance_id=SENTINEL)
    assert result["error"]["code"] == "ignored_parameter"
    assert not calls, "拒绝要发生在调用服务层之前"
    assert "compare" in result["error"]["message"]

    result, _ = await _guarded("inventory_assistant", "summary", item_name="刚玉战锤")
    assert result["error"]["code"] == "ignored_parameter"
    assert 'intent="search"' in result["error"]["message"]


async def test_loadout_get_no_longer_swallows_loadout_id() -> None:
    """loadout 的 get 与 list 是同一个分支，只按 character 过滤。

    传 loadout_id 想取单套，以前会安静地返回全部配装 —— 模型很容易把第一条
    当成用户说的那套。现在会被拒绝，并说明要自己从结果里挑。
    """
    result, calls = await _guarded("loadout_assistant", "get", loadout_id="local:1")

    assert result["error"]["code"] == "ignored_parameter"
    assert not calls
    assert "全部配装" in result["error"]["message"]


def _description(annotation: object) -> str:
    for item in getattr(annotation, "__metadata__", ()):
        text = getattr(item, "description", None)
        if text:
            return str(text)
    return ""


def test_every_parameter_is_described_in_the_schema() -> None:
    """8 个 assistant 的每个参数都要有说明 —— 模型只看得见 schema。

    曾经只有 16 个"高危"参数有说明，剩下 67 个（包括 `intent`）只能靠名字猜，
    而且同一个 `intent` 在 weapon/build 有说明、在另外四个工具没有，属于直接的不一致。
    `ctx` 是运行时注入的，不进 schema，所以不在检查范围。
    """
    missing = []
    for tool in ASSISTANT_NAMES:
        parameters = inspect.signature(TOOLS[tool], eval_str=True).parameters
        for name, parameter in parameters.items():
            if name == "ctx":
                continue
            if not _description(parameter.annotation):
                missing.append(f"{tool}.{name}")

    assert not missing, f"这些参数在 schema 里没有说明：{missing}"


# ── 默认值哨兵规则 ────────────────────────────────────────────────────────────
#
# 每个"有含义的默认值"参数，它在函数体里补的默认值登记在这。表只用于测试取值，
# 改大了也不会让断言失真（非 None 的任何值都必须算"传了"）。

SENTINEL_BODY_DEFAULTS: dict[tuple[str, str], Any] = {
    ("inventory_assistant", "limit"): 10,
    ("inventory_assistant", "locked"): True,
    ("inventory_assistant", "tracked"): True,
    ("weapon_assistant", "limit"): 50,
    ("weapon_assistant", "include_inventory"): True,
    ("weapon_assistant", "community_section"): "text",
    ("build_assistant", "baseline"): "equipped",
    ("build_assistant", "max_replacements"): 2,
    ("build_assistant", "top_n"): 5,
    ("build_assistant", "include_inventory"): True,
    ("loadout_assistant", "slot_number"): 1,
    ("loadout_assistant", "kind"): "all",
    ("subclass_assistant", "limit"): 10,
    ("subclass_assistant", "community_section"): "text",
    ("activity_assistant", "maxtop"): 10,
    ("activity_assistant", "count"): 20,
    ("activity_assistant", "community_section"): "text",
    ("world_assistant", "limit"): 12,
    ("world_assistant", "community_section"): "text",
}


def test_assistant_signature_defaults_are_sentinels() -> None:
    """签名默认值只能是空值；有含义的默认值要走 None 哨兵 + 函数体补默认值。

    反例（都曾经真实存在）：`limit=10`、`locked=True`、`slot_number=1`、
    `kind="all"`、`top_n=5`、`baseline="equipped"`。
    """
    offenders = []
    for tool in sorted(contracts.TOOL_INTENTS):
        function = TOOLS[tool]
        for name, parameter in inspect.signature(function, eval_str=True).parameters.items():
            if name in {"ctx", "intent"} or parameter.default is inspect.Parameter.empty:
                # intent 本身有默认值（"summary"/"analyze"…），但每个 intent 都认领它，
                # 永远不可能"没人认"，不属于这条规则要管的范围。
                continue
            if parameter.default in (None, "", 0, False):
                continue
            offenders.append(f"{tool}.{name} 的默认值是 {parameter.default!r}")

    assert not offenders, (
        "这些参数的签名默认值是有含义的具体值，显式传它会被当成\"没传\"而静默吞掉。"
        "请把签名默认值改成 None，把默认值搬进函数体（并在 "
        "SENTINEL_BODY_DEFAULTS 里登记）：" + "；".join(offenders)
    )


@pytest.mark.parametrize(
    "tool,parameter,value",
    [(tool, parameter, value) for (tool, parameter), value in sorted(SENTINEL_BODY_DEFAULTS.items())],
)
async def test_explicit_body_default_counts_as_passed(
    tool: str, parameter: str, value: Any
) -> None:
    """显式传"函数体里的默认值"也必须算传了 —— 不认领它的 intent 必须拒绝。

    这一条就是 `limit=12` 那类静默黑洞的通用回归：修之前 12（=签名默认值）
    会被当成没传，于是在不读 limit 的 intent 上一路 ok=true。
    """
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    accepted = []
    for intent in sorted(contracts.TOOL_INTENTS[tool] - contract.intents):
        result, _calls = await _guarded(tool, intent, parameter, **{parameter: value})
        if result.get("error", {}).get("code") != "ignored_parameter":
            accepted.append(f"{intent} -> {result.get('error', {}).get('code') or '接受了'}")

    assert not accepted, (
        f"{tool} 的 {parameter} 显式传 {value!r}（函数体默认值）时，"
        f"这些不认领它的 intent 没有拒绝：{accepted}"
    )


@pytest.mark.parametrize(
    "tool,parameter,value",
    [(tool, parameter, value) for (tool, parameter), value in sorted(SENTINEL_BODY_DEFAULTS.items())],
)
async def test_owner_intent_treats_omitted_and_explicit_default_alike(
    tool: str, parameter: str, value: Any
) -> None:
    """认领它的 intent 上，"不传"和"显式传默认值"必须完全同效（默认值补得对不对）。"""
    contract = contracts.PARAMETER_OWNERS[(tool, parameter)]
    intent = sorted(contract.intents)[0]

    _omitted_result, omitted_calls = await _guarded(tool, intent, parameter)
    _explicit_result, explicit_calls = await _guarded(
        tool, intent, parameter, **{parameter: value}
    )

    assert _trace(explicit_calls) == _trace(omitted_calls), (
        f"{tool}({intent}) 不传 {parameter} 与显式传 {value!r} 的服务调用不一致：\n"
        f"不传={_trace(omitted_calls)}\n显式={_trace(explicit_calls)}"
    )
