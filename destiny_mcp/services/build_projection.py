"""候选行投影：把求解结果压成"能决定穿不穿这套"的一行（`find`/`recommend` 的默认出口）。

搬出 `tools/_build_flow.py` 的原因：那边贴着体积上限（366 行），而 ①（"这套为什么装不上"
要说进 `find` 的信封）需要在同一处加分支 —— 投影是**形状工厂**，本来就该在服务层
（与 `services/weapon_analysis_projection.py`、`inventory_analysis_service.duplicate_rows`
同一个分工），而"为什么没有候选"是分支话术。

真机基线（2026-09-25）：`find` 一次 21.5 KB / 2 套，其中每套 `build` 6.74 KB —— 那是**求解器的
内部模型**（`tuning_option_hashes` / `base_roll_stats` / `archetype_*` / `armor3_roll_verified` /
`roll_parse_error` / `icon_url` …），模型看不懂也用不上；`canonical_build` 又占 2 KB 且是
"原样回传"的执行载荷。两者都不该出现在默认响应里：默认给行，细节与执行走 `execution_id`
（确认信封 `equip_build(execution_id, confirmed=false)` 会给出逐件预览与 canonical_build）。

保留什么：**能让人决定"穿不穿这套"的东西** —— 分数/达标率/六维/金装/套装/是否要改调谐，
以及这套是哪五件（名字、部位、实例 ID、光等、能量、六维、调谐名）。
"""

from __future__ import annotations

from typing import Any


__all__ = ["ROWS_NOTE", "candidate_rows", "row_hint", "tuning_summary"]

_ROW_ITEM_FIELDS = (
    "name", "item_hash", "item_instance_id", "slot", "slot_key", "slot_display",
    "power", "energy_capacity", "stats", "is_exotic", "set_bonus_name",
    "tuning_name", "is_masterworked", "is_artifice",
)


def _tuning_rows(changes: Any) -> list[dict[str, Any]]:
    """调谐改动：只留"哪一件、从什么换成什么、六维怎么动"。

    真机形状是 `{item_instance_id, item_name, slot, from{hash,name}, to{hash,name}, delta{…},
    slot_key, slot_display}`；hash 对玩家没意义（要执行的话用 `execution_id` 取候选，
    那里有完整的 `tuning_changes`），所以这里只留名字与六维变化。
    """
    rows: list[dict[str, Any]] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        row: dict[str, Any] = {
            key: change[key]
            for key in ("item_instance_id", "item_name", "slot", "slot_key", "slot_display", "delta")
            if change.get(key) is not None
        }
        for key in ("from", "to"):
            block = change.get(key)
            if isinstance(block, dict) and block.get("name"):
                row[key] = {"name": block["name"]}
        rows.append(row)
    return rows


def candidate_rows(results: Any) -> list[dict[str, Any]]:
    """把 `find` / `recommend` 的结果投影成**候选行**（默认出口）。"""
    rows: list[dict[str, Any]] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        candidate = result.get("build") if isinstance(result.get("build"), dict) else {}
        canonical = result.get("canonical_build") if isinstance(result.get("canonical_build"), dict) else {}
        items = candidate.get("items") if isinstance(candidate.get("items"), list) else []
        row: dict[str, Any] = {
            "execution_id": result.get("execution_id") or canonical.get("execution_id") or "",
            "score": result.get("score"),
            "completion_rate": result.get("completion_rate"),
            "stats": {
                name: candidate.get(name)
                for name in ("weapons", "health", "class_stat", "grenade", "melee", "super_stat")
                if candidate.get(name) is not None
            },
            "missing_requirements": result.get("missing_requirements") or [],
            "max_violations": result.get("max_violations") or [],
            "requires_tuning": bool(result.get("requires_tuning")),
            "tuning_changes": _tuning_rows(result.get("tuning_changes")),
            "items": [
                {key: item.get(key) for key in _ROW_ITEM_FIELDS if item.get(key) is not None}
                for item in items
                if isinstance(item, dict)
            ],
        }
        if result.get("tuning_note"):
            row["tuning_note"] = result["tuning_note"]
        if result.get("functional_mods"):
            row["functional_mods"] = result["functional_mods"]
        exotic = next((i.get("name") for i in row["items"] if i.get("is_exotic")), "")
        sets = [i.get("set_bonus_name") for i in row["items"] if i.get("set_bonus_name")]
        row["exotic"] = exotic or ""
        # 同一套里同一套装名会出现多次：去重但保序（"埃希恩记忆 ×4"这种话要靠它）
        row["set"] = list(dict.fromkeys(sets))
        rows.append(row)
    return rows


ROWS_NOTE = (
    "这里是**候选行**：分数/六维/金装/套装 + 这套是哪五件（名字、部位、实例 ID、光等、能量、"
    "调谐名）。求解器内部字段与可执行载荷（canonical_build）不在默认响应里 —— "
    "要看逐件预览或装备它，用 execution_id 调 equip_build。"
)


def row_hint(row: dict[str, Any]) -> str:
    """每一行都带一句"下一步"：不给就把"怎么把行变成动作"丢给模型猜。"""
    return (
        "要看逐件预览或装备它：build_assistant(intent=\"equip_build\", "
        f'execution_id="{row.get("execution_id")}", character=…)；先不确认拿预览，'
        "得到同意后加 confirmed=true（编号 30 分钟内有效）。"
    )


def tuning_summary(builds: Any) -> dict[str, Any] | None:
    """结果里有没有"要靠调谐才达标"的方案；没有就返回 None（不占响应）。"""
    if not isinstance(builds, list):
        return None
    rows = [
        build
        for build in builds
        if isinstance(build, dict) and build.get("tuning_changes")
    ]
    if not rows:
        return None
    changes = [change for row in rows for change in row.get("tuning_changes") or []]
    return {
        "build_count": len(rows),
        "change_count": len(changes),
        "changes": changes,
        "note": (
            "这些方案是「按原目标求解器没达标 → 放宽目标复解 → 用真实目标逐套复核」"
            "找出来的；tuning_changes 里是要改的调谐（从什么改成什么、六维怎么变）。"
            "调谐**确认后一起改**（2026-09-22 起开放代写）：条件只有一条 —— 这颗在这件护甲"
            "允许的清单里（组件 310）。canonical_build 里带着它，equip_build 会连同护甲与模组"
            "一起写；清单外的不会进计划（那种情况上游会回 1675「这颗装不到这件上」）。"
        ),
    }

