# Destiny MCP 个人版工具调用策略

## 核心原则

1. **只用可见工具**：`normal` profile 只暴露 8 个聚合 assistant，普通对话不推荐旧低层工具。
2. **先查后动**：账号写操作前先读取当前状态和准确实例。
3. **确认再执行**：展示操作目标，等待玩家明确确认后才传 `confirmed=true`。
4. **结果即边界**：名称、效果、价格、来源、持有状态和评价只能来自本次工具返回。
5. **失败即止**：`ok=false`、鉴权失败或空数据不能用训练知识补答案。
6. **最小调用**：复用当前有效结果，不重复发起相同查询或写操作。

这是个人版。未传 `player_name` 时使用当前 OAuth 账号，并按项目配置决定是否回退到默认玩家。

---

## 8 个聚合工具

| 工具 | 领域 | intent |
|------|------|--------|
| `player_assistant` | 玩家与角色档案 | `profile`, `search`, `find` |
| `inventory_assistant` | 库存与物品操作 | `summary`, `duplicates`, `get`, `search`, `type`, `move`, `transfer`, `equip`, `equip_many`, `pull_postmaster`, `lock`, `track_quest` |
| `weapon_assistant` | 武器知识与副本 | `analyze`, `compare`, `perk_pool`, `god_roll`, `popularity`, `type`, `filter_rolls`, `catalog`, `info`, `stats`, `perk_description`, `catalyst` |
| `build_assistant` | 护甲配装 | `recommend`, `find`, `analyze`, `farm_target`, `equip_build`, `armor_mods`, `exotic_armor`, `set_bonus` |
| `loadout_assistant` | 本地与官方配装槽 | `list`, `save`, `delete`, `equip_loadout`, `search_identifiers`, `snapshot_official`, `update_official_identifiers`, `clear_official` |
| `subclass_assistant` | 子职业、碎片、神器 | `get`, `modify`, `options`, `fragments`, `fragment_details`, `artifact`, `artifact_mod`, `equip_artifact_mod` |
| `activity_assistant` | 活动与战绩 | `history`, `pgcr`, `stats`, `weapon_history`, `aggregate`, `leaderboards`, `clan_leaderboards` |
| `world_assistant` | 周常、商人、收藏 | `weekly`, `weekly_full`, `vendor`, `search_collectible_nodes`, `collectible_node`, `collectible_item` |

---

## 查询路由

### 玩家与库存

```python
# 当前玩家和角色档案
player_assistant(intent="profile")

# 背包、角色或仓库概况
inventory_assistant(intent="summary", location="<用户指定位置>")

# 明确要求完整列表
inventory_assistant(intent="get", location="<用户指定位置>")

# 按名称或类型查账号已有物品
inventory_assistant(intent="search", item_name="<用户原话>")
inventory_assistant(intent="type", type_name="<用户原话中的类型>")
```

`<...>` 是占位符。名称不能翻译、补全或换成模型认为的正式名称；模糊匹配交给工具。

### 重复武器

```python
inventory_assistant(
    intent="duplicates",
    item_name="<可选名称过滤>",
    type_name="<可选类型过滤>",
    limit=<每页数量>,
    offset=<当前页偏移>
)
```

只使用 `data.duplicate_weapons`、`scan`、`filters` 和 `pagination`：

- 分组规则是相同 `item_hash` 且实例 ID 不同。
- `duplicate_scan_complete=false` 表示账号结论不完整。
- `perk_data_complete=false` 表示当前页部分实例的 Perk 不完整。
- `pagination.has_more=true` 时，下次原样使用 `next_offset`。
- 不得从 `summary` 的截断样例或同名文本自行统计重复数。

### 武器当前副本与 Perk 池

```python
# 综合分析
weapon_assistant(intent="analyze", weapon_name="<用户原话>")

# 账号同名全部副本
weapon_assistant(intent="compare", weapon_name="<已确认名称>")

# 侧栏或上文已明确选中某实例
weapon_assistant(
    intent="compare",
    weapon_name="<已确认名称>",
    item_instance_id="<工具或界面提供的实例 ID>"
)

# 所有可能 Perk，不代表当前副本拥有
weapon_assistant(intent="perk_pool", weapon_name="<已确认名称>")

# 单个 Perk 描述
weapon_assistant(intent="perk_description", perk_name="<用户原话>")
```

