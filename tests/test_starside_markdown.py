"""Contracts for the author-provided, directly usable Markdown bundle."""

from pathlib import Path

from destiny_mcp.services.starside_markdown import (
    _split_table_row,
    parse_markdown_document,
    semantic_markdown,
)
from destiny_mcp.services.starside_service import StarsideService


SHARE_ROOT = Path(__file__).resolve().parents[1] / "share"


def test_custom_markers_keep_qualifiers_and_remove_display_only_markup() -> None:
    text = semantic_markdown(
        "{perk|辉耀炽热} {pvp|[10%]} {enh|↑15%} "
        "{unsure|?} {note|说明} {ico|![](icons/missing.webp)}"
    )

    assert text == (
        "辉耀炽热 [pvp][10%][/pvp] [enh]↑15%[/enh] "
        "[unsure]?[/unsure] [note]说明[/note]"
    )


def test_table_split_does_not_treat_custom_token_pipe_as_column() -> None:
    row = "| 武器 | {perk|治疗弹匣}\\\\{perk|即兴弹药} | {src|先锋} |"

    assert _split_table_row(row) == [
        "武器",
        "{perk|治疗弹匣}\\\\{perk|即兴弹药}",
        "{src|先锋}",
    ]


def test_real_share_bundle_parses_all_documents_and_metadata() -> None:
    service = StarsideService(None, archive_root=SHARE_ROOT / "not-an-archive", share_root=SHARE_ROOT)
    result = service.search_knowledge("辉耀炽热", category="weapons", limit=20)

    assert result["archive_available"] is True
    assert result["retrieval_mode"] == "author_markdown"
    assert result["author_document_count"] == 22
    assert result["page_count"] == 22
    assert result["build_count"] == 0
    assert result["matched_count"] > 1
    perk = next(
        item
        for item in result["results"]
        if item["kind"] == "table_row" and item["title"] == "辉耀炽热"
    )
    assert perk["source"]["local_path"] == "share/weapon-perks.md"
    assert perk["source"]["updated_at"] == "2026.8.30"
    assert perk["source"]["source_type"] == "author_markdown"
    assert perk["source"]["game_version_verified"] is False
    assert perk["source"]["redistribution_license"] == "used_with_author_permission"
    assert "[enh]" in perk["snippet"]


def test_real_weapon_row_keeps_columns_and_multiple_perks() -> None:
    service = StarsideService(None, archive_root=SHARE_ROOT / "missing", share_root=SHARE_ROOT)
    result = service.search_knowledge("傍晚 SI4", category="weapons", limit=10)
    row = next(item for item in result["results"] if item["kind"] == "table_row")

    assert "Perk 三号位：治疗弹匣\n即兴弹药" in row["snippet"]
    assert "Perk 四号位：辉耀炽热\n邪剑守则\n燃烧野心" in row["snippet"]
    assert "获取地点：先锋" in row["snippet"]


def test_real_nested_armor_section_and_activity_table_are_queryable() -> None:
    service = StarsideService(None, archive_root=SHARE_ROOT / "missing", share_root=SHARE_ROOT)
    armor = service.search_knowledge("圣贤保护者", category="armor", limit=10)
    section = next(item for item in armor["results"] if item["kind"] == "section")
    detail = service.get_knowledge(section["knowledge_id"])
    assert "战斗冥想" in detail["text"]
    assert "利刃专注" in detail["text"]
    assert detail["source"]["line_start"] == 1452

    activity = service.search_knowledge("被腐化的卡丽", category="activities", limit=10)
    boss = next(item for item in activity["results"] if item["kind"] == "table_row")
    assert boss["title"] == "被腐化的卡丽"
    assert "生命值：299440" in boss["snippet"]
    links = service.get_knowledge(boss["page_id"], section="links")
    assert links["links"][0]["url"].startswith("https://docs.google.com/spreadsheets/")


def test_parser_exposes_complex_table_without_missing_icons(tmp_path: Path) -> None:
    path = tmp_path / "weapon-frames.md"
    path.write_text(
        "# 武器框架\n\n更新：2026.9.10\n\n## 数据\n\n"
        "| 武器 | 图标 | 数值 |\n|---|---|---|\n"
        "| 手炮 | {ico|![](icons/missing.webp)} | {unsure|?} |\n",
        encoding="utf-8",
    )

    parsed = parse_markdown_document(path, tmp_path).record
    assert parsed["tables"][0]["rows"][1][0]["text"] == "手炮"
    assert parsed["tables"][0]["rows"][1][1]["text"] == ""
    assert parsed["tables"][0]["rows"][1][2]["text"] == "[unsure]?[/unsure]"


def test_bundled_markdown_survives_a_broken_optional_archive(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "index.json").write_text("{}", encoding="utf-8")

    result = StarsideService(
        None, archive_root=archive, share_root=SHARE_ROOT
    ).search_knowledge("辉耀炽热", category="weapons", limit=1)

    assert result["retrieval_mode"] == "author_markdown"
    assert result["results"][0]["source"]["source_type"] == "author_markdown"
    assert any("网页归档损坏" in warning for warning in result["warnings"])
