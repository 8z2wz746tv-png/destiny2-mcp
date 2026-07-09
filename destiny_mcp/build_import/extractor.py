"""Extractors — 从外部来源提取 BuildDraft。

Phase 1: ScreenshotExtractor — Claude Vision 截图提取。
Phase 2: ArticleExtractor — URL → 正文提取 → LLM 解析 → BuildDraft。
Phase 3: VideoExtractor — 视频 → 字幕 + 关键帧 → BuildDraft。

LLM 只输出 Name，禁止输出 Hash。参见 ADR-007。
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx
from pydantic import ValidationError

from ..logging_config import get_logger
from ..utils.web_fetch import fetch_url
from .exceptions import ExtractionError, FetchError
from .models import BuildDraft

logger = get_logger(__name__)

# Skill 文件路径
_SKILL_DIR = Path(__file__).parent.parent.parent.parent / "skills"


def _load_skill(name: str) -> str:
    """加载 skill 文件。"""
    path = _SKILL_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning("Skill file not found: %s", path)
    return ""


class ScreenshotExtractor:
    """从截图提取 BuildDraft。Phase 1: Claude Vision。"""

    def __init__(self) -> None:
        self._skill_prompt = _load_skill("build_screenshot_skill.md")

    async def extract(self, image_base64: str) -> BuildDraft:
        """从截图提取 BuildDraft。

        Phase 1: 只做输入验证，返回空 BuildDraft。
        实际的 VLM 调用由 MCP 工具层完成，使用 get_skill_prompt() 获取提示词。

        Args:
            image_base64: PNG/JPG 截图的 base64 编码。

        Returns:
            BuildDraft（全 Name）。Phase 1 返回空 BuildDraft。

        Raises:
            ExtractionError: 图片数据为空。
        """
        if not image_base64:
            raise ExtractionError("screenshot", "图片数据为空")

        logger.info("ScreenshotExtractor: ready (skill prompt loaded: %s)", bool(self._skill_prompt))
        return BuildDraft()

    def get_skill_prompt(self) -> str:
        """返回 Skill 提示词，供 MCP 工具层使用。"""
        return self._skill_prompt

    def parse_vlm_response(self, response_text: str) -> BuildDraft:
        """解析 VLM 响应为 BuildDraft。

        Args:
            response_text: VLM 返回的 JSON 字符串。

        Returns:
            BuildDraft。

        Raises:
            ExtractionError: 解析失败。
        """
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise ExtractionError("screenshot", f"VLM 返回的不是有效 JSON: {e}")

        try:
            return BuildDraft.model_validate(data)
        except ValidationError as e:
            raise ExtractionError("screenshot", f"BuildDraft 解析失败: {e}")


# ── Article Extractor ────────────────────────────────────────────────

# 截断上限：避免超出 LLM 上下文
_MAX_TEXT_LENGTH = 8000


class _HTMLTextExtractor(HTMLParser):
    """从 HTML 提取正文文本（去 script/style/nav 等标签）。"""

    _SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _extract_text_from_html(html: str) -> str:
    """从 HTML 提取纯文本。"""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    text = parser.get_text()
    # 合并连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ArticleExtractor:
    """从文章 URL 提取 BuildDraft。Phase 2。"""

    def __init__(self) -> None:
        self._skill_prompt = _load_skill("build_article_skill.md")

    async def fetch_article(self, url: str) -> str:
        """抓取 URL，清洗 HTML，返回纯文本。

        Args:
            url: 文章 URL。

        Returns:
            提取的纯文本（截断到 _MAX_TEXT_LENGTH）。

        Raises:
            FetchError: 抓取失败。
            ExtractionError: 提取的文本为空。
        """
        logger.info("ArticleExtractor: fetching %s", url)
        try:
            resp = await fetch_url(url)
        except httpx.HTTPStatusError as e:
            raise FetchError(url, f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            raise FetchError(url, str(e))

        html = resp.text
        text = _extract_text_from_html(html)

        if not text:
            raise ExtractionError("article", f"从 URL 提取的文本为空: {url}")

        # 截断
        if len(text) > _MAX_TEXT_LENGTH:
            text = text[:_MAX_TEXT_LENGTH] + "\n\n[文本已截断...]"

        logger.info("ArticleExtractor: extracted %d chars from %s", len(text), url)
        return text

    def get_skill_prompt(self) -> str:
        """返回文章解析 Skill 提示词。"""
        return self._skill_prompt

    def parse_llm_response(self, response_text: str) -> BuildDraft:
        """解析 LLM 响应为 BuildDraft。

        Args:
            response_text: LLM 返回的 JSON 字符串（可能带 markdown 包裹）。

        Returns:
            BuildDraft。

        Raises:
            ExtractionError: 解析失败。
        """
        text = response_text.strip()
        # 容错：去掉 markdown 代码块包裹
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionError("article", f"LLM 返回的不是有效 JSON: {e}")

        try:
            return BuildDraft.model_validate(data)
        except ValidationError as e:
            raise ExtractionError("article", f"BuildDraft 解析失败: {e}")
