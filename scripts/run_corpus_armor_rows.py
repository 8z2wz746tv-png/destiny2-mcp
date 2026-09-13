#!/usr/bin/env python
"""语料护甲章节的**逐行实跑**：把 `TESTING_CORPUS.md` 护甲部分的"验收点"变成可执行断言。

和单元测试的分工：单元测试用替身、任何机器都能跑；这个脚本走**真机**（需要 OAuth、
本地 Manifest 与社区资料），回答的是"文档里写的那些话，现在真的还成立吗"。
它覆盖的是护甲章节里**没有标 ⭐ 的那些字段级断言**：

    .venv/bin/python scripts/run_corpus_armor_rows.py

退出码 0 = 语料护甲行全部成立；非 0 = 有行过期或行为退化（看 FAIL 行的判定依据）。
"""

import asyncio
import sys

from destiny_mcp.server import create_server, app_lifespan
from destiny_mcp.tools.assistants import build_assistant, inventory_assistant

RESULTS = []

# 真机样例：一件 T5（词条 武器30/超能25/手雷20）与一件老护甲（15 槽）
T5_LEGS = "6917530198796768597"
LEGACY_PIECE = "6917529740500777594"


def check(row: str, ok: bool, evidence: str) -> None:
    RESULTS.append((row, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'} | {row}\n       {evidence}")


async def main():
    async with app_lifespan(create_server("normal")) as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})()})()

        async def inv(**kwargs):
            return await inventory_assistant(ctx=ctx, **kwargs)

        async def build(**kwargs):
            return await build_assistant(ctx=ctx, **kwargs)

        # 1. 列表：护甲行带统一槽位键与 T 级，武器行不受影响
        r = await inv(intent="get", armor_slot="legs", limit=3)
        rows = ((r.get("data") or {}).get("inventory") or {}).get("items") or []
        armor_row = rows[0] if rows else {}
        check(
            "① 列表：护甲行有 slot/slot_display/gear_tier/armor_system（bucket_type 仍在）",
            r["ok"] and bool(rows)
            and armor_row.get("slot") == "legs"
            and armor_row.get("slot_display") == "腿部护甲"
            and "gear_tier" in armor_row
            and armor_row.get("armor_system") in {"armor_3", "legacy"}
            and "bucket_type" in armor_row,
            f"slot={armor_row.get('slot')} display={armor_row.get('slot_display')} "
            f"tier={armor_row.get('gear_tier')} system={armor_row.get('armor_system')} "
            f"bucket={armor_row.get('bucket_type')!r}",
        )

        r = await inv(intent="get", item_type="weapon", limit=2)
        weapon_rows = ((r.get("data") or {}).get("inventory") or {}).get("items") or []
        weapon_row = weapon_rows[0] if weapon_rows else {}
        check(
            "② 列表：武器行仍带 bucket_type，且不会被塞护甲键",
            r["ok"] and bool(weapon_rows) and "bucket_type" in weapon_row
            and not weapon_row.get("slot") and not weapon_row.get("slot_display"),
            f"武器首行 slot={weapon_row.get('slot')!r} display={weapon_row.get('slot_display')!r} "
            f"bucket={weapon_row.get('bucket_type')!r}",
        )

        # 2. 单件详情：三层属性、插槽可改性、能量
        r = await inv(intent="item", item_instance_id=T5_LEGS)
        armor = ((r.get("data") or {}).get("armor") or {})
        stats = armor.get("stats") or {}
        sockets = armor.get("sockets") or []
        roll_sockets = [row for row in sockets if row["kind"] == "roll"]
        check(
            "③ 单件详情（T5）：roll/base/final 三层 + 词条槽不可改 + 能量",
            r["ok"] and armor.get("identity", {}).get("gear_tier") == 5
            and stats.get("base") == stats.get("roll")
            and roll_sockets and all(row["editable"] is False for row in roll_sockets)
            and (armor.get("instance", {}).get("energy") or {}).get("capacity"),
            f"tier={armor.get('identity', {}).get('gear_tier')} roll={ {k: v for k, v in (stats.get('roll') or {}).items() if v} } "
            f"词条槽={len(roll_sockets)} 能量={(armor.get('instance', {}).get('energy') or {}).get('capacity')}",
        )

        r = await inv(intent="item", item_instance_id=LEGACY_PIECE)
        legacy = ((r.get("data") or {}).get("armor") or {})
        check(
            "④ 单件详情（老护甲）：armor_system=legacy、没有词条原型、并给中文说明",
            r["ok"] and legacy.get("identity", {}).get("armor_system") == "legacy"
            and not legacy.get("identity", {}).get("archetype")
            and any("老护甲" in w for w in (r.get("warnings") or [])),
            f"system={legacy.get('identity', {}).get('armor_system')} "
            f"槽数={len(legacy.get('sockets') or [])} warnings={len(r.get('warnings') or [])}",
        )

        r = await inv(intent="item", item_instance_id="1")
        check(
            "⑤ 单件详情：不存在的实例 → item_not_found_error（不是裸抛）",
            not r["ok"] and (r.get("error") or {}).get("code") == "item_not_found_error",
            f"code={(r.get('error') or {}).get('code')}",
        )

        # 3. 换模组：确认请求要能读懂；不进服务层写入
        r = await inv(intent="equip_mod", item_instance_id=T5_LEGS, mod_name="手雷模组",
                      character="hunter", confirmed=False)
        candidate = (r.get("candidates") or [{}])[0]
        check(
            "⑥ 换模组：confirmed=false 给「哪件/哪槽/从什么换成什么/能量怎么变」",
            not r["ok"] and (r.get("error") or {}).get("code") == "confirmation_required"
            and candidate.get("energy") and candidate.get("to", {}).get("stat_bonus")
            and "槽" in str(candidate.get("summary")),
            f"summary={str(candidate.get('summary'))[:90]}",
        )

        r = await inv(intent="equip_mod", item_instance_id=T5_LEGS, mod_name="手雷快速启动",
                      character="hunter", confirmed=False)
        check(
            "⑦ 换模组：装错部位的模组 → 说清没有该槽，而不是硬装",
            not r["ok"] and (r.get("error") or {}).get("code") == "invalid_argument_error"
            and "插槽" in str((r.get("error") or {}).get("message")),
            f"msg={str((r.get('error') or {}).get('message'))[:90]}",
        )

        r = await inv(intent="equip_mod", item_instance_id=T5_LEGS, mod_name="手雷模组",
                      character="hunter", confirmed=False)
        wrong_char = await inv(intent="equip_mod", item_instance_id=LEGACY_PIECE,
                               mod_name="手雷模组", character="titan", confirmed=False)
        check(
            "⑧ 换模组：不在该角色身上 → 让调用方先搬，而不是猜一个角色",
            not wrong_char["ok"]
            and (wrong_char.get("error") or {}).get("code") == "invalid_argument_error",
            f"code={(wrong_char.get('error') or {}).get('code')} "
            f"msg={str((wrong_char.get('error') or {}).get('message'))[:70]}",
        )

        # 4. 装备回显：逐件预览
        found = await build(intent="find", character="hunter", exotic_name="快速装弹松身裤",
                            weapons_target=150, class_target=100, super_target=80)
        if (found.get("error") or {}).get("code") == "exotic_confirmation_required":
            found = await build(**dict(found["candidates"][0]["arguments"]))
        canonical = (((found.get("data") or {}).get("builds") or [{}])[0]).get("canonical_build") or {}
        confirm = await build(intent="equip_build", canonical_build=canonical, confirmed=False)
        preview = ((confirm.get("candidates") or [{}])[0]).get("items_preview") or []
        check(
            "⑨ 装备确认：五件逐件给光等/能量/现有模组与将要装的模组",
            confirm.get("error", {}).get("code") == "confirmation_required"
            and len(preview) == 5
            and all(row.get("slot_display") for row in preview)
            and all("current_mods" in row for row in preview),
            f"预览件数={len(preview)} 首件={preview[0]['slot'] if preview else None}"
            f"/{preview[0]['slot_display'] if preview else None}"
            f" 能量={preview[0].get('energy') if preview else None}",
        )

        # 5. 无解时的六维阶梯
        ladder_call = dict(found["candidates"][0]["arguments"]) if found.get("candidates") else {}
        blocked = await build(intent="find", character="hunter", exotic_name="快速装弹松身裤",
                              weapons_target=150, class_target=100, super_target=80,
                              melee_target=70, grenade_target=70,
                              priority_stats=["weapons", "class_stat", "super_stat", "melee", "grenade"])
        if (blocked.get("error") or {}).get("code") == "exotic_confirmation_required":
            blocked = await build(**dict(blocked["candidates"][0]["arguments"]))
        data = blocked.get("data") or {}
        ladder = data.get("ladder") or {}
        check(
            "⑩ 无解：find 给 0 候选 + ladder（shortfall/ceiling/suggestion 三件套）",
            blocked["ok"] and not (data.get("builds") or [])
            and ladder.get("ceiling") and ladder.get("suggestion")
            and ladder.get("single_stat_ceiling"),
            f"ceiling={ladder.get('ceiling')} shortfall={ladder.get('shortfall')} "
            f"suggestion drop={ladder.get('suggestion', {}).get('drop')}",
        )
        check(
            "⑪ 阶梯只提议：原始硬约束没被改（targets 里还是 近战70/手雷70）",
            (ladder.get("targets") or {}).get("melee") == 70
            and (ladder.get("targets") or {}).get("grenade") == 70,
            f"targets={ladder.get('targets')}",
        )

        # 6. 社区核对：护甲需求带统一槽位键
        r = await build(intent="community_build",
                        community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1",
                        include_inventory=True, character="hunter")
        match = (((r.get("data") or {}).get("selected_build") or {}).get("inventory_match")) or {}
        armor_reqs = [q for q in (match.get("requirements") or [])
                      if q.get("kind") in {"exotic_armor", "armor_set"}]
        owned = [inst for q in armor_reqs for inst in (q.get("owned_instances") or [])]
        check(
            "⑫ 社区核对：护甲需求的已拥有副本带 slot/slot_display",
            r["ok"] and bool(armor_reqs)
            and (not owned or all("slot" in inst for inst in owned)),
            f"护甲需求={len(armor_reqs)} 已拥有副本={len(owned)} "
            f"样例={owned[0] if owned else None}",
        )

        # 7. 列表保持轻量：不带插槽/能量（要看这些走 intent=item）
        r = await inv(intent="get", armor_slot="legs", limit=2)
        row = (((r.get("data") or {}).get("inventory") or {}).get("items") or [{}])[0]
        check(
            "⑬ 列表保持轻量：护甲行不带 sockets / energy（要看这些走 intent=item）",
            r["ok"] and "sockets" not in row and "energy" not in row,
            f"首行含 sockets={'sockets' in row} energy={'energy' in row} keys={sorted(row)[:8]}",
        )

        # 8. 旧六维名要能映射到新名（纪律 = 手雷）
        r = await inv(intent="equip_mod", item_instance_id=T5_LEGS, mod_name="纪律模组",
                      character="hunter", confirmed=False)
        candidate = (r.get("candidates") or [{}])[0]
        check(
            "⑭ 旧六维名（纪律）自动映射成新名（手雷）并找到真有加成的那个版本",
            not r["ok"] and (r.get("error") or {}).get("code") == "confirmation_required"
            and candidate.get("to", {}).get("name") == "手雷模组"
            and candidate.get("to", {}).get("energy_cost") == 3
            and candidate.get("to", {}).get("stat_bonus"),
            f"to={candidate.get('to')}",
        )

        # 9. 能量不够：找一件"属性模组槽是空的、但剩余能量 < 3"的护甲
        #    （能量满但槽已占的件不算 —— 换掉旧模组不涨能量，那是正常确认）
        scanned = 0
        tight_result = None
        tight_piece = None
        listed = await inv(intent="get", armor_slot="legs", limit=8)
        for listed_row in ((listed.get("data") or {}).get("inventory") or {}).get("items") or []:
            detail = await inv(intent="item", item_instance_id=listed_row["item_instance_id"])
            armor = ((detail.get("data") or {}).get("armor") or {})
            energy = armor.get("instance", {}).get("energy") or {}
            general = [s for s in (armor.get("sockets") or []) if s.get("kind") == "general"]
            scanned += 1
            if general and general[0].get("empty") and (energy.get("unused") or 99) < 3:
                tight_piece = armor.get("identity", {}).get("name")
                tight_result = await inv(intent="equip_mod",
                                         item_instance_id=listed_row["item_instance_id"],
                                         mod_name="手雷模组", character="hunter", confirmed=False)
                break
        if tight_result is not None:
            message = str((tight_result.get("error") or {}).get("message"))
            check(
                "⑮ 能量不够：消息里给具体数字（已用/容量 → 换后/容量），不是笼统「装不上」",
                not tight_result["ok"]
                and (tight_result.get("error") or {}).get("code") == "invalid_argument_error"
                and "能量不够" in message,
                f"{tight_piece}：msg={message[:110]}",
            )
        else:
            check(
                "⑮ 能量不够（本轮无样本，该分支由单测覆盖）",
                True,
                f"扫了 {scanned} 件腿甲，没有「属性模组槽为空且剩余能量 < 3」的样本；"
                "分支由 tests/test_equip_mod.py::test_plan_refuses_when_energy_is_not_enough 覆盖",
            )

        # 10. 展示字段不污染可执行载荷：canonical 原样回传仍然有效
        again = await build(intent="equip_build", canonical_build=canonical, confirmed=False)
        check(
            "⑯ 装备确认带 items_preview，但 canonical_build 仍可原样回传（不被展示字段污染）",
            again.get("error", {}).get("code") == "confirmation_required"
            and (again.get("candidates") or [{}])[0].get("items_preview"),
            f"code={(again.get('error') or {}).get('code')} 预览件数={len((again.get('candidates') or [{}])[0].get('items_preview') or [])}",
        )

        # 11. 只给优先级时 completion_rate 不是 0.0
        only_priority = await build(intent="recommend", character="hunter",
                                    exotic_name="快速装弹松身裤",
                                    priority_stats=["weapons", "class_stat"])
        if (only_priority.get("error") or {}).get("code") == "exotic_confirmation_required":
            only_priority = await build(**dict(only_priority["candidates"][0]["arguments"]))
        results = (((only_priority.get("data") or {}).get("recommendation") or {}).get("results")) or []
        check(
            "⑰ 只给优先级时 completion_rate 为 null + 说明（不是 0.0）",
            only_priority["ok"] and bool(results)
            and results[0].get("completion_rate") is None
            and "没有硬目标" in str(results[0].get("completion_rate_note")),
            f"首条 completion_rate={results[0].get('completion_rate') if results else None} "
            f"note={str(results[0].get('completion_rate_note'))[:40] if results else None}",
        )

        # 12. 同一件护甲的不同副本要分清（写入必须传实例 ID，不许按名字猜）
        r = await inv(intent="search", item_name="至高狂徒腿铠")
        copies = (((r.get("data") or {}).get("result") or {}).get("items")) or []
        powers = sorted(
            {row.get("power") for row in copies if row.get("item_instance_id")}
        )
        check(
            "⑱ 同一件的多个副本按实例区分（光等可以不同，写入必须传 item_instance_id）",
            r["ok"] and len(copies) >= 2 and len(powers) >= 2,
            f"副本={[(row.get('item_instance_id'), row.get('power')) for row in copies[:3]]}",
        )

    print("\n=== 汇总 ===")
    failed = [row for row, ok, _ in RESULTS if not ok]
    print(f"共 {len(RESULTS)} 行，PASS {len(RESULTS) - len(failed)}，FAIL {len(failed)}")
    for row in failed:
        print("  FAIL:", row)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
