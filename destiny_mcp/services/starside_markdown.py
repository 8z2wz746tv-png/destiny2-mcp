"""Parse author-provided Starside Markdown into local knowledge records."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from ..exceptions import ConfigError


FILE_CATEGORIES = {
    "weapon-perks.md": "weapons",
    "weapon-frames.md": "weapons",
    "exotic-weapon.md": "weapons",
    "legendary-primary.md": "weapons",
    "legendary-special.md": "weapons",
    "legendary-heavy.md": "weapons",
    "exotic-armor.md": "armor",
    "armor-mods.md": "armor",
    "armor-sets.md": "armor",
    "arc.md": "subclass",
    "solar.md": "subclass",
    "void.md": "subclass",
    "stasis.md": "subclass",
    "strand.md": "subclass",
    "prismatic.md": "subclass",
    "class-abilities.md": "subclass",
    "artifact-mods.md": "subclass",
    "boss-hp.md": "activities",
    "扭曲星球速查表.md": "activities",
    "game-mechanics.md": "mechanics",
    "buff-debuffs.md": "mechanics",
    "ammo.md": "mechanics",
}

SEMANTIC_MARKERS = {"pvp", "enh", "unsure", "note"}
_CUSTOM_TOKEN = re.compile(r"\{([A-Za-z0-9_-]+)\|([^{}]*)\}")
_IMAGE = re.compile(r"!\[([^]]*)\]\(([^)]+)\)")
_LINK = re.compile(r"(?<!!)\[([^]]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s)>]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_METADATA = re.compile(r"^([^：\n]{1,12})：\s*(.*)$")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_IDENTITY_HEADERS = (
    "PERK",
    "武器",
    "首领名称",
    "名称",
    "护甲",
    "模组",
    "技能",
    "碎片",
    "套装",
    "神器",
)


@dataclass(frozen=True)
class ParsedMarkdown:
    record: dict
    sha256: str


def _replace_custom_tokens(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        marker, body = match.group(1), match.group(2)
        if marker in SEMANTIC_MARKERS:
            return f"[{marker}]{body}[/{marker}]"
        return body

    for _ in range(20):
        updated = _CUSTOM_TOKEN.sub(replace, value)
        if updated == value:
            break
        value = updated
    return value


def semantic_markdown(value: str) -> str:
    """Keep visible text and the four qualifiers used by the site."""

    value = _replace_custom_tokens(value)
    value = _IMAGE.sub(lambda match: match.group(1), value)
    value = _LINK.sub(lambda match: match.group(1), value)
    value = value.replace("<br>", "\n").replace("<br/>", "\n")
    value = value.replace("\\\\", "\n")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?\s*$", "", line)
        line = line.replace("**", "").replace("__", "")
        line = re.sub(r"[ \t]+", " ", line).strip(" |\t")
        if line:
            lines.append(line)
    return "\n".join(lines)


def _split_table_row(line: str) -> list[str]:
    """Split a Markdown row without breaking custom ``{type|text}`` tokens."""

    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith(r"\|"):
        value = value[:-1]
    cells: list[str] = []
    current: list[str] = []
    brace_depth = 0
    in_code = False
    for index, char in enumerate(value):
        slash_count = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            slash_count += 1
            cursor -= 1
        escaped = slash_count % 2 == 1
        if char == "`" and not escaped:
            in_code = not in_code
        elif char == "{" and not in_code and not escaped:
            brace_depth += 1
        elif char == "}" and not in_code and not escaped:
            brace_depth = max(0, brace_depth - 1)
        if char == "|" and not escaped and not in_code and brace_depth == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _is_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _heading_context(headings: list[tuple[int, str]]) -> str:
    return " / ".join(title for _, title in headings)


def _metadata(lines: list[str], first_section: int) -> dict[str, str]:
    result = {}
    for line in lines[:first_section]:
        match = _METADATA.match(line.strip())
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def _external_links(text: str) -> list[str]:
    links = [match.group(2) for match in _LINK.finditer(text)]
    links.extend(_URL.findall(_LINK.sub("", text)))
    return list(dict.fromkeys(link.rstrip(".,，。") for link in links))


def _entry_id(filename: str, kind: str, identity: str) -> str:
    digest = sha256(f"{filename}\0{kind}\0{identity}".encode()).hexdigest()[:24]
    return f"share-{kind}:{digest}"


def _parse_tables(
    lines: list[str], heading_at_line: dict[int, str], filename: str
) -> tuple[list[dict], list[dict]]:
    tables: list[dict] = []
    entries: list[dict] = []
    current_heading = ""
    index = 0
    while index < len(lines):
        if index in heading_at_line:
            current_heading = heading_at_line[index]
        if (
            index + 1 >= len(lines)
            or not lines[index].lstrip().startswith("|")
            or not _is_separator(lines[index + 1])
        ):
            index += 1
            continue
        raw_headers = _split_table_row(lines[index])
        headers = [semantic_markdown(cell) for cell in raw_headers]
        raw_rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
            raw_rows.append(_split_table_row(lines[cursor]))
            cursor += 1
        width = max([len(headers), *(len(row) for row in raw_rows)], default=0)
        headers.extend("" for _ in range(width - len(headers)))
        table_rows = [
            [
                {"tag": "th", "text": header, "html": "", "attrs": {}}
                for header in headers
            ]
        ]
        inherited_first = ""
        for row_number, raw_row in enumerate(raw_rows, 1):
            raw_row.extend("" for _ in range(width - len(raw_row)))
            cells = [semantic_markdown(cell) for cell in raw_row]
            table_rows.append(
                [
                    {"tag": "td", "text": cell, "html": "", "attrs": {}}
                    for cell in cells
                ]
            )
            if cells and cells[0]:
                inherited_first = cells[0]
            context = current_heading
            if cells and not cells[0] and inherited_first:
                context = " / ".join(filter(None, (context, inherited_first)))
            title = ""
            for preferred in _IDENTITY_HEADERS:
                title = next(
                    (
                        cells[column]
                        for column, header in enumerate(headers)
                        if header.casefold() == preferred.casefold() and cells[column]
                    ),
                    "",
                )
                if title:
                    break
            title = title or next(
                (cell for cell in cells if cell), current_heading or filename
            )
            pairs = [
                f"{headers[column] or f'列 {column + 1}'}：{cell}"
                for column, cell in enumerate(cells)
                if cell
            ]
            if inherited_first and cells and not cells[0]:
                pairs.insert(0, f"{headers[0] or '分组'}：{inherited_first}（继承上行）")
            entries.append(
                {
                    "knowledge_id": _entry_id(
                        filename,
                        "row",
                        f"{index + 1}:{row_number}:{'|'.join(raw_row)}",
                    ),
                    "title": title,
                    "kind": "table_row",
                    "group": context,
                    "text": "\n".join(filter(None, (context, *pairs))),
                    "line_start": index + row_number + 2,
                    "line_end": index + row_number + 2,
                }
            )
        tables.append(
            {
                "caption": "",
                "heading": current_heading,
                "rows": table_rows,
            }
        )
        index = cursor
    return tables, entries


def parse_markdown_document(path: Path, root: Path) -> ParsedMarkdown:
    try:
        body = path.read_bytes()
        text = body.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Starside Markdown 资料损坏或无法读取：{path.name}。") from exc

    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        raise ConfigError(f"Starside Markdown 资料为空：{path.name}。")
    digest = sha256(body).hexdigest()
    headings: list[dict] = []
    stack: list[tuple[int, str]] = []
    heading_at_line: dict[int, str] = {}
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = semantic_markdown(match.group(2))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        context = _heading_context(stack)
        headings.append({"index": index, "level": level, "title": title, "group": context})
        heading_at_line[index] = context

    first_nonempty = next(line.strip() for line in lines if line.strip())
    document_title = next(
        (heading["title"] for heading in headings if heading["level"] == 1),
        semantic_markdown(first_nonempty),
    )
    first_section = next(
        (heading["index"] for heading in headings if heading["level"] > 1),
        len(lines),
    )
    metadata = _metadata(lines, first_section)
    document_entries = []
    for number, heading in enumerate(headings):
        if heading["level"] == 1:
            continue
        end = len(lines)
        for candidate in headings[number + 1 :]:
            if candidate["level"] <= heading["level"]:
                end = candidate["index"]
                break
        content = semantic_markdown("\n".join(lines[heading["index"] + 1 : end]))
        document_entries.append(
            {
                "knowledge_id": _entry_id(
                    path.name,
                    "section",
                    f"{heading['level']}:{heading['group']}:{heading['index'] + 1}",
                ),
                "title": heading["title"],
                "kind": "section",
                "group": heading["group"],
                "text": "\n".join(filter(None, (heading["group"], content))),
                "line_start": heading["index"] + 1,
                "line_end": end,
            }
        )

    tables, table_entries = _parse_tables(lines, heading_at_line, path.name)
    document_entries.extend(table_entries)
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    page_id = f"page:share/{relative}"
    record = {
        "page_id": page_id,
        "source_type": "author_markdown",
        "title": document_title,
        "description": semantic_markdown(metadata.get("描述", "")),
        "category": FILE_CATEGORIES.get(path.name, "other"),
        "updated_at": metadata.get("更新", ""),
        "text": semantic_markdown(text),
        "source_blocks": [],
        "tables": tables,
        "external_links": _external_links(text),
        "record_sha256": digest,
        "local_path": f"share/{relative}",
        "metadata": {key: semantic_markdown(value) for key, value in metadata.items()},
        "document_entries": document_entries,
    }
    return ParsedMarkdown(record=record, sha256=digest)
