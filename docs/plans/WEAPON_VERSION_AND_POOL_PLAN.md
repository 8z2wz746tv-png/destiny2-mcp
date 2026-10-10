# 武器版本与 perk 池：默认给最新版 + 完整池

目标：解决两个真实体验问题（2026-10-10 用户报告，均已实测复现）：

1. **问 perk 池只能拿到老版本** —— 用户要新版本，工具给老版本，而且**没法指定**要哪一版；
2. **perk 池"不满"** —— 池子里只有名字，没有描述，模型答不出"这个 perk 什么效果"。

口径已拍板（用户 2026-10-10）：**A1** 名字默认取最新版 + 回执列出所有版本（模型可改选）；
**B1** 默认给完整池（含描述），大就分页。

## 一、实测证据（全部当场跑出来的，不是推断）

| 事实 | 数字 / 原文 | 怎么测的 |
| --- | --- | --- |
| **同名多版本有多普遍** | 武器定义 **2208 条 / 1318 个名字 → 580 个名字（44%）是多版本** | 直读 manifest sqlite，按 `displayProperties.name` 分组 |
| **"哪一版新"的可靠信号** | **`index`（越大越新）** —— `Judgment` 六版：**35186 > 33474 > 33466 > 32887 > 28330 > 23837**，与赛季顺序一致 | 同上；`versionNumber` 字段**不存在**（先猜的，已否） |
| **现在挑到的是哪一版** | `find_weapon("Judgment")` → `hash=-2068394602`、**index=33466** ✗（第三新；最新是 35186） | 调真服务（`app_lifespan` + `find_weapon`） |
| **搜索索引只能给一条** | `manifest.search(name, item_type=3)` 同名**只返回 1 条** —— 所以"列出所有版本"**不能靠它** | 同一个探针 |
| **perk 池的真实体积** | 玉兔：**8 个插槽 / 13 个选项 / 3.2 KB** | `perk_svc.get_weapon_perks("玉兔")` |
| **池子被砍的是"字段"不是"条数"** | 选项字段只有 `plug_hash / name / can_roll / enhanced_plug_hash` —— **没有 `description`、没有 `icon_url`**；出口**没有**条数截断（`perk_pool_payload` 不做投影、`get_weapon_perks` 无上限、`weapon_payload` 无 `[:N]`） | 读代码 + 上面那次调用的原文 |
| **开关本来就有** | `weapon_payload.socket_list(..., include_descriptions, include_icons)`；`scope=="definition"` 时**默认两个都关** | 读代码 |
| **关掉的代价（旧实测，注释里）** | 这两项曾占 **17 KB/把**（图标 **13.2** + 描述 **4.0**） | `weapon_payload.socket_list` 的 docstring（P6 实测） |
| **中文名不在武器定义表里** | 中文名多版本的武器 = **0 个** → 武器定义是**英文名**，中文名在本地化表 | sqlite 扫描（按 CJK 过滤后为空） |

## 二、口径（已拍板）

- **A1**：给名字 → **默认取最新版**（`index` 最大）；回执里**列出所有版本**（中文名 + hash + 新→旧），模型要改选就带 `item_hash` 再来一次。
- **B1**：perk 池**默认完整**（选项带 `description`）；**不**默认给图标（13.2 KB/把不值）；超阈值用**已有的** `limit`/`offset` 分页。

## 三、设计（改哪里、为什么）

### 1. 版本的唯一出处（`services/weapon_profile.py`）

- 新增 `weapon_versions(manifest, name) -> list[dict]`：**走定义表扫描**（`manifest_lookup.search_definitions_by_name` ✗ 默认 `scan_limit=5000` 会漏后段，要给足或换一条更省的查询），按 `displayProperties.name` **精确**匹配 + `itemType == 3`，用 **`index` 降序**排；每项 `{item_hash, index, name_zh, index}`。
- `find_weapon()` 改为**取第一项**（= 最新）—— 它的调用方（`perk_pool` / `info` / `stats` / `god_roll`）**一处修全部受益**。
- 名字：显示名走**本地化表**（`get_localized_definition`）取中文；取不到就退回英文名并**在回执里注明**（不编）。

