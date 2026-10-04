# 渲染块：出处、字段、骨架

这份文件是**渲染块的唯一定义处**。三件事写在一起，改一处不会漏另一处：

1. **喂它的 intent**（每块开头）；
2. **字段表**——每一行都在真机响应里核对过，由 `scripts/verify_render_fields.py` 复跑；
3. **HTML 骨架**与**降级规则**。

字段表的读法：`出处` 列里那行就是复跑时的**真实调用**（方括号路径用 `[].` 表示"任取一个元素"）。
`$weapon_instance` / `$armor_instance` / `$activity_id` 是复跑脚本现场从你的
账号里取的占位符（见脚本的 `PLACEHOLDERS`），不是让你手填的值；表里的 `character="hunter"`
同理，是复跑夹具，真实回答里换成用户问的那个角色。

**路径前的 `?` 是条件字段**：那个键只在满足条件时才存在（例：`?data.armor.instance.masterwork.level`
只有这件护甲大师过才有）。条件字段缺席时**整块不渲染**，不算"没查到"、更不许补 0。

**硬规矩**：这份文件里出现的路径，就是**允许渲染的全部字段**。表里没有的字段不许出现在卡片上 ——
写错的字段渲染出来是一张空白卡，而没人知道为什么。要加字段，先跑
`python scripts/verify_render_fields.py` 确认它在真实响应里。

**这些骨架是 HTML 片段，交付包装另算**：能渲染 HTML 的宿主**先按 §零 定包装** —— 宿主表、
探测步骤与切换规矩都在 `references/html-conventions.md` §零。**下面 5 段示例用的是豆包环境实测
的那一行**（起始行 ```` ```html type="renderer" ````；普通的 ```` ```html ```` 在豆包会被原样当源码
显示），所以照抄**只对豆包成立**：换成别的宿主（WorkBuddy / Codex / 没见过的）先走 §零 的探测，
再按那个宿主验过的那一行发 —— **别把豆包这一行带过去**。
示例本身写错比不写示例更糟，模型会照抄。

---

## 一、块索引（哪条 intent 喂哪块）

| 块 | 出处 intent | 备注 |
| --- | --- | --- |
| 武器卡 | `weapon_assistant` 的 `analyze` / `info` / `perk_pool` | `analyze` 才带账号副本；`info`/`perk_pool` 是纯定义 |
| 副本对比卡 | `weapon_assistant(intent="compare")` | 不给 `item_instance_id` = 每把一行的行视图；给了 = 单个副本明细 |
| perk 行 | 上面两块内部的栏位；另见 §三 列出的几条来源 | 定义级池子**没有** perk 图；实例级才有 |
| perk 选取率卡 | `weapon_assistant(intent="popularity")` | 快照数据，带 `source.captured_at`；行里**没有**图 |
| 护甲卡 | `inventory_assistant(intent="item")`；列表用 `intent="mods"` | `mods` 一次读回五件，形状与 `item` 的槽行一致 |
| 异域护甲卡 | `build_assistant(intent="exotic_armor")` | 定义级，不代表拥有 |
| 配装候选卡 | `build_assistant(intent="find")` / `recommend` | `find` 才带可执行载荷；`recommend` 只有排序 |
| 已存配装卡 | `loadout_assistant(intent="get")`；清单用 `intent="list"` | `list` 只是清单行，不带模板 |
| 活动/战绩行 | `activity_assistant` 的 `history` / `pgcr` / `raid_report` / `pvp_weapons` / `weapon_history` / `aggregate` / `counters` / `stats` | 八条各自一个表，别混用 |
| 清单表 | `inventory_assistant` 的 `summary` / `get` / `search`；`weapon_assistant` 的 `type` / `patterns` / `filter_rolls`；`inventory_assistant(intent="duplicates")` | 都有分页/截断，必须带上下文 |
| 周常·货架行 | `world_assistant` 的 `rotations` / `vendor` | 星象表带 `source`（官方口径 vs 自维护表） |

---

## 二、武器卡

**骨架**（照老 web 的 `weapon_workbench`：身份块 → 属性格 → 固有特性 → Perk 池 → 推荐思路 → 注意）：

```html type="renderer"
<section style="background:#101317;border:1px solid #3a424b;border-radius:7px;overflow:hidden;color:#e8e9e6;font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif">
  <!-- 身份块：68px 图标 + 踢脚 + 名字 + 英文名 + 标签 -->
  <div style="display:grid;grid-template-columns:68px minmax(0,1fr);gap:13px;align-items:center;padding:14px;background:#17141a;border-bottom:1px solid #3a424b">
    <img src="{{data.weapon.icon_url}}" alt="{{名字}} 图标" loading="lazy" style="width:68px;height:68px;display:block;object-fit:cover;background:#1a2026;border:2px solid #806497;border-radius:2px">
    <div style="min-width:0">
      <span style="font-size:9px;color:#cfbfd9">{{稀有度}} · {{类型}} · {{伤害类型}}/{{弹药类型}}</span>
      <h3 style="margin:2px 0 4px;font-size:19px;line-height:1.2;color:#fafaf7;overflow-wrap:anywhere">{{名字}}</h3>
      <p style="margin:0;font-size:9px;color:#9fa6ac;font-family:ui-monospace,Consolas,monospace">{{英文名}}</p>
    </div>
  </div>
  <!-- 属性格：auto-fit 88px -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));border-bottom:1px solid #2a3037">
    <div style="padding:8px 11px;border-right:1px solid #1d2227">
      <span style="display:block;font-size:9px;color:#9fa6ac">{{属性名}}</span>
      <strong style="display:block;margin-top:2px;font-size:13px;color:#fafaf7;font-variant-numeric:tabular-nums">{{display}}</strong>
    </div>
  </div>
  <!-- Perk 池：横向排列的栏位，每栏一列 perk 行 -->
  <div style="padding:13px 14px;border-bottom:1px solid #2a3037">
    <div style="display:grid;grid-auto-flow:column;grid-auto-columns:minmax(144px,1fr);gap:1px;overflow-x:auto;background:#1d2227;border:1px solid #1d2227">
      <div style="padding:8px;background:#101317">
        <h4 style="margin:0 0 7px;font-size:10px;color:#c4c9cd">{{栏名}}</h4>
        <div style="display:grid;gap:4px"><!-- perk 行，见 §三 --></div>
      </div>
    </div>
  </div>
  <!-- 注意：warnings 原样列出，不合并、不改写 -->
  <div style="padding:11px 14px;background:rgba(213,168,95,.07);color:#e6c791">
    <strong style="font-size:10px">注意</strong>
    <ul style="margin:5px 0 0;padding-left:18px;font-size:10px;line-height:1.55"><li>{{warning}}</li></ul>
  </div>
</section>
```

