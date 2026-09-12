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
- 305 itemSockets（已装 plug）
- 308 itemPlugObjectives（催化剂/击杀进度）
- 310 itemReusablePlugs（**这一件副本**能换的 plug —— T 级决定的那个数）
"""

from __future__ import annotations

# 物品 + 光等/属性：库存概况、移动、精算等只读基础查询
INVENTORY: list[int] = [102, 200, 201, 205, 300, 304]

# 同上但不要 304（历史行为：只要"有哪些东西"、不看属性值）
INVENTORY_MINIMAL: list[int] = [102, 200, 201, 205, 300]

# 要插槽但不要 304 —— 注意这和 ARMOR_SNAPSHOT 不是一个集合，别互相替换
INVENTORY_SOCKETS: list[int] = [102, 200, 201, 205, 300, 305]

# 护甲快照：属性值 + 插槽（模组要写进插槽，所以两个都要）
ARMOR_SNAPSHOT: list[int] = [102, 200, 201, 205, 300, 304, 305]

# 武器详情/按类型列武器：已装 plug + 能换的 plug + Bungie 的展示 perk + 催化剂进度
WEAPON_DETAIL: list[int] = INVENTORY + [305, 302, 310, 308]

# 官方配装槽位的最小集合（loadout_service 只需要槽位定义）
LOADOUT_SLOTS: list[int] = [102, 200, 201, 205, 206]

# 赛季神器（100 = profileProgression，302 = itemPerks）
ARTIFACT: list[int] = [100, 102, 200, 201, 205, 300, 302]

# 缓存里一次取全：读多写少的场景共用（profile_cache）。
# 必须覆盖所有调用方要的组件（含 308 催化剂进度），否则后台刷新会把并集降级、
# 下一次调用又要重新拉一遍 10 MB 的 profile。
FULL: list[int] = [102, 200, 201, 205, 300, 302, 304, 305, 308, 310]


def describe(components: list[int]) -> str:
    """给日志/报错用的一行说明（排查"这次为什么没拿到 310"时很有用）。"""
    return ",".join(str(component) for component in components)
