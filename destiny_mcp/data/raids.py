"""突袭/地牢 → 游戏内官方计数器（profile 组件 1100）的对照表。

**这是"实测出来的对照关系"，所以放 `data/`**（同 `pvp_counters.py` / `rotations.py`）：
里面有判断的部分（怎么读、怎么组装、缺了怎么报）在 `services/raid_report_service.py`。

## 这张表从哪来（2026-09-30 用本地 Manifest 生成 + 真机核对）

1. **副本清单**取自 `DestinyActivityDefinition.activityTypeHash`
   —— `2043403989` = 突袭、`608898761` = 地牢，名字去掉难度后缀
   （`: 普通`/`: 标准`/`: 大师`/`: 传说`/`: 巅峰`/`: 竞赛`/`: 专家`/`: 最后通牒`/`: 探索者`/`: 等级NN`）
   再按逗号拆开（`利维坦，星之塔` 与 `世界吞噬者，利维坦` 的逗号方向相反，两侧都要试）。
2. **计数器 hash** 取自 `DestinyMetricDefinition`，按**名字后缀**归属：
   `完成数`/`完成次数`/`完成` → completions、`导师` → sherpas、
   `无瑕完成数`/`无暇完成数` → flawless、`单人无瑕完成数` → solo_flawless。
3. **只收 all-time**：描述里出现「本周 / 本赛季 / 本篇章 / 本次发布」的一律排除 ——
   同一个副本这三个变体**名字完全相同**，只能靠 description 区分，抓错就拿错数。

## 为什么是硬编码表，不是运行时正则

因为两个方向都会错，而且真错过：

- **漏**：完成数计数器的名字有 `完成数` / `完成次数` / `完成` 三种写法
  （按 `完成数$` 匹配会漏掉玻璃拱顶 `2506886274` 与门徒誓约 `3585185883`）；
- **多**：按后缀匹配会抓进 `悬赏完成数`（3264536674）、`遗失区域完成数`（740213466）、
  `日落挑战完成数`、`英雄公共事件完成数` 这类**非副本**计数器；
- **描述写法有十来种**：`完成"X"的总场数` / `总共完成的"X"场数` / `完成地牢"X"的场数` /
  `无暇完成地牢"X"的次数` —— 靠描述正则会继续漏；而且 Manifest 里「无瑕」「无暇」**两种写法都有**。

所以：**表是人工核对过的结果，`tests/test_raid_report.py` 反过来校验它**
（每个 hash 必须在 Manifest 里存在、描述里必须出现副本名、且必须是 all-time 那一条）。
新副本上线时测试不会自动加行 —— 但也**不会静默少一行**：少了就是少一行数据，不是错数据。

## 口径（写进响应用户看得见）

- **导师是"人数"不是"次数"**：原文是「带领完成首次"深岩墓室"突袭的守护者**总人数**」——
  一次带 3 个新人记 +3。所以它能大于完成场次（实测某副本「全程 0 / 完成 6 / 导师 11」）。
- **不是每个副本都有全部四项**：老地牢多半没有导师计数器、部分副本连完成数都没有
  （如「平衡」）。缺的给 `None`，**不编 0**。
- **不放进来的**：`巅峰利维坦`/`巅峰星之塔`/`巅峰世界吞噬者`（与基础计数器是否重复**未定标**）、
  `永恒沙漠史诗突袭`、众神殿、以及 `前兆`/守护者游戏这类非副本计数器。
"""

from __future__ import annotations

import re
from typing import NamedTuple


class RaidEntry(NamedTuple):
    """一个副本：显示名 + 类型 + 它**实际拥有**的官方计数器。"""

    name: str
    kind: str  # "raid" | "dungeon"
    counters: dict[str, int]  # 计数器种类 → metric hash
    hashes: tuple[int, ...]  # 标准难度档的活动 hash（PGCR 扫描用；难度变体见 normalize_activity_name）


#: 计数器种类 → 中文标签（话术层只从这里取，别在分支里再写一份）
COUNTER_LABELS_ZH: dict[str, str] = {
    "completions": "完成次数",
    "sherpas": "担任导师次数（人数）",
    "flawless": "无瑕完成次数",
    "solo_flawless": "单人无瑕完成次数",
}

#: 输出顺序（报表里从左到右看到的顺序）
COUNTER_ORDER: tuple[str, ...] = ("completions", "sherpas", "flawless", "solo_flawless")

