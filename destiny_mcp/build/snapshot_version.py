"""指纹的对外出口（实现搬去 `snapshot_fingerprint`，见那边的 docstring 与 ADR-032）。

留着这个名字是因为调用点太多（求解、候选、写前复检），而它的体量上限只有 61 —— 抽走实现、
这里只做转发，是这个仓"超限先抽代码、不抬上限"的常规做法。
"""

from .snapshot_fingerprint import snapshot_version, substance_version

__all__ = ["snapshot_version", "substance_version"]
