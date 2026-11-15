# Community data notice

The Markdown documents in `share/` are a **Starside** community knowledge export — Starside: <https://starside.work/index.html> — provided to this project's maintainer and redistributed with the website author's permission. The site author and the upstream sources credited inside those documents retain all rights; the project's MIT license does not relicense them.

`data/starside/` additionally contains the portion of the same author's site archive that the MCP reads at runtime — the index, the per-page records, and the two public exports — also redistributed with the author's permission. The raw crawled HTML (`pages/`) and the site's icons and front-end assets (`assets/`) are deliberately not redistributed, and are re-fetched by `scripts/fetch_starside.py` when needed.

They are reference material, not Bungie data. Their statements, recommendations, measurements, update dates, and links remain attributable to their respective authors and upstream sources. The repository's MIT license applies to the software and does not by itself relicense this community content or third-party material cited by it. Community ratings are not official recommendations.

The MCP preserves document hashes, declared update dates, source links when present, and PvP, enhanced, note, and uncertainty markers. A missing date or source is reported as missing rather than inferred. Bungie inventory and Manifest data remain separate authorities.

Questions about reuse of the community documents outside this project should be directed to the Starside author. Bungie and Destiny are trademarks of Bungie, Inc.; this project is not affiliated with or endorsed by Bungie.


## 作者给的新归档（实体导出，2026-09-23）

作者（Starside，`Aegis` / `LGpig`）给的**逐物品/逐 perk 导出**（`inventory-items.json` 13.1 MB、
`sandbox-perks.json` 2.9 MB、`traits.json` 42 KB，快照 2026-09-21），我们接进了工具面
（见 `docs/plans/STARSIDE_ENTITY_PLAN.md`）。

- **再分发已获作者同意**（2026-09-23 聊天中明确「没事」）。所以整包以 **Release 附件**形式公开，
  **不进 git 历史**（18 MB 的派生快照不该拖累每次 clone）：
  <https://github.com/8z2wz746tv-png/destiny2-mcp/releases/tag/v0.6.0> 的
  `starside-entity-archive-2026-09-21.zip`（干净重打包，去掉了 macOS `__MACOSX` 垃圾）
- 指纹（可用于核对"我们没改数据"）：
  - Release 附件 `starside-entity-archive-2026-09-21.zip`：sha256 `32e98d064d7d81e3efbbbe1a8da49f0caaf84f4e521c11a5b4c92acf6bf37691`
  - 作者原始 `归档.zip`：sha256 `d282af762e22796188c8996a8acbe986a7e90c0096a15c774a2daa670282512d`
  - 源文件逐个指纹记在 `data/starside/index.json` 的 `entities.source_files`（`18453969…` / `5074c617…`）
- **可复现**：`python scripts/import_starside_entities.py --source <解压目录>` 逐字节复现入库的
  `data/starside/entities/{items,perks}.json`（确定性导入 + hash 逐条校验，任何一条对不上就退出）。
- 仍然是**非 Bungie 官方数据**：引用时保留作者与快照时间，声明它是社区整理；我们的转换只挑社区层字段，
  定义/名字/数值口径以我们本地 Manifest 为准。
