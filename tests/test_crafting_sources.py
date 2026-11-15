"""Starside「锻造武器来源」索引：替身测试，不碰真机。

盯住的是三条：

1. **归一化是唯一的判据入口**：清单写 `IKELOS_SR`、武器名是 `IKELOS_SR_v1.0.3`，清单写
   `伊尔约特之歌`、武器名带间隔号 —— 归一化没写好，这些就"查不到来源"（实测 183 条里
   有 13 条因此从"没命中"变成"命中"）；
2. **表格优先、正文兜底**，且正文要词边界：`ikelossm` 不能命中正文里的 `ikelossmg`；
3. **没命中只能说这份清单没收录**，话术与 `unmatched` 都必须留着这个区分 ——
   不能把"本地没这行"说成"这把武器没有来源"。

真机覆盖率（170 表格 + 2 正文 / 183）见 docs/plans/PATTERN_QUERY_PLAN.md。
"""

from __future__ import annotations

from destiny_mcp.services.starside_crafting_sources import (
    MAX_QUERY_NAMES,
    PAGE_ID,
    CraftingSources,
    note_key,
)


class FakeStarside:
    """只实现 `from_service` 用到的 get_knowledge。"""

    def __init__(
        self,
        *,
        table_pages: list[list[dict]] | None = None,
        text_chunks: list[str] | None = None,
        source: dict | None = None,
    ) -> None:
        self._table_pages = table_pages or [[]]
        self._text_chunks = text_chunks or [""]
        self._source = source if source is not None else {
            "title": "锻造武器来源 · Starside",
            "url": "https://starside.work/crafting/index.html",
            "updated_at": "2026.8.30",
            "trust": "untrusted_reference",
            "snapshot_id": "abc",
            "record_sha256": "should-be-dropped",
        }
        self.calls: list[tuple[str, int]] = []

    def get_knowledge(self, knowledge_id: str, *, section: str = "text", offset: int = 0, limit: int = 10) -> dict:
        assert knowledge_id == PAGE_ID
        self.calls.append((section, offset))
        if section == "tables":
            index = offset // 40
            page = self._table_pages[index] if index < len(self._table_pages) else []
            has_more = index + 1 < len(self._table_pages)
            return {"rows": page, "next_offset": offset + 40 if has_more else None, "source": self._source}
        index = offset // 6000
        chunk = self._text_chunks[index] if index < len(self._text_chunks) else ""
        has_more = index + 1 < len(self._text_chunks)
        return {"text": chunk, "next_offset": offset + 6000 if has_more else None, "source": self._source}


def row(heading: str, label: str, names: str) -> dict:
    return {"heading": heading, "cells": [{"text": label}, {"text": names}]}


def test_note_key_folds_the_ways_the_source_page_writes_names() -> None:
    assert note_key("IKELOS_SR_v1.0.3") == note_key("IKELOS_SR")
    assert note_key("伊尔·约特之歌") == note_key("伊尔约特之歌")
    assert note_key("太攀蛇-4FR") == note_key("太攀蛇 4fr")
    assert note_key("BxR-55 战士") == note_key("BxR-55战士")
    assert note_key("") == ""


def test_table_rows_are_indexed_with_their_heading_and_label() -> None:
    sources = CraftingSources(
        [row("突袭", "克洛塔的末日", "克洛塔之语、深渊叛逆"), row("异域任务", "行动时刻", "全面爆发")]
    )

    result = sources.lookup(["克洛塔之语", "全面爆发", "没这把枪"])

    assert [item["source"] for item in result["results"]] == ["突袭｜克洛塔的末日", "异域任务｜行动时刻"]
    assert {item["kind"] for item in result["results"]} == {"table"}
    assert result["unmatched"] == ["没这把枪"]
    assert result["matched_count"] == 2