### 字段表

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `summary` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 顶层摘要，放卡头副标题 |
| `data.weapon.name` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 中文名；必转义 |
| `data.weapon.name_en` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 英文名，可为空串 |
| `data.weapon.weapon_type` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 武器类型（手炮） |
| `data.weapon.frame` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 框架（精密框架） |
| `data.weapon.rarity` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 稀有度中文（传说） |
| `data.weapon.damage_type` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 伤害类型 |
| `data.weapon.ammo_type` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 弹药类型（白弹/绿弹） |
| `data.weapon.rpm` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 射速 |
| `data.weapon.description` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 风味文本，可空串 |
| `data.weapon.icon_url` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 唯一图片键；空串走占位块 |
| `?data.weapon.is_craftable` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 布尔；false 不渲染"可锻造"标签 |
| `data.weapon.roll_kind` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | `random`/`fixed` 等，决定"可滚栏"说法 |
| `data.stats[].name` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 属性名 |
| `data.stats[].value` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 数值 |
| `data.stats[].display` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 显示串，优先用它 |
| `data.sockets[].slot` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 中文栏名（枪管/弹匣/特性1…） |
| `data.sockets[].kind` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 稳定枚举，判"框架/特性/模组" |
| `data.sockets[].option_count` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 这一栏有几个可选项（权威数字） |
| `data.sockets[].options[].name` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 池子里的 perk 名 |
| `data.sockets[].options[].can_roll` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | false = 已退役，别当现在能滚 |
| `data.sockets[].options[].stat_effects[].stat` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 属性影响名 |
| `data.sockets[].options[].stat_effects[].value` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 属性影响值 |
| `data.inventory.instances[].weapon.instance.instance_id` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 副本 ID（要操作就回传它） |
| `data.inventory.instances[].weapon.instance.location` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 位置（仓库/角色/已装备） |
| `data.inventory.instances[].weapon.instance.power` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 光等 |
| `data.inventory.instances[].weapon.instance.is_equipped` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 是否装着 |
| `data.inventory.instances[].weapon.instance.locked` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 是否锁定 |
| `data.inventory.instances[].weapon.instance.god_roll_score` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 可为空串（本地愿单没收录） |
| `data.inventory.instances[].weapon.gear_tier` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 装备分级；**null = 不在分级体系内，不是 T0** |
| `data.inventory.instances[].weapon.item_level` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 装备等级 |
| `data.inventory.instances[].sockets[].slot` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 副本的栏名 |
| `data.inventory.instances[].sockets[].equipped.name` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 这一件**现在装着**什么 |
| `data.inventory.instances[].sockets[].equipped.plug_hash` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 现装 plug hash |
| `data.inventory.instances[].sockets[].option_count` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 可换项数；**`options_available=false` 时它仍是权威数字** |
| `data.inventory.instances[].sockets[].options_available` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | false = 这次没展开池子，**不是"没有可选项"** |
| `data.inventory.weapon.owned.count` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 我持有几件 |
| `data.inventory.weapon.owned.status` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | `checked` 才代表读过账号 |
| `data.god_roll.kind` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | `none` = 本地愿单没收录，**不等于不值得留** |
| `data.god_roll.source` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 愿单来源 |
| `data.god_roll.note` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 没数据时的那句说明，照抄 |
| `data.inventory_status` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 账号读取状态（`complete` 等） |
| `warnings[]` | `weapon_assistant(intent="analyze", weapon_name="星狐座")` | 原样列出 |

### 降级

- `data.weapon` 整个没有 → **不出卡**（老 web 同样：没名字就没有块）。
- `data.inventory` 缺失或 `owned.status != "checked"` → 只渲染定义部分，并在卡上写
  "账号未读取"；**不许**把 0 件当成"你没有"。
- `data.sockets[].options` 为空且 `options_available=false` → 只写
  "本栏 N 个可选项（本次未展开）"，**不要**画空列表。
- `data.sockets[].kind` 是非 roll 栏（`mod`/`masterwork`/`memento`/`tracker`/`shader`/
  `ornament`/`catalyst`/`other`）时**别和特性栏并排铺开**：只写"栏名 + `option_count`"
  或最多 3 个样本 —— 响应对非 roll 栏本来只给样本，铺开等于把几十行塞进卡里。
- `warnings` 为空 → 不输出"注意"块（不要输出空框）。

---

## 三、perk 行

perk 行有**两种形态**，选错就会出现"一排空图"：

- **带图行**：`name` + `icon_url` + 可选第二行（描述/选取率/栏位）；
  来源见下表 —— **只有表里这几条出口真的给图**，其余一律不带图。
- **不带图行**：只有 `name`（+ 可选数值）。定义级 perk 池、选取率快照、`duplicates`
  的 perk 行都属于这种 —— 这是**体积口径**（拍板 2/3/4），不是漏了，别去找图。

```html type="renderer"
<!-- 带图行：30px 图 + 名字；第二行放栏位/选取率/描述提示 -->
<div style="display:grid;grid-template-columns:30px minmax(0,1fr);gap:7px;align-items:center;padding:6px 7px;background:#14181d;border:1px solid #1d2227;border-radius:5px">
  <img src="{{icon_url}}" alt="{{名字}} 图标" loading="lazy" style="width:30px;height:30px;display:block;object-fit:cover;background:#25292d;border:1px solid #5d6670;border-radius:2px">
  <div style="min-width:0">
    <strong style="display:block;font-size:10px;color:#e8e9e6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{名字}}</strong>
    <span style="display:block;margin-top:2px;font-size:8px;color:#9fa6ac;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{第二行}}</span>
  </div>
</div>

<!-- 不带图行：不占图位，名字允许换行 -->
<div style="padding:6px 7px;background:#14181d;border:1px solid #1d2227;border-radius:5px">
  <strong style="display:block;font-size:10px;color:#e8e9e6;overflow-wrap:anywhere">{{名字}}</strong>
  <span style="display:block;margin-top:2px;font-size:8px;color:#9fa6ac">{{第二行}}</span>
</div>

<!-- icon_url 是空串时的占位块（有图位的那种形态）：给一个同尺寸色块，绝不写 <img src=""> -->
<span aria-hidden="true" style="width:30px;height:30px;display:block;background:#25292d;border:1px solid #5d6670;border-radius:2px"></span>
```

