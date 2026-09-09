#!/usr/bin/env python3
"""Download the public Starside static site into a local, classified cache.

This crawler intentionally stays within https://starside.work, skips external
documents and API endpoints, and uses a small delay between requests. The
output is generated data and should not be committed without the author's
permission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx


BASE_URL = "https://starside.work/"
HOST = "starside.work"
USER_AGENT = "destiny2-mcp-starside-cache/1.0"
ASSET_EXTENSIONS = {
    ".css", ".js", ".svg", ".webp", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".woff", ".woff2", ".ttf", ".json", ".pdf", ".mp4", ".mp3",
}
SKIP_COMPONENTS = {"admin", "api", "docs", ".git", "login"}
PAGE_EXTENSIONS = {".html", ".htm"}
MAX_FILE_BYTES = 25 * 1024 * 1024
BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "tr", "h1", "h2", "h3", "h4"}


def classify(path: str) -> str:
    top = path.strip("/").split("/", 1)[0]
    groups = {
        "builds": "builds",
        "weapon-perks": "weapons",
        "weapon-frames": "weapons",
        "exotic-weapon": "weapons",
        "exotic-weapons": "weapons",
        "shopping-primary": "weapons",
        "shopping-special": "weapons",
        "shopping-heavy": "weapons",
        "shopping-other": "weapons",
        "legendary-primary": "weapons",
        "legendary-special": "weapons",
        "legendary-heavy": "weapons",
        "armor-mods": "armor",
        "armor-sets": "armor",
        "exotic-armor": "armor",
        "farming-sets": "armor",
        "elements": "subclass",
        "artifact-mods": "subclass",
        "boss-hp": "activities",
        "dps": "activities",
        "swap-dps": "activities",
        "skill-damage": "activities",
        "pve-farming": "activities",
        "raid-guides": "activities",
        "rotation": "activities",
        "crafting": "activities",
        "ability-cooldown": "mechanics",
        "buff-debuffs": "mechanics",
        "combatant-scalars": "mechanics",
        "game-mechanics": "mechanics",
        "ammo": "mechanics",
        "power-delta": "mechanics",
        "changelog": "sources",
        "sources": "sources",
        "palette": "sources",
    }
    return groups.get(top, "other")


def normalize_url(raw: str, base: str) -> str | None:
    if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#")):
        return None
    try:
        parsed = urlparse(urldefrag(urljoin(base, raw))[0])
        if (parsed.scheme != "https" or parsed.hostname != HOST
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
            return None
    except ValueError:
        return None
    path = unquote(parsed.path or "/")
    if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        return None
    if any(ord(char) < 32 for char in path):
        return None
    if set(path.lower().split("/")) & SKIP_COMPONENTS:
        return None
    if path.endswith("/"):
        path += "index.html"
    suffix = Path(path).suffix.lower()
    if suffix not in PAGE_EXTENSIONS | ASSET_EXTENSIONS:
        return None
    # The site's q parameter only filters an already complete static page.
    if parsed.query and set(parse_qs(parsed.query, keep_blank_values=True)) - {"q", "v"}:
        return None
    return f"https://{HOST}" + quote(path, safe="/-._~")


class PageParser(HTMLParser):
    """Extract navigational links, asset references, text and page metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()
        self.assets: set[str] = set()
        self.text_parts: list[str] = []
        self.source_text: list[str] = []
        self.source_blocks: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.tables: list[dict[str, Any]] = []
        self._table: dict[str, Any] | None = None
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None
        self._heading: dict[str, str] | None = None
        self._scripts: list[str] = []
        self.styles: list[str] = []
        self._in_script = False
        self._in_style = False
        self._stamp_tag: str | None = None
        self._stamp_parts: list[str] = []
        self.title: str = ""
        self.description: str = ""
        self.data_src: str = ""
        self.data_hash: str = ""
        self._skip_depth = 0
        self._in_title = False
        self._in_source = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "script":
            self._in_script = True
        if tag == "style":
            self._in_style = True
        if values.get("style"):
            self.styles.append(values["style"] or "")
        if "stamp" in (values.get("class") or "").split():
            self._stamp_tag = tag
        if tag == "title":
            self._in_title = True
        if tag == "pre" and (values.get("id") == "src" or "src" in (values.get("class") or "").split()):
            self._in_source = True
            self.source_text = []
        if tag == "meta" and values.get("name") == "description":
            self.description = values.get("content", "") or ""
        if tag == "main":
            self.data_src = values.get("data-src", "") or ""
            self.data_hash = values.get("data-hash", "") or ""
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading = {"level": tag, "id": values.get("id") or "", "text": ""}
        if tag == "table":
            self._table = {"heading": self.headings[-1]["text"] if self.headings else "", "rows": []}
        if tag == "tr":
            self._row = []
        if tag in {"td", "th"}:
            self._cell = {"tag": tag, "attrs": values, "text": "", "html": ""}
        elif self._cell is not None:
            self._cell["html"] += self.get_starttag_text() or ""
        if tag in BLOCK_TAGS:
            self.text_parts.append("\n")
            if self._cell is not None:
                self._cell["text"] += "\n"
        for key in ("href", "src", "poster"):
            raw = values.get(key)
            if not raw:
                continue
            if key in {"src", "poster"} or Path(urlparse(raw).path).suffix.lower() in ASSET_EXTENSIONS:
                self.assets.add(raw)
            else:
                self.links.add(raw)
        for candidate in (values.get("srcset") or "").split(","):
            if candidate.strip():
                self.assets.add(candidate.strip().split()[0])

    def handle_endtag(self, tag: str) -> None:
        if tag == self._stamp_tag:
            self._stamp_tag = None
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "script":
            self._in_script = False
        if tag == "style":
            self._in_style = False
        if tag == "title":
            self._in_title = False
        if tag == "pre" and self._in_source:
            self._in_source = False
            self.source_blocks.append("".join(self.source_text).strip())
        if tag in {"h1", "h2", "h3", "h4"} and self._heading is not None:
            self._heading["text"] = self._heading["text"].strip()
            self.headings.append(self._heading)
            self._heading = None
        if tag in {"td", "th"} and self._cell is not None:
            self._cell["text"] = self._cell["text"].strip()
            if self._row is not None:
                self._row.append(self._cell)
            self._cell = None
        elif self._cell is not None:
            self._cell["html"] += f"</{tag}>"
        if tag == "tr" and self._row is not None:
            if self._table is not None:
                self._table["rows"].append(self._row)
            self._row = None
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        if tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_source:
            self.source_text.append(data)
        if self._in_script:
            self._scripts.append(data)
        if self._in_style:
            self.styles.append(data)
        if self._stamp_tag:
            self._stamp_parts.append(data)
        if self._heading is not None:
            self._heading["text"] += data
        if self._cell is not None:
            self._cell["text"] += data
            self._cell["html"] += escape(data)
        if not self._skip_depth:
            self.text_parts.append(data)

    def result(self, url: str) -> dict[str, Any]:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.text_parts))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        source = "\n\n".join(self.source_blocks)
        updated = re.findall(
            r"更新[：:\s]*(\d{4}\.\d{1,2}\.\d{1,2})",
            "".join(self._stamp_parts) or source,
        )
        return {
            "url": url,
            "title": self.title.strip(),
            "description": self.description.strip(),
            "category": classify(urlparse(url).path),
            "updated_at": updated[0] if updated else "",
            "data_src": self.data_src,
            "data_hash": self.data_hash,
            "source_text": source,
            "source_blocks": self.source_blocks,
            "headings": self.headings,
            "tables": self.tables,
            "text": text,
        }


