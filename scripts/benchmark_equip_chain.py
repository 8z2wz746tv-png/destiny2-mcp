#!/usr/bin/env python
"""装备链条的分段计时：求解 → 逐件搬运 → 模组写入 → 回读核对 → 还原核对。

**纪律（照抄 `docs/EXPERIENCE.md` 那条，别省）**：改前跑一次 → 改 → **立刻再跑一次**，
两次必须在同一会话内。上游波动实测 **31%**（同一条调用 76.4s → 99.8s），
**跨晚对比会把 20~30 秒的优化整个吃掉**；没有前后同晚对比，就别把差数当结论。

口径（表里必须一起打出来，不然数字会被读歪）：

- **墙钟归属**：每一次被挂住的函数调用各记一次真实耗时（`time.perf_counter`），
  嵌套段会各自计时，所以"分段合计"**不能**与整条链路的总时长相加；
- **计数 × 速率**：HTTP 那两张表记的是 `aiobungie.RESTClient._request`（所有 HTTP 的唯一出口）
  每一趟的真实往返耗时 —— 也是墙钟，但**并发探测（`find`）里这些往返是重叠的**，
  所以 HTTP 合计 > 段合计是正常的，不是记了两遍；
- **没被挂住的时间**：Manifest 加载、求解器计算、客户端序列化都不在段里。
  想知道它们，只能拿 `find` 的总时长去减段合计（脚本会打这个差）。

安全网（`--write` 时）：写前 `loadout_assistant(intent="save")` 存时间戳快照 →
跑完自动 `intent="equip_loadout"` 还原 → 用 `inventory_assistant(intent="mods")`
**逐槽 diff** 核对。**为腾能量而做的搬运不会被自动还原**（快照只记"这一位角色身上"的件），
脚本会把差的那几件点名，并说清要手动搬回哪里。

用法：

    .venv/bin/python scripts/benchmark_equip_chain.py --help
    .venv/bin/python scripts/benchmark_equip_chain.py                 # 只读：find + 确认回显
    .venv/bin/python scripts/benchmark_equip_chain.py --write         # 真写一次并分段计时

`--offline`（隐藏开关，给冒烟测试与人工验收用）是**零网络、零凭据**的一趟：四个工具入口换成
`OfflineStub`、读账号现场换成空现场、服务容器也不建真实客户端（不读 `.env`/token，Manifest 也不下）。
干净克隆（没有 `.env`）里必须跑得起来 —— 那正是这条开关存在的意义：它验的是"接线"，不是账号。
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import inspect
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiobungie

from destiny_mcp.bungie_client import BungieClient
from destiny_mcp.manifest import class_type_name, resolve_character_name
from destiny_mcp.player_resolver import CURRENT_OAUTH_PLAYER, PlayerResolver
from destiny_mcp.server import app_lifespan, create_server
from destiny_mcp.tools._helpers import resolve_player_name
from destiny_mcp.services import (
    build_execution_guard,
    loadout_armor_state,
    loadout_verify,
    profile_components,
    write_readback,
)
from destiny_mcp.tools.assistants import (
    SELF_GUARDED_WRITE_INTENTS,
    build_assistant,
    inventory_assistant,
    loadout_assistant,
    subclass_assistant,
)

# 默认值 = 一套**真机上有解**的示例：术士 `OneTop丶Husky#6641`（audit 2026-10-03 143405 的形状）。
# 形状照抄 `/tmp/bench_equip_segmented.py`（那份已跑通并产出过分段表），别重新发明。
DEFAULT_PLAYER = "OneTop丶Husky#6641"
DEFAULT_EXPECTED_CHARACTER_ID = "2305843009754046315"
DEFAULT_FRAGMENTS = ["保护琢面", "使命琢面", "黎明琢面", "勇气琢面", "希望琢面"]
DEFAULT_FUNCTIONAL_MODS = [
    "helmet:重型弹药搜寻者",
    "helmet:特殊武器弹药搜寻者",
    "helmet:谐振虹吸",
    "gauntlets:回天掌法",
    "gauntlets:回天掌法",
    "gauntlets:专注打击",
    "chest:缚丝弹药生成",
    "chest:谐振弹药生成",
    "chest:震荡阻尼器",
    "legs:缚丝回收器",
    "legs:谐振回收器",
    "legs:武器洗礼",
    "class_item:特殊终结技",
    "class_item:职业洗礼",
    "class_item:强力吸引",
]

# ── 分段仪器 ────────────────────────────────────────────────────────────────
# 归属规则：每一次 profile 抓取 / 每一次 HTTP 往返，都记在"当时最内层的那个被挂住的段"名下。
# 用 ContextVar 而不是全局栈：`find` 里有并发探测，任务各自带一份上下文副本，
# 这样并发段的归属不会被别的任务串味。
_STACK: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "bench_seg_stack", default=()
)
SPANS: list[dict[str, Any]] = []
HTTP: list[dict[str, Any]] = []
FETCHES: list[dict[str, Any]] = []

# 不带 `--write` 时，这些接口**一个都不许被调**：它们是本项目全部会改账号状态的入口
# （`equip` / `equip_build` / `equip_mod` 是服务端自守门的三个，其余在这里拦）。
GUARDED_WRITES: tuple[tuple[str, str], ...] = (
    ("loadout_assistant", "save"),
    ("loadout_assistant", "equip_loadout"),
    ("build_assistant", "equip_build"),
    ("inventory_assistant", "equip"),
    ("inventory_assistant", "equip_many"),
    ("inventory_assistant", "equip_mod"),
    ("inventory_assistant", "move"),
    ("inventory_assistant", "transfer"),
    ("inventory_assistant", "lock"),
    ("inventory_assistant", "track_quest"),
    ("inventory_assistant", "pull_postmaster"),
    ("subclass_assistant", "modify"),
    ("subclass_assistant", "equip_artifact_mod"),
    ("subclass_assistant", "equip_artifact"),
)


class ReadOnlyViolation(RuntimeError):
    """不带 `--write` 却走到了写接口 —— 这是脚本自己的 bug，不是账号问题。"""


class LiveTools:
    """活体工具表：四个入口函数放在一个可变对象上，守卫换的就是这里的属性。

    为什么不用 `sys.modules[__name__]`：那样子模块里的四个名字只是"碰巧"存在，
    ruff 判它们未使用（F401），而"守卫到底替换了什么"也读不出来。
    """

    def __init__(self, **entries: Any) -> None:
        for name, function in entries.items():
            setattr(self, name, function)


class OfflineStub:
    """`--offline` 的替身：**一次网络都不打**，只回放够用的形状（给冒烟测试用）。

    存在的理由是验收那句"不带 `--write` 时一个写接口都不调"必须能被自动化断言 ——
    真机脚本的验收当然是看表，但"零写入"这条只能靠离线跑一遍来证明。

    **四个入口都要求 `ctx`**（照活体工具的真实签名收严）：漏传 `ctx` 正是真机
    2026-10-04 那三处崩的根因，而 `find`/`equip_build` 那条路带着 `ctx` 照跑 ——
    替身原来用 `**kwargs` 全吞，于是"写段一条断言都没有"。这里收严之后，任何一处
    漏传在离线就是 `TypeError`，不必再等一次真机（也就不用拿账号去换这条证据）。

    回执形状照**真机**给：`intent="save"` 的 `loadout_id` 在 `data.result` 里，
    不在 `data` 顶层 —— 替身原来把它放在顶层，正好把脚本读错路径这件事一起"测绿"了。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def build_assistant(self, *, ctx: Any, **kwargs: Any) -> dict:
        self.calls.append({"tool": "build_assistant", **kwargs})
        intent = str(kwargs.get("intent") or "find")
        if intent == "equip_build" and kwargs.get("confirmed"):
            return {"ok": True, "data": {"result": {"message": "假写入（offline）", "steps": []}}}
        if intent == "equip_build":
            return {
                "ok": True,
                "candidates": [{"execution_id": "offline-exec", "items_preview": []}],
            }
        return {
            "ok": True,
            "data": {
                "builds": [
                    {
                        "execution_id": "offline-exec",
                        "canonical_build": {"execution_id": "offline-exec"},
                    }
                ]
            },
        }

    async def loadout_assistant(self, *, ctx: Any, **kwargs: Any) -> dict:
        self.calls.append({"tool": "loadout_assistant", **kwargs})
        intent = str(kwargs.get("intent") or "list")
        if intent == "save":
            return {
                "ok": True,
                "data": {"result": {"success": True, "loadout_id": "offline-snapshot"}},
            }
        return {"ok": True, "data": {"result": {"message": "假还原（offline）", "steps": []}}}

    async def inventory_assistant(self, *, ctx: Any, **kwargs: Any) -> dict:
        self.calls.append({"tool": "inventory_assistant", **kwargs})
        return {"ok": True, "data": {"equipped_armor": {"characters": []}}}

    async def subclass_assistant(self, *, ctx: Any, **kwargs: Any) -> dict:
        self.calls.append({"tool": "subclass_assistant", **kwargs})
        return {"ok": True, "data": {}}


