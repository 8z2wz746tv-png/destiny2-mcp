from __future__ import annotations

import os

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.build_import.extractor import ArticleExtractor, ScreenshotExtractor


def test_build_import_extractors_load_their_skill_prompts() -> None:
    screenshot_prompt = ScreenshotExtractor().get_skill_prompt()
    article_prompt = ArticleExtractor().get_skill_prompt()

    assert "Build Screenshot Skill" in screenshot_prompt
    assert "Build Article Analyst" in article_prompt
    assert '"class_name"' in screenshot_prompt
    assert '"class_name"' in article_prompt