KIND_LABELS_ZH: dict[str, str] = {"raid": "突袭", "dungeon": "地牢"}


ENTRIES: dict[str, RaidEntry] = {
    "世界吞噬者": RaidEntry("世界吞噬者", "raid", {"completions": 2659534585, "sherpas": 4071965745}, (2164432138, 3089205900,)),
    "二象性": RaidEntry("二象性", "dungeon", {"completions": 3862075762, "flawless": 1034442994, "solo_flawless": 1084707005}, (2823159265,)),
    "克洛塔的末日": RaidEntry("克洛塔的末日", "raid", {"completions": 2552956848, "sherpas": 124026888}, (107319834, 1566480315, 4179289725,)),
    "分离教义": RaidEntry("分离教义", "dungeon", {"completions": 2781975991, "sherpas": 4032045755, "flawless": 2724885891, "solo_flawless": 1174363710}, (247869137, 3834447244,)),
    "利维坦": RaidEntry("利维坦", "raid", {"completions": 2486745106, "sherpas": 4126092134}, (89727599, 287649202, 1699948563, 1875726950, 2693136600, 2693136601, 2693136602, 2693136603, 2693136604, 2693136605, 3916343513, 4039317196,)),
    "国王的陨落": RaidEntry("国王的陨落", "raid", {"completions": 1624029217, "sherpas": 4210188841}, (1374392663, 2897223272,)),
    "守望者尖塔": RaidEntry("守望者尖塔", "dungeon", {"completions": 3702217360, "flawless": 4002846192, "solo_flawless": 411086447}, (1262462921,)),
    "平衡": RaidEntry("平衡", "dungeon", {"sherpas": 2041961731, "flawless": 2568247915, "solo_flawless": 3015566934}, (2727361621,)),
    "异端深渊": RaidEntry("异端深渊", "dungeon", {"completions": 1451729471, "flawless": 310888283, "solo_flawless": 3741172422}, (1375089621, 2582501063,)),
    "往日之苦": RaidEntry("往日之苦", "raid", {"completions": 1201631538, "sherpas": 870430854}, (548750096, 2812525063,)),
    "忧愁王冠": RaidEntry("忧愁王冠", "raid", {"completions": 1815425870, "sherpas": 4154960946}, (960175301, 3333172150,)),
    "战争领主的废墟": RaidEntry("战争领主的废墟", "dungeon", {"completions": 3932004679, "flawless": 3808047795, "solo_flawless": 3253584750}, (2004855007,)),
    "救赎的边缘": RaidEntry("救赎的边缘", "raid", {"completions": 31271381, "sherpas": 4060528349}, (940375169, 1541433876, 2192826039,)),
    "救赎花园": RaidEntry("救赎花园", "raid", {"completions": 1168279855, "sherpas": 3331373451}, (1042180643, 2497200493, 2659723068, 3458480158, 3845997235,)),
    "星之塔": RaidEntry("星之塔", "raid", {"completions": 700051716, "sherpas": 3069033980}, (119944200, 3004605630,)),
    "晚星之主": RaidEntry("晚星之主", "dungeon", {"completions": 2695240656, "sherpas": 673890130, "flawless": 4059881008, "solo_flawless": 2706420015}, (300092127, 1915770060, 3492566689,)),
    "最后一愿": RaidEntry("最后一愿", "raid", {"completions": 905240985, "sherpas": 1139173585}, (1661734046, 2122313384,)),
    "梦魇根源": RaidEntry("梦魇根源", "raid", {"completions": 321051454, "sherpas": 2499684194}, (2381413764,)),
    "永恒沙漠": RaidEntry("永恒沙漠", "raid", {"completions": 2326543328, "sherpas": 2430589496}, (1044919065,)),
    "深岩墓室": RaidEntry("深岩墓室", "raid", {"completions": 954805812, "sherpas": 2330596844}, (910380154, 3976949817,)),
    "深渊机灵": RaidEntry("深渊机灵", "dungeon", {"completions": 3846201365, "flawless": 3251969937, "solo_flawless": 2521923488}, (313828469,)),
    "玻璃拱顶": RaidEntry("玻璃拱顶", "raid", {"completions": 2506886274, "sherpas": 619234070}, (3711931140, 3881495763,)),
    "破碎王座": RaidEntry("破碎王座", "dungeon", {"completions": 1339818929, "flawless": 761318885}, (2032534090,)),
    "贪婪之握": RaidEntry("贪婪之握", "dungeon", {"completions": 451157118, "flawless": 2269915270, "solo_flawless": 3765286137}, (4078656646,)),
    "门徒誓约": RaidEntry("门徒誓约", "raid", {"completions": 3585185883, "sherpas": 3632833403}, (1441982566, 2906950631,)),
    "预言": RaidEntry("预言", "dungeon", {"completions": 352659556, "flawless": 1099614108}, (1077850348, 4148187374,)),
}


