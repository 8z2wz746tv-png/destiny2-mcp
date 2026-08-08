"""DIM wish list data acquisition for personal installs."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

SOURCE_REPO = "48klocs/dim-wish-list-sources"
SOURCE_URL = f"https://api.github.com/repos/{SOURCE_REPO}/contents"
SOURCE_ZIP_URL = f"https://codeload.github.com/{SOURCE_REPO}/zip/refs/heads/master"
DEFAULT_VERSION = "2026-06-16"
DEFAULT_FILENAME = "dim_wishlists.json"
PACKAGE_WISHLIST_PATH = Path(__file__).resolve().parent / "data" / DEFAULT_FILENAME

CURATORS = [
    "PandaPaxxy",
    "Mercules904",
    "Legoleflash",
    "AyyItsChevy",
    "RNGeez",
    "SirStallion",
    "SpaceMonkey",
    "WarlockMaggie",
    "YeezyGT",
    "blueberries-dot-gg",
    "heckinn",
    "kj415j45",
    "misc",
]

WISH_RE = re.compile(r"dimwishlist:item=(\d+)&perks=([\d,]+)")
TAG_RE = re.compile(r"\|tags?:([^,\s]+(?:,[^,\s]+)*)", re.IGNORECASE)


def default_wishlist_path() -> Path:
    """Resolve where DIM wish list data should live for this install."""
    env_path = os.getenv("DESTINY_WISHLIST_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    if PACKAGE_WISHLIST_PATH.exists():
        return PACKAGE_WISHLIST_PATH

    data_root = os.getenv("DATA_PATH")
    if data_root:
        return (Path(data_root).expanduser().resolve() / DEFAULT_FILENAME)

    project_root = os.getenv("DESTINY_MCP_ROOT")
    if project_root:
        return Path(project_root).expanduser().resolve() / "data" / DEFAULT_FILENAME

    cwd = Path.cwd().resolve()
    if (cwd / "destiny_mcp").is_dir():
        return cwd / "data" / DEFAULT_FILENAME

    return Path.home() / ".destiny_mcp" / "data" / DEFAULT_FILENAME


def _api_get(url: str, *, timeout: int) -> list[dict[str, Any]] | dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "destiny-mcp"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _fetch_file_content(download_url: str, *, timeout: int) -> str:
    req = urllib.request.Request(download_url, headers={"User-Agent": "destiny-mcp"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_bytes(url: str, *, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "destiny-mcp"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_tags_from_notes(line: str) -> list[str]:
    match = TAG_RE.search(line)
    if match:
        return [tag.strip().lower() for tag in match.group(1).split(",") if tag.strip()]

    lower = line.lower()
    tags = []
    if "pve" in lower:
        tags.append("pve")
    if "pvp" in lower:
        tags.append("pvp")
    return tags


def parse_wish_list(text: str, source: str) -> dict[int, list[dict[str, Any]]]:
    """Parse DIM wish list text into raw roll records."""
    result: dict[int, list[dict[str, Any]]] = {}
    current_tags: list[str] = []
    current_source = source

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("//notes:") or line.startswith("// notes:"):
            current_tags = _parse_tags_from_notes(line)
            source_match = re.match(r"//\s*notes:\s*(\w+)", line)
            if source_match:
                current_source = source_match.group(1)
            continue

        if line.startswith("//") or line.startswith("title:") or line.startswith("description:"):
            continue

        match = WISH_RE.search(line)
        if match:
            item_hash = int(match.group(1))
            perk_hashes = [int(perk) for perk in match.group(2).split(",") if perk]
            result.setdefault(item_hash, []).append(
                {
                    "perks": perk_hashes,
                    "tags": current_tags.copy(),
                    "source": current_source,
                }
            )

    return result


def fetch_from_repo_zip(*, timeout: int = 30) -> dict[int, list[dict[str, Any]]]:
    """Fetch the upstream repository zip and parse wish list text files locally."""
    archive = _fetch_bytes(SOURCE_ZIP_URL, timeout=timeout)
    all_rolls: dict[int, list[dict[str, Any]]] = {}

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if len(parts) < 3:
                continue
            curator = parts[1]
            name = parts[-1]
            if curator not in CURATORS or not name.endswith(".txt"):
                continue

            content = zf.read(info).decode("utf-8", errors="replace")
            parsed = parse_wish_list(content, f"{curator}/{name}")
            for item_hash, rolls in parsed.items():
                all_rolls.setdefault(item_hash, []).extend(rolls)

    if not all_rolls:
        raise RuntimeError("No DIM wish list rolls were found in repository zip")
    return all_rolls


def fetch_from_github_api(*, timeout: int = 20, max_list_failures: int = 3) -> dict[int, list[dict[str, Any]]]:
    """Fetch and parse DIM wish list sources from the GitHub Contents API."""
    all_rolls: dict[int, list[dict[str, Any]]] = {}
    list_failures = 0

    for curator in CURATORS:
        try:
            items = _api_get(f"{SOURCE_URL}/{curator}", timeout=timeout)
            list_failures = 0
        except Exception as exc:
            list_failures += 1
            logger.warning("Failed to list DIM wish list curator %s: %s", curator, exc)
            if list_failures >= max_list_failures:
                raise RuntimeError("GitHub DIM wish list source is not reachable") from exc
            continue

        if not isinstance(items, list):
            logger.warning("Unexpected DIM wish list response for %s", curator)
            continue

        txt_files = [item for item in items if str(item.get("name", "")).endswith(".txt")]
        for item in txt_files:
            name = str(item.get("name", ""))
            download_url = str(item.get("download_url", ""))
            try:
                content_b64 = item.get("content")
                if isinstance(content_b64, str):
                    content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                elif download_url:
                    content = _fetch_file_content(download_url, timeout=timeout)
                else:
                    logger.warning("Skipping DIM wish list file without download URL: %s/%s", curator, name)
                    continue
            except Exception as exc:
                logger.warning("Failed to fetch DIM wish list file %s/%s: %s", curator, name, exc)
                continue

            parsed = parse_wish_list(content, f"{curator}/{name}")
            for item_hash, rolls in parsed.items():
                all_rolls.setdefault(item_hash, []).extend(rolls)

    if not all_rolls:
        raise RuntimeError("No DIM wish list rolls were fetched")
    return all_rolls


def fetch_all_wish_lists(*, timeout: int = 30) -> dict[int, list[dict[str, Any]]]:
    """Fetch DIM wish lists, preferring a repo zip to avoid GitHub API rate limits."""
    try:
        return fetch_from_repo_zip(timeout=timeout)
    except Exception as exc:
        logger.warning("DIM wish list zip fetch failed, falling back to GitHub API: %s", exc)
        return fetch_from_github_api(timeout=timeout)


def deduplicate_rolls(all_rolls: dict[int, list[dict[str, Any]]]) -> dict[int, list[dict[str, Any]]]:
    """Deduplicate rolls by item hash and perk set while preserving sources."""
    deduped: dict[int, list[dict[str, Any]]] = {}

    for item_hash, rolls in all_rolls.items():
        seen: dict[frozenset[int], dict[str, Any]] = {}
        for roll in rolls:
            perk_key = frozenset(roll["perks"])
            if perk_key in seen:
                existing = seen[perk_key]
                for tag in roll["tags"]:
                    if tag not in existing["tags"]:
                        existing["tags"].append(tag)
                if roll["source"] not in existing["source"]:
                    existing["source"] += f", {roll['source']}"
            else:
                seen[perk_key] = {
                    "perks": list(roll["perks"]),
                    "tags": list(roll["tags"]),
                    "source": str(roll["source"]),
                }
        deduped[item_hash] = list(seen.values())

    return deduped


def build_wishlist_payload(all_rolls: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    """Convert raw rolls into the compact JSON format used by WishListService."""
    deduped = deduplicate_rolls(all_rolls)
    compact_rolls = {}

    for item_hash, rolls in deduped.items():
        compact_rolls[str(item_hash)] = [
            {
                "p": ",".join(str(hash_value) for hash_value in roll["perks"]),
                "t": roll["tags"],
                "s": roll["source"],
            }
            for roll in rolls
        ]

    return {
        "version": DEFAULT_VERSION,
        "source": SOURCE_REPO,
        "weapon_count": len(deduped),
        "rolls": compact_rolls,
    }


def write_wishlist_payload(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write DIM wish list JSON to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def fetch_and_write_wishlist(path: Path | None = None, *, timeout: int = 30) -> Path:
    """Fetch DIM wish list data and write it to path."""
    output = path or default_wishlist_path()
    raw = fetch_all_wish_lists(timeout=timeout)
    payload = build_wishlist_payload(raw)
    write_wishlist_payload(output, payload)
    logger.info(
        "DIM wish list data written to %s (%d weapons)",
        output,
        payload["weapon_count"],
    )
    return output


def ensure_wishlist_data(path: Path | None = None, *, force: bool = False, timeout: int = 30) -> Path | None:
    """Return usable DIM wish list data, downloading it when missing."""
    output = path or default_wishlist_path()
    if output.exists() and output.stat().st_size > 0 and not force:
        return output

    try:
        return fetch_and_write_wishlist(output, timeout=timeout)
    except Exception as exc:
        logger.warning("DIM wish list data unavailable; god roll annotations will be limited: %s", exc)
        return output if output.exists() else None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch DIM wish list data for Destiny MCP")
    parser.add_argument("--output", help="Output JSON path. Defaults to DESTINY_WISHLIST_PATH or local data dir.")
    parser.add_argument("--force", action="store_true", help="Re-download even if the file already exists.")
    parser.add_argument("--timeout", type=int, default=30, help="Network timeout per request, default 30 seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    output = Path(args.output).expanduser().resolve() if args.output else default_wishlist_path()
    path = ensure_wishlist_data(output, force=args.force, timeout=args.timeout)
    if not path:
        raise SystemExit("DIM wish list 下载失败；MCP 仍可运行，但 god roll 标注会缺失。")
    print(f"DIM wish list ready: {path}")


if __name__ == "__main__":
    main()
