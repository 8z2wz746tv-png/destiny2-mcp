"""玩家显示名：**游戏内 ID 优先**（平台名会让人看到 Steam 名）。

社区反馈 + 真机实测（2026-09-17）：同一账号的游戏内 ID 是 `OneTop丶Husky#6641`，
而四个平台的 `displayName` 各不相同（`OneTop丶Husky` / `SecHusky` / `early_moccasin0` /
`此人以嫖到广东`）。旧代码写的是 `displayName or bungieGlobalDisplayName` —— 顺序反了。
"""

from __future__ import annotations

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
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "player_names.py" or "__pycache__" in path.as_posix():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if 'get("displayName"' in line or "get('displayName'" in line:
                offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert offenders == [], (
        "玩家名要走 utils/player_names.bungie_display_name（displayName 是平台名）：\n"
        + "\n".join(offenders)
    )
