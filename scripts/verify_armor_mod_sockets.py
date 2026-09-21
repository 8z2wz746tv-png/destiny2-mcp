"""真机验证：护甲上**每一类**模组插槽能不能通过 API 写。

存在的理由：`build_assistant(intent="equip_build")` 与 `equip_loadout` 只写过"一般护甲模组"
（属性模组），`inventory_assistant(intent="equip_mod")` 到底能不能写部位模组（头盔/手臂/
胸甲/腿甲/职业物品）从来没有真机证据。这个脚本把每一类的读写都走一遍。

用法：

    # 只读：打印每件护甲上所有"玩家可改的模组插槽"，并标出哪些模组**这一位玩家还没解锁**
    .venv/bin/python scripts/verify_armor_mod_sockets.py warlock

    # 写入验证：每类插槽做一次"同类别、能量不增的一换一 → 回读 → 换回"
    .venv/bin/python scripts/verify_armor_mod_sockets.py warlock --apply

写入是可逆的一换一（同类同槽、能量不增），并且写回原值；每步都回读核对。

判定口径（每一类都要三条全过）：
1. `plan` 解析到的 socket 的**实时 plug 类别 == 目标模组类别**（不是"第一个能塞的槽"）；
2. 写入返回 `ErrorCode == 1`，且回读窗口内该槽真的变成目标 plug；
3. 换回原 plug 也成功，回到初始状态。

**可插入清单**：Bungie 在 `characterPlugSets`（组件 207，随 305 一起回来）给出**这一位角色
实际能插入**的 plug —— 它比 Manifest 的 plug set 小得多（实测头盔 21/61、手臂 25/56、
一般 9/24），未解锁（例如"必须在赛季神器中选择"）的模组不在里面。写入被上游回
1676 `DestinyFailedPlugInsertionRules` 的那几颗，全都不在这份清单里。

读的是 `.env` 里的默认玩家（与 MCP 工具同一处口径）。
"""

from __future__ import annotations

import asyncio
import sys

from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.player_resolver import PlayerResolver
from destiny_mcp.services import profile_components, write_readback
from destiny_mcp.services.armor_mod_service import ArmorModService
from destiny_mcp.tools._helpers import resolve_player_name

#: 要验的插槽类别：短键 → `enhancements.*` 标识（一般模组当对照组）。
CATEGORIES = {
    "general": "enhancements.v2_general",
    "helmet": "enhancements.v2_head",
    "gauntlets": "enhancements.v2_arms",
    "chest": "enhancements.v2_chest",
    "legs": "enhancements.v2_legs",
    "class_item": "enhancements.v2_class_item",
}

_SHORT_BY_IDENTIFIER = {identifier: short for short, identifier in CATEGORIES.items()}

BUCKET_TO_KEY = {
    3448274439: "helmet",
    3551918588: "gauntlets",
    14239492: "chest",
    20886954: "legs",
    1585787867: "class_item",
}


def insertion_messages(definition: dict | None) -> list[str]:
    """plug 的插入条件（Manifest 官方中文）：条件不满足时上游回 1676。"""
    rules = ((definition or {}).get("plug") or {}).get("insertionRules") or []
    return [m for m in (rule.get("failureMessage") for rule in rules) if m]