class _OfflineEquipment:
    """`--offline` 下 `equip`（= `build_svc._equipment`）的替身：**碰真账号就炸**。

    `run()` 只从服务容器拿这一个属性，而离线段连它都不该用（读现场已经换成
    `read_armor_state_offline`）。所以这里**一个属性都不提供**：哪天有人把真读数接回
    离线段，拿到的是本文件里这句写明原因的错误，而不是
    `AttributeError: 'NoneType' object has no attribute '_resolver'` 那种看不出所以然的崩。
    """

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"--offline 不该走真实账号路径（equip.{name}）—— 这一趟的语义是零网络、零凭据"
        )


class _OfflineBuildService:
    """`build_svc` 的离线替身：只给 `run()` 真正会读的那一个属性。"""

    _equipment = _OfflineEquipment()


@asynccontextmanager
async def offline_service_context() -> AsyncIterator[dict[str, Any]]:
    """`--offline` 的服务容器替身：**不建真实客户端、不读 OAuth、一次网络都不打**。

    为什么不能照用 `app_lifespan(create_server())`：那条路第一件事就是 `BungieClient().start()` ——
    `config.validate_credentials()` 要 `.env`、落盘 token 二选一，干净克隆（没有 `.env`）直接
    `AuthenticationError`；就算有凭据，紧接着 `manifest.ensure_loaded()` 在本地没有 Manifest 缓存时
    还要下几十 MB。于是"零网络、只走替身"的 `--offline` 反倒成了最需要凭据与网络的那种跑法。
    2026-10-04 实测：干净树里 `tests/test_benchmark_equip_chain_smoke.py` 有 3 条就死在这儿，
    子进程 stderr 是 `OAuth setup required`，最后两帧是 `raise SystemExit(main())` 与
    `AuthenticationError: No valid Destiny OAuth tokens found`。

    离线段真正从 `svc` 拿的只有 `build_svc._equipment` 一个属性（`_patch_instrumentation` 与
    `verify_character` 都只在活体那半跑），所以替身给到这一个是够的 —— 给的也就只有它。
    """
    yield {"build_svc": _OfflineBuildService()}


async def read_armor_state_offline(
    owner: Any, player: str, character: str, char_id: str, *, equipped_only: bool
) -> dict[str, Any]:
    """`--offline` 的"读账号现场"替身：回一份**空现场**，形状与 `read_armor_state` 一致。

    离线没有账号可读 —— 真读数要 `owner._resolver`（真凭据 + 真网络）。但存快照之后的现场、
    逐槽 diff、位置缺口那几段要的是同一个形状，所以给 `{"char_id": …, "pieces": {}}` 这份
    "一件都没有"的现场，让整条写段的接线照跑（替身不打网络，本来也就读不出件）。
    参数一个都不用：替身就是"这一趟没有账号"这件事本身。
    """
    return {"char_id": str(char_id), "pieces": {}}


def _here() -> str:
    stack = _STACK.get()
    return stack[-1] if stack else "(顶层)"


def _record(store: list[dict[str, Any]], label: str, seconds: float, **extra: Any) -> None:
    store.append({"label": label, "seconds": seconds, **extra})


