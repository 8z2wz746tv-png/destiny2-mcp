"""护甲调谐（Tuning）：把 ±5 的调谐槽变成求解器真能用的一根杠杆。

实测口径（Manifest + 实机 inventory，2026-07 数据，见 docs/plans/ARMOR_FORMAT_PLAN.md P7）：

- 调谐槽的 plug set 是 `1155052024`，里面正好 32 个插件：30 个方向型
  （+5 某一项 / −5 另一项，六维的 30 个有序组合一个不少）、1 个「平衡调整」
  （**恰好三项并列最低时各 +1**）、1 个「空调整模组插槽」（什么都不加）。
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
from itertools import combinations
from typing import Any, Iterable, Literal, Mapping, Sequence

from .armor_rules import (
    BALANCED_TUNING_HASH,
    BALANCED_TUNING_LOWEST_STAT_BONUS,
    DIRECTIONAL_TUNING_HASHES,
    DIRECTIONAL_TUNING_STAT_BONUS,
    balanced_tuning_bonus,
)
from ..vocabulary import STAT_LABELS_ZH  # 六维中文名的单一出处
from .constants import STAT_NAMES

TuningKind = Literal["directional", "balanced", "empty"]

#: 调谐槽的 plug set（Manifest 里 1653 件护甲都引用它，且没有随机池）。
TUNING_PLUG_SET_HASH = 1155052024
#: 「空调整模组插槽」：装了等于没装。
EMPTY_TUNING_PLUG_HASH = 2121121504


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
        # 只认"空插槽"。**不能**用 `not any(delta)` 判：平衡调整的 delta 是按件现算的
        # （见 `PieceTuning.choice_delta`），目录里那份是全 0，照 delta 判会把它当成空操作。
        return self.kind == "empty"

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
            # **不是常量增量**：它给"恰好三项并列最低"各 +1，得看这件的基础六维才定得下来
            # （真机实测见 `armor_rules.balanced_tuning_bonus`）。所以这里放全 0 占位，
            # 真正的增量由 `PieceTuning.choice_delta` 现算 —— 谁要是拿这个 delta 直接用，
            # 就会把平衡调整算成"什么都没做"，这比算成"六维各 +1"安全。
            delta=_zeros(),
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
    #: 观测到的六维（含当前调谐，与组件 304 同口径）。反推不出来时它就是"保持现状"的值。
    observed: tuple[int, ...] = ()
    #: 这件**能不能动调谐**。反推不出基础六维时为 False —— 机器可读的那一份，
    #: 与 `note != ""` 恒等（`tests/test_tuning.py` 钉住这条一致性）。
    movable: bool = True

    def stats_with(self, choice: TuningChoice | None) -> tuple[int, ...]:
        """装上 `choice`（None = 保持现状）之后这件护甲的六维。

        反推不出基础六维的件**原样返回观测值**：它的调谐改动我们算不出来，
        那就"不动它"，而不是编一个改法出来（调用方本来就该看 `movable` 先跳过它，
        这里是防止有人漏看时凭空造出属性）。
        """
        if not self.movable:
            return self.observed
        chosen = self.current if choice is None else choice
        return self.base_with(self.choice_delta(chosen))

    def choice_delta(self, choice: TuningChoice) -> tuple[int, ...]:
        """这个选择相对「没装调谐」的增量。

        方向型是常量 ±5；**平衡调整要按这件的基础六维现算**（它只给最低的三项 +1）。
        """
        if choice.kind == "balanced":
            return balanced_tuning_bonus(self.base)
        return choice.delta

    def base_with(self, delta: Sequence[int]) -> tuple[int, ...]:
        """基础六维 + 一份调谐增量。

        **不夹 0**（2026-09-22 真机纠正）：以前这里写 `max(0, base + delta)`，
        理由是"游戏里属性不会变成负数"。可组件 304 **如实报负数** —— 光芒领主手套
        `…631525` 的 304 就是「生命 −5」（它的生命基础值是 0，装着 +职业/−生命值）。
        夹 0 会让反推（`observed − delta`）把被夹掉的那 5 点当成"本来就有"，
        于是贪心每走一步就凭空长 5 点：真机同一件走几步后从 武器25/生命−5/手雷0/超能0
        变成 武器30/生命5/手雷5/超能5 —— **多出 15 点账面属性**（用户发现的）。
        模型必须等于游戏报的数，所以这里如实算。
        """
        return tuple(value + change for value, change in zip(self.base, delta))

    def option(self, plug_hash: int) -> TuningChoice | None:
        wanted = _canon(plug_hash)
        for choice in self.options:
            if choice.plug_hash == wanted:
                return choice
        return None

    def delta_of(self, choice: TuningChoice) -> tuple[int, ...]:
        """相对**现状**的净变化（求解器里的属性已经是含当前调谐的值）。

        **比的是"生效值"：每项先夹到 0** —— 用户口径（2026-09-22，他在游戏里确认）：
        人物那一项见底之后，再往下扣**不掉点数**；组件 304 报的负数只是记录值，没有实际影响。
        所以"牺牲一个已经见底的属性"是**白给**的，改成 0 才不会把免费走法误判成有代价。

        注意**只夹在这里**（比较用）：`base` 与 `stats_with` 保持**不夹**（跟 304 一致），
        否则反推会凭空长出点数 —— 上午就是因为把夹 0 写进了存储层才出的那个 bug。
        """
        now = self.stats_with(None)
        after = self.stats_with(choice)
        return tuple(
            max(0, new) - max(0, old) for new, old in zip(after, now)
        )


def _invert_tuning(
    observed: tuple[int, ...], current: TuningChoice
) -> tuple[int, ...] | None:
    """从"含当前调谐的观测六维"反推"没装调谐的基础六维"。

    方向型直接减。平衡调整要解**不动点**：它的 +1 只落在"恰好三项并列最低"上，
    所以要猜是哪三项、减掉之后再正着算一遍，能还原观测值才认。猜不出来返回 None ——
    调用方宁可让这件保持现状，也不要拿一个错的基础值去算别的调谐。
    """
    if current.kind != "balanced":
        return tuple(
            value - current.delta[index] for index, value in enumerate(observed)
        )
    minimum = min(observed)
    # 拿了 +1 的那三项在观测值里必然是并列最低（它们 = 基础最低值 + 1），
    # 但可能有更多项并列，所以要在候选里试，再用"正着算一遍"筛。
    for drop in combinations(range(len(observed)), 3):
        if any(observed[index] != minimum for index in drop):
            continue
        candidate = tuple(
            value - (1 if index in drop else 0)
            for index, value in enumerate(observed)
        )
        restored = tuple(
            value + bonus
            for value, bonus in zip(candidate, balanced_tuning_bonus(candidate))
        )
        if restored == observed:
            return candidate
    return None


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
    # ── 这件**允许装**哪些调谐：只信组件 310 的清单（`Armor.tuning_option_hashes`）──
    # Manifest 的调谐 plug set 是全局那 32 颗（真机：两件同名手套指向同一个集合），照它规划
    # 就会给出"这件根本装不上"的调谐 —— 用户发现的那个问题。每件实际只开放"某一个属性 +5"
    # 的 5 颗 + 平衡调整（真机：一件是 职业、另一件是 近战）。
    legal = tuple(int(h) for h in (getattr(armor, "tuning_option_hashes", ()) or ()))
    if legal:
        allowed = set(legal)
        options = tuple(choice for choice in catalog if choice.plug_hash in allowed)
        if EMPTY_TUNING_PLUG_HASH not in allowed:
            # 撤掉调谐（插回空插件）永远可以做，它一般不在 310 的清单里
            options = (*options, empty)
    else:
        # 读不到清单：**缺数据 ≠ 允许** —— 只留"撤掉"这一个动作，不规划任何改法。
        options = (empty,)
        no_options_note = "读不到这件护甲的调谐候选（组件 310），不猜它能装什么。"
    current_hash = _canon(int(getattr(armor, "tuning_mod_hash", 0) or 0))
    current = by_hash.get(current_hash, empty)

    stats = getattr(armor, "stats", None)
    observed = tuple(int(getattr(stats, name, 0) or 0) for name in STAT_NAMES)
    base = _invert_tuning(observed, current)
    note = ""
    movable = True
    if not legal:
        note = no_options_note
        movable = False
    if base is None or any(value < 0 for value in base):
        # 反推不出来（数据对不上）：宁可只让这件保持现状，也不要编一个基础值 ——
        # 更不许拿它去算"换成某个调谐会怎样"（以前这里是 `base = observed`，
        # 配上 `stats_with(当前调谐)` 会把当前调谐**再加一遍**）。
        base = tuple(value for value in observed)
        movable = False
        note = "这件护甲反推不出未调谐的基础属性，只能保持当前调谐。"

    return PieceTuning(
        item_instance_id=str(getattr(armor, "item_instance_id", "") or ""),
        item_hash=item_hash,
        name=str(getattr(armor, "name", "") or ""),
        slot=str(getattr(armor, "slot", "") or ""),
        current=current,
        options=options,
        base=base,
        note=note,
        observed=observed,
        movable=movable,
    )


def armor_with_tuning(armor: Any, piece: PieceTuning, choice: TuningChoice) -> Any:
    """把一件护甲换成指定的调谐：**属性与 tuning 字段一起改**，别只改一半。

    以前这个方法在 `services/build_tuning.py`（第 3 层），于是 `build/` 里想用就用不了 ——
    层规则挡着。它本身是纯的（只依赖 `PieceTuning` 与 `ArmorStats`），挪到这一层才对。
    """
    from .models import ArmorStats

    return armor.model_copy(
        update={
            "stats": ArmorStats(**dict(zip(STAT_NAMES, piece.stats_with(choice)))),
            "tuning_mod_hash": choice.plug_hash,
            "tuning_name": choice.name,
        }
    )


#: 局部贪心的默认步数上限（**只写这一处**：池子包装函数以前自己抄了个 12，
#: 于是"改了默认值"只改了单测直接调用的那条路，产品路径还是 12 —— 实测收敛点见
#: `docs/plans/SOLVER_OPTIMALITY_PLAN.md` 的 P5 记录，上限 12 时第 13 步本来还能再涨）。
LOCAL_TUNING_MAX_STEPS = 24


def local_tuning_improvement(
    armor_set: Any,
    context: Any,
    manifest: Any,
    *,
    max_steps: int = LOCAL_TUNING_MAX_STEPS,
) -> tuple[Any, TuningPlan]:
    """在"单件调谐"这个邻域里贪心提升一套**已经达标**的配装。

    为什么要有它（与 `plan_tuning` 的分工）：

    - `plan_tuning`：**差得补不上**时的穷举补救 —— 先放宽目标复解、再逐套复核，
      真机实测一条请求要 200 多秒。它只在"严格解为空"那条路走。
    - 这里：**已经达标、还想更好**时的局部提升。真机上"手雷 120 就能到 130"那 10 点
      全在免费的调谐槽里（0 能量、+5/−5），以前没人去拿。

    两件事判据**完全相同**：每一步都过 `validate_fixed_process_items`（按真实目标重新分配
    属性模组、再逐项比六维），所以"换了调谐反而掉破另一项"的走法不会被接受。

    **这是局部搜索，不是全局最优的证明** —— 只能保证"没有单件调谐替换能再改进"
    （d2-armor-solver 对同类算法写过同一句：局部邻域完成 ≠ 全局最优）。
    """
    if max_steps <= 0:
        return armor_set, TuningPlan(feasible=True, reason="没开局部调谐优化。")


    from .process_types import ProcessArmorSet, armor_to_process_item
    from .solver import validate_fixed_process_items

    arms = list(armor_set.armor)
    pieces: list[PieceTuning] = []
    for armor in arms:
        piece = piece_tuning(armor, manifest)
        if piece is None or not piece.movable:
            return armor_set, TuningPlan(feasible=True, reason="有的护甲没有可用的调谐槽。")
        pieces.append(piece)
    # 原件视角留着算**净改动**：贪心可能对同一件走好几步（第一步 +手雷/−生命值，
    # 第二步发现那一项已经见底、改成 +手雷/−超能 是白拿的），中间步骤不是用户要看的
    # —— 真机第一次跑就报了 12 条改动、实际只涉及 5 件。对外只给"从原样改成最终样"。
    original = list(pieces)

    constraints = context.constraints
    # 用户什么都没要求时**不动他的调谐**：调谐免费，但"改 5 件"要他自己动手点。
    # 没有目标也没有优先级时，唯一还能提升的只剩排序键最后那位"封顶总和"——
    # 为了 +6 总点让用户改 5 件，不是他要的（真机第一次跑就出现过这个噪音）。
    if not constraints.has_any_target and not constraints.ordered_priority_indices:
        return armor_set, TuningPlan(feasible=True, reason="没有目标也没有优先级，不动调谐。")
    maximums = constraints.max_vector()
    priority_indices = constraints.ordered_priority_indices
    changes: list[TuningChange] = []

    def _evaluate(candidate_arms: Sequence[Any]) -> Any:
        items = [armor_to_process_item(armor) for armor in candidate_arms]
        return validate_fixed_process_items(items, context)

    # 每一步都要和**当前**状态比，不能和原始状态比 —— 第一版就是拿 `armor_set.rank_key`
    # 当基线，于是"当前已经比任何单件替换都好，但仍然优于原始"时它会继续接受更差的走法
    # （守门测试 `test_no_single_piece_tuning_can_improve_the_result_any_further` 抓到的）。
    current_key = armor_set.rank_key
    steps_used = 0
    for _ in range(max_steps):
        steps_used += 1
        best: tuple[tuple[int, ...], int, TuningChoice] | None = None
        for piece_index, piece in enumerate(pieces):
            for choice in piece.options:
                if choice.is_noop or choice.plug_hash == piece.current.plug_hash:
                    continue
                delta = piece.delta_of(choice)
                # 只试"可能变好"的方向（判据见 docs/plans/SOLVER_OPTIMALITY_PLAN.md P4）：
                # 提升某个优先项 / 把超上限的那项降下来 / 抬高封顶总和。
                # 一个只动非优先且都在区间内的项、又不改封顶总和的走法，
                # 在排序键上不可能有任何一项变好 —— 跳过是**安全**的（不会漏掉改进）。
                raises_priority = any(delta[index] > 0 for index in priority_indices)
                # `ArmorStats` 是 pydantic 模型，`.get()` 只收一个参数 —— 用 `getattr` 取
                # （真机上这一行抛过 TypeError：`get(name, 0)` 多给了默认值）。
                now = [getattr(arms[piece_index].stats, name, 0) for name in STAT_NAMES]
                fixes_cap = any(
                    delta[index] < 0 and maximums[index] > 0 and now[index] > maximums[index]
                    for index in range(6)
                )
                raises_total = any(
                    delta[index] > 0
                    and (maximums[index] == 0 or now[index] < maximums[index])
                    for index in range(6)
                )
                if not (raises_priority or fixes_cap or raises_total):
                    continue
                candidate = list(arms)
                candidate[piece_index] = armor_with_tuning(
                    arms[piece_index], piece, choice
                )
                verified = _evaluate(candidate)
                if verified is None:
                    continue
                if best is None or verified.rank_key > best[0]:
                    best = (verified.rank_key, piece_index, choice)
        if best is None or best[0] <= current_key:
            break
        current_key, piece_index, choice = best
        arms[piece_index] = armor_with_tuning(arms[piece_index], pieces[piece_index], choice)
        moved = piece_tuning(arms[piece_index], manifest)
        if moved is not None:
            pieces[piece_index] = moved

    for index, piece in enumerate(original):
        final_hash = _canon(int(getattr(arms[index], "tuning_mod_hash", 0) or 0))
        if final_hash == piece.current.plug_hash:
            continue
        final_choice = piece.option(final_hash)
        if final_choice is None:  # pragma: no cover - 选项来自同一个目录
            continue
        changes.append(TuningChange(
            item_instance_id=piece.item_instance_id,
            item_name=piece.name,
            slot=piece.slot,
            from_plug=piece.current.plug_hash,
            from_name=piece.current.name,
            to_plug=final_choice.plug_hash,
            to_name=final_choice.name,
            delta=piece.delta_of(final_choice),
            increased=final_choice.increased,
            decreased=final_choice.decreased,
        ))

    exhausted = steps_used >= max_steps
    if not changes:
        return armor_set, TuningPlan(
            feasible=True, reason="现状已经吃满了免费的调谐额度。", exhausted=exhausted
        )

    verified = _evaluate(arms)
    if verified is None:  # pragma: no cover - 每一步都验过，走不到
        return armor_set, TuningPlan(feasible=False, reason="局部调谐优化复核失败。")
    final = ProcessArmorSet(
        armor=arms,
        process_items=list(verified.process_items),
        stats=list(verified.stats),
        bonus_stats=list(verified.bonus_stats),
        stat_mods=list(verified.stat_mods),
        stat_mod_assignments=dict(verified.stat_mod_assignments),
        rank_key=verified.rank_key,
        power=armor_set.power,
    )
    delta = [0] * len(STAT_NAMES)
    for change in changes:
        for index, value in enumerate(change.delta):
            delta[index] += value
    return final, TuningPlan(
        feasible=True,
        changes=tuple(changes),
        final_delta=tuple(delta),
        exhausted=exhausted,
    )


def improve_pool_with_local_tuning(
    armor_sets: Sequence[Any],
    context: Any,
    manifest: Any,
    *,
    max_steps: int = LOCAL_TUNING_MAX_STEPS,
) -> list[tuple[Any, TuningPlan]]:
    """对一池候选逐个做局部调谐提升，返回 `[(提升后的集合, 计划)]`（顺序不变）。

    只在**有解**时跑。判据与补救路径相同（`validate_fixed_process_items`），
    但成本是"池子大小 × 单件选项"，不是"放宽目标再解一遍 + 逐套复核"。
    """
    return [
        local_tuning_improvement(armor_set, context, manifest, max_steps=max_steps)
        for armor_set in armor_sets
    ]


def tuning_headroom(pieces: Iterable[PieceTuning]) -> dict[str, int]:
    """每项最多能靠调谐加多少点（把每件能给出的最大正增益加起来）。"""
    headroom = {name: 0 for name in STAT_NAMES}
    for piece in pieces:
        if not piece.movable:
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
    #: 局部贪心是**跑到步数上限**停的，不是"没有改进了"停的 —— 也就是"没吃干净"。
    #: 真机上这不是理论问题：默认上限 12 时，合成夹具里第 13 步本来还能再涨 3 点
    #: （`tests/test_build_local_tuning.py` 的局部最优不变量抓到的）。默认上限已按
    #: "实测收敛点 20 步"调到 24，但**碰上限必须说出来**，不能默默少给。
    exhausted: bool = False
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
            "exhausted": self.exhausted,
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
    usable = [piece for piece in pieces if piece.movable]
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

    # 平衡调整只在缺口 ≤1（它给最低那三项各 +1，单项最多 +1）时才有意义，其余情况不入选，
    # 免得白翻。这是**粗筛**：真的够不够由下面的 `helps`（按件现算的 delta）说了算。
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
        # 这里**没有**"任何一项都不许变成负数"的硬规则（2026-09-22 真机纠正）：
        # 游戏允许把 −5 打在基础值为 0 的项上（光芒领主手套 `…631525` 的 304 就是
        # 生命 −5），所以"扣成负数"是合法走法，只按**目标**判合格与否（`_meets`）。
        # 以前那条规则会把这类走法全部判死，于是"牺牲一项没用的属性去补目标"永远出不来。
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
    "armor_with_tuning",
    "improve_pool_with_local_tuning",
    "local_tuning_improvement",
    "tuning_headroom",
]
