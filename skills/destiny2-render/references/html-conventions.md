# HTML 约定

这份文件定的不是审美，是**哪些写法在别人的对话宿主里活不下来**。老 web（`Destiny_MCP` 的
`webui/src/features/chat/DestinyBlocks.tsx` + `styles/app.css`）有一套现成的卡片样式，
但它是**自己的应用**：能吃外链 CSS、能跑 React 的 `onError`。模型把 HTML 写进对话里没有这些
前提，所以下面每条都注明了"老 web 怎么做 / 这里为什么改"。

---

## 零、交付包装：HTML 交给宿主时的那一行（**宿主特有**，不是 HTML 的通用写法）

**实测（2026-10-05，豆包客户端）**：同一个模型、同一套卡片数据，只换代码块的起始行 ——
起始行写成 ```` ```html type="renderer" ```` 时渲染成可视化卡片；写成普通的 ```` ```html ````
或**裸贴 HTML 字符串**时，整段 HTML 被**原样当源码显示**（用户看到的就是一大坨
`<div style=…>`）。所以"HTML 片段合法"和"宿主把它渲染成卡片"是两件事，中间还隔着一道包装：
**这一节给的不是某一个值，而是"这个宿主认哪一行"怎么定下来、换了宿主怎么重定。**

### 0.1 宿主表（**唯一出处**：哪个宿主认哪一行）

| 宿主 | 交付包装 | 依据 |
| --- | --- | --- |
| 豆包（chat renderer） | 代码块起始行写 ```` ```html type="renderer" ````，HTML 全写在块里 | **实测**（2026-10-05，豆包客户端）：同一套数据，普通 ```` ```html ```` 或裸 HTML 被原样当源码显示 |
| WorkBuddy | **未知 —— 先探测**（走 0.2；探出来之前按 SKILL.md §1 的"只能 Markdown"那一档发） | **没有实测**：只知道它可能有自己的格式，没有人验过 —— **不许编**，也不许拿豆包那一行顶 |
| Codex | **未知 —— 先探测**（同 0.2） | **没有实测**：同 WorkBuddy，不许编 |
| 其他 / 未知宿主 | **未知 —— 先探测**（同 0.2） | 包装是宿主特有的：`type="renderer"` 是豆包认的标记，不是 HTML 规范里的属性，也不是每个宿主都认 |

表里**没有**的宿主按最后一行办（就是未知宿主），别按"豆包那行应该也能用"猜。

### 0.2 探测：一次便宜、安全、可复现的确认（**本节的重点**）

宿主没验过（新宿主、新客户端版本、新入口都算），就**先探测、再发正式块**。
探测要便宜：**别先渲染一整张卡** —— 猜错一次，吐出去的就是一大坨源码，收不回来。

1. **发最小一块**：一个 `<div>` 加一行字，例如
   `<div style="border:1px solid #3a424b;padding:8px">渲染测试</div>`。
   **不带图、不带表格、不带账号数据** —— 带图会把"包装不对"和"图挂了"混在一起，看不出是哪一种。
2. **一次只换一个变量**（按序试，成功就停）：① 宿主自己的文档 / 示例里给的那一行
   （豆包 = ```` ```html type="renderer" ````，这是**已知值**）；② 普通 ```` ```html ```` 围栏；
   ③ 裸 HTML（连代码块都不包）。
3. **看判据**：出来的是**渲染成卡片 / 带样式的块** = 这个包装成立；**原样吐出源码** = 不成立，
   换下一个候选。三个都不成立，就当这个宿主不吃 HTML，走 SKILL.md §1 的下两档（Markdown / 纯文本）。
4. **定下来再发正式块**：第一块渲染成功的那一行，就是**这个环境**后面所有块要用的起始行。
5. **一个环境只探一次**：第一块定了之后，后面照它发 —— 不要每块都重探。

### 0.3 切换规矩（换宿主时最容易做错的三件事）

1. **不许把某个宿主的标记带到没验过的宿主**：`type="renderer"` 是豆包认的标记，不是 HTML 规范属性；
   换个没验过的宿主照抄，可能只是一个它不认识的标记 —— **换宿主 = 回到未知**。
2. **换了环境要重走一遍探测**：换宿主、换客户端版本、换入口（网页 / 桌面 / 插件）都算换了环境。
   **不在这个环境验过就当作未知** —— 上次验过、在别的环境验过，都不算数。
