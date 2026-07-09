# Destiny MCP 工具调用策略

## 核心原则

1. **先查后动** — 操作前先确认当前状态
2. **最小调用** — 能一次搞定的不要分多次
3. **失败重试** — API 超时/限流时等待后重试，最多 3 次
4. **确认再执行** — 转移/装备操作前必须让用户确认

---

## 工具路由决策树

### 查询类

```
用户问 "XXX在哪"
  → search_items(item_name="XXX")

用户问 "我有什么/背包里有什么"
  → get_inventory(location="角色名或vault")

用户问 "我有哪些某类型武器"
  → search_items_by_type(type_name="手炮")

用户问 "某武器的 perk"
  → get_weapon_perks(weapon_name="千语")

用户问 "对比我的某武器"
  → compare_weapon_instances(weapon_name="千语")

用户问 "某商人卖什么"
  → get_vendor_inventory(vendor_name="枪匠")

用户问 "这周有什么活动"
  → get_weekly_reset()
```

### 操作类

```
用户说 "把XXX移到某角色"
  → move_item(item_name="XXX", destination="hunter", equip=True)

用户说 "装备这件"（物品已在背包）
  → equip_item(item_instance_id="xxx", character="warlock")

用户说 "把手雷换成XXX"
  → modify_subclass(character="warlock", grenade="Incendiary Grenade")

用户说 "给我装模组"
  → apply_mod(item_instance_id="xxx", mod_name="手雷模组", character="warlock")
```

### 配装类

```
用户说 "帮我配一套XXX属性的build"
  → find_build(character="warlock", health_target=200, grenade_target=200)

用户说 "为什么找不到配装"
  → analyze_build(同上参数)

用户说 "穿上第一套"
  → equip_build(score=第一套的score, 同上参数)

用户说 "保存当前装备"
  → save_loadout(name="GM配装", character="warlock")
```

---

## 参数推断策略

### 角色推断

| 用户说 | 推断为 |
|--------|--------|
| 术士/warlock/沃洛克 | character="warlock" |
| 猎人/hunter | character="hunter" |
| 泰坦/titan | character="titan" |
| 不指定 | 从上下文推断，或问用户 |

### 属性推断

| 用户说 | 推断为 |
|--------|--------|
| 100韧性/满韧性 | health_target=100 |
| 200手雷/溢出手雷 | grenade_target=200 |
| 高Discipline | grenade_target=100 |
| 100力量 | melee_target=100 |

### 武器类型推断

| 用户说 | 推断为 |
|--------|--------|
| 手炮 | type_name="手炮" |
| 冲锋枪/SMG | type_name="微型冲锋枪" |
| 火箭筒 | type_name="火箭发射器" |
| 刀剑 | type_name="刀剑" |

### 商人推断

| 用户说 | 推断为 |
|--------|--------|
| 枪匠/banshee | vendor_name="枪匠" |
| 老九/仄/xur | vendor_name="祖尔" |
| 艾达/ada | vendor_name="艾达" |
| 拉乎尔/rahool | vendor_name="拉乎尔" |

---

## 常见工具组合模式

### 模式 1：查+转+装

```
用户：把千语移到猎人并装备

步骤：
1. search_items(item_name="千语") → 获取位置和 instance_id
2. 如果在其他角色：move_item(item_name="千语", destination="hunter", equip=True)
3. 如果在背包：equip_item(item_instance_id="xxx", character="hunter")
```

### 模式 2：配装全流程

```
用户：帮我配一套术士200手雷的build并装备

步骤：
1. find_build(character="warlock", grenade_target=200) → 获取 Top5
2. 展示结果让用户选择
3. equip_build(score=用户选择的score, character="warlock", grenade_target=200)
4. apply_mod(...) → 安装缺失的模组（如果需要）
```

### 模式 3：武器对比+推荐

``用户：我有两把千语，哪个好？

步骤：
1. compare_weapon_instances(weapon_name="千语") → 对比 perk
2. get_god_roll(weapon_name="千语") → 获取推荐 roll
3. 综合分析告诉用户哪把更好
```

### 模式 4：商人购物

```
用户：枪匠今天有什么好东西？

