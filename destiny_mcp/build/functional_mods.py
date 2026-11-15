"""社区配装里**照抄**的部位功能模组（功能模组不进求解器）。

口径（2026-09-25 用户拍板）：功能模组是**流派取向**（抗性/搜寻/回收/吸引），算不出来、也不需要算——
照抄作者写的那几颗就行。求解器只该知道一件事："这些槽会被占、这些能量别动"，好把属性模组放进
**剩下的**能量里（它以为整件护甲的能量都能用，就会规划出装不下的方案——2026-09-22 那个 12/11）。

所以这里只做三件确定的事，不确定的一律不猜：

1. **名字 → 插件 hash**：同名多版本**全留着**（实测「鼓舞爆弹」有 2 点与 1 点两版，只有一版在这一位
   角色的可插入清单里），挑哪版交给执行器按现场数据决定；
2. **归位到部位**：靠 Manifest 的护甲模组类别（`get_armor_mods` 的 `slot`），不靠名字猜；属性模组
   （`slot="general"`）**不算功能模组**——那半边必须由求解器给，照抄会与六维目标打架；
3. **算预留能量**：取同名版本里**最贵**的那颗。执行器挑的一定不比它贵（"能插"优先于"便宜"），
   宁可多留不可少留：少留的后果是属性模组装不下 → 整条配装预检失败并回退，多留只是少用一点能量。

对不上名的如实进 `unresolved`（实测社区模板里「回收利用」在 Manifest 里就对不上），不替换、不省略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..exceptions import describe_exception

#: 属性模组的部位键（`get_armor_mods` 的 `slot`）：它要被求解，不能照抄。
STAT_MOD_SLOT = "general"
#: `get_armor_mods` 认不出部位时给的兜底值。
UNKNOWN_SLOT = "unknown"


@dataclass(frozen=True)
class FunctionalMod:
    """一颗照抄来的功能模组：认到部位、认到同名版本、算出预留能量。"""

    slot: str
    name: str
    variants: tuple[int, ...]
    energy_cost: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "name": self.name,
            "variants": list(self.variants),
            "energy_cost": self.energy_cost,
        }


@dataclass(frozen=True)
class FunctionalModPlan:
    """一次求解要照抄的功能模组集合（`mods` 有序、允许重复，如两颗「火力无限」）。"""

    mods: tuple[FunctionalMod, ...] = ()
    unresolved: tuple[dict[str, Any], ...] = ()

    def energy_by_slot(self) -> dict[str, int]:
        """每个部位要被功能模组占掉的能量（预留额度，求解器只能用剩下的）。"""
        out: dict[str, int] = {}
        for mod in self.mods:
            out[mod.slot] = out.get(mod.slot, 0) + mod.energy_cost
        return out

    def groups_by_slot(self) -> dict[str, list[list[int]]]:
        """每个部位要装的插件组（一组 = 同名版本，执行器按现场数据挑一版）。"""
        out: dict[str, list[list[int]]] = {}
        for mod in self.mods:
            out.setdefault(mod.slot, []).append(list(mod.variants))
        return out

    def as_payload(self) -> dict[str, Any]:
        return {
            "source": "copied_from_template",
            "mods": [mod.as_payload() for mod in self.mods],
            "energy_by_slot": self.energy_by_slot(),
            "unresolved": list(self.unresolved),
            "note": (
                "功能模组是照抄配装作者写的（流派取向），不是求解出来的；它们占掉的能量已经从"
                "六维求解里扣掉。插不进这一位角色、或能量不够的会**跳过并点名**，不会让整条配装失败。"
            ),
        }


def _split_entry(entry: str) -> tuple[str, str]:
    """`部位:名字` 的可选前缀。

    社区模板按部位给模组，本来就不需要前缀；用户手写一长串时（或同名模组跨部位时）用得上。
    """
    for sep in (":", "："):
        head, found, tail = entry.partition(sep)
        if found and head.strip() and tail.strip():
            return head.strip(), tail.strip()
    return "", entry.strip()


def resolve_functional_mods(manifest: Any, entries: Iterable[str]) -> FunctionalModPlan:
    """把"照抄清单"（`["充沛", "helmet:特殊武器弹药搜寻者", …]`）解析成可执行的计划。

    认不出来的一律进 `unresolved` 并带上原因：调用方要能对玩家说清"哪一颗没抄上、为什么"。
    """
    mods: list[FunctionalMod] = []
    unresolved: list[dict[str, Any]] = []
    cache: dict[str, list[dict]] = {}

    def rows_for(slot_hint: str) -> list[dict]:
        if slot_hint not in cache:
            cache[slot_hint] = list(manifest.get_armor_mods(slot=slot_hint))
        return cache[slot_hint]

    for raw in entries:
        entry = str(raw or "").strip()
        if not entry:
            continue
        slot_hint, name = _split_entry(entry)
        try:
            rows = rows_for(slot_hint)
        except Exception as exc:  # noqa: BLE001 —— 部位词不认识只该让这一条落空，不该让整次求解挂掉
            unresolved.append({"entry": entry, "reason": describe_exception(exc)})
            continue
        matched = [row for row in rows if str(row.get("name") or "") == name]
        functional = [
            row
            for row in matched
            if str(row.get("slot") or "") not in ("", STAT_MOD_SLOT, UNKNOWN_SLOT)
        ]
        if not functional:
            unresolved.append({
                "entry": entry,
                "reason": (
                    "Manifest 里没有同名的部位功能模组（属性模组不照抄 —— 那半边由六维求解给）"
                    if matched
                    else "Manifest 里找不到这个名字"
                ),
            })
            continue
        slots = sorted({str(row["slot"]) for row in functional})
        if len(slots) > 1:
            unresolved.append({
                "entry": entry,
                "reason": f"同名模组跨多个部位（{'/'.join(slots)}），请写成「部位:名字」",
            })
            continue
        mods.append(FunctionalMod(
            slot=slots[0],
            name=name,
            variants=tuple(sorted({int(row["hash"]) for row in functional})),
            energy_cost=max(int(row.get("energy_cost") or 0) for row in functional),
        ))

    return FunctionalModPlan(tuple(mods), tuple(unresolved))


def functional_mods_from_template(build: dict) -> list[str]:
    """社区模板的 `armor.mods` → 照抄清单（按部位写成 `部位:名字`，保持作者给的顺序与重复）。"""
    entries: list[str] = []
    for slot, names in (build.get("armor", {}).get("mods") or {}).items():
        for name in names or []:
            entries.append(f"{slot}:{name}")
    return entries