3. **探测结果要能被复用**：
   - **同一个环境**：第一块定下来的那一行覆盖后面**所有**块，不要每块重探；
   - **跨环境**：能写文件就把结论**记回 0.1 那张表**（探测 → 记一行 → 写依据 + 日期），
     下次**先查表**，表里有实测值就直接用；
   - 写不了文件（豆包这类对话宿主）就退一步：结论写在本次对话里、同一个环境继续用，
     换环境按第 2 条重探 —— **别把"上次在别处能用"当成"这里能用"**。

### 0.4 新宿主怎么加进来（扩展位：表是加行用的，不是封闭清单）

WorkBuddy / Codex 的格式一旦确认，**改一行就行**，这一节不用重写：

1. 在那个环境跑一遍 0.2 的探测（最小一块，别拿整张卡试）；
2. 在 0.1 的表里**加一行**：宿主 / 那一行 / 依据；
3. 依据只写**验过的**：实测就写日期 + 怎么测的；没验出来就写"未知 —— 先探测"；
   三个候选都不渲染，就记"这一档只能 Markdown"，**别留空、别猜**。

包装长这样（**豆包环境**实测的那一行；`blocks.md` 里的 5 段骨架用的就是它）：

```html type="renderer"
<section style="…">卡片内容</section>
```

**示例写错比不写示例更糟**：模型会照抄。所以 skill 里每一段 HTML 示例的起始行，都必须是
**那个宿主验过的那一行**（表里已有的实测值可以直接照抄；换宿主先按 0.2 探测，再动示例）。

---

## 一、标签白名单

只允许这些标签：

```
section article header div span h3 h4 p strong em small ul ol li
table thead tbody tr th td img br details summary code
```

- **不带 `class`**：宿主没有我们的样式表，class 只会是死字符串。所有样式写在元素的
  `style` 上。
- **禁止 `<style>` / `<link>` / `<script>` / `<iframe>` / `<svg>` / 外链字体 / 外链 CSS**：
  宿主随时会剥掉它们，剥掉之后卡片要么全裸要么空白。
- **禁止 `<a href>`**：卡片上的东西不需要可点（真要点，把 URL 当纯文本写出来）。
  这一条也是安全边界：卡片里的文字来自响应，能生成链接就等于能生成钓鱼链接。
- `details/summary` 用**可选**：宿主不支持时它会退化成"标题 + 内容"，仍然可读。老 web 的
  "关键差异"折叠块就是它（`DestinyBlocks.tsx` 的 `RollComparison`）。
- 属性只写 `style` / `src` / `alt` / `loading` / `title` / `aria-hidden` / `aria-label` /
  `colspan`。**不许**写 `on*` 事件属性（`onclick`、`onerror`…）：那是脚本，会被剥掉；
  少数宿主保留它反而是 XSS 面。图片加载失败的降级靠"给 `<img>` 自带底色"（见 §三），
  不靠 `onerror`。

## 二、内联样式白名单

只允许这些 CSS 属性，值用**字面量**（宿主不一定定义了我们的 `var(--…)`）：

| 类别 | 允许的属性 |
| --- | --- |
| 排版 | `font-size` `font-weight` `line-height` `font-family` `color` `text-align` `text-overflow` `white-space` `overflow-wrap` `word-break` `font-variant-numeric` `text-wrap` |
| 盒 | `display` `padding` `margin` `gap` `width` `height` `max-width` `min-width` `box-sizing` `overflow` `overflow-x` `object-fit` `vertical-align` |
| 网格/弹性 | `grid-template-columns` `grid-auto-flow` `grid-auto-columns` `flex` `flex-wrap` `align-items` `align-content` `justify-content` |
| 视觉 | `background` `background-color` `border` `border-radius` `list-style` `border-collapse` `table-layout` |

**禁止**：`position`（fixed/absolute/sticky 会跑出宿主的容器）、`z-index`、`transform`、
`filter`、`animation`、`transition`、`content`、`url(...)`、`@media`、`@import`、
`var(--…)`、`!important`、`cursor`。

配色照老 web 的 token（`styles/tokens.css` 默认主题）抄成字面值：

| 用途 | 值 |
| --- | --- |
| 卡片底 / 卡片边框 | `#101317` / `#3a424b` |
| 卡头底 / 细分隔线 | `#14181d` / `#2a3037` |
| 更浅的分隔 | `#1d2227` |
| 正文 / 强调字 | `#e8e9e6` / `#fafaf7` |
| 次要字 / 更次要字 | `#c4c9cd` / `#9fa6ac` |
| 强调（金） | `#d7b35e`；注意块底色 `rgba(213,168,95,.07)`，字色 `#e6c791` |
| 武器身份块底 / 图标描边 / 身份块踢脚字 | `#17141a` / `#806497` / `#cfbfd9` |
| 图标占位块底 / 描边 | `#25292d` / `#5d6670` |
| 列表小图标底 | `#25303c` |
| 进度条底 / 进度条填充 | `#282e34` / `#7bb0b5` |

