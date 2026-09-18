"""PGCR 落盘缓存（`instanceId → 原始结算响应`）。

为什么单独一个模块：结算**不可变**，缓存命中就等价于上游结果，所以"怎么存、存多少、
坏了怎么办"和"怎么聚合榜单"是两个独立的问题 —— 原来它们挤在一个文件里，
那个文件因此顶到了体积上限（`tests/test_module_size_ratchet.py` 逼着做这次拆分）。

只读上游、只写自己的缓存目录，不涉及账号写入，所以不需要 `confirmed`。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger(__name__)

# 缓存目录：`DESTINY_CACHE_PATH/pgcr/`（默认 `~/.destiny_mcp/cache/pgcr`）。
CACHE_SUBDIR = "pgcr"

# 缓存轮换上限：一条 PGCR 约 50 KB，2000 条 ≈ 100 MB。超出按 mtime 删最旧。
MAX_CACHE_FILES = 2000


def cache_dir() -> Path:
    """缓存目录：`DESTINY_CACHE_PATH/pgcr`。

    以前借用 `DESTINY_TOKEN_PATH`（令牌目录）—— 名字会误导"删令牌就清了缓存"，
    而且用户没法单独把缓存挪到大盘上。
    """
    from .. import config

    return Path(config.DESTINY_CACHE_PATH, CACHE_SUBDIR)


# 缓存目录：`DESTINY_CACHE_PATH/pgcr/`（默认 `~/.destiny_mcp/cache/pgcr`）。
# PGCR 不可变，缓存命中即等价于上游结果。
CACHE_SUBDIR = "pgcr"

# 缓存轮换上限：一条 PGCR 约 50 KB，2000 条 ≈ 100 MB。超出按 mtime 删最旧。
MAX_CACHE_FILES = 2000

# 失败场次的样本上限（全量数字在 `failed_matches.total` 与 `window.matches_failed`）。
FAILED_SAMPLE = 10


class PgcrCache:
    """PGCR 落盘缓存（`instanceId → 原始响应`）。

    只读上游、只写自己的缓存目录，不涉及账号写入，所以不需要 `confirmed`。
    缓存坏掉/写不进去都不许影响主结果：读不到就当没缓存，写失败只记日志
    （磁盘满、只读挂载都不该让一次查询失败）。
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or cache_dir()

    def path_for(self, instance_id: str) -> Path:
        return self.directory / f"{instance_id}.json"

    def get(self, instance_id: str) -> dict | None:
        path = self.path_for(instance_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def prune(self, keep: int = MAX_CACHE_FILES) -> int:
        """按 mtime 只留最新 `keep` 条，返回删除条数。

        无上限的缓存会一直长（100 场 ≈ 5 MB，长期跑就是一路涨）。这里不做 TTL：
        PGCR 不可变，旧条目只是"用不上"，删掉不损失正确性 —— 留着是为了省上游请求，
        所以按"最近用过"轮换就够。整轮只做一次（由服务层保证），不在每场之后扫目录。
        """
        try:
            files = sorted(
                self.directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            logger.debug("PGCR 缓存轮换跳过：%s", exc)
            return 0
        removed = 0
        for path in files[keep:]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        if removed:
            logger.info("PGCR 缓存轮换：删除 %d 个最旧条目（保留 %d）", removed, keep)
        return removed

    def put(self, instance_id: str, payload: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.path_for(instance_id).with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            tmp.replace(self.path_for(instance_id))
        except OSError as exc:
            logger.debug("PGCR 缓存写入失败 %s: %s", instance_id, exc)