### 字段表

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.comparison.instances[].options[].slot` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 栏名（这一件能换的栏） |
| `data.comparison.instances[].options[].option_count` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 可换项数 |
| `data.comparison.instances[].options[].options_available` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | false = 池子没展开 |
| `data.comparison.instances[].options[].equipped.name` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 现在装着的那颗 |
| `data.comparison.instances[].options[].options[].name` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | **实例级 perk 名（带图的那条路）** |
| `data.comparison.instances[].options[].options[].icon_url` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 实例级 perk 图（唯一稳定给 perk 图的出口） |
| `data.comparison.instances[].options[].options[].can_roll` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 已退役为 false |
| `data.comparison.instances[].options[].options[].enhanced_plug_hash` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 0 = 无强化版 |
| `data.comparison.instances[].options[].options[].description` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | perk 效果，放 title 或第二行 |
| `data.comparison.instances[].options[].options[].stat_effects[].stat` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 属性影响名 |
| `data.comparison.instances[].options[].options[].stat_effects[].value` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 属性影响值 |
| `data.comparison.instances[].sockets[].equipped` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 行视图里 `equipped` 是**字符串**，不是对象 |
| `data.comparison.instances[].sockets[].options[].name` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 行视图的可换项（**不带图**） |
| `data.comparison.weapon.item_hash` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 行视图卡头那把枪的 hash（同名多版本时看这个） |
| `data.comparison.instances[].item_hash` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 这一把属于哪个版本 |
| `data.comparison.instances[].icon_url` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 行视图**每一行**的武器图（同一 hash 的行必然同一张图；空串 = 查不到定义 → 画占位块） |
| `data.duplicate_weapons[].icon_url` | `inventory_assistant(intent="duplicates", limit=3)` | 组级武器图：**同一组里每一把都是这张图**。实例行**没有这个字段**（不是空串，体积口径见 `docs/adr/021`）→ 渲染每把时复用它，**不要画占位块**，也别写成"每组只有第一把有图"（细则在 §八 的降级） |
| `data.subclass.subclass_name` | `subclass_assistant(intent="get", character="hunter")` | **子职业自己**那一行（卡片标题） |
| `data.subclass.icon_url` | `subclass_assistant(intent="get", character="hunter")` | 子职业本身的图（2026-10-05 补：以前只有 `plugs` 有图，标题行只能放色块） |
| `data.subclass.plugs[].name` | `subclass_assistant(intent="get", character="hunter")` | 子职业当前装的技能/星象/碎片 |
| `data.subclass.plugs[].icon_url` | `subclass_assistant(intent="get", character="hunter")` | 技能图 |
| `data.subclass.plugs[].is_active` | `subclass_assistant(intent="get", character="hunter")` | 是否生效 |
| `data.subclass.plugs[].socket_type` | `subclass_assistant(intent="get", character="hunter")` | 槽类别，用来分组 |
| `data.subclass.plugs[].available[].name` | `subclass_assistant(intent="get", character="hunter")` | 该槽可换的其它项 |
| `data.subclass.plugs[].available[].icon_url` | `subclass_assistant(intent="get", character="hunter")` | 可换项的图 |
| `data.armor.sockets[].name` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 护甲模组槽行 |
| `data.armor.sockets[].icon_url` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 模组图 |
| `data.armor.sockets[].energy_cost` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 占几格能量 |
| `data.armor.sockets[].empty` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | true = 空槽，别画成模组 |
| `data.equipped_armor.characters[].items[].mods[].name` | `inventory_assistant(intent="mods", character="hunter")` | 五件护甲**现在装着**的模组 |
| `data.equipped_armor.characters[].items[].mods[].icon_url` | `inventory_assistant(intent="mods", character="hunter")` | 模组图 |
| `data.equipped_armor.characters[].items[].mods[].energy_cost` | `inventory_assistant(intent="mods", character="hunter")` | 能量开销 |
| `data.equipped_armor.characters[].items[].mods[].kind` | `inventory_assistant(intent="mods", character="hunter")` | 槽类别 |
| `data.equipped_armor.characters[].items[].mods[].empty` | `inventory_assistant(intent="mods", character="hunter")` | 空槽 |
| `data.loadouts[].build_template.weapons[].plugs[].name` | `loadout_assistant(intent="get")` | 已存配装的武器插槽 |
| `data.loadouts[].build_template.weapons[].plugs[].icon_url` | `loadout_assistant(intent="get")` | 插槽图（可为空串） |
| `data.loadouts[].build_template.weapons[].plugs[].category` | `loadout_assistant(intent="get")` | plug 类别（barrels/frames…） |
| `data.mods[].name` | `build_assistant(intent="armor_mods")` | 护甲模组清单行 |
| `data.mods[].icon_url` | `build_assistant(intent="armor_mods")` | 模组图 |
| `data.mods[].energy_cost` | `build_assistant(intent="armor_mods")` | 能量开销 |
| `data.mods[].stat_bonus` | `build_assistant(intent="armor_mods")` | 属性加成（可为 null） |
| `data.mods[].category` | `build_assistant(intent="armor_mods")` | 模组类别 |
| `data.match.kind` | `build_assistant(intent="armor_mods")` | 本次筛选口径，放卡头 |
| `data.popularity.perk_columns[].label` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 选取率的栏名 |
| `data.popularity.perk_columns[].items[].name` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | **不带图**的 perk 行 |
| `data.popularity.perk_columns[].items[].selection_rate` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 百分比数值；画 4px 进度条 |
| `data.popularity.popular_combinations[].perks[].name` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 热门组合里的 perk |
| `data.popularity.popular_combinations[].selection_rate` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 组合占比 |
| `data.popularity.intrinsic.name` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 框架名 |
| `data.popularity.source.label` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 快照来源（用户截图等），必须印在卡上 |
| `data.popularity.source.captured_at` | `weapon_assistant(intent="popularity", weapon_name="庆典飞行")` | 快照时间，必须印在卡上 |
| `data.perk.name` | `weapon_assistant(intent="perk_description", perk_name="边打边劫")` | 单个 perk 的效果卡 |
| `data.perk.description` | `weapon_assistant(intent="perk_description", perk_name="边打边劫")` | 效果正文（照抄，不推断） |
| `data.perk.icon_url` | `weapon_assistant(intent="perk_description", perk_name="边打边劫")` | perk 图 |
| `data.perk.plug_category` | `weapon_assistant(intent="perk_description", perk_name="边打边劫")` | 类别 |

### 降级

- `icon_url` 缺键或空串 → 用不带图行的排版（别留空图位、别写 `<img src="">`）。
- `selection_rate` 缺失或 `null` → 行尾写"暂无数据"，**不写 0%**（老 web 的
  `.missing` 就是这个口径）。
- `enhanced_plug_hash` 为 0 → 不标"强化"；名字里的 `↑` 是响应自带的强化标记，照原样显示。

---

## 四、副本对比卡

两个视图，别混：**行视图**（不给 `item_instance_id`）每把一行、只有可切换栏；
**明细**（给了 `item_instance_id`）带 `options[]` 与逐颗 perk 图。
**两边都有武器图**：行视图每行取 `instances[].icon_url`（2026-10-05 起；同一把枪的两个视图以前
只有明细给图，行视图整包 0 张图）—— 行视图的 perk 项仍然不带图，那不是漏了，是体积口径。

```html type="renderer"
<section style="background:#101317;border:1px solid #3a424b;border-radius:7px;overflow:hidden;color:#e8e9e6">
  <header style="padding:12px 14px;background:#14181d;border-bottom:1px solid #2a3037">
    <h3 style="margin:0;font-size:14px;color:#fafaf7">你拥有的 4 把「星狐座」</h3>
    <p style="margin:4px 0 0;font-size:10px;color:#9fa6ac">{{data.comparison.rows_note 的口径说明，可省}}</p>
  </header>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1px;background:#2a3037">
    <article style="padding:10px 12px;background:#101317">
      <div style="display:grid;grid-template-columns:44px minmax(0,1fr);gap:9px;align-items:center">
        <img src="{{icon_url}}" alt="副本图标" loading="lazy" style="width:44px;height:44px;display:block;object-fit:cover;background:#1a2026;border:1px solid #806497;border-radius:2px">
        <div style="min-width:0">
          <strong style="display:block;font-size:11px;color:#fafaf7">仓库 · 光等 550 · T5</strong>
          <span style="display:block;margin-top:3px;font-size:9px;color:#9fa6ac">6917530184222840477</span>
        </div>
      </div>
      <!-- 每栏一行：栏名 + 现装 + 全部可换项 -->
    </article>
  </div>
</section>
```

### 字段表

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.comparison.weapon.name` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 卡头用 |
| `data.comparison.weapon.weapon_type` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 类型 |
| `data.comparison.weapon.frame` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 框架 |
| `data.comparison.instances[].instance_id` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 行视图的副本 ID |
| `data.comparison.instances[].location` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 位置 |
| `data.comparison.instances[].power` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 光等 |
| `data.comparison.instances[].is_equipped` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 是否装着 |
| `data.comparison.instances[].locked` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 是否锁定 |
| `data.comparison.instances[].sockets[].slot` | `weapon_assistant(intent="compare", weapon_name="星狐座")` | 有得选的栏（行视图只列这些） |
| `data.comparison.weapon.icon_url` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 明细视图的武器图（**两个视图都有**：行视图那一份 2026-10-05 补上，见上面那张表的同名行） |
| `data.comparison.instances[].weapon.gear_tier` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | T 级逐副本给；null = 无分级 |
| `data.comparison.instances[].weapon.instance.location` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 明细里的位置 |
| `data.comparison.instances[].weapon.instance.power` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 明细里的光等 |
| `data.comparison.instances[].weapon.instance.god_roll_score` | `weapon_assistant(intent="compare", weapon_name="星狐座", item_instance_id="$weapon_instance")` | 可为空串 |

### 降级

- 行视图、明细视图的**字段位置不同**（前者 `instances[].location`，后者
  `instances[].weapon.instance.location`）。按视图取，别用一个路径套两种调用。
- `god_roll_score` 空串 → 不显示评分位。

---

## 五、护甲卡

```html type="renderer"
<section style="background:#101317;border:1px solid #3a424b;border-radius:7px;overflow:hidden;color:#e8e9e6">
  <div style="display:grid;grid-template-columns:68px minmax(0,1fr);gap:13px;align-items:center;padding:14px;background:#17141a;border-bottom:1px solid #3a424b">
    <img src="{{icon_url}}" alt="{{名字}} 图标" loading="lazy" style="width:68px;height:68px;display:block;object-fit:cover;background:#1a2026;border:2px solid #806497;border-radius:2px">
    <div style="min-width:0">
      <span style="font-size:9px;color:#cfbfd9">头盔 · T5 · 光等 550</span>
      <h3 style="margin:2px 0 4px;font-size:19px;color:#fafaf7">星界夜鹰</h3>
      <p style="margin:0;font-size:10px;color:#c4c9cd">高能者 · 能量 11/11</p>
    </div>
  </div>
  <!-- 三层属性：roll（原始）/ base（模板）/ final（含大师与模组）各一组 -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));border-bottom:1px solid #2a3037">
    <div style="padding:8px 11px;border-right:1px solid #1d2227">
      <span style="display:block;font-size:9px;color:#9fa6ac">武器</span>
      <strong style="display:block;margin-top:2px;font-size:13px;color:#fafaf7">30</strong>
    </div>
  </div>
  <!-- 模组槽行：用 §三 的带图 perk 行 -->
</section>
```

