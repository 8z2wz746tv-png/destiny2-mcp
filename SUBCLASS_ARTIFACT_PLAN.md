# 计划：换子职业元素 + 神器模组（修复两个做不到的能力）

状态：**待评审**（未开工）。目标版本：0.3.0（含破坏性/新增能力）。
本文只记事实、决定与验证方式；实现细节落到代码与注释里。

## 一、两个症状

1. **术士从棱镜切火，切不了**（"把子职业换成烈日"这类请求失败）。
2. **神器模组换不了**（`subclass_assistant(intent="equip_artifact_mod")` 装不上，除了极少数情况）。

## 二、已确认的事实（有出处）

### 2.1 游戏规则：元素属于"子职业物品"，不是插槽

- DIM `src/app/inventory/subclass.ts`：元素从**装备的那件子职业物品**读（`item.element.enumValue`，
  棱镜=Kinetic、火=Thermal、虚空=Void、冰=Stasis、缚丝=Strand），图标也按元素取。
- DIM `src/app/inventory/store/override-sockets.ts`：插槽写入模型是 `SocketOverrides = {socketIndex: plugHash}`，
  即**每个插件带着自己的槽索引**去插，没有"默认槽"这回事。

### 2.2 我们的代码为什么做不到

| 症状 | 根因（位置） |
| --- | --- |
| 换元素 | `services/subclass_service.py:258 modify_subclass` 只会把 plug 插进**当前子职业**的槽。拿"火的超能/近战"往棱镜的槽里插，上游必然拒 |
| 换元素（另一条路） | `services/loadout_subclass_sockets.py:150-160`：一旦"当前子职业 ≠ 保存/确认的子职业"就**直接失败**（`action="subclass"`, `detail="当前子职业与保存/确认的子职业不一致。"`）——这条路上也没有"换过去"的动作 |
| 神器模组 | `services/artifact_service.py` 装模组时 **`socket_index=0` 写死**。神器是按模组分槽的一排插槽，只有"恰好落在 0 号槽"的模组能成功 |

### 2.3 DIM 的正确做法（神器）

DIM `src/app/loadout/ArtifactPlugDrawer.tsx`：

```js
for (const socket of artifact.sockets.allSockets) {
  if (!socket.plugSet && socket.socketDefinition.socketTypeHash !== RESET_SOCKET) continue;
  const matchingPlug = remainingPlugs.find((plug) =>
    socket.plugSet?.plugs.some((option) => option.plugDef.hash === plug.hash));
  if (matchingPlug) newOverrides[socket.socketIndex] = matchingPlug.hash;  // 槽索引是算出来的
}
```

即：**遍历神器的槽，找"候选插件里包含目标模组"的那个槽**，用它自己的 `socketIndex` 去插。

## 三、目标与非目标

**目标**：把这两件事做成"能真做、做完能核对、失败说得清"的能力。

**非目标**：
- 不做"一键换整套子职业 DIM 式预设"（那是 loadout 的活，已存在）；
- 不碰武器/护甲的插槽写入路径（它们已经工作正常）；
- 不为旧行为留兼容分支。

## 四、分阶段

### P0 只读勘探（不改账号）

- 用项目自己的 `services/profile_components.py` 常量取档（**不要手写组件号**：第一次勘探我用了猜的
  205/305/310，被上游拒了），读出：
  - 术士当前神器实例的 socket 列表（组件 305）与每槽候选（组件 310）；
  - 角色子职业 bucket 的物品清单（每个元素的子职业物品 hash + 实例 ID + 是否装在身上）；
  - 一个具体模组 hash 落在哪个槽的候选里。
- 产出：写进本文档「实采记录」一节（槽位数量、索引与模组对应关系）。

### P1 神器模组：按 hash 找槽

- `artifact_service.equip_artifact_mod`：删掉 `socket_index=0`，改成
  "在该神器实例的候选里找包含这个模组 hash 的槽 → 用那个索引插入"；
