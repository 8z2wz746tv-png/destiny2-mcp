"""结构化参数被写成文本时的解析规则：分隔符与键值写法**只此一处**。

有些宿主（实测：豆包 connector）序列化工具参数时只发标量，`list[str]` 与 `dict`
原样送不出去，调用方只能把 "亡者复仇,速射" 或 "grenade=100,melee=90" 当字符串发。
这份规则就是"一段文本怎么拆"的唯一定义，`tools/_coerce.py`（工具层还原入参）与
`services/weapon_roll_filter_service.py`（历史上自己写过一份 `split(",")`）都走这里 ——
两处规则迟早会分叉，而分叉的表现是"换个宿主就少筛一半"。

分隔符只收**中文名字里不会出现**的几个：半角/全角逗号、顿号、半角/全角分号、换行。
所以"每栏一个词"的拆法是安全的，不需要转义、不需要猜。键值之间的 `=` 允许被中文
输入法写成 `：`/`:`（同一件事的三种写法）；值里出现别的字符一律当值原样保留。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 词与词之间的分隔。顿号是全角标点里最容易被中文输入法打出来的那个。
_ITEM_SEPARATORS = ",，、;；\n"
# 键与值之间的分隔。
_KEY_VALUE_SEPARATORS = "=:："

_ITEM_SPLIT = re.compile(f"[{re.escape(_ITEM_SEPARATORS)}]")
_KEY_VALUE_SPLIT = re.compile(f"[{re.escape(_KEY_VALUE_SEPARATORS)}]")


def split_items(value: str | Iterable[str]) -> list[str]:
    """把一段文本或一串词归一到 `["词1", "词2"]`（去空白、丢空词）。

    字符串按分隔符拆；已经是序列的原样逐项取 —— 不拆项内的分隔符：调用方给了列表
    就说明它自己分好词了，再拆一次会把带逗号的英文名拆坏。
    """
    if isinstance(value, str):
        return [part.strip() for part in _ITEM_SPLIT.split(value) if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def split_pairs(value: str | Iterable[str]) -> list[tuple[str, str]]:
    """把 `"k=v,k2=v2"` 拆成 `[("k", "v"), ("k2", "v2")]`。

    片段里没有键值分隔符（`"grenade"`）或键/值为空，都抛 `ValueError`：
    那是"写法不对"而不是"这个键没值"，静默丢键比报错难查得多。
    """
    pairs: list[tuple[str, str]] = []
    for raw in split_items(value):
        parts = _KEY_VALUE_SPLIT.split(raw, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"{raw!r} 不是 k=v 形态：键和值都要有。")
        key, item = parts[0].strip(), parts[1].strip()
        if not key or not item:
            raise ValueError(f"{raw!r} 不是 k=v 形态：键和值都要有。")
        pairs.append((key, item))
    return pairs
