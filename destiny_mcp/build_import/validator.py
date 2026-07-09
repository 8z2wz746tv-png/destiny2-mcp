"""Validator — 校验 CanonicalBuild。

三态：
- FAILED: class_type 不存在，或所有字段均为空
- PARTIAL: class_type 存在，但有非空字段解析失败
- COMPLETE: 所有非空字段均成功解析为有效 hash

关键：Validator 只验证"解析是否成功"，不验证"信息是否完整"。
没提金装 ≠ 解析失败。

参见 ADR-007: Canonical Build Strategy。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..logging_config import get_logger
from .models import CanonicalBuild

logger = get_logger(__name__)


class ValidationResult(BaseModel):
    """校验结果。"""

    status: str = Field(description="failed / partial / complete")
    errors: list[str] = Field(default_factory=list, description="解析失败的字段描述")
    warnings: list[str] = Field(default_factory=list, description="非致命警告")


class Validator:
    """CanonicalBuild 校验器。"""

    def validate(
        self,
        build: CanonicalBuild,
        normalization_errors: list[str],
    ) -> ValidationResult:
        """校验 CanonicalBuild。

        Args:
            build: Normalizer 输出的 CanonicalBuild。
            normalization_errors: Normalizer 阶段产生的解析错误。

        Returns:
            ValidationResult with status = failed / partial / complete。
        """
        # FAILED: class_type 缺失
        if not build.class_type:
            return ValidationResult(
                status="failed",
                errors=["class_type 缺失（无法识别职业）"],
            )

        # FAILED: 所有字段均为空
        if build.is_empty:
            return ValidationResult(
                status="failed",
                errors=["所有字段均为空（未识别到任何有效内容）"],
            )

        # PARTIAL: 有解析失败的字段
        if normalization_errors:
            return ValidationResult(
                status="partial",
                errors=normalization_errors,
            )

        # COMPLETE
        return ValidationResult(status="complete")
