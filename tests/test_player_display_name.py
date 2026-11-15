"""玩家显示名：**游戏内 ID 优先**（平台名会让人看到 Steam 名）。

社区反馈 + 真机实测（2026-09-17）：同一账号的游戏内 ID 是 `OneTop丶Husky#6641`，
而四个平台的 `displayName` 各不相同（`OneTop丶Husky` / `SecHusky` / `early_moccasin0` /
`此人以嫖到广东`）。旧代码写的是 `displayName or bungieGlobalDisplayName` —— 顺序反了。
"""

from __future__ import annotations

import re
from pathlib import Path

from destiny_mcp.utils.player_names import bungie_display_name, bungie_display_name_of_player


def test_prefers_the_in_game_id_over_the_platform_name() -> None:
    node = {
        "displayName": "SecHusky",                      # Xbox persona
        "bungieGlobalDisplayName": "OneTop丶Husky",      # 游戏内 ID
        "bungieGlobalDisplayNameCode": 6641,
    }
    assert bungie_display_name(node) == "OneTop丶Husky#6641"


def test_falls_back_to_the_platform_name_only_when_there_is_no_in_game_id() -> None:
    assert bungie_display_name({"displayName": "SecHusky"}) == "SecHusky"
    assert bungie_display_name({}) == ""
    assert bungie_display_name(None) == ""


def test_in_game_id_without_a_code_has_no_hash() -> None:
    assert bungie_display_name({"bungieGlobalDisplayName": "OneTop丶Husky"}) == "OneTop丶Husky"
    assert (
        bungie_display_name({"bungieGlobalDisplayName": "OneTop丶Husky",
                             "bungieGlobalDisplayNameCode": 0})
        == "OneTop丶Husky"
    )


def test_pgcr_style_player_node_is_unwrapped() -> None:
    pgcr_player = {
        "characterClass": "Warlock",
        "destinyUserInfo": {
            "displayName": "此人以嫖到广东",
            "bungieGlobalDisplayName": "OneTop丶Husky",
            "bungieGlobalDisplayNameCode": 6641,
        },
    }
    assert bungie_display_name_of_player(pgcr_player) == "OneTop丶Husky#6641"


def test_no_module_builds_player_names_from_the_platform_field() -> None:
    """禁止再直接读 `displayName` 拼玩家名 —— 那就是平台名（Steam/Xbox/PSN/Epic）。"""
    root = Path(__file__).resolve().parents[1] / "destiny_mcp"
    # 三种形态都要拦（真机上三处都踩过）：
    #   get("displayName") / ["displayName"]  → 平台名当游戏内 ID；
    #   displayNameCode                       → **这个字段不存在**（真名 bungieGlobalDisplayNameCode），
    #                                           拼出来就是 `名字#` 那种尾随空 #。
    platform_reads = ('get("displayName"', "get('displayName'", '["displayName"]', "['displayName']")
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "player_names.py" or "__pycache__" in path.as_posix():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # `displayNameCode` 只在**拼名字**的上下文里算违规：Bungie 的搜索**请求体**
            # 入参就叫 `displayName`/`displayNameCode`（那个字段名是上游定的，不是我们的错）。
            suspicious = any(marker in line for marker in platform_reads) or (
                "displayNameCode" in line and "#" in line
            )
            if suspicious:
                offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert offenders == [], (
        "玩家名要走 utils/player_names.bungie_display_name（displayName 是平台名）：\n"
        + "\n".join(offenders)
    )


def test_leaderboard_preview_shows_the_in_game_id() -> None:
    """排行榜那条路径（`_leaderboard_preview`）也必须用游戏内 ID。

    真机实测（2026-09-18）：账号级排行榜上游返回空，没法用真机钉住这条；
    它是纯函数，这里用上游形状的假数据把口径固定下来。
    """
    from destiny_mcp.services.activity_service import ActivityService

    response = {
        "focusMembershipId": "4611686018492803873",
        "allPvP": {
            "allTime": {
                "statId": "allTime",
                "entries": [
                    {
                        "rank": 1,
                        "characterId": "2305843009",
                        "value": {"basic": {"value": 78864, "displayValue": "78864"}},
                        "player": {
                            "destinyUserInfo": {
                                "displayName": "SecHusky",              # Xbox 平台名
                                "bungieGlobalDisplayName": "OneTop丶Husky",
                                "bungieGlobalDisplayNameCode": 6641,
                            }
                        },
                    }
                ],
            }
        },
    }

    preview = ActivityService._leaderboard_preview(response, max_entries=5)
    assert preview[0]["entries"][0]["player"] == "OneTop丶Husky#6641"


def test_search_result_with_empty_code_has_no_trailing_hash() -> None:
    """搜索路径的真实形状：Bungie 对部分账号把 code 返回成空字符串。

    真机复现（2026-09-18）：`player_assistant(intent="search")` 四个候选全显示成
    `OneTop丶Husky#` / `SecHusky#` —— 因为 `player_service._identity_of` 自己拼
    `f"{name}#{code}"`，只挡了 `None`，没挡 `""`。
    """
    from destiny_mcp.services.player_service import _identity_of

    mid, mtype, name = _identity_of({
        "bungieGlobalDisplayName": "OneTop丶Husky",
        "bungieGlobalDisplayNameCode": "",          # ← 真机就是空串
        "destinyMemberships": [
            {"membershipId": "4611686018492803873", "membershipType": 3, "crossSaveOverride": 3}
        ],
    })
    assert name == "OneTop丶Husky", f"空 code 不该拼出尾随 #：{name!r}"
    assert mid == "4611686018492803873" and mtype == 3

    _, _, with_code = _identity_of({
        "bungieGlobalDisplayName": "OneTop丶Husky",
        "bungieGlobalDisplayNameCode": 6641,
        "destinyMemberships": [],
    })
    assert with_code == "OneTop丶Husky#6641"


def test_no_module_builds_player_names_by_hand() -> None:
    """禁止自己拼 `名字#code` —— 唯一出处是 `utils/player_names`。

    搜索那条路就是这么漏过去的（旧扫描只禁了 `get("displayName"`，管不到"自己拼 #"）。
    只盯「把某个 code 变量拼进 f-string」这一种形态，两点讲究：
    - **跳过注释行**：注释里引用旧写法是证据（本仓库就留着一条），不是违规；
    - **不盯 `"#" +`**：`starside_service` 用它拼 URL 锚点，跟玩家名无关 —— 宁可少抓，
      也不要为了"更严"去误伤，那样只会逼出 `noqa`。
    """
    root = Path(__file__).resolve().parents[1] / "destiny_mcp"
    code_in_fstring = re.compile(r"""f["'][^"']*#\{[a-z_]*code""", re.IGNORECASE)
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "player_names.py" or "__pycache__" in path.as_posix():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if code_in_fstring.search(line):
                offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert offenders == [], (
        "玩家名要走 utils/player_names.bungie_display_name，别自己拼 `名字#code`：\n"
        + "\n".join(offenders)
    )