def wrap(owner: Any, name: str, label: str) -> None:
    """把 `owner.name` 换成计时版；`owner` 可以是实例，也可以是**模块**。

    `loadout_verify` / `loadout_armor_state` / `build_execution_guard` 那一族今天从
    `loadout_equipment_service` 拆成了模块函数，所以 `owner` 得能收模块 ——
    只认实例的写法在 `equip._verify_loadout` 搬走之后就静默挂不上了
    （原版脚本 112 行里那句 `_wrap(build_svc._equipment, "_verify_loadout", …)`
    会直接 `AttributeError`，脚本连跑都跑不起来）。
    """
    if not hasattr(owner, name):
        # 挂不上就吵：静默跳过等于"表里少了那一段"，而少的那段往往正是要看的。
        raise AttributeError(
            f"{getattr(owner, '__name__', owner)} 上没有 {name!r} —— 挂点搬走了，先核现状"
        )
    original = getattr(owner, name)

    if inspect.iscoroutinefunction(original):

        async def timed(*args: Any, **kwargs: Any) -> Any:
            token = _STACK.set(_STACK.get() + (label,))
            started = time.perf_counter()
            ok = True
            try:
                return await original(*args, **kwargs)
            except BaseException:
                ok = False
                raise
            finally:
                _STACK.reset(token)
                _record(SPANS, label, time.perf_counter() - started, ok=ok)

    else:

        def timed(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
            token = _STACK.set(_STACK.get() + (label,))
            started = time.perf_counter()
            ok = True
            try:
                return original(*args, **kwargs)
            finally:
                _STACK.reset(token)
                _record(SPANS, label, time.perf_counter() - started, ok=ok)

    setattr(owner, name, timed)


def patch_http() -> None:
    """`RESTClient._request` 是 aiobungie 所有 HTTP 的唯一出口 → 请求次数的硬账。"""
    original = aiobungie.RESTClient._request

    async def counted(self: Any, method: Any, route: str, **kwargs: Any) -> Any:
        started = time.perf_counter()
        ok = True
        try:
            return await original(self, method, route, **kwargs)
        except BaseException:
            ok = False
            raise
        finally:
            _record(
                HTTP,
                _here(),
                time.perf_counter() - started,
                route=str(route),
                method=str(method),
                ok=ok,
            )

    aiobungie.RESTClient._request = counted  # type: ignore[method-assign]


def component_label(components: Any) -> str:
    """把组件号翻回 `profile_components` 里的名字，报表里才看得懂这一抓是干什么的。

    常量只在 `profile_components` 一处定义（单一出处），这里只做反查，不抄第二份。
    """
    try:
        wanted = sorted(int(c) for c in components)
    except (TypeError, ValueError):
        return str(components)
    for name in dir(profile_components):
        if name.startswith("_"):
            continue
        value = getattr(profile_components, name)
        if isinstance(value, list) and sorted(int(c) for c in value) == wanted:
            return f"{name}{wanted}"
    return f"[{','.join(str(c) for c in wanted)}]"


def patch_profile_fetch() -> None:
    original = BungieClient.get_profile

    async def counted(
        self: Any, membership_id: str, membership_type: int, components: Any
    ) -> Any:
        label = _here()
        started = time.perf_counter()
        ok = True
        try:
            return await original(self, membership_id, membership_type, components)
        except BaseException:
            ok = False
            raise
        finally:
            _record(
                FETCHES,
                label,
                time.perf_counter() - started,
                components=component_label(components),
                ok=ok,
            )

    BungieClient.get_profile = counted  # type: ignore[method-assign]


def patch_resolver() -> None:
    """解析器**无缓存**（每次调用都打上游）—— 给它们单独挂标签，才看得出重复。

    `resolve_player` 内部有**两条完全不同的路**，标签必须分开，否则报表在骗人
    （真机 2026-10-04 实测踩到）：`player_name == CURRENT_OAUTH_PLAYER` 时它走
    `get_current_destiny_membership`（`User/GetMembershipsForCurrentUser/`），
    否则走 `search_player`（`SearchDestinyPlayerByBungieName`）。第一次跑时两路共用一个标签，
    于是 15 次"当前登录者查询"被读成了"搜了 15 次人" —— 而真相是
    **`.env` 没配 `DESTINY_DEFAULT_PLAYER`**，工具层每次都拿哨兵回落到当前登录者，
    这条路上没有任何缓存，一次 profile 抓取要白搭好几次往返。
    """
    wrap(PlayerResolver, "get_profile", "      · resolver.get_profile")
    wrap(
        PlayerResolver, "resolve_player",
        "      · resolve_player(search_player 或 当前登录者查询)",
    )
    wrap(
        PlayerResolver, "resolve_current_player",
        "         · resolve_current_player(GetMembershipsForCurrentUser)",
    )
    wrap(
        PlayerResolver, "resolve_character_id",
        "      · resolve_character_id(profile[200])",
    )


def route_kind(route: str) -> str:
    low = route.lower()
    if "profile" in low:
        return "profile 抓取"
    if "searchdestinyplayer" in low:
        return "search_player 搜人"
    if "getmembershipsforcurrentuser" in low:
        return "GetMembershipsForCurrentUser 当前登录者"
    if "transferitem" in low:
        return "TransferItem 搬运"
    if "equipitems" in low:
        return "EquipItems 批量装备"
    if "equipitem" in low:
        return "EquipItem 单件装备"
    if "insertsocketplug" in low:
        return "InsertSocketPlugFree 写模组"
    if "setlockstate" in low or "settrackedstate" in low:
        return "SetLock/TrackState"
    if "equiploadout" in low:
        return "EquipLoadout"
    return route


def _fmt_rows(rows: list[dict[str, Any]], key: str) -> str:
    """按 label 聚合：次数 / 合计秒 / 单次均值 / 最长一次。"""
    groups: dict[str, list[float]] = OrderedDict()
    for row in rows:
        groups.setdefault(row[key], []).append(row["seconds"])
    out = []
    for label, secs in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        out.append(
            f"    {label:<44} n={len(secs):<3} 合计={sum(secs):7.1f}s "
            f"均值={sum(secs) / len(secs):5.2f}s 最长={max(secs):6.2f}s"
        )
    return "\n".join(out) or "    （空）"


# ── 五张表 ─────────────────────────────────────────────────────────────────


def table_segments() -> None:
    print("\n【表 1】分段（墙钟，按调用顺序；缩进 = 嵌套深度）")
    if not SPANS:
        print("    （空）")
        return
    for span in SPANS:
        depth = span["label"].count("└") + span["label"].count("·")
        prefix = "  " * min(depth, 4)
        print(
            f"    {prefix}{span['label'].strip():<48} {span['seconds']:7.2f}s"
            + ("" if span["ok"] else "  ← 抛错")
        )
    total = sum(s["seconds"] for s in SPANS)
    print(f"    —— 分段墙钟合计 {total:.1f}s（**嵌套，不能与链路总时长相加**）")


def table_span_aggregate() -> None:
    print("\n【表 2】分段聚合（同一段被调多次时：次数/合计/均值/最长）")
    print(_fmt_rows(SPANS, "label"))


def table_fetches() -> None:
    print("\n【表 3】逐次 profile 抓取（`BungieClient.get_profile`；归属=当时最内层的段）")
    if not FETCHES:
        print("    （空）")
        return
    for i, row in enumerate(FETCHES, 1):
        print(
            f"    #{i:<3} {row['seconds']:6.2f}s  {row['label']:<46} {row['components']}"
            + ("" if row["ok"] else "  ← 抛错")
        )
    print(f"    profile 抓取合计 {sum(f['seconds'] for f in FETCHES):.1f}s / {len(FETCHES)} 次")


def table_http() -> None:
    print("\n【表 4】逐次 HTTP 往返（`aiobungie.RESTClient._request`，所有 HTTP 的唯一出口）")
    if not HTTP:
        print("    （空）")
        return
    for i, row in enumerate(HTTP, 1):
        print(
            f"    #{i:<3} {row['seconds']:6.2f}s  {row['label']:<44} {route_kind(row['route'])}"
            + ("" if row["ok"] else "  ← 抛错")
        )
    print(
        f"    HTTP 合计 {sum(h['seconds'] for h in HTTP):.1f}s / {len(HTTP)} 次"
        "（find 里并发探测的往返会重叠 → 合计可能大于段合计）"
    )


def table_endpoints() -> None:
    print("\n【表 5】按上游端点聚合（计数 × 实测单次；并发重叠同上）")
    kinds: dict[str, list[float]] = OrderedDict()
    for row in HTTP:
        kinds.setdefault(route_kind(row["route"]), []).append(row["seconds"])
    if not kinds:
        print("    （空）")
        return
    for kind, secs in sorted(kinds.items(), key=lambda kv: -sum(kv[1])):
        print(
            f"    {kind:<34} n={len(secs):<3} 合计={sum(secs):6.1f}s "
            f"单次均值={sum(secs) / len(secs):4.2f}s 最长={max(secs):5.2f}s"
        )
    print("\n    按段归属的 HTTP 往返：")
    print(_fmt_rows(HTTP, "label"))


def print_method_note() -> None:
    print("\n【口径】以上五行 '合计' 都是**墙钟**，但覆盖面不同：")
    print("    · 表 1/2 = 被挂住的函数耗时（嵌套各自计时，合计不可与链路总时长相加）")
    print("    · 表 3/4/5 = 逐次真实往返（HTTP 那张是 aiobungie 的唯一出口，最接近上游）")
    print("    · 没被挂住的（Manifest 加载、求解器、序列化）只能拿链路总时长做减法")


def dump_json(path: Path, extra: dict[str, Any]) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "spans": SPANS,
        "http": HTTP,
        "fetches": FETCHES,
        "method": {
            "spans": "被挂住函数的墙钟归属（嵌套各自计时）",
            "http": "aiobungie.RESTClient._request 每次真实往返（并发重叠）",
            "fetches": "BungieClient.get_profile 每次抓取 + 组件清单",
            "window": (
                f"write_readback: ATTEMPTS={write_readback.ATTEMPTS} "
                f"DELAY_SECONDS={write_readback.DELAY_SECONDS} "
                f"window_seconds()={write_readback.window_seconds():g}"
            ),
        },
        **extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  明细已写 {path}")


# ── 安全网：快照 / 还原 / 逐槽 diff ────────────────────────────────────────


def _unwrap(response: Any) -> dict[str, Any]:
    return response if isinstance(response, dict) else {}


def _data(response: Any) -> dict[str, Any]:
    data = _unwrap(response).get("data")
    return data if isinstance(data, dict) else {}


def _steps(response: Any) -> list[dict[str, Any]]:
    """从各入口的回执里把 steps 抓出来（写入详情只在 steps 里，摘要会把它埋掉）。"""
    routes = [
        _data(response).get("result"),
        ((_unwrap(response).get("candidates") or [{}])[0] or {}).get("result")
        if _unwrap(response).get("candidates")
        else None,
        _data(response),
    ]
    for route in routes:
        if isinstance(route, dict) and isinstance(route.get("steps"), list):
            return [s for s in route["steps"] if isinstance(s, dict)]
    return []


def _render_steps(prefix: str, steps: list[dict[str, Any]]) -> None:
    if not steps:
        print(f"  {prefix}（回执里没有 steps）")
        return
    for step in steps:
        print(
            f"  {prefix}{str(step.get('action')):<14} ok={str(step.get('success')):<5} "
            f"{str(step.get('detail'))[:110]}"
        )


async def save_snapshot(
    tool: Any, *, ctx: Any, player: str, character: str
) -> dict[str, Any]:
    """写前快照：`loadout_assistant(intent="save")`，名字带时间戳。

    名字带时间戳是为了**手动兜底**：脚本崩了/还原失败时，人能在 GUI 里按名字找到它。

    `ctx` 必须一路传下去（本函数 + `restore_snapshot` + `socket_diff_rows` 三处）：活体工具
    函数第一件事是 `get_ctx(ctx)`，漏传就是 `'NoneType' object has no attribute
    'request_context'`。真机 2026-10-04 踩到的是**最难发现的那种分布** —— `find` 与
    `equip_build` 那两条路都显式带了 `ctx=ctx`，只有写段这三处漏，于是"跑得通的部分"把
    "崩掉的部分"盖住了：**崩的恰好是安全网本身**（存快照 / 还原 / 逐槽 diff）。

    `loadout_id` 只在 `data.result.loadout_id`（真机回执两次都一样）。读 `data.loadout_id`
    会**静默**拿到空串，脚本随即判"没拿到还原点"而拒绝写入 —— 拒写是对的，但理由是假的：
    快照明明存成了（真机 2026-10-04 就这么白跑一趟，还多留了一套孤儿快照）。
    """
    name = f"bench-equip-snapshot {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    response = _unwrap(
        await tool.loadout_assistant(
            ctx=ctx, intent="save", player_name=player, character=character, name=name,
            confirmed=True,
        )
    )
    snapshot = {
        "name": name,
        "loadout_id": str((_data(response).get("result") or {}).get("loadout_id") or ""),
        "ok": bool(response.get("ok")),
        "message": str(response.get("summary") or (_unwrap(response).get("error") or {}).get("message") or ""),
    }
    return snapshot


