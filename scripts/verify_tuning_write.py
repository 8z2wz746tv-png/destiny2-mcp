"""真机验证：护甲上的**调谐**（tuning）能不能通过 API 换。

存在的理由：`inventory_assistant(intent="equip_mod")` 对调谐一直回
「本项目**还没验证过**调谐写入，请进游戏手动改」，而更早的说法「Bungie 只允许游戏内改
（实测 1663）」已经被 ADR-012 推翻 —— 那个 1663 是字段名 bug 造成的，不是"调谐不可写"的证据。
`scripts/verify_armor_mod_sockets.py` 的 6/6 验证只覆盖 `enhancements.v2_*` 六类插槽，
**调谐是另一个 plug set**（`TUNING_PLUG_SET_HASH`），没被那次验证覆盖。这个脚本补上这一格。

用法：

    # 只读：列出身上每件护甲的调谐槽现状、可换成哪些调谐（含"这一位解锁了没"）
    .venv/bin/python scripts/verify_tuning_write.py warlock

    # 写入往返：挑一件，换一颗别的调谐 → 回读 → 换回原样
    .venv/bin/python scripts/verify_tuning_write.py warlock --apply
    .venv/bin/python scripts/verify_tuning_write.py warlock --apply --piece 6917530188460608169

    # 指定换成哪一颗（用来分辨"要材料"与"这颗你没拥有"：指一颗你身上正装着的）
    .venv/bin/python scripts/verify_tuning_write.py warlock --apply --to 3122197216

判定口径（三条全过才算"可写"）：
1. 写入返回 `ErrorCode == 1`；
2. 回读**插槽 plug** 真的变成目标 hash；
3. 回读**组件 304 的六维**的变化 == 两颗调谐 `investmentStats` 之差（只改一个插槽却让六维
   动了别的数，说明我们对这颗插槽的理解是错的，不能只信插槽回读）；
4. 换回原 plug 也成功，插槽与六维都回到初始值。

写入都是**同一个插槽的一换一**、并且要求能量不增；失败时如实打印上游 `ErrorCode` 与 `Message`
（这就是这个脚本要带回来的证据）。任何一步失败都会把"换回"走完，不把账号留在中间态。
"""

from __future__ import annotations

import asyncio
import sys

from destiny_mcp.build.tuning import EMPTY_TUNING_PLUG_HASH, TUNING_PLUG_SET_HASH
from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.player_resolver import PlayerResolver
from destiny_mcp.services import profile_components, write_readback
from destiny_mcp.services.armor_mod_service import ArmorModService
from destiny_mcp.services.armor_payload import STAT_HASH_TO_KEY, stats_from_component
from destiny_mcp.tools._helpers import resolve_player_name

BUCKET_TO_KEY = {
    3448274439: "helmet",
    3551918588: "gauntlets",
    14239492: "chest",
    20886954: "legs",
    1585787867: "class_item",
}

#: 调谐插槽的 plug 类别（`core.gear_systems.armor_tiering.plugs.tuning.mods`）。
TUNING_CATEGORY_HASH = 3481777685