### 字段表

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `summary` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 摘要 |
| `data.armor.identity.name` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 护甲名 |
| `data.armor.identity.name_en` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 英文名 |
| `data.armor.identity.slot_display` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 部位中文 |
| `data.armor.identity.gear_tier` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | T 级；**null 不是 T0** |
| `data.armor.identity.gear_tier_note` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | null 的原因说明，照抄 |
| `data.armor.identity.armor_system` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | `armor_3` / `legacy` |
| `data.armor.identity.icon_url` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 护甲图 |
| `?data.armor.identity.archetype.name` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 词条原型 |
| `?data.armor.identity.archetype.icon_url` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 词条原型的图（2026-10-05 补：它是真插件，Manifest 里有图） |
| `?data.armor.instance.tuning.icon_url` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 调谐的图（与 `armor.sockets[]` 里同一颗同一张） |
| `data.armor.instance.power` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 光等 |
| `data.armor.instance.is_equipped` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 是否穿着 |
| `data.armor.instance.location` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 位置 |
| `data.armor.instance.energy.capacity` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 能量上限 |
| `data.armor.instance.energy.used` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 已用能量 |
| `data.armor.instance.energy.unused` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 剩余能量 |
| `?data.armor.instance.masterwork.level` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 大师等级 |
| `?data.armor.instance.tuning.name` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 调谐名；"空调整模组插槽" 是空槽的写法 |
| `?data.armor.instance.tuning.installed` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 是否真装了调谐 |
| `?data.armor.instance.class_item_perks[].name` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 职业物品特性（仅职业物品有） |
| `?data.armor.instance.class_item_perks[].icon_url` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 特性图 |
| `data.armor.stats.roll.weapons` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 原始 roll 的六维之一（同组还有 `health`/`class_stat`/`grenade`/`super_stat`/`melee`） |
| `data.armor.stats.final.weapons` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 计入大师/模组后的六维（同上六键） |
| `data.armor.stats.notes` | `inventory_assistant(intent="item", item_instance_id="$armor_instance")` | 属性口径说明，非空时印在卡上 |
| `data.armor.exotics[].identity.name` | `build_assistant(intent="exotic_armor", character="hunter")` | 异域护甲清单行 |
| `data.armor.exotics[].identity.icon_url` | `build_assistant(intent="exotic_armor", character="hunter")` | 异域护甲图 |
| `data.armor.exotics[].identity.slot_display` | `build_assistant(intent="exotic_armor", character="hunter")` | 部位 |
| `data.armor.exotics[].identity.class_display` | `build_assistant(intent="exotic_armor", character="hunter")` | 适用职业 |
| `data.armor.exotics[].identity.gear_tier` | `build_assistant(intent="exotic_armor", character="hunter")` | T 级 |
| `data.armor.exotics[].intrinsic_perks[].name` | `build_assistant(intent="exotic_armor", character="hunter")` | 固有特性名 |
| `data.armor.exotics[].intrinsic_perks[].description` | `build_assistant(intent="exotic_armor", character="hunter")` | 特性效果 |
| `data.armor.exotics[].intrinsic_perks[].icon_url` | `build_assistant(intent="exotic_armor", character="hunter")` | 特性图 |
| `data.equipped_armor.characters[].items[].name` | `inventory_assistant(intent="mods", character="hunter")` | 五件护甲清单行（轻量） |
| `data.equipped_armor.characters[].items[].icon_url` | `inventory_assistant(intent="mods", character="hunter")` | 护甲图 |
| `data.equipped_armor.characters[].items[].slot_key` | `inventory_assistant(intent="mods", character="hunter")` | 部位键（`helmet`…） |
| `data.equipped_armor.characters[].items[].is_exotic` | `inventory_assistant(intent="mods", character="hunter")` | 是否金装 |
| `data.equipped_armor.characters[].items[].power` | `inventory_assistant(intent="mods", character="hunter")` | 光等 |
| `data.equipped_armor.characters[].items[].energy.capacity` | `inventory_assistant(intent="mods", character="hunter")` | 能量上限 |
| `data.equipped_armor.characters[].items[].energy.used` | `inventory_assistant(intent="mods", character="hunter")` | 已用能量 |

### 降级

- `gear_tier` 为 `null` → 写"无分级"并附 `gear_tier_note`；**不许**写成 T0。
- `tuning.installed=false` → 不画调谐行（`name` 是"空调整模组插槽"这种占位）。
- `location` 是角色名（`hunter`）时按职业中文名显示；它是**角色名**不是"已装备"，
  是否穿着看 `is_equipped`。

---

## 六、活动 / 战绩行

### 6.1 最近活动 `activity_assistant(intent="history", count=5)`

行：`活动名 | 模式 | 角色 | 完成 | 击杀/死亡/助攻 | 用时 | 开始时间 | 图标`。

| 字段路径 | 出处 | 说明 |
| --- | --- | --- | --- |
| `summary` | `activity_assistant(intent="history", count=5)` | 摘要 |
| `data.activities[].activity_name` | `activity_assistant(intent="history", count=5)` | 活动名 |
| `data.activities[].icon_url` | `activity_assistant(intent="history", count=5)` | 活动图（活动道，`pgcrImage`） |
| `data.activities[].instance_id` | `activity_assistant(intent="history", count=5)` | 场次 ID；要单场详情就回传它 |
| `data.activities[].character` | `activity_assistant(intent="history", count=5)` | 角色 |
| `data.activities[].mode_name` | `activity_assistant(intent="history", count=5)` | 模式中文名 |
| `data.activities[].is_completed` | `activity_assistant(intent="history", count=5)` | 完成与否；**false 不是"失败"**，是没打完 |
| `data.activities[].kills` | `activity_assistant(intent="history", count=5)` | 击杀（上游是浮点） |
| `data.activities[].deaths` | `activity_assistant(intent="history", count=5)` | 死亡 |
| `data.activities[].assists` | `activity_assistant(intent="history", count=5)` | 助攻 |
| `data.activities[].time_played` | `activity_assistant(intent="history", count=5)` | 用时串（`1m 30s`） |
| `data.activities[].standing` | `activity_assistant(intent="history", count=5)` | Victory/Defeat 等 |
| `data.activities[].start_time` | `activity_assistant(intent="history", count=5)` | ISO 时间 |

**口径**：这是"最近 N 场"（`count` 决定的窗口），卡头要写"最近 5 场"这类字样。

### 6.2 单场结算 `activity_assistant(intent="pgcr", activity_id="$activity_id")`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.pgcr.activity_name` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 活动名 |
| `data.pgcr.icon_url` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 活动图 |
| `data.pgcr.start_time` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 开始时间 |
| `data.pgcr.team_count` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 队伍数 |
| `data.pgcr.entries[].player_name` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 玩家（可能含 `#1234`） |
| `data.pgcr.entries[].class` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 职业 |
| `data.pgcr.entries[].light_level` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 光等 |
| `data.pgcr.entries[].kills` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 击杀 |
| `data.pgcr.entries[].deaths` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 死亡 |
| `data.pgcr.entries[].assists` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 助攻 |
| `data.pgcr.entries[].kd_ratio` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | KD（**字符串**） |
| `data.pgcr.entries[].score` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 得分 |
| `data.pgcr.entries[].standing` | `activity_assistant(intent="pgcr", activity_id="$activity_id")` | 胜负 |

