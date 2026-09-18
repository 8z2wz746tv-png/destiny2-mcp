#!/usr/bin/env python3
"""PvP/生涯口径的真机语料（新一套）：逐行调用 + 断言 + 原始数字。

与 `run_corpus_all_rows.py` 的分工：那个跑全 intent 的信封与形状；这一套只盯
"生涯/计数器/模式/周期/名称口径"这批**刚做过、且容易出现口径错**的行为，跑得快（十几行）。

用法：`.venv/bin/python scripts/run_corpus_pvp_rows.py`
全部只读；断言失败会打印实际值，退出码非 0。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".venv" / "bin" / "destiny-mcp"

# 真机实测基线（2026-09-18 采集；上游数据不变时应保持）
CRUCIBLE_CAREER_DEFEATS = 124495
TRIALS_CAREER_DEFEATS = 10696
TRIALS_CAREER_WINS = 826
IRON_BANNER_DEFEATS = 1737
CRUCIBLE_SEASON_DEFEATS = 3522
# 2026-08-31 的一场突袭（结算不可变，可当固定样本）
PGCR_SAMPLE_ACTIVITY = "17161198628"
# 游戏内 ID 形如 `名字#1234`；平台名（Steam/Xbox/PSN/Epic）没有 #code
IN_GAME_ID = re.compile(r"^.+#\d{3,4}$")
SELF_IN_GAME_ID = "OneTop丶Husky#6641"


class Server:
    """一次进程内跑完所有行：候选/会话状态都在一起。"""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [str(BIN)], cwd=str(ROOT), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._id = 0
        threading.Thread(target=self._drain, daemon=True).start()
        self._send({"jsonrpc": "2.0", "id": self._next(), "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "pvp-corpus", "version": "0"}}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._wait()

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for _ in self.proc.stderr:
            pass

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _next(self) -> int:
        self._id += 1
        return self._id

    def _wait(self, want: int | None = None) -> dict:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise SystemExit("服务器提前退出")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if want is None or msg.get("id") == want:
                return msg

    def call(self, tool: str, args: dict) -> dict:
        rid = self._next()
        self._send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": tool, "arguments": args}})
        text = "".join(
            block.get("text", "")
            for block in self._wait(rid).get("result", {}).get("content", [])
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=30)


def _counters(payload: dict) -> dict[int, int]:
    rows = ((payload.get("data") or {}).get("counters") or [])
    return {int(r["metric_hash"]): int(r["progress"] or 0) for r in rows}


def _stat_rows(payload: dict) -> list[dict]:
    """行在 `data.stats.groups[].stats[]`（别猜成 `rows`）。"""
    stats = (payload.get("data") or {}).get("stats") or {}
    rows: list[dict] = []
    for group in stats.get("groups") or []:
        rows.extend(group.get("stats") or [])
    return rows


def main() -> int:
    srv = Server()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    try:
        # 1 玩家档案：游戏内 ID（带 #），不是平台名
        r = srv.call("player_assistant", {"intent": "profile"})
        name = ((r.get("data") or {}).get("profile") or {}).get("display_name", "")
        check("profile 显示游戏内 ID（含 #）", r.get("ok") is True and "#" in name, f"display_name={name!r}")

        # 2 熔炉生涯：三档 + 公式
        r = srv.call("activity_assistant", {"intent": "stats", "mode": "crucible"})
        rows = _stat_rows(r)
        defeats = next((x for x in rows if x.get("upstream_id") == "opponentsDefeated"), {})
        check(
            "熔炉生涯三档存在且 account_total = existing + deleted",
            r.get("ok") is True
            and defeats.get("existing") == 50622
            and defeats.get("deleted") == 28242
            and defeats.get("account_total") == 78864,
            f"existing={defeats.get('existing')} deleted={defeats.get('deleted')} "
            f"account_total={defeats.get('account_total')}",
        )

        # 3 双来源：计数器 124,495（不带 mode 的 stats 才附 game_counters；计数器是账号级的）
        r = srv.call("activity_assistant", {"intent": "stats"})
        counters = ((r.get("data") or {}).get("game_counters") or [])
        hit = next((c for c in counters if int(c.get("metric_hash", 0)) == 811894228), {})
        check(
            "熔炉生涯击败计数器 = 124495（来源 profile.metrics）",
            hit.get("progress") == CRUCIBLE_CAREER_DEFEATS,
            f"progress={hit.get('progress')}",
        )

        # 4 试炼：模式过滤 + 两个已核对的数
        r = srv.call("activity_assistant", {"intent": "counters", "mode": "trials"})
        c = _counters(r)
        check(
            "试炼 击败=10696 且 胜场=826",
            c.get(2082314848) == TRIALS_CAREER_DEFEATS and c.get(1365664208) == TRIALS_CAREER_WINS,
            f"defeats={c.get(2082314848)} wins={c.get(1365664208)}",
        )

        # 5 铁旗
        r = srv.call("activity_assistant", {"intent": "counters", "mode": "iron_banner"})
        c = _counters(r)
        check("铁旗 击败=1737", c.get(2161492053) == IRON_BANNER_DEFEATS, f"defeats={c.get(2161492053)}")

        # 6 赛季（熔炉）：计数器能给
        r = srv.call(
            "activity_assistant",
            {"intent": "counters", "mode": "crucible", "period": "season"},
        )
        c = _counters(r)
        check(
            "熔炉赛季 击败=3522（period=season 走计数器）",
            c.get(2935221077) == CRUCIBLE_SEASON_DEFEATS,
            f"defeats={c.get(2935221077)}",
        )

        # 7 统计接口的赛季：如实报取不到（不降级、不编）
        r = srv.call("activity_assistant", {"intent": "stats", "mode": "crucible", "period": "season"})
        err = (r.get("error") or {}).get("code")
        check(
            "stats+period=season 如实失败（不假装有数据）",
            r.get("ok") is False and err in {"a_p_i_error", "unavailable"} or "unavailable" in json.dumps(r, ensure_ascii=False),
            f"ok={r.get('ok')} code={err}",
        )

        # 8 武器榜口径：all_modes + 明说不是 PvP 榜
        r = srv.call("activity_assistant", {"intent": "weapon_history"})
        data = r.get("data") or {}
        scope = (data.get("weapon_history") or {}).get("scope") or data.get("scope")
        check("武器榜标 scope=all_modes 并说明不是 PvP 榜", scope == "all_modes", f"scope={scope!r}")

        # 9 模式词表外的值：报错并列出可用值（不返回空清单）
        r = srv.call("activity_assistant", {"intent": "counters", "mode": "日落"})
        check(
            "模式词表外的值如实报错",
            r.get("ok") is False and (r.get("error") or {}).get("code") == "invalid_argument_error",
            f"code={(r.get('error') or {}).get('code')}",
        )

        # 10 子职业读取（回归）
        r = srv.call("subclass_assistant", {"intent": "get", "character": "warlock"})
        sub = ((r.get("data") or {}).get("subclass") or {}).get("subclass_name")
        check("子职业读取正常", r.get("ok") is True and bool(sub), f"subclass={sub!r}")

        # 11 神器：给角色时附身上那件（回归）
        r = srv.call("subclass_assistant", {"intent": "artifact", "character": "warlock"})
        equipped = (((r.get("data") or {}).get("artifact") or {}).get("character_artifact") or {}).get("equipped") or {}
        check("神器读取带身上那件", r.get("ok") is True and bool(equipped.get("name")), f"equipped={equipped.get('name')!r}")

        # 12 装备编排：不带确认只给计划、零写入（回归）
        r = srv.call(
            "inventory_assistant",
            {"intent": "equip", "item_instance_id": "6917530135965667837", "character": "warlock"},
        )
        steps = ((r.get("candidates") or [{}])[0].get("steps") or [])
        check(
            "equip 不带确认 → confirmation_required + 计划（零写入）",
            (r.get("error") or {}).get("code") == "confirmation_required" and len(steps) >= 1,
            f"steps={[s.get('action') for s in steps]}",
        )
        # 13 玩家名口径：PGCR 里所有参与者都是游戏内 ID（带 #数字），不是平台名
        #    （社区反馈：旧代码显示 Steam 名。真机样本：2026-08-31「永恒沙漠: 标准」）
        r = srv.call("activity_assistant", {"intent": "pgcr", "activity_id": PGCR_SAMPLE_ACTIVITY})
        data = r.get("data") or {}
        pgcr = data.get("pgcr") if isinstance(data.get("pgcr"), dict) else data
        players = [e.get("player_name", "") for e in (pgcr.get("entries") or [])]
        in_game = [n for n in players if IN_GAME_ID.match(n or "")]
        check(
            "PGCR 参与者显示游戏内 ID（带 #code）",
            r.get("ok") is True and bool(players) and len(in_game) == len(players)
            and SELF_IN_GAME_ID in players,
            f"players={players[:3]}… 合规 {len(in_game)}/{len(players)}",
        )

        # 14 排行榜：上游对该账号返回空（实测 2026-09-18）——要么给出合规榜单，
        #    要么如实报上游为空，绝不能凭记忆编排名。
        r = srv.call("activity_assistant", {"intent": "leaderboards", "statid": "activitiesCleared"})
        if r.get("ok") is True:
            names = [
                e.get("player", "")
                for m in ((r.get("data") or {}).get("preview") or [])
                for e in (m.get("entries") or [])
            ]
            good = bool(names) and all(IN_GAME_ID.match(n or "") for n in names)
            detail = f"有榜单，名字合规 {len(names)} 条"
        else:
            code = (r.get("error") or {}).get("code")
            good = code == "a_p_i_error"
            detail = f"上游为空 → code={code}"
        check("排行榜空响应如实上报（不编排名）", good, detail)

    finally:
        srv.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n共 {len(results)} 行，PASS {len(results) - len(failed)}，FAIL {len(failed)}")
    if failed:
        print("失败行：" + "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
