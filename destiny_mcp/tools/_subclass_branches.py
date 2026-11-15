"""神器一族 intent 的分派：目录、模组、装模组、换神器。

搬出 `assistants.py` 的原因与 `_weapon_branches` 一样：那边只该做"认参数、选分支"，
而神器这四个 intent 各有自己的前置校验与话术（写入还要走确认信封），堆在主文件里
既撑体量、也让"换神器到底改了什么"没法一眼看清。

规矩：
- 数据形状来自 `services.artifact_service`；这里只管前置校验与信封；
- 写入一律走 `_responses.action_response`（失败 = `ok:false` + `write_failed(intent)`），
  调用方给错目标（名字不在身上）由服务层抛 `invalid_argument_error`；
- 读 `artifact` 带 `character` 时附带**他身上那件**：目录里的"当前神器"按赛季算，
  与角色实际装着的那件可以不同（实采：三角色分别装着 s26/s21/s25，目录报 s27）。
"""

from __future__ import annotations

from typing import Any

from ..error_codes import ErrorCode
from ..services.starside_notes import artifact_mod_notes, fragment_note
from ._responses import error_response, ok_response


async def artifact_branch(
    svc: dict[str, Any],
    intent: str,
    resolved: str,
    character: str,
    artifact_name: str,
    artifact_mod_hash: int,
    action_response: Any,
) -> dict[str, Any] | None:
    """处理 artifact / artifact_mod / equip_artifact_mod / equip_artifact；不是这四个返回 None。"""
    if intent == "artifact":
        result = svc["artifact_svc"].get_seasonal_artifact(artifact_name)
        if character:
            result = {
                **result,
                "character_artifact": await svc["artifact_svc"].artifact_state(
                    resolved, character
                ),
            }
        # 社区层：这件神器的模组注记（档位/效果/实机细节/冷却）。神器模组不是背包物品，
        # 要从我们 Manifest 的神器定义取列表再查归档；当季覆盖不全，coverage 一起给。
        starside = artifact_mod_notes(
            svc["starside_entities_svc"], svc["manifest"], svc["manifest"].get_current_artifact() or {}
        )
        return ok_response("已读取赛季神器。", {"artifact": result, "starside": starside})

    if intent == "artifact_mod":
        if not artifact_mod_hash:
            return error_response(
                ErrorCode.MISSING_ARTIFACT_MOD_HASH,
                "查询神器模组需要提供 artifact_mod_hash。",
            )
        result = svc["artifact_svc"].get_artifact_mod_info(artifact_mod_hash)
        return ok_response("已读取神器模组。", {"artifact_mod": result})

    if intent == "equip_artifact_mod":
        if not artifact_mod_hash:
            return error_response(
                ErrorCode.MISSING_ARTIFACT_MOD_HASH,
                "装备神器模组需要提供 artifact_mod_hash。",
            )
        result = await svc["artifact_svc"].equip_artifact_mod(
            resolved, artifact_mod_hash, character
        )
        return action_response(intent, "神器模组装备已执行。", result)

    if intent == "equip_artifact":
        if not artifact_name.strip():
            return error_response(
                ErrorCode.MISSING_ARTIFACT_NAME,
                "换神器要给出神器名字（artifact_name）。"
                '先用 intent="artifact" 带上 character 看他现在有哪几件。',
            )
        result = await svc["artifact_svc"].switch_artifact(
            resolved, character, artifact_name
        )
        return action_response(intent, "换神器已执行。", result)

    return None


def fragment_details_payload(svc: dict[str, Any], fragment_name: str, community: dict[str, Any]) -> dict[str, Any]:
    """`subclass_assistant(intent="fragment_details")`：碎片说明 + 页面正文检索 + 社区结构化注记。"""
    return ok_response(
        "已读取碎片详情。",
        {
            "fragment": svc["fragment_svc"].get_fragment_details(fragment_name),
            "community_references": community,
            "starside": fragment_note(svc["starside_entities_svc"], svc["manifest"], fragment_name),
        },
    )