### 6.3 突袭/地牢报表 `activity_assistant(intent="raid_report", mode="raid")`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.rows[].activity` | `activity_assistant(intent="raid_report", mode="raid")` | 副本名 |
| `data.rows[].icon_url` | `activity_assistant(intent="raid_report", mode="raid")` | 副本图（活动道） |
| `data.rows[].completions` | `activity_assistant(intent="raid_report", mode="raid")` | 完成次数（组件 1100） |
| `data.rows[].sherpas` | `activity_assistant(intent="raid_report", mode="raid")` | 导师**人数**，不是场次 |
| `data.rows[].flawless` | `activity_assistant(intent="raid_report", mode="raid")` | **null = 本次没取到这一列**，不是 0 |
| `data.rows[].solo_flawless` | `activity_assistant(intent="raid_report", mode="raid")` | 同上 |
| `data.rows[].missing_counters` | `activity_assistant(intent="raid_report", mode="raid")` | 哪几列没取到，逐项印在卡上 |
| `data.rows[].full_clears` | `activity_assistant(intent="raid_report", mode="raid")` | 扫过 PGCR 才有；null + `not_scanned` |
| `data.rows[].fastest_seconds` | `activity_assistant(intent="raid_report", mode="raid")` | 同上 |
| `data.rows[].not_scanned` | `activity_assistant(intent="raid_report", mode="raid")` | true = 这两列还没扫，写"未扫描" |
| `data.totals.completions` | `activity_assistant(intent="raid_report", mode="raid")` | 合计 |
| `data.totals.sherpas` | `activity_assistant(intent="raid_report", mode="raid")` | 合计 |
| `data.coverage.flawless.rows_with_counter` | `activity_assistant(intent="raid_report", mode="raid")` | 有这一列的副本数 |
| `data.coverage.flawless.rows_total` | `activity_assistant(intent="raid_report", mode="raid")` | 副本总数 |
| `data.unavailable[].field` | `activity_assistant(intent="raid_report", mode="raid")` | 官方就没有的列（day_one_rank / ranks） |
| `data.unavailable[].reason` | `activity_assistant(intent="raid_report", mode="raid")` | 为什么没有，照抄给用户 |

**口径**：`flawless=null` 与 `flawless=0` 是两件事；`data.unavailable` 里的列**不许**
在表里留空格子假装是 0，要么不画这一列，要么写"官方无此口径"。

### 6.4 PvP 武器榜 `activity_assistant(intent="pvp_weapons", count=10)`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.pvp_weapons.window.newest` | `activity_assistant(intent="pvp_weapons", count=10)` | 窗口终点 |
| `data.pvp_weapons.window.oldest` | `activity_assistant(intent="pvp_weapons", count=10)` | 窗口起点 |
| `data.pvp_weapons.window.matches_analyzed` | `activity_assistant(intent="pvp_weapons", count=10)` | 实际分析了几场 |
| `data.pvp_weapons.mode_group.label` | `activity_assistant(intent="pvp_weapons", count=10)` | 模式组中文名 |
| `data.pvp_weapons.activities[].name` | `activity_assistant(intent="pvp_weapons", count=10)` | 这批场次里打过的地图 |
| `data.pvp_weapons.activities[].icon_url` | `activity_assistant(intent="pvp_weapons", count=10)` | 地图图 |
| `data.pvp_weapons.activities[].matches` | `activity_assistant(intent="pvp_weapons", count=10)` | 每张图几场 |
| `data.pvp_weapons.mode_tally[].name` | `activity_assistant(intent="pvp_weapons", count=10)` | 逐模式场次 |
| `data.pvp_weapons.mode_tally[].matches` | `activity_assistant(intent="pvp_weapons", count=10)` | 场次数 |
| `data.pvp_weapons.weapons[].name` | `activity_assistant(intent="pvp_weapons", count=10)` | 武器名 |
| `data.pvp_weapons.weapons[].icon_url` | `activity_assistant(intent="pvp_weapons", count=10)` | 武器图 |
| `data.pvp_weapons.weapons[].kills` | `activity_assistant(intent="pvp_weapons", count=10)` | 击杀 |
| `data.pvp_weapons.weapons[].precision_kills` | `activity_assistant(intent="pvp_weapons", count=10)` | 精准击杀 |
| `data.pvp_weapons.weapons[].precision_rate` | `activity_assistant(intent="pvp_weapons", count=10)` | 0–1 的比例，**渲染成百分比要 ×100** |
| `data.pvp_weapons.weapons[].kill_share` | `activity_assistant(intent="pvp_weapons", count=10)` | 0–1 占本窗口武器击杀的比例 |
| `data.pvp_weapons.weapons[].kills_per_match` | `activity_assistant(intent="pvp_weapons", count=10)` | 场均 |
| `data.pvp_weapons.failed_matches.total` | `activity_assistant(intent="pvp_weapons", count=10)` | 结算失败场次；非 0 要印 |

**口径（必须印在卡上）**：这是**最近 N 场**的聚合，不是生涯；卡头写
`最近 {matches_analyzed} 场（{oldest} → {newest}）`。响应里那两条 `warnings`
就是这个意思，别省。

### 6.5 武器使用排行 `activity_assistant(intent="weapon_history", count=5)`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.scope` | `activity_assistant(intent="weapon_history", count=5)` | `all_modes` = PvE+PvP 合计，**不是 PvP 榜** |
| `data.count` | `activity_assistant(intent="weapon_history", count=5)` | 一共多少把有记录 |
| `data.weapons[].name` | `activity_assistant(intent="weapon_history", count=5)` | 武器名 |
| `data.weapons[].icon_url` | `activity_assistant(intent="weapon_history", count=5)` | 武器图 |
| `data.weapons[].kills_display` | `activity_assistant(intent="weapon_history", count=5)` | 带千分位的击杀串，优先用它 |
| `data.weapons[].precision_kills_display` | `activity_assistant(intent="weapon_history", count=5)` | 精准击杀串 |

### 6.6 活动累计 `activity_assistant(intent="aggregate", count=5)`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.count` | `activity_assistant(intent="aggregate", count=5)` | 一共多少种活动 |
| `data.activities[].activity_name` | `activity_assistant(intent="aggregate", count=5)` | 活动名 |
| `data.activities[].icon_url` | `activity_assistant(intent="aggregate", count=5)` | 活动图 |
| `data.activities[].completions_display` | `activity_assistant(intent="aggregate", count=5)` | 完成数显示串 |
| `data.activities[].kills_display` | `activity_assistant(intent="aggregate", count=5)` | 击杀显示串 |
| `data.activities[].seconds_played_display` | `activity_assistant(intent="aggregate", count=5)` | 时长串 |

### 6.7 游戏内计数器 `activity_assistant(intent="counters", count=5)`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.counters[].name` | `activity_assistant(intent="counters", count=5)` | 计数器名 |
| `data.counters[].progress` | `activity_assistant(intent="counters", count=5)` | 游戏内数字（生涯） |
| `data.counters[].completion_value` | `activity_assistant(intent="counters", count=5)` | 上限；为 0/1 时别当进度条分母 |
| `data.counters[].mode_label` | `activity_assistant(intent="counters", count=5)` | 分类 |
| `data.counters[].period_label` | `activity_assistant(intent="counters", count=5)` | 周期；空串 = 没周期标注 |

**口径**：计数器（组件 1100）与 `stats` 接口**不是一套数**，两张卡不能并列比较、
更不能相加。

