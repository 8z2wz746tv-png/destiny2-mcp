"""Build Import Service — Build Import Agent 唯一入口。

编排 Extractor → Normalizer → Validator 的完整流程。

参见 ADR-007: Canonical Build Strategy。
"""

from __future__ import annotations

from ..build_import.exceptions import ExtractionError
from ..build_import.extractor import ArticleExtractor, ScreenshotExtractor
from ..build_import.models import BuildDraft, CanonicalBuild
from ..build_import.normalizer import Normalizer
from ..build_import.validator import ValidationResult, Validator
from ..logging_config import get_logger
from ..manifest import ManifestManager

logger = get_logger(__name__)


class BuildImportService:
    """Build Import Agent 唯一入口。"""

    def __init__(self, manifest: ManifestManager) -> None:
        self._screenshot_extractor = ScreenshotExtractor()
        self._article_extractor = ArticleExtractor()
        self._normalizer = Normalizer(manifest)
        self._validator = Validator()

    async def import_from_image(self, image_base64: str) -> dict:
        """从截图导入配装。

        Args:
            image_base64: PNG/JPG 截图的 base64 编码。

        Returns:
            {
                "build": CanonicalBuild,
                "validation": ValidationResult,
                "draft": BuildDraft,
                "skill_prompt": str,
            }

        Raises:
            ExtractionError: 截图提取失败。
        """
        logger.info("import_from_image: starting")

        # Step 1: Extract (Phase 1: return empty draft + skill prompt)
        try:
            draft = await self._screenshot_extractor.extract(image_base64)
        except ExtractionError:
            logger.error("import_from_image: extraction failed", exc_info=True)
            raise

        # Step 2: Normalize
        canonical, errors = self._normalizer.normalize(draft)
        logger.info("import_from_image: normalized, %d errors", len(errors))

        # Step 3: Validate
        validation = self._validator.validate(canonical, errors)
        logger.info("import_from_image: validation status=%s", validation.status)

        return {
            "build": canonical,
            "validation": validation,
            "draft": draft,
            "skill_prompt": self._screenshot_extractor.get_skill_prompt(),
        }

    async def import_from_url(self, url: str) -> dict:
        """从文章 URL 导入配装。

        流程：抓取网页 → 提取文本 → 返回文本 + skill_prompt。
        LLM 解析文本后调用 import_from_article_response()。

        Args:
            url: 文章 URL。

        Returns:
            {
                "article_text": str,
                "skill_prompt": str,
            }

        Raises:
            FetchError: URL 抓取失败。
            ExtractionError: 提取的文本为空。
        """
        logger.info("import_from_url: fetching %s", url)

        article_text = await self._article_extractor.fetch_article(url)
        skill_prompt = self._article_extractor.get_skill_prompt()

        return {
            "article_text": article_text,
            "skill_prompt": skill_prompt,
        }

    def import_from_article_response(self, llm_response: str) -> dict:
        """解析 LLM 的文章分析结果为 CanonicalBuild。

        Args:
            llm_response: LLM 返回的 JSON 字符串。

        Returns:
            {
                "build": CanonicalBuild,
                "validation": ValidationResult,
                "draft": BuildDraft,
            }

        Raises:
            ExtractionError: JSON 解析失败。
        """
        logger.info("import_from_article_response: parsing LLM response")

        # Step 1: Parse
        draft = self._article_extractor.parse_llm_response(llm_response)

        # Step 2: Normalize
        canonical, errors = self._normalizer.normalize(draft)
        logger.info("import_from_article_response: normalized, %d errors", len(errors))

        # Step 3: Validate
        validation = self._validator.validate(canonical, errors)
        logger.info("import_from_article_response: validation status=%s", validation.status)

        return {
            "build": canonical,
            "validation": validation,
            "draft": draft,
        }

    def import_from_draft(self, draft: BuildDraft) -> dict:
        """从 BuildDraft 导入配装（跳过 Extractor，用于测试或手动输入）。

        Args:
            draft: BuildDraft 结构。

        Returns:
            {
                "build": CanonicalBuild,
                "validation": ValidationResult,
            }
        """
        logger.info("import_from_draft: starting")

        # Step 1: Normalize
        canonical, errors = self._normalizer.normalize(draft)
        logger.info("import_from_draft: normalized, %d errors", len(errors))

        # Step 2: Validate
        validation = self._validator.validate(canonical, errors)
        logger.info("import_from_draft: validation status=%s", validation.status)

        return {
            "build": canonical,
            "validation": validation,
        }
