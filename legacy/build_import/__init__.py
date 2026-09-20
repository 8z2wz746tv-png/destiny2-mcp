"""Build Import Agent — 从外部来源导入配装。

将截图/文章/视频转换为 CanonicalBuild，再交给 Build Engine 和 Equip Engine 处理。

数据流:
    Source → Extractor → BuildDraft → Normalizer → CanonicalBuild → Validator → Consumer
"""
