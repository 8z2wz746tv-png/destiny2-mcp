"""本地社区评级清单：登记表、表格摊平与按名精确查询。

只管已经加载好的归档记录，不做 I/O、不认识 StarsideService。
加一份新清单只需要在 RATED_LISTS 里加一条；评级正则和字段映射都不用动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping
from urllib.parse import urlparse

from .starside_builds import clean
from .starside_text import cell_lines, cell_text

# 单次查询最多返回的行数。一次问很多名字必须分批，否则截断会被误读成「清单里没有」。
MAX_QUERY_ROWS = 20
# 批量查来源时每批的名字数：一批最多两行（同一件出现在多份精选清单里），留足余量。
SOURCING_BATCH_NAMES = 8

# 刻度：不同清单的评级不可互相比较，所以每行都必须带上自己来自哪一套。
FARM_SCALE = "T"  # 刷取清单：T0–T4，回答「值不值得刷」
TIER_SCALE = "S-F"  # 购物清单：全武器梯队 S–F，回答「强不强」
ORDERED_SCALE = "ordered"  # 按泛用性排序但源数据没有档位列，不编造评级

FARMING_NAME_HEADERS = ("武器", "名称")


@dataclass(frozen=True)
class RatedList:
    page_id: str
    name: str
    scale: str
    columns: Mapping[str, str] = field(default_factory=dict)
    name_headers: tuple[str, ...] = FARMING_NAME_HEADERS


RATED_LISTS: tuple[RatedList, ...] = (
    # 精选「刷取清单」在前：它才是「值不值得刷」的答案。
    RatedList("page:share/legendary-primary.md", "刷取清单-白弹紫枪", FARM_SCALE),
    RatedList("page:share/legendary-special.md", "刷取清单-绿弹紫枪", FARM_SCALE),
    RatedList("page:share/legendary-heavy.md", "刷取清单-威能紫枪", FARM_SCALE),
    RatedList("page:legendary-primary/index.html", "刷取清单-白弹紫枪", FARM_SCALE),
    RatedList("page:legendary-special/index.html", "刷取清单-绿弹紫枪", FARM_SCALE),
    RatedList("page:legendary-heavy/index.html", "刷取清单-威能紫枪", FARM_SCALE),
    RatedList("page:exotic-weapons/index.html", "刷取清单-异域武器", FARM_SCALE),
    # 全武器梯队，只有精选清单没有这把时才拿来回答，且必须带着自己的刻度返回。
    RatedList("page:shopping-primary/index.html", "购物清单-白弹", TIER_SCALE),
    RatedList("page:shopping-special/index.html", "购物清单-绿弹", TIER_SCALE),
    RatedList("page:shopping-heavy/index.html", "购物清单-威能", TIER_SCALE),
    RatedList("page:shopping-other/index.html", "购物清单-其他", TIER_SCALE),
    # 护甲套装：名字列是「套装」而不是「武器」，源数据没有档位列，顺序即泛用性排序。
    RatedList(
        "page:farming-sets/index.html",
        "刷取清单-护甲套装",
        ORDERED_SCALE,
        columns={"件数": "pieces", "应用场景": "scenario", "说明": "note"},
        name_headers=("套装",),
    ),
)

# 各族清单共用的表头映射（归一化后）。清单里没有的列自然取不到，多余的条目无害；
# 同一含义的不同写法在这里并列，例如「获取地点」与「来源」、「评级理由」与「注解」。
FARMING_FIELDS = {
    "评级": "tier",
    "框架射速": "frame",
    "框架": "frame",
    "属性": "element",
    "勇士": "champion",
    "获取地点": "source",
    "来源": "source",
    "Perk三号位": "perk_3",
    "Perk四号位": "perk_4",
    "Perk1": "perk_3",
    "Perk2": "perk_4",
    "排名": "rank",
    "总伤": "total_damage",
    "DPS": "dps",
    "切换DPS": "swap_dps",
    "备注": "remark",
    "评级理由": "note",
    "注解": "note",
    "理由一": "reason_1",
    "理由二": "reason_2",
    "理由三": "reason_3",
}

FARMING_REASON_FIELDS = ("reason_1", "reason_2", "reason_3")
FARMING_PERK_FIELDS = (("perk_3", "三号位"), ("perk_4", "四号位"))
# 这些单元格里换行分开的是同一栏位的备选，必须保留分行，不能压成一个字符串。
FARMING_LIST_FIELDS = frozenset({"perk_3", "perk_4"})
# 档位/梯队：T0–T4（可带限定词，例如旧版本那行的「T0（旧）」）或购物清单的 S–F 字母。
FARMING_GRADE = re.compile(
    r"^(?:[Tt]?\d+(?:\.\d+)?(?:\s*[（(][^）)]*[）)])?|[SABCDEF][+-]?)$"
)
FARMING_SCENARIO_TIER = re.compile(
    r"(输出|清怪|高难|宗师|日常|PvP)\s*[:：]\s*([Tt]?\d+(?:\.\d+)?)"
)
# 清单用 | == 框架评级说明 == | 这种单格行分组，它不是武器行。
FARMING_DIVIDER_ROW = re.compile(r"^==.*==$")


def normalize_farming_header(value: str) -> str:
    return re.sub(r"[\s\\/]+", "", value or "")


def _page_id(url: str) -> str:
    return urlparse(url).path.removeprefix("/")


class RatedLists:
    """把注册表里的清单摊平成 名称 → {清单名: 一行字段}，并做精确查询。"""

    def __init__(self, records: Mapping[str, dict], snapshot_id: str) -> None:
        self._records = records
        self._snapshot_id = snapshot_id
        self._index: dict[str, dict[str, dict]] | None = None

    # ── 索引 ─────────────────────────────────────────────────────────
    def _record(self, page_id: str) -> dict | None:
        """随附 Markdown 以 page: 前缀为键，归档记录以完整 URL 为键。"""
        record = self._records.get(page_id)
        if record is not None:
            return record
        if not page_id.startswith("page:"):
            return None
        wanted = page_id.removeprefix("page:")
        return next(
            (
                candidate
                for url, candidate in self._records.items()
                if _page_id(url) == wanted
            ),
            None,
        )

    @staticmethod
    def _group_row(row: list[dict], cells: list[str]) -> bool:
        """清单用分组行标注框架评级，它不是武器行。

        Markdown 版写作 ``| == 说明 == |``；归档版是单个 ``th[scope=colgroup]``。
        """
        if len(row) == 1 and (row[0].get("attrs") or {}).get("scope") == "colgroup":
            return True
        return bool(cells) and FARMING_DIVIDER_ROW.fullmatch(cells[0]) is not None

    def _build(self) -> dict[str, dict[str, dict]]:
        """摊平成 名称 → {清单名: 一行字段}；不做模糊匹配。

        同一清单里同名只留第一行（一份清单可能把同一把枪按新旧版本占两行）；
        不同清单各自保留，取值时按 RATED_LISTS 的先后决定用哪一份。
        """
        index: dict[str, dict[str, dict]] = {}
        for rated in RATED_LISTS:
            record = self._record(rated.page_id)
            if not record:
                continue
            for table in record["tables"]:
                rows = table["rows"]
                if not rows or not all(cell["tag"] == "th" for cell in rows[0]):
                    continue
                headers = [
                    normalize_farming_header(cell_text(cell)) for cell in rows[0]
                ]
                columns = {name: i for i, name in enumerate(headers) if name}
                name_column = next(
                    (columns[key] for key in rated.name_headers if key in columns),
                    None,
                )
                if name_column is None:
                    continue
                for row in rows[1:]:
                    cells = [cell_text(cell) for cell in row]
                    if name_column >= len(cells):
                        continue
                    name = cells[name_column]
                    if not name or self._group_row(row, cells):
                        continue
                    per_list = index.setdefault(name, {})
                    if rated.name in per_list:
                        continue
                    per_list[rated.name] = self._entry(
                        name, rated, columns, cells, row, record
                    )
        return index

    def _entry(
        self,
        name: str,
        rated: RatedList,
        columns: dict[str, int],
        cells: list[str],
        raw_row: list[dict],
        record: dict,
    ) -> dict:
        entry: dict = {"name": name, "list": rated.name, "scale": rated.scale}
        for header, field_name in (FARMING_FIELDS | dict(rated.columns)).items():
            column = columns.get(header)
            if column is None or column >= len(cells):
                continue
            if field_name in FARMING_LIST_FIELDS:
                values = cell_lines(raw_row[column])
                if values:
                    entry[field_name] = values
                continue
            if not cells[column]:
                continue
            entry[field_name] = cells[column]

        grade = entry.pop("tier", "")
        if grade:
            scenario = dict(FARMING_SCENARIO_TIER.findall(grade))
            if scenario:
                # 异域清单把场景评级写在同一个单元格里，例如「输出：T0 高难：T0.5」。
                entry["scenario_tiers"] = scenario
            elif FARMING_GRADE.fullmatch(grade):
                entry["tier"] = grade
            else:
                # 同列还有「输出工具枪」这类定位标签，不是档位。
                entry["role"] = grade

        perks = {
            label: entry.pop(key)
            for key, label in FARMING_PERK_FIELDS
            if entry.get(key)
        }
        if perks:
            entry["perks"] = perks

        reasons = [entry.pop(key) for key in FARMING_REASON_FIELDS if entry.get(key)]
        if reasons:
            joined = " ".join(dict.fromkeys(reasons))
            entry["note"] = " ".join(filter(None, (entry.get("note"), joined)))

        if record.get("source_type") == "author_markdown":
            source_ref = {
                "source_type": "author_markdown",
                "local_path": record["local_path"],
                "updated_at": record.get("updated_at") or None,
            }
        else:
            source_ref = {
                "source_type": "web_archive_v2",
                "url": record["url"],
                "updated_at": record.get("updated_at") or None,
            }
        entry["source_ref"] = source_ref | {
            "snapshot_id": self._snapshot_id,
            "trust": "untrusted_reference",
        }
        return entry

    def _entries(self) -> dict[str, dict[str, dict]]:
        if self._index is None:
            self._index = self._build()
        return self._index

    def names(self) -> list[str]:
        """已建索引的名称，按加入顺序；给诊断和测试用。"""
        return list(self._entries())

    # ── 查询 ─────────────────────────────────────────────────────────
    def _resolve(self, name: str) -> dict[str, dict] | None:
        index = self._entries()
        per_list = index.get(name)
        if per_list is not None:
            return per_list
        folded = name.casefold()
        return next(
            (value for key, value in index.items() if key.casefold() == folded), None
        )

    @staticmethod
    def _requested(names: str | list[str]) -> list[str]:
        wanted = [names] if isinstance(names, str) else list(names or [])
        requested: list[str] = []
        for value in wanted:
            if isinstance(value, str) and value.strip():
                candidate = clean(value)
                if candidate and candidate not in requested:
                    requested.append(candidate)
        return requested

    def _rows_for(self, name: str, per_list: dict[str, dict] | None) -> list[dict] | None:
        """精选刷取清单才是「值不值得刷」的答案；没有它才回退到全武器梯队。"""
        if not per_list:
            return None
        preferred = [
            entry for entry in per_list.values() if entry["scale"] == FARM_SCALE
        ]
        return preferred or list(per_list.values())

    def lookup(self, names: str | list[str], *, limit: int = 5) -> dict:
        """按名称精确查本地评级清单。

        未命中只说明本地清单没有这个名称，不能反推该名称不值得刷。
        """
        limit = max(1, min(limit, MAX_QUERY_ROWS))
        requested = self._requested(names)

        rows: list[dict] = []
        unmatched: list[str] = []
        for name in requested:
            matched = self._rows_for(name, self._resolve(name))
            if matched is None:
                unmatched.append(name)
                continue
            rows.extend(matched)

        results = rows[:limit]
        index = self._entries()
        available = bool(index)
        list_names = sorted(
            {row["list"] for per_list in index.values() for row in per_list.values()}
        )
        return {
            "available": available,
            "matched_count": len(rows),
            "returned_count": len(results),
            "truncated": len(rows) > len(results),
            "results": results,
            "unmatched": unmatched[:limit],
            "lists": list_names,
            "list_count": len(list_names),
            "coverage_scope": "indexed_rated_lists_only",
            "warnings": (
                [
                    "清单是社区评级，不是官方数据；引用时保留清单名、刻度与更新时间。",
                    "scale=T 是精选刷取清单（值不值得刷）；scale=S-F 是购物清单的全武器"
                    "梯队（强不强），两者刻度不同，不能互相比较。",
                    "未命中只说明本地清单没有这个名称，不代表该名称不值得刷。",
                ]
                if available
                else ["本地未安装评级清单资料；不能据此判断某名称不在清单中。"]
            ),
        }

    def lookup_all(self, names: list[str]) -> dict:
        """按名批量查全，分批规避单次上限；截断会被读成「清单里没有」。"""
        rows: list[dict] = []
        available = False
        for start in range(0, len(names), SOURCING_BATCH_NAMES):
            chunk = names[start : start + SOURCING_BATCH_NAMES]
            result = self.lookup(chunk, limit=MAX_QUERY_ROWS)
            available = available or bool(result.get("available"))
            rows.extend(result.get("results", []))
        return {"available": available, "results": rows}
