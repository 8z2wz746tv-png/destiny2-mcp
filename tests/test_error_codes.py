"""错误码的单一出处：`error.code` 只能来自 `destiny_mcp/error_codes.py`。

以前 43 处直接写字符串字面量：打错一个字母不会报错，只会悄悄多出一个新码，
调用方按码判断就失灵，而且谁也说不清一共有多少种码。这个测试守三件事：

1. **不许再出现裸字符串**：`error_response("...")` 的第一参必须是
   `ErrorCode.<成员>`，或者 `code_for_exception()` / `write_failed()` 派生；
2. **改类名等于改码**：异常类名是契约的一部分（`APIError` → `a_p_i_error`），
   重命名类会让线上码悄悄变掉，这里用一份手写清单钉住；
3. **枚举本身**：值唯一、snake_case、27 个字面量码一个不少。
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
    "community_template_not_executable",
    "confirmation_required",
    "exact_build_required",
    "exotic_confirmation_required",
    "exotic_not_found",
    "exotic_resolution_failed",
    "ignored_parameter",
    "invalid_arguments",
    "invalid_baseline",
    "invalid_canonical_build",
    "invalid_exotic_confirmation",
    "invalid_max_replacements",
    "inventory_summary_failed",
    "item_disambiguation_required",
    "missing_artifact_mod_hash",
    "missing_artifact_name",
    "equip_blocked",
    "missing_item_instance_ids",
    "missing_name_prefix",
    "missing_player_name",
    "missing_weapon_name",
    "popularity_lookup_failed",
    "unsupported_intent",
    "weapon_analysis_failed",
    "weapon_catalog_lookup_failed",
    "weekly_reset_unavailable",
    "weekly_summary_failed",
}


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def test_literal_codes_are_complete_and_snake_case() -> None:
    values = [member.value for member in ErrorCode]

    assert set(values) == _EXPECTED_LITERAL_CODES, (
        "字面量码清单变了：新增/删除码要同时更新这里与 docs/testing/TESTING_CORPUS.md 的码表"
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