class Probe:
    def __init__(self, service: ArmorModService, manifest: ManifestManager) -> None:
        self.svc = service
        self.manifest = manifest
        self.insertable: dict[int, set[int]] = {}

    # ── 读 ────────────────────────────────────────────────────────────

    def _definition(self, plug_hash: int) -> dict:
        return self.manifest.get_item_definition(plug_hash) or {}

    def _name(self, plug_hash: int) -> str:
        name = (self._definition(plug_hash).get("displayProperties") or {}).get("name", "")
        return name or f"<{plug_hash}>"

    def _category(self, plug_hash: int) -> str:
        return ((self._definition(plug_hash).get("plug") or {}).get(
            "plugCategoryIdentifier")) or ""

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
            mid, mtype, profile_components.INVENTORY_SOCKETS
        )
        # 可插入清单在 profile 级与角色级两处，**都要并**（角色级才是"这一位"的口径）。
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
            "equipment": equipment,
        }

    def pieces(self, state: dict) -> list[dict]:
        """角色身上每件护甲 → 它的"玩家可改模组插槽"清单。"""
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
            mod_sockets = self.svc.read_armor_mod_sockets(
                inst_id, item_hash, state["sockets"]
            )
            entries = (definition.get("sockets") or {}).get("socketEntries", [])
            mods = {}
            for index, plug_hash in sorted(mod_sockets.items()):
                entry = entries[index] if index < len(entries) else {}
                plug_set_hash = entry.get("reusablePlugSetHash") or entry.get(
                    "randomizedPlugSetHash", 0
                )
                mods[index] = {
                    "hash": plug_hash,
                    "name": self._name(plug_hash),
                    "category": self._category(plug_hash),
                    "cost": self._cost(plug_hash),
                    "plug_set_hash": plug_set_hash,
                    "insertable": self.is_insertable(plug_set_hash, plug_hash),
                    "rules": insertion_messages(self._definition(plug_hash)),
                }
            out.append({
                "instance_id": inst_id,
                "item_hash": item_hash,
                "name": definition["displayProperties"]["name"],
                "slot": slot_key,
                "energy": (state["instances"].get(inst_id) or {}).get("energy") or {},
                "socket_entries": entries,
                "mods": mods,
            })
        return out

    # ── 选一个"同槽同类、能量不增、且已解锁"的替代模组 ───────────────

    def _candidate(self, piece: dict, index: int) -> tuple[int, str, bool]:
        """挑替代模组：先要**已解锁**（可插入清单里有），再看能量不增。"""
        current = piece["mods"][index]
        entry = piece["socket_entries"][index]
        plug_set_hash = entry.get("reusablePlugSetHash") or entry.get(
            "randomizedPlugSetHash", 0
        )
        plug_set = self.manifest.get_definition(
            "DestinyPlugSetDefinition", plug_set_hash
        ) or {}
        unlocked: list[tuple[int, str]] = []
        locked: list[tuple[int, str]] = []
        for row in plug_set.get("reusablePlugItems", []):
            plug_hash = int(row.get("plugItemHash", 0) or 0)
            if not plug_hash or plug_hash == current["hash"]:
                continue
            if self._category(plug_hash) != current["category"]:
                continue
            if self._cost(plug_hash) > current["cost"]:
                continue
            name = self._name(plug_hash)
            if not name or "空" in name:
                continue
            if self.is_insertable(plug_set_hash, plug_hash):
                unlocked.append((plug_hash, name))
            else:
                locked.append((plug_hash, name))
        pool = unlocked or locked
        if not pool:
            return 0, "", False
        plug_hash, name = sorted(pool)[0]
        return plug_hash, name, bool(unlocked)

    # ── 写 ────────────────────────────────────────────────────────────

    async def write(self, state: dict, piece: dict, index: int, plug_hash: int) -> dict:
        result = await self.svc._insert_armor_mod(
            piece["instance_id"],
            plug_hash,
            index,
            state["character_id"],
            state["membership_type"],
        )
        if not isinstance(result, dict) or result.get("ErrorCode") != 1:
            return {"ok": False, "response": result}
        return {"ok": True, "response": result}

    async def readback(self, state: dict, instance_id: str, index: int, expect: int) -> int:
        async def read() -> int:
            profile = await self.svc._resolver.get_profile(
                state["membership_id"],
                state["membership_type"],
                profile_components.INVENTORY_SOCKETS,
            )
            sockets = (
                ((profile.get("itemComponents") or {}).get("sockets") or {})
                .get("data", {})
                .get(instance_id, {})
                .get("sockets", [])
            )
            return int((sockets[index] if index < len(sockets) else {}).get("plugHash", 0) or 0)

        return await write_readback.read_until(read, lambda value: value == expect)


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    character = sys.argv[1]
    apply_writes = "--apply" in sys.argv
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
        pieces = probe.pieces(state)

        covered: set[str] = set()
        for piece in pieces:
            print("=" * 100)
            energy = piece["energy"]
            print(f"{piece['name']}（{piece['slot']}）能量 "
                  f"{energy.get('energyUsed')}/{energy.get('energyCapacity')}")
            for index, mod in piece["mods"].items():
                short = _SHORT_BY_IDENTIFIER.get(mod["category"], mod["category"] or "?")
                covered.add(short)
                flag = {True: "已解锁", False: "未解锁", None: "无清单"}[mod["insertable"]]
                rules = ("  插入条件: " + " / ".join(mod["rules"])) if mod["rules"] else ""
                print(f"  槽 {index:2d} [{short:10s}] {mod['name']:16s} "
                      f"(hash={mod['hash']} 能量={mod['cost']}) {flag}{rules}")

        print("\n" + "=" * 100)
        print("已覆盖的插槽类别:", ", ".join(sorted(covered)))
        missing = set(CATEGORIES) - covered
        if missing:
            print("**这具角色身上没出现的类别**:", ", ".join(sorted(missing)))

        if not apply_writes:
            print("\n（只读）加 --apply 做每类的写入往返验证。")
            return exit_code

        print("\n" + "=" * 100)
        print("写入往返验证：每类取一个槽，换成同类同能量以内、且**已解锁**的另一个，回读，再换回")
        tested: dict[str, bool] = {}
        for piece in pieces:
            for index, mod in piece["mods"].items():
                short = _SHORT_BY_IDENTIFIER.get(mod["category"])
                if short is None or short in tested:
                    continue
                candidate = probe._candidate(piece, index)
                label = f"{piece['name']} 槽 {index} [{short}]"
                if not candidate[0]:
                    print(f"\n-- {label}: 没找到同类同能量的替代模组，跳过")
                    tested[short] = False
                    exit_code = 1
                    continue
                plug_hash, plug_name, unlocked = candidate
                note = "" if unlocked else "（注意：这是未解锁的那一档，预期会失败）"
                print(f"\n-- {label}: {mod['name']} → {plug_name}（{plug_hash}）{note}")
                forward = await probe.write(state, piece, index, plug_hash)
                if not forward["ok"]:
                    print(f"   写入失败: {forward['response']}")
                    tested[short] = False
                    exit_code = 1
                    continue
                seen = await probe.readback(state, piece["instance_id"], index, plug_hash)
                if seen != plug_hash:
                    print(f"   回读不符：期望 {plug_hash}({plug_name})，"
                          f"读到 {seen}({probe._name(seen)})")
                    tested[short] = False
                    exit_code = 1
                else:
                    print(f"   写入 OK，回读 = {probe._name(seen)}")
                restore = await probe.write(state, piece, index, mod["hash"])
                if not restore["ok"]:
                    print(f"   换回失败（账号停在新值）: {restore['response']}")
                    exit_code = 1
                    continue
                back = await probe.readback(state, piece["instance_id"], index, mod["hash"])
                if back != mod["hash"]:
                    print(f"   换回后回读不符：期望 {mod['hash']}，读到 {back}")
                    exit_code = 1
                else:
                    print(f"   换回 OK，回读 = {probe._name(back)}")
                tested.setdefault(short, True)

        print("\n" + "=" * 100)
        print("按插槽类别的结论：")
        for short in CATEGORIES:
            if short in tested:
                print(f"  {short:10s}: {'可写' if tested[short] else '没验成'}")
            else:
                print(f"  {short:10s}: 该角色身上没有这种槽，未验")
    finally:
        await bungie.close()
        manifest.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
