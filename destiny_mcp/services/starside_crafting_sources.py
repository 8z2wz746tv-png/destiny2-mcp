"""Starside「锻造武器来源」：按武器名索引那一页，给图样查询挂来源。

消费者是 `weapon_assistant(intent="patterns")`：图样进度是**账号事实**，来源是**社区参考**，
两者在响应里必须分开放、各带自己的出处（`trust`/`updated_at`）。

取数走 `StarsideService.get_knowledge()` 这个公开入口，不改那个服务（它已经贴着体量上限）；
本模块自己只吃"页面表格 + 页面正文 + 出处"，建完索引就与 Starside 无关。

判据是"名字命中"，而清单里的写法与武器名常有出入，所以归一化只写在这一处（`note_key`）。
实测这四条（去空白与间隔号、大小写折叠、去 `_v1.0.3` 这类版本后缀、统一横线）把
`IKELOS_SR` ↔ `IKELOS_SR_v1.0.3`、`伊尔约特之歌` ↔ `伊尔·约特之歌` 对上：
183 条图样里表格命中 170、页面正文另命中 2（宿命、享乐主义）、11 条这份清单里确实没有。
**没命中只能说这份清单没有这一行，不能说这把武器没有来源。**
"""

from __future__ import annotations

import re
from typing import Any

PAGE_ID = "page:crafting/index.html"

# get_knowledge 的分页单位：表格按行（一次最多 40，够一页拿完），正文按字符（6000）。
_TABLE_PAGE = 40
# 单次最多匹配多少名字。超了要显式报 truncated —— 截断会被读成"清单里没有"。
MAX_QUERY_NAMES = 400
# 正文命中的标签里带原文，太长就截断（保留可追溯性，又不至于刷屏）。
_PROSE_LABEL_LIMIT = 80
# 防呆：页面正文最多读几段（正常两三段就读完；上游数据异常时不至于无限翻页）。
_MAX_TEXT_CHUNKS = 8

_VERSION_SUFFIX = re.compile(r"_v[\d.]+$")
_DROPPED = re.compile(r"[\s·・\-–—_]")
_NAME_SPLIT = re.compile(r"[、，,／/]")
# 出处里对调用方有意义的字段；其余（哈希、抓取时间）留在 Starside 那边，不往这里抄。
_PAGE_FIELDS = ("title", "url", "local_path", "source_type", "updated_at", "snapshot_id", "trust")


def note_key(value: str) -> str:
    """名字归一化：清单写法与武器名比对时唯一的口径。"""
    text = (value or "").strip().casefold()
    text = _VERSION_SUFFIX.sub("", text)
    return _DROPPED.sub("", text)