def script_assets(text: str, url: str) -> set[str]:
    """Discover literal public dependencies, without evaluating remote code."""
    found = set()
    # The site uses lazy('x.js'), import('x.js'), and filename replacement.
    for raw in re.findall(r'''["']([^\s"'<>]+\.(?:js|css|json|woff2?|webp|png|jpg|svg))["']''', text):
        if raw.startswith(("assets/", "builds/")) or "/icons/" in raw:
            base = BASE_URL
        else:
            base = url
        normalized = normalize_url(raw, base)
        if normalized:
            found.add(normalized)
    return found


def css_assets(text: str, url: str) -> set[str]:
    found = set()
    for raw in re.findall(r'''url\(\s*["']?([^"')]+)["']?\s*\)''', text):
        normalized = normalize_url(raw.strip(), url)
        if normalized:
            found.add(normalized)
    return found


def assigned_json(text: str, variable: str) -> Any:
    prefix = f"window.{variable} ="
    if not text.lstrip().startswith(prefix):
        raise ValueError(f"Unexpected format for {variable}")
    payload, end = json.JSONDecoder().raw_decode(text.lstrip()[len(prefix):].lstrip())
    if text.lstrip()[len(prefix):].lstrip()[end:].strip() not in {"", ";"}:
        raise ValueError(f"Unexpected trailing code for {variable}")
    return payload