### 6.8 生涯统计 `activity_assistant(intent="stats")`

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.stats.groups[].label` | `activity_assistant(intent="stats")` | PvE / PvP 分组 |
| `data.stats.groups[].stats[].name` | `activity_assistant(intent="stats")` | 指标中文名 |
| `data.stats.groups[].stats[].account_total` | `activity_assistant(intent="stats")` | 账号合计（**已含已删角色**） |
| `data.stats.groups[].stats[].existing` | `activity_assistant(intent="stats")` | 现存角色 |
| `data.stats.groups[].stats[].deleted` | `activity_assistant(intent="stats")` | 已删角色明细 |
| `data.stats.groups[].stats[].display` | `activity_assistant(intent="stats")` | 显示串 |
| `data.stats.characters.existing` | `activity_assistant(intent="stats")` | 现存角色数 |
| `data.stats.characters.deleted` | `activity_assistant(intent="stats")` | 已删角色数 |
| `data.stats.characters.total` | `activity_assistant(intent="stats")` | 合计 |
| `data.stats.period.label` | `activity_assistant(intent="stats")` | 周期（生涯） |

**口径**：三档（existing / deleted / account_total）**不许相加**：`account_total`
已经含了已删角色。卡上写清楚这是账号级还是单角色（看 `data.stats.scope`）。

---

## 七、配装候选卡

```html type="renderer"
<section style="background:#101317;border:1px solid #3a424b;border-radius:7px;overflow:hidden;color:#e8e9e6">
  <header style="display:flex;justify-content:space-between;gap:14px;align-items:center;padding:12px 14px;background:#14181d;border-bottom:1px solid #2a3037">
    <h3 style="margin:0;font-size:14px;color:#fafaf7">候选配装 1</h3>
    <span style="font-size:9px;color:#c4c9cd;background:#1a2026;border:1px solid #2a3037;border-radius:5px;padding:3px 7px">分数 507</span>
  </header>
  <!-- 六维：当前值 + 目标（有目标时才画目标） -->
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));border-bottom:1px solid #2a3037">
    <div style="padding:8px 11px;border-right:1px solid #1d2227">
      <span style="display:block;font-size:9px;color:#9fa6ac">武器</span>
      <strong style="display:block;margin-top:2px;font-size:13px;color:#fafaf7">180</strong>
    </div>
  </div>
  <!-- 五件：44px 图 + 名字 + 部位 + 光等 + 调谐 -->
</section>
```

### 字段表

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.builds[].score` | `build_assistant(intent="find", character="hunter", top_n=2)` | 排序分；**不是达成率** |
| `data.builds[].completion_rate` | `build_assistant(intent="find", character="hunter", top_n=2)` | 可为 null（没硬目标时无意义），配 `completion_rate_note` |
| `data.builds[].completion_rate_note` | `build_assistant(intent="find", character="hunter", top_n=2)` | null 的原因，照抄 |
| `data.builds[].execution_id` | `build_assistant(intent="find", character="hunter", top_n=2)` | 服务端签发的执行凭据（只用于回传，不显示） |
| `data.builds[].stats.weapons` | `build_assistant(intent="find", character="hunter", top_n=2)` | 六维之一；同组 `health`/`class_stat`/`grenade`/`melee`/`super_stat` |
| `data.builds[].items[].name` | `build_assistant(intent="find", character="hunter", top_n=2)` | 五件装备名 |
| `data.builds[].items[].icon_url` | `build_assistant(intent="find", character="hunter", top_n=2)` | 护甲图 |
| `data.builds[].items[].slot_display` | `build_assistant(intent="find", character="hunter", top_n=2)` | 部位中文 |
| `data.builds[].items[].power` | `build_assistant(intent="find", character="hunter", top_n=2)` | 光等 |
| `data.builds[].items[].energy_capacity` | `build_assistant(intent="find", character="hunter", top_n=2)` | 能量上限 |
| `data.builds[].items[].is_exotic` | `build_assistant(intent="find", character="hunter", top_n=2)` | 金装标记 |
| `data.builds[].items[].set_bonus_name` | `build_assistant(intent="find", character="hunter", top_n=2)` | 套装名，可空串 |
| `data.builds[].items[].tuning_name` | `build_assistant(intent="find", character="hunter", top_n=2)` | 调谐名（"平衡调整"等） |
| `data.builds[].exotic` | `build_assistant(intent="find", character="hunter", top_n=2)` | 这套的金装名，可空串 |
| `data.builds[].set[]` | `build_assistant(intent="find", character="hunter", top_n=2)` | 生效套装名列表 |
| `data.builds[].requires_tuning` | `build_assistant(intent="find", character="hunter", top_n=2)` | 需要调谐才能达标 |
| `data.builds[].missing_requirements[]` | `build_assistant(intent="find", character="hunter", top_n=2)` | 缺什么；非空要印 |
| `data.query.character` | `build_assistant(intent="find", character="hunter", top_n=2)` | 本次查询的角色 |
| `data.query.targets.weapons` | `build_assistant(intent="find", character="hunter", top_n=2)` | 硬目标（同组另有 class/grenade/melee/super/health）；**可能是 null** |
| `data.reachable.weapons` | `build_assistant(intent="find", character="hunter", top_n=2)` | 保守下界，**不是上限**；要印 `reachable_note` |
| `data.loadouts[].name` | `loadout_assistant(intent="list")` | 已存配装清单行 |
| `data.loadouts[].character` | `loadout_assistant(intent="list")` | 角色 |
| `data.loadouts[].item_count` | `loadout_assistant(intent="list")` | 件数 |
| `data.loadouts[].exotic_armor` | `loadout_assistant(intent="list")` | 金装名，可空串 |
| `data.loadouts[].subclass` | `loadout_assistant(intent="list")` | 子职业 |
| `data.loadouts[].execution_supported` | `loadout_assistant(intent="list")` | 能不能一键穿 |
| `data.loadouts[].visuals.armor.exotic.name` | `loadout_assistant(intent="list", limit=3)` | 这套的金装名（与同行的 `exotic_armor` 同一个值；这里是能画图的那一块） |
| `data.loadouts[].visuals.armor.exotic.icon_url` | `loadout_assistant(intent="list", limit=3)` | 金装图；**可为空串**（名字在那五件里对不唯一就不给图，不猜） |
| `data.loadouts[].visuals.subclass.name` | `loadout_assistant(intent="list", limit=3)` | 子职业名（Manifest 全名，如「棱镜猎人」） |
| `data.loadouts[].visuals.subclass.icon_url` | `loadout_assistant(intent="list", limit=3)` | 子职业图；**可为空串** |
| `data.total_loadouts` | `loadout_assistant(intent="list")` | 总套数 |
| `data.next_offset` | `loadout_assistant(intent="list")` | 翻页偏移 |
| `data.loadouts[].build_template.title` | `loadout_assistant(intent="get")` | 配装标题 |
| `data.loadouts[].build_template.subclass` | `loadout_assistant(intent="get")` | 子职业 |
| `data.loadouts[].build_template.class.icon_url` | `loadout_assistant(intent="get")` | 子职业**自己**的图（2026-10-05 补：以前只有 `class.plugs[]` 有图，标题行只能放色块） |
| `data.loadouts[].build_template.weapons[].name` | `loadout_assistant(intent="get")` | 武器 |
| `data.loadouts[].build_template.weapons[].icon_url` | `loadout_assistant(intent="get")` | 武器图 |
| `data.loadouts[].build_template.armor.items[].name` | `loadout_assistant(intent="get")` | 护甲 |
| `data.loadouts[].build_template.armor.items[].icon_url` | `loadout_assistant(intent="get")` | 护甲图 |
| `data.loadouts[].build_template.armor.items[].slot` | `loadout_assistant(intent="get")` | 部位键 |
| `data.loadouts[].build_template.executable` | `loadout_assistant(intent="get")` | **不是**可执行 build 的证明；要装备走服务端签发那条路 |

### 降级

- `completion_rate` 为 `null` → 写 `completion_rate_note` 的那句话，**不写 0%**。
- `query.targets.*` 全为 null → 不画"目标"列；`reachable` 要带"保守下界"说明。
- `builds[].items[]` 少于 5 件 → 照实画几件，别补空位。
- 已存配装**清单行**（`intent="list"`）的图只有两处：`visuals.armor.exotic.icon_url`
  与 `visuals.subclass.icon_url`。**空串 = 解析不到**（金装名在那套的五件里对不唯一、
  或者这套是旧记录没有模板）→ 画同尺寸占位块 + 只写名字，**别写 `<img src="">`**，
  也别为了补图再调一次 `intent="get"`（清单页要为几十套各调一次）。套装（`armor_set`）
  **永远**没有图（`DestinyEquipableItemSetDefinition` 实测 `hasIcon: false`），别给它留图位。

---

## 八、清单表

