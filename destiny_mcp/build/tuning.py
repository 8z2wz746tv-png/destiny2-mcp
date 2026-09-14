"""护甲调谐（Tuning）：把 ±5 的调谐槽变成求解器真能用的一根杠杆。

实测口径（Manifest + 实机 inventory，2026-07 数据，见 ARMOR_FORMAT_PLAN.md P7）：

- 调谐槽的 plug set 是 `1155052024`，里面正好 32 个插件：30 个方向型
  （+5 某一项 / −5 另一项，六维的 30 个有序组合一个不少）、1 个「平衡调整」
  （六维各 +1）、1 个「空调整模组插槽」（什么都不加）。
- 有调谐槽的护甲定义里 `socketEntries[].reusablePlugSetHash == 1155052024`，
  没有 `randomizedPlugSetHash` —— 也就是说这 31 个非空调谐**件件都能装**，
  不存在"这件只能选某几项"的限制。
- Bungie 组件 304（itemStats）给的是**已经含调谐**的最终值，所以换调谐要
  先减掉当前那一次再加新的：`base = 现状 − 当前调谐`。
- 方向型是零和的（+5 一定伴随 −5），平衡调整是 +1×6。所以调谐**不能凭空加属性**，
  只能把 5 点从一项挪到另一项 —— 这一条决定了它在求解器里的正确用法：
  补缺口时要同时检查"被减的那一项会不会掉破目标"。

这个模块只做纯计算（不碰网络、不碰账号）：目录、单件视图、以及"给一批缺口找
一套调谐改动"的精确小搜索。写入路径与求解器接线分别在
`services/loadout_mod_sockets.py` 和 `services/build_tuning.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Sequence

from .armor_rules import (
    BALANCED_TUNING_HASH,
    BALANCED_TUNING_LOWEST_STAT_BONUS,
    DIRECTIONAL_TUNING_HASHES,
    DIRECTIONAL_TUNING_STAT_BONUS,
)
from .constants import STAT_NAMES

TuningKind = Literal["directional", "balanced", "empty"]

#: 调谐槽的 plug set（Manifest 里 1653 件护甲都引用它，且没有随机池）。
TUNING_PLUG_SET_HASH = 1155052024
#: 「空调整模组插槽」：装了等于没装。
EMPTY_TUNING_PLUG_HASH = 2121121504

# 属性名 → 中文（只用于拼兜底名字；正常情况下名字直接读 Manifest 的官方翻译）。
STAT_LABELS_ZH: dict[str, str] = {
    "weapons": "武器",
    "health": "生命值",
    "class_stat": "职业",
    "grenade": "手雷",
    "melee": "近战",
    "super_stat": "超能",
}

STAT_INDEX: dict[str, int] = {name: index for index, name in enumerate(STAT_NAMES)}


def _zeros() -> tuple[int, ...]:
    return (0,) * len(STAT_NAMES)


def _canon(plug_hash: int) -> int:
    """统一成无符号 32 位：实机给的 plugHash 是无符号，硬编码表是带符号的。"""
    return int(plug_hash) & 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class TuningChoice:
    """一个可装的调谐插件，以及它相对「没装调谐」的六维增量。"""

    plug_hash: int
    name: str
    kind: TuningKind
    delta: tuple[int, ...]  # STAT_NAMES 顺序，可正可负
    increased: str | None = None
    decreased: str | None = None

    @property
    def is_noop(self) -> bool:
        return self.kind == "empty" or not any(self.delta)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "hash": self.plug_hash,
            "name": self.name,
            "kind": self.kind,
        }
        if self.increased is not None:
            out["increased"] = self.increased
        if self.decreased is not None:
            out["decreased"] = self.decreased
        if any(self.delta):
            out["delta"] = {
                name: value
                for name, value in zip(STAT_NAMES, self.delta)
                if value
            }
        return out


_catalog_cache: dict[int, tuple[TuningChoice, ...]] = {}


def _catalog(manifest: Any) -> tuple[TuningChoice, ...]:
    """Return every tuning plug once per Manifest instance (cached)."""
    key = id(manifest)
    cached = _catalog_cache.get(key)
    if cached is not None:
        return cached

    def plug_name(plug_hash: int, fallback: str) -> str:
        try:
            name = manifest.get_item_name(plug_hash)
        except Exception:  # noqa: BLE001 - 名字只是展示用，读不到就兜底
            name = ""
        return name if name and not str(name).startswith("#") else fallback

    choices: list[TuningChoice] = []
    for (increased, decreased), plug_hash in DIRECTIONAL_TUNING_HASHES.items():
        delta = [0] * len(STAT_NAMES)
        delta[STAT_INDEX[increased]] += DIRECTIONAL_TUNING_STAT_BONUS
        delta[STAT_INDEX[decreased]] -= DIRECTIONAL_TUNING_STAT_BONUS
        fallback = f"+{STAT_LABELS_ZH[increased]} / -{STAT_LABELS_ZH[decreased]}"
        choices.append(
            TuningChoice(
                plug_hash=_canon(plug_hash),
                name=plug_name(_canon(plug_hash), fallback),
                kind="directional",
                delta=tuple(delta),
                increased=increased,
                decreased=decreased,
            )
        )
    choices.append(
        TuningChoice(
            plug_hash=_canon(BALANCED_TUNING_HASH),
            name=plug_name(_canon(BALANCED_TUNING_HASH), "平衡调整"),
            kind="balanced",
            delta=(BALANCED_TUNING_LOWEST_STAT_BONUS,) * len(STAT_NAMES),
        )
    )
    choices.append(
        TuningChoice(
            plug_hash=EMPTY_TUNING_PLUG_HASH,
            name=plug_name(EMPTY_TUNING_PLUG_HASH, "空调整模组插槽"),
            kind="empty",
            delta=_zeros(),
        )
    )
    ordered = tuple(sorted(choices, key=lambda choice: (choice.kind, -sum(choice.delta), choice.plug_hash)))
    _catalog_cache[key] = ordered
    return ordered


def tuning_catalog(manifest: Any) -> tuple[TuningChoice, ...]:
    """全部 32 个调谐插件（30 方向 + 平衡 + 空）。"""
    return _catalog(manifest)


def directional_tuning_hash(increased: str, decreased: str) -> int:
    """按「加哪一项 / 减哪一项」取方向型调谐 hash；不存在就报错。"""
    try:
        return _canon(DIRECTIONAL_TUNING_HASHES[(increased, decreased)])
    except KeyError as exc:  # pragma: no cover - 由调用方保证取值
        raise KeyError(f"没有 +{increased} / -{decreased} 这种调谐") from exc


def has_tuning_socket(definition: Mapping[str, Any] | None) -> bool:
    """护甲定义里有没有调谐槽（不依赖实机插槽状态）。"""
    if not isinstance(definition, Mapping):
        return False
    entries = (definition.get("sockets") or {}).get("socketEntries") or []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("reusablePlugSetHash") == TUNING_PLUG_SET_HASH:
            return True
        if entry.get("singleInitialItemHash") == EMPTY_TUNING_PLUG_HASH:
            return True
        for plug in entry.get("reusablePlugItems") or []:
            if isinstance(plug, Mapping) and plug.get("plugItemHash") == EMPTY_TUNING_PLUG_HASH:
                return True
    return False


@dataclass(frozen=True, slots=True)
class PieceTuning:
    """一件护甲的调谐视角：现在装的是什么、能换成什么、换完六维是多少。"""

    item_instance_id: str
    item_hash: int
    name: str
    slot: str
    current: TuningChoice
    options: tuple[TuningChoice, ...]
    base: tuple[int, ...]  # 减掉当前调谐之后的六维（STAT_NAMES 顺序）
    note: str = ""

    def stats_with(self, choice: TuningChoice | None) -> tuple[int, ...]:
        """装上 `choice`（None = 保持现状）之后这件护甲的六维。"""
        chosen = self.current if choice is None else choice
        return self.base_with(chosen.delta)

    def base_with(self, delta: Sequence[int]) -> tuple[int, ...]:
        return tuple(
            max(0, value + change)
            for value, change in zip(self.base, delta)
        )

    def option(self, plug_hash: int) -> TuningChoice | None:
        wanted = _canon(plug_hash)
        for choice in self.options:
            if choice.plug_hash == wanted:
                return choice
        return None

    def delta_of(self, choice: TuningChoice) -> tuple[int, ...]:
        """相对**现状**的净变化（求解器里的属性已经是含当前调谐的值）。

        按"每项最低到 0"计算：−5 打在本就是 0 的那一项上是白给的（游戏里属性不会
        变成负数，T5 词条本来就只有三项非零）。所以这里用夹到 0 之后的差值，
        而不是原始 ±5 —— 否则会把"牺牲一个已经见底的属性"误判成有代价，
        也会算出负数这种游戏里不存在的值。
        """
        now = self.stats_with(None)
        after = self.stats_with(choice)
        return tuple(new - old for new, old in zip(after, now))


def piece_tuning(
    armor: Any,
    manifest: Any,
    definition: Mapping[str, Any] | None = None,
) -> PieceTuning | None:
    """把一件 Armor 变成调谐视图；没有调谐槽就返回 None（legacy 护甲就是这种）。"""
    item_hash = int(getattr(armor, "item_hash", 0) or 0)
    if definition is None:
        definition = manifest.get_item_definition(item_hash)
    if not has_tuning_socket(definition):
        return None

    catalog = tuning_catalog(manifest)
    by_hash = {choice.plug_hash: choice for choice in catalog}
    empty = by_hash[EMPTY_TUNING_PLUG_HASH]
    current_hash = _canon(int(getattr(armor, "tuning_mod_hash", 0) or 0))
    current = by_hash.get(current_hash, empty)

    stats = getattr(armor, "stats", None)
    base = tuple(
        int(getattr(stats, name, 0) or 0) - current.delta[index]
        for index, name in enumerate(STAT_NAMES)
    )
    note = ""
    if any(value < 0 for value in base):
        # 反推不出来（理论上不会发生）：宁可只让这件保持现状，也不要编一个负的基础值。
        base = tuple(max(0, value) for value in base)
        note = "这件护甲反推不出未调谐的基础属性，只能保持当前调谐。"

    return PieceTuning(
        item_instance_id=str(getattr(armor, "item_instance_id", "") or ""),
        item_hash=item_hash,
        name=str(getattr(armor, "name", "") or ""),
        slot=str(getattr(armor, "slot", "") or ""),
        current=current,
        options=catalog,
        base=base,
        note=note,
    )


def tuning_headroom(pieces: Iterable[PieceTuning]) -> dict[str, int]:
    """每项最多能靠调谐加多少点（把每件能给出的最大正增益加起来）。"""
    headroom = {name: 0 for name in STAT_NAMES}
    for piece in pieces:
        if piece.note:
            continue
        gains = [0] * len(STAT_NAMES)
        for choice in piece.options:
            for index, value in enumerate(piece.delta_of(choice)):
                if value > gains[index]:
                    gains[index] = value
        for index, name in enumerate(STAT_NAMES):
            headroom[name] += gains[index]
    return headroom


@dataclass(frozen=True, slots=True)
class TuningChange:
    """一处要改的调谐。"""

    item_instance_id: str
    item_name: str
    slot: str
    from_plug: int
    from_name: str
    to_plug: int
    to_name: str
    delta: tuple[int, ...]  # 相对现状的净变化
    increased: str | None = None
    decreased: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "item_instance_id": self.item_instance_id,
            "item_name": self.item_name,
            "slot": self.slot,
            "from": {"hash": self.from_plug, "name": self.from_name},
            "to": {"hash": self.to_plug, "name": self.to_name},
            "delta": {
                name: value
                for name, value in zip(STAT_NAMES, self.delta)
                if value
            },
        }
        if self.increased is not None:
            out["increased"] = self.increased
        if self.decreased is not None:
            out["decreased"] = self.decreased
        return out


@dataclass(frozen=True, slots=True)
class TuningPlan:
    """一套调谐改动。`feasible=False` 时 `reason` 说明为什么补不上。"""

    feasible: bool
    changes: tuple[TuningChange, ...] = ()
    final_delta: tuple[int, ...] = _zeros()
    reason: str = ""
    searched: int = 0
    #: 次优方案（同序，已去重）。复核用它兜底：排序只是启发式，能不能过由复核说了算。
    alternatives: tuple[tuple[TuningChange, ...], ...] = ()

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "changes": [change.as_dict() for change in self.changes],
            "change_count": self.change_count,
            "final_delta": {
                name: value
                for name, value in zip(STAT_NAMES, self.final_delta)
                if value
            },
            "reason": self.reason,
        }


def _meets(stats: Sequence[int], targets: Mapping[str, int], offset: Sequence[int]) -> bool:
    for name, target in targets.items():
        index = STAT_INDEX[name]
        if stats[index] + offset[index] < target:
            return False
    return True


def plan_tuning(
    pieces: Sequence[PieceTuning],
    stats_now: Sequence[int],
    targets: Mapping[str, int],
    priority: Sequence[str] = (),
    max_nodes: int = 200_000,
    total_budget: int = 0,
    collect: int = 4,
) -> TuningPlan:
    """给一批缺口找一套调谐改动：能补就补，补不上说清为什么。

    之所以能"精确"：每件护甲的候选只保留**能增加某个缺口项**的那些调谐
    （含"撤掉现在这次调谐"这种），再对这 5 件做一次穷举 —— 5 件 × ≤8 个候选
    在 5 件的规模下是有界的，且每个候选都按目标逐项校验，
    所以"被减的那一项掉破目标"不可能被漏掉。

    `total_budget`：调谐之外还能补的点数（属性模组）。只要**剩余缺口合计**不超过它，
    这套方案就算可行 —— 因为求解器复核时会把模组往缺口上堆（每件一个 +10 的模组）。
    实机踩过：把预算按"某项优先"硬摊到每一项头上，会得出"明明只差 4 点却说补不上"，
    或者白白改 5 件护甲的调谐。合计口径才是和复核一致的口径。
    """
    usable = [piece for piece in pieces if not piece.note]
    if not usable:
        return TuningPlan(feasible=False, reason="没有带调谐槽的护甲，调谐补不了。")

    live_targets = {
        name: int(target)
        for name, target in targets.items()
        if name in STAT_INDEX and int(target) > 0
    }
    if not live_targets:
        return TuningPlan(feasible=True, reason="没有需要补的目标，不需要动调谐。")

    deficits = {
        name: live_targets[name] - int(stats_now[STAT_INDEX[name]])
        for name in live_targets
        if live_targets[name] > int(stats_now[STAT_INDEX[name]])
    }
    if not deficits:
        return TuningPlan(feasible=True, reason="现状已经达标，不需要动调谐。")

    # 必要条件（逐项、精确）：每项能加的点数上限 = 各件在这一项上能给出的最大增益之和。
    # 单件不一定是 5 —— 这件现在要是 +武器/−生命值，"换成 +生命值/−武器"就是 +10 生命。
    max_gain = {name: 0 for name in STAT_NAMES}
    for piece in usable:
        best_of_piece = {name: 0 for name in STAT_NAMES}
        for choice in piece.options:
            delta = piece.delta_of(choice)
            for index, name in enumerate(STAT_NAMES):
                if delta[index] > best_of_piece[name]:
                    best_of_piece[name] = delta[index]
        for name in STAT_NAMES:
            max_gain[name] += best_of_piece[name]
    over = {
        name: gap
        for name, gap in deficits.items()
        if gap > max_gain[name] + max(0, total_budget)
    }
    if over:
        detail = "、".join(
            f"{name} 差 {gap} 点（调谐最多能加 {max_gain[name]}）"
            for name, gap in over.items()
        )
        return TuningPlan(
            feasible=False,
            reason=f"缺口超出调谐能力：{detail}。",
        )

    # 平衡调整只在缺口 ≤1（它给六维各 +1）时才有意义，其余情况不入选，免得白翻。
    allow_balanced = any(gap <= BALANCED_TUNING_LOWEST_STAT_BONUS for gap in deficits.values())
    candidate_options: list[list[TuningChoice]] = []
    for piece in usable:
        options: list[TuningChoice] = []
        for choice in piece.options:
            delta = piece.delta_of(choice)
            helps = any(
                delta[STAT_INDEX[name]] > 0 for name in deficits
            )
            if choice.kind == "balanced" and not allow_balanced:
                continue
            if choice.kind == "balanced" and not helps and piece.current.is_noop:
                helps = True
            if helps:
                options.append(choice)
        candidate_options.append(options)

    best_key: tuple[Any, ...] | None = None
    best_changes: tuple[TuningChange, ...] = ()
    best_delta: tuple[int, ...] = _zeros()
    searched = 0
    truncated = False

    priority_indices = [STAT_INDEX[name] for name in priority if name in STAT_INDEX] or [
        STAT_INDEX[name] for name in STAT_NAMES
    ]
    ranked: list[tuple[tuple[Any, ...], tuple[TuningChange, ...], tuple[int, ...]]] = []

    # 每件护甲对每一项最多能加多少（可能 >5：撤掉反向调谐就是 +10），
    # 以及"从第 i 件往后"的后缀和 —— 剪枝要用它算乐观上界。
    piece_gain: list[list[int]] = []
    for piece in usable:
        gains = [0] * len(STAT_NAMES)
        for choice in piece.options:
            for index, value in enumerate(piece.delta_of(choice)):
                if value > gains[index]:
                    gains[index] = value
        piece_gain.append(gains)
    suffix_gain: list[list[int]] = [[0] * len(STAT_NAMES) for _ in range(len(usable) + 1)]
    for position in range(len(usable) - 1, -1, -1):
        for index in range(len(STAT_NAMES)):
            suffix_gain[position][index] = (
                suffix_gain[position + 1][index] + piece_gain[position][index]
            )

    def evaluate(assignment: list[TuningChoice | None]) -> None:
        nonlocal best_key, best_changes, best_delta
        offset = [0] * len(STAT_NAMES)
        for piece, choice in zip(usable, assignment):
            if choice is None:
                continue
            delta = piece.delta_of(choice)
            for index, value in enumerate(delta):
                offset[index] += value
        meets_all = _meets(stats_now, live_targets, offset)
        remaining = 0 if meets_all else sum(
            max(0, target - (int(stats_now[STAT_INDEX[name]]) + offset[STAT_INDEX[name]]))
            for name, target in live_targets.items()
        )
        if not meets_all and remaining > max(0, total_budget):
            return
        for index, value in enumerate(offset):
            if int(stats_now[index]) + value < 0:
                return
        changes = tuple(
            TuningChange(
                item_instance_id=piece.item_instance_id,
                item_name=piece.name,
                slot=piece.slot,
                from_plug=piece.current.plug_hash,
                from_name=piece.current.name,
                to_plug=choice.plug_hash,
                to_name=choice.name,
                delta=piece.delta_of(choice),
                increased=choice.increased,
                decreased=choice.decreased,
            )
            for piece, choice in zip(usable, assignment)
            if choice is not None
        )
        # 排序口径（都是"达标方案之间"的偏好）：
        # 1) 改动件数最少；2) 总让步最小（能不动别人就不动 —— 缺口 1 点时平衡调整就是
        #    零让步，胜过 5 点大挪移）；3) 让步后"最紧的那一项"尽量宽松（别可着刚够目标
        #    的项薅）；4) 优先级属性总和最大；5) delta 字典序（保证结果可复现）。
        harm = sum(value for value in offset if value < 0)
        slacks = sorted(
            int(stats_now[STAT_INDEX[name]]) + offset[STAT_INDEX[name]] - target
            for name, target in live_targets.items()
        )
        key = (
            len(changes),
            remaining,
            -harm,
            tuple(-slack for slack in slacks),
            tuple(-offset[index] for index in priority_indices),
            tuple(offset),
        )
        ranked.append((key, changes, tuple(offset)))
        if best_key is None or key < best_key:
            best_key = key
            best_changes = changes
            best_delta = tuple(offset)

    def walk(index: int, assignment: list[TuningChoice | None]) -> None:
        nonlocal searched, truncated
        if truncated:
            return
        if index == len(usable):
            searched += 1
            if searched > max_nodes:
                truncated = True
                return
            evaluate(assignment)
            return
        # 乐观上界剪枝：剩下的每一件都按"它对每一项的最大增益"算（同一件同时算进每一项，
        # 所以这是上界，不会误砍），再看"缺口总量 − 模组预算"这个必须由调谐补的量能不能凑出来。
        partial = [0] * len(STAT_NAMES)
        for piece, choice in zip(usable, assignment):
            if choice is None:
                continue
            for offset_index, value in enumerate(piece.delta_of(choice)):
                partial[offset_index] += value
        gaps = _remaining(deficits, stats_now, live_targets, partial)
        if not gaps:
            return  # 缺口已经被补上了，剩下的件不用再翻
        budget_left = max(0, total_budget)
        need = max(0, sum(gaps) - budget_left)
        possible = 0
        for name in _remaining_names(deficits, stats_now, live_targets, partial):
            gap = live_targets[name] - (int(stats_now[STAT_INDEX[name]]) + partial[STAT_INDEX[name]])
            if gap <= 0:
                continue
            possible += min(gap, suffix_gain[index][STAT_INDEX[name]])
        if possible < need:
            return
        for choice in (None, *candidate_options[index]):
            assignment.append(choice)
            walk(index + 1, assignment)
            assignment.pop()

    walk(0, [])

    if best_key is None:
        reason = "调谐补不上这组缺口（每件要么没有能补这一项的调谐，要么减的那一项会掉破目标）。"
        if truncated:
            reason = (
                "候选组合太多，搜到上限就停了，没敢下结论 —— "
                "把目标收窄一点再试。"
            )
        return TuningPlan(feasible=False, reason=reason, searched=searched)
    ranked.sort(key=lambda row: row[0])
    distinct: list[tuple[TuningChange, ...]] = []
    seen: set[tuple[tuple[str, int], ...]] = set()
    for _key, changes, _offset in ranked:
        signature = tuple(sorted((change.item_instance_id, change.to_plug) for change in changes))
        if signature in seen:
            continue
        seen.add(signature)
        distinct.append(changes)
        if len(distinct) >= max(1, collect):
            break
    return TuningPlan(
        feasible=True,
        changes=best_changes,
        final_delta=best_delta,
        reason="" if best_changes else "现状已经达标，不需要动调谐。",
        searched=searched,
        alternatives=tuple(distinct[1:]),
    )


def _remaining_names(
    deficits: Mapping[str, int],
    stats_now: Sequence[int],
    targets: Mapping[str, int],
    partial: Sequence[int],
) -> list[str]:
    """还差着的项（名字），与 `_remaining` 同口径。"""
    out: list[str] = []
    for name in deficits:
        index = STAT_INDEX[name]
        if targets[name] - (int(stats_now[index]) + partial[index]) > 0:
            out.append(name)
    return out


def _remaining(
    deficits: Mapping[str, int],
    stats_now: Sequence[int],
    targets: Mapping[str, int],
    partial: Sequence[int],
) -> list[int]:
    """还差多少点（只算仍然差着的项，用于剪枝）。"""
    out: list[int] = []
    for name in deficits:
        index = STAT_INDEX[name]
        gap = targets[name] - (int(stats_now[index]) + partial[index])
        if gap > 0:
            out.append(gap)
    return out


__all__ = [
    "EMPTY_TUNING_PLUG_HASH",
    "STAT_INDEX",
    "TUNING_PLUG_SET_HASH",
    "PieceTuning",
    "TuningChange",
    "TuningChoice",
    "TuningPlan",
    "directional_tuning_hash",
    "has_tuning_socket",
    "piece_tuning",
    "plan_tuning",
    "tuning_catalog",
    "tuning_headroom",
]
