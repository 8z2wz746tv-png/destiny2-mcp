#!/usr/bin/env python3
"""Read-only integration smoke test through a real MCP stdio session."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result) -> dict:
    data = result.structuredContent
    if not isinstance(data, dict):
        data = next(
            (
                json.loads(block.text)
                for block in result.content
                if block.type == "text"
            ),
            {},
        )
    if result.isError or data.get("ok") is not True:
        raise RuntimeError(
            "MCP read failed: "
            + str((data.get("error") or {}).get("code", "protocol_error"))
        )
    return data["data"]


async def verify(root: Path, *, inventory: bool, timeout: float) -> None:
    params = StdioServerParameters(
        command=str(root / ".venv/bin/destiny-mcp"),
        env={"DESTINY_MCP_ROOT": str(root), "DESTINY_MCP_TOOL_PROFILE": "normal"},
    )
    async with asyncio.timeout(timeout):
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as server_log:
            async with stdio_client(params, errlog=server_log) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if len(tools.tools) != 8:
                        raise RuntimeError("Normal tool count changed")
                    print("MCP_TOOL_COUNT=8", flush=True)
                    query = payload(
                        await session.call_tool(
                            "weapon_assistant",
                            {
                                "intent": "community",
                                "perk_name": "辉耀炽热",
                                "limit": 1,
                            },
                        )
                    )
                    if not query.get("archive_available") or not query.get("results"):
                        raise RuntimeError(
                            "Local Starside data is unavailable or lacks the smoke-test Perk"
                        )
                    if query.get("author_document_count", 0) < 22:
                        raise RuntimeError("Bundled author documents are incomplete")
                    entry = query["results"][0]
                    detail = payload(
                        await session.call_tool(
                            "weapon_assistant",
                            {
                                "intent": "community",
                                "knowledge_id": entry["knowledge_id"],
                            },
                        )
                    )
                    if not detail.get("text") or not detail.get("source", {}).get(
                        "snapshot_id"
                    ):
                        raise RuntimeError("Knowledge detail/provenance missing")
                    print("STARSIDE_KNOWLEDGE_CHECK=ok", flush=True)
                    print(
                        f"STARSIDE_AUTHOR_DOCUMENT_COUNT={query['author_document_count']}",
                        flush=True,
                    )
                    for tool, arguments, expected in (
                        ("weapon_assistant", {"perk_name": "傍晚 SI4"}, "傍晚 SI4"),
                        ("subclass_assistant", {"query": "冰霜护甲"}, "冰霜护甲"),
                        ("activity_assistant", {"query": "被腐化的卡丽"}, "被腐化的卡丽"),
                        (
                            "world_assistant",
                            {"query": "圣贤保护者", "community_category": "armor"},
                            "圣贤保护者",
                        ),
                    ):
                        response = payload(
                            await session.call_tool(
                                tool, {"intent": "community", **arguments}
                            )
                        )
                        if not response.get("results"):
                            raise RuntimeError(
                                f"Community route returned no records: {tool}"
                            )
                        if expected not in json.dumps(response["results"], ensure_ascii=False):
                            raise RuntimeError(
                                f"Community route did not return expected content: {tool}"
                            )
                    print("STARSIDE_DOMAIN_ROUTES=ok", flush=True)
                    if query.get("build_count", 0) == 0:
                        print("STARSIDE_BUILD_ARCHIVE=not_installed", flush=True)
                        print("STARSIDE_VERIFY_OK=read-only checks passed")
                        return
                    offset, builds, snapshot_id = 0, {}, query["snapshot_id"]
                    while True:
                        page = payload(
                            await session.call_tool(
                                "build_assistant",
                                {
                                    "intent": "community",
                                    "include_inventory": False,
                                    "top_n": 20,
                                    "offset": offset,
                                },
                            )
                        )
                        if page["snapshot_id"] != snapshot_id:
                            raise RuntimeError("Archive changed during verification")
                        for build in page["results"]:
                            builds[build["build_id"]] = build
                        if page["next_offset"] is None:
                            if len(builds) != page["matched_count"] or not builds:
                                raise RuntimeError("Build pagination incomplete")
                            break
                        offset = page["next_offset"]
                    print(f"STARSIDE_BUILD_COUNT={len(builds)}", flush=True)
                    selected = payload(
                        await session.call_tool(
                            "build_assistant",
                            {
                                "intent": "community",
                                "community_build_id": next(iter(builds)),
                                "include_inventory": inventory,
                            },
                        )
                    )["selected_build"]
                    if (
                        not selected.get("raw_text")
                        or selected["validation"]["execution_supported"]
                    ):
                        raise RuntimeError(
                            "Community template execution boundary failed"
                        )
                    print("STARSIDE_BUILD_DETAIL=ok", flush=True)
                    if inventory:
                        match = selected["inventory_match"]
                        if match.get("inventory_status") != "complete" or match.get(
                            "read_errors"
                        ):
                            raise RuntimeError(
                                "Account match data unavailable or partially read"
                            )
                        if match["execution_eligible"]:
                            raise RuntimeError(
                                "Unexpected executable community template"
                            )
                        print("STARSIDE_ACCOUNT_READ=ok", flush=True)
                        print(
                            f"KNOWN_MISSING_REQUIREMENTS={match['known_missing_count']}",
                            flush=True,
                        )
                        print(
                            f"UNKNOWN_OR_UNCHECKED_REQUIREMENTS={match['unknown_or_unchecked_count']}",
                            flush=True,
                        )
    print("STARSIDE_VERIFY_OK=read-only checks passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--inventory",
        action="store_true",
        help="Also read account inventory; never performs game writes.",
    )
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        asyncio.run(
            verify(args.root.resolve(), inventory=args.inventory, timeout=args.timeout)
        )
    except Exception as exc:
        # Do not relay server stderr, API responses or credential-bearing exceptions.
        print(f"STARSIDE_VERIFY_FAILED={type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
