# ADR-004: 组件号只能来自 profile_components.py，读插槽必须带清单类组件

- Status: accepted
- Date: 2026-09-17
- Decision By: 用户（Husky）
- Scope: `services/profile_components.py`、所有调用 `get_profile` 的地方、`tests/test_profile_components.py`

## Context

Bungie 的 profile 接口按"组件号"取数据（`DestinyComponentType`）。这个项目为此有过一次
专门收拢（17 处字面量、集合还各不相同），落成 `services/profile_components.py`。
但收拢只解决了"同一个集合写很多遍"，没解决**"写错了会静默读空"**：

真机实测（2026-09-16/17）：**只请求组件 `305`（itemSockets）时，上游一个插槽都不返回** ——

| 请求的组件 | 返回带插槽的实例数 |
| --- | --- |
| 只要 `[305]` | **0** |
| `ARMOR_SNAPSHOT`（含 102/200/201/205/300） | **1627** |

后果极其隐蔽：插槽读取拿到空数据 → "找不到模组 X 的唯一兼容插槽" → 看起来像**匹配逻辑写错了**，
于是排查方向一开始完全跑偏（真机上耗掉了一整轮）。同类坑还有两处：缓存里存着**一条空列表**
被当成"这件装备的插槽就是空的"；以及"请求组件"这件事本身没有被任何守门测试盯住。

**What changed** —— 原来只有"禁止裸组件号字面量"的扫描（且只覆盖部分模块、只认几个 token）；
现在把"要 305 就必须带清单类组件"也变成硬约束。

## Decision

1. **组件号只有一处**：`services/profile_components.py` 的命名集合。服务里**不允许**出现裸组件号；
   要新的组合就在那个文件里加名字、写清"谁需要它、用来干什么"。
2. **读插槽必须带清单类组件**：任何请求 `305` 的 `get_profile` 调用，必须同时带
   `102/200/201/205` 之一（项目里用 `INVENTORY_SOCKETS` / `ARMOR_SNAPSHOT` / `SUBCLASS`）。
   只给 `305` 会静默读空。
3. **空列表 ≠ 没有插槽**：缓存里遇到空插槽要**重读**（并带同步窗口重试），
   不能当成"这件装备没有插槽"进而报"找不到兼容插槽"。
4. **守门测试**（`tests/test_profile_components.py`）：
   - 命名集合的内容被逐个钉住（改了集合必须改测试，改的人得回答"这个调用点到底要不要 304"）；
   - 服务里不许出现裸组件号；
   - **要 305 就必须带清单类组件**（违反 → 红，带文件:行号）。
5. **不允许**：在 service 里直接开 sqlite 查桶定义来绕开组件表；把"读不到"当成"没有"。

被否掉的方案：① 给 `get_profile` 包一层"自动补全组件"的兜底（会让"到底请求了什么"不可见，
掩盖上游行为差异）；② 只写文档提醒（这次的代价就是"没守住"）。

## Consequences

- 新增组件需求要动 `profile_components.py` + 钉桩测试，多一步 —— 这是刻意换来的"读空了会红"。
- `INVENTORY_SOCKETS` 这类集合会带上比单个需求更多的组件（响应更大一点），
  换的是"不会因为少带一个组件而静默读空"。
- 桶容量（`DestinyInventoryBucketDefinition.itemCount`）走 Manifest 而不是 profile，
  它**不是**组件问题，别混进来（用 `to_signed()` 查，见 `docs/reference/bungie_api.md`）。
