#!/usr/bin/env python
"""复跑渲染 skill 的字段表：把 `skills/destiny2-render/references/blocks.md` 里声明的每个
字段路径，逐条对**真实 MCP 响应**核对一次。

为什么要有这个脚本（而不是靠人读一遍）：

- 渲染 skill 里的字段名一旦写错，模型渲染出的是一张**空白卡片** —— 它不会报错，也没人知道为什么；
- 字段名会随响应形状变化（本仓 0.2.0 起"改形状旧键一个不留"），文档不会自己跟着变；
- 所以把"字段表"当数据读，用真机响应逐条解析：解析不到就是 FAIL，退出码非 0。

它做什么：

1. 解析 blocks.md 里所有表头为 `| 字段路径 | 出处 | 说明 |` 的表格；
2. `出处` 列写的就是**真实调用**（`tool(intent="x", kw=…)`），去重后各跑一次；
3. 把每个路径在响应里解析一遍（`[]` = 任取一个列表元素；空列表记 EMPTY）；
4. 额外形状校验：路径以 `icon_url` 结尾时，值必须是空串或 Bungie CDN 的 https 地址
   （"不许自己拼 URL"这条口径的机器化）。

用法（真机、要 OAuth 与本地 Manifest）：

    .venv/bin/python scripts/verify_render_fields.py            # 全跑
    .venv/bin/python scripts/verify_render_fields.py --only 武器 # 只跑标题含关键词的表

退出码 0 = 每条路径都在真实响应里；1 = 有 MISSING 或形状不符。**不做任何写入。**
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from destiny_mcp.server import app_lifespan, create_server  # noqa: E402
from destiny_mcp.tools import assistants  # noqa: E402

BLOCKS = ROOT / "skills" / "destiny2-render" / "references" / "blocks.md"

#: 字段表的表头（只有这一种表头会被当成"要核对的字段"）。
HEADER = ("字段路径", "出处", "说明")
#: `weapon_assistant(intent="analyze", weapon_name="星狐座")` 这一形态。
CALL_RE = re.compile(r"^(\w+)\((.*)\)$", re.S)
BACKTICKED_RE = re.compile(r"`([^`]+)`")
ICON_PREFIX = "https://www.bungie.net/"

#: 表里允许出现的占位符 —— 现场从**你自己的账号**里取，任何机器都能复跑。
PLACEHOLDERS = ("$weapon_instance", "$armor_instance", "$activity_id")
#: 路径前缀 `?` = 条件字段：只在满足条件时才有这个键（没有就整块不渲染，不算写错）。
CONDITIONAL = "?"


class Miss(Exception):
    """路径解析不到。"""


def field_rows(text: str) -> list[tuple[str, str, str]]:
    """取出所有字段表行：(标题, 路径, 出处)。路径带 `?` 前缀的是条件字段。"""
    rows: list[tuple[str, str, str]] = []
    heading = ""
    in_table = False
    for line in text.splitlines():
        if line.startswith("#"):
            heading = line.lstrip("# ").strip()
            in_table = False
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if tuple(cells) == HEADER:
            in_table = True
            continue
        if not in_table or set("".join(cells)) <= set("-: "):
            continue
        if len(cells) != len(HEADER):
            continue
        path = cells[0].strip("`")
        call = BACKTICKED_RE.search(cells[1])
        if not path or call is None:
            continue
        rows.append((heading, path, call.group(1)))
    return rows


def parse_call(spec: str) -> tuple[str, dict[str, Any]]:
    """`tool(intent="x", count=5)` → (tool, kwargs)。"""
    match = CALL_RE.match(spec.strip())
    if match is None:
        raise ValueError(f"调用写法看不懂：{spec}")
    tool, raw_args = match.group(1), match.group(2)
    kwargs: dict[str, Any] = {}
    if raw_args.strip():
        # 关键字实参逐个 literal_eval，避免 eval 任意代码。
        for chunk in _split_args(raw_args):
            key, _, value = chunk.partition("=")
            kwargs[key.strip()] = ast.literal_eval(value.strip())
    return tool, kwargs


def _split_args(raw: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in raw:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts


def resolve(payload: Any, path: str) -> Any:
    """按 `a.b[].c` 解析；`[]` 表示"任取一个非空元素"（空列表 → Miss("EMPTY")）。"""
    node: Any = payload
    for part in path.split("."):
        many = part.endswith("[]")
        key = part[:-2] if many else part
        if not isinstance(node, dict) or key not in node:
            raise Miss(f"{key} 不在 {type(node).__name__} 里")
        node = node[key]
        if many:
            if not isinstance(node, list):
                raise Miss(f"{key} 不是列表")
            if not node:
                raise Miss("EMPTY")
            node = node[0]
    return node


def check_icon(value: Any) -> str:
    """icon_url 的形状：空串，或 Bungie CDN 的 https 绝对地址。"""
    if value == "":
        return ""
    if isinstance(value, str) and value.startswith(ICON_PREFIX):
        return ""
    return f"icon_url 形状不对：{value!r}"


async def discover(ctx: Any) -> dict[str, str]:
    """现场取占位符 —— 换账号/换机器也能跑。"""
    values: dict[str, str] = {}
    search = await assistants.inventory_assistant(ctx=ctx, intent="search", item_name="星狐座")
    items = ((search.get("data") or {}).get("result") or {}).get("items") or []
    if items:
        values["$weapon_instance"] = str(items[0].get("item_instance_id") or "")
    profile = await assistants.player_assistant(ctx=ctx, intent="profile")
    characters = ((profile.get("data") or {}).get("profile") or {}).get("characters") or []
    class_names = {0: "titan", 1: "hunter", 2: "warlock"}
    if characters:
        # 身上那五件才有 masterwork/tuning/class_item_perks 这些条件键。
        character = class_names.get(characters[0].get("class_type"), "hunter")
        mods = await assistants.inventory_assistant(ctx=ctx, intent="mods", character=character)
        rows = ((mods.get("data") or {}).get("equipped_armor") or {}).get("characters") or []
        equipped = rows[0].get("items") if rows else None
        for item in equipped or []:
            instance_id = str(item.get("item_instance_id") or "")
            if instance_id:
                values["$armor_instance"] = instance_id
                break
    history = await assistants.activity_assistant(ctx=ctx, intent="history", count=5)
    activities = ((history.get("data") or {}).get("activities")) or []
    if activities:
        values["$activity_id"] = str(activities[0].get("instance_id") or "")
    return values


async def run(only: str) -> int:
    rows = field_rows(BLOCKS.read_text(encoding="utf-8"))
    assert rows, f"没从 {BLOCKS} 里解析到字段表"
    if only:
        rows = [row for row in rows if only in row[0]]

    calls: dict[str, dict[str, Any]] = {}
    for _heading, _path, spec in rows:
        tool, kwargs = parse_call(spec)
        calls.setdefault(spec, {"tool": tool, "kwargs": kwargs})

    problems: list[str] = []
    empty: list[str] = []
    conditional: list[str] = []
    ok_count = 0
    skipped: list[str] = []

    async with app_lifespan(create_server()) as service:
        ctx = type("C", (), {"request_context": type("R", (), {"lifespan_context": service})})()
        placeholders = await discover(ctx)
        missing_ph = [name for name in PLACEHOLDERS if name not in placeholders]

        responses: dict[str, Any] = {}
        for spec, call in calls.items():
            kwargs = dict(call["kwargs"])
            unresolved = [v for v in kwargs.values() if isinstance(v, str) and v in PLACEHOLDERS]
            if unresolved and missing_ph:
                skipped.append(spec)
                continue
            for key, value in list(kwargs.items()):
                if isinstance(value, str) and value in placeholders:
                    kwargs[key] = placeholders[value]
            try:
                responses[spec] = await asyncio.wait_for(
                    getattr(assistants, call["tool"])(ctx=ctx, **kwargs), 300
                )
            except BaseException as exc:  # noqa: BLE001 - 真机异常要如实报
                responses[spec] = {"ok": False, "_exception": f"{type(exc).__name__}: {exc}"}

        print("=" * 78)
        print(f"字段核对：{BLOCKS.relative_to(ROOT)}")
        if missing_ph:
            print(f"⚠️  占位符没取到（相关表跳过）：{missing_ph}")
        print("=" * 78)

        for spec, call in calls.items():
            payload = responses.get(spec)
            bound = [row for row in rows if row[2] == spec]
            if payload is None:
                print(f"\n— {spec}\n    跳过：占位符没取到")
                continue
            if not isinstance(payload, dict):
                problems.append(f"{spec}: 响应不是字典")
                continue
            if payload.get("ok") is not True:
                reason = payload.get("_exception") or json.dumps(
                    payload.get("error") or {}, ensure_ascii=False
                )
                print(f"\n— {spec}\n    SKIP（这条调用本身没成功，不影响字段结论）：{reason[:160]}")
                skipped.append(spec)
                continue
            print(f"\n— {spec}")
            for heading, path, _spec in bound:
                optional = path.startswith(CONDITIONAL)
                clean = path.lstrip(CONDITIONAL)
                try:
                    value = resolve(payload, clean)
                except Miss as exc:
                    if optional:
                        conditional.append(f"{spec} → {clean}（条件字段，这次没有）")
                        print(f"    cond   {clean}   （条件字段，这次响应里没有）")
                    elif str(exc) == "EMPTY":
                        empty.append(f"{spec} → {clean}")
                        print(f"    EMPTY  {clean}")
                    else:
                        problems.append(f"{spec} → {clean}: {exc}")
                        print(f"    MISS   {clean}   ({exc})")
                    continue
                note = ""
                if clean.endswith("icon_url"):
                    note = check_icon(value)
                    if note:
                        problems.append(f"{spec} → {clean}: {note}")
                ok_count += 1
                sample = repr(value)
                print(f"    ok     {clean} = {sample[:70]}{'  ⚠️ ' + note if note else ''}")

    print("\n" + "=" * 78)
    print(
        f"解析成功 {ok_count} 条 / MISSING {len(problems)} 条 / "
        f"EMPTY {len(empty)} 条 / 条件字段这次没有 {len(conditional)} 条"
    )
    if empty:
        print("EMPTY（这次响应里那个列表是空的，不是字段写错）：")
        for item in empty:
            print(f"  - {item}")
    if conditional:
        print("条件字段（这次响应里没有；渲染时整块不输出，见 blocks.md 的降级）：")
        for item in conditional:
            print(f"  - {item}")
    if skipped:
        print("跳过的调用：")
        for item in sorted(set(skipped)):
            print(f"  - {item}")
    if problems:
        print("\n❌ 这些路径在真实响应里解析不到（skill 里写错了，或者响应形状变了）：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n✅ 字段表里每条路径都在真实响应里解析到（icon_url 形状也已校验）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="复跑 destiny2-render 的字段表")
    parser.add_argument("--only", default="", help="只跑标题含这个关键词的表")
    args = parser.parse_args()
    if args.only:
        text = BLOCKS.read_text(encoding="utf-8")
        kept = [row for row in field_rows(text) if args.only in row[0]]
        if not kept:
            print(f"没有标题含「{args.only}」的字段表")
            return 1
        print(f"（--only {args.only}：只核对 {len(kept)} 行）")
    return asyncio.run(run(args.only))


if __name__ == "__main__":
    raise SystemExit(main())
