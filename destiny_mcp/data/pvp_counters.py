"""游戏内计数器（profile 组件 1100）的**模式/周期对照表**与实测证据。

为什么需要这张表：三条口径完全不同的计数器**同名叫「已击败对手」**
（熔炉生涯 124,495 / 试炼生涯 10,696 / 熔炉本赛季 3,522），光看名字与数字分不出谁是谁；
而 Manifest 只帮得上一半的忙：

| 依据 | 能分什么 | 分不了什么 |
| --- | --- | --- |
| `DestinyMetricDefinition.parentNodeHashes` → `DestinyPresentationNodeDefinition` | 模式**家族**（熔炉竞技场 / 奥斯里斯试炼 / 智谋 / 突袭 / 赛季…） | 生涯 vs 本赛季（两者同挂"熔炉竞技场"）；铁旗、竞技等级（也挂在"熔炉竞技场"下） |
| 计数器自己的**名字 + 描述** | 子模式与周期（"已击败铁旗对手数"、"本赛季…"、"自第1赛季开始计算"） | 靠人读，必须落成表 |

所以这张表是**人读描述后落下来的实测结论**，不是从上游字段推出来的：表里没有的 hash
一律 `mode="other"` / `period=None`（**不猜**），宁可少标也不要标错。

证据采集（2026-09-17 本机真账号，只读）：`GetProfile` 组件 1100 → 402 条计数器，
名称/描述来自本地中文 Manifest 的 `DestinyMetricDefinition`。下表每条的注释就是当时的
描述原文（差异只在排版），`progress` 是当日实测值（会随游玩增长，只作对照）。

只收录**我们要暴露的**那批（熔炉/试炼/铁旗/竞技/智谋/突袭；共 39 条）：
- 402 条里绝大多数是 PvE 计数器（日落得分、赛季等级、称号镀金…），全收进来只会让
  `mode=` 过滤变得没有意义；
- 描述里写"**本周**"的那批**故意不收**：`period` 的取值只有 career/season/act，
  硬塞一个不存在的周期就是猜（它们的模式很明确，但周期只能靠人读成"周常"）。

`mode` 的取值同时是 `counters(mode=…)` 的合法词表，且**就是 `data/activity_modes` 里的词**
（模式词、`modeType` 数值与中文名的唯一出处都在那边；这里只维护"计数器 → 家族/周期"）。
每条家族词都必须是 `activity_modes.MODES` 的 key —— `tests/test_activity_modes.py` 钉住。
"""

from __future__ import annotations

from typing import Any

# 计数器家族。`other` 是"表里没有"的落点，不是给人过滤用的词。
MODES: tuple[str, ...] = (
    "crucible",
    "trials",
    "iron_banner",
    "competitive",
    "gambit",
    "raid",
    "other",
)

# 周期。`None`（表里的缺失值）表示"描述没说清周期"，不是"没有周期"。
PERIODS: tuple[str, ...] = ("career", "season", "act")

PERIOD_LABELS_ZH: dict[str, str] = {
    "career": "生涯",
    "season": "本赛季",
    "act": "本篇章",
}


def _counter(mode: str, period: str | None, label_zh: str) -> dict[str, Any]:
    """一条对照记录。`period=None` 表示描述没说清周期（如"当前多人竞技级别"）。"""
    return {"mode": mode, "period": period, "label_zh": label_zh}