行模板（`inventory` 面）：36px 图 + 名字 + 一行小字（类型 · 位置 · 光等 · 已装备）。

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.inventory.total` | `inventory_assistant(intent="summary")` | 总件数 |
| `data.inventory.counts.by_location[].label` | `inventory_assistant(intent="summary")` | 位置分组名 |
| `data.inventory.counts.by_location[].count` | `inventory_assistant(intent="summary")` | 该位置件数（`by_bucket`/`by_item_type` 同形） |
| `data.inventory.equipped[].name` | `inventory_assistant(intent="summary")` | 已装备示例行 |
| `data.inventory.equipped[].type` | `inventory_assistant(intent="summary")` | 类型 |
| `data.inventory.equipped[].power` | `inventory_assistant(intent="summary")` | 光等 |
| `data.inventory.equipped[].location` | `inventory_assistant(intent="summary")` | 位置 |
| `data.inventory.equipped[].icon_url` | `inventory_assistant(intent="summary")` | 图标 |
| `data.inventory.highest_power[].name` | `inventory_assistant(intent="summary")` | 最高光等示例行 |
| `data.inventory.items[].name` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 清单行 |
| `data.inventory.items[].icon_url` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 图标 |
| `data.inventory.items[].item_type_display` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 类型中文 |
| `data.inventory.items[].power` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 光等 |
| `data.inventory.items[].location` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 位置键（`vault`/`hunter`…） |
| `data.inventory.items[].is_equipped` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 是否装着 |
| `data.inventory.items[].quantity` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 数量 |
| `data.inventory.items[].gear_tier` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 列表行的 T 级（可为 null） |
| `data.inventory.total_items` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 命中总数 |
| `data.inventory.returned_items` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 本次返回数 |
| `data.inventory.truncated` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 截断标记 |
| `data.inventory.next_offset` | `inventory_assistant(intent="get", item_type="armor", limit=5)` | 翻页偏移 |
| `data.result.items[].name` | `inventory_assistant(intent="search", item_name="星狐座")` | 按名字搜到的行 |
| `data.result.items[].icon_url` | `inventory_assistant(intent="search", item_name="星狐座")` | 图标 |
| `data.result.items[].item_type_display` | `inventory_assistant(intent="search", item_name="星狐座")` | 类型 |
| `data.result.items[].power` | `inventory_assistant(intent="search", item_name="星狐座")` | 光等 |
| `data.result.items[].location` | `inventory_assistant(intent="search", item_name="星狐座")` | 位置 |
| `data.weapons.items[].name` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 按类型列账号武器 |
| `data.weapons.items[].icon_url` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 图标 |
| `data.weapons.items[].damage_type` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 伤害类型 |
| `data.weapons.items[].ammo_type` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 弹药类型 |
| `data.weapons.items[].gear_tier` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | T 级；null = 不在分级体系内 |
| `data.weapons.items[].item_level` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 装备等级 |
| `data.weapons.total` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 命中总数 |
| `data.weapons.returned` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 返回数 |
| `data.weapons.truncated` | `weapon_assistant(intent="type", weapon_type="手炮", limit=5)` | 截断标记 |
| `data.patterns.items[].name` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 锻造图样行 |
| `data.patterns.items[].icon_url` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 图标 |
| `data.patterns.items[].weapon_type` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 类型 |
| `data.patterns.items[].tier` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 稀有度 |
| `data.patterns.items[].need` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 需要几个（分母） |
| `data.patterns.items[].progress` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 现在几个（游戏里那条 4/5） |
| `data.patterns.items[].remaining` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 还差几个 |
| `data.patterns.items[].status` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | `已解锁`/`未开始` 等；**未开始 ≠ 0/5** |
| `data.patterns.items[].source` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 掉落来源（社区资料） |
| `data.counts.unlocked` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 已解锁条数 |
| `data.counts.total` | `weapon_assistant(intent="patterns", rarity="异域", limit=5)` | 本页筛出的总条数 |
| `data.duplicate_weapons[].name` | `inventory_assistant(intent="duplicates", limit=3)` | 重复武器组 |
| `data.duplicate_weapons[].icon_url` | `inventory_assistant(intent="duplicates", limit=3)` | **组级**图标 —— 同一组里每一把都用它（实例行**没有**这个字段，不是空串：体积口径；细则在 §八 的降级） |
| `data.duplicate_weapons[].weapon_type` | `inventory_assistant(intent="duplicates", limit=3)` | 类型 |
| `data.duplicate_weapons[].instance_count` | `inventory_assistant(intent="duplicates", limit=3)` | 这一组几件 |
| `data.duplicate_weapons[].instances[].location` | `inventory_assistant(intent="duplicates", limit=3)` | 副本位置 |
| `data.duplicate_weapons[].instances[].power` | `inventory_assistant(intent="duplicates", limit=3)` | 副本光等 |
| `data.duplicate_weapons[].instances[].equipped` | `inventory_assistant(intent="duplicates", limit=3)` | 是否装着 |
| `data.duplicate_weapons[].instances[].perks[].name` | `inventory_assistant(intent="duplicates", limit=3)` | perk 行（**只有 name/slot，没有图**） |
| `data.duplicate_weapons[].instances[].perks[].slot` | `inventory_assistant(intent="duplicates", limit=3)` | perk 栏 |
| `data.duplicate_weapons[].instances[].perks_complete` | `inventory_assistant(intent="duplicates", limit=3)` | false = 这件的 perk 数据不全，要写在行上 |
| `data.scan.duplicate_scan_complete` | `inventory_assistant(intent="duplicates", limit=3)` | 是否扫完整份背包 |
| `data.scan.perk_data_complete` | `inventory_assistant(intent="duplicates", limit=3)` | 是否每件都读到 perk |
| `data.scan.weapon_instances` | `inventory_assistant(intent="duplicates", limit=3)` | 扫了多少件 |
| `data.scan.duplicate_groups` | `inventory_assistant(intent="duplicates", limit=3)` | 一共多少组 |
| `data.pagination.total` | `inventory_assistant(intent="duplicates", limit=3)` | 总组数 |
| `data.pagination.has_more` | `inventory_assistant(intent="duplicates", limit=3)` | 还有下一页 |
| `data.pagination.next_offset` | `inventory_assistant(intent="duplicates", limit=3)` | 翻页偏移 |

### 降级

- 截断（`truncated=true`）→ 卡头必须写"本次 N / 共 M"，并给 `next_offset`。
- `perk_data_complete=false` → 卡头写"perk 数据不完整"；`perks_complete=false` 的行
  单独标一句，**不许**把空 perk 列表画成"没有 perk"。
- **`duplicates` 的实例行：每一把都用组级 `icon_url`，不要画占位块。**
  实例行**没有** `icon_url` 这个字段（不是空串）—— 那是**体积口径**（每个实例给图 +17.3%，
  见 `docs/adr/021`），不是漏了。同一组的每一把**本来就是同一张图**（分组判据是
  `exact_item_hash_and_distinct_instance_id`），所以渲染实例时直接复用
  `data.duplicate_weapons[].icon_url`：既不画占位块，也不要写成"只有第一把有图"、
  更不要为了凑图去调别的 intent（那样是白花一次调用）。
  判据是"组级有没有图"：组级也空串时才画占位块（那次是真查不到定义）。

---

## 九、周常 · 货架行

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.rows[].kind_label` | `world_assistant(intent="rotations")` | 行类别（夜幕/宗师、上维挑战…） |
| `data.rows[].name` | `world_assistant(intent="rotations")` | 活动名 |
| `data.rows[].difficulty` | `world_assistant(intent="rotations")` | 难度，可空串 |
| `data.rows[].icon_url` | `world_assistant(intent="rotations")` | 活动图 |
| `data.rows[].recommended_light` | `world_assistant(intent="rotations")` | 推荐光等 |
| `data.rows[].completed` | `world_assistant(intent="rotations")` | 本周是否已打 |
| `data.rows[].modifiers[]` | `world_assistant(intent="rotations")` | 词缀（字符串列表） |
| `data.rows[].source` | `world_assistant(intent="rotations")` | `official` = 官方口径；`schedule` = 我们自排的表 |
| `data.rows[].rewards[].name` | `world_assistant(intent="rotations")` | 掉落名 |
| `data.rows[].rewards[].icon_url` | `world_assistant(intent="rotations")` | 掉落图 |
| `data.rows[].rewards[].quantity` | `world_assistant(intent="rotations")` | 数量 |
| `data.vendors.vendors[].name` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 商人名 |
| `data.vendors.vendors[].icon_url` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 商人**自己**那一行的图（方形徽标；菜单模式下每个候选商人也有） |
| `data.vendors.vendors[].sale_items[].name` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 在卖什么 |
| `data.vendors.vendors[].sale_items[].icon_url` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 商品图 |
| `data.vendors.vendors[].sale_items[].item_type` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 类型 |
| `data.vendors.vendors[].sale_items[].tier` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 稀有度 |
| `data.vendors.vendors[].sale_items[].can_be_sold` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | false 必须配 `failure_reasons` 一起显示 |
| `data.vendors.vendors[].sale_items[].failure_reasons[]` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 为什么买不了（"需要等级4"） |
| `data.vendors.vendors[].sale_items[].owned` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 账号里已有 |
| `data.vendors.vendors[].rank.name` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 声望名 |
| `data.vendors.vendors[].rank.level` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 声望等级 |
| `data.vendors.vendors[].rank.next_level_at` | `world_assistant(intent="vendor", vendor_name="班西-44", limit=3)` | 下一级门槛 |