class Probe:
    def __init__(self, service: ArmorModService, manifest: ManifestManager) -> None:
        self.svc = service
        self.manifest = manifest
        self.insertable: dict[int, set[int]] = {}

    # ── Manifest 小工具 ───────────────────────────────────────────────

    def _definition(self, plug_hash: int) -> dict:
        return self.manifest.get_item_definition(plug_hash) or {}

    def _name(self, plug_hash: int) -> str:
        if plug_hash == EMPTY_TUNING_PLUG_HASH:
            return "空调整模组插槽"
        name = (self._definition(plug_hash).get("displayProperties") or {}).get("name", "")
        return name or f"<{plug_hash}>"

    def _delta(self, plug_hash: int) -> dict[str, int]:
        """这颗调谐对六维的影响（Manifest `investmentStats`）。"""
        out: dict[str, int] = {}
        for entry in self._definition(plug_hash).get("investmentStats") or []:
            key = STAT_HASH_TO_KEY.get(int(entry.get("statTypeHash", 0) or 0))
            value = int(entry.get("value", 0) or 0)
            if key and value:
                out[key] = out.get(key, 0) + value
        return out

    def _split(self, plug_hash: int) -> tuple[str, str]:
        """这颗调谐"加哪一项、减哪一项"（平衡调整与空插槽返回空串）。"""
        delta = self._delta(plug_hash)
        up = max(delta.items(), key=lambda kv: kv[1])[0] if delta else ""
        down = min(delta.items(), key=lambda kv: kv[1])[0] if delta else ""
        return (up, down) if up != down else ("", "")

    def _cost(self, plug_hash: int) -> int:
        return self.svc._plug_energy_cost(plug_hash) or 0

    def is_insertable(self, plug_set_hash: int, plug_hash: int) -> bool | None:
        """这一位玩家现在能不能插这颗 —— None = 上游没给这个 plug set。"""
        known = self.insertable.get(int(plug_set_hash))
        return None if known is None else plug_hash in known

    # ── 取账号状态 ────────────────────────────────────────────────────

    async def load(self, player: str, character: str) -> dict:
        resolver = self.svc._resolver
        resolved = await resolver.resolve_player(player)
        mid, mtype = resolved["membership_id"], resolved["membership_type"]
        char_id = await resolver.resolve_character_id(mid, mtype, character)
        profile = await resolver.get_profile(
            mid, mtype, profile_components.ARMOR_SNAPSHOT
        )
        self.insertable = {}
        for key, scope in (("profilePlugSets", None), ("characterPlugSets", char_id)):
            data = (profile.get(key) or {}).get("data") or {}
            if scope is not None:
                data = data.get(char_id) or {}
            for plug_set_hash, items in (data.get("plugs") or {}).items():
                self.insertable.setdefault(int(plug_set_hash), set()).update(
                    int((row or {}).get("plugItemHash", 0)) for row in (items or [])
                )
        components = profile.get("itemComponents") or {}
        equipment = (
            ((profile.get("characterEquipment") or {}).get("data") or {})
            .get(char_id, {})
            .get("items")
            or []
        )
        return {
            "membership_id": mid,
            "membership_type": mtype,
            "character_id": char_id,
            "instances": (components.get("instances") or {}).get("data") or {},
            "sockets": (components.get("sockets") or {}).get("data") or {},
            "stats": (components.get("stats") or {}).get("data") or {},
            "equipment": equipment,
        }

    def tuning_pieces(self, state: dict) -> list[dict]:
        """身上每件护甲 → 它的调谐槽（有就列出来）。"""
        out = []
        for item in state["equipment"]:
            item_hash = int(item["itemHash"])
            definition = self._definition(item_hash)
            if definition.get("itemType") != 2:
                continue
            bucket = (definition.get("inventory") or {}).get("bucketTypeHash", 0)
            slot_key = BUCKET_TO_KEY.get(bucket)
            if not slot_key:
                continue
            inst_id = str(item["itemInstanceId"])
            sockets = (state["sockets"].get(inst_id) or {}).get("sockets") or []
            entries = (definition.get("sockets") or {}).get("socketEntries", [])
            index = None
            for i, entry in enumerate(entries):
                if int(entry.get("reusablePlugSetHash", 0) or 0) == TUNING_PLUG_SET_HASH:
                    index = i
                    break
            if index is None:
                continue
            current = int((sockets[index] if index < len(sockets) else {}).get(
                "plugHash", 0) or 0)
            if not current:
                current = int(entries[index].get("singleInitialItemHash", 0) or 0)
            out.append({
                "instance_id": inst_id,
                "item_hash": item_hash,
                "name": definition["displayProperties"]["name"],
                "slot": slot_key,
                "index": index,
                "current": current,
                "energy": (state["instances"].get(inst_id) or {}).get("energy") or {},
                "stats": self.stats(state, inst_id),
            })
        return out

    # ── 读六维与插槽 ──────────────────────────────────────────────────

    def stats(self, state: dict, instance_id: str) -> dict[str, int]:
        raw = (state["stats"].get(instance_id) or {}).get("stats") or {}
        return stats_from_component(raw)

    async def read_live(
        self, state: dict, instance_id: str, index: int
    ) -> tuple[int, dict[str, int]]:
        """实时读一次：这个插槽的 plug + 组件 304 的六维。"""
        profile = await self.svc._resolver.get_profile(
            state["membership_id"],
            state["membership_type"],
            profile_components.ARMOR_SNAPSHOT,
        )
        components = profile.get("itemComponents") or {}
        sockets = ((components.get("sockets") or {}).get("data") or {}).get(
            instance_id, {}
        ).get("sockets") or []
        raw_stats = ((components.get("stats") or {}).get("data") or {}).get(
            instance_id, {}
        ).get("stats") or {}
        return (
            int((sockets[index] if index < len(sockets) else {}).get("plugHash", 0) or 0),
            stats_from_component(raw_stats),
        )

    # ── 挑一个"已解锁、能量不增"的另一颗调谐 ─────────────────────────

    def candidates(self, piece: dict) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
        plug_set = self.manifest.get_definition(
            "DestinyPlugSetDefinition", TUNING_PLUG_SET_HASH
        ) or {}
        current_cost = self._cost(piece["current"])
        unlocked: list[tuple[int, str]] = []
        locked: list[tuple[int, str]] = []
        for row in plug_set.get("reusablePlugItems", []):
            plug_hash = int(row.get("plugItemHash", 0) or 0)
            if not plug_hash or plug_hash == piece["current"]:
                continue
            if plug_hash == EMPTY_TUNING_PLUG_HASH:
                continue  # 撤掉调谐是另一件事，见 --include-empty
            if self._cost(plug_hash) > current_cost:
                continue
            entry = (unlocked if self.is_insertable(TUNING_PLUG_SET_HASH, plug_hash)
                     else locked)
            entry.append((plug_hash, self._name(plug_hash)))
        # 挑目标时**优先挑"减的那一项与现装相同"的**：这样减项前后不变，六维对账里就
        # 不含"−5 打在 0 上"的夹断歧义（那个歧义是另一件事，见 ARMOR_FORMAT_PLAN 第 10 条）。
        _, current_down = self._split(piece["current"])
        key = lambda row: (self._split(row[0])[1] != current_down, row[0])  # noqa: E731
        return sorted(unlocked, key=key), sorted(locked, key=key)

    # ── 写 ────────────────────────────────────────────────────────────

    async def write(self, state: dict, piece: dict, plug_hash: int) -> dict:
        result = await self.svc._insert_armor_mod(
            piece["instance_id"],
            plug_hash,
            piece["index"],
            state["character_id"],
            state["membership_type"],
        )
        code = int((result or {}).get("ErrorCode", 0) or 0) if isinstance(result, dict) else 0
        return {
            "ok": code == 1,
            # 1679 `DestinySocketAlreadyHasPlug`：槽里已经是这颗 —— **不是失败**，是"没得改"。
            # 实测：正向写被 1675 拒了之后，"换回"就是回这个码，说明账号其实一个字节没变。
            # 第一版把它当"换回失败、账号停在新值"报了出来，纯属喊狼来了。
            "already": code == 1679,
            "response": result,
        }

    async def readback(
        self, state: dict, instance_id: str, index: int, expect: int
    ) -> tuple[int, dict]:
        async def read() -> int:
            plug, _ = await self.read_live(state, instance_id, index)
            return plug

        plug = await write_readback.read_until(read, lambda value: value == expect)
        _, stats = await self.read_live(state, instance_id, index)
        return plug, stats