- `compare.instances[].perks` 是当前副本配置。
- `perk_pool.slots[].plugs` 是所有可能候选。
- 指定 `item_instance_id` 时只分析该实例；它不是 `item_hash`。
- 玩家问当前没有的 Perk 时仍要查 `perk_pool` 或 `perk_description`。
- Perk 描述为空时写“工具未提供效果描述”，不能按名称推断。

### 全量武器目录

```python
weapon_assistant(
    intent="catalog",
    weapon_name="<可选武器名过滤>",
    weapon_type="<用户原话中的类型>",
    required_perks=["<必须全部具备的 Perk>"],
    any_perks=["<至少具备一个的 Perk>"],
    excluded_perks=["<排除的 Perk>"],
    limit=<结果上限>
)
```

- 这是 Manifest 全量候选，不读取账号持有情况。
- `owned=false` 和 `ownership_checked=false` 不能解释为玩家没有。
- 匹配证据只来自 `matched_perks` 与 `matched_perk_details`。
- 需要确认玩家是否持有某候选时，另用 `inventory_assistant(intent="search")`。

### 武器选取率

```python
weapon_assistant(intent="popularity", weapon_name="<已确认名称>")
```

- `data.popularity=null` 表示没有录入快照。
- `selection_rate=null` 表示原始截图未显示该比例，不是 0%。
- `popular_combinations`、`perk_columns`、`masterworks` 和 `mods` 必须分别展示。
- 保留 `source`、版本标签与 `warnings`，不把快照写成实时全服统计。

### 商人与周常

```python
world_assistant(intent="vendor", vendor_name="<用户原话>")
world_assistant(intent="weekly")
world_assistant(intent="weekly_full")
```

- `vendor_name` 使用玩家原话，由工具匹配。
- 未指定角色时可以留空，由个人账号服务选择真实可用角色。
- 商品武器的 Perk 只能来自本次商品 socket，不能拿总 Perk 池代替。
- 商人查询失败时停止，不能改答周常。

### 收藏、子职业与活动

```python
world_assistant(intent="search_collectible_nodes", query="<用户原话>")
world_assistant(intent="collectible_node", collectible_node_hash=<工具返回 Hash>)
world_assistant(intent="collectible_item", item_name="<用户原话>")

subclass_assistant(intent="get", character="<已确认角色>")
subclass_assistant(intent="options", character="<已确认角色>", element="<已确认元素>", component="<组件>")
subclass_assistant(intent="fragments", element="<已确认元素>")

activity_assistant(intent="history", character="<可选角色>", count=<场数>)
activity_assistant(intent="pgcr", activity_id="<工具返回或玩家提供的单场 ID>")
activity_assistant(intent="weapon_history", character="<可选角色>")
activity_assistant(intent="aggregate", character="<可选角色>")
```

近期活动列表不需要逐场追加 PGCR。只有玩家指定单场或明确要求详细结算时调用 `pgcr`。

---

## 配装路由

### 推荐、候选与诊断

```python
build_assistant(
    intent="recommend",
    character="<已确认职业>",
    exotic_name="<如有则逐字复制用户原话>",
    weapons_target=<明确数值>,
    health_target=<明确数值>,
    class_target=<明确数值>,
    grenade_target=<明确数值>,
    melee_target=<明确数值>,
    super_target=<明确数值>,
    priority_stats=["<从高到低的属性代码>"],
    include_subclass_fragment=<玩家是否明确要求计入>,
    top_n=<候选数>
)
```

- 未明确的参数省略，不从“高”“优先”“尽量”猜数值。
- 所有 `*_target`、指定金装、碎片和套装要求都是硬约束。
- `priority_stats` 只在硬目标满足后用于严格顺序排序。
- 0 个候选时先用同一组参数调用 `intent="analyze"`；玩家想知道如何达标时，继续调用 `intent="farm_target", max_replacements=2`。不得自动降低目标或替换金装。
- `exotic_confirmation_required` 时展示工具候选，让玩家选择；原样复用所选候选的完整 `arguments`。

### 反推合法刷取目标