字体栈写一次在卡片根上：
`font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif`；
数字用 `font-variant-numeric:tabular-nums`；等宽串（英文名、实例 ID）用
`font-family:ui-monospace,Consolas,monospace`。

## 三、图片：尺寸、空串、加载失败

**只有 `icon_url` 是图片键**（物品道与活动道同名，perk 同名）。除此之外的图片数据
（社区正文里的 Markdown 图、`data.sources.page.url` 这类页面地址）**都不是** `<img>` 的输入。

尺寸照老 web 的类（`styles/app.css:187–277`），单位固定：

| 位置 | 尺寸 | 底色 / 描边（照抄） |
| --- | --- | --- |
| 武器身份块大图 | 68×68，`border:2px solid #806497` | 底 `#1a2026` |
| 护甲身份块大图 | 68×68 | 同上 |
| 副本/清单行图标 | 44×44，`border:1px solid #806497` | 底 `#1a2026` |
| 列表行小图标 | 36×36，`border-radius:5px` | 底 `#25303c` |
| perk 行图标 | 30×30，`border:1px solid #5d6670`，`border-radius:2px` | 底 `#25292d` |
| 评分行图标 | 28×28 | 同上 |
| 副本内 perk 小图 | 24×24 | 同上 |
| 异域候选图 | 48×48，`border:2px solid #d7b35e` | 底 `#1a2026` |

每个 `<img>` 都要带：`src` / `alt`（`{{名字}} 图标`）/ `loading="lazy"` / 上面那组
`width`+`height`+`object-fit:cover`+`display:block`+底色+描边+圆角。

- **老 web 的降级**（`DestinyBlocks.tsx:106` 的 `ItemIcon`）：URL 为空 →
  渲染一个同样尺寸的空 `<span>`（`aria-hidden`），不是 `<img>`；加载失败 →
  `onError` 把图藏掉。我们是静态 HTML，**没有 onError**，所以把"底色 + 尺寸"直接写在
  `<img>` 自己身上：图挂了就是一个深色方块 —— 和老 web 的观感一致，且不会跳版。
- **`icon_url` 是空串时（本轮口径：缺图给空串）**：输出占位块
  `<span aria-hidden="true" style="width:30px;height:30px;display:block;background:#25292d;border:1px solid #5d6670;border-radius:2px"></span>`
  （尺寸换成该位置的值）。**绝对不要**写 `<img src="">`：空 src 在某些宿主里会去请求当前
  文档地址或显示破图图标。
- **绝对不要自己拼图标 URL**。图标文件名 ≠ 物品 hash（`hash → definition →
  displayProperties.icon`）。拼出来的地址长得像真的、但打不开，比缺图更难查。
  空串就按空串渲染。

## 四、转义（硬要求）

卡片里的每个值都来自响应，而响应里有**用户数据**（装备名、玩家名、公会名、自建配装名、
社区文本）。必须转义，否则一个 `<` 就能把卡片结构拆掉（这是注入，不是排版问题）。

在把值放进 HTML 之前，按这个顺序替换：

| 字符 | 替换成 |
| --- | --- |
| `&` | `&amp;` |
| `<` | `&lt;` |
| `>` | `&gt;` |
| `"` | `&quot;` |
| `'` | `&#39;` |

规则：

1. **`&` 必须先替换**（否则后面替换出来的实体会被二次转义）。
2. 属性值（`alt` / `title`）同样转义；不要用未转义的值拼 `style`。
3. **不要渲染 HTML 富文本**：响应里的描述、`text`、`snippet`、社区正文都是**纯文本或
   Markdown**，不是 HTML。要么原样当文本放（转义后），要么不渲染。绝不调用 Markdown
   渲染器再塞进卡片 —— 那会把社区内容变成可执行的标签。
4. 换行用 `white-space:pre-wrap` 保留，不要用 `<br>` 拼接外部文本（拼接处最容易漏转义）。

## 五、布局基准与"必须印出来的上下文"

- 卡片：`border-radius:7px`，`overflow:hidden`；分区之间用 `1px solid #2a3037` 的分隔线
  （老 web 用 `border-bottom`，照抄）。