async def restore_snapshot(
    tool: Any, *, ctx: Any, player: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """自动还原：`loadout_assistant(intent="equip_loadout")`。

    **已知缺口**（不修，只如实报）：快照只覆盖"这一位角色身上的件"（见 `loadout_armor_state`），
    而为腾能量做的搬运（仓库 ↔ 角色）**不会被这一步搬回去** —— 那需要"搬回原处"的独立事实，
    得手动做。`armor_restore_skipped` 那一步也一样要在报告里点名。

    `ctx` 见 `save_snapshot`：这一处漏传的后果最重 —— 它挂在 `finally` 里，写入已经落地，
    还原却先抛 `AttributeError`，连"手动还原用哪套快照"那句提示都走不到。
    """
    response = _unwrap(
        await tool.loadout_assistant(
            ctx=ctx,
            intent="equip_loadout",
            player_name=player,
            loadout_id=snapshot.get("loadout_id") or "",
            confirmed=True,
        )
    )
    return {
        "ok": bool(response.get("ok")),
        "message": str(
            response.get("summary") or (_unwrap(response).get("error") or {}).get("message") or ""
        ),
        "steps": _steps(response),
    }


def manual_restore_hint(snapshot: dict[str, Any], *, player: str, reason: str = "") -> str:
    """"手动还原用哪套快照"这句话的**唯一出处**（打印它的分支不止一条）。

    为什么必须提出来：真机 2026-10-04 的链是「写入已落地 → 自动还原**自己抛** `AttributeError`
    → 脚本带 traceback 退出」，而当时这句提示写在 `if not restore["ok"]` 里面 —— 异常路径
    根本走不到，于是"账号被改过、还原没做成、该怎么办"一个字都没留下。**半修状态比全坏更危险**
    说的就是这件事：漏传 `ctx` 时脚本会在存快照那一步就良性失败、零写入，可一旦有人只补了一半
    （补了存快照/写入、没补还原），写入会真的落地，而安全网反倒成了抛异常的那一环。

    `reason` 非空时附一句"为什么走到手动这一步"（还原回执说失败 / 这一步自己抛）。
    """
    return (
        "  ⚠ 自动还原**没成功** —— 账号可能还停在写过的状态，请手动还原"
        + (f"（{reason}）" if reason else "")
        + "：\n"
        f"    loadout_assistant(intent=\"equip_loadout\", "
        f"player_name=\"{player}\", loadout_id=\"{snapshot.get('loadout_id') or ''}\", "
        "confirmed=true)\n"
        f"    快照名 {snapshot.get('name')!r}（留给下次/手动用）\n"
        "    注：快照只覆盖这一位角色身上的件；为腾能量搬走的件要按下面那节的清单自己搬回。"
    )


async def read_armor_state(
    owner: Any, player: str, character: str, char_id: str, *, equipped_only: bool
) -> dict[str, Any]:
    """一次 profile 读取 → `{实例号: {name, slot, location, mods:{槽:插件hash}}}`。

    走服务层的 `read_armor_mod_sockets`（`loadout_armor_state` 用的同一份），
    不在脚本里再写一套"插槽怎么读"。

    `equipped_only=True` 只留**目标角色身上**那五件 —— 逐槽 diff 要的是"这一趟该写的那五件"，
    把整账号 80+ 件护甲都拿去 diff 会把表撑成一屏噪音（第一次真机跑就是这么淹掉的）。
    位置缺口那一半留 `equipped_only=False`：为腾能量被搬走的件可能已经不在身上，
    只读身上就看不见它去了哪 —— 那恰恰是**已知缺口**要报的东西。
    """
    resolver = owner._resolver
    info = await resolver.resolve_player(player)
    mid, mtype = info["membership_id"], info["membership_type"]
    char_id = char_id or await resolver.resolve_character_id(mid, mtype, character)
    profile = await resolver.get_profile(mid, mtype, profile_components.INVENTORY_SOCKETS)
    sockets_data = (profile.get("itemComponents", {}).get("sockets", {}).get("data", {})) or {}
    equipped_ids = {
        str(raw.get("itemInstanceId", ""))
        for raw in (profile.get("characterEquipment", {}).get("data", {}).get(char_id, {}) or {}).get(
            "items", []
        )
    }
    live = loadout_armor_state._live_armor(profile, owner._manifest)
    out: dict[str, Any] = {"char_id": str(char_id), "pieces": {}}
    for item in live.values():
        if equipped_only and item.item_instance_id not in equipped_ids:
            continue
        if equipped_only and (item.location or "") != character:
            continue  # 别人身上穿着同一实例号是不可能的；这一条只是把"不在这一位"钉死
        out["pieces"][item.item_instance_id] = {
            "name": item.name,
            "slot": item.slot,
            "location": item.location,
            "item_hash": item.item_hash,
            "mods": dict(
                owner.read_armor_mod_sockets(item.item_instance_id, item.item_hash, sockets_data)
            ),
        }
    return out


async def socket_diff_rows(
    tool: Any, *, ctx: Any, player: str, character: str, before: list[Any]
) -> tuple[list[str], list[str]]:
    """逐槽 diff：`inventory_assistant(intent="mods")` 读回 vs 快照 → (表行, 缺口)。

    核对口径与判据都复用服务层：快照里 `mod_sockets` 是 `{槽号: 插件 hash}`（`LoadoutArmorState`），
    现场的插槽行来自 `armor_payload.socket_rows`（`plug_hash`/`name`）。

    **只比快照记过的那些槽**（`expected` 的键），不按并集比：`read_armor_mod_sockets` 收的是
    "可写模组槽"（通用/部位/调谐，见 `loadout_mod_sockets._MOD_CATEGORY_HASHES`），而 `intent="mods"`
    把着色器/大师杰作/原型/词条/皮肤/固有能力也一起列出来。按并集比 = 每一次都把那些槽报成
    "快照=空 现场=有"的假差异（真机 2026-10-04：五件全 ✗，而账号逐件等于快照）。
    **假红比不报更糟** —— 它会教读的人忽略这份核对；所以那部分只如实说"本次不判"，不进 `gaps`。

    `ctx` 见 `save_snapshot`。
    """
    response = _unwrap(
        await tool.inventory_assistant(
            ctx=ctx, intent="mods", player_name=player, character=character
        )
    )
    blocks = (_data(response).get("equipped_armor") or {}).get("characters") or []
    live_items: dict[str, dict[str, Any]] = {}
    for block in blocks:
        for row in block.get("items") or []:
            live_items[str(row.get("item_instance_id") or "")] = row

    rows: list[str] = []
    gaps: list[str] = []
    unjudged = 0
    for record in before:
        instance_id = str(getattr(record, "item_instance_id", "") or "")
        name = str(
            (live_items.get(instance_id) or {}).get("name")
            or getattr(record, "slot", "")
            or instance_id
        )
        row = live_items.get(instance_id)
        if row is None:
            gaps.append(f"'{name}'（{record.slot}，实例 …{instance_id[-7:]}）**这次没读回来**")
            rows.append(f"    {record.slot:<11} {name:<16} 不在回读里（快照有、现场没有）")
            continue
        by_index = {int(s.get("index", -1)): s for s in row.get("mods") or []}
        expected = dict(getattr(record, "mod_sockets", {}) or {})
        unjudged += len(set(by_index) - set(expected))
        diffs: list[str] = []
        for index in sorted(expected):
            want = expected.get(index)
            got_row = by_index.get(index) or {}
            got = got_row.get("plug_hash")
            if (want or 0) != (got or 0):
                diffs.append(
                    f"槽{index}: 快照={want or '空'} 现场={got or '空'}"
                    f"({got_row.get('name') or '空'})"
                )
        if diffs:
            gaps.append(f"'{name}'（{record.slot}，实例 …{instance_id[-7:]}）：" + "；".join(diffs))
            rows.append(f"    {record.slot:<11} {name:<16} ✗ " + "；".join(diffs))
        else:
            rows.append(
                f"    {record.slot:<11} {name:<16} ✓ 实例如快照、{len(expected)} 格逐槽一致"
            )
    if unjudged:
        rows.append(
            f"    （另有 {unjudged} 个槽快照不记、还原也不管（着色器/大师杰作/原型/词条/皮肤/"
            "固有能力）—— 本次**不判**，别把它们读成差异）"
        )
    return rows, gaps


async def location_gap_rows(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """**已知缺口**：为腾能量做的搬运不会被自动还原 —— 把差的那几件点名说清要搬回哪。"""
    rows: list[str] = []
    for instance_id, state in (before.get("pieces") or {}).items():
        now = (after.get("pieces") or {}).get(instance_id)
        if now is None:
            rows.append(
                f"    '{state.get('name')}'（{state.get('slot')}）**这次没读回来** —— "
                f"测试前在 {state.get('location')}，现在读不到，先自己确认它在哪"
            )
            continue
        if now.get("location") != state.get("location"):
            rows.append(
                f"    '{state.get('name')}'（{state.get('slot')}）："
                f"{state.get('location')} → {now.get('location')}"
                f"  ← 要手动搬回 {state.get('location')}（快照那一步只还原这一位角色身上的件）"
            )
    return rows


# ── 参数 / 主流程 ──────────────────────────────────────────────────────────


def parse_functional_mods(raw: list[str]) -> list[str]:
    """把 `--functional-mod` 的 `部位:名字` 归一：没写部位就补 `helmet:` 并说明。

    项目口径（`build_assistant` 的 `functional_mods`）：认得出部位时写成 `部位:名字`，
    认不出部位的**不该猜** —— 这里直接报错，别把玩家的一颗模组装到错的部位上。
    """
    out: list[str] = []
    for entry in raw:
        text = entry.strip()
        if not text:
            continue
        if ":" in text:
            out.append(text)
            continue
        raise SystemExit(
            f"功能模组 {entry!r} 没写部位。写成 `部位:名字`，例如 "
            "`helmet:谐振虹吸`；部位取值：helmet/gauntlets/chest/legs/class_item。"
        )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_equip_chain.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "装备链条的分段计时 + 五张表（分段/聚合/逐次抓取/逐次 HTTP/端点聚合）+ 落盘 JSON。\n"
            "不带 --write 时**零写入**：只跑 find 与确认回显（只读路径），且写接口被硬拦。\n"
            "带 --write 时：先存时间戳快照 → 真写 → 自动还原 → 逐槽 diff 核对。\n\n"
            "纪律：改前跑一次 → 改 → 立刻再跑一次（同一会话内）。上游波动实测 31%，\n"
            "跨晚对比会把 20~30 秒的优化整个吃掉；没有前后同晚对比就别把差数当结论。"
        ),
        epilog=(
            "例：\n"
            "  .venv/bin/python scripts/benchmark_equip_chain.py\n"
            "  .venv/bin/python scripts/benchmark_equip_chain.py --write --out /tmp/bench_equip.json\n"
        ),
    )
    parser.add_argument("--write", action="store_true", help="真写账号（默认只读，零写入）")
    parser.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--player", default=DEFAULT_PLAYER, help=f"BungieName（默认 {DEFAULT_PLAYER}）")
    parser.add_argument(
        "--character", default="warlock",
        help="目标角色：hunter/warlock/titan 或 猎人/术士/泰坦（默认 warlock）",
    )
    parser.add_argument(
        "--expected-character-id", default=DEFAULT_EXPECTED_CHARACTER_ID,
        help=(
            "目标角色的 native id；跑之前用它核对"
            "'这一位就是授权的那一位'"
            f"（默认 {DEFAULT_EXPECTED_CHARACTER_ID}，空串 = 不核对）"
        ),
    )
    parser.add_argument(
        "--exotic-name", default="黎明副歌",
        help="逐字传的金装名（硬约束，不许翻译或补全；默认 黎明副歌）",
    )
    # 三列分别是 CLI 名 / `dest`（= `build_assistant` 的参数名）/ 中文标签。
    # `--class-stat-target` 的默认 dest 会存成 `class_stat_target`，与 `class_target` 对不上，
    # 所以**显式写 dest**；这里直接用字面量 flag 而不是 f-string —— 实测 Python 3.13 下
    # `add_argument(f"--x-y", dest="z")` 的 `dest` 会被**静默丢掉**（存成 x_y），
    # 于是拼参数时才炸 `AttributeError`，排查要多花一轮。
    for flag, dest, default, label in (
        ("--weapons-target", "weapons_target", 130, "武器"),
        ("--health-target", "health_target", None, "生命"),
        ("--class-stat-target", "class_target", 70, "职业"),
        ("--grenade-target", "grenade_target", 50, "手雷"),
        ("--melee-target", "melee_target", 40, "近战"),
        ("--super-target", "super_target", 70, "超能"),
    ):
        parser.add_argument(
            flag, type=int, default=default, dest=dest,
            help=f"{label}属性最低目标（硬约束；传 0 = 不设该目标）",
        )
    parser.add_argument(
        "--fragment", dest="fragments", action="append", default=None,
        help=(
            "碎片名，可重复（`--fragment 保护琢面 --fragment 使命琢面`）。"
            "不传 = 用内置示例那 5 片；`--fragments ''` = 明确不要碎片"
        ),
    )
    parser.add_argument(
        "--functional-mod", dest="functional_mods", action="append", default=None,
        help=(
            "部位功能模组，写成 `部位:名字`，可重复（同名两颗写两遍）。"
            "不传 = 用内置示例那 15 颗；`--functional-mod ''` = 明确不要"
        ),
    )
    parser.add_argument("--top-n", type=int, default=5, help="find 返回几个候选（默认 5）")
    parser.add_argument(
        "--out", default=None,
        help="明细 JSON 落盘路径（默认 bench_equip_segments_<时间戳>.json，落在仓库根）",
    )
    return parser