def relative_path(url: str, default_suffix: str = ".json") -> Path:
    path = urlparse(url).path.strip("/") or "index.html"
    if path.endswith("/"):
        path += "index.html"
    result = Path(path)
    if default_suffix and result.suffix.lower() in {".html", ".htm"}:
        result = result.with_suffix(default_suffix)
    return result


def save_json(path: Path, payload: Any) -> None:
    save_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def save_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)


class Downloader:
    def __init__(self, output: Path, delay: float, timeout: float, refresh: bool):
        self.output, self.delay, self.refresh = output, delay, refresh
        self.last_request = 0.0
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
        self.robots = RobotFileParser()
        self.robots_status = "not_checked"

    def close(self) -> None:
        self.client.close()

    def request(self, url: str) -> tuple[bytes, str, str]:
        current = url
        for _ in range(5):
            for attempt in range(3):
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    with self.client.stream("GET", current) as response:
                        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                            wait = response.headers.get("Retry-After", "")
                            time.sleep(min(45, max(2 ** (attempt + 1), int(wait) if wait.isdigit() else 0)))
                            continue
                        if response.is_redirect:
                            current = normalize_url(response.headers.get("location", ""), current)
                            if not current:
                                raise ValueError("Redirect leaves allowed public static scope")
                            if self.robots_status != "not_checked" and not self.robots.can_fetch(USER_AGENT, current):
                                raise ValueError("Redirect target disallowed by robots.txt")
                            break
                        response.raise_for_status()
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                raise ValueError("File exceeds archive size limit")
                            chunks.append(chunk)
                        return b"".join(chunks), response.headers.get("content-type", "").split(";")[0], current
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** (attempt + 1))
        raise ValueError("Too many redirects")

    def check_robots(self) -> None:
        try:
            body, mime, _ = self.request(BASE_URL + "robots.txt")
            save_bytes(self.output / "metadata" / "robots-response.txt", body)
            if mime == "text/html" or body.lstrip().startswith(b"<"):
                self.robots_status = "html_instead_of_robots_rules"
                self.robots.parse([])
            else:
                self.robots.parse(body.decode("utf-8", errors="replace").splitlines())
                self.robots_status = "parsed"
                self.delay = max(self.delay, self.robots.crawl_delay(USER_AGENT) or 0)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            self.robots.parse([])
            self.robots_status = "not_found"

    def get(self, url: str, page: bool) -> tuple[bytes, dict[str, Any]]:
        target = self.output / ("pages" if page else "assets") / relative_path(url, "")
        if not target.resolve().is_relative_to(self.output):
            raise ValueError("Archive target escapes output directory")
        if not self.robots.can_fetch(USER_AGENT, url):
            raise ValueError("Disallowed by robots.txt")
        if target.is_file() and not self.refresh:
            body = target.read_bytes()
            mime, final_url = "cached", url
        else:
            body, mime, final_url = self.request(url)
            if page and mime != "text/html":
                raise ValueError(f"Expected HTML, received {mime}")
            if not page and (mime == "text/html" or body.lstrip().lower().startswith((b"<!doctype html", b"<html"))):
                raise ValueError("Static resource returned HTML (possible soft 404)")
            save_bytes(target, body)
        return body, {
            "url": url, "final_url": final_url, "path": str(target.relative_to(self.output)),
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "content_type": mime,
            "fetched_at": datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/starside"))
    parser.add_argument("--delay", type=float, default=0.35, help="Minimum seconds between request starts")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--max-assets", type=int, default=15000)
    parser.add_argument("--no-assets", action="store_true", help="Do not download same-origin assets")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached files; default resumes using saved files")
    args = parser.parse_args()
    if args.delay < 0.1:
        parser.error("--delay must be at least 0.1 seconds")

    output: Path = args.output.resolve()
    queue: deque[str] = deque()
    asset_queue: deque[str] = deque()
    scheduled: set[str] = set()
    seen: set[str] = set()
    pages: list[dict[str, Any]] = []
    asset_urls: set[str] = set()
    assets: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    excluded: set[str] = set()
    external: dict[str, set[str]] = {}
    exports: list[str] = []
    categories: dict[str, list[dict[str, Any]]] = {}
    downloader = Downloader(output, args.delay, args.timeout, args.refresh)

    def schedule(raw: str, base: str) -> None:
        url = normalize_url(raw, base)
        if not url:
            if raw and not raw.startswith(("data:", "#")):
                absolute = urljoin(base, raw)
                parsed = urlparse(absolute)
                if parsed.scheme in {"http", "https"} and parsed.hostname != HOST:
                    external.setdefault(absolute, set()).add(base)
                else:
                    excluded.add(absolute)
            return
        if url in scheduled:
            return
        scheduled.add(url)
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in PAGE_EXTENSIONS:
            queue.append(url)
        else:
            asset_urls.add(url)
            if not args.no_assets:
                if suffix in {".js", ".css", ".json"}:
                    asset_queue.appendleft(url)
                else:
                    asset_queue.append(url)

    def checkpoint(status: str) -> None:
        save_json(output / "index.json", {
            "schema_version": 2, "provider": "starside", "base_url": BASE_URL,
            "status": status, "updated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Public same-origin static URLs discovered from pages and scripts; no API or authenticated access.",
            "license": "Not established. Local archive only; original authors and upstream rights remain unchanged.",
            "crawler": {
                "user_agent": USER_AGENT, "delay_seconds": downloader.delay,
                "robots_status": downloader.robots_status, "urls_seen": len(seen),
                "pages_saved": len(pages), "assets_discovered": len(asset_urls),
                "assets_saved": len(assets), "pending": len(queue) + len(asset_queue),
            },
            "categories": categories, "assets": assets, "failures": failures,
            "external_links_file": "external-links.json", "public_exports": exports,
            "pending_urls": list(queue) + list(asset_queue), "excluded_urls": sorted(excluded),
        })

    status = "running"
    try:
        downloader.check_robots()
        schedule(BASE_URL + "index.html", BASE_URL)
        while queue or asset_queue:
            page = bool(queue)
            if (page and len(pages) >= args.max_pages) or (not page and len(assets) >= args.max_assets):
                status = "limit_reached"
                break
            url = (queue if page else asset_queue).popleft()
            seen.add(url)
            try:
                body, metadata = downloader.get(url, page)
                suffix = Path(urlparse(url).path).suffix.lower()
                if page:
                    parsed = PageParser()
                    parsed.feed(body.decode("utf-8"))
                    parsed.close()
                    record = parsed.result(url)
                    record["archive"] = metadata
                    record["links"] = sorted({u for raw in parsed.links if (u := normalize_url(raw, url))})
                    record["external_links"] = sorted({
                        urljoin(url, raw) for raw in parsed.links
                        if urlparse(urljoin(url, raw)).scheme in {"http", "https"}
                        and urlparse(urljoin(url, raw)).hostname != HOST
                    })
                    dependencies = set(parsed.assets)
                    dependencies.update(css_assets("\n".join(parsed.styles), url))
                    dependencies.update(script_assets("\n".join(parsed._scripts), url))
                    record["assets"] = sorted({u for raw in dependencies if (u := normalize_url(raw, url))})
                    for raw in sorted(parsed.links | dependencies):
                        schedule(raw, url)
                    record_path = Path("records") / relative_path(url)
                    text_path = Path("texts") / record["category"] / relative_path(url, ".md")
                    save_json(output / record_path, record)
                    heading = f'# {record["title"]}\n\nSource: {url}\n\nUpdated: {record["updated_at"]}\n\n'
                    save_bytes(output / text_path, (heading + (record["source_text"] or record["text"]) + "\n").encode("utf-8"))
                    pages.append(record)
                    categories.setdefault(record["category"], []).append({
                        "title": record["title"], "url": url, "updated_at": record["updated_at"],
                        "record": str(record_path), "text": str(text_path), "raw": metadata["path"],
                        "source_blocks": len(record["source_blocks"]), "tables": len(record["tables"]),
                    })
                    print(f"PAGE {len(pages):03d}: {url}", flush=True)
                else:
                    assets.append(metadata)
                    if suffix in {".js", ".css"}:
                        text = body.decode("utf-8")
                        dependencies = css_assets(text, url) if suffix == ".css" else script_assets(text, url)
                        for dependency in sorted(dependencies):
                            schedule(dependency, url)
                        variable = {"/assets/search.js": "starsideIndex", "/builds/desc.js": "starsideDesc"}.get(urlparse(url).path)
                        if variable:
                            payload = assigned_json(text, variable)
                            export_path = f"exports/{variable}.json"
                            save_json(output / export_path, payload)
                            exports.append(export_path)
                            if variable == "starsideIndex":
                                for entry in payload:
                                    schedule(entry.get("u", ""), BASE_URL)
                    if len(assets) % 50 == 0 or suffix in {".js", ".css", ".json"}:
                        print(f"ASSETS {len(assets)}/{len(asset_urls)}: {url}", flush=True)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                failures.append({"url": url, "kind": "page" if page else "asset", "error": f"{type(exc).__name__}: {exc}"})
                print(f"WARN {url}: {exc}", file=sys.stderr, flush=True)
            if len(seen) % 25 == 0:
                checkpoint("running")
        else:
            status = "complete_with_failures" if failures else "complete"
    except KeyboardInterrupt:
        status = "interrupted"
    finally:
        downloader.close()
        for entries in categories.values():
            entries.sort(key=lambda item: item["url"])
        save_json(output / "external-links.json", [
            {"url": url, "referenced_by": sorted(refs), "downloaded": False}
            for url, refs in sorted(external.items())
        ])
        for category, entries in categories.items():
            save_json(output / "categories" / f"{category}.json", entries)
        checkpoint(status)

    catalog = [
        "# Starside 本地分类目录", "", f"归档状态：`{status}`。", "",
        f"已保存 {len(pages)} 个页面、{len(assets)} 个静态资源；失败 {len(failures)} 个。", "",
        "仅包含公开链接可发现的同域静态内容；外部文档只保留链接，未访问后台或调用 API。",
        "原始 HTML/JS 仅用于归档，不是离线安全镜像；打开原始页可能执行原站脚本和发起网络请求。",
        "建议阅读下方分类文本，程序使用 records/ 中保留表格单元格与标记的 JSON。",
        "此归档不等于获得转载许可；未经确认不要随 MCP 发布或推送 GitHub。", "",
        "[机器索引](index.json) · [外部出处链接](external-links.json)", "",
    ]
    labels = {"builds": "配装", "weapons": "武器", "armor": "护甲", "subclass": "职业与神器", "activities": "活动与输出", "mechanics": "机制", "sources": "来源与站务", "other": "其他"}
    for category, entries in sorted(categories.items()):
        catalog.extend([f"## {labels.get(category, category)} ({len(entries)})", ""])
        for entry in entries:
            title = entry["title"].replace("[", "(").replace("]", ")")
            catalog.append(f'- [{title}]({entry["text"]}) · {entry["updated_at"] or "未标日期"}')
        catalog.append("")
    save_bytes(output / "CATALOG.md", ("\n".join(catalog) + "\n").encode("utf-8"))
    print(
        f"DONE status={status} pages={len(pages)} assets={len(assets)}/{len(asset_urls)} "
        f"failures={len(failures)} categories={dict(Counter(p['category'] for p in pages))} output={output}",
        flush=True,
    )
    return 0 if status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
