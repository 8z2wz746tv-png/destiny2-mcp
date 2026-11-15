"""别名不能"偷偷跑偏"：`docs/COMPATIBILITY.md` 里登记的每一组别名，必须真的走同一段分派。

风险很具体：有人在 `assistants.py` 里给某个别名加了单独分支，于是"同一个意思"的两条路
返回不同结果——`move` 和 `transfer` 就是历史上踩过的坑（名字像，参数不一样）。
这个测试做三件事：

1. 每个 Literal 取值都必须登记（canonical 或某个 canonical 的别名）——新增 intent 时强制做决定；
2. 别名必须真的存在对应的 Literal 里，不许登记一个不存在的名字；
3. 每组别名必须出现在 `assistants.py` 的同一处分派（同一个集合字面量或同一条比较），
   或者落在同一个模块级集合常量里。

**这个文件的边界**：它能抓"某个别名只走了单独分支"，抓不到"两边都进同一段分派、
但中途按名字分叉"——那种要靠行为等价来验：真机跑
`scripts/run_corpus_all_rows.py`（`aliases` 行：同参调用同组别名，比对 `data` 是否逐字节相同）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from destiny_mcp.tools import _requests as R

SOURCE_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = SOURCE_ROOT / "destiny_mcp" / "tools"
# 分派处有两类：工具门面本身，以及按域拆出去的 `_*_branches.py`（仓库既定分工，
# 例如 `_weapon_usage_branches.py` 里 `weapon_history` 与 `pvp_weapons` 两个口径）。
_DISPATCH_FILES = [_TOOLS / "assistants.py", *sorted(_TOOLS.glob("_*_branches.py"))]

# canonical → 别名（与 docs/COMPATIBILITY.md 的表一一对应）
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "PlayerIntent": {
        "profile": ("get_profile", "角色", "档案"),
        "search": ("search_player",),
        "find": ("find_players", "fuzzy"),
    },
    "InventoryIntent": {
        "summary": ("summarize", "概况"),
        "duplicates": ("duplicate_weapons", "find_duplicates", "重复武器"),
        "get": ("inventory", "list"),
        "search": ("find_item",),
        "type": ("search_type",),
        "equip_many": ("equip_items",),
        "track_quest": ("quest_tracking",),
    },
    "WeaponIntent": {
        "catalog": ("search_catalog", "all_weapons", "global", "search_all"),
        "compare": ("compare_duplicates",),
        "perk_pool": ("perks",),
        "popularity": ("selection_rates", "perk_selection", "selection", "usage_rates"),
        # 锻造武器模式：玩家说「红框」「锻造武器」（永久别名），游戏官方中文叫「模式」，
        # 第三方工具常译作「图样」；英文近义 pattern/craft 只登记不宣传
        "patterns": (
            "pattern", "craft", "锻造", "锻造武器", "图样", "图样进度", "模式进度", "红框", "红框进度",
        ),
    },
    # `list` 与 `get` 在 0.7.6 之后是**两件事**（list 给清单行、get 给完整模板并可按 id 取一套），
    # 所以 `get` 不再是 `list` 的别名 —— 留在别名表里，语料的"同参同 data"那行会一直红。
    "LoadoutIntent": {},
    "SubclassIntent": {"get": ("subclass",)},
    "ActivityIntent": {
        "stats": ("career", "historical_stats"),
        "weapon_history": ("weapons", "weapon_usage", "weapon_leaderboard"),
        "aggregate": ("activity_aggregate", "activity_stats"),
        "leaderboards": ("leaderboard",),
    },
    "BuildIntent": {"community": ("starside",)},
    "WorldIntent": {
        # 周常轮换：中文说法（永久别名）
        "rotations": ("轮换", "周常轮换", "这周"),
    },
}

# 独立行为：自己有单独一段分派、不是任何东西的别名。新增 intent 必须落进
# ALIASES（canonical 或别名）或这里，两者都不是就让测试红掉——逼着做决定。
STANDALONE: dict[str, tuple[str, ...]] = {
    "PlayerIntent": (),
    "InventoryIntent": ("item", "move", "transfer", "equip", "equip_mod", "pull_postmaster", "lock"),
    "WeaponIntent": (
        "analyze", "filter_rolls", "god_roll", "type", "info", "stats",
        "perk_description", "catalyst", "community",
    ),
    "LoadoutIntent": (
        "list", "get",
        "save", "delete", "equip_loadout", "search_identifiers",
        "snapshot_official", "update_official_identifiers", "clear_official",
    ),
    "SubclassIntent": (
        "modify", "options", "fragments", "fragment_details", "artifact",
        "artifact_mod", "equip_artifact_mod", "equip_artifact", "community",
    ),
    "ActivityIntent": (
        "history", "pgcr", "counters", "clan_leaderboards", "community",
        # 独立口径：PvP 武器榜（逐场 PGCR 聚合"最近 N 场"，不是 weapon_history 的别名）
        "pvp_weapons",
    ),
    "BuildIntent": (
        "recommend", "find", "analyze", "farm_target", "equip_build",
        "armor_mods", "exotic_armor", "set_bonus", "community_build",
    ),
    "WorldIntent": (
        "weekly", "weekly_full", "vendor", "search_collectible_nodes",
        "collectible_node", "collectible_item", "community",
    ),
}


def _dispatch_groups() -> list[set[str]]:
    """所有分派文件里"一组 intent 一起处理"的地方（集合字面量 + 相等比较 + 模块常量）。"""
    groups: list[set[str]] = []
    for path in _DISPATCH_FILES:
        groups.extend(_groups_in(path))
    # 同义 intent 收进 `_requests.py` 的模块常量、分派里只写 `intent in SOME_CONST`
    # 是正确做法（单一出处），这里要把那些常量也算成"同一处分派"。
    groups.extend(_module_constants().values())
    return groups


def _module_constants() -> dict[str, set[str]]:
    """`_requests.py` 与分派文件里的模块级"全字符串常量"：名字 → 取值集合。"""
    found: dict[str, set[str]] = {}
    for path in [Path(R.__file__), *_DISPATCH_FILES]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                targets: list[ast.expr] = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                continue
            value = node.value
            if not isinstance(value, (ast.Set, ast.Tuple, ast.List)) or not value.elts:
                continue
            if not all(
                isinstance(element, ast.Constant) and isinstance(element.value, str)
                for element in value.elts
            ):
                continue
            names = {element.value for element in value.elts}
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = names
    return found


def _groups_in(path: Path) -> list[set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    groups: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Set):
            names = {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if names:
                groups.append(names)
        elif isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    groups.append({comparator.value})
    # 模块级集合常量（例如 _INVENTORY_PAGE_INTENTS = {"get", "inventory", "list"}）
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Set):
            names = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if names:
                groups.append(names)
    return groups


def test_every_intent_value_is_registered() -> None:
    """Literal 里的每个取值都要在别名的表里有归属（canonical 或某组的别名）。"""
    orphans: dict[str, dict[str, list[str]]] = {}
    for literal, mapping in ALIASES.items():
        values = set(get_args(getattr(R, literal)))
        known = (
            set(mapping)
            | {alias for group in mapping.values() for alias in group}
            | set(STANDALONE[literal])
        )
        unregistered = sorted(values - known)
        stale = sorted(known - values)
        if unregistered or stale:
            orphans[literal] = {"未登记": unregistered, "登记了但不存在": stale}

    assert not orphans, (
        "intent 清单与登记表对不上（新增/删除后请更新 docs/COMPATIBILITY.md 与这张表）："
        f"{orphans}"
    )


def test_declared_aliases_exist_in_the_literal() -> None:
    wrong: list[str] = []
    for literal, mapping in ALIASES.items():
        values = set(get_args(getattr(R, literal)))
        for canonical, aliases in mapping.items():
            if canonical not in values:
                wrong.append(f"{literal}: canonical {canonical} 不在 Literal 里")
            wrong.extend(
                f"{literal}: 别名 {alias} 不在 Literal 里"
                for alias in aliases
                if alias not in values
            )

    assert not wrong, "登记的别名与 _requests.py 不一致：\n  " + "\n  ".join(wrong)


def test_alias_groups_share_one_dispatch_site() -> None:
    """同一个别名的所有成员必须出现在同一处分派里，否则就是"偷偷跑偏"。"""
    groups = _dispatch_groups()
    detached: list[str] = []
    for literal, mapping in ALIASES.items():
        for canonical, aliases in mapping.items():
            members = {canonical, *aliases}
            if not any(members <= group for group in groups):
                detached.append(f"{literal}: {sorted(members)}")

    assert not detached, (
        "这些别名组没有共同的处分派（说明有人给其中某个别名写了单独分支）：\n  "
        + "\n  ".join(detached)
    )


def test_corpus_alias_groups_come_from_the_alias_table() -> None:
    """语料脚本里的别名组必须与这张表一致 —— 别名表改了、语料那份副本没改就会一直红。

    真机 2026-09-24：0.7.6 把 `list`/`get` 拆成两件事（list 只给清单行、get 给完整模板），
    别名表跟着改了，可 `scripts/run_corpus_all_rows.py` 里**另抄了一份** `_ALIAS_GROUPS`，
    于是每一轮真机语料都在报"list/get 同参不同 data"。这条闸扫那份副本：
    同组取值必须同属一个 canonical 组（或同为 STANDALONE 的那种"独立行为"成组）。
    """
    import importlib.util
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts" / "run_corpus_all_rows.py"
    spec = importlib.util.spec_from_file_location("_corpus_rows_for_alias_check", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 取值会跨工具重名（`find`/`get`/`list`/`type`/`stats` 都有两份），所以按**工具的 Literal**查
    literal_of_tool = {
        "player_assistant": "PlayerIntent",
        "inventory_assistant": "InventoryIntent",
        "weapon_assistant": "WeaponIntent",
        "loadout_assistant": "LoadoutIntent",
        "subclass_assistant": "SubclassIntent",
        "activity_assistant": "ActivityIntent",
        "world_assistant": "WorldIntent",
        "build_assistant": "BuildIntent",
    }
    known: dict[tuple[str, str], str] = {}
    for literal, groups in ALIASES.items():
        for canonical, aliases in groups.items():
            for value in (canonical, *aliases):
                known[(literal, value)] = f"{canonical}"
    for literal, values in STANDALONE.items():
        for value in values:
            known[(literal, value)] = f"STANDALONE:{value}"

    problems = []
    for tool, _args, intents, _slow in module._ALIAS_GROUPS:
        literal = literal_of_tool[tool]
        groups = {known.get((literal, value)) for value in intents}
        if None in groups or len(groups) != 1:
            problems.append(f"{tool} {intents} → {sorted(str(g) for g in groups)}")

    assert not problems, (
        "语料里的别名组与别名表对不上（同参断言会一直红）：" + "；".join(problems)
    )