def targets_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """命令行 → `build_assistant(intent="find")` 的参数字典。**一处拼装**，别散在各处。"""
    targets: dict[str, Any] = {
        "character": args.character,
        "exotic_name": args.exotic_name,
        "top_n": args.top_n,
    }
    for key, value in (
        ("weapons_target", args.weapons_target),
        ("health_target", args.health_target),
        ("class_target", args.class_target),
        ("grenade_target", args.grenade_target),
        ("melee_target", args.melee_target),
        ("super_target", args.super_target),
    ):
        # `None` 或 **≤0** 都算"没指定"（项目统一哨兵规则，见 `_helpers.positive_or_default`）：
        # 属性目标发 0 出去会被当成硬约束"某项至少 0"，看着无害，实则把"没要求"说成了要求。
        if value is not None and value > 0:
            targets[key] = value

    fragments = args.fragments
    if fragments is None:
        fragments = list(DEFAULT_FRAGMENTS)
    fragments = [f for f in fragments if f.strip()]
    if fragments:
        targets["include_subclass_fragment"] = True
        targets["fragment_names"] = fragments

    mods = args.functional_mods
    if mods is None:
        mods = list(DEFAULT_FUNCTIONAL_MODS)
    mods = parse_functional_mods(mods)
    if mods:
        targets["functional_mods"] = mods
    return targets


