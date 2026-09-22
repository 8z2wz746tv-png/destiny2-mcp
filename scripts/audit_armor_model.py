"""口径审计（只读）：把游戏自己的组件数据和**我们模型算出来的数**逐件对账。

存在的理由：2026-09-22 用户连着发现三个"数据口径"错（调谐的 −5 被夹成 0、每件的可调谐属性
被当成全局 32 颗、大师加成对认不出的档位照补 +5×3）。三次都不是算法错，而是**模型眼里的护甲
不是游戏里的护甲** —— 而我一直只看输出、没审输入。这个脚本就是那次审计。

它做的是**独立对账**：期望值用组件原文 + `armor_rules` 里声明的规则**另算一遍**，
不复用 `InventorySnapshot.from_profile` 的解析路径（否则是自己证明自己）。对不上的地方
连原样数据一起打出来，方便照着它写守门测试。

用法：

    .venv/bin/python scripts/audit_armor_model.py                 # 全账号护甲
    .venv/bin/python scripts/audit_armor_model.py --limit 20      # 只看前 20 件
    .venv/bin/python scripts/audit_armor_model.py --verbose       # 每件都打一行

**只读**：只读账号（背包/已装备/组件 300/304/305/310），不写任何东西。
退出码：0 = 没有对不上的；1 = 有（可以当机器检查用）。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from destiny_mcp.build.constants import STAT_NAMES
from destiny_mcp.build.models import InventorySnapshot
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.player_resolver import PlayerResolver
from destiny_mcp.services import profile_components
from destiny_mcp.tools._helpers import resolve_player_name

#: 六维 statTypeHash → 名字（组件 304 用的就是这套键）。
STAT_HASH = {
    2996146975: "weapons", 392767087: "health", 1943323491: "class_stat",
    1735777505: "grenade", 144602215: "super_stat", 4244567218: "melee",
}
LABEL = {"weapons": "武器", "health": "生命", "class_stat": "职业",
         "grenade": "手雷", "super_stat": "超能", "melee": "近战"}
TUNING_CATEGORY = 3481777685          # core.gear_systems.armor_tiering.plugs.tuning.mods
MASTERWORK_CATEGORY = "v460.plugs.armor.masterworks"
DIRECTIONAL_TUNING_BONUS = 5          # = armor_rules.DIRECTIONAL_TUNING_STAT_BONUS
MOD_CATEGORY_HASHES = {
    2487827355,  # v2_general（属性模组）
    2323986101,  # v2_general 的另一个变体（artifice 走同一个家族）
}


def _fmt(values: dict[str, int]) -> str:
    return " ".join(f"{LABEL[k]}{values.get(k, 0)}" for k in STAT_NAMES)


class Audit:
    def __init__(self, manifest: ManifestManager) -> None:
        self.manifest = manifest
        self.problems: list[str] = []
        self.evidence: list[str] = []
        self.counts: dict[str, int] = {}
        self.checked = 0

    def note(self, kind: str, detail: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.problems.append(f"[{kind}] {detail}")

    # ── 组件原文小工具 ────────────────────────────────────────────────

    def _def(self, item_hash: int) -> dict:
        return self.manifest.get_item_definition(item_hash) or {}

    def _plug_stats(self, plug_hash: int) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._def(plug_hash).get("investmentStats") or []:
            key = STAT_HASH.get(int(entry.get("statTypeHash", 0) or 0))
            value = int(entry.get("value", 0) or 0)
            if key and value:
                out[key] = out.get(key, 0) + value
        return out

    def _category(self, plug_hash: int) -> str:
        return str((self._def(plug_hash).get("plug") or {}).get("plugCategoryIdentifier") or "")

    # ── 一件一件对 ────────────────────────────────────────────────────

    def check_piece(self, armor: Any, raw: dict) -> None:
        self.checked += 1
        inst = armor.item_instance_id
        name = f"{armor.name}({inst[-6:]})"
        stats_304 = {
            STAT_HASH[int(h)]: int((v or {}).get("value", 0) or 0)
            for h, v in (raw.get("stats") or {}).items()
            if int(h) in STAT_HASH
        }
        sockets = raw.get("sockets") or []
        instances = raw.get("instances") or {}
        reusable = raw.get("reusable") or {}

        # ① 我们模型里的 stats 必须 = 304 − 已装的属性/工匠模组
        mod_delta: dict[str, int] = {}
        installed: list[tuple[int, str]] = []
        for socket in sockets:
            plug_hash = int(socket.get("plugHash", 0) or 0)
            if not plug_hash:
                continue
            category = self._category(plug_hash)
            if category.startswith("enhancements.") or "artifice" in category:
                installed.append((plug_hash, category))
                for key, value in self._plug_stats(plug_hash).items():
                    mod_delta[key] = mod_delta.get(key, 0) + value
        expected = {k: stats_304.get(k, 0) - mod_delta.get(k, 0) for k in STAT_NAMES}
        model_stats = {k: int(getattr(armor.stats, k, 0) or 0) for k in STAT_NAMES}
        if expected != model_stats:
            self.note("stats", f"{name} 304−模组={_fmt(expected)} 而模型={_fmt(model_stats)}")

        # legacy 护甲没有 T 级、也没有调谐槽：词条反推与调谐这两块**天生不适用**，
        # 单独计数，不当成"对不上"。
        if getattr(armor, "armor_system", "armor_3") != "armor_3":
            self.counts["legacy_skipped"] = self.counts.get("legacy_skipped", 0) + 1
            return

        # ② 调谐：装的那颗 vs 组件 310 给的清单（**逐件**，不是全局 32 颗）
        tuning_socket = None
        for index, socket in enumerate(sockets):
            plug_hash = int(socket.get("plugHash", 0) or 0)
            if plug_hash and self._category(plug_hash) and "tuning" in self._category(plug_hash):
                tuning_socket = index
                break
        legal: set[int] = set()
        for socket_index, rows in (reusable.get("plugs") or {}).items():
            hashes = {int((r or {}).get("plugItemHash", 0) or 0) for r in (rows or [])}
            if any("tuning" in self._category(h) for h in hashes if h):
                legal = hashes
                if tuning_socket is not None and str(tuning_socket) != str(socket_index):
                    self.note("tuning_socket",
                              f"{name} 调谐槽判定不一致：305 说是 {tuning_socket}，310 说是 {socket_index}")
                break
        model_legal = {int(h) for h in (getattr(armor, "tuning_option_hashes", ()) or ())}
        if legal and model_legal != legal:
            self.note("tuning_options",
                      f"{name} 310 给 {len(legal)} 颗，模型存了 {len(model_legal)} 颗"
                      f"（差 {len(legal ^ model_legal)} 颗）")
        if not legal:
            if tuning_socket is None:
                # 本来就没有调谐槽（legacy / 低 T 级）—— 不适用，不算问题
                self.counts["no_tuning_socket"] = self.counts.get("no_tuning_socket", 0) + 1
            else:
                self.note("tuning_no_list",
                          f"{name} **有调谐槽**（305 槽{tuning_socket}）但 310 没给清单")

        # ③ 装的调谐必须能加它在 310 里对应的那一项
        current = int(getattr(armor, "tuning_mod_hash", 0) or 0)
        if current:
            current_stats = self._plug_stats(current)
            increased = {k for k, v in current_stats.items() if v > 0}
            ups_in_legal = {
                k for h in legal for k, v in self._plug_stats(h).items() if v > 0
            }
            if increased and ups_in_legal and not increased <= ups_in_legal:
                self.note("tuning_installed_mismatch",
                          f"{name} 装着 {self._def(current).get('displayProperties', {}).get('name')}"
                          f"（加 {increased}），但 310 只允许加 {ups_in_legal}")

        # ④ 调谐反推：base + 当前调谐增量 必须还原 304−模组（不许夹 0）
        tuning_delta = self._plug_stats(current) if current else {}
        if tuning_delta and set(tuning_delta) == set(STAT_NAMES) and set(tuning_delta.values()) == {1}:
            # 平衡调整：插件**声明**六维各 +1，真机规则是"**最低那三项**各 +1"
            # （2026-09-22 实测）。审计这里也踩过同一个坑 —— 用声明值去重建就永远差 3 点。
            snapshot = {
                k: expected.get(k, 0) - 1 for k in STAT_NAMES
            }
            lowest = sorted(STAT_NAMES, key=lambda k: (snapshot[k], k))[:3]
            tuning_delta = {k: 1 for k in lowest}
        base = {k: expected.get(k, 0) - tuning_delta.get(k, 0) for k in STAT_NAMES}
        if armor.armor_system == "armor_3" and any(v < 0 for v in base.values()):
            self.note("tuning_inversion_negative",
                      f"{name} 反推出来的基础值有负数 {_fmt(base)}（数据对不上）")

        # ⑤ 大师：认得出"满大师"的档位才许按 +5×3 补
        mw_plug = 0
        for socket in sockets:
            plug_hash = int(socket.get("plugHash", 0) or 0)
            if plug_hash and self._category(plug_hash) == MASTERWORK_CATEGORY:
                mw_plug = plug_hash
                break
        # 真机规则（2026-09-22）：同名「升级护甲」有两种 —— 一种**声明**六维各 +5（真改造过），
        # 另一种只带能量标记。所以判据是"插件声明了什么"，不是"是不是某个特定 hash"。
        declared = max(
            (
                int(e.get("value", 0) or 0)
                for e in self._def(mw_plug).get("investmentStats") or []
                if int(e.get("statTypeHash", 0) or 0) in STAT_HASH
            ),
            default=0,
        )
        if int(getattr(armor, "masterwork_level", 0) or 0) != declared:
            self.note("masterwork_mismatch",
                      f"{name} 大师插件 {mw_plug} 声明档位={declared}，"
                      f"模型 masterwork_level={armor.masterwork_level}")
        if bool(getattr(armor, "is_masterworked", False)) != (declared > 0):
            self.note("masterwork_is_flag",
                      f"{name} 声明档位={declared} 但 is_masterworked="
                      f"{armor.is_masterworked}")

        # ⑥ 词条反推：verified 的件，重建必须等于 304−模组−调谐
        if getattr(armor, "armor3_roll_verified", False):
            roll = getattr(armor, "base_roll_stats", None)
            roll_map = {k: int(getattr(roll, k, 0) or 0) for k in STAT_NAMES} if roll else {}
            zeros = [k for k in STAT_NAMES if roll_map.get(k, 0) == 0]
            rebuilt = dict(roll_map)
            # 大师给"非词条那三项"各 +N，N = **插槽插件声明的档位**（1–5；没改造就是 0）。
            # 不是固定 +5 —— 真机上一大批件只升到 1/3/4 档。
            tier = int(getattr(armor, "masterwork_level", 0) or 0)
            if getattr(armor, "is_masterworked", False):
                for key in zeros:
                    rebuilt[key] = rebuilt.get(key, 0) + tier
            for key, value in tuning_delta.items():
                rebuilt[key] = rebuilt.get(key, 0) + value
            for key, value in mod_delta.items():
                rebuilt[key] = rebuilt.get(key, 0) + value
            if rebuilt != stats_304:
                tuning_name = self._def(current).get("displayProperties", {}).get("name") if current else "空"
                self.note("roll_rebuild",
                          f"{name} 词条重建={_fmt(rebuilt)} 而 304={_fmt(stats_304)}"
                          f" | 词条槽={_fmt(roll_map)} 大师={'有' if armor.is_masterworked else '无'}"
                          f" 调谐={tuning_name}（{tuning_delta}）模组={mod_delta}")
        else:
            roll_sockets = {}
            for socket in sockets:
                plug_hash = int(socket.get("plugHash", 0) or 0)
                if plug_hash and self._category(plug_hash) == "armor_stats":
                    roll_sockets.update(self._plug_stats(plug_hash))
            if armor.gear_tier != 5:
                # 我们**只**反推 T5 的词条（低 T 级的形状规则不同，没建模），这是设计不是错
                self.counts["not_t5"] = self.counts.get("not_t5", 0) + 1
                return
            self.note(
                "roll_unverified",
                f"{name} 词条反推不出来：{armor.roll_parse_error or '（没说原因）'}"
                f" | 词条槽={_fmt(roll_sockets)} 304={_fmt(stats_304)}"
                f" 大师={'有' if armor.is_masterworked else '无'} 原型={armor.archetype_name or '—'}"
                f" T{armor.gear_tier}",
            )

        # ⑦ 其他便宜字段
        energy = (instances.get("energy") or {}).get("energyCapacity")
        if energy is not None and int(getattr(armor, "energy_capacity", -1) or -1) != int(energy):
            self.note("energy", f"{name} 能量 304={energy} 模型={armor.energy_capacity}")

        if any(stats_304.get(k, 0) < 0 for k in STAT_NAMES):
            # 304 会报负数 —— 这是"模型不许夹 0"那条口径的**证据**，不是问题
            self.evidence.append(
                f"{name} 的 304 里有负数："
                + " ".join(f"{LABEL[k]}{stats_304[k]}" for k in STAT_NAMES if stats_304.get(k, 0) < 0)
            )


async def main() -> int:
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    verbose = "--verbose" in sys.argv

    player = resolve_player_name("")
    manifest, bungie = ManifestManager(), BungieClient()
    await bungie.start()
    try:
        await manifest.ensure_loaded(bungie)
        resolver = PlayerResolver(bungie, manifest)
        resolved = await resolver.resolve_player(player)
        profile = await resolver.get_profile(
            resolved["membership_id"], resolved["membership_type"],
            [*profile_components.BUILD_ARMOR, 302],
        )
        snapshot = InventorySnapshot.from_profile(profile, manifest, "")
        components = profile.get("itemComponents") or {}
        raw_stats = (components.get("stats") or {}).get("data") or {}
        raw_sockets = (components.get("sockets") or {}).get("data") or {}
        raw_instances = (components.get("instances") or {}).get("data") or {}
        raw_reusable = (components.get("reusablePlugs") or {}).get("data") or {}

        audit = Audit(manifest)
        pieces = []
        for slot in ("helmets", "gauntlets", "chests", "legs", "class_items"):
            pieces.extend(snapshot.get_slot(slot))
        if limit:
            pieces = pieces[:limit]
        for armor in pieces:
            inst = armor.item_instance_id
            audit.check_piece(armor, {
                "stats": (raw_stats.get(inst) or {}).get("stats") or {},
                "sockets": (raw_sockets.get(inst) or {}).get("sockets") or [],
                "instances": raw_instances.get(inst) or {},
                "reusable": raw_reusable.get(inst) or {},
            })
            if verbose:
                print(f"  {armor.name:16s} {armor.slot:12s} "
                      f"{_fmt({k: int(getattr(armor.stats, k, 0) or 0) for k in STAT_NAMES})}"
                      f"  可调谐={len(getattr(armor, 'tuning_option_hashes', ()) or ())} 颗")

        print(f"\n审计了 {audit.checked} 件护甲")
        print("按设计不适用的:", dict(sorted(audit.counts.items(), key=lambda kv: -kv[1])) or "（无）")
        if audit.evidence:
            print(f"口径证据（{len(audit.evidence)} 条）：")
            for line in audit.evidence[:6]:
                print("  " + line)
        if audit.problems:
            print("\n对不上的地方（最多打 40 条）：")
            for line in audit.problems[:40]:
                print("  " + line)
            return 1
        print("全部对得上 ✓")
        return 0
    finally:
        await bungie.close()
        manifest.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
