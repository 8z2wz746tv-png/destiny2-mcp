# ADR-020: 功能模组照抄社区配装，不进求解器；求解器只让出它们的能量

- Status: accepted
- Date: 2026-09-25
- Decision By: maintainer
- Scope: `destiny_mcp/build/functional_mods.py`、`destiny_mcp/services/build_service.py`、`destiny_mcp/services/loadout_functional_mods.py`、`destiny_mcp/services/loadout_energy_budget.py`、`destiny_mcp/models/loadout.py`

## Context

**What changed**：一件护甲的能量要同时养活两类模组 —— 槽 0 的**六维属性模组**（+10 武器/手雷…）
与槽 1–3 的**部位功能模组**（抗性/弹药搜寻/回收/吸引…）。在此之前：

- 属性模组由求解器算（`stat_mods`），**它以为整件护甲的能量都能拿来装属性模组**，
  功能模组只按"当前装着的那些"扣一个固定开销（`energy_used_by_other_mods`）；
- 社区模板里作者写的那 15 颗功能模组（每件 3 颗）**既没核对、也不代装**：
  它们被解析成 `armor_mod` 要求后，在账号核对那一步整类落到
  `inventory_status="not_account_checked"` + `unverifiable_reason="mod_unlock_state_not_available"`
  （`account_check_exclusions` 里明写"mod and artifact unlocks"没查）。

用户 2026-09-25 的判断是这条线的分界：**"功能模组不进求解器，进了你也解不出来 —— 本来就是让功能模组
直接照抄社区配装的，进求解有什么意义，这玩意又不是能算出来的。"** 同时他要求先做一次写入验证。

## Decision

1. **功能模组照抄，不进求解器。** 求解器永远不挑功能模组、不做取舍；它只被告知"这些能量别动"，
   然后在**剩下的**能量里解属性模组（`reserved_mod_energy` 按部位给，快照取
   `max(已装的部位模组, 预留额度)` —— 少算的后果是属性模组装不下并回退，多算只是少用一点能量）。
2. **同名的多个版本全留着**（执行时按"这一位能不能插"挑，其次挑便宜的）；预留能量取同名版本里
   **最贵**的那颗，保证执行器挑中的一定不超预算。
3. **认不出来的一律如实报**（`unresolved`）：Manifest 对不上名（实测社区模板里「回收利用」就对不上）、
   名字是属性模组（那半边必须由求解器给）、同名跨多个部位却没说部位。
4. **执行时插不进/装不下 → 跳过 + 点名**，不拆玩家别的模组、不许让整条配装失败或回退；
   已经装着的不写（上游会对"再装一次"回 1679）。
5. **只有功能模组可以照抄**：武器、六维、装备、子职业仍然必须走我们的求解与核对，
   社区模板依旧不是可执行凭据（ADR-001 与 `COMMUNITY_TEMPLATE_NOT_EXECUTABLE` 不变）。

被否掉的选项：

- **把功能模组也交给求解器挑**（按抗性/搜寻的重要性打分）：那是流派取向，不是数值问题；
  用户要的是"照抄作者这套"，不是"我们替他重新设计一套"。
- **让执行器先写功能模组、再重解属性模组**（两遍求解）：要多花一整轮求解（真机 50–90 秒）
  和一次新的确认，而"先扣能量再解"一遍就能得到同一个结果。
- **照抄模组装不下就整条失败**：作者那套 15 颗能量很凶（真机实测每件 7–9 点 / 上限 11），
  整条失败等于"这个功能碰不得"；跳过并点名才是可用的。

## Consequences

- 真机验收（2026-09-25，hunter）：3 颗照抄模组 → `find` 28.7 秒出 3 套候选（摘要写明"含照抄社区配装的
  3 颗功能模组，已从六维求解里扣掉它们的能量"）→ 确认信封逐件列出 `copied_from_template: true`
  → `equip_build` 93.5 秒写入，`steps` 里逐颗写中文名：`模组 '特殊武器弹药搜寻者' → '至高狂徒面具'`、
  `模组 '震荡阻尼器' → '共振狂怒胸甲'`、`模组 '绝缘' → '快速装弹松身裤'`。
- **同一份模板的 15 颗全上时，那套六维目标在这位玩家的护甲上解不出来了**（预留 9/8/7/7/7 点，
  上限 11）—— 这是如实结果而不是 bug：护甲 3.0 的规则就是功能模组与六维抢同一个池子。
  回执要说清"是照抄模组吃掉了能量"，让玩家自己决定砍哪一头。
- 代价：`build_service.py` 多一处透传（体量闸上限跟着 +9），新开三个模块
  （`build/functional_mods.py` 解析、`services/loadout_functional_mods.py` 现场决策、
  `services/loadout_energy_budget.py` 从 `loadout_mod_sockets` 拆出的腾能量逻辑），三个都登记了上限。