def _is_write_call(tool_name: str, intent: str, confirmed: bool) -> bool:
    """这一趟是不是**真要写账号** —— 判据不分叉，直接问工具层自己那份。"""
    if (tool_name, intent) not in set(GUARDED_WRITES):
        return False
    # `equip` / `equip_build` / `equip_mod` 是自守门的三个：`confirmed=False` 时它们只出
    # 计划/预览（**只读**），照着拦会把确认回显这条合法路径拦掉。其余写入没有这个口子。
    if intent in SELF_GUARDED_WRITE_INTENTS:
        return bool(confirmed)
    return True


def _install_write_guard(tool: Any) -> None:
    """不带 `--write` 时把**所有**接口换成会吵的替身：命中写调用就抛。

    为什么不是"看代码里没写"就算数：`equip_build` 的 `confirmed=True` 藏在分支深处，
    靠人眼审"这条路径不写"已经错过一次；拦在接口上，任何路径都绕不过。
    """
    for tool_name, intent in GUARDED_WRITES:
        function = getattr(tool, tool_name, None)
        if function is None or not inspect.iscoroutinefunction(function):
            continue

        def make(original: Any, name: str, blocked_intent: str) -> Any:
            async def guarded(*args: Any, **kwargs: Any) -> Any:
                # 位置参数也认：`confirmed` 实际是关键字传的，但别把这条规矩绑死在调用风格上。
                confirmed = bool(kwargs.get("confirmed"))
                if _is_write_call(name, str(kwargs.get("intent") or ""), confirmed):
                    raise ReadOnlyViolation(
                        f"没加 --write 却调了 {name}(intent={blocked_intent!r}, "
                        f"confirmed={confirmed}) —— 只读路径不该碰写接口，这是脚本的 bug。"
                    )
                return await original(*args, **kwargs)

            return guarded

        setattr(tool, tool_name, make(function, tool_name, intent))


def _patch_instrumentation(equip: Any, build_svc: Any) -> None:
    """挂点清单 —— 每一行都对应"回执里看不到、但确实花了时间"的一处。

    注意 `_wrap(owner, name, label)` 的 owner 有三种：服务实例（`equip`）、
    别的服务（`equip._transfer`）、**模块**（`loadout_verify` 那一族）。
    """
    wrap(build_svc._inventory, "get_armor_snapshot", "① 护甲快照(get_armor_snapshot)")
    wrap(equip, "equip_with_recovery", "② equip_with_recovery 总")
    wrap(equip, "_capture_recovery_state", "   回滚快照(_capture_recovery_state)")
    wrap(equip, "_equip_local_unlocked", "   搬运+模组(_equip_local_unlocked)")
    # 回读核对的入口早已不是 `_verify_loadout`：它搬成了模块函数（见文件头）。
    wrap(loadout_verify, "readback_verdict", "   回读核对(readback_verdict 总)")
    wrap(loadout_verify, "verify_loadout", "     └ 回读核对单次(verify_loadout)")
    # 细分：每一次网络往返都单独计时，才能说出时间花在哪
    wrap(equip, "_apply_subclass_config", "     └ 子职业(_apply_subclass_config)")
    wrap(equip, "_mod_preflight", "     └ Step0 模组预检(_mod_preflight)")
    wrap(equip, "_prepare_mod_operations", "        · 模组预检/规划 每件(_prepare_mod_operations)")
    wrap(equip, "_read_sockets", "           · _read_sockets(隐藏全量抓取)")
    wrap(equip, "_insert_armor_mod", "     └ 写一颗模组(_insert_armor_mod)")
    wrap(equip, "_mod_write_snapshot", "        · _mod_write_snapshot(拆现场)")
    wrap(equip, "transfer_loadout_items", "     └ 搬运(transfer_loadout_items)")
    wrap(equip, "_restore_exact_state", "   回滚执行(_restore_exact_state)")
    # 还原那一半：`equip_loadout` 之后补写的"账号护甲现场"（模块函数）
    wrap(loadout_armor_state, "restore_after_equip", "   附加还原(restore_after_equip)")
    # 写账号之前的那道只读闸（模块函数）
    wrap(build_execution_guard, "recheck_confirmed_build", "③ 确认后复检(recheck_confirmed_build)")
    # `_transfer` 挂在 equipment 服务上（不是 BuildService）
    wrap(equip._transfer, "transfer_item", "        └ 搬一件(transfer_item)")
    wrap(equip._transfer, "equip_items", "     └ 批量装备(equip_items)")
    wrap(equip._transfer, "_fetch_all_items", "           · _fetch_all_items(profile INVENTORY)")


async def verify_character(svc: Any, *, player: str, character: str, expected: str) -> str:
    """跑之前核对"这就是授权的那一位角色"：真机脚本动错角色是不可逆的。"""
    resolver = svc["build_svc"]._equipment._resolver
    info = await resolver.resolve_player(player)
    char_id = await resolver.resolve_character_id(
        info["membership_id"], info["membership_type"], character
    )
    print(f"玩家 {player} / 角色 {character} → native id {char_id}")
    if expected and str(char_id) != str(expected):
        raise SystemExit(
            f"角色 id 与 --expected-character-id 不一致（{char_id} != {expected}）—— "
            "停在这里，别在没授权的角色上写账号。"
        )
    return str(char_id)


def _resolved_player_is_bearer() -> str:
    """工具层在没传 `player_name` 时会解析成什么 —— 只用来判断"是不是走了当前登录者那条路"。"""
    return resolve_player_name(None) or ""