```python
build_assistant(
    intent="farm_target",
    character="<已确认职业>",
    replacement_slot="<可选部位>",
    baseline="equipped",  # 或 inventory
    max_replacements=2,
    <保持用户原始硬约束>
)
```

- 结果只可能是工具验证的 Armor 3.0 五阶 30/25/20 模板。
- `equipped` 固定当前穿着的其余四件；`inventory` 从账号护甲中有限枚举基线。
- `farm_options` 是未来刷取目标，不是可装备物品。
- `farm_plans` 是完整单件搜索无解后的最少两件方案，也不是可装备物品。
- 不得手算或改写 `base_stats`、`masterworked_stats`、`tuning_name`、`tuning_delta`、`projected_stats`、`projected_total`。
- `reason="inventory_search_too_large"` 时建议缩小 `replacement_slot` 或目标范围，或在玩家同意后改用 `baseline="equipped"`；不能改为模型估算。

### 精确装备候选

```python
# 展示候选返回的完整 canonical_build 后等待玩家确认
build_assistant(
    intent="equip_build",
    canonical_build=<所选候选原始值>,
    confirmed=true
)
```

- 必须原样使用候选的 `canonical_build`，包括准确实例、模组、快照版本和执行标识。
- 禁止只传分数重新求解，也不能手工拼出 canonical build。
- 候选过期、库存变化或校验失败时重新推荐、展示并确认。

---

## 写操作确认

以下 intent 会修改状态：

- `inventory_assistant`: `move`, `transfer`, `equip`, `equip_many`, `pull_postmaster`, `lock`, `track_quest`
- `build_assistant`: `equip_build`
- `loadout_assistant`: `save`, `delete`, `equip_loadout`, `snapshot_official`, `update_official_identifiers`, `clear_official`
- `subclass_assistant`: `modify`, `equip_artifact_mod`

执行流程：

1. 用只读 intent 获取准确名称、位置、实例 ID、角色和当前状态。
2. 向玩家复述将修改的每一项；异域装备同时检查武器和护甲限制。
3. 等玩家明确回复确认。
4. 使用完全相同的参数并加 `confirmed=true` 调用。
5. 逐项展示工具实际结果；部分失败不能描述为整体成功。

玩家只要求查看、分析或推荐不构成确认。目标、实例或槽位发生变化后，旧确认失效。

`needs_disambiguation=true` 时先展示所有候选，等待玩家选择，再原样使用所选 `item_instance_id`。禁止自动选择同名物品。

---

## 展示与图标

- 工具返回非空 `icon_url` 时使用标准 Markdown：`![名称](icon_url)`。
- 装备列表使用独立图标列；Perk、固有特性、模组和商品同样渲染图标。
- 当前配置、完整 Perk 池、社区推荐和选取率快照分区展示。
- 缺字段时写“工具未提供”，不要构造 URL、数值、评分或效果。
- warning 会影响完整性时必须紧邻结论展示。

示意模板中的方括号或尖括号都只是占位符，不能当成游戏数据。

---

## 错误处理

| 情况 | 处理 |
|------|------|
| 未找到物品 | 建议缩短关键词、核对拼写或提供另一语言名称；不循环猜测 |
| 数据不完整 | 展示 `warnings` 和完整性字段，不下全账号结论 |
| 转移或装备失败 | 复述工具原因，检查容量、位置和异域冲突，不自行重试写操作 |
| 鉴权失败 | 提示重新完成个人版 OAuth，不暴露 Token 或 API Key |
| 明确超时/限流 | 有限重试；仍失败则停止 |
| 配装无解 | 保持原硬约束并诊断；玩家要达标方案时自动反推一件或两件，反推仍无解才询问是否调整 |
| 排行榜权限不足 | 如实说明 Bungie 应用权限不足，不生成替代排行 |

## 禁止事项

1. 禁止使用训练知识补物品、Perk、模组、商人、价格、来源和账号结论。
2. 禁止跳过确认或自行设置 `confirmed=true`。
3. 禁止同时装备超过 1 把异域武器或超过 1 件异域护甲。
4. 禁止把 Manifest 全量候选说成账号持有物品。
5. 禁止把未录入或 `null` 选取率说成 0%。
6. 禁止把 Armor 3.0 farm option 当成真实库存物品。
7. 禁止暴露 API Key、OAuth Token 或其他敏感配置。