# metric_hash → 对照记录。注释 = 采集当日中文 Manifest 里的名称与描述（证据本身）。
COUNTERS: dict[int, dict[str, Any]] = {
    # ── 熔炉竞技场（父节点"熔炉竞技场"；生涯那条描述写明"自第1赛季开始计算"）──
    811894228: _counter("crucible", "career", "已击败对手"),   # 已击败对手｜在熔炉竞技场比赛中击败的对手总数。自第1赛季开始计算。progress=124495
    3157801630: _counter("crucible", "career", "金色勋章"),    # 金色勋章｜在熔炉竞技场中获得金色勋章的总数。自第4赛季开始计算。
    2799217987: _counter("crucible", "career", "万夫莫敌"),    # 万夫莫敌｜在一条命内，击败20名对手。自第4赛季开始计算。
    2935221077: _counter("crucible", "season", "已击败对手"),  # 已击败对手｜本赛季在熔炉竞技场比赛中击败的对手数。progress=3522
    871184140: _counter("crucible", "season", "KDA"),         # KDA｜本赛季熔炉竞技场KDA指数。progress=202、completionValue=100 → 游戏内显示 2.02
    1028121783: _counter("crucible", "season", "胜场"),        # 胜场｜本赛季或篇章熔炉竞技场比赛中的胜利总数。
    2941499201: _counter("crucible", "season", "胜率"),        # 胜率｜本赛季熔炉竞技场胜率。
    1249684581: _counter("crucible", "season", "连胜"),        # 连胜｜本赛季在熔炉竞技场中的最高连胜数。
    303387541: _counter("crucible", "season", "超能效率"),      # 超能效率｜本赛季在熔炉竞技场比赛中平均每次超能激活所取得的最后一击数。
    # ── 铁旗（名字直接点名；两条等级/悬赏是累计值）──
    902410069: _counter("iron_banner", "career", "铁旗等级"),      # 铁旗等级｜在铁旗中获得升级奖励包裹的总数。自第1赛季开始计算。progress=949
    3264536674: _counter("iron_banner", "career", "悬赏完成数"),   # 悬赏完成数｜完成铁旗悬赏的总数。自第4赛季开始计算。
    2161492053: _counter("iron_banner", "season", "已击败铁旗对手数"),  # 已击败铁旗对手数｜本赛季铁旗比赛中击败的守护者总数。progress=1737
    1509147660: _counter("iron_banner", "season", "铁旗效率"),     # 铁旗效率｜本赛季铁旗比赛中的平均效率。
    429382583: _counter("iron_banner", "season", "铁旗胜场"),      # 铁旗胜场｜本赛季铁旗比赛中的胜利总数。
    # ── 奥斯里斯试炼（父链是四层"奥斯里斯试炼"；生涯/赛季由描述区分）──
    2082314848: _counter("trials", "career", "已击败对手"),     # 已击败对手｜在奥斯里斯试炼中击败的对手数。自第10赛季开始计算。progress=10696
    1365664208: _counter("trials", "career", "胜场"),          # 胜场｜在"奥斯里斯试炼"中取得的胜场数。自第10赛季开始计算。progress=826
    1765255052: _counter("trials", "career", "完美入场券"),     # 完美入场券｜获得7胜0负入场券的次数。从第10赛季至篇章2期间进行计算。
    1076064058: _counter("trials", "career", "无瑕连胜"),       # 无瑕连胜｜在奥斯里斯试炼中使用一张无瑕灯塔入场券取得的最高连胜。自篇章3开始计算。
    4112712479: _counter("trials", "career", "天下无双称号镀金"),  # 天下无双称号镀金｜天下无双称号镀金的次数。自第13赛季开始计算。
    3481560625: _counter("trials", "season", "已击败对手"),     # 已击败对手｜本赛季在"奥斯里斯试炼"中击败的对手数。
    2367472811: _counter("trials", "season", "胜场"),          # 胜场｜本赛季在"奥斯里斯试炼"中取得的胜场数。
    957196641: _counter("trials", "season", "连胜"),           # 连胜｜本赛季于奥斯里斯试炼中在取得的连续最高连胜。
    3364207969: _counter("trials", "season", "连续零封"),       # 连续零封｜本赛季于奥斯里斯试炼中在无队友死亡的情况下取得的连续最高连胜。
    # ── 多人竞技等级（描述只说"当前"：既不写生涯也不写本赛季 → period 留 None，不猜）──
    268448617: _counter("competitive", None, "多人竞技级别"),       # 多人竞技级别｜当前多人竞技级别。progress=4395（游戏内显示的竞技等级）
    526068952: _counter("competitive", "career", "最高多人竞技等级胜场"),   # 最高多人竞技等级胜场｜以最高多人竞技等级取得的胜场总数。自凯旋纪念碑开始计算。
    1325547125: _counter("competitive", "season", "最高多人竞技等级胜场"),  # 最高多人竞技等级胜场｜在本赛季或篇章中以最高多人竞技等级取得的胜场。
    # ── 智谋（父节点"智谋"）──
    1462038198: _counter("gambit", "career", "储存萤光"),   # 储存萤光｜在智谋比赛中存入的萤光总数。自第4赛季开始计算。progress=11264
    3227312321: _counter("gambit", "career", "击败入侵者"),  # 击败入侵者｜在智谋比赛中消灭的入侵者总数。自第4赛季开始计算。progress=608
    87898835: _counter("gambit", "career", "击败阻绝者"),    # 击败阻绝者｜在智谋比赛中消灭的阻绝者总数。自第4赛季开始计算。
    3587221881: _counter("gambit", "career", "胜场"),       # 胜场｜总共赢得的智谋比赛场数。自第10赛季开始计算。progress=423
    3740642975: _counter("gambit", "career", "击败古昧"),    # 击败古昧｜在智谋比赛中消灭的古昧总数。自第4赛季开始计算。
    2920575849: _counter("gambit", "season", "储存萤光"),    # 储存萤光｜本赛季在智谋比赛中存储的萤光数。
    921988512: _counter("gambit", "season", "击败入侵者"),    # 击败入侵者｜本赛季在智谋比赛中消灭的入侵者数。
    2709150210: _counter("gambit", "season", "击败阻绝者"),   # 击败阻绝者｜本赛季在智谋比赛中消灭的阻绝者数。
    3483580010: _counter("gambit", "season", "胜场"),        # 胜场｜本赛季在智谋比赛中获胜次数。
    3642143556: _counter("gambit", "season", "击败古昧"),     # 击败古昧｜本赛季在智谋比赛中消灭的古昧数。
    # ── 突袭（父链是"突袭"；只收三个最常被问的完成数）──
    2486745106: _counter("raid", "career", "利维坦完成数"),        # 利维坦完成数｜总共完成的"利维坦"场数。自第1赛季开始计算。
    905240985: _counter("raid", "career", "最后一愿完成数"),       # 最后一愿完成数｜总共完成的"最后一愿"场数。自第4赛季开始计算。
    954805812: _counter("raid", "career", "“深岩墓室”完成数"),     # "深岩墓室"完成数｜总共完成的"深岩墓室"场数。自第12赛季开始计算。progress=229
}


def classify(metric_hash: int) -> dict[str, Any]:
    """一个 hash 的对照记录；表里没有就给 `mode="other"` / `period=None`（**不猜**）。

    返回值始终是同一组键（`mode`/`period`/`label_zh`），缺的是值不是结构：
    `label_zh=""` 表示"这张表没收录它"，调用方应改用 Manifest 的名称。
    """
    known = COUNTERS.get(metric_hash)
    if known is None:
        return {"mode": "other", "period": None, "label_zh": ""}
    return dict(known)


def filter_modes() -> tuple[str, ...]:
    """`counters(mode=…)` 认的词表（不含 `other` —— 那是"没分类"的落点，不是筛选项）。"""
    return tuple(mode for mode in MODES if mode != "other")
