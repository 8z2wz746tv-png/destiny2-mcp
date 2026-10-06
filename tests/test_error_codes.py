"""错误码的单一出处：`error.code` 只能来自 `destiny_mcp/error_codes.py`。

以前 43 处直接写字符串字面量：打错一个字母不会报错，只会悄悄多出一个新码，
调用方按码判断就失灵，而且谁也说不清一共有多少种码。这个测试守四件事：

1. **不许再出现裸字符串**：`error_response("...")` 的第一参必须是
   `ErrorCode.<成员>`，或者 `code_for_exception()` / `write_failed()` 派生；
2. **结构化 payload 里的裸 `"code"` 同样不许**：服务层的候选拒绝码不走 `error_response()`，
   而是 `return {"success": False, "code": "…"}`（工具层原样带出去当 `error.code`）——
   第 1 条按函数名扫的判据看不见它们，于是 `stale_inventory_snapshot` 曾经出现在响应里、
   枚举里却没有这个成员；
3. **改类名等于改码**：异常类名是契约的一部分（`APIError` → `a_p_i_error`），
   重命名类会让线上码悄悄变掉，这里用一份手写清单钉住；
4. **枚举本身**：值唯一、snake_case、字面量码一个不少（清单即当前契约）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from destiny_mcp import exceptions
from destiny_mcp.error_codes import ErrorCode, code_for_exception, write_failed

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "destiny_mcp"

# 手写清单：这些码是对外契约（语料、skill 文档、调用方都在按它判断），改类名就会变。
_EXPECTED_EXCEPTION_CODES = {
    "ConfigError": "config_error",
    "PlayerNotFoundError": "player_not_found_error",
    "CharacterNotFoundError": "character_not_found_error",
    "ItemNotFoundError": "item_not_found_error",
    "DefinitionNotFoundError": "definition_not_found_error",
    "TransferError": "transfer_error",
    "ManifestError": "manifest_error",
    "InvalidArgumentError": "invalid_argument_error",
    "BuildValidationError": "build_validation_error",
    "WeaponPopularityDataError": "weapon_popularity_data_error",
    "AuthenticationError": "authentication_error",
    "SubclassError": "subclass_error",
    # 这个怪码是既有契约：按大写边界逐字插下划线，A_P_I_Error → a_p_i_error
    "APIError": "a_p_i_error",
    "UpstreamNotFoundError": "upstream_not_found_error",
    "BungieServiceUnavailableError": "bungie_service_unavailable_error",
    "BuildTooLargeError": "build_too_large_error",
}

_EXPECTED_LITERAL_CODES = {
    "a_p_i_error",
    "auth_required",
    "build_recommendation_failed",
    "canonical_build_mismatch",
    "character_mismatch",
    "community_template_not_executable",
    "confirmation_required",
    "exact_build_required",
    "exact_item_missing",
    "execution_precondition_failed",
    "exotic_confirmation_required",
    "exotic_not_found",
    "exotic_resolution_failed",
    "expired_execution_id",
    "ignored_parameter",
    "invalid_arguments",
    "invalid_baseline",
    "invalid_canonical_build",
    "invalid_exact_items",
    "invalid_exotic_confirmation",
    "invalid_item_count",
    "invalid_max_replacements",
    "invalid_mod_hash",
    "inventory_summary_failed",
    "item_disambiguation_required",
    "missing_artifact_mod_hash",
    "missing_artifact_name",
    "equip_blocked",
    "missing_execution_id",
    "missing_item_instance_ids",
    "missing_name_prefix",
    "missing_player_name",
    "missing_snapshot_version",
    "missing_weapon_name",
    "popularity_lookup_failed",
    "stale_inventory_snapshot",
    "unknown_execution_id",
    "unsupported_intent",
    "used_execution_id",
    "weapon_analysis_failed",
    "weapon_catalog_lookup_failed",
    "weekly_reset_unavailable",
    "weekly_summary_failed",
}

#: 结构化 payload 里 `code` 键**不是** `error.code` 的例外（文件 → 这是哪个域）。
#: 现在为空：现存两处非错误码的 `code`（`tools/_responses` 的信封、`oauth_setup` 的 OAuth
#: 授权码）都是**变量透传**，落不进"字符串字面量"这条判据，本来就不需要豁免。
#: 留这张表是为了下次真出现另一个域的 `code` 字面量时，有一个**必须写清是哪个域**的入口 ——
#: 而不是把判据放宽成"某些文件不扫"，那等于给这一族码开后门。
_PAYLOAD_CODE_OTHER_DOMAINS: dict[str, str] = {}


def _payload_code_values(node: ast.AST) -> list[ast.expr]:
    """取出节点里"键是 `code`"的值：两种写法都算结构化 payload。

    - `dict` 字面量 `{"code": …}`（服务层现在的写法）；
    - 关键字实参 `f(code=…)`（同样的载荷换个写法，不该因为写法不同就漏掉）。

    `**kwargs` 展开的键是 `None`，不是 `code`，天然不进来。
    """
    if isinstance(node, ast.Dict):
        return [
            value
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == "code"
        ]
    if isinstance(node, ast.Call):
        return [kw.value for kw in node.keywords if kw.arg == "code"]
    return []


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def test_literal_codes_are_complete_and_snake_case() -> None:
    values = [member.value for member in ErrorCode]

    assert set(values) == _EXPECTED_LITERAL_CODES, (
        "字面量码清单变了：新增/删除码要同时更新这里，以及 `docs/testing/TESTING_CORPUS.md` 里"
        "按码断言的那几行（那边没有集中的码表，是按场景引用码值的）"
    )
    assert len(set(values)) == len(values), "错误码值必须唯一"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in values)


def test_exception_names_still_map_to_the_same_codes() -> None:
    """类名即契约：重命名异常类会悄悄改掉线上码，这里逐个钉住。"""
    actual = {}
    for name, klass in vars(exceptions).items():
        if (
            isinstance(klass, type)
            and issubclass(klass, exceptions.DestinyMCPError)
            and klass is not exceptions.DestinyMCPError
        ):
            # 只看类型名，不跑 __init__（各异常的构造签名不同）
            actual[name] = code_for_exception(klass.__new__(klass))

    assert set(actual) == set(_EXPECTED_EXCEPTION_CODES), (
        "异常类清单变了（新增/改名）：请同步这份契约清单，并确认调用方与文档"
    )
    for name, expected in _EXPECTED_EXCEPTION_CODES.items():
        assert actual[name] == expected, (
            f"{name} 的码从 {expected} 变成了 {actual[name]}："
            "类名是对外契约，改名前先确认调用方与文档"
        )


def test_error_response_is_never_called_with_a_bare_string() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "error_response" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                offenders.append(f"{path.relative_to(SOURCE_ROOT.parent)}:{node.lineno}")

    assert not offenders, (
        "这些地方还在直接写错误码字符串，请改用 error_codes.ErrorCode.<成员>：\n  "
        + "\n  ".join(offenders)
    )


def test_structured_payloads_do_not_carry_bare_code_literals() -> None:
    """`{"code": "…"}` 里的码也是 `error.code`，只认枚举与派生（不看函数名）。

    这一条抓的是一整族**审计漏掉的码**：服务层的候选拒绝码不经过 `error_response()`，
    而是 `return {"success": False, "code": "…"}`，工具层再原样带出去当 `error.code`
    （`_armor_branches.equip_build` 的 `failure_response(result["code"])`）——
    上面那条按函数名扫的判据全看不见。于是 `stale_inventory_snapshot` 出现在响应里、
    文档与测试都在按它断言，而 `ErrorCode` 里根本没有这个成员。

    误报控制：只抓**字符串字面量**。`"code": code`（OAuth 授权码透传）、
    `"code": ErrorCode.X`、`candidate.get("code")` 都不是字面量，本来就不该拦 ——
    这条判据问的是"码值有没有第二个出处"，不是"`code` 这个键能不能出现"。
    真出现另一个域的 `code` 字面量（不是 error.code），登记进
    `_PAYLOAD_CODE_OTHER_DOMAINS` 并写清是哪个域，不要放宽判据。
    """
    for relative, reason in _PAYLOAD_CODE_OTHER_DOMAINS.items():
        assert (SOURCE_ROOT.parent / relative).is_file(), f"豁免表里的文件不存在：{relative}"
        assert reason.strip(), f"{relative} 的豁免没写理由：要说清这个 code 属于哪个域"

    offenders: list[str] = []
    for path in _python_files():
        relative = path.relative_to(SOURCE_ROOT.parent).as_posix()
        if relative in _PAYLOAD_CODE_OTHER_DOMAINS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for value in _payload_code_values(node):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    offenders.append(f"{relative}:{value.lineno}")

    assert not offenders, (
        "这些结构化 payload 还在直接写错误码字符串，请改用 error_codes.ErrorCode.<成员>"
        "（它不走 error_response()，工具层照样会把它当 error.code 发出去）：\n  "
        + "\n  ".join(offenders)
    )


def test_exception_code_derivation_lives_in_one_place() -> None:
    """异常类名 → 码 的推导只能在 error_codes.py 里做。"""
    offenders = []
    for path in _python_files():
        if path.name == "error_codes.py":
            continue
        text = path.read_text(encoding="utf-8")
        # 那个"大写边界插下划线"的正则，以及手写的 type(...).__name__ + lower()
        if '"(?<!^)(?=[A-Z])"' in text or re.search(
            r"type\(\w+\)\.__name__[^\n]*\.lower\(\)", text
        ):
            offenders.append(str(path.relative_to(SOURCE_ROOT.parent)))

    assert not offenders, (
        "异常类名 → 码 的推导散到了别处，请统一用 error_codes.code_for_exception：\n  "
        + "\n  ".join(offenders)
    )


def test_write_failed_family_is_generated_not_listed() -> None:
    """写入失败码按 intent 生成（`move_failed`…），不逐个进枚举。"""
    assert write_failed("move") == "move_failed"
    assert write_failed("build_equip") == "build_equip_failed"

    declared = {member.value for member in ErrorCode}
    for intent in ("move", "transfer", "equip", "equip_many", "equip_mod", "save", "delete"):
        assert write_failed(intent) not in declared, (
            f"{write_failed(intent)} 不该写进枚举：它是按 intent 派生的"
        )
