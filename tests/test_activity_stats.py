"""活动统计的守卫：全量、拼写、数值。

`tests/baselines/activity_stat_keys.json` 是真机抓下来的上游键清单（PvE 65 项、PvP 66 项）。
以前 `activity_service` 手写 8 个键、`precisionkills` 拼错（上游是 `precisionKills`），
结果"精准击杀"静默消失、另外 59 项根本没给。这个测试守三件事：

1. **拼写与覆盖**：上游每个键都必须能在表里拿到中文名（新增上游项会让测试红，逼着做决定）；
2. **不许丢项**：给一段上游统计，行数必须与键数一致（没登记的也要出行）；
3. **数值**：`value` 取原始数（整数还原成 int）、`display` 取显示值、缺了就是 `None`
   （**不编 0**）、场均（`pga`）有就给、没有就不给。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from destiny_mcp import activity_stats as stats

FIXTURE = json.loads(
    (Path(__file__).parent / "baselines" / "activity_stat_keys.json").read_text(encoding="utf-8")
)
UPSTREAM_KEYS = sorted(set(FIXTURE["allPvE"]) | set(FIXTURE["allPvP"]))

SAMPLE_VALUE = {
    "statId": "secondsPlayed",
    "basic": {"value": 3671933.0, "displayValue": "42d 11h"},
    "pga": {"value": 992.9510546241212, "displayValue": "16m 32s"},
}


def _section(keys: list[str] | None = None) -> dict:
    return {key: dict(SAMPLE_VALUE, statId=key) for key in (keys or UPSTREAM_KEYS)}


def test_every_upstream_key_has_a_chinese_label() -> None:
    unlabeled = [key for key in UPSTREAM_KEYS if not stats._label(key)[0]]

    assert not unlabeled, (
        "上游有统计项没登记中文名（新增/改名后要补 activity_stats._TABLE）：\n  "
        + "\n  ".join(unlabeled)
    )


def test_no_upstream_key_is_dropped() -> None:
    rows = stats.stat_rows(_section())

    assert len(rows) == len(UPSTREAM_KEYS)
    assert {row["upstream_id"] for row in rows} == set(UPSTREAM_KEYS), "一行都不能少"


def test_unknown_key_still_becomes_a_row_and_is_not_invented() -> None:
    rows = stats.stat_rows({"someBrandNewStat": dict(SAMPLE_VALUE)})

    assert len(rows) == 1
    row = rows[0]
    assert row["upstream_id"] == "someBrandNewStat"
    assert row["stat_id"] == "some_brand_new_stat"
    assert row["name"] == "" and row["group"] == "other", "没登记就如实留空，不编名字"


def test_value_display_and_per_game_come_from_upstream() -> None:
    row = stats.stat_row("secondsPlayed", SAMPLE_VALUE)

    assert row["stat_id"] == "seconds_played"
    assert row["name"] == "游戏时长"
    assert row["unit"] == "seconds", "时长类要标单位，否则没法折小时"
    assert row["value"] == 3671933, "原始数值要还原成 int"
    assert row["display"] == "42d 11h", "显示值照抄上游"
    assert row["per_game"]["value"] == 992.9511
    assert row["per_game"]["display"] == "16m 32s"


def test_missing_value_is_none_not_zero() -> None:
    row = stats.stat_row("kills", {"statId": "kills"})

    assert row["value"] is None
    assert row["display"] == ""
    assert "per_game" not in row, "没有场均就不给这个字段，不编 0"


def test_stat_ids_are_unique_snake_case() -> None:
    rows = stats.stat_rows(_section())
    ids = [row["stat_id"] for row in rows]

    assert len(set(ids)) == len(ids), "stat_id 撞车说明机械转换不够用，要显式登记"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", stat_id) for stat_id in ids)


def test_groups_are_known_and_ordered() -> None:
    payload = stats.stat_groups({"pve": _section(), "pvp": _section()})

    assert payload["schema_version"] == stats.SCHEMA_VERSION
    assert [group["key"] for group in payload["groups"]] == ["pve", "pvp"]
    assert all(group["label"] for group in payload["groups"])
    assert all(group["stat_count"] == len(group["stats"]) for group in payload["groups"])
    for group in payload["groups"]:
        positions = [stats.GROUP_ORDER.index(row["group"]) for row in group["stats"]]
        assert positions == sorted(positions), "分组要按 GROUP_ORDER 排好，常用的在前"


def test_empty_section_yields_no_group() -> None:
    assert stats.stat_groups({"pve": {}})["groups"] == []