def test_multi_line_cells_and_repeated_labels_are_handled() -> None:
    sources = CraftingSources([
        {"heading": "仄出售", "cells": [{"text": "侠盗赛季"}, {"text": "无赦、血仇\n褪色毅力"}]},
        {"heading": "仄出售", "cells": [{"text": "侠盗赛季"}, {"text": "无赦"}]},
    ])

    result = sources.lookup(["血仇", "褪色毅力", "无赦"])

    assert {item["name"]: item["source"] for item in result["results"]} == {
        "血仇": "仄出售｜侠盗赛季",
        "褪色毅力": "仄出售｜侠盗赛季",
        "无赦": "仄出售｜侠盗赛季",
    }


def test_table_hit_wins_over_prose() -> None:
    sources = CraftingSources([row("突袭", "玻璃拱顶", "惩戒措施")], "突袭\n惩戒措施掉自玻璃拱顶。")

    result = sources.lookup(["惩戒措施"])

    assert result["results"][0] == {"name": "惩戒措施", "source": "突袭｜玻璃拱顶", "kind": "table"}


def test_prose_is_used_when_the_tables_miss_and_shows_the_sentence() -> None:
    sources = CraftingSources(
        [row("地牢", "二象性", "另一把")],
        "地牢\n二象性另会掉落宿命（尾王）与享乐主义（尾王、第一位首领），掉率较低。",
    )

    result = sources.lookup(["宿命"])
    item = result["results"][0]

    assert item["kind"] == "prose"
    assert item["source"].startswith("正文｜二象性另会掉落宿命")
    assert len(item["source"]) <= 3 + 80


def test_prose_match_requires_a_word_boundary() -> None:
    """`ikelossm` 不该命中正文里的 `ikelossmg` —— 前缀误配会把来源挂到别的枪上。"""
    sources = CraftingSources([], "异域任务\n炽天使之盾：IKELOS_SR、IKELOS_SMG")

    assert sources.lookup(["IKELOS_SMG_v1.0.3"])["matched_count"] == 1
    assert sources.lookup(["IKELOS_SM_v1.0.3"])["unmatched"] == ["IKELOS_SM_v1.0.3"]


def test_markdown_table_lines_are_not_used_as_prose() -> None:
    """页面正文里也含表格原文；正文兜底只看非表格行，否则归属会变成一整行管道符。"""
    sources = CraftingSources([], "| 分组 | 武器 |\n| --- | --- |\n| 突袭 | 惩戒措施 |")

    assert sources.lookup(["惩戒措施"])["matched_count"] == 0


def test_empty_page_is_unavailable_and_says_so() -> None:
    sources = CraftingSources([], "")

    result = sources.lookup(["惩戒措施"])

    assert result["available"] is False
    assert result["results"] == []
    assert any("不能据此判断某把武器没有来源" in warning for warning in result["warnings"])


def test_lookup_reports_truncation_instead_of_silently_dropping_names() -> None:
    sources = CraftingSources([row("突袭", "玻璃拱顶", "惩戒措施")])

    result = sources.lookup([f"枪{i}" for i in range(MAX_QUERY_NAMES + 5)], limit=MAX_QUERY_NAMES)

    assert result["truncated"] is True


def test_page_reference_keeps_only_callable_fields() -> None:
    sources = CraftingSources([], "", page={"title": "锻造武器来源 · Starside", "updated_at": "2026.8.30",
                                            "trust": "untrusted_reference", "record_sha256": "x"})

    assert "record_sha256" not in sources.lookup(["x"])["page"]


def test_from_service_reads_every_table_page_and_text_chunk() -> None:
    first = [row("突袭", f"首领{i}", f"枪{i}") for i in range(40)]
    second = [row("突袭", "最后一个首领", "终局之枪")]
    fake = FakeStarside(table_pages=[first, second], text_chunks=["文" * 6000, "正文第二段"])

    sources = CraftingSources.from_service(fake)
    result = sources.lookup(["枪0", "终局之枪"])

    assert result["matched_count"] == 2
    assert ("tables", 0) in fake.calls and ("tables", 40) in fake.calls
    assert ("text", 0) in fake.calls and ("text", 6000) in fake.calls
    assert result["page"]["url"] == "https://starside.work/crafting/index.html"


def test_from_service_tolerates_a_page_without_rows() -> None:
    sources = CraftingSources.from_service(FakeStarside())

    assert sources.lookup(["惩戒措施"])["available"] is False
