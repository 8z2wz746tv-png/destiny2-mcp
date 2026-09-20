"""`player_assistant` 的模糊搜人分支（`intent="find"`）。

从 `assistants.py` 搬出来：那里的体量上限只降不升（`tests/test_module_size_ratchet.py`），
而"搜人"这一段自带错误映射、空候选话术、两条 warnings 与响应封装，本来就该按域放
`_*_branches.py`（与 `_counters_branches.py` / `_weapon_usage_branches.py` 同一分工）。

**这一支的口径要点**（改之前先读）：
- 默认**不读任何人的档案**（`include_profile=false`）：真机实测那 10 次档案读取串行要 21.8 秒，
  占整次调用的 96%，而它只用来排"置信度"；
- 上游模糊搜索失败**必须显式失败**，不能回空列表 —— 空列表会被读成"没这个人"；
- 空候选也只说明"这次没匹配到"，话术里要引导用完整 Bungie 名精确查。
"""

from __future__ import annotations

from typing import Any

from ..error_codes import ErrorCode
from ..exceptions import DestinyMCPError
from ._responses import error_response, ok_response


async def find_response(
    svc: dict[str, Any], name_prefix: str, include_profile: bool
) -> dict:
    """`intent="find"` 的响应（`include_profile` 决定要不要连候选档案一起读）。"""
    if not name_prefix:
        return error_response(ErrorCode.MISSING_NAME_PREFIX, "必须提供 name_prefix。")
    try:
        result = await svc["player_svc"].find_players(
            name_prefix, enrich=bool(include_profile)
        )
    except DestinyMCPError as exc:
        # 上游模糊搜索不可用时必须显式失败 + 给出下一步，**不能**回空列表 ——
        # 那会被读成「没这个人」（语料第二章：不能把没查到说成不存在）。
        return error_response(
            ErrorCode.API_ERROR,
            str(exc),
            next_actions=[{
                "label": "改用完整 Bungie 名（名字#1234）精确查找",
                "tool": "player_assistant",
                "arguments": {"intent": "search"},
            }],
        )

    players = result.get("players") or []
    has_more = bool(result.get("has_more"))
    if not players:
        return ok_response(
            f"模糊搜索没有返回候选（前缀：{name_prefix}）。",
            {"players": [], "page": result.get("page", 0), "has_more": has_more},
            warnings=[
                "空候选只说明这次没匹配到；若要确认某人是否存在，"
                "请用完整 Bungie 名（名字#1234）走 intent=\"search\"。"
            ],
        )

    warnings = []
    if not include_profile:
        # 默认不拉别人的档案：真机实测那 10 次档案读取（串行）要 21.8 秒，占整次调用 96%，
        # 而它只用来排"置信度"。要排序/分辨谁是谁时再开，那时是一次扇出并发（2.8–4.2 秒）。
        warnings.append(
            "候选默认只给名字、ID 与平台，按上游顺序排列（**没有**游玩时长/凯旋分）。"
            "要按这些排序就说一声（include_profile=true，约 3–5 秒）。"
        )
    if has_more:
        warnings.append(
            "上游还有下一页候选；这里只列了最高置信度的若干条。"
            "拿到完整名（名字#1234）后用 intent=\"search\" 精确定位。"
        )
    return ok_response(
        f"已模糊搜索玩家：找到 {len(players)} 个候选。",
        {"players": players, "page": result.get("page", 0), "has_more": has_more},
        warnings=warnings,
    )
