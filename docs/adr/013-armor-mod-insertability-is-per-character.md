# ADR-013: 能不能插一颗护甲模组，看角色级的可插入清单，不看 Manifest 的 plug set

- Status: accepted
- Date: 2026-09-21
- Decision By: maintainer
- Scope: `destiny_mcp/services/loadout_plug_lookup.py`、`loadout_mod_sockets.py`、`armor_mod_service.py`、`loadout_equipment_service.py`

## Context

用户报"除了一般护甲模组（属性模组），护甲上剩下的模组槽（手臂护甲模组等）装不了，配装里带的
其他模组同样装不了"。按上次的思路（字段名映射 / 有符号 hash）排查，真机结果是**这次不是格式 bug**：

- `insert_socket_plug_free` 对部位模组一视同仁。实测 6 类插槽各做一次"同类别、能量不增的一换一 →
  回读 → 换回"，**6/6 全部成功**（`scripts/verify_armor_mod_sockets.py --apply`，2026-09-21）：
    一般 / 头盔 / 手臂 / 胸甲 / 腿部 / 职业物品。
- 真正被拒的是**这一位还没解锁**的那几颗：上游回 **1676** `DestinyFailedPlugInsertionRules`
  （"The requirements have not been met."），`message_data` 是空的、不说是哪条没过。Manifest 的
  `plug.insertionRules[].failureMessage` 里写着条件，被拒的那四颗里都有「**必须在赛季神器中选择**」。
- **能被插入的清单在上游手里**：组件 207 `characterPlugSets`（随 305 一起回来，不用单独请求）
  给出这一位角色实际能插的 plug。实测头盔 plug set Manifest 有 61 颗、这一位只有 21 颗；
  手臂 25/56；一般模组 9/24。被 1676 拒掉的那四颗，全都不在这份清单里；而当场写通的那些全都在。
- 我们以前只拿 Manifest 的 `reusablePlugItems` 当"这个槽接受哪些模组"，于是：把没解锁的模组
  当能装，让用户确认完才失败；同名模组有"已解锁/未解锁"两档时按属性加成挑，可能正好挑到没解锁那档。
- `equip_loadout` 还把 1676 归成"Bungie 不允许 API 改护甲模组，请游戏内手动装" —— 游戏里同样装不上，
  这句是错的（它来自 0.4.x 那次"按能量消耗选付费接口"的错误，见 ADR-002 / ADR-012）。

**What changed**：ADR-002 说"护甲模组必须游戏内装"，ADR-012 推翻了其中的写入格式部分；
这条补上最后一半 —— **能写，但只能写"这一位解锁了的"**。"装不了"的三种原因（没解锁 / 非免费可逆 /
角色状态）以前被糊成一句，现在各归各的。

## Decision

1. **单一出处**：`PlugLookupMixin.insertable_plugs(profile, character_id)` 是"这一位能插哪些 plug"
   的唯一读法，`profilePlugSets`（profile 级）与 `characterPlugSets`（角色级）**都要并** ——
   解锁跟着角色走（神器在角色身上），profile 级那份更小、代替不了。
2. **缺数据 ≠ 不允许**：上游没给某个 plug set 时返回**缺键**，`plug_is_insertable` 返回 `None`。
   `None`（没数据，不判断）与 `False`（给了、里面没有）在任何调用方都必须分开处理；
   把"没查到"读成"装不了"是本项目反复踩过的坑。
3. **已经装在槽里的那颗永远算能插**：上游那份清单会漏（实测调谐槽里正装着的那颗就不在清单里），
   而"插回原值"是游戏里一定允许的动作。
4. **同名优先挑已解锁的那档**：`ArmorModService.plan` 的候选排序第一位是"可插入"，
   属性加成与能量成本往后排。挑不到已解锁的就**不写**：`writable=false` + `writable_reason`
   带上 Manifest 给的插入条件，让用户先解决条件。
5. **1676 不许说成"去游戏里装"**：`mod_write_blocker` 按三种原因分开措辞 ——
   1676 = 插入条件没满足（游戏里同样装不上）、403 = 付费接口要 AWA（本项目没实现）、
   1663 = 上游含糊话术（原文照转，不替它下结论）。
6. **配装预检也算这一步**：`_prepare_mod_operations` 拿到可插入清单时，在**写之前**就报错并带上条件，
   而不是写到一半让上游拒。

被否掉的方案：

- **只信 Manifest 的 plug set**（现状）：它回答"这个槽能接受哪些模组"，不回答"这一位能不能插"，
  两者差 40 颗（头盔）。这条正是本次 bug。
- **拿一次真实写入当探针**来判定能不能插：会改账号状态，且失败也可能已经写进去一部分。
- **把 1676 归成"必须游戏内操作"**：用户进游戏一样装不上，只会白跑一趟。

## Consequences

- 判定依赖响应里的 207 组件。目前它随 305 一起回来，**不额外请求、不额外耗时**；上游哪天不再附带，
  表现是退回 `None`（不判断、照旧尝试），**不会**变成"全部装不了" —— 这是刻意的降级方向。
- `equip_mod` 的响应多了 `to.unlock_state` / `to.conditions` / `alternatives[].unlock_state`；
  配装执行把 `mod_in_game` 步骤改名为 `mod_blocked` 并带原因（旧键不保留，见 `docs/COMPATIBILITY.md`）。
- 调谐仍在"不代改"那一类，但**理由换了**：不再是"上游只允许游戏内改"（那句出自被推翻的 1663），
  而是"本项目没验证过"。实测免费接口能寻址调谐槽（原样重插回的是 1679 `DestinySocketAlreadyHasPlug`），
  真要放开得先做一次可逆的真机验证。
- 要改这条决定，得同时改：`tests/test_armor_mod_unlock.py`（20 条守门）、
  `docs/reference/bungie_api.md` 的 1676/207 条目、`scripts/verify_armor_mod_sockets.py` 的判定口径。
