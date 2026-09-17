"""计数器对照表的守门：hash 唯一、取值在枚举内、输出标签与表一致。

`data/pvp_counters.py` 是一张**人工读描述后落下来的实测结论**（三条重名的「已击败对手」
只能靠它区分）。人工表最怕两件事，这个文件各守一条：

1. **同一个 hash 写两遍**：`{811894228: ..., 811894228: ...}` 在 Python 里是**合法**的
   （后者静默覆盖前者），光看代码看不出来 —— 所以这里直接**扫源码里的 hash 字面量**，
   重复即红；
2. **写了个不存在的词**：`mode="crucible2"`、`period="weekly"` 这类拼错会让过滤永远筛不到，
   而调用方看到的是"没有这条计数器"。所以取值必须在 `MODES`/`PERIODS` 里。

第三条是"表与输出一致"：`counters` 每一行的 `mode`/`period`/`label_zh` 必须**就是**表里
那一份（不是两处各写一遍、也不是输出时现算），否则表就不是单一出处了。

真机核对（`811894228`=124,495、`2082314848`=10,696、`2935221077`=3,522、`2161492053`=1,737）
在 `docs/reference/bungie_api.md` 与真机脚本里，不进这个文件：单测用替身、任何机器能跑。
"""

from __future__ import annotations

import re
from pathlib import Path

from destiny_mcp.data import pvp_counters
from destiny_mcp.services import activity_counters_service as counters_module
from destiny_mcp.services.activity_counters_service import parse_counters

TABLE_SOURCE = (
    Path(__file__).resolve().parents[1] / "destiny_mcp" / "data" / "pvp_counters.py"
)

# 表里的 hash 字面量：行首缩进的 `123456789: _counter(...)`。
HASH_LITERAL = re.compile(r"^\s*(\d{6,}):\s*_counter\(", re.MULTILINE)

# 2026-09-17 真机确证的四条（数字当天实测；这里只钉"分类"，数字会随游玩增长）。
CONFIRMED = {
    811894228: ("crucible", "career", "已击败对手"),      # progress=124495
    2082314848: ("trials", "career", "已击败对手"),        # progress=10696
    2935221077: ("crucible", "season", "已击败对手"),      # progress=3522
    2161492053: ("iron_banner", "season", "已击败铁旗对手数"),  # progress=1737
}


def test_table_hashes_are_unique_in_source() -> None:
    """源码里的 hash 字面量不许重复（字典重复键会静默覆盖）。"""
    hashes = HASH_LITERAL.findall(TABLE_SOURCE.read_text(encoding="utf-8"))
    duplicates = sorted({h for h in hashes if hashes.count(h) > 1})

    assert not duplicates, f"对照表里有重复 hash（后者会静默覆盖前者）：{duplicates}"
    assert len(hashes) == len(pvp_counters.COUNTERS), (
        "源码里的 hash 条数与 COUNTERS 不一致 —— 说明有写法没被 HASH_LITERAL 认出来"
        f"（源码 {len(hashes)} 条 / 表 {len(pvp_counters.COUNTERS)} 条）"
    )


def test_table_values_are_inside_the_enums() -> None:
    offenders = []
    for metric_hash, entry in pvp_counters.COUNTERS.items():
        if entry["mode"] not in pvp_counters.MODES:
            offenders.append(f"{metric_hash}: mode={entry['mode']!r}")
        if entry["period"] is not None and entry["period"] not in pvp_counters.PERIODS:
            offenders.append(f"{metric_hash}: period={entry['period']!r}")
        if not entry["label_zh"].strip():
            offenders.append(f"{metric_hash}: label_zh 是空的")
    assert not offenders, "对照表里有枚举外的取值（过滤会永远筛不到）：" + "；".join(offenders)


def test_table_size_is_within_the_planned_range() -> None:
    """只收"我们要暴露的那批"：太少没有意义，太多说明在往里堆。"""
    assert 20 <= len(pvp_counters.COUNTERS) <= 40, len(pvp_counters.COUNTERS)


def test_confirmed_hashes_keep_their_classification() -> None:
    """真机确证的四条：分类与标签钉死（改名/挪动就是错的）。"""
    for metric_hash, (mode, period, label) in CONFIRMED.items():
        assert pvp_counters.classify(metric_hash) == {
            "mode": mode, "period": period, "label_zh": label,
        }, metric_hash


def test_unknown_hash_is_other_and_not_guessed() -> None:
    assert pvp_counters.classify(12345678) == {"mode": "other", "period": None, "label_zh": ""}


def test_classify_returns_a_copy() -> None:
    """改了返回值不许弄脏表本身（调用方拿到的是副本）。"""
    entry = pvp_counters.classify(811894228)
    entry["mode"] = "被改过了"
    assert pvp_counters.COUNTERS[811894228]["mode"] == "crucible"


def test_filter_modes_excludes_other() -> None:
    assert "other" not in pvp_counters.filter_modes()
    assert set(pvp_counters.filter_modes()) <= set(pvp_counters.MODES)


class _Manifest:
    """`get_metric_definition` 只用得到 displayProperties，替身不需要连接。"""

    def __init__(self, names: dict[int, str]) -> None:
        self._names = names

    def get_metric_definition(self, metric_hash: int) -> dict | None:
        name = self._names.get(metric_hash)
        return {"displayProperties": {"name": name, "description": ""}} if name else None


def test_counter_rows_carry_the_table_labels() -> None:
    """输出行必须原样带出表里的三项（不是输出时另算一份）。"""
    raw = {
        "811894228": {"objectiveProgress": {"progress": 124495, "completionValue": 20000}},
        "12345678": {"objectiveProgress": {"progress": 5, "completionValue": 10}},
    }
    manifest = _Manifest({811894228: "已击败对手", 12345678: "某条没收录的计数"})

    rows = {row["metric_hash"]: row for row in parse_counters(raw, manifest)}

    for key in ("mode", "period", "label_zh"):
        assert rows[811894228][key] == pvp_counters.COUNTERS[811894228][key], key
    assert rows[811894228]["mode_label"] == pvp_counters.MODE_LABELS_ZH["crucible"]
    assert rows[811894228]["period_label"] == pvp_counters.PERIOD_LABELS_ZH["career"]
    # 表里没有的：other/None/""，而 Manifest 的名字照旧给出来（缺的是我们的口径，不是上游文本）。
    assert rows[12345678]["mode"] == "other"
    assert rows[12345678]["period"] is None
    assert rows[12345678]["label_zh"] == ""
    assert rows[12345678]["name"] == "某条没收录的计数"


def test_mode_filter_rejects_words_outside_the_table() -> None:
    """词表外的 mode 报错，不返回空清单 —— "筛出来是空"和"词不认识"要分开。"""
    import pytest

    from destiny_mcp.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        counters_module.filter_counters([], "", mode="日落")
    with pytest.raises(InvalidArgumentError):
        counters_module.filter_counters([], "", period="weekly")
