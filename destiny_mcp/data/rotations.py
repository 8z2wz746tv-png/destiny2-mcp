"""周常轮换表与锚点：**官方给不了的那半**写死在这里。

三类数据别混（口径与实测见 `docs/plans/ROTATION_PLAN.md`）：

- **官方**（不在这张表里）：本周特色突袭/地牢来自 `/Destiny2/Milestones/`；
  本周夜幕/宗师（连词缀与掉落）来自 profile 组件 204 的 `availableActivities[]`；
- **本文件的表**：上维挑战（6 周循环）、异域任务轮换（7 周循环）、泉源（每天 攻击/防御 交替）、
  遗失区域（31 个地点的顺序）—— 官方接口一个都不给（证据见计划 §2.6）。

锚点规矩：每条表都写清"哪一周 = 列表里第几条"（`anchor_week_start_utc` + `anchor_index`），
连同 `verified_at` / `verified_against`。**游戏已停更、周期不再变化**，所以核对一次即长期有效；
**没核对过的（遗失区域）一律不猜**，改用 `anchored=False` 如实上报。

时间口径：重置点是 **17:00 UTC**（周二为每周重置）。截图上的日期是北京时间（UTC+8）的次日凌晨，
所以"9/16"对应的重置点是 `2026-09-15T17:00:00Z`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 每日/每周重置的小时（UTC）。北京时间 = 次日 01:00。
RESET_HOUR_UTC = 17
SECONDS_PER_DAY = 86_400
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


@dataclass(frozen=True)
class WeeklyRotation:
    """一周一换的轮换：candidates 按顺序循环，锚点那一周显示 `candidates[anchor_index]`。"""

    key: str
    label: str
    candidates: tuple[str, ...]
    anchor_week_start_utc: str
    anchor_index: int
    verified_at: str
    verified_against: str
    note: str = ""

    def index_at(self, week_start: datetime) -> int:
        anchor = _parse(self.anchor_week_start_utc)
        weeks = (week_start - anchor).days // 7
        return (self.anchor_index + weeks) % len(self.candidates)

    def name_at(self, week_start: datetime) -> str:
        return self.candidates[self.index_at(week_start)]

    def upcoming(self, now: datetime, *, weeks: int = 2) -> list[tuple[datetime, str]]:
        first = week_start(now)
        return [(first + timedelta(days=7 * i), self.name_at(first + timedelta(days=7 * i)))
                for i in range(max(1, weeks))]


# ── 上维挑战：6 周循环 ───────────────────────────────────────────────────
# 名字来自游戏内轮换页（Manifest 里只有 lore/记录的描述提到这些名字，**不是字段**），
# 顺序与锚点来自用户提供的截图：8/12 衔尾蛇 → 8/19 失却神殿 → 8/26 破碎废墟 → 9/2 锋刃要塞
# → 9/9 权威深渊 → 9/16 辛梅里安卫戍营 → 9/23 又回衔尾蛇。
ASCENDANT_CHALLENGE = WeeklyRotation(
    key="ascendant_challenge",
    label="上维挑战",
    candidates=("衔尾蛇", "失却神殿", "破碎废墟", "锋刃要塞", "权威深渊", "辛梅里安卫戍营"),
    anchor_week_start_utc="2026-09-15T17:00:00Z",
    anchor_index=5,
    verified_at="2026-09-21",
    verified_against="用户提供的游戏内轮换页截图（8/12 衔尾蛇 … 9/16 辛梅里安卫戍营 … 9/23 衔尾蛇）",
    note="名字是游戏内显示名（社区口径），不是 Manifest 字段。",
)

# ── 异域任务轮换：7 周循环 ───────────────────────────────────────────────
# 截图：9/9 安可 → 9/16 凯尔之陨 → 9/23 前兆 → 9/30 暗屋之声 → 10/7 行动：炽天使之盾
# → 10/14 //节点.超控.阿瓦隆// → 10/21 苦命鸳鸯。
# 只存名字、**不存活动 hash**：同一个任务在 Manifest 里有多个历史版本 hash，
# 挑错比不给更糟（要链到活动查询时，让服务按名字去 Manifest 里精确找）。
EXOTIC_MISSION = WeeklyRotation(
    key="exotic_mission",
    label="异域任务轮换",
    candidates=("安可", "凯尔之陨", "前兆", "暗屋之声", "行动：炽天使之盾",
                "//节点.超控.阿瓦隆//", "苦命鸳鸯"),
    anchor_week_start_utc="2026-09-15T17:00:00Z",
    anchor_index=1,
    verified_at="2026-09-21",
    verified_against="用户提供的游戏内轮换页截图（9/9 安可 … 10/21 苦命鸳鸯）",
)

WEEKLY_ROTATIONS: tuple[WeeklyRotation, ...] = (ASCENDANT_CHALLENGE, EXOTIC_MISSION)

# ── 泉源：每天在「攻击 / 防御」之间交替 ─────────────────────────────────
# 截图日列表（北京时间）：9/16 攻击 → 9/17 防御 → … → 9/22 攻击 ⇒ 重置点 09-15T17:00Z = 攻击。
WELLSPRING_MODES: tuple[str, ...] = ("攻击", "防御")
WELLSPRING_DIFFICULTIES: tuple[str, ...] = ("标准", "专家", "大师")
WELLSPRING_ANCHOR_DAY_UTC = "2026-09-15T17:00:00Z"
WELLSPRING_VERIFIED_AT = "2026-09-21"
WELLSPRING_VERIFIED_AGAINST = "用户提供的游戏内轮换页截图（9/16 攻击 → 9/22 攻击，逐日交替）"

# ── 遗失区域：31 个地点，**顺序表还没锚定** ──────────────────────────────
# Manifest 里 101 条遗失区域活动（专家/大师/传说变体 + 历史重复 hash）去重成 31 个地点；
# 但"今天轮到哪个"官方不给、也还没从游戏里核对过 ⇒ 本模块只给候选名单，不猜顺序。
# 核对方式：在游戏里看一眼今天的传说/大师遗失区域，把地点名报出来，再补 anchor。
LOST_SECTOR_LOCATIONS: tuple[str, ...] = (
    "K1后勤区", "K1启示区", "K1员工宿舍", "K1通讯区", "地堡E15", "墓冢", "天空码头IV", "惊颤竞速",
    "拾荒者巢穴", "挖掘区XII", "星光大殿", "水培区", "永劫地狱", "汇流", "消息，第一部分",
    "消息，第二部分", "消息，第三部分", "溪谷迷宫", "溺亡之愿海湾", "破碎深渊", "空坦克", "繁盛深渊",
    "萃取地", "蜕变", "裂痕", "远日点之栖", "遗忘深渊", "采石场", "镀金箴言", "隐匿虚空",
    "黑色移民号花园2A",
)
LOST_SECTOR_TOTAL = len(LOST_SECTOR_LOCATIONS)
# 31 = Manifest 里 101 条遗失区域活动按地点去重后的数量（含专家/大师/传说变体）。
LOST_SECTOR_ANCHORED = False
LOST_SECTOR_VERIFIED_AT = ""
LOST_SECTOR_VERIFIED_AGAINST = ""


def week_start(now: datetime) -> datetime:
    """本周的重置点（周二 17:00Z；`now` 落在哪一周就算哪一周）。"""
    moment = now.astimezone(timezone.utc)
    shifted = moment - timedelta(hours=RESET_HOUR_UTC)
    start = shifted - timedelta(days=(shifted.weekday() - 1) % 7)
    return start.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)


def day_start(now: datetime) -> datetime:
    """本"游戏日"的重置点（17:00Z；17:00Z 之后算新的一天）。"""
    moment = now.astimezone(timezone.utc)
    start = moment.replace(hour=RESET_HOUR_UTC, minute=0, second=0, microsecond=0)
    if moment < start:
        start -= timedelta(days=1)
    return start


def wellspring_mode(day: datetime) -> str:
    """这一天泉源是「攻击」还是「防御」。"""
    anchor = _parse(WELLSPRING_ANCHOR_DAY_UTC)
    days = (day_start(day) - anchor).days
    return WELLSPRING_MODES[days % len(WELLSPRING_MODES)]


def wellspring_upcoming(now: datetime, *, days: int = 2) -> list[tuple[datetime, str]]:
    first = day_start(now)
    return [(first + timedelta(days=i), wellspring_mode(first + timedelta(days=i)))
            for i in range(max(1, days))]


def week_stamp(moment: datetime) -> str:
    """给响应用的 ISO 时间戳（UTC，秒级）。"""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