async def run(args: argparse.Namespace) -> int:
    # 有替身（`--offline`）就用替身，否则用**活体模块** —— 写守卫要能替换模块上的函数，
    # 所以给的是模块对象本身，而不是那四个函数的引用。
    tool: Any = OfflineStub() if args.offline else LiveTools(
        build_assistant=build_assistant,
        loadout_assistant=loadout_assistant,
        inventory_assistant=inventory_assistant,
        subclass_assistant=subclass_assistant,
    )
    if args.write:
        print("⚠ --write：这次会**真写账号**（只动授权的那一位角色）。")
    else:
        _install_write_guard(tool)
        print("（没加 --write：零写入，只跑 find + 确认回显；写接口已被硬拦）")

    # 角色名先归一成**英文**（`术士` → `warlock`）：`build_assistant` 两边都认，
    # 但下面要拿它跟 `InventoryItem.location` 比对，而那边只有英文（见 `item_parser`）。
    args.character = class_type_name(resolve_character_name(args.character)).lower()
    targets = targets_from_args(args)
    out_path = Path(args.out) if args.out else Path(
        f"bench_equip_segments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )

    if not args.offline:
        patch_http()
        patch_profile_fetch()
        patch_resolver()

    # `--offline` 的两处替换都在这儿，别塞回下面：服务容器（不建真实客户端 = 不要凭据、不下 Manifest）
    # 与"读账号现场"（真读数走 `owner._resolver` = 真凭据 + 真网络）。剩下那几处 `args.offline`
    # 分支只关掉计时挂点与角色核对，本来就碰不到网络。
    context = offline_service_context() if args.offline else app_lifespan(create_server())
    read_state = read_armor_state_offline if args.offline else read_armor_state

    async with context as svc:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": svc})()})()
        equip = svc["build_svc"]._equipment
        build_svc = svc["build_svc"]
        if not args.offline:
            _patch_instrumentation(equip, build_svc)

        char_id = ""
        if not args.offline:
            char_id = await verify_character(
                svc, player=args.player, character=args.character,
                expected=args.expected_character_id,
            )
        print(f"目标：{json.dumps(targets, ensure_ascii=False)}")
        # 哨兵提醒：工具层在"没给 player_name"时会回落到当前登录者，而**那条路没有缓存**，
        # 一次 profile 抓取要白搭好几次 `GetMembershipsForCurrentUser`（真机实测 15 次）。
        # 只报不改：往 `.env` 写 `DESTINY_DEFAULT_PLAYER` 是用户的事。
        if _resolved_player_is_bearer() == CURRENT_OAUTH_PLAYER:
            print(
                "⚠ 解析出来的是『当前登录者』哨兵（`.env` 里没有 DESTINY_DEFAULT_PLAYER）："
                "这条路上每次解析都要打一次 User/GetMembershipsForCurrentUser，且无缓存 —— "
                "表 4/5 里那一串同名往返就是它。要跑得干净，把玩家名写进 .env。"
            )

        # ── 读：find + 确认回显（这条路径本来就只读） ────────────────────
        # 先给"没有候选"那条路兜好底：`find` 被执行前提整批砍掉时（真机 2026-10-03 实测：
        # 金装撞 1641 + 三个格 10/10），`echo`/`rows` 根本没机会被赋值，
        # 而下面的表头要拼 `echo` —— 不初始化就是 `UnboundLocalError`，
        # 于是"没有候选"这条最该看清拒答原文的路反而崩在打印上。
        echo = 0.0
        rows: list[dict[str, Any]] = []
        started = time.perf_counter()
        found = _unwrap(await tool.build_assistant(ctx=ctx, intent="find", **targets))
        solve = time.perf_counter() - started
        builds = _data(found).get("builds") or []
        print(
            f"find: {solve:.1f}s builds={len(builds)} "
            f"code={(_unwrap(found).get('error') or {}).get('code')}"
        )
        no_candidate_reason = ""
        if not builds:
            # **原文一字不删**：`find` 的拒答把"为什么没有候选"写得很具体（哪件金装撞了 1641、
            # 哪个格满了 10/10、仓库里有几件搬不进来），那正是排查要看的东西 ——
            # 真机第一次跑就只出这一段，截断它等于把唯一的证据丢掉。
            print("\n没有候选，无法继续。find 拒答原文：")
            print(str(_unwrap(found).get("summary") or "（没有 summary）"))
            print(f"\nfind error：{json.dumps(_unwrap(found).get('error'), ensure_ascii=False)}")
            no_candidate_reason = str(_unwrap(found).get("summary") or "")
            execution_id = ""
        else:
            build = builds[0]
            execution_id = build.get("execution_id") or (
                build.get("canonical_build") or {}
            ).get("execution_id")
            started = time.perf_counter()
            preview = _unwrap(
                await tool.build_assistant(
                    ctx=ctx, intent="equip_build", execution_id=execution_id,
                    character=args.character, confirmed=False,
                )
            )
            echo = time.perf_counter() - started
            rows = ((preview.get("candidates") or [{}])[0]).get("items_preview") or []
            print(
                f"确认回显: {echo:.1f}s 预览={len(rows)} 件 "
                f"code={(_unwrap(preview).get('error') or {}).get('code')}"
            )
            for row in rows:
                mods = row.get("mods") or row.get("mod_labels") or []
                print(
                    f"   {str(row.get('slot')):<11} {str(row.get('name')):<12} "
                    f"inst=…{str(row.get('item_instance_id'))[-7:]} n_mods={len(mods)}"
                )

        snapshot: dict[str, Any] = {}
        restore: dict[str, Any] = {}
        armor_before: dict[str, Any] = {}
        equipped_before: dict[str, Any] = {}
        armor_after: dict[str, Any] = {}
        diff_rows: list[str] = []
        diff_gaps: list[str] = []
        loc_rows: list[str] = []
        write_seconds: float | None = None
        write_ok: bool | None = None
        write_steps: list[dict[str, Any]] = []
        write_detail = ""
        write_code: Any = None
        # 写入那一步自己抛出来的异常（正常路径是 `None`）：`finally` 里要先把"还原成没成"
        # 报清楚，再把它抛出去 —— 否则还原失败会把真写的失败盖掉，看到的故障原因就是错的。
        write_error: BaseException | None = None

        if not builds:
            print("\n没有候选 → **这一趟什么都没写**（账号一个字节都没动）。")
        elif not args.write:
            print("\n（没加 --write：到此为止，账号一个字节都没动）")
        else:
            # ── 安全网 1：写前存快照 ──────────────────────────────────
            print("\n【安全网】写前存快照…")
            snapshot = await save_snapshot(
                tool, ctx=ctx, player=args.player, character=args.character
            )
            print(f"  快照 {snapshot['name']!r} id={snapshot['loadout_id']} ok={snapshot['ok']}")
            if not snapshot["loadout_id"]:
                raise SystemExit(
                    "快照没拿到 loadout_id → **不写**（没有还原点就不动账号）。"
                    f"回执：{snapshot['message'] or '（空）'}"
                )
            # 写前拍**全账号**位置图（不只是身上那五件）：为腾能量被搬走的件会离开这一位，
            # 只读身上就看不见它原本在哪 —— 而"搬回原处"正是已知缺口要报的东西。
            armor_before = await read_state(
                equip, args.player, args.character, char_id, equipped_only=False
            )
            equipped_before = await read_state(
                equip, args.player, args.character, char_id, equipped_only=True
            )

            # ── 写：确认真写 ──────────────────────────────────────────
            try:
                started = time.perf_counter()
                done = _unwrap(
                    await tool.build_assistant(
                        ctx=ctx, intent="equip_build", execution_id=execution_id,
                        character=args.character, confirmed=True,
                    )
                )
                write_seconds = time.perf_counter() - started
                write_ok = bool(done.get("ok"))
                write_steps = _steps(done)
                result = _data(done).get("result") or {}
                write_code = (_unwrap(done).get("error") or {}).get("code")
                write_detail = str(
                    (done.get("error") or {}).get("message")
                    or (result.get("message") if isinstance(result, dict) else "")
                    or done.get("summary")
                    or ""
                )
            except BaseException as exc:
                # 写入这一步抛了（网络断、上游 5xx…）：还原照做，但异常本身先记下来 ——
                # 下面 `finally` 结束之后再抛，保住"故障原因就是写入失败"这条因果。
                write_error = exc
                raise
            finally:
                # ── 安全网 2：无论成败都自动还原 ──────────────────────
                print("\n【安全网】自动还原（loadout_assistant intent=equip_loadout）…")
                # 还原这一步自己也会抛（真机 2026-10-04 抛的就是这里的 `AttributeError`，
                # 根因是漏传 `ctx`；上游 5xx / 网络断掉是同一类）。**异常必须当成"还原失败"
                # 接住再往下走**，不能让它在 `finally` 里直接冲出 `run()`：
                # 1) 写入已经落地了，异常一冲出，连"手动还原用哪套快照"都不打印 ——
                #    那是用户明确点名的、最危险的一条路径：脚本看着崩了，账号还停在写过的状态；
                # 2) 真写回执 / 逐槽 diff / 位置缺口 / 落盘 JSON 也一起没了，
                #    而那几样恰是判断"账号现在到底什么样"的唯一证据。
                try:
                    restore = await restore_snapshot(
                        tool, ctx=ctx, player=args.player, snapshot=snapshot
                    )
                except BaseException as exc:  # noqa: BLE001 —— 接住是为了**报告**，不是吞掉
                    restore = {
                        "ok": False,
                        "message": f"还原这一步自己抛了：{type(exc).__name__}: {exc}",
                        "steps": [],
                        # 标记"是抛出、不是回执说失败"：下面据此决定要不要把异常再抛出去。
                        "raised": True,
                    }
                print(f"  还原 ok={restore['ok']} {restore['message'][:200]}")
                _render_steps("    step ", restore["steps"])
                # 判据用**快照 id 在不在**，不用 `restore["ok"]`：提示是给"账号可能没回去"用的，
                # 没有 id 就没什么可指。这样"回执说失败"与"这一步自己抛"两条路都覆盖到。
                if not (restore["ok"] and snapshot.get("loadout_id")):
                    print(manual_restore_hint(snapshot, player=args.player))

            if write_error is not None:
                # 到这里"还原成没成"已经报完了（上面那个 `finally`），现在才把写入的异常抛出去：
                # 顺序反过来的话，还原一旦也失败，看到的故障原因就变成"还原失败"，
                # 而真正的根因（写入那一步为什么炸）被盖掉。
                raise write_error

            if write_seconds is not None:
                print(
                    f"\n真写：{write_seconds:.1f}s ok={write_ok} "
                    f"code={write_code}"
                )
                print(f"  消息：{write_detail[:300]}")
                print("  回执 steps：")
                _render_steps("    ", write_steps)

            # ── 安全网 3：独立回读 + 逐槽 diff ───────────────────────
            print("\n【安全网】独立回读（inventory_assistant intent=mods）→ 逐槽 diff")
            try:
                live_after = await read_state(
                    equip, args.player, args.character, char_id, equipped_only=False
                )
                armor_after = live_after
                diff_rows, diff_gaps = await socket_diff_rows(
                    tool, ctx=ctx, player=args.player, character=args.character,
                    before=snapshot_records(equipped_before),
                )
            except Exception as exc:  # noqa: BLE001 —— 核对是可选证据，炸了要如实说而不是吞掉
                print(f"  ⚠ 逐槽 diff 没做成：{type(exc).__name__}: {exc}")
                diff_rows, diff_gaps = [], [f"逐槽 diff 没做成：{type(exc).__name__}: {exc}"]

        # ── 五张表 ────────────────────────────────────────────────────
        print(f"\n{'=' * 78}\n分段表（find {solve:.1f}s / 确认回显 {echo:.1f}s"
              + (f" / 真写 {write_seconds:.1f}s" if write_seconds else "")
              + ("；**这次没有候选，所以没有写与还原那一段**" if not builds else "")
              + "）\n" + "=" * 78)
        print_method_note()
        table_segments()
        table_span_aggregate()
        table_fetches()
        table_http()
        table_endpoints()

        if args.write:
            print("\n【还原核对】逐槽 diff（快照 mod_sockets vs 独立回读，一次 profile 读取）")
            print("\n".join(diff_rows) or "    （没有可比对的快照记录）")
            if diff_gaps:
                print(f"\n  ✗ 还没对齐的 {len(diff_gaps)} 处：")
                for gap in diff_gaps:
                    print(f"    - {gap}")
            else:
                print("  ✓ 五个部位、逐格与快照一致。")

            print("\n【已知缺口】为腾能量做的搬运**不会被自动还原**（快照只覆盖这一位角色身上的件）")
            print(
                "  这份对照的**盲区**：它的「写前现场」是在**本次快照之后**才拍的，所以只看得见"
                "「脚本启动之后」的位置变化。\n"
                "  你在跑脚本**之前**手工腾的格（顶下金装、把件搬去仓库）不在对照范围内，"
                "脚本既看不见也搬不回来 —— 那几件要自己按实例号搬回原位。"
            )
            loc_rows = await location_gap_rows(armor_before, armor_after)
            print("\n".join(loc_rows) or "    （位置没有变化：没有件被搬到别的角色/仓库）")
            skipped = [s for s in restore.get("steps", []) if s.get("action") == "armor_restore_skipped"]
            if skipped:
                print("\n  `armor_restore_skipped`（还原步骤自己报的缺口）：")
                for step in skipped:
                    print(f"    - {step.get('detail')}")
            else:
                print("\n  `armor_restore_skipped`：这次没有（还原步骤没报跳过）。")
            print(
                f"\n  快照留着（{snapshot.get('name')!r} id={snapshot.get('loadout_id')}）—— "
                "要手动兜底就用它。"
            )

        total = sum(s["seconds"] for s in SPANS)
        if not builds:
            print(f"\nfind 墙钟 {solve:.1f}s？—— 上面表的'分段合计'只覆盖被挂住的段；"
                  "`find` 里没被挂住的（Manifest、求解器）只能拿总时长做减法。")
        print(
            f"\n总耗时：find {solve:.1f}s + 确认回显 {echo:.1f}s"
            + (f" + 真写 {write_seconds:.1f}s" if write_seconds else "")
            + f"；分段墙钟合计 {total:.1f}s（嵌套，别相加）"
        )
        dump_json(
            out_path,
            {
                "targets": targets,
                "player": args.player,
                "character": args.character,
                "character_id": char_id,
                "find_seconds": solve,
                "no_candidate_reason": no_candidate_reason,
                "preview_seconds": echo,
                "write_seconds": write_seconds,
                "write_ok": write_ok,
                "write_steps": write_steps,
                "snapshot": snapshot,
                "restore": restore,
                "socket_diff": diff_rows,
                "socket_diff_gaps": diff_gaps,
                "location_gaps": loc_rows,
                "armor_before": armor_before,
                "armor_after": armor_after,
                "equipped_before": equipped_before,
            },
        )
    return 0


def snapshot_records(armor_before: dict[str, Any]) -> list[Any]:
    """把"写前读到的现场"包成带 `mod_sockets` 的记录，喂给 `socket_diff_rows`。

    形状刻意与 `LoadoutArmorState` 对齐（`item_instance_id` / `item_hash` / `mod_sockets`），
    多带一个 `slot` 只为了表里好看 —— 那三格才是"同一份判据"读的东西。
    """
    return [
        _SlotSnapshot(
            item_instance_id=instance_id,
            item_hash=int(state.get("item_hash") or 0),
            mod_sockets={int(k): int(v) for k, v in (state.get("mods") or {}).items()},
            slot=str(state.get("slot") or ""),
        )
        for instance_id, state in (armor_before.get("pieces") or {}).items()
    ]


@dataclass
class _SlotSnapshot:
    item_instance_id: str
    item_hash: int
    mod_sockets: dict[int, int]
    slot: str = ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