- 属性格：`display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr))`。
- perk 池：`grid-auto-flow:column;grid-auto-columns:minmax(144px,1fr);overflow-x:auto`
  —— 横向滑动看完整池子，这是老 web 的 `destiny-rich-perk-slots`。
- 列表：`overflow-x:auto` 包一层 `<table style="width:100%;border-collapse:collapse">`；
  `th` 用 `background:#101317;color:#c4c9cd`，单元格 `padding:8px 10px`。
- 一行里的数字用 `<strong>`，单位/说明用 `<span>` 小字（老 web 的 `strong`+`span` 两层）。

**这些上下文不印出来，数字就是错的**（字段出处见 `blocks.md`）：

| 场景 | 必须印 |
| --- | --- |
| `pvp_weapons` | "最近 {matches_analyzed} 场（{oldest} → {newest}）"；`failed_matches.total` 非 0 也要写 |
| `weapon_history` | `scope=all_modes` 是**全模式**合计，不是 PvP 榜 |
| `history` | "最近 N 场"（`count` 的窗口） |
| 任何分页 | "本次 {returned} / 共 {total}"，下一页用 `next_offset` |
| `duplicates` | `duplicate_scan_complete` 与 `perk_data_complete` 的取值 |
| `raid_report` | `not_scanned` / `missing_counters` / `coverage.*` 与 `unavailable` 的原因 |
| `stats` | 账号级三档（`existing`/`deleted`/`account_total`）不相等也不相加；`scope` 是账号还是角色 |
| `counters` | 与 `stats` 不是一套数，不并列、不相加 |
| 社区 / `farming` / `popularity` | 来源名 + 更新时间 / `captured_at`；`trust: untrusted_reference` 照实标 |
| `rotations` | 每行 `source`（`official` vs `schedule`）；商人是本次刷新（`next_refresh`） |

## 六、社区资料（Starside）的图片：**归档相对路径，不是外链**

社区响应的正文里会带 Markdown 图片，形态是
`![](assets/elements/arc/icons/5fc7a97f63.webp)`。它是**归档里的相对路径**，不是
`https://` 地址 —— 服务端刻意不给 `starside.work` 热链（第三方站点，
`redistribution_license: not_established`），判据在仓库的 `destiny_mcp/services/starside_icons.py`。

所以：**不要把这种路径塞进 `<img src>`**，除非你确实能把归档挂成一个静态目录。
两种处理，按宿主能力二选一：

1. **宿主能读本地文件 / 起静态目录**：归档根是服务器的数据目录
   `<DATA_PATH>/starside/`（`DATA_PATH` 默认是仓库的 `data/`，可用环境变量覆盖）。
   把 `assets/…` 这个相对路径接在 `<mount>/` 后面即可，例如挂载
   `<DATA_PATH>/starside/` 到 `/starside/` 后写 `/starside/assets/elements/arc/icons/5fc7a97f63.webp`。
   挂载点由宿主决定，**不要**在卡片里写本机绝对路径。
2. **宿主读不到文件（豆包这类对话宿主就是这种）**：**别猜 URL、别热链第三方站点**。
   把图片那一小段去掉，保留文字：`{slot|![](…)}` 这种写法在正文里就是"这里本来有张图"，
   渲染时删掉 `![](...)` 只留周边的文字，并在需要时说明"图标在本地归档里"。

社区块永远带出处（`source.title` / `source.url` / `source.updated_at` /
`attribution.unofficial`），并标它是**别人怎么说**，不是官方数据。

## 七、回应里的 `unavailable` / `null`：写法固定

| 数据 | 渲染成 |
| --- | --- |
| `null`（有键无值） | "未取得"（+ 同一响应里的 `*_note` / `reason` / `missing_counters`） |
| `""`（空串） | 不显示这一项（不是空框） |
| `unavailable[]` | 那一列**不画**，或画成"官方无此口径"并附 `reason` |
| `available: false` | "本地没有这份数据" + `note`（例：`popularity.available=false` 的"没有不等于没人用"） |
| `coverage_complete: false` | 卡头标"覆盖不完整"；**0 命中不等于账号里没有** |
| `truncated: true` | "本次 N / 共 M"，并给 `next_offset` |

**"没查到" ≠ "没有"**：这是仓库的铁律，落到渲染上就是上面这张表 —— 任何一处把 `null`
画成 `0`、把空列表画成"没有"、把没扫过的列画成空格，都是错的。
