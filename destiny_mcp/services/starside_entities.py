"""Starside 实体层：按 hash 查站点作者给的社区数据（推荐 / 评语 / 关联）。

数据来自作者给的新归档，经 `scripts/import_starside_entities.py` 转成
`data/starside/entities/{items,perks}.json`（只留社区层字段；定义、名字、图标一律以我们自己的
Manifest 为准 —— 单一出处）。

三条设计约束：

1. **懒加载**：不在启动时读（性能计划第七项那条基线：启动 5.7 秒里没有它的位置），首次用到才读一次。
2. **hash 一律无符号**：文件里的键就是无符号字符串，查询入口再归一一次。今天真机踩过两次——
   导出是无符号（`+超能 / -生命值` = 4026414261），`manifest.search` 是有符号（-268553035），
   裸比较会把"清单里有的"判成没有。
3. **它是社区资料（第三档）**：这里的任何东西都不能当事实用；`meta()` 里的
   `source`/`snapshot_at`/`authors`/`unofficial` 必须跟着数据一起出去。
   缺数据就返回 `None`/空元组（可选资料降级不弄坏主结果），并把原因留在 `load_problem` 里。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config
from ..logging_config import get_logger
from ..utils.hash_utils import to_unsigned

logger = get_logger(__name__)


class StarsideEntities:
    """社区实体索引（按 hash）。线程内共享一个实例即可，读文件只做一次。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or (config.DATA_PATH / "starside" / "entities")).resolve()
        self._items: dict[str, dict[str, Any]] | None = None
        self._perks: dict[str, dict[str, Any]] | None = None
        self._meta: dict[str, Any] = {}
        self.load_problem = ""

    # ── 加载 ─────────────────────────────────────────────────────────

    def _read(self, filename: str) -> dict[str, dict[str, Any]]:
        path = self._root / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.load_problem = f"社区实体数据缺失：{path}（跑 scripts/import_starside_entities.py 生成）"
            logger.warning(self.load_problem)
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            self.load_problem = f"社区实体数据读不了：{path}（{exc}）"
            logger.warning(self.load_problem)
            return {}
        if not self._meta:
            meta = payload.get("_meta")
            self._meta = meta if isinstance(meta, dict) else {}
        body = payload.get(filename.split(".")[0])
        return body if isinstance(body, dict) else {}

    def _items_data(self) -> dict[str, dict[str, Any]]:
        if self._items is None:
            self._items = self._read("items.json")
        return self._items

    def _perks_data(self) -> dict[str, dict[str, Any]]:
        if self._perks is None:
            self._perks = self._read("perks.json")
        return self._perks

    # ── 元数据与可用性 ───────────────────────────────────────────────

    def available(self) -> bool:
        return bool(self._items_data()) or bool(self._perks_data())

    def meta(self) -> dict[str, Any]:
        """来源信息（`source`/`snapshot_at`/`authors`/`unofficial`/`counts`/`coverage`）。

        先摸一次数据，保证 `_meta` 已读；返回浅拷贝，调用方改不动内部状态。
        """
        self._items_data()
        return dict(self._meta)

    def attribution(self) -> dict[str, Any]:
        """响应里要跟着社区数据一起出去的字段（缺数据时也给，只是 `available=False`）。"""
        meta = self.meta()
        return {
            "source": meta.get("source", "starside.work"),
            "snapshot_at": meta.get("snapshot_at", ""),
            "authors": list(meta.get("authors") or []),
            "unofficial": True,
            "available": self.available(),
            "note": "Starside 站点的社区整理，非 Bungie 官方数据；数值与评级都带站点口径。",
        }

    # ── 查询（全部按无符号 hash）─────────────────────────────────────

    def item(self, item_hash: int | str) -> dict[str, Any] | None:
        return self._items_data().get(str(to_unsigned(int(item_hash))))

    def perk(self, perk_hash: int | str) -> dict[str, Any] | None:
        return self._perks_data().get(str(to_unsigned(int(perk_hash))))

    def items_with_perk(self, perk_hash: int | str) -> tuple[int, ...]:
        """这颗 perk 出现在哪些物品上（作者导出的反查索引，6,134 条边）。

        返回的 hash 里有一部分**在本数据文件里没有条目** —— 我们只收"有社区字段"的物品
        （5,760 → 4,483），所以 6,134 条边里有 922 条指向的物品只有社区数据之外的东西。
        这不是丢数据：那些物品的名字/类型一律去我们的 Manifest 取（单一出处），
        真机审计 `scripts/audit_starside_entities.py` 保证每条边都能在 Manifest 里解出。
        """
        entry = self.perk(perk_hash) or {}
        return tuple(to_unsigned(int(h)) for h in (entry.get("onItems") or []))

    def sets_of_perk(self, perk_hash: int | str) -> tuple[int, ...]:
        """这颗 perk 属于哪些套装（套装 2/4 件效果的评语挂在这里）。"""
        entry = self.perk(perk_hash) or {}
        return tuple(to_unsigned(int(s)) for s in (entry.get("onSets") or []))

    def frame_stats(self, archetype_hash: int | str) -> tuple[dict[str, Any], ...]:
        """帧级 DPS 模型（28 个框架有；键是**框架 plug hash**，不是武器 hash）。"""
        entry = self.item(archetype_hash) or {}
        rows = (entry.get("zh") or {}).get("site_frameStats") or []
        return tuple(row for row in rows if isinstance(row, dict))

    def frame_stats_for_weapon(self, weapon_hash: int | str) -> tuple[dict[str, Any], ...]:
        """武器 → 它的框架 → 帧表（`derived.archetype` 就是框架 hash，28/28 都在我们库里）。"""
        entry = self.item(weapon_hash) or {}
        archetype = (entry.get("derived") or {}).get("archetype")
        return self.frame_stats(archetype) if archetype else ()

    def artifact_of(self, mod_hash: int | str) -> int | None:
        """这个模组属于哪件赛季神器（神器**物品** hash；缺就 None）。"""
        entry = self.item(mod_hash) or {}
        artifact = entry.get("site_artifact")
        return to_unsigned(int(artifact)) if artifact else None

    def authors_of(self, item_hash: int | str) -> dict[str, Any]:
        """这一件物品上的作者块（Aegis / LGpig …），没有就空字典。"""
        entry = self.item(item_hash) or {}
        block = (entry.get("zh") or {}).get("site_authors")
        return block if isinstance(block, dict) else {}
