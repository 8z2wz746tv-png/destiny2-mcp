"""写入失败话术的对外出口（表与匹配都搬去 `_failure_hints`，见那边的 docstring）。

留着这个名字是因为调用点（`_responses`）与守门都按它引；实现搬走、这里只做转发 ——
这张表加"平台/位置类"条目后就撞了 43 行的体量闸，按仓库规矩**先抽代码、不抬上限**。
"""

from ._failure_hints import write_failure_hints

__all__ = ["write_failure_hints"]