class CraftingSources:
    """「锻造武器来源」的名字索引：表格优先（能给出分组），正文兜底。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        page_text: str = "",
        *,
        page: dict[str, Any] | None = None,
    ) -> None:
        self._rows = list(rows or [])
        self._page_text = page_text or ""
        self._page = {key: value for key, value in (page or {}).items() if key in _PAGE_FIELDS}
        self._table: dict[str, list[str]] = {}
        self._prose: list[tuple[str, str]] = []
        self._built = False

    @classmethod
    def from_service(cls, starside: Any) -> CraftingSources:
        """唯一的 I/O 入口：把那一页的表格、正文和出处读出来交给索引。"""
        rows: list[dict[str, Any]] = []
        page: dict[str, Any] = {}
        offset = 0
        while True:
            payload = starside.get_knowledge(PAGE_ID, section="tables", offset=offset, limit=_TABLE_PAGE)
            page = page or dict(payload.get("source") or {})
            rows.extend(payload.get("rows") or [])
            if payload.get("next_offset") is None:
                break
            offset = payload["next_offset"]

        text = ""
        offset = 0
        for _ in range(_MAX_TEXT_CHUNKS):
            payload = starside.get_knowledge(PAGE_ID, section="text", offset=offset)
            page = page or dict(payload.get("source") or {})
            text += payload.get("text") or ""
            if payload.get("next_offset") is None:
                break
            offset = payload["next_offset"]
        return cls(rows, text, page=page)

    # ── 索引 ─────────────────────────────────────────────────────────
    def _build(self) -> None:
        if self._built:
            return
        self._built = True
        for number, row in enumerate(self._rows):
            heading = str(row.get("heading") or "").strip() or f"表{number + 1}"
            cells = [cell for cell in (row.get("cells") or []) if isinstance(cell, dict)]
            if not cells:
                continue
            # 第一格是行首标签（分组/目的地/任务）；名字在其余格里用「、」连写，
            # 换行分开的是同一栏位的备选，所以按行拆、再把每行按顿号拆。
            label = " ".join(str(cells[0].get("text") or "").split()) or f"第{number + 1}行"
            for cell in cells[1:]:
                for line in str(cell.get("text") or "").splitlines():
                    for name in _NAME_SPLIT.split(line):
                        key = note_key(name)
                        if not key:
                            continue
                        tag = f"{heading}｜{label}"
                        tags = self._table.setdefault(key, [])
                        if tag not in tags:
                            tags.append(tag)
        for line in self._page_text.splitlines():
            text = " ".join(line.split())
            # 表格行上一步已经按行归属了，正文兜底只看非表格行。
            if not text or text.startswith("|"):
                continue
            self._prose.append((note_key(text), text))

    def names(self) -> list[str]:
        """索引到的名字键数量（诊断与测试用）。"""
        self._build()
        return list(self._table)

    def lookup(self, names: str | list[str], *, limit: int = MAX_QUERY_NAMES) -> dict:
        """按武器名批量查来源；没命中的名字进 `unmatched`，不会被当成"没有来源"。"""
        self._build()
        wanted = [names] if isinstance(names, str) else list(names or [])
        requested: list[str] = []
        for value in wanted:
            if isinstance(value, str) and value.strip() and value not in requested:
                requested.append(value)
        limit = max(1, min(limit, MAX_QUERY_NAMES))
        selected = requested[:limit]

        results: list[dict] = []
        unmatched: list[str] = []
        for name in selected:
            key = note_key(name)
            labels = list(self._table.get(key) or [])
            kind = "table"
            if not labels:
                line = self._prose_match(key)
                if line:
                    kind = "prose"
                    labels.append("正文｜" + self._clip(line))
            if not labels:
                unmatched.append(name)
                continue
            results.append({"name": name, "source": "；".join(labels), "kind": kind})

        available = bool(self._table or self._prose)
        return {
            "available": available,
            "matched_count": len(results),
            "returned_count": len(results),
            "truncated": len(requested) > len(selected),
            "results": results,
            "unmatched": unmatched,
            "page": dict(self._page),
            "coverage_scope": "starside_crafting_page_only",
            "warnings": self._warnings(available),
        }

    @staticmethod
    def _clip(value: str) -> str:
        return value if len(value) <= _PROSE_LABEL_LIMIT else value[: _PROSE_LABEL_LIMIT - 1] + "…"

    def _prose_match(self, key: str) -> str:
        """正文兜底：要求词边界，避免 `ikelossm` 命中 `ikelossmg` 这种前缀误配。"""
        if not key:
            return ""
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])")
        for prose_key, prose_line in self._prose:
            if pattern.search(prose_key):
                return prose_line
        return ""

    @staticmethod
    def _warnings(available: bool) -> list[str]:
        if not available:
            return [
                "本地未安装 Starside「锻造武器来源」资料；不能据此判断某把武器没有来源。"
            ]
        return [
            "来源是本地社区资料（Starside），不是实时掉率；引用时保留页面与更新时间。",
            "清单里没有这一行，只说明本地资料没收录，不代表这把武器没有来源。",
        ]
