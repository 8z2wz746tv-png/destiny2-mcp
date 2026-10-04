"""Starside 归档里图标文件的**相对路径**解析：`icons/<hash>.webp` → `assets/<topic>/icons/<hash>.webp`。

**为什么要有这个模块**：站点文本里的图标写法是 `![](icons/4cdf23a2.webp)` —— 相对于
**它所在的那一页**。实体导出（`entities/perks.json`）把"哪一页"丢了，所以主题名只能拿
归档自己的索引 `index.json` 反查（那一页的 `assets[].path` 就是磁盘上的相对路径）。

**为什么不给 `https://starside.work/...` 外链**（2026-10-04 拍板，见
`docs/plans/ICON_URL_PLAN.md` §十）：

1. 那是第三方站点的地址，归档里 `redistribution_license: not_established`
   （见 `docs/community/COMMUNITY_DATA_NOTICE.md`）—— 把外链写进响应等于替别人做热链分发；
2. 图标本来就是**本地文件**（3767 个，`missing on disk: 0`），给相对路径就够渲染，
   调用方不必联网，也不必替我们承担别人的可用性。

**多个主题下有同一个 hash 时怎么办**：归档里确实存在（90 个 key 出现在 2–9 个主题下，
因为元素图标被每页各存一份）。取**路径字典序最小的那个** —— 判据是确定的，而且**字节
完全相同**（`index.json` 里这 90 个 key 的各份 `sha256` 全等，2026-10-04 核对），
所以选哪个都不影响渲染。别改成"随便取第一个"：那会随索引顺序变化。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config
from ..logging_config import get_logger

logger = get_logger(__name__)

#: 归档根（与 `StarsideService` 的默认 archive_root 同一个位置）
ARCHIVE_ROOT: Path = config.DATA_PATH / "starside"
#: 归档自己的资源清单（哪个相对路径真的在归档里，以它为准）
INDEX_FILE = "index.json"
#: 站点写法保留末尾两段（`icons/<文件名>`）作为反查键
_KEY_SEGMENTS = 2

_cache: tuple[tuple[int, int] | None, dict[str, str]] | None = None


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _asset_map() -> dict[str, str]:
    """归档索引 → `{icons/<名>: assets/<主题>/icons/<名>}`（按 (mtime, size) 缓存）。

    索引不在（自定义 `DATA_PATH`、或归档没抓过）时给空表：调用方退回站点原写法，
    **不编路径**（编出来的路径一定打不开，比原写法更难查）。
    """
    global _cache
    index_path = ARCHIVE_ROOT / INDEX_FILE
    stamp = _stamp(index_path)
    if _cache is not None and _cache[0] == stamp:
        return _cache[1]
    mapping: dict[str, str] = {}
    if stamp is not None:
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            logger.warning("Starside 资源索引读不了（%s）：图标按站点原写法给出。", exc)
            payload = None
        assets = payload.get("assets") if isinstance(payload, dict) else None
        for asset in assets if isinstance(assets, list) else []:
            path = str((asset or {}).get("path") or "")
            parts = path.split("/")
            if len(parts) > _KEY_SEGMENTS and parts[-2] == "icons":
                key = "/".join(parts[-_KEY_SEGMENTS:])
                current = mapping.get(key)
                if current is None or path < current:
                    mapping[key] = path
    _cache = (stamp, mapping)
    return mapping


def archive_relative_path(site_path: str) -> str:
    """站点写法 → 归档里的相对路径；说不准时**原样返回**（不抹掉、不编）。

    - `icons/<名>` 且索引里有 → `assets/<主题>/icons/<名>`；
    - 索引里没有（或没有索引）→ 原样返回站点写法：调用方仍看得出"这里本来有张图"，
      而不是被静默删掉。
    """
    text = str(site_path or "").strip()
    if not text:
        return ""
    return _asset_map().get(text, text)
