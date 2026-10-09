"""候选 → "求解那次调用的参数"（B 件事，唯一出处）。

为什么要单独一个模块：`build_candidates.py` 贴着体量上限，而这份簿记是**另一件事** ——
候选本身是方案，这里是"它当初是按什么条件求出来的"。

为什么要有：候选过期（`stale_inventory_snapshot`）时，回执以前只给
`{"intent": "recommend", "character": …}` —— **换了词、还丢掉了金装/套装/属性目标**，
模型照做会求出另一套配装（实测 15 次）。存下来就能原样回带"用同样的条件重新求解"。
"""

from __future__ import annotations

from typing import Any


class SearchArgsBook:
    """`execution_id → 求解参数`。没有就返回空 dict —— **不编**。"""

    def __init__(self) -> None:
        self._args: dict[str, dict[str, Any]] = {}

    def record(self, execution_id: str, args: dict[str, Any] | None) -> None:
        if execution_id:
            self._args[execution_id] = dict(args or {})

    def get(self, execution_id: str) -> dict[str, Any]:
        return dict(self._args.get(execution_id) or {})

    def forget(self, execution_id: str) -> None:
        self._args.pop(execution_id, None)
