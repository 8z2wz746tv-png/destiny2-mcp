"""`error.code` 的唯一出处。

信封里的 `error.code` 有四类，全部从这里出：

1. **字面量码**：`ErrorCode` —— 工具层自己判定的失败（缺参、越界、需要确认…）。
2. **异常派生码**：`code_for_exception()` —— `InvalidArgumentError` → `invalid_argument_error`。
   服务层抛异常时由 `tools/_helpers.handle_tool_error` 统一转码，别在别处再写一遍正则。
   注意 `APIError` 派生出的是 `a_p_i_error`（按大写边界逐字插下划线，`A_P_I_Error`），
   这个看着别扭的码是既有契约，**不能改**。
3. **写入失败族**：`write_failed(intent)` —— `{intent}_failed`（`move_failed`、`equip_failed`…）。
4. **候选拒绝码**：服务层判定完直接 `return {"success": False, "code": …}`，工具层原样带出去
   （`_armor_branches.equip_build` 的 `failure_response(result["code"])`）。这一族同样是**对外契约**：
   调用方按值分支、语料按值断言，所以值一个字都不能改。

为什么要收在一处：以前 43 处直接写字符串字面量，打错一个字母不会报错，只会悄悄多出一个
新码——调用方按码判断就失灵，而且谁也说不清一共有多少种码。`tests/test_error_codes.py`
会扫源码禁止裸字符串（**含结构化 payload 里的 `"code"` 键**：第 4 类不走 `error_response()`，
扫函数名那一版抓不到它），并钉住"改名不能悄悄改码"。

第 4 类为什么也算：`stale_inventory_snapshot` 曾经只活在 `build_execution_guard` 的字典里，
枚举里查不到 —— 一个会出现在响应里的码，在"唯一出处"里却不存在，那这份唯一出处就是假的。
"""

from __future__ import annotations

import re
from enum import StrEnum


class ErrorCode(StrEnum):
    """字面量错误码。**值就是发给调用方的字符串，改名可以，改值不行。**"""

    # 候选拒绝码一族（模块 docstring 第 4 类，2026-10-03 从服务层收进来）按字母序散在下面，
    # 它们没有单独的前缀可认 —— 要认全看 docstring 与 `tests/test_error_codes.py` 的清单。

    API_ERROR = "a_p_i_error"
    AUTH_REQUIRED = "auth_required"
    BUILD_RECOMMENDATION_FAILED = "build_recommendation_failed"
    CANONICAL_BUILD_MISMATCH = "canonical_build_mismatch"
    CHARACTER_MISMATCH = "character_mismatch"
    COMMUNITY_TEMPLATE_NOT_EXECUTABLE = "community_template_not_executable"
    CONFIRMATION_REQUIRED = "confirmation_required"
    EXACT_BUILD_REQUIRED = "exact_build_required"
    EXACT_ITEM_MISSING = "exact_item_missing"
    EXECUTION_PRECONDITION_FAILED = "execution_precondition_failed"
    EXOTIC_CONFIRMATION_REQUIRED = "exotic_confirmation_required"
    EXOTIC_NOT_FOUND = "exotic_not_found"
    EXOTIC_RESOLUTION_FAILED = "exotic_resolution_failed"
    EXPIRED_EXECUTION_ID = "expired_execution_id"
    IGNORED_PARAMETER = "ignored_parameter"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_BASELINE = "invalid_baseline"
    INVALID_CANONICAL_BUILD = "invalid_canonical_build"
    INVALID_EXACT_ITEMS = "invalid_exact_items"
    INVALID_EXOTIC_CONFIRMATION = "invalid_exotic_confirmation"
    INVALID_ITEM_COUNT = "invalid_item_count"
    INVALID_MAX_REPLACEMENTS = "invalid_max_replacements"
    INVALID_MOD_HASH = "invalid_mod_hash"
    INVENTORY_SUMMARY_FAILED = "inventory_summary_failed"
    # 「同名多件，先选一件」：写入没发生，也不该报成 `xxx_failed`（真机 2026-09-24 之前是
    # `move_failed`，而语义是"需要你选"；报成失败会让调用方以为出错、甚至重试同一个调用）。
    ITEM_DISAMBIGUATION_REQUIRED = "item_disambiguation_required"
    MISSING_ARTIFACT_MOD_HASH = "missing_artifact_mod_hash"
    MISSING_ARTIFACT_NAME = "missing_artifact_name"
    EQUIP_BLOCKED = "equip_blocked"
    MISSING_EXECUTION_ID = "missing_execution_id"
    MISSING_ITEM_INSTANCE_IDS = "missing_item_instance_ids"
    MISSING_NAME_PREFIX = "missing_name_prefix"
    MISSING_PLAYER_NAME = "missing_player_name"
    MISSING_SNAPSHOT_VERSION = "missing_snapshot_version"
    MISSING_WEAPON_NAME = "missing_weapon_name"
    POPULARITY_LOOKUP_FAILED = "popularity_lookup_failed"
    STALE_INVENTORY_SNAPSHOT = "stale_inventory_snapshot"
    UNKNOWN_EXECUTION_ID = "unknown_execution_id"
    # 「这次候选**已经用过了**」——与 expired / unknown 是三件不同的事（2026-10-06 真机）：
    # 一次被**执行前提拦下**的执行过去会把候选一起烧掉，调用方拿到的却是 unknown
    # （"已失效或不属于当前玩家"），于是去重解而不是重试。现在焚烧推迟到写成功之后。
    USED_EXECUTION_ID = "used_execution_id"
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
