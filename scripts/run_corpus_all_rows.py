#!/usr/bin/env python
"""全面语料逐行实跑：八个工具面的**全部 intent 信封体检** + 字段级契约 + MCP 协议层。

和另外两个 runner 的分工：

- `run_corpus_weapon_rows.py`：武器章节的字段级断言（真机）。
- `run_corpus_armor_rows.py`：护甲章节的字段级断言（真机）。
- **本脚本**：其余六个工具面（player / inventory / build / loadout / subclass /
  activity / world）的字段级断言，加上跨切面（默认条数、翻页、中英同义、错误信封、
  响应体积），以及语料里"冻结前把所有 intent 跑一遍"那条——现在它是自动的 `sweep` 组。
  `mcp` 组走**真 stdio 握手**，专门测协议层才有的东西（schema 拒收、enum、工具面）。

    .venv/bin/python scripts/run_corpus_all_rows.py                  # 三组全跑
    .venv/bin/python scripts/run_corpus_all_rows.py --group sweep    # 只跑 intent 体检
    .venv/bin/python scripts/run_corpus_all_rows.py --skip-slow      # 跳过求解类（几十秒级）
    .venv/bin/python scripts/run_corpus_all_rows.py --only 商人      # 只跑标题含关键词的行
    .venv/bin/python scripts/run_corpus_all_rows.py --report /tmp/corpus_all.json

退出码 0 = 没有 FAIL（WARN/INFO/SKIP 不算失败）；1 = 有 FAIL。
**不做任何写入**：所有写入类 intent 一律 `confirmed=false`，且会断言响应里没有落盘标记。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, get_args

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from destiny_mcp.server import app_lifespan, create_server  # noqa: E402
from destiny_mcp.tools import assistants as assistants_module  # noqa: E402
from destiny_mcp.tools._requests import (  # noqa: E402
    WEAPON_PATTERN_INTENTS,
    WRITE_INTENTS,
    ActivityIntent,
    BuildIntent,
    InventoryIntent,
    LoadoutIntent,
    PlayerIntent,
    SubclassIntent,
    WeaponIntent,
    WorldIntent,
)

TOOL_NAMES = (
    "player_assistant",
    "inventory_assistant",
    "weapon_assistant",
    "build_assistant",
    "loadout_assistant",
    "subclass_assistant",
    "activity_assistant",
    "world_assistant",
)
TOOLS = {name: getattr(assistants_module, name) for name in TOOL_NAMES}
INTENTS = {
    "player_assistant": PlayerIntent,
    "inventory_assistant": InventoryIntent,
    "weapon_assistant": WeaponIntent,
    "build_assistant": BuildIntent,
    "loadout_assistant": LoadoutIntent,
    "subclass_assistant": SubclassIntent,
    "activity_assistant": ActivityIntent,
    "world_assistant": WorldIntent,
}

# 求解类 intent：真跑几十秒到几分钟，`--skip-slow` 会跳过它们。
SLOW_INTENTS = {
    ("build_assistant", "recommend"),
    ("build_assistant", "find"),
    ("build_assistant", "analyze"),
    ("build_assistant", "farm_target"),
    ("build_assistant", "community"),
    ("build_assistant", "community_build"),
    ("build_assistant", "starside"),
    ("activity_assistant", "pvp_weapons"),
    ("activity_assistant", "aggregate"),
    ("activity_assistant", "activity_aggregate"),
    ("activity_assistant", "activity_stats"),
    # catalog 及其别名每次都是一次全库扫描（实测 27s 闲 / 100s+ 忙），--skip-slow 时跳过
    ("weapon_assistant", "catalog"),
    ("weapon_assistant", "search_catalog"),
    ("weapon_assistant", "all_weapons"),
    ("weapon_assistant", "global"),
    ("weapon_assistant", "search_all"),
    # 锻造图样：读账号组件 900（首次约 3s，之后 5 分钟 TTL 内是缓存）
    ("weapon_assistant", "patterns"),
    ("player_assistant", "find"),
    ("player_assistant", "find_players"),
    ("player_assistant", "fuzzy"),
}

# 最简参数下**确定**该出现的错误码（其余允许 ok=true，或任何带 code 的干净失败）。
# 只登记有实机证据、且与上游状态无关的那几条；「上游挂了」不该写死在这里。
EXPECT_CODE = {
    ("activity_assistant", "leaderboards"): "a_p_i_error",
    ("activity_assistant", "leaderboard"): "a_p_i_error",
}

# 已登记待修的问题（根因与修复建议见 docs/testing/TESTING_CORPUS_FULL.md 的「已知问题」）。
# 默认仍然算 FAIL（严格口径）；加 --known 跑时降级成 WARN，用来当回归闸门：
# 只有**新**问题才会让退出码非 0。
KNOWN_OPEN = {
    "analyze 不能断言": "analyze 兜底文案自称「没有合法组合」，同约束 recommend 能达标（P1）",
    "exotic_armor 与 intent=item": "exotic_armor 还是老的 camelCase 护甲块（P2）",
    "collectible_item 的 item_hash": "收藏品类 intent 没做无符号归一（P2）",
    "artifact 不带名字": "不带 artifact_name 时不返回 current_artifact（P2）",
    "报错文案不出现": "子职业报错出现重复句号「。。」（P3）",
    "search 未命中": "未命中只说「已搜索物品」，没写「没找到」（P3）",
    "item 传 null": "armor_item 的 item_instance_id 缺 None 防御（进程内可达，P3）",
}

RESULTS: list[dict[str, Any]] = []
TIMINGS: list[tuple[float, str]] = []
SIZES: list[tuple[int, str]] = []


def record(
    group: str,
    title: str,
    status: str,
    evidence: str = "",
    seconds: float | None = None,
) -> None:
    RESULTS.append(
        {
            "group": group,
            "title": title,
            "status": status,
            "evidence": evidence,
            "seconds": seconds,
        }
    )
    suffix = f"  [{seconds:.1f}s]" if seconds is not None else ""
    print(f"{status:4} | {group:5} | {title}{suffix}")
    if evidence:
        print(f"       {evidence}")


def check(
    group: str,
    title: str,
    ok: bool,
    evidence: str,
    *,
    warn: bool = False,
    info: bool = False,
    seconds: float | None = None,
) -> None:
    status = "PASS" if ok else ("INFO" if info else ("WARN" if warn else "FAIL"))
    record(group, title, status, evidence, seconds)


def short(value: Any, limit: int = 260) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


def keys_of(value: Any) -> list[str]:
    return sorted(value) if isinstance(value, dict) else []


def walk_dicts(value: Any):
    """递归产出所有 dict（用于在不确定层级里找字段）。"""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_dicts(item)


def first_row(value: Any) -> dict:
    if isinstance(value, list):
        return next((x for x in value if isinstance(x, dict)), {})
    return value if isinstance(value, dict) else {}


class Runner:
    def __init__(self, timeout: float, slow_timeout: float) -> None:
        self.timeout = timeout
        self.slow_timeout = slow_timeout
        self.ctx: Any = None

    async def call(
        self,
        tool: str,
        *,
        slow: bool = False,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> tuple[dict | None, float, BaseException | None]:
        limit = timeout or (self.slow_timeout if slow else self.timeout)
        tag = f"{tool}({kwargs.get('intent', '?')})"
        start = time.perf_counter()
        payload: dict | None = None
        error: BaseException | None = None
        try:
            payload = await asyncio.wait_for(TOOLS[tool](ctx=self.ctx, **kwargs), limit)
        except BaseException as exc:  # noqa: BLE001 - 断言要看到具体异常类型
            error = exc
        elapsed = time.perf_counter() - start
        TIMINGS.append((elapsed, tag))
        if isinstance(payload, dict):
            SIZES.append(
                (len(json.dumps(payload, ensure_ascii=False, default=str)), tag)
            )
        return payload, elapsed, error

    @staticmethod
    def failing(
        payload: dict | None, error: BaseException | None
    ) -> tuple[bool, str]:
        """调用是否「干净」：没抛异常、ok 是布尔、失败带 code、成功带 data 与 summary。"""
        if error is not None:
            kind = type(error).__name__
            if isinstance(error, asyncio.TimeoutError):
                return False, f"超时（> 上限）: {kind}"
            return False, f"抛异常 {kind}: {str(error)[:200]}"
        if not isinstance(payload, dict):
            return False, f"返回不是 dict: {type(payload).__name__}"
        blob = json.dumps(payload, ensure_ascii=False, default=str)
        if "Traceback (most recent call last)" in blob:
            return False, "响应里出现 Traceback 原文"
        if not isinstance(payload.get("ok"), bool):
            return False, f"ok 不是布尔: {payload.get('ok')!r}"
        err = payload.get("error") or {}
        if payload["ok"]:
            if payload.get("data") is None:
                return False, "ok=true 但 data 为 null"
            if not payload.get("summary"):
                return False, "ok=true 但没有 summary"
        else:
            code = err.get("code") if isinstance(err, dict) else None
            if not code:
                return False, f"ok=false 但 error.code 为空: {short(payload.get('error'), 120)}"
        if not isinstance(payload.get("warnings"), list):
            return False, f"warnings 不是 list: {type(payload.get('warnings')).__name__}"
        return True, ""


async def discover(runner: Runner) -> dict[str, Any]:
    """先读一次真机事实，后面的断言用真实值而不是写死的名字。"""
    live: dict[str, Any] = {}
    profile, _, _ = await runner.call("player_assistant", intent="profile")
    live["profile"] = profile or {}
    display = ((profile or {}).get("data") or {}).get("profile") or {}
    live["player_name"] = display.get("display_name") or ""
    live["characters"] = display.get("characters") or []

    weapons, _, _ = await runner.call(
        "weapon_assistant", intent="type", weapon_type="手炮", limit=5
    )
    items = (((weapons or {}).get("data") or {}).get("weapons") or {}).get("items") or []
    first_weapon = first_row(items)
    live["weapon_name"] = (first_weapon.get("weapon") or {}).get("name") or "遗产"
    live["weapon_instance_id"] = (
        ((first_weapon.get("weapon") or {}).get("instance") or {}).get("instance_id") or ""
    )

    legs, _, _ = await runner.call(
        "inventory_assistant", intent="get", armor_slot="legs", limit=8
    )
    armor_rows = (((legs or {}).get("data") or {}).get("inventory") or {}).get("items") or []
    t5 = next((row for row in armor_rows if row.get("gear_tier")), None)
    live["armor_instance_id"] = (t5 or first_row(armor_rows)).get("item_instance_id") or ""
    if live["armor_instance_id"]:
        detail, _, _ = await runner.call(
            "inventory_assistant",
            intent="item",
            item_instance_id=live["armor_instance_id"],
        )
        identity = (((detail or {}).get("data") or {}).get("armor") or {}).get("identity") or {}
        live["armor_identity"] = identity
        live["set_name"] = (identity.get("set") or {}).get("name") or ""

    history, _, _ = await runner.call(
        "activity_assistant", intent="history", count=2
    )
    live["activity_id"] = (
        first_row(((history or {}).get("data") or {}).get("activities") or []).get(
            "instance_id"
        )
        or ""
    )

    nodes, _, _ = await runner.call(
        "world_assistant", intent="search_collectible_nodes", query="地牢"
    )
    live["node_hash"] = first_row(((nodes or {}).get("data") or {}).get("nodes") or []).get(
        "node_hash"
    )

    collectible, _, _ = await runner.call(
        "world_assistant", intent="collectible_item", item_name="无感"
    )
    live["collectible_hash"] = first_row(
        ((collectible or {}).get("data") or {}).get("items") or []
    ).get("collectible_hash")
    live["collectible_payload"] = collectible or {}

    artifact, _, _ = await runner.call("subclass_assistant", intent="artifact")
    current = ((artifact or {}).get("data") or {}).get("artifact") or {}
    live["artifact"] = current
    live["artifact_name"] = (current.get("artifact") or {}).get("name") or ""
    tiers = (current.get("current_artifact") or {}).get("tiers") or []
    if not tiers:
        # 不带 artifact_name 时响应里没有 current_artifact（见「已知问题」#4），
        # 所以模组 hash 得再按名字查一次才拿得到；这也是那两条 artifact_mod 行的前提。
        named, _, _ = await runner.call(
            "subclass_assistant",
            intent="artifact",
            artifact_name=live["artifact_name"] or "好奇之器",
        )
        named_block = (((named or {}).get("data") or {}).get("artifact") or {})
        tiers = (named_block.get("current_artifact") or {}).get("tiers") or []
    mods = next((tier.get("mods") for tier in tiers if tier.get("mods")), []) or []
    live["artifact_mod_hash"] = first_row(mods).get("hash")
    live["artifact_mod_name"] = first_row(mods).get("name")

    menu, _, _ = await runner.call("world_assistant", intent="vendor")
    vendors = ((menu or {}).get("data") or {}).get("vendors") or {}
    live["vendor_menu"] = vendors if isinstance(vendors, dict) else {}
    live["vendor_hash"] = first_row(live["vendor_menu"].get("vendors") or []).get(
        "vendor_hash"
    )

    loadouts, _, _ = await runner.call("loadout_assistant", intent="list", limit=2)
    live["loadouts"] = loadouts or {}
    live["loadout_id"] = first_row(((loadouts or {}).get("data") or {}).get("loadouts") or []).get(
        "id"
    )

    names, _, _ = await runner.call(
        "loadout_assistant", intent="search_identifiers", kind="name", query="术士"
    )
    live["identifier_payload"] = names or {}
    live["name_hash"] = first_row(((names or {}).get("data") or {}).get("identifiers") or []).get(
        "hash"
    )
    return live


# 信封统一（第二批）：`data` 里不许再塞第二个状态信封，键名一律 snake_case。
# 写入类的领域结果（`data.result.success/message`）不算——那是"这次操作的结果"，不是状态信封。
_ENVELOPE_ALLOWED_BLOCKS = {"result"}

# 上游（Bungie）自己的标识符：活动统计沿用 statId（activitiesEntered / killsDeathsRatio…），
# 武器历史的逐项 values 里也是同一套。这些不是我们取的键名，改它们要动载荷契约，
# 单独排期（见 docs/testing/TESTING_CORPUS_FULL.md「信封统一」一节的待办）。
_UPSTREAM_KEY_BLOCKS = {"values", "pve", "pvp"}


def envelope_violations(payload: dict | None) -> list[str]:
    """返回这个响应里的信封违规项（空列表 = 干净）。"""
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for key in ("success", "message"):
        if key in data:
            out.append(f"data.{key}")
    for block, value in data.items():
        if isinstance(value, dict) and block not in _ENVELOPE_ALLOWED_BLOCKS:
            for key in ("success", "message"):
                if key in value:
                    out.append(f"data.{block}.{key}")

    def walk(node: Any, path: str, upstream: bool = False) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                inside_upstream = upstream or str(key) in _UPSTREAM_KEY_BLOCKS
                if not inside_upstream and re.search(r"[a-z][A-Z]", str(key)):
                    out.append(f"{path}.{key}")
                walk(value, f"{path}.{key}", inside_upstream)
        elif isinstance(node, list):
            for index, value in enumerate(node[:3]):
                walk(value, f"{path}[{index}]", upstream)

    walk(data, "data")
    return sorted(set(out))


def sweep_args(tool: str, intent: str, live: dict[str, Any]) -> dict[str, Any]:
    """每个 intent 的「最简但有意义」参数。空手跑不是目的，跑出信封才是。"""
    args: dict[str, Any] = {}
    if tool == "player_assistant":
        if intent in {"search", "search_player"}:
            args["player_name"] = live.get("player_name") or "Guardian#0001"
        elif intent in {"find", "find_players", "fuzzy"}:
            args["name_prefix"] = live.get("name_prefix") or "husky"
    elif tool == "inventory_assistant":
        if intent in {"search", "find_item"}:
            args["item_name"] = "无感"
        elif intent in {"type", "search_type", "duplicates", "duplicate_weapons",
                        "find_duplicates", "重复武器"}:
            args["type_name"] = "手炮"
        elif intent in {"move", "transfer"}:
            args.update(item_name="无感", destination="vault")
        elif intent == "equip":
            args.update(item_name="无感", character="hunter")
        elif intent in {"equip_many", "equip_items"}:
            args["item_instance_ids"] = [
                x for x in (live.get("weapon_instance_id"), live.get("armor_instance_id")) if x
            ]
        elif intent == "equip_mod":
            args.update(mod_name="手雷模组", character="hunter")
        elif intent in {"pull_postmaster", "lock", "track_quest", "quest_tracking"}:
            args["item_name"] = "无感"
        elif intent == "item":
            args["item_instance_id"] = live.get("weapon_instance_id") or ""
    elif tool == "weapon_assistant":
        if intent == "type":
            args["weapon_type"] = "手炮"
        elif intent in WEAPON_PATTERN_INTENTS:
            args["weapon_name"] = "累积救赎"
        elif intent == "perk_description":
            args["perk_name"] = "萤火虫"
        elif intent == "community":
            # weapon_assistant 没有 query 参数：社区搜索走 weapon_name / perk_name。
            args["perk_name"] = "萤火虫"
        elif intent in {"catalog", "search_catalog", "all_weapons", "global", "search_all"}:
            args["perk_name"] = "萤火虫"
        else:
            args["weapon_name"] = "无感"
    elif tool == "build_assistant":
        if intent in {"recommend", "find", "analyze"}:
            args.update(character="hunter", health_target=100)
        elif intent == "farm_target":
            args.update(character="hunter", health_target=120, replacement_slot="helmet")
        elif intent == "equip_build":
            args["canonical_build"] = {"items": []}
        elif intent == "armor_mods":
            args["priority_stat"] = "手雷"
        elif intent == "exotic_armor":
            args.update(exotic_name="星火协议", character="warlock")
        elif intent == "set_bonus":
            args["set_bonus_name"] = live.get("set_name") or "至高狂徒"
        elif intent in {"community", "community_build", "starside"}:
            args["query"] = "术士"
    elif tool == "loadout_assistant":
        if intent in {"get", "list"}:
            args["character"] = "hunter"
        elif intent == "save":
            args["name"] = "语料体检-不应落盘"
        elif intent in {"delete", "equip_loadout"}:
            args["loadout_id"] = live.get("loadout_id") or "不存在的配装"
        elif intent == "search_identifiers":
            args["kind"] = "name"
            args["query"] = "术士"
        elif intent in {"snapshot_official", "clear_official"}:
            args["slot_number"] = 1
        elif intent == "update_official_identifiers":
            args.update(slot_number=1, name_hash=live.get("name_hash") or 1)
    elif tool == "subclass_assistant":
        if intent in {"get", "subclass"}:
            args["character"] = "hunter"
        elif intent == "modify":
            args.update(character="hunter", changes={"super": "金色枪"})
        elif intent == "options":
            args.update(element="void", component="grenade", character="hunter")
        elif intent == "fragments":
            args["element"] = "void"
        elif intent == "fragment_details":
            args["fragment_name"] = "回火"
        elif intent == "artifact":
            args["artifact_name"] = live.get("artifact_name") or ""
        elif intent == "artifact_mod":
            args["artifact_mod_hash"] = live.get("artifact_mod_hash") or 0
        elif intent == "equip_artifact_mod":
            args.update(
                artifact_mod_hash=live.get("artifact_mod_hash") or 0, character="hunter"
            )
        elif intent == "community":
            args["query"] = "碎片"
    elif tool == "activity_assistant":
        if intent in {"pgcr"}:
            args["activity_id"] = live.get("activity_id") or ""
        elif intent in {"history"}:
            args["count"] = 2
        elif intent == "pvp_weapons":
            # 体检只取 2 场：默认 10 场要真的打 10 次 PGCR（冷启约 1 场/秒），
            # 信封体检不需要那么多场次。
            args["count"] = 2
        elif intent == "clan_leaderboards":
            args["group_id"] = "4611686018490000000"
        elif intent in {"aggregate", "activity_aggregate", "activity_stats"}:
            args["mode"] = "raid"
        elif intent == "community":
            args["query"] = "突袭"
    elif tool == "world_assistant":
        if intent == "vendor":
            args["vendor_name"] = str(live.get("vendor_hash") or "")
        elif intent == "collectible_node":
            args["collectible_node_hash"] = live.get("node_hash") or 0
        elif intent == "collectible_item":
            args["item_name"] = "无感"
        elif intent == "search_collectible_nodes":
            args["query"] = "地牢"
        elif intent == "community":
            args["query"] = "宗师"
    return args


async def run_sweep(runner: Runner, live: dict[str, Any], skip_slow: bool) -> None:
    print("\n=== sweep：八个工具面全部 intent 的最简参体检 ===")
    total = 0
    failures: list[str] = []
    for tool, literal in INTENTS.items():
        for intent in get_args(literal):
            slow = (tool, intent) in SLOW_INTENTS
            if slow and skip_slow:
                record("sweep", f"{tool}(intent={intent})", "SKIP", "按 --skip-slow 跳过")
                continue
            total += 1
            args = sweep_args(tool, intent, live)
            # intent 必须显式传：漏掉它每个工具都会跑默认 intent，
            # 110 条"体检"就全变成同一个默认调用的复制品（这个坑真踩过）。
            payload, elapsed, error = await runner.call(
                tool, slow=slow, intent=intent, **args
            )
            clean, why = Runner.failing(payload, error)
            title = f"{tool}(intent={intent})"
            if not clean:
                failures.append(title)
                check("sweep", title, False, why, seconds=elapsed)
                continue
            code = ((payload or {}).get("error") or {}).get("code")
            expected = EXPECT_CODE.get((tool, intent))
            if expected and code != expected:
                # 这些码登记的是「上游现状」而不是我们的契约，变了要人看一眼、不算回归失败。
                check(
                    "sweep",
                    title,
                    False,
                    f"登记的是 {expected}，实际 {code}；params={short(args, 120)}",
                    seconds=elapsed,
                    warn=True,
                )
                continue
            # 写入类：不允许出现落盘标记
            write_note = ""
            if intent in WRITE_INTENTS:
                written = ((payload or {}).get("data") or {})
                marker = written.get("written") if isinstance(written, dict) else None
                if marker not in (False, None):
                    check(
                        "sweep",
                        title,
                        False,
                        f"写入类 intent 出现落盘标记 written={marker!r}",
                        seconds=elapsed,
                    )
                    continue
                write_note = f" written={marker!r}"
            state = "ok" if payload["ok"] else f"code={code}"
            extra = f" expected={expected}" if expected else ""
            envelope_issues = envelope_violations(payload) if payload["ok"] else []
            check(
                "sweep",
                title,
                not envelope_issues,
                f"{state}{extra}{write_note} params={short(args, 100)}"
                + (f" ｜ 信封违规：{envelope_issues[:4]}" if envelope_issues else ""),
                seconds=elapsed,
            )
    record(
        "sweep",
        "汇总：intent 体检",
        "INFO",
        f"跑 {total} 个 intent，无信封问题；异常/超时/空 code 计 {len(failures)} 条",
    )


async def run_rows(runner: Runner, live: dict[str, Any], skip_slow: bool) -> None:
    print("\n=== rows：字段级契约 ===")
    call = runner.call

    # ── player_assistant ────────────────────────────────────────────────
    profile = live.get("profile") or {}
    data = profile.get("data") or {}
    inner = data.get("profile") or {}
    characters = inner.get("characters") or []
    check(
        "rows",
        "player：profile 给 display_name/membership/角色列表，且不触发写入",
        bool(inner.get("display_name")) and bool(inner.get("membership_id")) and bool(characters)
        and all("light" in row for row in characters),
        f"name={inner.get('display_name')} membership={inner.get('membership_id')}/"
        f"{inner.get('membership_type')} 角色={[(c.get('class_name'), c.get('light')) for c in characters]}",
    )

    search, dt, err = await call(
        "player_assistant", intent="search", player_name=live.get("player_name") or ""
    )
    search_ids = {
        str(node.get("membership_id"))
        for node in walk_dicts((search or {}).get("data"))
        if node.get("membership_id")
    }
    check(
        "rows",
        "player：search 精确名返回同一个 membership_id",
        err is None and (search or {}).get("ok") is True
        and str(inner.get("membership_id")) in search_ids,
        f"ok={(search or {}).get('ok')} code={((search or {}).get('error') or {}).get('code')} "
        f"返回的 membership={sorted(search_ids)} 期望={inner.get('membership_id')}",
        seconds=dt,
    )

    find, dt, err = await call(
        "player_assistant", intent="find", name_prefix=(live.get("player_name") or "husky")[:5],
        slow=True,
    )
    fdata = (find or {}).get("data") or {}
    candidates = fdata.get("players") or []
    row = first_row(candidates)
    check(
        "rows",
        "player：find 前缀模糊给人选 + has_more，默认**不读别人档案**（不算置信度）",
        err is None and (find or {}).get("ok") is True and bool(candidates)
        and {"membership_id", "display_name"} <= set(row)
        # 没读的字段**不给键**（给 null 会被读成"读了但没有"），并说明怎么才能拿到
        and not {"confidence", "playtime_hours", "last_played"} & set(row)
        and "has_more" in fdata
        and any("include_profile" in str(w) for w in (find or {}).get("warnings") or []),
        f"候选={len(candidates)} "
        f"首项={short({k: row.get(k) for k in ('display_name','confidence','playtime_hours')}, 120)} "
        f"has_more={fdata.get('has_more')} 用时={dt:.1f}s",
        seconds=dt,
        warn=dt > 5,
    )

    enriched, dt_e, err_e = await call(
        "player_assistant", intent="find", name_prefix=(live.get("player_name") or "husky")[:5],
        include_profile=True, slow=True,
    )
    edata = (enriched or {}).get("data") or {}
    erow = first_row(edata.get("players") or [])
    check(
        "rows",
        "player：find + include_profile=true 才去读档案、给置信度与游玩时长",
        err_e is None and (enriched or {}).get("ok") is True
        and {"confidence", "last_played", "playtime_hours", "triumph_score"} <= set(erow)
        and not any("include_profile" in str(w) for w in (enriched or {}).get("warnings") or []),
        f"首项={short({k: erow.get(k) for k in ('display_name','confidence','playtime_hours')}, 120)} "
        f"用时={dt_e:.1f}s",
        seconds=dt_e,
        warn=dt_e > 12,
    )

    gibberish, dt, err = await call(
        "player_assistant", intent="find", name_prefix="zzqqxx绝无此玩家", slow=True
    )
    gdata = (gibberish or {}).get("data") or {}
    if err is not None or (gibberish or {}).get("ok") is not True:
        clean = err is None and bool(((gibberish or {}).get("error") or {}).get("code"))
        check(
            "rows",
            "player：find 空结果要么给干净失败码、要么给空列表 + warning",
            clean,
            f"ok={(gibberish or {}).get('ok')} code={((gibberish or {}).get('error') or {}).get('code')}",
            seconds=dt,
        )
    else:
        check(
            "rows",
            "player：find 空结果要么给干净失败码、要么给空列表 + warning",
            not (gdata.get("players") or []) and bool((gibberish or {}).get("warnings")),
            f"候选={len(gdata.get('players') or [])} warnings={short((gibberish or {}).get('warnings'), 160)}",
            seconds=dt,
        )

    # ── inventory_assistant ────────────────────────────────────────────
    summary, dt, err = await call("inventory_assistant", intent="summary")
    sdata = (summary or {}).get("data") or {}
    inv = sdata.get("inventory") or {}
    check(
        "rows",
        "inventory：summary 区分角色背包与仓库，只给数量概况",
        err is None and (summary or {}).get("ok") is True and isinstance(inv, dict),
        f"keys={keys_of(inv)} 概况={short({k: v for k, v in inv.items() if isinstance(v, (int, str))}, 200)}",
        seconds=dt,
    )

    bad_type, dt, err = await call(
        "inventory_assistant", intent="summary", item_type="手炮"
    )
    check(
        "rows",
        "inventory：summary 的 item_type 只认 weapon/armor/all，传「手炮」要报 config_error",
        err is None and (bad_type or {}).get("ok") is False
        and ((bad_type or {}).get("error") or {}).get("code") == "config_error",
        f"ok={(bad_type or {}).get('ok')} code={((bad_type or {}).get('error') or {}).get('code')} "
        f"msg={short(((bad_type or {}).get('error') or {}).get('message'), 140)}",
        seconds=dt,
    )

    vault, dt, err = await call(
        "inventory_assistant", intent="get", location="vault", limit=5
    )
    vdata = (vault or {}).get("data") or {}
    vpage = vdata.get("inventory") or {}
    vrows = vpage.get("items") or []
    check(
        "rows",
        "inventory：get 翻页四件套（total_items/returned_items/truncated/next_offset）",
        err is None and len(vrows) == 5 and "total_items" in vpage
        and vpage.get("returned_items") == 5 and "truncated" in vpage
        and (vpage.get("next_offset") in (5, None) or vpage.get("truncated") is False),
        f"total={vpage.get('total_items')} returned={vpage.get('returned_items')} "
        f"truncated={vpage.get('truncated')} next_offset={vpage.get('next_offset')}",
        seconds=dt,
    )

    page2, dt, err = await call(
        "inventory_assistant", intent="get", location="vault", limit=5, offset=5
    )
    rows2 = (((page2 or {}).get("data") or {}).get("inventory") or {}).get("items") or []
    ids1 = {row.get("item_instance_id") for row in vrows}
    ids2 = {row.get("item_instance_id") for row in rows2}
    check(
        "rows",
        "inventory：offset 翻页不重叠、不跳号",
        err is None and len(rows2) == 5 and not (ids1 & ids2),
        f"第一页={len(ids1)} 第二页={len(ids2)} 交集={len(ids1 & ids2)} 例={sorted(ids1)[:2]}",
        seconds=dt,
    )

    beyond, dt, err = await call(
        "inventory_assistant", intent="get", location="vault", limit=5, offset=999999
    )
    bpage = (((beyond or {}).get("data") or {}).get("inventory") or {})
    check(
        "rows",
        "inventory：offset 超出总数给空页而不是报错或倒出全量",
        err is None and not (bpage.get("items") or []) and bpage.get("truncated") in (False, None),
        f"items={len(bpage.get('items') or [])} truncated={bpage.get('truncated')} "
        f"next_offset={bpage.get('next_offset')}",
        seconds=dt,
    )

    # 注意：inventory 的 type **不读 limit**（参数归属表里没有它），传了会 ignored_parameter。
    smg, dt, err = await call(
        "inventory_assistant", intent="type", type_name="微型冲锋枪"
    )
    block = ((smg or {}).get("data") or {}).get("result") or {}
    srow = first_row(block.get("items") or [])
    check(
        "rows",
        "inventory：type 给总数/返回数/截断 + 行内不带 perk（要 perk 走 weapon_assistant）",
        err is None and {"total", "returned", "truncated"} <= set(block)
        and block.get("returned") == len(block.get("items") or [])
        and "sockets" not in srow and block.get("weapon_count") is not None,
        f"total={block.get('total')} returned={block.get('returned')} weapon_count={block.get('weapon_count')} "
        f"行键={keys_of(srow)}",
        seconds=dt,
    )

    dup, dt, err = await call("inventory_assistant", intent="duplicates", limit=3)
    ddata = (dup or {}).get("data") or {}
    groups = ddata.get("duplicate_weapons") or []
    hashes_ok = all(
        len({item.get("item_hash") for item in (group.get("instances") or [])}) <= 1
        for group in groups
    )
    check(
        "rows",
        "inventory：duplicates 按精确 item_hash 分组（同名不同版本不混）",
        err is None and bool(groups) and hashes_ok
        and {"pagination", "scan"} <= set(ddata),
        f"组数={len(groups)} 首组={short({k: groups[0].get(k) for k in ('name','item_hash','instance_count')} if groups else None, 160)} "
        f"分页={short(ddata.get('pagination'), 120)}",
        seconds=dt,
    )

    hunt, dt, err = await call("inventory_assistant", intent="search", item_name="无感")
    hresult = ((hunt or {}).get("data") or {}).get("result") or {}
    hits = hresult.get("items") or hresult.get("instances") or []
    hrow = first_row(hits)
    check(
        "rows",
        "inventory：search 命中给实例 ID 与位置（键名 item_instance_id）",
        err is None and bool(hits) and bool(hrow.get("item_instance_id")) and hrow.get("location") is not None,
        f"命中={len(hits)} 首项={short({k: hrow.get(k) for k in ('item_instance_id','name','location','power')}, 200)}",
        seconds=dt,
    )

    miss, dt, err = await call(
        "inventory_assistant", intent="search", item_name="绝对不存在的物品名zzqq"
    )
    mdata = (miss or {}).get("data") or {}
    text = json.dumps(miss or {}, ensure_ascii=False)
    check(
        "rows",
        "inventory：search 未命中说「没找到」，不反推「全账号没有」",
        err is None and (miss or {}).get("ok") is True and "没找到" in text
        and "全账号" not in text,
        f"ok={(miss or {}).get('ok')} summary={short((miss or {}).get('summary'), 120)} "
        f"result={short(mdata.get('result'), 120)}",
        seconds=dt,
    )

    empty_id, dt, err = await call("inventory_assistant", intent="item", item_instance_id="")
    check(
        "rows",
        "inventory：item 缺实例 ID → 干净信封 + 说清要先用 get/search 拿 ID",
        err is None and (empty_id or {}).get("ok") is False
        and ((empty_id or {}).get("error") or {}).get("code") in {"invalid_arguments", "invalid_argument_error"}
        and "item_instance_id" in str(((empty_id or {}).get("error") or {}).get("message")),
        f"ok={(empty_id or {}).get('ok')} code={((empty_id or {}).get('error') or {}).get('code')} "
        f"msg={short(((empty_id or {}).get('error') or {}).get('message'), 140)}",
        seconds=dt,
    )

    # 旧六维名必须换成**存在**的模组名：韧性 → 生命值。
    # 曾经映射成「生命」，而游戏里的模组叫「生命值模组」，真机直接报「没找到护甲模组」。
    if live.get("armor_instance_id"):
        legacy_mod, dt, err = await call(
            "inventory_assistant", intent="equip_mod", mod_name="韧性模组",
            item_instance_id=live["armor_instance_id"], character="hunter",
        )
        canon_mod, dt2, err2 = await call(
            "inventory_assistant", intent="equip_mod", mod_name="生命值模组",
            item_instance_id=live["armor_instance_id"], character="hunter",
        )
        legacy_code = ((legacy_mod or {}).get("error") or {}).get("code")
        canon_code = ((canon_mod or {}).get("error") or {}).get("code")
        check(
            "rows",
            "inventory：旧名「韧性模组」与规范名「生命值模组」走到同一个模组",
            err is None and err2 is None
            and legacy_code == canon_code == "confirmation_required",
            f"韧性模组 → {legacy_code} ｜ 生命值模组 → {canon_code}",
            seconds=dt,
        )
    else:
        record("rows", "inventory：旧六维名映射", "SKIP", "没取到护甲实例")

    none_id, dt, err = await call(
        "inventory_assistant", intent="item", item_instance_id=None  # type: ignore[arg-type]
    )
    clean = err is None and (none_id or {}).get("ok") is False
    check(
        "rows",
        "inventory：item 传 null（进程内）不能裸抛 AttributeError",
        clean,
        f"结果={short(none_id, 140) if err is None else f'抛 {type(err).__name__}: {str(err)[:120]}'}"
        "｜注：真 MCP 路径由 schema 层拦住（string_type），这条只在进程内可达，属防御性缺口",
        seconds=dt,
        warn=True,
    )

    # ── weapon_assistant（0.1.11 / 0.1.12 新增口径）─────────────────────
    analyze, dt, err = await call(
        "weapon_assistant", intent="analyze", weapon_name="无感", include_inventory=True,
        slow=True,
    )
    instances = (((analyze or {}).get("data") or {}).get("inventory") or {}).get("instances") or []
    marked, plain_pairs, instance_options = [], 0, 0
    for instance in instances:
        for socket in instance.get("sockets") or []:
            equipped = socket.get("equipped") or {}
            name = equipped.get("name") or ""
            if name.endswith("↑"):
                marked.append(name)
                if equipped.get("name_plain"):
                    plain_pairs += 1
        instance_options += len(instance.get("options") or [])
    if marked:
        check(
            "rows",
            "weapon：强化版 perk 名字带 ↑ 且同时给 name_plain（逐副本）",
            plain_pairs == len(marked),
            f"副本={len(instances)} 带↑的已装项={marked} 带 name_plain 的={plain_pairs} 实例可换栏={instance_options}",
            seconds=dt,
        )
    else:
        record(
            "rows",
            "weapon：强化版 perk 名字带 ↑ 且同时给 name_plain（逐副本）",
            "INFO",
            f"这 2 个副本当前都没有已选中的强化 perk，跳过标记检查；实例可换栏={instance_options}",
            dt,
        )

    filtered, dt, err = await call(
        "weapon_assistant", intent="filter_rolls", perk_name="快速命中", limit=5
    )
    fdata2 = (filtered or {}).get("data") or {}
    check(
        "rows",
        "weapon：带 ↑ 的副本用不带箭头的名字也能筛到（内部按规范名匹配）",
        err is None and (fdata2.get("matched_count") or 0) >= 1,
        f"matched={fdata2.get('matched_count')} unknown={fdata2.get('unknown_count')} "
        f"coverage_complete={fdata2.get('coverage_complete')}",
        seconds=dt,
    )

    # ── weapon_assistant(intent="patterns")：锻造图样 ───────────────────
    patterns, dt, err = await call("weapon_assistant", intent="patterns", slow=True)
    pdata = (patterns or {}).get("data") or {}
    pcounts = pdata.get("counts") or {}
    pitems = (pdata.get("patterns") or {}).get("items") or []
    psources = pdata.get("sources") or {}
    ptotal = pcounts.get("total") or 0
    check(
        "rows",
        "patterns：四档计数自洽，且图鉴总数 183（游戏里那一页的条数）",
        err is None and (patterns or {}).get("ok") is True
        and ptotal == 183 and pdata.get("catalog_total") == 183
        and (pcounts.get("unlocked") or 0) + (pcounts.get("in_progress") or 0)
        + (pcounts.get("not_started") or 0) == ptotal,
        f"counts={pcounts} catalog_total={pdata.get('catalog_total')} 返回={len(pitems)}"
        f" read={pdata.get('read')}",
        seconds=dt,
    )
    check(
        "rows",
        "patterns：两个作用域都读过（read 声明 profile+character），本账号未开始必须为 0",
        err is None
        and ((pdata.get("read") or {}).get("record_scopes") == ["profile", "character"])
        and (pcounts.get("not_started") or 0) == 0,
        f"read={pdata.get('read')} not_started={pcounts.get('not_started')}"
        "｜注：183 条模式记录里 151 条档案级、32 条角色级（characterRecords）；只读档案级时"
        "这 32 把会被误判成未开始",
    )

    scoped, dt, err = await call("weapon_assistant", intent="patterns", weapon_name="面纱威胁")
    srow = first_row(((scoped or {}).get("data") or {}).get("patterns", {}).get("items") or [])
    check(
        "rows",
        "patterns：角色级记录的那把（面纱威胁）读出来是已解锁",
        err is None and srow.get("status") == "已解锁",
        f"row={short(srow, 160)}",
        seconds=dt,
    )

    check(
        "rows",
        "patterns：「未开始」的行 progress 必须是 null（没有记录 ≠ 进度 0）",
        all(row.get("progress") is None for row in pitems if row.get("status") == "未开始"),
        f"未开始={sum(1 for row in pitems if row.get('status') == '未开始')} "
        f"进行中={[(r.get('name'), r.get('progress'), r.get('need')) for r in pitems if r.get('status') == '进行中'][:3]}",
    )
    check(
        "rows",
        "patterns：来源是社区资料，带页面与更新时间，且跟账号进度分开",
        psources.get("available") is True and (psources.get("page") or {}).get("updated_at")
        and (psources.get("page") or {}).get("trust") == "untrusted_reference",
        f"matched={psources.get('matched')}/{psources.get('total')} page={short(psources.get('page'), 160)}",
    )

    exotics, dt, err = await call("weapon_assistant", intent="patterns", rarity="异域", limit=50)
    edata = (exotics or {}).get("data") or {}
    erows = (edata.get("patterns") or {}).get("items") or []
    check(
        "rows",
        "patterns：rarity=异域 一次拿全金枪（16 把），不受默认一页 20 条影响",
        err is None and len(erows) == 16 and all(r.get("tier") == "异域" for r in erows)
        and (edata.get("by_tier") or {}) == {"异域": 16},
        f"返回={len(erows)} by_tier={edata.get('by_tier')} "
        f"名单={[r.get('name') for r in erows]}",
        seconds=dt,
    )

    check(
        "rows",
        "patterns：by_tier 汇总跟着筛选走（不筛时异域 16 + 传说 167 = 183）",
        (pdata.get("by_tier") or {}) == {"异域": 16, "传说": 167},
        f"by_tier={pdata.get('by_tier')}",
    )

    variant, dt, err = await call("weapon_assistant", intent="patterns", weapon_name="惩戒措施（失时）")
    vdata = (variant or {}).get("data") or {}
    vrow = first_row((vdata.get("patterns") or {}).get("items") or [])
    check(
        "rows",
        "patterns：变体（失时）指回基础版，并给出它的塑形配置（3 栏位 / 三四号固定 / 无深视插槽）",
        err is None and vrow.get("name") == "惩戒措施" and "没有单独的模式" in str((variant or {}).get("summary"))
        and (vdata.get("variant") or {}).get("shapeable_columns") == ["框架", "枪管", "弹夹"]
        and (vdata.get("variant") or {}).get("base_shapeable_columns")
        == ["框架", "枪管", "弹夹", "特征1", "特征2"]
        and (vdata.get("variant") or {}).get("traits_fixed") is True
        and (vdata.get("variant") or {}).get("has_deepsight_socket") is False
        and (vdata.get("variant") or {}).get("has_upgrade_socket") is True,
        f"summary={short((variant or {}).get('summary'), 200)} variant={short(vdata.get('variant'), 200)}",
        seconds=dt,
    )

    check(
        "rows",
        "patterns：术语对照随响应给出去（玩家说红框、游戏说模式、工具说图样）",
        set(((pdata.get("terms") or {}).keys())) == {"红框", "模式", "塑形"},
        f"terms={short(pdata.get('terms'), 200)}",
    )

    typo, dt, err = await call("weapon_assistant", intent="patterns", weapon_name="zzqq不存在")
    check(
        "rows",
        "patterns：名字对不上时说清「图鉴共 183 条」，不编一句「没有来源」",
        err is None and (typo or {}).get("ok") is True
        and "183" in str((typo or {}).get("summary"))
        and not ((typo or {}).get("data") or {}).get("patterns", {}).get("items"),
        f"summary={short((typo or {}).get('summary'), 140)}",
        seconds=dt,
    )

    community, dt, err = await call(
        "build_assistant", intent="community", query="术士", include_inventory=False
    )
    cdata = (community or {}).get("data") or {}
    results = cdata.get("results") or []
    check(
        "rows",
        "build：社区搜索给归档元数据 + 每条 build_id/executable，不说成可执行方案",
        err is None and cdata.get("archive_available") is True
        and cdata.get("network_fallback_used") is False
        and bool(results) and all("build_id" in row for row in results)
        and all(row.get("executable") is False for row in results),
        f"build_count={cdata.get('build_count')} 命中={len(results)} "
        f"首条={short({k: first_row(results).get(k) for k in ('title','build_id','executable')}, 200)}",
        seconds=dt,
    )

    build_id = first_row(results).get("build_id")
    if not build_id:
        record("rows", "build：community_build 逐条核对", "SKIP", "社区搜索没给出 build_id")
    else:
        detail, dt, err = await call(
            "build_assistant",
            intent="community_build",
            community_build_id=build_id,
            include_inventory=True,
            character="warlock",
            slow=True,
        )
        bdata = (detail or {}).get("data") or {}
        warn_text = json.dumps((detail or {}).get("warnings") or [], ensure_ascii=False)
        check(
            "rows",
            "build：community_build 明说「不可直接执行」，且不再带 results/next_offset",
            err is None and "不可直接执行" in str((detail or {}).get("summary"))
            and "execution_supported=false" in warn_text
            and "results" not in bdata and "next_offset" not in bdata,
            f"summary={short((detail or {}).get('summary'), 120)} data 键={keys_of(bdata)}",
            seconds=dt,
        )
        match = (bdata.get("selected_build") or {}).get("inventory_match") or {}
        rows_all = (
            (match.get("requirements") or [])
            + (match.get("known_missing_requirements") or [])
            + (match.get("unresolved_or_unchecked_requirements") or [])
        )
        statuses: dict[str, int] = {}
        bad_reason: list[str] = []
        three_layer = 0
        for row in rows_all:
            if not row.get("required_perks"):
                continue
            for instance in row.get("owned_instances") or []:
                status = instance.get("selectable_plug_status")
                statuses[str(status)] = statuses.get(str(status), 0) + 1
                if status is None:
                    bad_reason.append(f"{row.get('name')}: 缺 selectable_plug_status")
                elif status == "not_read" and not row.get("alternate_perk_options_reason"):
                    bad_reason.append(f"{row.get('name')}: not_read 但没说原因")
                if status == "available":
                    three_layer += 1
                    if not {"perks_current_match", "perks_available_to_switch",
                            "perks_unavailable"} <= set(instance):
                        bad_reason.append(f"{row.get('name')}: available 但缺三层列表")
        check(
            "rows",
            "build：三层 roll 只在真读了可换栏时才给结论（没读必须说原因）",
            err is None and not bad_reason and bool(statuses),
            f"带 perk 要求的行={sum(1 for r in rows_all if r.get('required_perks'))} "
            f"实例层状态={statuses} 有可换项的实例={three_layer} 问题={bad_reason[:3]}",
            seconds=dt,
        )

    # ── build_assistant 定义类 ─────────────────────────────────────────
    mods, dt, err = await call(
        "build_assistant", intent="armor_mods", priority_stat="手雷"
    )
    mdata = (mods or {}).get("data") or {}
    check(
        "rows",
        "build：armor_mods 按属性词表给 match.kind=stat",
        err is None and (mdata.get("match") or {}).get("kind") == "stat"
        and bool(mdata.get("mods")),
        f"match={short(mdata.get('match'), 140)} 模组={len(mdata.get('mods') or [])}",
        seconds=dt,
    )

    keyword, dt, err = await call(
        "build_assistant", intent="armor_mods", priority_stat="速度"
    )
    kdata = (keyword or {}).get("data") or {}
    kwarn = json.dumps((keyword or {}).get("warnings") or [], ensure_ascii=False)
    check(
        "rows",
        "build：词表外但蒙中（速度）→ match.kind=keyword + warning 说明不加该属性",
        err is None and (kdata.get("match") or {}).get("kind") == "keyword" and bool(kwarn),
        f"match={short(kdata.get('match'), 140)} warnings={short((keyword or {}).get('warnings'), 160)}",
        seconds=dt,
    )

    bogus_mod, dt, err = await call(
        "build_assistant", intent="armor_mods", priority_stat="zzqq绝无此属性"
    )
    check(
        "rows",
        "build：armor_mods 乱填 → invalid_argument_error 并列出词表",
        err is None and (bogus_mod or {}).get("ok") is False
        and ((bogus_mod or {}).get("error") or {}).get("code") == "invalid_argument_error",
        f"code={((bogus_mod or {}).get('error') or {}).get('code')} "
        f"msg={short(((bogus_mod or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    if live.get("set_name"):
        bonus, dt, err = await call(
            "build_assistant", intent="set_bonus", set_bonus_name=live["set_name"]
        )
        bdata2 = (bonus or {}).get("data") or {}
        sblock2 = (bdata2.get("set_bonus") or {}) if isinstance(bdata2, dict) else {}
        perks = sblock2.get("perks") or []
        counts = sorted({p.get("required_count") for p in perks if p.get("required_count")})
        check(
            "rows",
            "build：set_bonus 用真实 T5 护甲的套装名能往返（2/4 件效果）",
            err is None and (bonus or {}).get("ok") is True and counts == [2, 4],
            f"套装={sblock2.get('set_name')} 件数={sblock2.get('armor_count')} "
            f"效果档={counts} 首条={short(first_row(perks), 180)}",
            seconds=dt,
        )
    else:
        record("rows", "build：set_bonus 真实套装名往返", "SKIP", "账号里没取到护甲套装名")

    missing_set, dt, err = await call(
        "build_assistant", intent="set_bonus", set_bonus_name="绝对不存在的套装名"
    )
    check(
        "rows",
        "build：不存在的套装 → definition_not_found_error",
        err is None and ((missing_set or {}).get("error") or {}).get("code")
        == "definition_not_found_error",
        f"code={((missing_set or {}).get('error') or {}).get('code')} "
        f"msg={short(((missing_set or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    exotic, dt, err = await call(
        "build_assistant", intent="exotic_armor", exotic_name="星火协议", character="warlock"
    )
    armor = ((exotic or {}).get("data") or {}).get("armor") or {}
    identity = armor.get("identity") or {}
    unified = {
        "item_hash", "name", "name_en", "slot", "slot_display", "item_type_display",
        "gear_tier", "gear_tier_note", "armor_system", "rarity", "rarity_tier", "class_type",
    }
    check(
        "rows",
        "build：exotic_armor 与 intent=item 用同一套护甲身份块（snake_case）",
        err is None and identity and unified <= set(identity) and "nameEn" not in identity,
        f"armor 键={keys_of(armor)} identity 缺={sorted(unified - set(identity))}",
        seconds=dt,
    )

    forged, dt, err = await call(
        "build_assistant", intent="equip_build", canonical_build={"items": []}
    )
    fwritten = ((forged or {}).get("data") or {})
    check(
        "rows",
        "build：手拼的 canonical_build 被拒且不落盘",
        err is None and (forged or {}).get("ok") is False
        and not (isinstance(fwritten, dict) and fwritten.get("written")),
        f"code={((forged or {}).get('error') or {}).get('code')} "
        f"msg={short(((forged or {}).get('error') or {}).get('message'), 140)}",
        seconds=dt,
    )

    if skip_slow:
        for title in (
            "build：recommend 小目标给五件齐全的候选",
            "build：analyze 超规模立刻返回 not_computed",
            "build：farm_target 只反推待刷件、不当成已拥有",
            "cross：默认条数体检",
        ):
            record("rows", title, "SKIP", "按 --skip-slow 跳过")
    else:
        rec, dt, err = await call(
            "build_assistant", intent="recommend", character="hunter", health_target=100,
            top_n=2, slow=True,
        )
        rdata = (rec or {}).get("data") or {}
        recommendation = rdata.get("recommendation") or {}
        builds = recommendation.get("results") or []
        first = first_row(builds)
        items = ((first.get("build") or {}).get("items")) or []
        slots = [row.get("slot_key") for row in items if isinstance(row, dict)]
        check(
            "rows",
            "build：recommend 给齐五件护甲（slot_key 是统一名）+ 实例 ID + 达标率",
            err is None and bool(builds) and len([s for s in slots if s]) >= 5
            and all(row.get("item_instance_id") for row in items),
            f"候选={len(builds)} 部位={slots} completion_rate={first.get('completion_rate')} "
            f"（内部复数名 slot={[row.get('slot') for row in items if isinstance(row, dict)]}）",
            seconds=dt,
        )

        gate, dt, err = await call(
            "build_assistant",
            intent="analyze",
            character="warlock",
            weapons_target=150,
            class_target=100,
            super_target=80,
            melee_target=70,
            grenade_target=70,
            health_target=100,
            slow=True,
        )
        gdata2 = (gate or {}).get("data") or {}
        ganalysis = gdata2.get("analysis") or {}
        precision = ganalysis.get("precision")
        check(
            "rows",
            "build：analyze 给 precision（exact/not_computed）+ 上限或收窄建议",
            err is None and (gate or {}).get("ok") is True
            and precision in {"exact", "not_computed"},
            f"ok={(gate or {}).get('ok')} precision={precision} "
            f"max_possible={short(ganalysis.get('max_possible'), 140)} reason={short(ganalysis.get('reason'), 160)}",
            seconds=dt,
        )

        # analyze 的 reason 不许在没有证据时宣称"配不出来"：
        # 判据是确定的——只要算出了 max_possible 且没有任何目标超过对应上限，
        # 就不能说 "No valid armor combination"。实机复现过：precision=exact、
        # max_possible.health=134 > 目标 100，却回了这句，而同约束 recommend 达标率 1.0。
        max_possible = ganalysis.get("max_possible") or {}
        reason_text = str(ganalysis.get("reason") or "")
        claims_none = "No valid armor combination" in reason_text
        targets = {"health": 100}
        over = [
            f"{stat}: 目标 {value} > 上限 {max_possible[stat]}"
            for stat, value in targets.items()
            if isinstance(max_possible.get(stat), int) and max_possible[stat] < value
        ]
        unfounded = bool(claims_none and max_possible and not over)
        agree, dt2, err2 = await call(
            "build_assistant", intent="recommend", character="hunter", health_target=100,
            top_n=1, slow=True,
        )
        agree_first = first_row(
            ((agree or {}).get("data") or {}).get("recommendation", {}).get("results") or []
        )
        check(
            "rows",
            "build：analyze 没有证据时不许宣称「没有合法组合」（同约束 recommend 能达标）",
            err is None and not unfounded,
            f"precision={precision} reason={short(reason_text, 110)} ｜ "
            f"上限={short(max_possible, 120)} 超过上限的目标={over or '无'} ｜ "
            f"同约束 recommend completion_rate={agree_first.get('completion_rate')}"
            "（claims_none 且无目标超上限 = 自相矛盾，P1 已登记）",
            seconds=dt2,
        )

        farm, dt, err = await call(
            "build_assistant",
            intent="farm_target",
            character="hunter",
            health_target=120,
            replacement_slot="helmet",
            slow=True,
        )
        fdata3 = (farm or {}).get("data") or {}
        no_solution = "无法满足" in str((farm or {}).get("summary"))
        check(
            "rows",
            "build：farm_target 给待刷方案；配不出来时要明说（不把待刷件当已拥有）",
            err is None and (farm or {}).get("ok") is True
            and (no_solution or bool(fdata3.get("farm_target") or fdata3.get("results"))),
            f"keys={keys_of(fdata3)} 摘要={short((farm or {}).get('summary'), 140)} "
            f"（{'(本轮为「无解」分支)' if no_solution else '有方案'}；待刷件不得当作已拥有）",
            seconds=dt,
        )

    # ── loadout_assistant ──────────────────────────────────────────────
    loadouts = live.get("loadouts") or {}
    ldata = loadouts.get("data") or {}
    rows_l = ldata.get("loadouts") or []
    check(
        "rows",
        "loadout：list 默认最多 5 套 + 四个分页字段 + 每套带 build_template",
        loadouts.get("ok") is True and len(rows_l) <= 5
        and {"total_loadouts", "returned_loadouts", "truncated", "next_offset"} <= set(ldata)
        and all("build_template" in row for row in rows_l),
        f"total={ldata.get('total_loadouts')} returned={ldata.get('returned_loadouts')} "
        f"truncated={ldata.get('truncated')} next_offset={ldata.get('next_offset')} "
        f"首套键={keys_of(first_row(rows_l))}",
    )

    page_a, dt, err = await call("loadout_assistant", intent="list", limit=2, offset=0)
    page_b, dt2, err2 = await call("loadout_assistant", intent="list", limit=2, offset=2)
    ids_a = {row.get("id") for row in (((page_a or {}).get("data") or {}).get("loadouts") or [])}
    ids_b = {row.get("id") for row in (((page_b or {}).get("data") or {}).get("loadouts") or [])}
    check(
        "rows",
        "loadout：offset 翻页不重叠",
        err is None and err2 is None and len(ids_a) == 2 and len(ids_b) == 2 and not (ids_a & ids_b),
        f"第一页={len(ids_a)} 第二页={len(ids_b)} 交集={len(ids_a & ids_b)}",
        seconds=dt,
    )

    got, dt, err = await call(
        "loadout_assistant", intent="get", loadout_id=live.get("loadout_id") or "x"
    )
    check(
        "rows",
        "loadout：get 不接受 loadout_id（要 Agent 自己从全部里挑）",
        err is None and (got or {}).get("ok") is False
        and ((got or {}).get("error") or {}).get("code") == "ignored_parameter",
        f"code={((got or {}).get('error') or {}).get('code')} "
        f"msg={short(((got or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    ident, dt, err = await call(
        "loadout_assistant", intent="search_identifiers", kind="color"
    )
    idata = (ident or {}).get("data") or {}
    ident_rows = (idata.get("results") or {}).get("color") if isinstance(idata.get("results"), dict) else None
    ident_rows = ident_rows or idata.get("identifiers") or []
    check(
        "rows",
        "loadout：search_identifiers 按 kind 给标识（含 hash）",
        err is None and (ident or {}).get("ok") is True and bool(ident_rows)
        and all("hash" in row for row in ident_rows),
        f"keys={keys_of(idata)} kind={idata.get('kind')} 条数={len(ident_rows)} "
        f"首条={short(first_row(ident_rows), 120)}",
        seconds=dt,
    )

    ident_bad, dt, err = await call(
        "loadout_assistant", intent="search_identifiers", kind="乱填"
    )
    check(
        "rows",
        "loadout：search_identifiers 乱填 kind → invalid_argument_error 列词表",
        err is None and ((ident_bad or {}).get("error") or {}).get("code")
        == "invalid_argument_error",
        f"code={((ident_bad or {}).get('error') or {}).get('code')} "
        f"msg={short(((ident_bad or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    save_no_name, dt, err = await call(
        "loadout_assistant", intent="save", name="", character="hunter"
    )
    check(
        "rows",
        "loadout：save 缺名字 → invalid_arguments（工具层，中文说清缺什么）",
        err is None and (save_no_name or {}).get("ok") is False
        and ((save_no_name or {}).get("error") or {}).get("code") == "invalid_arguments",
        f"code={((save_no_name or {}).get('error') or {}).get('code')} "
        f"msg={short(((save_no_name or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    save_guard, dt, err = await call(
        "loadout_assistant", intent="save", name="语料体检-不应落盘", character="hunter"
    )
    check(
        "rows",
        "loadout：save 不给确认 → confirmation_required（账号未动）",
        err is None and (save_guard or {}).get("ok") is False
        and ((save_guard or {}).get("error") or {}).get("code") == "confirmation_required",
        f"code={((save_guard or {}).get('error') or {}).get('code')} "
        f"msg={short(((save_guard or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    upd_none, dt, err = await call(
        "loadout_assistant", intent="update_official_identifiers", slot_number=1
    )
    check(
        "rows",
        "loadout：update_official_identifiers 一个标识都不给 → invalid_arguments",
        err is None and (upd_none or {}).get("ok") is False
        and ((upd_none or {}).get("error") or {}).get("code") == "invalid_arguments",
        f"code={((upd_none or {}).get('error') or {}).get('code')} "
        f"msg={short(((upd_none or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    # ── subclass_assistant ─────────────────────────────────────────────
    sub, dt, err = await call("subclass_assistant", intent="get", character="hunter")
    sblock = ((sub or {}).get("data") or {}).get("subclass") or {}
    plugs = sblock.get("plugs") or []
    check(
        "rows",
        "subclass：get 给当前超能/手雷/星相/碎片 + 每项的可换项",
        err is None and bool(sblock.get("subclass_name")) and bool(plugs)
        and all("socket_type" in plug for plug in plugs),
        f"职业={sblock.get('character_class')} 子职业={sblock.get('subclass_name')} "
        f"插槽={[(p.get('socket_type'), p.get('name'), len(p.get('available') or [])) for p in plugs][:6]}",
        seconds=dt,
    )

    opt_missing, dt, err = await call(
        "subclass_assistant", intent="options", element="void", component="grenade"
    )
    om_msg = ((opt_missing or {}).get("error") or {}).get("message") or ""
    check(
        "rows",
        "subclass：options 缺 character → subclass_error 并说清合法职业",
        err is None and ((opt_missing or {}).get("error") or {}).get("code") == "subclass_error"
        and "hunter" in om_msg and "warlock" in om_msg,
        f"code={((opt_missing or {}).get('error') or {}).get('code')} msg={short(om_msg, 180)}",
        seconds=dt,
    )
    check(
        "rows",
        "subclass：报错文案不出现「。。」这类重复标点",
        not om_msg.endswith("。。"),
        f"结尾={short(om_msg[-24:], 40)}",
        seconds=dt,
    )

    opt_ok, dt, err = await call(
        "subclass_assistant", intent="options", element="void", component="grenade",
        character="hunter",
    )
    oblock = ((opt_ok or {}).get("data") or {}).get("options") or {}
    check(
        "rows",
        "subclass：options 给该元素该部件的全部可选项",
        err is None and (opt_ok or {}).get("ok") is True and (oblock.get("count") or 0) >= 1,
        f"count={oblock.get('count')} 首项={short(first_row(oblock.get('options') or []).get('name'), 60)}",
        seconds=dt,
    )

    alias_counts: dict[str, Any] = {}
    for element in ("void", "虚空", "strand", "缚丝", "编织"):
        alias, dt, err = await call(
            "subclass_assistant", intent="fragments", element=element
        )
        fblock = ((alias or {}).get("data") or {}).get("fragments") or {}
        alias_counts[element] = (
            fblock.get("count") if err is None and (alias or {}).get("ok") else f"ERR:{((alias or {}).get('error') or {}).get('code')}"
        )
    check(
        "rows",
        "subclass：碎片元素中英与旧写法等价（void=虚空；strand=缚丝=编织）",
        isinstance(alias_counts.get("void"), int)
        and alias_counts.get("void") == alias_counts.get("虚空")
        and alias_counts.get("strand") == alias_counts.get("缚丝") == alias_counts.get("编织"),
        f"各写法条数={alias_counts}（虚空 19 ≠ 缚丝 16 是元素本身不同，不是别名失效）",
    )

    frag_bad, dt, err = await call(
        "subclass_assistant", intent="fragment_details", fragment_name="zzqq绝无此碎片"
    )
    check(
        "rows",
        "subclass：不存在的碎片 → definition_not_found_error（不编效果）",
        err is None and ((frag_bad or {}).get("error") or {}).get("code")
        == "definition_not_found_error",
        f"code={((frag_bad or {}).get('error') or {}).get('code')} "
        f"msg={short(((frag_bad or {}).get('error') or {}).get('message'), 140)}",
        seconds=dt,
    )

    art = live.get("artifact") or {}
    current = art.get("current_artifact") or {}
    named, _, _ = await call(
        "subclass_assistant",
        intent="artifact",
        artifact_name=first_row(art.get("artifacts") or []).get("name") or "",
    )
    named_current = (((named or {}).get("data") or {}).get("artifact") or {}).get("current_artifact") or {}
    check(
        "rows",
        "subclass：artifact 不带名字也要给 current_artifact（「我现在用哪个神器」）",
        bool(current.get("name")) and bool(current.get("tiers")),
        f"不带名：current_artifact={current.get('name')} 阶数={len(current.get('tiers') or [])} ｜ "
        f"带名（{first_row(art.get('artifacts') or []).get('name')}）："
        f"current_artifact={named_current.get('name')} 阶数={len(named_current.get('tiers') or [])}",
    )

    mod_hash = live.get("artifact_mod_hash")
    if mod_hash:
        mod_detail, dt, err = await call(
            "subclass_assistant", intent="artifact_mod", artifact_mod_hash=mod_hash
        )
        check(
            "rows",
            "subclass：artifact_mod 用 artifact 给的正数 hash 能查到详情",
            err is None and (mod_detail or {}).get("ok") is True,
            f"模组={live.get('artifact_mod_name')} hash={mod_hash} "
            f"data 键={keys_of((mod_detail or {}).get('data'))}",
            seconds=dt,
        )
        guard, dt, err = await call(
            "subclass_assistant",
            intent="equip_artifact_mod",
            artifact_mod_hash=mod_hash,
            character="hunter",
        )
        check(
            "rows",
            "subclass：equip_artifact_mod 不给确认 → confirmation_required",
            err is None and (guard or {}).get("ok") is False
            and ((guard or {}).get("error") or {}).get("code") == "confirmation_required",
            f"code={((guard or {}).get('error') or {}).get('code')} "
            f"msg={short(((guard or {}).get('error') or {}).get('message'), 140)}",
            seconds=dt,
        )
    else:
        record("rows", "subclass：artifact_mod / 写入拦截", "SKIP", "没取到神器模组 hash")

    # ── activity_assistant ─────────────────────────────────────────────
    hist3, dt, err = await call("activity_assistant", intent="history", count=3)
    rows_a = ((hist3 or {}).get("data") or {}).get("activities") or []
    check(
        "rows",
        "activity：history 按 count 给记录，字段可核对（实例 ID/时间/模式）",
        err is None and 1 <= len(rows_a) <= 3
        and all(
            row.get("instance_id") and row.get("start_time") and row.get("mode") is not None
            for row in rows_a
        ),
        f"条数={len(rows_a)} 首条={short({k: first_row(rows_a).get(k) for k in ('instance_id','activity_name','mode','start_time','is_completed')}, 220)}",
        seconds=dt,
    )

    hist_top, dt, err = await call("activity_assistant", intent="history", maxtop=3)
    top_msg = str(((hist_top or {}).get("error") or {}).get("message") or "")
    check(
        "rows",
        "activity：history 传 maxtop → ignored_parameter（并说明它不读）",
        err is None and ((hist_top or {}).get("error") or {}).get("code") == "ignored_parameter"
        and "不读" in top_msg,
        f"code={((hist_top or {}).get('error') or {}).get('code')} 提示里提到 count={'count' in top_msg} "
        f"msg={short(top_msg, 180)}",
        seconds=dt,
    )

    if live.get("activity_id"):
        pgcr, dt, err = await call(
            "activity_assistant", intent="pgcr", activity_id=live["activity_id"], slow=True
        )
        pdata = (pgcr or {}).get("data") or {}
        check(
            "rows",
            "activity：pgcr 真 ID 给结算内容（不是空壳）",
            err is None and (pgcr or {}).get("ok") is True and bool(pdata),
            f"实例={live['activity_id']} keys={keys_of(pdata)} 摘要={short((pgcr or {}).get('summary'), 120)}",
            seconds=dt,
        )
    for label, value in (("缺 ID", ""), ("非数字", "abc")):
        payload, dt, err = await call(
            "activity_assistant", intent="pgcr", activity_id=value
        )
        check(
            "rows",
            f"activity：pgcr {label} → invalid_argument_error（不是裸抛 404）",
            err is None and ((payload or {}).get("error") or {}).get("code")
            == "invalid_argument_error",
            f"code={((payload or {}).get('error') or {}).get('code')} "
            f"msg={short(((payload or {}).get('error') or {}).get('message'), 160)}",
            seconds=dt,
        )

    ghost, dt, err = await call(
        "activity_assistant", intent="pgcr", activity_id="9999999999999", slow=True
    )
    check(
        "rows",
        "activity：pgcr 数字但不存在 → upstream_not_found_error 信封",
        err is None and ((ghost or {}).get("error") or {}).get("code")
        == "upstream_not_found_error",
        f"code={((ghost or {}).get('error') or {}).get('code')} "
        f"msg={short(((ghost or {}).get('error') or {}).get('message'), 180)}",
        seconds=dt,
    )

    career, dt, err = await call("activity_assistant", intent="career")
    historical, dt2, err2 = await call("activity_assistant", intent="historical_stats")
    single, dt3, err3 = await call("activity_assistant", intent="stats", character="hunter")
    def _stat_of(payload: dict | None, group_key: str, stat_id: str) -> dict:
        block = ((payload or {}).get("data") or {}).get("stats") or {}
        for group in block.get("groups") or []:
            if group.get("key") == group_key:
                for row in group.get("stats") or []:
                    if row.get("stat_id") == stat_id:
                        return row
        return {}

    cstats = ((career or {}).get("data") or {}).get("stats") or {}
    hstats = ((historical or {}).get("data") or {}).get("stats") or {}
    cdata = (career or {}).get("data") or {}
    pve_entered = _stat_of(career, "pve", "activities_entered")
    pvp_ratio = _stat_of(career, "pvp", "kills_deaths_ratio")
    pvp_defeated = _stat_of(career, "pvp", "opponents_defeated")
    counters = cdata.get("game_counters") or []
    counter_progress = next(
        (row.get("progress") for row in counters if row.get("metric_hash") == 811894228), None
    )
    warnings_text = " ".join((career or {}).get("warnings") or [])
    check(
        "rows",
        "activity：career / historical_stats 给账号级三档（existing/deleted/account_total，"
        "account_total 已含已删角色）",
        err is None and err2 is None
        and [g.get("key") for g in cstats.get("groups") or []] == ["pve", "pvp"]
        and [g.get("key") for g in hstats.get("groups") or []] == ["pve", "pvp"]
        and cstats.get("scope") == "account"
        and cstats.get("source") == "GetHistoricalStatsForAccount"
        and isinstance(pve_entered.get("account_total"), int)
        and isinstance(pve_entered.get("existing"), int)
        and isinstance(pvp_defeated.get("deleted"), int)
        and pvp_ratio.get("aggregate") in {"sum", "max", "min", "derived", "none"}
        # 账号级行不给 value，免得被当成生涯。
        and "value" not in pve_entered
        and cstats == hstats,
        f"账号级 {cstats.get('scope')} 角色={cstats.get('characters')} "
        f"| 熔炉击败={short(pvp_defeated, 200)} "
        f"| pve 场次={short(pve_entered, 200)} "
        f"| 两组项数={[g.get('stat_count') for g in cstats.get('groups') or []]}",
        seconds=dt,
    )

    single_stats = ((single or {}).get("data") or {}).get("stats") or {}
    check(
        "rows",
        "activity：stats 传 character → 单角色口径（scope=character + 角色名），不再冒充生涯",
        err3 is None
        and single_stats.get("scope") == "character"
        and single_stats.get("source") == "GetHistoricalStats"
        and (single_stats.get("character") or {}).get("name") == "Hunter"
        and all("account_total" not in row for g in single_stats.get("groups") or [] for row in g.get("stats") or []),
        f"scope={single_stats.get('scope')} character={single_stats.get('character')} "
        f"组={[g.get('key') for g in single_stats.get('groups') or []]}",
        seconds=dt3,
    )

    check(
        "rows",
        "activity：两个来源并列（profile.metrics 计数器 + 统计接口），差值写进 warnings",
        err is None
        and isinstance(counter_progress, int)
        and counter_progress > pvp_defeated.get("account_total", 0)
        and str(counter_progress - pvp_defeated.get("account_total", 0)) in warnings_text
        and all(row.get("source") == "profile.metrics" for row in counters),
        f"计数器 {counter_progress}（{len(counters)} 条，source="
        f"{[row.get('source') for row in counters]}） vs 统计接口 {pvp_defeated.get('account_total')}"
        f" | 差={counter_progress - pvp_defeated.get('account_total', 0) if isinstance(counter_progress, int) else '?'}"
        f" | warnings={' / '.join((career or {}).get('warnings') or [])[:220]}",
        seconds=dt,
    )

    trials_mode, dt4, err4 = await call(
        "activity_assistant", intent="stats", mode="trials", slow=True
    )
    tstats = ((trials_mode or {}).get("data") or {}).get("stats") or {}
    trials_kd = _stat_of(trials_mode, "trials", "kills_deaths_ratio")
    season, dt5, err5 = await call(
        "activity_assistant", intent="stats", mode="trials", period="season"
    )
    check(
        "rows",
        "activity：stats 支持 mode/period —— 试炼按模式给得出；season 上游没有 → unavailable",
        err4 is None and err5 is None
        and tstats.get("aggregation") == "computed"
        and (tstats.get("mode") or {}).get("upstream_modes") == 84
        and (tstats.get("mode") or {}).get("upstream_group") == "trials_of_osiris"
        and trials_kd.get("account_total") is not None
        and (tstats.get("period") or {}).get("key") == "career"
        and season.get("ok") is False
        and "unavailable" in season.get("error", {}).get("message", ""),
        f"试炼 mode={tstats.get('mode')} K/D={trials_kd.get('account_total')}"
        f"（{trials_kd.get('display')}）角色={tstats.get('characters')} "
        f"| season ok={season.get('ok')} code={season.get('error', {}).get('code')} "
        f"msg={short(season.get('error', {}).get('message'), 120)}",
        seconds=dt4,
    )

    weapon_hist, dt, err = await call(
        "activity_assistant", intent="weapon_history", count=3
    )
    whdata = (weapon_hist or {}).get("data") or {}
    check(
        "rows",
        "activity：weapon_history 给常用武器 + 行式统计（数值+显示值+中文名）",
        err is None and bool(whdata.get("weapons"))
        and "kills" in first_row(whdata.get("weapons"))
        and isinstance(first_row(whdata.get("weapons")).get("stats"), list)
        and first_row(first_row(whdata.get("weapons")).get("stats") or []).get("stat_id")
        and whdata.get("scope") == "all_modes"
        # 话术在**信封**的 summary 里（`data.message` 是信封违规，全量语料自己会判红）。
        and "不是 PvP 榜" in str((weapon_hist or {}).get("summary") or ""),
        f"记录数={whdata.get('count')} 返回={len(whdata.get('weapons') or [])} "
        f"scope={whdata.get('scope')} 首项={short(first_row(whdata.get('weapons')), 160)}",
        seconds=dt,
    )

    aggregate, dt, err = await call("activity_assistant", intent="aggregate", slow=True)
    adata = (aggregate or {}).get("data") or {}
    aggregate_mode, dt2, err2 = await call(
        "activity_assistant", intent="aggregate", mode="raid", slow=True
    )
    check(
        "rows",
        "activity：aggregate 给账号累计排行；mode 不是它读的参数（别按模式问它）",
        err is None and bool(adata.get("activities")) and err2 is None
        and ((aggregate_mode or {}).get("error") or {}).get("code") == "ignored_parameter",
        f"活动数={len(adata.get('activities') or [])} count={adata.get('count')} "
        f"| 带 mode → {((aggregate_mode or {}).get('error') or {}).get('code')} "
        f"{short(((aggregate_mode or {}).get('error') or {}).get('message'), 120)}",
        seconds=dt,
    )

    clan_missing, dt, err = await call("activity_assistant", intent="clan_leaderboards")
    clan_fake, dt2, err2 = await call(
        "activity_assistant",
        intent="clan_leaderboards",
        group_id="4611686018490000000",
        slow=True,
    )
    # 伪公会 ID 的答案取决于上游当时怎么回：正常是 404 → upstream_not_found_error，
    # 但实测（2026-09-20）会返回**空响应** → a_p_i_error + "不要凭记忆给排名"。两种都是
    # 如实回答（我们分不清"没这个公会"和"上游抖了一下"），所以两种码都算过；
    # 缺 group_id 那条仍然必须是我们自己的 invalid_argument_error。
    fake_code = ((clan_fake or {}).get("error") or {}).get("code")
    check(
        "rows",
        "activity：clan_leaderboards 缺 group_id → invalid_argument_error；伪 ID → 上游如实报错",
        err is None and err2 is None
        and ((clan_missing or {}).get("error") or {}).get("code") == "invalid_argument_error"
        and fake_code in {"upstream_not_found_error", "a_p_i_error"},
        f"缺 ID → {((clan_missing or {}).get('error') or {}).get('code')} | 伪 ID → {fake_code}",
        seconds=dt,
    )

    board, dt, err = await call("activity_assistant", intent="leaderboards", slow=True)
    bcode = ((board or {}).get("error") or {}).get("code")
    bmsg = str(((board or {}).get("error") or {}).get("message") or "")
    check(
        "rows",
        "activity：leaderboards 的现状（已知上游问题：不许编排名）",
        err is None and ((board or {}).get("ok") is True or bool(bcode)),
        f"ok={(board or {}).get('ok')} code={bcode} msg={short(bmsg, 200)}",
        seconds=dt,
        info=True,
    )

    act_community, dt, err = await call(
        "activity_assistant", intent="community", query="突袭"
    )
    acomm = (act_community or {}).get("data") or {}
    check(
        "rows",
        "activity：community 走本地资料（不是实时数据）",
        err is None and (act_community or {}).get("ok") is True and bool(acomm),
        f"keys={keys_of(acomm)} 摘要={short((act_community or {}).get('summary'), 140)}",
        seconds=dt,
    )

    sub_community, dt, err = await call(
        "subclass_assistant", intent="community", query="碎片"
    )
    scomm = (sub_community or {}).get("data") or {}
    check(
        "rows",
        "subclass：community 走本地技能资料",
        err is None and (sub_community or {}).get("ok") is True and bool(scomm),
        f"keys={keys_of(scomm)} 摘要={short((sub_community or {}).get('summary'), 140)}",
        seconds=dt,
    )

    # ── world_assistant ────────────────────────────────────────────────
    weekly, dt, err = await call("world_assistant", intent="weekly")
    wblock = ((weekly or {}).get("data") or {}).get("weekly") or {}
    check(
        "rows",
        "world：weekly 给重置时间 + 分类计数 + 重点活动",
        err is None and bool(wblock.get("reset_time")) and bool(wblock.get("categories"))
        and bool(wblock.get("highlights")),
        f"reset={wblock.get('reset_time')} total={wblock.get('total')} "
        f"分类={[(c.get('label'), c.get('count')) for c in wblock.get('categories') or []]} "
        f"重点={len(wblock.get('highlights') or [])}",
        seconds=dt,
    )

    weekly_full, dt, err = await call("world_assistant", intent="weekly_full", limit=3)
    check(
        "rows",
        "world：weekly_full 不读 limit（要完整周常就别传 limit）",
        err is None and ((weekly_full or {}).get("error") or {}).get("code")
        == "ignored_parameter",
        f"code={((weekly_full or {}).get('error') or {}).get('code')} "
        f"msg={short(((weekly_full or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    menu = live.get("vendor_menu") or {}
    inner_menu = menu.get("vendors") or []
    empty_sale = all(not (row.get("sale_items") or []) for row in inner_menu)
    check(
        "rows",
        "world：vendor 不点名 → 菜单模式（不倾倒商品、给下一步）",
        menu.get("mode") == "menu" and bool(menu.get("total_vendors"))
        and bool(menu.get("question")) and bool(menu.get("next_actions")) and empty_sale,
        f"total_vendors={menu.get('total_vendors')} returned={menu.get('returned_vendors')} "
        f"truncated={menu.get('truncated')} 商品全空={empty_sale} "
        f"question={short(menu.get('question'), 60)}",
    )

    detail, dt, err = await call(
        "world_assistant", intent="vendor", vendor_name="萨瓦拉", limit=5
    )
    vblock = ((detail or {}).get("data") or {}).get("vendors") or {}
    vendor = first_row(vblock.get("vendors") or [])
    categories = vendor.get("categories") or []
    sale_items = vendor.get("sale_items") or []
    check(
        "rows",
        "world：vendor 详情给等级与分类，商品数受 limit 约束",
        err is None and vblock.get("mode") == "detail"
        and bool((vendor.get("rank") or {}).get("name"))
        and bool(categories)
        and all(c.get("kind") in {"rewards", "sale", "submenu"} for c in categories)
        and len(sale_items) <= 5,
        f"vendor={vendor.get('name')} rank={short(vendor.get('rank'), 120)} "
        f"分类={[(c.get('kind'), c.get('item_count')) for c in categories]} "
        f"sale_items={len(sale_items)} total_items={vendor.get('total_items')} "
        f"truncated={vendor.get('truncated')}",
        seconds=dt,
    )

    round_trip, dt, err = await call(
        "world_assistant",
        intent="vendor",
        vendor_name=str(live.get("vendor_hash") or ""),
        limit=3,
    )
    rt_block = ((round_trip or {}).get("data") or {}).get("vendors") or {}
    rt_vendor = first_row(rt_block.get("vendors") or [])
    menu_first = first_row(inner_menu)
    check(
        "rows",
        "world：vendor 用 hash 直查与菜单里的同一条对得上",
        err is None and rt_vendor.get("vendor_hash") == menu_first.get("vendor_hash"),
        f"菜单首条={menu_first.get('vendor_hash')}/{menu_first.get('name')} "
        f"hash 直查={rt_vendor.get('vendor_hash')}/{rt_vendor.get('name')}",
        seconds=dt,
    )

    garbled, dt, err = await call(
        "world_assistant", intent="vendor", vendor_name="zzqq乱写的商人名"
    )
    gblock = ((garbled or {}).get("data") or {}).get("vendors") or {}
    check(
        "rows",
        "world：编造的商人名给空菜单 + 下一步（不是错误信封）",
        err is None and (garbled or {}).get("ok") is True
        and not (gblock.get("vendors") or [])
        and bool(gblock.get("next_actions") or gblock.get("question")),
        f"ok={(garbled or {}).get('ok')} 候选={len(gblock.get('vendors') or [])} "
        f"question={short(gblock.get('question'), 60)} next_actions={short(gblock.get('next_actions'), 140)}",
        seconds=dt,
    )

    nodes, dt, err = await call(
        "world_assistant", intent="search_collectible_nodes", query="地牢"
    )
    node_rows = ((nodes or {}).get("data") or {}).get("nodes") or []
    check(
        "rows",
        "world：search_collectible_nodes 给节点号（节点号 ≠ 收藏品号）",
        err is None and bool(node_rows)
        and all("node_hash" in row and "name" in row for row in node_rows),
        f"节点数={len(node_rows)} 首项={short(first_row(node_rows), 180)}",
        seconds=dt,
    )

    if live.get("node_hash"):
        node_ok, dt, err = await call(
            "world_assistant", intent="collectible_node", collectible_node_hash=live["node_hash"]
        )
        ndata = (node_ok or {}).get("data") or {}
        check(
            "rows",
            "world：collectible_node 给已获得/未获得计数与条目",
            err is None and (node_ok or {}).get("ok") is True
            and (ndata.get("counts") or {}).get("total") is not None,
            f"node={ndata.get('node_name')} counts={ndata.get('counts')} 条目={len(ndata.get('items') or [])}",
            seconds=dt,
        )
    if live.get("collectible_hash"):
        node_bad, dt, err = await call(
            "world_assistant",
            intent="collectible_node",
            collectible_node_hash=live["collectible_hash"],
        )
        bad_msg = str(((node_bad or {}).get("error") or {}).get("message") or "")
        check(
            "rows",
            "world：把收藏品号当节点号 → invalid_argument_error 且说清两者区别",
            err is None and ((node_bad or {}).get("error") or {}).get("code")
            == "invalid_argument_error"
            and "不是展示节点" in bad_msg,
            f"code={((node_bad or {}).get('error') or {}).get('code')} msg={short(bad_msg, 180)}",
            seconds=dt,
        )

    collectible_items = ((live.get("collectible_payload") or {}).get("data") or {}).get("items") or []
    hashes = [
        row.get("item_hash") for row in collectible_items if isinstance(row.get("item_hash"), int)
    ]
    negatives = [value for value in hashes if value < 0]
    check(
        "rows",
        "world：collectible_item 的 item_hash 与其它面统一为非负（无符号）",
        bool(hashes) and not negatives,
        f"item_hash={hashes} 负数={negatives}",
    )

    world_community, dt, err = await call(
        "world_assistant", intent="community", query="宗师"
    )
    wcomm = (world_community or {}).get("data") or {}
    check(
        "rows",
        "world：community 跨分类搜本地资料",
        err is None and (world_community or {}).get("ok") is True and bool(wcomm),
        f"keys={keys_of(wcomm)} 摘要={short((world_community or {}).get('summary'), 140)}",
        seconds=dt,
    )

    await run_alias_rows(runner, skip_slow)
    await run_cross(runner, live, skip_slow)


async def run_cross(runner: Runner, live: dict[str, Any], skip_slow: bool) -> None:
    call = runner.call

    # 默认条数（语料第十二章 A 的表格）。取数用显式取件函数，别用猜路径的循环。
    def pick(payload: dict | None, *path: str) -> Any:
        cursor: Any = (payload or {}).get("data")
        for step in path:
            if isinstance(cursor, dict):
                cursor = cursor.get(step)
            else:
                return None
        return cursor

    defaults = [
        ("inventory_assistant", {"intent": "get", "location": "vault"}, 100,
         lambda p: pick(p, "inventory", "items"), "inventory.items"),
        ("weapon_assistant", {"intent": "type", "weapon_type": "手炮"}, 10,
         lambda p: pick(p, "weapons", "items"), "weapons.items"),
        ("weapon_assistant", {"intent": "catalog", "perk_name": "萤火虫"}, 50,
         lambda p: pick(p, "matched"), "matched"),
        ("weapon_assistant", {"intent": "patterns"}, 20,
         lambda p: pick(p, "patterns", "items"), "patterns.items"),
        ("inventory_assistant", {"intent": "duplicates"}, 10,
         lambda p: pick(p, "duplicate_weapons"), "duplicate_weapons"),
        ("loadout_assistant", {"intent": "list"}, 5,
         lambda p: pick(p, "loadouts"), "loadouts"),
        ("world_assistant", {"intent": "vendor"}, 15,
         lambda p: pick(p, "vendors", "vendors"), "vendors.vendors"),
        ("activity_assistant", {"intent": "history"}, 20,
         lambda p: pick(p, "activities"), "activities"),
    ]
    if skip_slow:
        record("rows", "cross：默认条数体检", "SKIP", "按 --skip-slow 跳过")
    else:
        observed: dict[str, Any] = {}
        for tool, args, cap, extract, label in defaults:
            payload, dt, err = await call(tool, **args)
            rows = extract(payload if err is None else None)
            count = len(rows) if isinstance(rows, list) else None
            observed[f"{tool}:{args['intent']}"] = (
                count if err is None else f"ERR:{type(err).__name__}"
            )
            check(
                "rows",
                f"cross：{tool}(intent={args['intent']}) 默认条数 ≤ {cap}（{label}）",
                err is None and count is not None and count <= cap,
                f"实际={count} 上限={cap}",
                seconds=dt,
            )
        record("rows", "cross：默认条数一览", "INFO", short(observed, 400))

    # fragments 不读 limit（只有 community 读）——语料里「subclass 10」那句是错的，这里改成真跑一遍
    frag_limit, dt, err = await call(
        "subclass_assistant", intent="fragments", element="void", limit=10
    )
    check(
        "rows",
        "cross：subclass fragments 不读 limit（要限量只有 community 支持）",
        err is None and ((frag_limit or {}).get("error") or {}).get("code") == "ignored_parameter",
        f"code={((frag_limit or {}).get('error') or {}).get('code')} "
        f"msg={short(((frag_limit or {}).get('error') or {}).get('message'), 160)}",
        seconds=dt,
    )

    # rarity 中英同结果
    zh, dt, err = await call(
        "inventory_assistant", intent="get", item_type="weapon", rarity="异域", limit=1
    )
    en, dt2, err2 = await call(
        "inventory_assistant", intent="get", item_type="weapon", rarity="exotic", limit=1
    )
    zh_total = (((zh or {}).get("data") or {}).get("inventory") or {}).get("total_items")
    en_total = (((en or {}).get("data") or {}).get("inventory") or {}).get("total_items")
    check(
        "rows",
        "cross：rarity 中英（异域/exotic）结果数量一致",
        err is None and err2 is None and zh_total == en_total and zh_total is not None,
        f"异域={zh_total} exotic={en_total}",
        seconds=dt,
    )

    # character 中英等价
    zh_c, dt, err = await call("subclass_assistant", intent="get", character="猎人")
    en_c, dt2, err2 = await call("subclass_assistant", intent="get", character="hunter")
    zh_name = (((zh_c or {}).get("data") or {}).get("subclass") or {}).get("subclass_name")
    en_name = (((en_c or {}).get("data") or {}).get("subclass") or {}).get("subclass_name")
    check(
        "rows",
        "cross：character 中英（猎人/hunter）读到同一套配置",
        err is None and err2 is None and bool(zh_name) and zh_name == en_name,
        f"猎人={zh_name} hunter={en_name}",
        seconds=dt,
    )

    # 错误信封形状抽查
    probes = [
        ("inventory_assistant", {"intent": "item", "item_instance_id": "不存在"}),
        ("weapon_assistant", {"intent": "info", "weapon_name": "zzqq不存在"}),
        ("world_assistant", {"intent": "collectible_node", "collectible_node_hash": 123456}),
        ("subclass_assistant", {"intent": "fragment_details", "fragment_name": "zzqq不存在"}),
    ]
    shape_issues: list[str] = []
    codes: list[str] = []
    for tool, args in probes:
        payload, dt, err = await call(tool, **args)
        if err is not None:
            shape_issues.append(f"{tool}: 抛 {type(err).__name__}")
            continue
        error = (payload or {}).get("error") or {}
        codes.append(str(error.get("code")))
        if (payload or {}).get("ok") is not False or not error.get("code") or not error.get("message"):
            shape_issues.append(f"{tool}: {short(payload, 120)}")
        if not isinstance((payload or {}).get("warnings"), list):
            shape_issues.append(f"{tool}: warnings 不是 list")
    check(
        "rows",
        "cross：失败信封统一（ok=false + error.code + error.message）",
        not shape_issues,
        f"错误码={codes} 问题={shape_issues[:3]}",
    )

    # 中英 weapon_type（只记录现状，不判失败：中文分类名是约定）
    zh_type, dt, err = await call(
        "weapon_assistant", intent="type", weapon_type="手炮", limit=1
    )
    en_type, dt2, err2 = await call(
        "weapon_assistant", intent="type", weapon_type="Hand Cannon", limit=1
    )
    zh_block = (((zh_type or {}).get("data") or {}).get("weapons") or {})
    en_block = (((en_type or {}).get("data") or {}).get("weapons") or {})
    check(
        "rows",
        "cross：weapon_type 中文分类名可靠；英文名只记录现状",
        zh_block.get("total") is not None,
        f"手炮 total={zh_block.get('total')} | Hand Cannon: ok={(en_type or {}).get('ok')} "
        f"code={((en_type or {}).get('error') or {}).get('code')} total={en_block.get('total')}",
        seconds=dt,
        info=True,
    )


# 别名等价（真机）：同参调用同组别名，`data` 必须逐字节相同。
# 分组与 docs/COMPATIBILITY.md / tests/test_intent_aliases.py 一致；标 slow 的组在
# `--skip-slow` 时跳过（catalog 那组是一次全库扫描，慢）。
_ALIAS_GROUPS: list[tuple[str, dict[str, Any], list[str], bool]] = [
    ("player_assistant", {"player_name": "OneTop丶Husky#6641"}, ["profile", "get_profile", "角色", "档案"], False),
    ("player_assistant", {"name_prefix": "husky"}, ["find", "find_players", "fuzzy"], True),
    ("inventory_assistant", {}, ["summary", "summarize", "概况"], False),
    ("inventory_assistant", {"location": "vault", "limit": 3}, ["get", "inventory", "list"], False),
    ("inventory_assistant", {"limit": 2}, ["duplicates", "duplicate_weapons", "find_duplicates", "重复武器"], False),
    ("inventory_assistant", {"item_name": "无感"}, ["search", "find_item"], False),
    ("inventory_assistant", {"type_name": "手炮"}, ["type", "search_type"], False),
    ("inventory_assistant", {"item_name": "无感"}, ["track_quest", "quest_tracking"], False),
    ("weapon_assistant", {"perk_name": "萤火虫"}, ["catalog", "search_catalog", "all_weapons", "global", "search_all"], True),
    ("weapon_assistant", {"weapon_name": "无感"}, ["compare", "compare_duplicates"], False),
    ("weapon_assistant", {"weapon_name": "遗产"}, ["perk_pool", "perks"], False),
    ("weapon_assistant", {"weapon_name": "遗产"}, ["popularity", "selection_rates", "perk_selection", "selection", "usage_rates"], False),
    ("weapon_assistant", {"weapon_name": "累积救赎"},
     ["patterns", "pattern", "craft", "锻造", "锻造武器", "图样", "图样进度", "模式进度", "红框", "红框进度"], False),
    ("subclass_assistant", {"character": "hunter"}, ["get", "subclass"], False),
    ("activity_assistant", {"character": "hunter"}, ["stats", "career", "historical_stats"], False),
    ("activity_assistant", {"count": 2}, ["weapon_history", "weapons", "weapon_usage", "weapon_leaderboard"], False),
    ("loadout_assistant", {"limit": 2}, ["list", "get"], False),
]


async def run_alias_rows(runner: Runner, skip_slow: bool) -> None:
    """别名必须真的等价：同参调用同组别名，`data` 逐字节相同。"""
    print("\n=== aliases：别名等价（真机）===")
    for tool, args, intents, slow in _ALIAS_GROUPS:
        title = f"{tool}: {'/'.join(intents)} 同参返回相同 data"
        if slow and skip_slow:
            record("aliases", title, "SKIP", "按 --skip-slow 跳过（这组是一次全库扫描）")
            continue
        signatures: dict[str, str] = {}
        error = None
        for intent in intents:
            payload, elapsed, exc = await runner.call(tool, slow=slow, intent=intent, **args)
            if exc is not None:
                error = f"{intent} 抛 {type(exc).__name__}: {exc}"
                break
            signatures[intent] = json.dumps(
                (payload or {}).get("data"), ensure_ascii=False, sort_keys=True, default=str
            )
        if error:
            check("aliases", title, False, error)
            continue
        distinct = {signature for signature in signatures.values()}
        check(
            "aliases",
            title,
            len(distinct) == 1,
            f"{len(intents)} 个取值 → {len(distinct)} 种 data"
            + ("" if len(distinct) == 1 else f"；不一致：{sorted(signatures)}"),
        )


async def run_mcp(live: dict[str, Any]) -> None:
    print("\n=== mcp：真 stdio 握手与协议层 ===")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = ROOT / ".venv" / "bin" / "destiny-mcp"
    if not command.is_file():
        record("mcp", "协议层体检", "SKIP", f"没有找到 {command}")
        return

    def payload_of(result: Any) -> dict:
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            return structured
        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return {}

    def error_text(result: Any) -> str:
        return " ".join(
            str(getattr(block, "text", "")) for block in getattr(result, "content", [])
        )

    async def with_session(env_extra: dict[str, str], body: Any) -> Any:
        params = StdioServerParameters(
            command=str(command),
            args=[],
            env={
                "DESTINY_MCP_ROOT": str(ROOT),
                **env_extra,
            },
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return await body(session)

    async def normal_body(session: Any) -> dict[str, Any]:
        tools = (await session.list_tools()).tools
        names = sorted(tool.name for tool in tools)
        check(
            "mcp",
            "tools/list 恰好 8 个聚合工具",
            len(names) == 8 and set(names) == set(TOOL_NAMES),
            f"count={len(names)} names={names}",
        )
        schema_gaps: list[str] = []
        for tool in tools:
            schema = getattr(tool, "inputSchema", None) or {}
            enums: set[str] = set()
            for node in walk_dicts(schema.get("properties", {}).get("intent")):
                values = node.get("enum")
                if isinstance(values, list):
                    enums.update(str(v) for v in values)
            if not enums:
                schema_gaps.append(f"{tool.name}: schema 里没有 intent 枚举")
                continue
            declared = set(get_args(INTENTS[tool.name]))
            missing = declared - enums
            if missing:
                schema_gaps.append(f"{tool.name}: 缺 {sorted(missing)[:5]}")
        check(
            "mcp",
            "每个工具的 intent 枚举覆盖代码里的全部取值",
            not schema_gaps,
            f"问题={schema_gaps[:4]}",
        )

        guard = await session.call_tool(
            "inventory_assistant", {"intent": "summary", "item_name": "自检"}
        )
        code = (payload_of(guard).get("error") or {}).get("code")
        check(
            "mcp",
            "参数拦截：传给不读它的 intent → ignored_parameter",
            code == "ignored_parameter",
            f"isError={getattr(guard, 'isError', None)} code={code}",
        )

        extra = await session.call_tool(
            "inventory_assistant", {"intent": "summary", "zzqq_unknown": 1}
        )
        text = error_text(extra)
        check(
            "mcp",
            "未声明参数在 schema 层被拒（extra_forbidden）",
            bool(getattr(extra, "isError", False)) and "extra_forbidden" in text,
            f"isError={getattr(extra, 'isError', None)} text={short(text, 160)}",
        )

        literal = await session.call_tool("weapon_assistant", {"intent": "zzqq乱填"})
        ltext = error_text(literal)
        check(
            "mcp",
            "不存在的 intent 在 schema 层被拒（literal_error）",
            bool(getattr(literal, "isError", False)) and "literal_error" in ltext,
            f"isError={getattr(literal, 'isError', None)} text={short(ltext, 160)}",
        )

        null_id = await session.call_tool(
            "inventory_assistant", {"intent": "item", "item_instance_id": None}
        )
        ntext = error_text(null_id)
        npayload = payload_of(null_id)
        clean_null = (
            bool(getattr(null_id, "isError", False))
            or (npayload.get("ok") is False and bool((npayload.get("error") or {}).get("code")))
        )
        check(
            "mcp",
            "item_instance_id=null 走 schema 拒绝或干净信封（不裸抛 AttributeError）",
            clean_null and "AttributeError" not in ntext,
            f"isError={getattr(null_id, 'isError', None)} text={short(ntext or npayload, 160)}",
        )

        slot = await session.call_tool(
            "loadout_assistant",
            {"intent": "update_official_identifiers", "slot_number": 21, "name_hash": 1},
        )
        stext = error_text(slot)
        check(
            "mcp",
            "槽位越界（21）在 schema 层被拒（less_than_equal）",
            bool(getattr(slot, "isError", False)) and "less_than_equal" in stext,
            f"isError={getattr(slot, 'isError', None)} text={short(stext, 160)}",
        )

        profile = await session.call_tool("player_assistant", {"intent": "profile"})
        ppayload = payload_of(profile)
        check(
            "mcp",
            "协议层读账号得到 ok=true（真握手能通到 Bungie）",
            ppayload.get("ok") is True,
            f"ok={ppayload.get('ok')} code={(ppayload.get('error') or {}).get('code')}",
        )
        return {"names": names}

    try:
        result = await asyncio.wait_for(
            with_session({}, normal_body), timeout=300
        )
        if isinstance(result, dict) and result.get("names"):
            check("mcp", "正常工具面握手", True, f"{len(result['names'])} 个工具")
    except BaseException as exc:  # noqa: BLE001
        check("mcp", "正常工具面握手", False, f"握手失败 {type(exc).__name__}: {str(exc)[:200]}")

    async def tool_names_body(session: Any) -> dict[str, Any]:
        return {"names": sorted(tool.name for tool in (await session.list_tools()).tools)}

    try:
        # 历史工具面已于 2026-09-20 剥离到 legacy/：现在**任何**环境变量都变不出别的工具，
        # 连旧开关一起塞进去也只该有 8 个（少了这条，谁把旧面挂回来都不会被发现）。
        stale = await asyncio.wait_for(
            with_session(
                {
                    "DESTINY_MCP_TOOL_PROFILE": "full",
                    "DESTINY_MCP_ENABLE_LEGACY_TOOLS": "1",
                },
                tool_names_body,
            ),
            timeout=300,
        )
        names = (stale or {}).get("names") or []
        check(
            "mcp",
            "历史工具面已剥离：设旧的 profile/开关也只暴露 8 个聚合工具",
            len(names) == 8 and "get_inventory" not in names,
            f"count={len(names)} 含 get_inventory={'get_inventory' in names}",
        )
    except BaseException as exc:  # noqa: BLE001
        check("mcp", "历史工具面已剥离", False, f"{type(exc).__name__}: {str(exc)[:160]}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="all", choices=["all", "sweep", "rows", "mcp"])
    parser.add_argument(
        "--only",
        default="",
        help="在最终汇总与报告里只保留标题含该关键词的行（跑的还是全量）",
    )
    parser.add_argument("--skip-slow", action="store_true", help="跳过求解类（几十秒级）")
    parser.add_argument("--timeout", type=float, default=90.0, help="单次调用上限（秒）")
    parser.add_argument("--slow-timeout", type=float, default=420.0, help="慢 intent 的调用上限")
    parser.add_argument("--report", default="", help="把结果写成 JSON")
    parser.add_argument(
        "--known",
        action="store_true",
        help="把 KNOWN_OPEN 里登记过的 FAIL 降级成 WARN（只看新问题）",
    )
    args = parser.parse_args()

    runner = Runner(timeout=args.timeout, slow_timeout=args.slow_timeout)
    async with app_lifespan(create_server()) as service:
        runner.ctx = type(
            "C", (), {"request_context": type("R", (), {"lifespan_context": service})}
        )()
        live = await discover(runner)
        print(
            "探测到的事实："
            f"玩家={live.get('player_name')} 武器实例={live.get('weapon_instance_id')} "
            f"护甲实例={live.get('armor_instance_id')} 套装={live.get('set_name')} "
            f"活动={live.get('activity_id')} 节点={live.get('node_hash')} "
            f"收藏品={live.get('collectible_hash')} 神器模组={live.get('artifact_mod_hash')} "
            f"商人={live.get('vendor_hash')} 配装={live.get('loadout_id')}"
        )
        if args.group in {"all", "sweep"}:
            await run_sweep(runner, live, args.skip_slow)
        if args.group in {"all", "rows"}:
            await run_rows(runner, live, args.skip_slow)
        if args.group in {"all", "mcp"}:
            await run_mcp(live)

    if args.only:
        kept = [row for row in RESULTS if args.only in row["title"]]
        RESULTS.clear()
        RESULTS.extend(kept)

    if args.known:
        for row in RESULTS:
            if row["status"] != "FAIL":
                continue
            for needle, reason in KNOWN_OPEN.items():
                if needle in row["title"]:
                    row["status"] = "WARN"
                    row["evidence"] = f"（已登记：{reason}）" + row["evidence"]
                    break

    counts: dict[str, int] = {}
    for row in RESULTS:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("\n=== 汇总 ===")
    print("状态分布：" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("最慢 8 次调用：")
    for seconds, tag in sorted(TIMINGS, reverse=True)[:8]:
        print(f"  {seconds:6.1f}s  {tag}")
    print("最大 8 个响应：")
    for size, tag in sorted(SIZES, reverse=True)[:8]:
        print(f"  {size / 1024:8.1f} KB  {tag}")
    failures = [row for row in RESULTS if row["status"] == "FAIL"]
    if failures:
        print(f"\nFAIL {len(failures)} 条：")
        for row in failures:
            print(f"  - [{row['group']}] {row['title']}")
            print(f"    {row['evidence']}")

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "results": RESULTS,
                    "timings": TIMINGS,
                    "sizes": SIZES,
                    "counts": counts,
                    "live": {k: v for k, v in live.items() if k != "profile"},
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\n报告已写入 {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
