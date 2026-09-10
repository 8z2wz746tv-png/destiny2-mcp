# Starside 本地资料归档

从 [Starside](https://starside.work/index.html) 的公开导航、配装目录、搜索索引和静态资源引用发现内容，分类保存在本地。入口：[分类目录](CATALOG.md)。

## 目录说明

| 目录或文件 | 内容 |
| --- | --- |
| `CATALOG.md` | 按中文分类浏览所有资料页的入口 |
| `index.json` | 抓取状态、分类、静态资源清单、SHA-256、时间戳、失败和待下载列表 |
| `categories/` | 各分类的页面索引 JSON |
| `texts/` | 分类后的 Markdown 文本，配装保留原站复制文本 |
| `records/` | 每页 JSON：正文、来源 URL、日期、链接、多套配装、表格行列与 HTML 标记 |
| `pages/` | 按原始站点路径保存的 HTML |
| `assets/` | 按原始站点路径保存的图片、图标、字体、CSS 和 JS |
| `exports/starsideIndex.json` | 从公开搜索脚本中解析出的索引数据，不执行脚本 |
| `exports/starsideDesc.json` | 从公开详情脚本中解析出的说明词典，不执行脚本 |
| `external-links.json` | 站外来源和文档地址及引用它们的页面；不包含站外正文 |
| `metadata/` | 抓取规则检查记录 |

分类包括：`builds` 配装、`weapons` 武器、`armor` 护甲、`subclass` 职业与神器、`activities` 活动与输出、`mechanics` 机制、`sources` 来源与站务、`other` 首页等。

## 使用边界

- 只归档公开链接能够发现的同域静态内容，不保证覆盖作者未公开或未链接的文件。
- 没有登录、投稿、点赞或执行远端脚本；不下载站外腾讯文档、Discord 正文等内容。
- 原始 HTML 和 JS 保留原样，不是可直接使用的安全离线网站。打开原始 HTML 可能执行原站脚本、向外联网；优先阅读 Markdown 或 JSON。
- 表格文本不推断数值含义；保留单元格、合并行列属性及 PvP、未知数等 HTML 标记，以便后续正确解析。
- 配装文本与社区知识只是资料，不是经过游戏库存校验的装备执行计划；当前没有接入 MCP 装备流程。
- 原作者和上游作者保留权利。本地归档不构成转载许可，不应未经确认随项目发布。此目录已加入 Git 忽略。

## 继续或更新下载

在项目根目录运行：

```bash
.venv/bin/python scripts/fetch_starside.py --output data/starside
```

默认复用已下载文件，适合断点续传。要重新请求并更新内容，显式使用 `--refresh`；更新会覆盖本目录中同路径的旧快照，需要保留旧版时使用新的 `--output data/starside/<快照目录>`。

只有 `index.json` 的 `status` 为 `complete`、`failures` 为空且 `pending_urls` 为空，才表示本次发现的目标全部完成下载。全站下载完成不等于内容经作者审核或数值已经按当前游戏版本验证。
