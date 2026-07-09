"""Build Import Agent 自定义异常。"""

from __future__ import annotations

from ..exceptions import DestinyMCPError


class BuildImportError(DestinyMCPError):
    """Build Import 基础异常。"""


class ExtractionError(BuildImportError):
    """截图/文章/视频提取失败。"""

    def __init__(self, source_type: str, detail: str = "") -> None:
        self.source_type = source_type
        super().__init__(
            f"提取失败（{source_type}）。"
            + (f" {detail}" if detail else "")
        )


class FetchError(BuildImportError):
    """URL 抓取失败。"""

    def __init__(self, url: str, detail: str = "") -> None:
        self.url = url
        super().__init__(
            f"抓取失败：{url}"
            + (f" {detail}" if detail else "")
        )


class NormalizationError(BuildImportError):
    """名称→Hash 转换失败。"""

    def __init__(self, field_name: str, raw_value: str, detail: str = "") -> None:
        self.field_name = field_name
        self.raw_value = raw_value
        super().__init__(
            f"无法识别 '{raw_value}'（字段: {field_name}）。"
            + (f" {detail}" if detail else "")
        )


class ValidationError(BuildImportError):
    """CanonicalBuild 校验失败（class_type 缺失或所有字段为空）。"""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"配装校验失败。"
            + (f" {detail}" if detail else "")
        )
