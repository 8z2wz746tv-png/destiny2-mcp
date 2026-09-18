"""活动模式词表（词 → `modeType`）的**唯一出处**。

为什么合并：同一个"模式"概念以前在三处各有一张手写表，其中两张有错值，全是真机抓出来的
（2026-09-18）：

| 旧值 | 实际（Manifest 实测） | 后果 |
| --- | --- | --- |
| `ACTIVITY_MODES["allpvp"] = 9` | 伞形 AllPvP 是 **5**；9 是 `Reserved9` | `modes=9` 直接 HTTP 500 |
| `ACTIVITY_MODES["onslaught"]` / `"猛攻"` = 69 | 69 是**多人竞技PvP**（PvP 家族） | 说"猛攻"会被当成竞技 PvP 去查 |
| `MODE_NAMES[69] = "猛攻"` | 69 = 多人竞技PvP | 历史列表把竞技场次标成"猛攻" |
| `ACTIVITY_MODES["grandmaster"] = 46` | 46 = 计分日落；47 = 计分巅峰日落 | 大师日落被当成普通日落 |
| `MODE_NAMES` 只有 8 条 | 真机跑出的子模式 43/44/71/73/89/91 都不在里面 | 输出"模式43"这种没用的名字 |

`onslaught` 的别名**直接删掉**，不是改指别的数：中英 Manifest 里没有 Onslaught 这个模式
（86 叫 `Offensive`／攻势，无法确认就是猛攻）—— 按"缺值给 None、不编"的规矩，
宁可报"不认识这个词"，也不能把智力竞技当成猛攻返回。

数值出处：本地中英 Manifest `DestinyActivityModeDefinition.modeType`（2026-09-18 实测：
75 个模式类型，**无重复、无重名**）。`category` 取自同表 `activityModeCategory`：
**1 = PvE、2 = PvP、3 = PvPvE（智谋 63 是 3）**。

**中文名不写在这里**：它是 Manifest 的官方字段（zh 库 `displayProperties.name`），
运行时用 `ManifestManager.get_activity_mode_name(mode_type)` 取，别再抄一张标签表
——抄一次就会像 `MODE_NAMES[69]="猛攻"` 那样烂掉。

真机实测（2026-09-18，`GetActivityHistory` 的**单数** `mode=`）：

- `mode=5` → 250 场**全部**是 PvP（子模式 43/44/71/73/84/89/91/31/48/81）；
- `mode=19` 返回子模式 43、`mode=69` 返回 37、`mode=84` 全是 84 ——
  **伞形过滤、回报具体模式**（所以榜单要按回报的 `activityDetails.mode` 标名字）；
- `mode=63`（智谋）**不在** `mode=5` 里：智谋是 category=3（PvPvE）。
  所以"纯 PvP"就是 category=2，智谋只能单独说、单独筛；
- `modes=`（**复数**）被**静默忽略**：传 `modes=5` 返回的是 PvE 场次。
  只有单数有效 —— 见 `tests/test_activity_modes.py` 里那条防呆。
"""

from __future__ import annotations

from typing import Any

# `activityModeCategory` 的三个取值（Manifest 字段，实测过）。
CATEGORY_PVE = 1
CATEGORY_PVP = 2
CATEGORY_PVPVE = 3

# 词 → 模式定义。`aliases` 是**永久**别名（中文说法与英文常用词），一律小写比对。
MODES: dict[str, dict[str, Any]] = {
    "story": {"mode_type": 2, "category": CATEGORY_PVE, "aliases": ("故事", "剧情")},
    "strike": {"mode_type": 3, "category": CATEGORY_PVE, "aliases": ("打击",)},
    "raid": {"mode_type": 4, "category": CATEGORY_PVE, "aliases": ("突袭", "突袭任务")},
    # 5 是 PvP 的**伞形**：铁旗、试炼、竞技、占领、死斗这些子模式都挂在它下面。
    # `pvp` / `allpvp` / `竞技场` 都指它，不另立一个 key（计数器表里叫 crucible，沿用）。
    "crucible": {
        "mode_type": 5,
        "category": CATEGORY_PVP,
        "umbrella": True,
        "aliases": ("pvp", "allpvp", "熔炉", "熔炉竞技场", "竞技场"),
    },
    "patrol": {"mode_type": 6, "category": CATEGORY_PVE, "aliases": ("巡逻", "探索")},
    "allpve": {"mode_type": 7, "category": CATEGORY_PVE, "umbrella": True, "aliases": ("pve", "全pve")},
    "iron_banner": {
        "mode_type": 19,
        "category": CATEGORY_PVP,
        "umbrella": True,
        "aliases": ("铁旗", "ib"),
    },
    "nightfall": {"mode_type": 46, "category": CATEGORY_PVE, "aliases": ("日落", "计分日落")},
    "grandmaster": {
        "mode_type": 47,
        "category": CATEGORY_PVE,
        "aliases": ("大师日落", "宗师日落", "gm"),
    },
    # 智谋是 PvPvE（category=3）：它**不属于**"纯 PvP"，筛 PvP 时不会带上它。
    "gambit": {"mode_type": 63, "category": CATEGORY_PVPVE, "aliases": ("智谋",)},
    "competitive": {
        "mode_type": 69,
        "category": CATEGORY_PVP,
        "umbrella": True,
        "aliases": ("多人竞技", "多人竞技pvp", "竞技", "comp"),
    },
    "dungeon": {"mode_type": 82, "category": CATEGORY_PVE, "aliases": ("地牢",)},
    "trials": {
        "mode_type": 84,
        "category": CATEGORY_PVP,
        "aliases": ("试炼", "奥斯里斯试炼", "trialsofosiris", "osiris"),
    },
    "lostsector": {"mode_type": 87, "category": CATEGORY_PVE, "aliases": ("遗失区域",)},
}

# 扁平映射由 `MODES` 现算（不是第二份表：改了上面这里跟着变）。
MODE_TYPE: dict[str, int] = {key: spec["mode_type"] for key, spec in MODES.items()}

# 别名 → key。key 本身也是别名（`resolve("trials")` 必须成立）。
_LOOKUP: dict[str, str] = {}
for _key, _spec in MODES.items():
    _LOOKUP[_key] = _key
    for _alias in _spec.get("aliases", ()):
        _LOOKUP[_alias.lower()] = _key
del _key, _spec, _alias


def keys() -> tuple[str, ...]:
    """全部模式词（不含别名）。"""
    return tuple(MODES)


def resolve(word: str) -> str | None:
    """模式词/别名 → key；不认识就 `None`（调用方负责报错，不许猜一个默认模式）。"""
    return _LOOKUP.get((word or "").strip().lower())


def is_pvp(key: str) -> bool:
    """是不是**纯 PvP**（category=2）。智谋是 PvPvE，这里返回 False。"""
    spec = MODES.get(key)
    return bool(spec) and spec["category"] == CATEGORY_PVP


def pvp_keys() -> tuple[str, ...]:
    """纯 PvP 家族的词（熔炉/铁旗/竞技/试炼）—— 不含智谋。"""
    return tuple(key for key in MODES if is_pvp(key))


def words_with_labels(labels: dict[str, str] | None = None) -> str:
    """错误话术用："crucible（熔炉竞技场）、trials（奥斯里斯试炼）…"。

    `labels` 是调用方从 Manifest 取的中文名（取不到就不带括号），
    这样话术不会因为这里没有标签表而退化。
    """
    parts = []
    for key in MODES:
        label = (labels or {}).get(key, "")
        parts.append(f"{key}（{label}）" if label else key)
    return "、".join(parts)