### 2. 出口带版本列表（`services/perk_service.py` + `tools/_weapon_branches.py`）

- `perk_pool_payload` 的 `data` 增加 `versions`（新→旧，含 `is_default` 标出哪一版是默认）；
- `summary` 里点一句"这是第 N 版（最新的那版）"，多版本时说明"还有 M 个旧版本，要哪版传 `item_hash`"。

### 3. 新增 `item_hash` 参数（契约变更）

- 工具面加 `item_hash: int`（定义级武器意图：`perk_pool` / `info` / `stats` / `god_roll` / `catalog` 视情况）；
- 归属写进 `tools/_param_contracts.py`（唯一出处），跑 `--write-doc` 更新 `routing.md`；
- `docs/COMPATIBILITY.md` 记一行（新增参数，非破坏性）；`docs/adr/` 写一条（"默认最新版 + 可指定 hash"是**对外契约**的决定，连同被否掉的 A2）。

### 4. 池子给全（`tools/_weapon_branches.py`）

- `perk_pool` 那条调用传 `include_descriptions=True`（图标仍关）：**一行**；
- 分页：`perk_pool` 认领**已有的** `limit` / `offset`（默认值待 §四 量完再定）。

## 四、落地前必须先量的两条（不许猜）

1. **定义表全扫耗时** —— 扫描是"每次调用都做"还是"要加缓存"，取决于这个数。
   - 若 > ~0.5 s：加一层进程内缓存（键 = 名字），或改成只扫 `DestinyInventoryItemDefinition` 的**索引列**。
2. **带描述的池子体积** —— 玉兔 3.2 KB → 带描述后多少（预估 +4 KB/把 ✗ 待量）。
   - 用它定 `limit` 默认值（例如"一页 40 个选项"）。

## 五、守门与验收

- **守门**（每个都注入一次确认会咬人）：
  - 多版本名必须取**最新**：夹具用实测的 `Judgment` 六版 index 顺序；
  - 回执必须带 `versions` 且新→旧、且标出默认那版；
  - `item_hash` 指定哪版就答哪版（拿旧版 hash 问，返回的池子必须来自旧版）；
  - perk 池选项必须带 `description`（**不许**静默退回无描述）；
  - 分页：`limit`/`offset` 生效且 `truncated` 如实标。
- **真机**：拿 `Judgment`（六版）问一次 perk 池 → 回执里默认版必须是 35186 那一版；再拿旧版 hash 问一次 → 池子必须不同；顺带记下带描述的载荷大小。

## 六、代价与风险（如实记）

- **体积**：perk 池每把 +≈4 KB（描述）；一次问多把会累积 —— 分页是兜底。
- **扫描成本**：定义表扫描是新的开销路径；**没量之前不下结论**。
- **契约**：新增 `item_hash` 属非破坏变更，但"默认最新版"会**改变已有调用的结果**（同一句问话现在给新版本）—— 这正是用户要的，但要写进 CHANGELOG 与 ADR。
- **不做**：不记忆"用户上次选的那版"（跨调用状态 ✗）；不给旧版本加"已无法刷取"的推断标注（社区来源未验 ✗，只给 hash 与来源字段原文）。

## 七、状态

- 2026-10-10：**调研完成**（§一 全部为实测）→ 当天落地两项：
  - **B1 已做**：`perk_pool` 默认带描述（玉兔 2.4 → **3.0 KB**，只多 **0.6 KB**；真机鹰月 11/11 与 12+12 条选项全带描述）；
  - **A1 已做**：`find_weapon` 默认取最新（判据 `index`，见 ADR-033）+ 精确同名认中英两种名字；
    真机鹰月 `235827225`(23927) → **`386864872`(35200)**，社区建议 5/5 命中；
    版本数与直读 sqlite 交叉核对一致（Judgment 13 → 6、A Sudden Death 8 → 6）。
  - **未做**：`item_hash` 参数、着色器/模组/大师杰作三栏的"要全"出口。
