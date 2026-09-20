"""账号 profile 组件号：一份定义，所有服务共用。

Bungie 的 profile 接口按"组件号"取数据，某个服务要哪几个组件以前是各自写死的
（17 处字面量，集合还不一样：有的要 305、有的不要）。结果就是同一个概念换了地方
就得改一次，还容易漏 —— 武器服务少了 310（能换的 perk）就是这样丢的。

这里只做命名与注释，不改任何服务的取数行为；要加组件时改这一处，并在注释里写清
"谁需要它、用来干什么"。

组件号对照（Bungie DestinyComponentType）：
- 102 profileInventory、200 characters、201 characterInventories、205 characterEquipment
- 206 characterLoadouts（官方配装的槽位定义）
- 300 itemInstances（光等、`gearTier`、itemLevel、quality）
- 302 itemPerks（Bungie 算好的展示 perk）
- 304 itemStats（当前属性值）
- 305 itemSockets（已装 plug；写入后的回读也读它）
- 308 itemPlugObjectives（催化剂/击杀进度）
- 310 itemReusablePlugs（**这一件副本**能换的 plug —— T 级决定的那个数）
- 1100 metrics（游戏内生涯计数器，如"熔炉生涯击败"；**不在 item 家族里**）
"""

from __future__ import annotations

# 装备插槽：读「这件东西现在装着什么」——模组、perk、子职业碎片、神器模组都靠它。
# 写入之后要回读核对，用的也是它（写进去的 plug 只有这里能看到）。
ITEM_SOCKETS: list[int] = [305]

# 物品 + 光等/属性：库存概况、移动、精算等只读基础查询
INVENTORY: list[int] = [102, 200, 201, 205, 300, 304]

# 同上但不要 304（历史行为：只要"有哪些东西"、不看属性值）
INVENTORY_MINIMAL: list[int] = [102, 200, 201, 205, 300]

# 要插槽但不要 304 —— 注意这和 ARMOR_SNAPSHOT 不是一个集合，别互相替换
INVENTORY_SOCKETS: list[int] = [102, 200, 201, 205, 300, *ITEM_SOCKETS]

# 护甲快照：属性值 + 插槽（模组要写进插槽，所以两个都要）
ARMOR_SNAPSHOT: list[int] = [102, 200, 201, 205, 300, 304, *ITEM_SOCKETS]

# 武器详情/按类型列武器：已装 plug + 能换的 plug + Bungie 的展示 perk + 催化剂进度
WEAPON_DETAIL: list[int] = INVENTORY + [*ITEM_SOCKETS, 302, 310, 308]

# 官方配装槽位的最小集合（loadout_service 只需要槽位定义）
LOADOUT_SLOTS: list[int] = [102, 200, 201, 205, 206]

# 赛季神器（100 = profileProgression，302 = itemPerks）
ARTIFACT: list[int] = [100, 102, 200, 201, 205, 300, 302]

# 子职业：205 看正装备的那件、305 看它装着什么，201 是"这个角色还有哪些子职业物品"——
# **换子职业**必须有 201（实采：一个角色背包里躺着该职业全部子职业，bucketTypeHash=3284755031、
# itemType=16）；只看 205 的话永远只看得见正装着的那一个，换不了。
SUBCLASS: list[int] = [200, 201, 205, *ITEM_SOCKETS]

# 游戏内生涯计数器（1100 = profileMetrics）：读的是
# `Response.metrics.data.metrics[metricHash].objectiveProgress`，形状是「每个 statId 一个条目」，
# 和装备组件毫无关系，所以**不要**把它并进 INVENTORY/WEAPON_DETAIL 那几套里
# （多取一个组件 = 响应更大、更慢，而计数器只有生涯计数一个用途）。
#
# 谁需要它：activity_counters_service（`activity_assistant(intent="counters")`）——
# 用户问"我在游戏里显示的 PvP 击败是多少"时，只有这里给得出游戏内那个数
# （统计接口给的是另一套口径，见 docs/reference/bungie_api.md「Metrics vs Stats」）。
#
# 注意：真机实测这个组件会**整块缺失**（同一 URL 连续请求会返回 0 条，重试后恢复），
# 所以读取方必须重试，拿不到要如实报不可用，绝不能把"空"当成 0。
METRICS: list[int] = [1100]

# 锻造图样进度（900 = profileRecords）：读的是
# `Response.profileRecords.data.records[记录hash].objectives[0].progress / completionValue`
# —— 就是游戏里那条「图样进度 4/5」。
#
# 谁需要它：pattern_service（`weapon_assistant(intent="patterns")`）。**不要**并进 FULL：
# 实测 1.44 MB / 2.5 s，而图样是它唯一的用途；另外两条候选都给不出进度 ——
# 组件 800（收藏品）里图样解锁状态一条都没有，1300（Craftables）只回答"能塑形哪些 perk"
# （219 条 `visible` 全为 true）。实测见 docs/plans/PATTERN_QUERY_PLAN.md。
PATTERNS: list[int] = [900]

# 缓存里一次取全：读多写少的场景共用（profile_cache）。
# 必须覆盖所有调用方要的组件（含 308 催化剂进度），否则后台刷新会把并集降级、
# 下一次调用又要重新拉一遍 10 MB 的 profile。
FULL: list[int] = [102, 200, 201, 205, 300, 302, 304, *ITEM_SOCKETS, 308, 310]


def describe(components: list[int]) -> str:
    """给日志/报错用的一行说明（排查"这次为什么没拿到 310"时很有用）。"""
    return ",".join(str(component) for component in components)