- 找不到就**如实失败**：`invalid_argument_error` + 说清"这个模组不在当前神器的候选里（可能未解锁或不属于本赛季）"；
- 插入后**回读核对**：重新读 305，确认该槽 `plugHash == 目标 hash`，再报成功；
- 测试：单测（用夹具的 socket 结构，覆盖"命中/未命中/多槽候选"三种）+ 真机语料行（装一个真模组 + 回读）。

### P2 换子职业元素：装备另一件子职业物品

- `subclass_assistant(intent="modify")` 新增一个变更键（见「待决定」）：
  `changes={"subclass": "烈日"}` → 解析成子职业物品（元素/中英文名都认）→ 从角色背包找到该物品**实例**
  → 走既有 equip 流程装上 → **然后**才按新子职业的插槽应用同一请求里的其它 plug 变更（顺序有要求：
  先换物品，再改槽，因为旧物品的槽索引在新物品上没有意义）；
- `loadout_subclass_sockets._apply_subclass_config` 的"子职业不一致就失败"改成"先换再配"；
- 换完回读：`subclass get` 的元素/子职业名必须与请求一致，插槽变更逐项核对；
- 测试：单测（元素解析、找不到该元素物品、不是该角色/不在背包）+ 真机语料行
  （术士切烈日 → 回读 → 切回棱镜 → 回读）。

### P3 收敛与登记

- 参数契约：`tools/_param_contracts.py` 登记新键的归属；`changes` 的合法键加入校验；
- 文档：`skills/destiny2-mcp/references/routing.md`（契约测试会校验）、README 的能力表；
- 语料：`TESTING_CORPUS_FULL.md` 加两行；`run_corpus_all_rows.py` 的 rows 组加断言；
- `COMPATIBILITY.md`：新键是**新能力**（不是别名），登记一句；旧行为（"子职业不一致就失败"）直接删除，
  不留兼容；
- 版本：0.3.0（新增能力 + 行为变更），CHANGELOG 一段。

## 五、验证方式（每阶段都要）

| 阶段 | 验证 |
| --- | --- |
| P0 | 只读，无账号变更；产出实采记录 |
| P1 | 单测 + 真机：装一个模组 → 回读 305 确认 → （可选）换回原模组 |
| P2 | 单测 + 真机：术士切烈日 → 回读 → 切回棱镜 → 回读；每次写入前后各读一次 |
| P3 | `pytest -q` + `verify_mcp.py` + `run_corpus_all_rows.py --group rows` |

**写入纪律**：这两件事都会改账号，但都**可逆**（模组可换回、子职业可切回）。执行前逐次说明"要改什么"，
得到你明确同意再写；每次写完回读核对并报告。

## 六、风险与边界

- **神器是赛季制**：赛季切换后模组集合会变，未解锁的模组本来就插不上——失败信息要区分"未解锁"与"槽不存在"；
- **神器重置槽**（DIM 里的 `RESET_SOCKET`）：重置是**付费/消耗**操作，本次**不实现**，遇到就如实说；
- **子职业物品必须在背包/身上**：不在的话要先取回（`pull_postmaster`/`move`），不能凭空造；
- **顺序**：换子职业物品之后再改槽；反过来会拿旧槽索引改新物品，必然错；
- **上游权限**：神器的付费操作可能需要 `AdvancedWriteActions`（我们目前没开），免费插入不受影响。

## 七、待决定（需要你拍板）

1. **换元素的键名**：`changes={"subclass": "烈日"}` 还是 `{"element": "solar"}`？我倾向 `subclass`（中文用户说"换成烈日"）；
2. **是否允许真机写入验证**（P1/P2 各一次，均可逆）；
3. **你遇到时的报错原文**（哪个 intent、`code`/`message`）——能帮我确认是不是还有第三个原因（比如权限或未解锁）。

## 实采记录

（待填：P0 完成后写这里——神器槽位数、索引↔模组对应、子职业物品清单）
