"""Starside 归档的文本解析：把 HTML 单元格转成保留语义标记的纯文本。

被归档读取和评级清单共用，所以放在最低层，不依赖任何服务。
"""

from __future__ import annotations

from html.parser import HTMLParser
import re

from .starside_builds import clean

# 归档用 class 标记数值的限定条件，这些标记必须跟着文本一起保留。
SEMANTIC_MARKERS = frozenset({"pvp", "enh", "unsure", "note"})


class _SemanticText(HTMLParser):
    """Retain inline numerical qualifiers without returning executable HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, list[str]]] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        markers = [
            value
            for value in (dict(attrs).get("class") or "").split()
            if value in SEMANTIC_MARKERS
        ]
        if not self.skip:
            if tag in {"p", "div", "br", "li", "tr"}:
                self.parts.append("\n")
            self.parts.extend(f"[{marker}]" for marker in markers)
        if tag not in {"br", "img", "hr", "input", "meta", "link"}:
            self.stack.append((tag, markers))

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            _, markers = self.stack.pop()
            if not self.skip:
                self.parts.extend(f"[/{marker}]" for marker in reversed(markers))
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def semantic_text(html: str) -> str:
    parser = _SemanticText()
    parser.feed(html)
    parser.close()
    return "\n".join(
        clean(line) for line in "".join(parser.parts).splitlines() if clean(line)
    )


def cell_block(cell: dict) -> str:
    """单元格的语义文本，保留换行；表格详情用这个。"""
    html = cell.get("html") or ""
    text = semantic_text(html) if html else (cell.get("text") or "")
    for marker in (cell.get("attrs") or {}).get("class", "").split():
        if marker in SEMANTIC_MARKERS:
            text = f"[{marker}]{text}[/{marker}]"
    return text


def cell_text(cell: dict) -> str:
    """单元格的单行值：换行压成空格。"""
    return clean(re.sub(r"\s+", " ", cell_block(cell)))


def cell_lines(cell: dict) -> list[str]:
    """单元格的多值形式：保留换行，换行分开的是同一栏位的备选。"""
    return [
        value
        for value in (clean(part) for part in cell_block(cell).splitlines())
        if value
    ]