def by_kind(kind: str) -> list[RaidEntry]:
    """按类型取副本（`raid` / `dungeon`），顺序稳定（按名字）。"""
    return [entry for _, entry in sorted(ENTRIES.items()) if entry.kind == kind]


#: Bungie 的逗号命名**方向不一致**（实测全库只有这两条）：
#: 「世界吞噬者，利维坦」本名在前，「利维坦，星之塔」本名在后。
#: 判据是"哪个部分不会独自作为活动名出现" —— 因为「利维坦」自己就是一条活动。
#: 新副本若带来新的逗号名，`tests/test_raid_report.py` 会判红（不许静默归错组）。
_COMMA_NAMES: dict[str, str] = {
    "世界吞噬者，利维坦": "世界吞噬者",
    "利维坦，星之塔": "星之塔",
}

#: 难度后缀 → 归到同一个副本。**只有标准档进 `hashes`**：
#: 大师/巅峰/史诗/永恒 是另一档（更长、更难），拿它们算「最短用时」会得到没有意义的数字。
_DIFFICULTY_STANDARD = ("", "普通", "标准")
_DIFFICULTY_OTHER = (
    "大师", "传说", "巅峰", "竞赛", "专家", "最后通牒", "探索者", "探索者（匹配）",
    "永恒", "史诗", "挑战模式",
)


def normalize_activity_name(name: str) -> str | None:
    """活动名 → 副本名；认不出来返回 `None`（**不猜**）。

    只做两件事：剥难度后缀、按 `_COMMA_NAMES` 定逗号名的归属。
    认不出来（新的逗号名、新的难度后缀）就是 `None` —— 由守门测试兜住，
    不让它静默归到某个副本上。
    """
    base = (name or "").strip()
    if not base:
        return None
    # **先剥难度后缀再判逗号名**：`世界吞噬者，利维坦: 普通` 要先变成 `世界吞噬者，利维坦`，
    # 顺序反了这两条就会归不出来（第一版就是反的）。
    # `等级NN` 是"大师"的另一种写法，先按它剥。
    base = re.sub(r"[:：]\s*等级\d+$", "", base).strip()
    for suffix in _DIFFICULTY_OTHER + _DIFFICULTY_STANDARD[1:]:
        if base.endswith(suffix):
            base = base[: -len(suffix)].strip()
            break
    # 剥掉后缀后剩下的分隔符/括号一起清掉（`玻璃拱顶: 大师` → `玻璃拱顶`）
    base = re.sub(r"[:：]\s*$", "", base).strip()
    base = re.sub(r"[（(]\s*(?:史诗|永恒|挑战模式)\s*[)）]$", "", base).strip()
    if base in _COMMA_NAMES:
        return _COMMA_NAMES[base]
    if "，" in base or "," in base:
        return None  # 新的逗号名：不猜，由守门测试报出来
    return base if base in ENTRIES else None


def is_standard_difficulty(name: str) -> bool:
    """这个名字是不是标准档（是否要进 PGCR 扫描的 hash 集合）。"""
    tail = (name or "").strip()
    # **先剥标准档标记再判难度档**：真机上有 `永恒沙漠（史诗）: 标准` 这种写法 ——
    # 字面结尾是「标准」，可它是史诗档（第一版按结尾判，把它算进了标准档）。
    tail = re.sub(r"[:：]\s*(?:标准|普通)$", "", tail).strip()
    # 后缀还可能带括号或分隔符：`（史诗）` / `: 永恒` / `：挑战模式` —— 直接 endswith 也抓不到
    for suffix in _DIFFICULTY_OTHER:
        if re.search(rf"[:：(\（]?\s*{re.escape(suffix)}\s*[)）]?$", tail):
            return False
    return not re.search(r"[:：]\s*等级\d+$", tail)
