#!/usr/bin/env python
"""语料武器章节的**逐行实跑**：把 `TESTING_CORPUS.md` 里每一行的"验收点"变成可执行断言。

和单元测试的分工：单元测试用替身、任何机器都能跑；这个脚本走**真机**（需要 OAuth 与
本地 Manifest、社区资料），回答的是"文档里写的那些话，现在真的还成立吗"。
改了武器响应形状、口径或话术之后，除了跑 `pytest`，也要跑它：

    .venv/bin/python scripts/run_corpus_weapon_rows.py

退出码 0 = 语料武器行全部成立；非 0 = 有行过期或行为退化（看 FAIL 行的判定依据）。
"""

import asyncio
import json
import sys

from destiny_mcp.server import create_server, app_lifespan
from destiny_mcp.tools.assistants import weapon_assistant

RESULTS = []


def check(row: str, ok: bool, evidence: str) -> None:
    RESULTS.append((row, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'} | {row}\n       {evidence}")


async def main():
    async with app_lifespan(create_server("normal")) as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})})()

        async def call(**kwargs):
            return await weapon_assistant(ctx=ctx, **kwargs)

        # 1. 全游戏里哪些武器能滚出 <Perk>
        r = await call(intent="catalog", perk_name="萤火虫", limit=5)
        d = r["data"]
        rows = d.get("matched") or []
        first = rows[0] if rows else {}
        check(
            "① catalog+perk_name：Manifest 候选标注 / matched_count+returned_count+truncated",
            r["ok"] and d["scope"] == "manifest_catalog"
            and all(k in d for k in ("matched_count", "returned_count", "truncated"))
            and first.get("owned") is False and first.get("ownership_checked") is False,
            f"scope={d.get('scope')} matched={d.get('matched_count')} returned={d.get('returned_count')} "
            f"truncated={d.get('truncated')} owned={first.get('owned')} checked={first.get('ownership_checked')}",
        )

        # 2. 全游戏的手炮有哪些（与账号无关）
        r = await call(intent="catalog", weapon_type="手炮", limit=5)
        d = r["data"]
        blob = json.dumps(d, ensure_ascii=False)
        check(
            "② catalog+weapon_type：与账号无关，不出现「你有/你没有」",
            r["ok"] and "你有" not in blob and "你没有" not in blob and "instance_id" not in blob,
            f"scope={d.get('scope')} 命中={d.get('matched_count')} 行内是否含 instance_id={'instance_id' in blob}",
        )

        # 3. <武器> 的 Perk 池
        r = await call(intent="perk_pool", weapon_name="遗产")
        w, sockets = r["data"]["weapon"], r["data"]["sockets"]
        rolls = [s for s in sockets if s["kind"] in {"barrel", "magazine", "trait"}]
        rec = [(o["name"], o["recommended"]) for s in sockets for o in s.get("options") or [] if o.get("recommended")]
        check(
            "③ perk_pool：scope=definition / 不是账号副本 / recommended + sources",
            r["ok"] and all(s["scope"] == "definition" for s in sockets) and bool(rolls) and bool(rec)
            and any(src.get("updated_at") for src in w["sources"]),
            f"栏数={len(sockets)} 可滚栏={[(s['slot'], s['option_count']) for s in rolls]} "
            f"带 recommended 的选项={len(rec)}（例：{rec[0] if rec else None}） sources={[s['kind'] for s in w['sources']]}",
        )

        # 4. info / stats
        r_info = await call(intent="info", weapon_name="遗产")
        r_stats = await call(intent="stats", weapon_name="遗产")
        wi, ws = r_info["data"]["weapon"], r_stats["data"]["stats"]
        check(
            "④ info/stats：不叠加账号加成 / info 的 popularity 明说「这个 intent 不查」",
            r_info["ok"] and r_stats["ok"]
            and wi["popularity"]["available"] is False and "不带" in wi["popularity"]["note"]
            and isinstance(ws, list) and ws[0]["name"] == "每分钟发射数" and ws[0]["value"] == 65,
            f"info.popularity={wi['popularity']} | stats 首项={ws[0]} 共{len(ws)}项",
        )

        # 5. catalyst：结构与传说武器口径
        r_leg = await call(intent="catalyst", weapon_name="遗产")
        c_leg = r_leg["data"]["catalyst"]
        r_exo = await call(intent="catalyst", weapon_name="泰拉巴")
        c_exo = r_exo["data"]["catalyst"]
        check(
            "⑤ catalyst：结构固定 + 传说 count=0「只有异域」+ unlock_state=not_checked",
            r_leg["ok"] and r_exo["ok"]
            and all(k in c_leg for k in ("weapon", "is_exotic", "count", "catalysts", "unlock_state", "note"))
            and c_leg["count"] == 0 and "只有异域" in c_leg["note"]
            and c_leg["unlock_state"] == "not_checked"
            and len(c_exo.get("catalysts") or []) <= 5,
            f"遗产 count={c_leg['count']} note={c_leg['note'][:28]}… unlock_state={c_leg['unlock_state']} | "
            f"泰拉巴 count={c_exo['count']} 词条数={len(c_exo.get('catalysts') or [])}",
        )

        # 6. type：列表形状
        r = await call(intent="type", weapon_type="手炮", limit=5)
        block = r["data"]["weapons"]
        item = block["items"][0]
        check(
            "⑥ type：每件 {weapon,sockets,options,stats} / 列计数 / options 是实例级 / 本地明说没查",
            r["ok"] and set(item) == {"weapon", "sockets", "options", "stats", "perks_complete", "notes"}
            and all(s["options_available"] is False for s in item["sockets"])
            and all(o["scope"] == "instance" for o in item["options"])
            and item["weapon"]["popularity"]["available"] is False
            and block["total"] >= block["returned"] == len(block["items"]),
            f"total={block['total']} returned={block['returned']} truncated={block['truncated']} | "
            f"首件 {item['weapon']['name']} 列={len(item['sockets'])} 可换栏={len(item['options'])} "
            f"stats={len(item['stats'])} 本地提示={item['weapon']['popularity']['note'][:16]}…",
        )

        # 7. perk_description：描述来自 Manifest
        r = await call(intent="perk_description", perk_name="萤火虫")
        perk = r["data"]["perk"]
        check(
            "⑦ perk_description：描述与 hash 来自 Manifest（不按名字推断）",
            r["ok"] and perk.get("description") and perk.get("item_hash"),
            f"name={perk.get('name')} hash={perk.get('item_hash')} 描述={str(perk.get('description'))[:36]}…",
        )

        # 8. filter_rolls（账号侧）
        r = await call(intent="filter_rolls", weapon_name="刚玉战锤", limit=5)
        d = r["data"]
        row = (d["matched"] or d["not_matched"] or d["unknown"] or [{}])[0]
        check(
            "⑧ filter_rolls：只筛账号持有副本，给出实例 ID 与位置",
            r["ok"] and d["scope"] == "owned_inventory" and row.get("instance_id") and row.get("location") is not None
            and "perks" in row and "coverage_complete" in d,
            f"scoped={d['scoped_count']} checked={d['checked_count']} matched={d['matched_count']} "
            f"unknown={d['unknown_count']} coverage_complete={d['coverage_complete']} | "
            f"首行 实例={row.get('instance_id')} 位置={row.get('location')} 当前perk={row.get('perks')}",
        )

        # 9. compare：差异必须指到副本；（缺 weapon_name → config_error）
        r = await call(intent="compare", weapon_name="信任")
        comp = r["data"]["comparison"]
        diffs = comp.get("differences") or []
        inst = comp["instances"]
        bad = [x for x in diffs if not x.get("present_in_instance") or not x.get("absent_in_instance")]
        r_missing = await call(intent="compare", item_instance_id="123")
        check(
            "⑨ compare：差异指到具体副本；缺 weapon_name 报错而不是猜",
            r["ok"] and not bad and all(i["weapon"]["instance"]["instance_id"] for i in inst)
            and r_missing["ok"] is False and r_missing["error"]["code"] == "config_error",
            f"副本数={len(inst)} 差异数={len(diffs)} 差异样例={diffs[0] if diffs else None} | "
            f"缺 weapon_name → {r_missing['error']['code']}: {r_missing['error']['message'][:20]}",
        )

        # 10. analyze：定义 + 副本
        r = await call(intent="analyze", weapon_name="遗产", include_inventory=True)
        d = r["data"]
        inv = d.get("inventory") or {}
        check(
            "⑩ analyze：定义（weapon/sockets/stats）与副本（inventory.instances）分开",
            r["ok"] and {"weapon", "sockets", "stats"} <= set(d)
            and d["inventory_status"] == "complete" and isinstance(inv.get("instances"), list)
            and all("weapon" in i and "sockets" in i for i in inv["instances"]),
            f"inventory_status={d['inventory_status']} 副本数={len(inv.get('instances') or [])} "
            f"副本首件键={sorted((inv.get('instances') or [{}])[0].keys())}",
        )

        # 11. popularity：没有快照时明说缺数据
        r = await call(intent="popularity", weapon_name="遗产")
        pop = r["data"]["popularity"]
        check(
            "⑪ popularity：没有快照时 data.popularity=null + 明说缺数据，不编百分比",
            r["ok"] and pop is None and "暂无录入的选取率快照" in r["summary"]
            and any("未录入" in x for x in r["warnings"]),
            f"popularity={pop} summary={r['summary']} warnings={r['warnings'][:2]}",
        )
        # ⑪b 另外两种"没有数据"的写法要能区分（覆盖表 vs 查了没有）
        r_pool = await call(intent="perk_pool", weapon_name="遗产")
        note_pool = r_pool["data"]["weapon"]["popularity"]["note"]
        r_info2 = await call(intent="info", weapon_name="遗产")
        note_info = r_info2["data"]["weapon"]["popularity"]["note"]
        check(
            "⑪b 「查了但没有」与「这个 intent 不查」两种 note 要分得清",
            "没有这把武器的选取率快照" in note_pool and "不带" in note_info,
            f"perk_pool: {note_pool} | info: {note_info}",
        )

        # 12. god_roll：三态
        kinds = {}
        for name in ("泰拉巴", "遗产", "枯骨鳞片"):
            rr = await call(intent="god_roll", weapon_name=name)
            kinds[name] = rr["data"]["god_roll"]["kind"]
        check(
            "⑫ god_roll：固定/推荐/无 三态分得清，不是空壳",
            kinds["泰拉巴"] == "fixed" and kinds["遗产"] in {"recommended", "none"}
            and all(k in ("fixed", "recommended", "none") for k in kinds.values()),
            f"泰拉巴={kinds['泰拉巴']}（固定武器） 遗产={kinds['遗产']} 枯骨鳞片={kinds['枯骨鳞片']}",
        )

        # 13. 关键区分：我有没有 vs 全游戏有多少
        r_own = await call(intent="filter_rolls", weapon_name="刚玉战锤", perk_name="萤火虫", limit=5)
        r_all = await call(intent="catalog", perk_name="萤火虫", limit=5)
        check(
            "⑬ 关键区分：「我有没有」走 filter_rolls（账号），「全游戏」走 catalog（Manifest）",
            r_own["data"]["scope"] == "owned_inventory" and r_all["data"]["scope"] == "manifest_catalog",
            f"filter_rolls.scope={r_own['data']['scope']}（checked={r_own['data']['checked_count']}） | "
            f"catalog.scope={r_all['data']['scope']}（checked={r_all['data']['checked_count']}）",
        )

        # 14. 0 命中不能说成「你没有」
        r = await call(intent="filter_rolls", location="vault", perk_name="绝不存在的Perk名", limit=5)
        d = r["data"]
        check(
            "⑭ 0 命中必须带 coverage_complete/unknown_count，不能等同于「你没有」",
            r["ok"] and d["matched_count"] == 0 and "coverage_complete" in d and "unknown_count" in d,
            f"matched={d['matched_count']} unknown={d['unknown_count']} coverage_complete={d['coverage_complete']} "
            f"warnings={r['warnings'][:1]}",
        )

        # 15. 能不能换成某 Perk：看 equipped 与 instance options
        r = await call(intent="type", weapon_type="手炮", limit=5)
        items = [x for x in r["data"]["weapons"]["items"] if x.get("options")]
        if items:
            it = items[0]
            equipped = [s["equipped"] for s in it["sockets"] if s.get("equipped")]
            inst_opts = [o for o in it["options"]]
            check(
                "⑮ 能不能换成某 Perk：equipped=现在装的，options(scope=instance)=这一件能换的",
                bool(equipped) and (bool(inst_opts) or bool(it["notes"])),
                f"{it['weapon']['name']}：现在装 {len(equipped)} 项（例 {equipped[0] if equipped else None}）、"
                f"实例可换栏 {len(inst_opts)}、notes={it['notes'][:1]}",
            )
        else:
            check("⑮ 能不能换成某 Perk", False, "这个类型下没有带实例可换项的武器样本")

    print("\n=== 汇总 ===")
    failed = [row for row, ok, _ in RESULTS if not ok]
    print(f"共 {len(RESULTS)} 行，PASS {len(RESULTS) - len(failed)}，FAIL {len(failed)}")
    for row in failed:
        print("  FAIL:", row)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
