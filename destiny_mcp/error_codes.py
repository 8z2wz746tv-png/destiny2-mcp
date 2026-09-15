"""`error.code` 的唯一出处。

信封里的 `error.code` 有三类，全部从这里出：

1. **字面量码**：`ErrorCode` —— 工具层自己判定的失败（缺参、越界、需要确认…）。
2. **异常派生码**：`code_for_exception()` —— `InvalidArgumentError` → `invalid_argument_error`。
   服务层抛异常时由 `tools/_helpers.handle_tool_error` 统一转码，别在别处再写一遍正则。
   注意 `APIError` 派生出的是 `a_p_i_error`（按大写边界逐字插下划线，`A_P_I_Error`），
   这个看着别扭的码是既有契约，**不能改**。
3. **写入失败族**：`write_failed(intent)` —— `{intent}_failed`（`move_failed`、`equip_failed`…）。

为什么要收在一处：以前 43 处直接写字符串字面量，打错一个字母不会报错，只会悄悄多出一个
新码——调用方按码判断就失灵，而且谁也说不清一共有多少种码。`tests/test_error_codes.py`
会扫源码禁止裸字符串，并钉住"改名不能悄悄改码"。
"""

from __future__ import annotations

import re
from enum import StrEnum


class ErrorCode(StrEnum):
    """字面量错误码。**值就是发给调用方的字符串，改名可以，改值不行。**"""

    API_ERROR = "a_p_i_error"
    AUTH_REQUIRED = "auth_required"
    BUILD_RECOMMENDATION_FAILED = "build_recommendation_failed"
    COMMUNITY_TEMPLATE_NOT_EXECUTABLE = "community_template_not_executable"
    CONFIRMATION_REQUIRED = "confirmation_required"
    EXACT_BUILD_REQUIRED = "exact_build_required"
    EXOTIC_CONFIRMATION_REQUIRED = "exotic_confirmation_required"
    EXOTIC_NOT_FOUND = "exotic_not_found"
    EXOTIC_RESOLUTION_FAILED = "exotic_resolution_failed"
    IGNORED_PARAMETER = "ignored_parameter"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_BASELINE = "invalid_baseline"
    INVALID_CANONICAL_BUILD = "invalid_canonical_build"
    INVALID_EXOTIC_CONFIRMATION = "invalid_exotic_confirmation"
    INVALID_MAX_REPLACEMENTS = "invalid_max_replacements"
    INVENTORY_SUMMARY_FAILED = "inventory_summary_failed"
    MISSING_ARTIFACT_MOD_HASH = "missing_artifact_mod_hash"
    MISSING_ITEM_INSTANCE_IDS = "missing_item_instance_ids"
    MISSING_NAME_PREFIX = "missing_name_prefix"
    MISSING_PLAYER_NAME = "missing_player_name"
    MISSING_WEAPON_NAME = "missing_weapon_name"
    POPULARITY_LOOKUP_FAILED = "popularity_lookup_failed"
    UNSUPPORTED_INTENT = "unsupported_intent"
    WEAPON_ANALYSIS_FAILED = "weapon_analysis_failed"
    WEAPON_CATALOG_LOOKUP_FAILED = "weapon_catalog_lookup_failed"
    WEEKLY_RESET_UNAVAILABLE = "weekly_reset_unavailable"
    WEEKLY_SUMMARY_FAILED = "weekly_summary_failed"


# 大写边界 → 下划线（`APIError` → `A_P_I_Error`，与既有契约一致）
_EXCEPTION_CODE_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def code_for_exception(exc: BaseException) -> str:
    """异常类名 → snake_case 码。类名即契约：改名等于改码，测试会拦。"""
    return _EXCEPTION_CODE_BOUNDARY.sub("_", type(exc).__name__).lower()


WRITE_FAILED_SUFFIX = "_failed"


def write_failed(intent: str) -> str:
    """写入类 intent 的失败码：`{intent}_failed`。"""
    return f"{intent}{WRITE_FAILED_SUFFIX}"
