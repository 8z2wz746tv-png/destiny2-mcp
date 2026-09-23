"""把求解器的 `ProcessArmorSet` 转成对外的 `BuildResult`（含可执行 `canonical_build`）。

从 `build_service.find_build` 里搬出来的：那个文件卡在体积上限（1114 行），而"调谐补齐"
正好要落在这段逻辑里 —— 先腾地方、再改，改完把上限跟着调低。

这一段是**唯一**把内部结果翻译成对外契约的地方，所以关于"对外字段代表什么"的口径都写在这里：

- `build.*` 六维 = 护甲（含调谐）+ 属性模组 + 子职业/碎片；
- `missing_requirements` 用 `stat: 实际/目标` 的写法，`completion_rate` 按目标条数算；
- `canonical_build.items[].mods` 是**逐件要装的插件 hash**，调谐插件也在这里面
  （调谐和模组走同一条写入路径：能量为 0 时自动用免费插槽接口）；
- `tuning_changes` / `requires_tuning` 只是给人看的解释，执行仍然只看 `canonical_build`。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..build.constants import STAT_NAMES
from ..build.models import (
    BuildCandidate,
    BuildConstraints,
    BuildResult,
    tuning_is_allowed,
)
from ..build.scorer import score as _score
from ..build.tuning import TuningPlan
from ..build_contracts import CanonicalBuild, ExecutableBuild
from ..models import LoadoutItem, LoadoutSubclassConfig
from ..vocabulary import STAT_LABELS_ZH


LOADOUT_SLOT_NAMES = {
    "helmets": "helmet",
    "helmet": "helmet",
    "gauntlets": "gauntlets",
    "chests": "chest",
    "chest": "chest",
    "legs": "legs",
    "class_items": "class_item",
    "class_item": "class_item",
}


@dataclass(slots=True)
class ResultContext:
    """构造 `BuildResult` 需要的一切（求解器之外的输入都在这里）。"""

    parsed: Any
    request: Any
    manifest: Any
    class_type: str
    snapshot_version: str
    bonus_vector: list[int]
    fragment_details: list[dict] = field(default_factory=list)
    execution_subclass: LoadoutSubclassConfig | None = None
    #: 护甲实例 ID 组合（排序后）→ 这套的调谐改动；空表示这套不用动调谐。
    tuning: dict[tuple[str, ...], TuningPlan] = field(default_factory=dict)


def set_key(items: Sequence[Any]) -> tuple[str, ...]:
    """一套护甲的稳定标识：按实例 ID 排序后做键。"""
    return tuple(sorted(str(getattr(item, "item_instance_id", "")) for item in items))


def tuning_note(plan: TuningPlan | None, item_names: dict[str, str] | None = None) -> str:
    """把调谐改动说成人话（没有改动就返回空串）。

    口径（2026-09-22）：调谐**能替你写**，条件只有一条 —— **这颗在这件护甲允许的清单里**
    （组件 310）。真机已验证：写入 `ErrorCode=1`、回读插槽与六维一致、不花材料
    （见 ADR-014 的修订与 `docs/plans/TUNING_WRITE_PLAN.md`）。
    所以这句话现在是"**确认后会一起改**"，不再是"要你进游戏手动改"。
    """
    if plan is None or not plan.feasible or not plan.changes:
        return ""
    names = item_names or {}
    parts = []
    for change in plan.changes:
        who = names.get(change.item_instance_id) or change.item_name or change.item_instance_id
        parts.append(f"{who} 改成「{change.to_name}」")
    head = (
        f"这套方案要先把 {len(plan.changes)} 件护甲的调谐改掉才能达标"
        "（调谐不占能量、不影响模组；确认后**一起改**，改不了会说明是哪一件、为什么）："
    )
    tail = ""
    if plan.exhausted:
        # 碰了步数上限就是"没吃干净"，不能装成已经最优（P4 的局部最优不变量会抓这个）。
        tail = "（这轮局部优化是碰到步数上限停的，可能还有别的改法没试到，不代表已经最优。）"
    return head + "；".join(parts) + "。" + tail


def build_results(
    process_sets: Sequence[Any],
    context: ResultContext,
) -> list[BuildResult]:
    """把求解器返回的前 N 套转成 `BuildResult` 列表（顺序已按优先级排好）。"""
    parsed = context.parsed
    request = context.request
    bonus_vector = context.bonus_vector
    results: list[BuildResult] = []

    for armor_set in list(process_sets)[: parsed.top_n]:
        # 属性模组带来的六维增量（子职业/碎片另算，见 bonus_vector）
        bonus_dict: dict[str, int] = {}
        for index, stat_name in enumerate(STAT_NAMES):
            if index < len(armor_set.bonus_stats):
                bonus_dict[stat_name] = armor_set.bonus_stats[index]

        stats = armor_set.stats
        candidate = BuildCandidate(
            items=list(armor_set.armor),
            weapons=(stats[0] + bonus_dict.get("weapons", 0) + bonus_vector[0]) if len(stats) > 0 else 0,
            health=(stats[1] + bonus_dict.get("health", 0) + bonus_vector[1]) if len(stats) > 1 else 0,
            class_stat=(stats[2] + bonus_dict.get("class_stat", 0) + bonus_vector[2]) if len(stats) > 2 else 0,
            grenade=(stats[3] + bonus_dict.get("grenade", 0) + bonus_vector[3]) if len(stats) > 3 else 0,
            melee=(stats[4] + bonus_dict.get("melee", 0) + bonus_vector[4]) if len(stats) > 4 else 0,
            super_stat=(stats[5] + bonus_dict.get("super_stat", 0) + bonus_vector[5]) if len(stats) > 5 else 0,
            bonus_stats=bonus_dict,
            stat_mods=armor_set.stat_mods,
            subclass_fragment_bonus=bonus_vector,
        )

        key = set_key(armor_set.armor)
        plan = context.tuning.get(key)
        # 调谐**并进** `mods`（2026-09-22 开放代写）：执行器按插件类别找插槽，本来就会往调谐槽写，
        # 回读也会一起核对 —— 走同一条可执行路径，不新增字段。只放"这件允许的"那几颗：
        # 不在组件 310 清单里的调谐**不进计划**（否则等于让 equip_build 去撞 1675）。
        tuning_plugs: dict[str, int] = {}
        if plan is not None and plan.feasible:
            by_id = {str(getattr(item, "item_instance_id", "")): item for item in armor_set.armor}
            for change in plan.changes:
                armor = by_id.get(str(change.item_instance_id))
                # `tuning_is_allowed` 两边过 `to_unsigned`（求解器给的是无符号，但这条比较
                # 是"写入前的兜底"，不许依赖上游有没有归一 —— 一个规矩只写一次）
                if armor is not None and tuning_is_allowed(
                    getattr(armor, "tuning_option_hashes", ()) or (), change.to_plug
                ):
                    tuning_plugs[str(change.item_instance_id)] = int(change.to_plug)
        # 注意 `requires_tuning` 要按**方案**算，不能按"有没有插件要写"算 ——
        # 后者在调谐不可写那阵子恒为 False（实机语料第 ⑩ 行抓到的回归）。
        has_plan = bool(plan is not None and plan.feasible and plan.changes)
        item_names = {
            str(getattr(item, "item_instance_id", "")): str(getattr(item, "name", ""))
            for item in armor_set.armor
        }

        results.append(
            BuildResult(
                score=round(_score(candidate, parsed), 2),
                completion_rate=_count_met(candidate, parsed) / max(1, _count_targets(parsed)),
                build=candidate,
                missing_requirements=_missing_requirements(candidate, parsed),
                max_violations=_max_violations(candidate, parsed),
                fragment_details=context.fragment_details,
                active_set_bonuses=_calculate_set_bonuses(list(armor_set.armor), context.manifest),
                tuning_changes=(
                    [change.as_dict() for change in plan.changes]
                    if plan is not None and plan.feasible
                    else []
                ),
                requires_tuning=has_plan,
                tuning_note=tuning_note(plan, item_names),
                canonical_build=ExecutableBuild(
                    class_type=context.class_type,
                    exotic_hash=next(
                        (item.item_hash for item in armor_set.armor if item.is_exotic),
                        None,
                    ),
                    subclass_item_hash=(
                        context.execution_subclass.subclass_item_hash
                        if context.execution_subclass
                        else None
                    ),
                    subclass_instance_id=(
                        context.execution_subclass.subclass_instance_id
                        if context.execution_subclass
                        else ""
                    ),
                    subclass_plug_sockets=(
                        context.execution_subclass.plug_sockets
                        if context.execution_subclass
                        else {}
                    ),
                    super_hash=(
                        context.execution_subclass.super_hash
                        if context.execution_subclass
                        else None
                    ),
                    grenade_hash=(
                        context.execution_subclass.grenade_hash
                        if context.execution_subclass
                        else None
                    ),
                    melee_hash=(
                        context.execution_subclass.melee_hash
                        if context.execution_subclass
                        else None
                    ),
                    class_ability_hash=(
                        context.execution_subclass.class_ability_hash
                        if context.execution_subclass
                        else None
                    ),
                    movement_hash=(
                        context.execution_subclass.movement_hash
                        if context.execution_subclass
                        else None
                    ),
                    aspect_hashes=(
                        context.execution_subclass.aspect_hashes
                        if context.execution_subclass
                        else []
                    ),
                    fragment_hashes=(
                        context.execution_subclass.fragment_hashes
                        if context.execution_subclass
                        else []
                    ),
                    target_stats={
                        name: value
                        for name, value in {
                            "weapons": request.weapons_target,
                            "health": request.health_target,
                            "class_stat": request.class_target,
                            "grenade": request.grenade_target,
                            "melee": request.melee_target,
                            "super_stat": request.super_target,
                        }.items()
                        if value is not None
                    },
                    items=[
                        LoadoutItem(
                            item_hash=armor.item_hash,
                            name=armor.name,
                            slot=LOADOUT_SLOT_NAMES.get(armor.slot, armor.slot),
                            item_instance_id=armor.item_instance_id,
                            # 属性模组 +（如果这套要改调谐）调谐插件：执行器按类别找槽，
                            # 调谐槽本来就是插槽，回读一视同仁。
                            mods=[
                                *armor_set.stat_mod_assignments.get(
                                    armor.item_instance_id, []
                                ),
                                *(
                                    [tuning_plugs[armor.item_instance_id]]
                                    if armor.item_instance_id in tuning_plugs
                                    else []
                                ),
                            ],
                            source_location=getattr(armor, "source_location", ""),
                            source_character_id=getattr(armor, "source_character_id", ""),
                            was_equipped=getattr(armor, "is_equipped", False),
                        )
                        for armor in armor_set.armor
                    ],
                    snapshot_version=context.snapshot_version,
                    execution_id=secrets.token_urlsafe(18),
                ),
            )
        )
    return results


# ═══════════════════════════════════════════════════════════════════════════
# 上限违规（对外字段：max_violations）—— 超了不拦，只标注 + 排序时靠后


def _max_violations(candidate: BuildCandidate, constraints: BuildConstraints) -> list[dict]:
    """哪几项超过了 `stat_caps`。没给上限的项（0）永远不算违规。"""
    caps = constraints.max_vector()
    return [
        {
            "stat": STAT_NAMES[index],
            "label": STAT_LABELS_ZH[STAT_NAMES[index]],
            "actual": candidate.stat(STAT_NAMES[index]),
            "max": caps[index],
        }
        for index in range(6)
        if caps[index] > 0 and candidate.stat(STAT_NAMES[index]) > caps[index]
    ]


# 目标达成统计（对外字段：completion_rate / missing_requirements）
# ═══════════════════════════════════════════════════════════════════════════


def _count_targets(parsed) -> int:
    """Count how many stat targets (non-zero minimums) are set."""
    return sum(1 for v in parsed.as_vector() if v > 0)


def _count_met(candidate, parsed) -> int:
    """Count how many stat targets are met."""
    met = 0
    for stat_name in STAT_NAMES:
        target = getattr(parsed, f"{stat_name}_min")
        if target > 0 and candidate.stat(stat_name) >= target:
            met += 1
    return met


def _missing_requirements(candidate, parsed) -> list[str]:
    """List stat targets that were not met."""
    missing: list[str] = []
    for stat_name in STAT_NAMES:
        target = getattr(parsed, f"{stat_name}_min")
        actual = candidate.stat(stat_name)
        if target > 0 and actual < target:
            missing.append(f"{stat_name}: {actual}/{target}")
    return missing


def _calculate_set_bonuses(armor_list: list, manifest) -> list[dict]:
    """Calculate active set bonuses for a list of armor pieces.

    Groups armor by set_bonus_hash, counts pieces per set, and looks up
    which perks are active based on required_set_count.

    Returns:
        List of {set_name, piece_count, perks: [{name, description}]}
    """
    set_counts: dict[int, int] = {}
    set_names: dict[int, str] = {}
    for armor in armor_list:
        if armor.set_bonus_hash:
            set_counts[armor.set_bonus_hash] = set_counts.get(armor.set_bonus_hash, 0) + 1
            set_names[armor.set_bonus_hash] = armor.set_bonus_name

    active_bonuses = []
    for set_hash, count in set_counts.items():
        if count < 2:
            continue  # Need at least 2 pieces for any bonus

        set_info = manifest.get_set_bonus_by_hash(set_hash)
        if not set_info:
            continue

        active_perks = []
        for perk in set_info.get("perks", []):
            required = perk.get("required_set_count", 0)
            if count >= required:
                active_perks.append({
                    "name": perk.get("perk_name", ""),
                    "description": perk.get("perk_description", ""),
                    "required_count": required,
                })

        if active_perks:
            active_bonuses.append({
                "set_name": set_names.get(set_hash, set_info.get("set_name", "")),
                "piece_count": count,
                "perks": active_perks,
            })

    return active_bonuses


__all__ = [
    "LOADOUT_SLOT_NAMES",
    "ResultContext",
    "build_results",
    "set_key",
    "tuning_note",
]


def canonical_subclass(build: CanonicalBuild) -> LoadoutSubclassConfig | None:
    """`CanonicalBuild` 的子职业字段 → 装备流程要的 `LoadoutSubclassConfig`（空则 None）。"""
    values = {
        "subclass_item_hash": build.subclass_item_hash or 0,
        "subclass_instance_id": build.subclass_instance_id,
        "super_hash": build.super_hash or 0,
        "grenade_hash": build.grenade_hash or 0,
        "melee_hash": build.melee_hash or 0,
        "class_ability_hash": build.class_ability_hash or 0,
        "movement_hash": build.movement_hash or 0,
        "aspect_hashes": build.aspect_hashes,
        "fragment_hashes": build.fragment_hashes,
        "plug_sockets": build.subclass_plug_sockets,
    }
    if not any([
        values["super_hash"],
        values["grenade_hash"],
        values["melee_hash"],
        values["class_ability_hash"],
        values["movement_hash"],
        values["aspect_hashes"],
        values["fragment_hashes"],
        values["plug_sockets"],
    ]):
        return None
    return LoadoutSubclassConfig(**values)