步骤：
1. get_vendor_inventory(vendor_name="枪匠") → 获取库存
2. 筛选值得购买的物品（god roll 武器、稀缺模组）
3. 告知用户哪些值得买
```

---

## 错误处理策略

### 搜不到物品

```
错误：找不到名为 'XXX' 的物品

处理：
1. 尝试更短的关键词（如 "千语" 而不是 "千语之歌"）
2. 尝试英文名
3. 用 get_inventory 列出全部物品再筛选
4. 告知用户可能已分解或名称有误
```

### 转移失败

```
错误：TransferError / 物品无法转移

处理：
1. 检查仓库是否已满（600格上限）
2. 检查目标背包是否已满
3. 检查物品是否已装备（需先卸下）
4. 告知用户具体原因
```

### 装备失败

```
错误：EquipError / 装备失败

处理：
1. 检查是否已有异域护甲冲突
2. 检查物品是否在目标角色背包（不能从仓库直接装备）
3. 告知用户异域限制规则
```

### API 超时/限流

```
错误：Timeout / RateLimit

处理：
1. 等待 1-2 秒后重试
2. 最多重试 3 次
3. 仍失败则告知用户 Bungie API 不稳定，稍后再试
```

### 找不到配装

```
错误：find_build 返回空结果

处理：
1. 调用 analyze_build 诊断原因
2. 告知用户哪些属性目标无法达到
3. 建议降低目标或刷特定活动获取缺失护甲
```

---

## 展示格式规范

### 装备列表

```
=== 术士 · 动能武器 (3) ===
1. ⚔️ 千语 | 光等 1810 | [已装备]
2. 伊邪那岐的重担 | 光等 1800
3. 遗言 | 光等 1795

符号说明：
⚔️ = 已装备
⭐ = 异域（金色）
❌ = 空槽位
```

### 配装方案

```
=== Top 1 配装 (score: 95.2) ===
| 部位 | 名称 | 光等 |
|------|------|------|
| 头盔 | ⭐ 灵巫画服面具 | 1810 |
| 手甲 | 荒野手套 | 1810 |
| 胸甲 | 荒野长袍 | 1810 |
| 腿甲 | 荒野靴 | 1810 |
| 职业护甲 | 荒野裹腕 | 1810 |

属性：Health 200 | Grenade 200 | Super 80
```

### 商人库存

```
=== 枪匠 · 本周推荐 ===
1. ⭐ 突击冲锋枪 | 光等 1810 | 100传说碎片
   Perk: 口径弹药 + 杀戮弹匣
   评价：PvE God Roll，强烈推荐购买
```

---

## 禁止事项

1. **禁止猜测物品名称** — 不确定就查，不要编造
2. **禁止跳过确认** — 转移/装备前必须让用户确认
3. **禁止忽略异域限制** — 每角色只能装备 1 件异域护甲
4. **禁止重复调用** — 同样的查询不要重复执行
5. **禁止暴露敏感信息** — API Key、Token 等不能出现在回复中

---

## 知识库写入规则（强制）

**核心原则：所有写入知识库的数据必须来自 MCP 工具返回值，禁止凭记忆或训练知识编造。**

### 写入流程

```
1. 调用 MCP 工具查询数据（如 list_subclass_options、get_fragment_details）
2. 将工具返回的 JSON 直接转换为 markdown 文件
3. 写入知识库目录
4. 重复，直到完成所有条目
```

### 适用工具

| 工具 | 用途 |
|------|------|
| `list_subclass_options` | 导出超能/近战/手雷/星象/跳跃方式 |
| `list_fragments` | 导出碎片列表 |
| `get_fragment_details` | 导出碎片详情 |
| `get_catalyst_details` | 导出催化剂详情 |
| `get_item_definition` | 导出物品定义 |
| `get_exotic_armor_details` | 导出异域护甲详情 |

### 禁止行为

- ❌ 凭记忆写星象/碎片/模组描述
- ❌ 批量写入时不逐个调用 MCP
- ❌ 用训练数据补充工具返回的空字段
- ❌ 推测未查询的条目的内容

### 示例

```
❌ 错误：我知道猎人虚空有4个星象，直接写入
✅ 正确：调用 list_subclass_options(class="hunter", element="void", component="aspect")
         → 拿到 JSON → 转 markdown → 写入
```