### 降级

- `source="schedule"` 的行必须标"自维护表（带核对日期）"，不能和官方口径的行混在一张表里不区分。
- 商人是**窗口数据**（本次刷新），卡头写 `next_refresh`。
- 轮换行的图分两档，**别把"给不出"画成"图挂了"**：官方那半（特色突袭/地牢、夜幕/宗师）
  的行有 `icon_url`；**自维护表那半（上维挑战 / 异域任务轮换 / 泉源）没有这个字段**
  （表里只存名字、没有活动 hash —— 名字→活动定义实测对不上，宁可不给）→ 那些行**不画图位**，
  按纯文字行渲染（§三 的"不带图行"），不要画占位块。

---

## 十、社区配装卡

**两档**（用户 2026-10-05 口径）：**列表先给简版**（每套只出护甲/武器/子职业的图 + 名字），
**用户追问某一套的详细内容时再给全部**（`validation.requirements[]` 每一行都有图 + 名字）。

这也解释了为什么有两组字段：`data.results[].visuals` 是简版，只在 `community` **列表**上；
`data.selected_build.*` 是详情，只在带 `community_build_id` 时出现。

### 10.1 列表（简版）

```html type="renderer"
<section style="background:#101317;border:1px solid #3a424b;border-radius:7px;overflow:hidden;color:#e8e9e6">
  <header style="padding:12px 14px;background:#14181d;border-bottom:1px solid #2a3037">
    <h3 style="margin:0;font-size:14px;color:#fafaf7">社区配装 · 第 1 套</h3>
  </header>
  <!-- 简版只有三行；每行 32px 图 + 名字。icon_url 为空串时画同尺寸占位块，别写 <img src=""> -->
  <div style="padding:11px 14px">
    <div style="display:grid;grid-template-columns:32px minmax(0,1fr);gap:9px;align-items:center">
      <img src="{{visuals.subclass.icon_url}}" alt="子职业图标" loading="lazy" style="width:32px;height:32px;display:block;object-fit:cover;background:#1a2026;border:1px solid #2a3037;border-radius:2px">
      <span style="font-size:12px;color:#e8e9e6">{{visuals.subclass.name}}</span>
    </div>
  </div>
</section>
```

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.results[].title` | `build_assistant(intent="community", query="猎人", top_n=5)` | 套名（卡片标题） |
| `data.results[].visuals.subclass.name` | `build_assistant(intent="community", query="猎人", top_n=5)` | 子职业名（解析出来的是 Manifest 的**全名**，如「棱镜猎人」） |
| `data.results[].visuals.subclass.icon_url` | `build_assistant(intent="community", query="猎人", top_n=5)` | 子职业图；**可为空串** |
| `data.results[].visuals.weapons[].name` | `build_assistant(intent="community", query="猎人", top_n=5)` | 武器名 |
| `data.results[].visuals.weapons[].icon_url` | `build_assistant(intent="community", query="猎人", top_n=5)` | 武器图；**可为空串**（解析不到就不出图） |
| `data.results[].visuals.armor.exotic.name` | `build_assistant(intent="community", query="猎人", top_n=5)` | 异域护甲名；职业金装给的是还原后的**金装名** |
| `data.results[].visuals.armor.exotic.icon_url` | `build_assistant(intent="community", query="猎人", top_n=5)` | 异域护甲图；**可为空串** |
| `?data.results[].visuals.armor.set.name` | `build_assistant(intent="community", query="猎人", top_n=5)` | 套装名；模板没写套装时整块没有 |
| `?data.results[].visuals.armor.set.icon_url` | `build_assistant(intent="community", query="猎人", top_n=5)` | **恒为空串** —— 套装在 Manifest 里 `hasIcon: false`，没有这张图 |
| `data.matched_count` | `build_assistant(intent="community", query="猎人", top_n=5)` | 命中多少套（卡片头写"第 N / 共 M 套"） |
| `data.next_offset` | `build_assistant(intent="community", query="猎人", top_n=5)` | 翻页偏移；非 null 说明还有下一页 |

### 10.2 详情（全部）

| 字段路径 | 出处 | 说明 |
| --- | --- | --- |
| `data.selected_build.title` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 套名 |
| `data.selected_build.visuals.weapons[].name` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 同简版三块（详情里也在，省得调用方再解析一遍） |
| `data.selected_build.visuals.subclass.icon_url` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 子职业图 |
| `data.selected_build.validation.requirements[].name` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | **全部细节**：武器 / 武器 perk / 异域护甲 / 套装 / 护甲模组 / 神器 / 神器模组 / 子职业组件 |
| `data.selected_build.validation.requirements[].kind` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 这一行是什么（决定画哪个图标位） |
| `data.selected_build.validation.requirements[].status` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | `resolved` / `ambiguous` / `unresolved`；**不是 resolved 就别画成"找到了"** |
| `data.selected_build.validation.requirements[].icon_url` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 这一行的图；**可为空串** |
| `data.selected_build.validation.requirements[].definitions[].item_hash` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 解析到的 Manifest hash（**可核**：图就是按它取的） |
| `?data.selected_build.validation.requirements[].perk_resolutions[].name` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 武器要求的 perk 行 |
| `?data.selected_build.validation.requirements[].perk_resolutions[].icon_url` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | perk 图 |
| `data.selected_build.inventory_match.inventory_status` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | 账号核对状态；账号不可用时是 `unavailable`，**不能读成"你没有"** |
| `data.selected_build.validation.execution_supported` | `build_assistant(intent="community", community_build_id="builds/s29/00vivy2a-hunter/index.html#build-1")` | false 必须在卡片上写出来（社区模板不是可执行配装） |

### 降级

- **`icon_url` 是空串 → 画同尺寸占位块 + 只写名字，别写 `<img src="">`，也别去找别的图顶上。**
  空串的含义是"这个名字解析不到、或同名定义的图不一致"，**不是漏了**：
  社区模板给的是散文，名字→物品这一跳必须能核（判据在 `destiny_mcp/services/starside_build_icons.py`）。
  实测反例：「斗牛士 64」精确名 0 命中、「埃希恩记忆」是套装（Manifest `hasIcon: false`）、
  「重型弹药搜寻者」3 个定义里有 2 种图。
- 套装行（`visuals.armor.set`）**永远**是空串 —— 别把套装的图省成"用某一件的图顶上"。
- `status != "resolved"` 的行照实标"名字对不上 Manifest"，不要因为它有名字就画成已确认。
- 列表是**简版**：不要顺手把 `data.results[].armor.mods`、`class.fragments` 这些也画出来 ——
  那正是"列表该简单"要避免的。要全部细节就再问一次带 `community_build_id` 的那一档。