def fmt_stats(stats: dict[str, int]) -> str:
    order = ("weapons", "health", "class_stat", "grenade", "super_stat", "melee")
    labels = {
        "weapons": "武器", "health": "生命", "class_stat": "职业",
        "grenade": "手雷", "super_stat": "超能", "melee": "近战",
    }
    return " ".join(f"{labels[k]}{stats.get(k, 0)}" for k in order)


def fmt_delta(delta: dict[str, int]) -> str:
    labels = {
        "weapons": "武器", "health": "生命", "class_stat": "职业",
        "grenade": "手雷", "super_stat": "超能", "melee": "近战",
    }
    if not delta:
        return "（无属性影响）"
    return " ".join(f"{labels.get(k, k)}{v:+d}" for k, v in delta.items())


def stat_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    character = sys.argv[1]
    apply_write = "--apply" in sys.argv
    include_empty = "--include-empty" in sys.argv
    want_piece = ""
    if "--piece" in sys.argv:
        want_piece = sys.argv[sys.argv.index("--piece") + 1]
    want_plug = 0
    if "--to" in sys.argv:
        want_plug = int(sys.argv[sys.argv.index("--to") + 1])
    player = resolve_player_name("")

    manifest = ManifestManager()
    bungie = BungieClient()
    await bungie.start()
    exit_code = 0
    try:
        await manifest.ensure_loaded(bungie)
        service = ArmorModService(bungie, manifest, PlayerResolver(bungie, manifest))
        probe = Probe(service, manifest)
        state = await probe.load(player, character)
        pieces = probe.tuning_pieces(state)

        print(f"玩家 {player} / {character}：身上 {len(pieces)} 件带调谐槽的护甲")
        for piece in pieces:
            unlocked, locked = probe.candidates(piece)
            print("=" * 100)
            print(f"{piece['name']}（{piece['slot']}，{piece['instance_id']}）"
                  f"槽 {piece['index']} 能量 "
                  f"{piece['energy'].get('energyUsed')}/{piece['energy'].get('energyCapacity')}")
            listed = probe.is_insertable(TUNING_PLUG_SET_HASH, piece["current"])
            listed_text = {True: "在", False: "不在", None: "上游没给这份清单"}[listed]
            print(f"  现装调谐: {probe._name(piece['current'])}（{piece['current']}）"
                  f" 六维影响 {fmt_delta(probe._delta(piece['current']))}"
                  f" —— 它{listed_text}角色的可插入清单里")
            print(f"  六维: {fmt_stats(piece['stats'])}")
            print(f"  清单内的替代: {len(unlocked)} 颗"
                  + ("".join(f"\n    - {n}（{h}）{fmt_delta(probe._delta(h))}"
                             for h, n in unlocked[:6]) if unlocked else " —— 一颗都没有"))
            if locked:
                print(f"  清单外的替代: {len(locked)} 颗"
                      f"（注意：**现装的那颗也不在清单里**，说明这份清单根本没覆盖调谐槽）")

        if not apply_write:
            print("\n（只读）加 --apply 做一次'换一颗 → 回读 → 换回'的写入往返。")
            return exit_code

        def has_swap(p: dict) -> bool:
            return bool(probe.candidates(p)[0] or probe.candidates(p)[1])

        target = next(
            (p for p in pieces if p["instance_id"] == want_piece), None
        ) if want_piece else next((p for p in pieces if has_swap(p)), None)
        if target is None:
            print("\n没有可做往返的件（要么没指定 --piece，要么一件都没有替代调谐）")
            return 1

        unlocked, locked = probe.candidates(target)
        pool = unlocked or locked or (
            [(EMPTY_TUNING_PLUG_HASH, "空调整模组插槽")] if include_empty else []
        )
        if want_plug:
            # `--to` 指定一颗**具体**的调谐。用来分开两种 1675：
            # ①"这个动作要材料"，②"这颗调谐你没拥有"。指一颗**你身上正装着**的
            # （换到别的件上）就能分辨 —— 如果是②，它应该能成。
            category = probe.svc._plug_category_hash(want_plug)
            if category != TUNING_CATEGORY_HASH:
                print(f"\n--to {want_plug} 不是调谐插槽的 plug（类别 {category}），拒绝")
                return 2
            owner = next(
                (p for p in pieces
                 if p["current"] == want_plug and p["instance_id"] != target["instance_id"]),
                None,
            )
            print(f"\n--to {want_plug}（{probe._name(want_plug)}）"
                  + (f" —— 它现在就装在你的「{owner['name']}」上，属于**你已经拥有**的调谐"
                     if owner else " —— 注意：这件身上没有任何一件正装着它"))
            pool = [(want_plug, probe._name(want_plug))]
        if not pool:
            print(f"\n{target['name']}: 没有替代调谐可用")
            return 1
        plug_hash, plug_name = pool[0]
        outside = not unlocked

        print("\n" + "=" * 100)
        print(f"写入往返：{target['name']} 槽 {target['index']}")
        print(f"  正向: {probe._name(target['current'])}（{target['current']}）"
              f" → {plug_name}（{plug_hash}）")
        print(f"  预期六维变化: {fmt_delta(probe._delta(plug_hash))}"
              f" − {fmt_delta(probe._delta(target['current']))}")
        print(f"  写入前六维: {fmt_stats(target['stats'])}")
        if outside:
            print("  注意：这颗**不在**角色的可插入清单里 —— 清单连现装那颗都没收，"
                  "所以这次写的就是「清单外」的那一档，成与不成都是结论")

        forward = await probe.write(state, target, plug_hash)
        if not forward["ok"]:
            print(f"  **写入被拒**: {forward['response']}")
            # 被拒 ≠ 账号变了。回读一次把这件事说死：没变就什么都不用做，
            # 变了才需要换回（"以为拒了其实写进去了"是必须能看出来的那种事故）。
            now_plug, now_stats = await probe.read_live(
                state, target["instance_id"], target["index"]
            )
            unchanged = now_plug == target["current"] and now_stats == target["stats"]
            print(f"  被拒后回读: plug={probe._name(now_plug)}（{now_plug}）"
                  f" 六维 {fmt_stats(now_stats)} —— "
                  + ("**账号没变，无需换回**" if unchanged
                     else "**账号变了！**需要换回"))
            if unchanged:
                print("\n" + "=" * 100)
                print("结论: 调谐**不能**通过免费接口换：上游回 "
                      f"{int((forward['response'] or {}).get('ErrorCode', 0) or 0)}"
                      "（这个动作要材料，不是免费动作）。账号未被改动。")
                return 1
            exit_code = 1
        else:
            seen, stats_after = await probe.readback(
                state, target["instance_id"], target["index"], plug_hash
            )
            print(f"  写入 OK（ErrorCode=1）；回读 plug = {seen}（{probe._name(seen)}）")
            print(f"  写入后六维: {fmt_stats(stats_after)}")
            observed = stat_diff(target["stats"], stats_after)
            expected = {
                k: probe._delta(plug_hash).get(k, 0) - probe._delta(target["current"]).get(k, 0)
                for k in set(probe._delta(plug_hash)) | set(probe._delta(target["current"]))
            }
            expected = {k: v for k, v in expected.items() if v}
            # 游戏里属性夹在 0（ARMOR_FORMAT_PLAN 第 10 条），但组件 304 会报负数（本脚本
            # 的只读输出里就有"生命-5"）。两种口径都算对，分别打印出来，避免把夹断当成 bug。
            clamped = {
                k: max(0, target["stats"].get(k, 0) + v) - target["stats"].get(k, 0)
                for k, v in expected.items()
            }
            clamped = {k: v for k, v in clamped.items() if v}
            print(f"  实测变化: {fmt_delta(observed)}"
                  f" / 预期(不夹): {fmt_delta(expected)}"
                  f" / 预期(夹 0): {fmt_delta(clamped)}")
            if seen != plug_hash:
                print(f"  **回读不符**: 期望 {plug_hash}，读到 {seen}")
                exit_code = 1
            if observed not in (expected, clamped):
                print("  **六维变化与 Manifest 不符** —— 只信插槽回读会漏掉这个")
                exit_code = 1

        restore = await probe.write(state, target, target["current"])
        if not restore["ok"] and not restore["already"]:
            print(f"  **换回失败，账号停在新值**: {restore['response']}")
            return 1
        back, stats_back = await probe.readback(
            state, target["instance_id"], target["index"], target["current"]
        )
        print(f"  换回 OK；回读 plug = {back}（{probe._name(back)}）")
        print(f"  换回后六维: {fmt_stats(stats_back)}")
        if back != target["current"] or stats_back != target["stats"]:
            print("  **换回后与初始状态不符**")
            exit_code = 1
        else:
            print("  已回到初始状态（插槽与六维都一致）")

        print("\n" + "=" * 100)
        print("结论:", "调谐**可以通过 API 换**（免费接口）" if exit_code == 0
              else "调谐写入**没验成**，看上面的上游错误码")
    finally:
        await bungie.close()
        manifest.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
