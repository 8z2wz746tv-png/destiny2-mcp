# ADR-002: 护甲模组只能游戏内手动装，工具侧只列清单且不回滚已换上的装备

- Status: accepted
- Date: 2026-09-17
- Decision By: 用户（Husky）／上游约束（Bungie）
- Scope: `services/loadout_mod_sockets.py`, `services/armor_mod_service.py`, `services/loadout_equipment_service.py`, `tools/_equip_branches.py`

## Context

`equip_build` 求解出来的配装常常带属性模组（例如 2× 武器模组 + 3× 手雷模组）。真机上
"装模组"这一步反复失败，最初被误判成我们自己的插槽匹配逻辑写错了（"找不到模组 X 的唯一兼容插槽"），
后来一度又以为只是接口选错（按"能量消耗"挑了付费接口 `InsertSocketPlug`）。

真机实测（2026-09-16/17，同一账号，三次对照）把结论钉死了：

| 试法 | 上游回复 |
| --- | --- |
| 付费接口 `InsertSocketPlug` | **403** `AccessNotPermittedByApplicationScope` |
| free 接口 `InsertSocketPlugFree` + 已装备护甲 | **500 / 1663** `DestinyItemActionForbidden`："This action can only be done in-game." |
| free 接口 + 未装备（背包里）护甲 | 同样 **1663** |

也就是说：**先脱下来再装也绕不过去**，接口选择不是关键，缺的是 `AdvancedWriteActions`。
而那个 scope 是 Bungie **按应用审批**的（[开发者后台](https://www.bungie.net/en/Application)
里没有自助勾选项，用户已确认看不到），个人应用基本拿不到；DIM 能做是因为 DIM 有这个 scope。
官方文档里那句"`InsertSocketPlugFree` 覆盖 Perks, **Armor Mods**, Shaders, Ornaments"是对的
（讲的是接口语义），但**它不等于"不需要权限"** —— 这一点当初被我们读成了后者。

**What changed** —— 早期结论"护甲模组能通过 API 写"被实测推翻；早期实现"模组失败即整体失败"
（会把刚换上的装备回滚）被判定为错误行为。

## Decision

1. **模组写入交给游戏内**：工具不承诺"能替你装模组"。真机上被 403/1663 挡住时，
   把它当作**已知上游限制**，返回 `mod_in_game` 步骤 + 待装清单（哪件装备、哪颗模组），
   并在话术里说清"需要你在游戏里手动装"。
2. **因此不回滚装备**：装备阶段已经成功时，模组被策略挡住**不触发回滚** ——
   回滚等于把用户要的东西又脱下来。整体 `success=false` 仍然照实返回（模组确实没装上）。
3. **接口仍按语义选**：护甲模组先走 free 接口（官方文档覆盖 Armor Mods），
   只有上游回 1663「只能游戏内做」才退回付费接口；403（scope 不够）**不重试付费接口**，
   避免把一次明确的权限失败伪装成"换个接口再试"。
4. **不允许**：假装成功；把 scope 缺失报成"稍后重试"；为了"让流程通过"而跳过模组并报 `success=true`。

被否掉的方案：① 先卸下装备再装模组再穿回去（实测同样 1663，白折腾且有风险）；
② 引导用户去后台勾 scope（后台没有这一项，只会让人白找）。

## Consequences

- 配装求解给出的六维是**含模组**的**目标值**；实际装完装备（模组未装）时六维会低一截，
  差值恰好等于那几颗模组的加成 —— 这一点必须在校验/话术里说清，否则会被读成"求解器算错了"。
- 想改成"API 也能装模组"，前提是 Bungie 给应用批 `AdvancedWriteActions`；
  那时要同时改这里、`docs/reference/bungie_api.md` 的对应条目与 `mod_in_game` 相关测试。
- 用户侧多一步手动操作，这是当前唯一诚实的选择。
