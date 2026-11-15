# legacy/ —— 已剥离的历史工具面（存档，不维护）

**这里的东西不是包的一部分，也不参与测试与 lint。** 它们只是"删掉可惜、留着碍事"的代码快照。

## 里面有什么

| 路径 | 原来在哪 | 是什么 |
| --- | --- | --- |
| `tools/*_tools.py`（16 个模块，67 个工具） | `destiny_mcp/tools/` | 低层工具：`get_inventory`、`search_weapons_by_type`、`get_vendor_inventory` … 每个域一个文件 |
| `build_import_tools.py` + `build_import_service.py` + `build_import/` | `destiny_mcp/tools/`、`destiny_mcp/services/`、`destiny_mcp/` | **配装导入**：文章 URL → 正文 + 解析提示词；解析结果 JSON → `CanonicalBuild`；截图入口 |
| `tests/test_build_import_skill_loading.py` | `tests/` | 配装导入的 skill/prompt 加载测试（跟着它一起存档） |
| `skills/build_article_skill.md`、`skills/build_screenshot_skill.md` | `skills/` | 配装导入那两个提示词（其余 5 个 `skills/*.md` 是 MCP prompt 在用的，留在原地） |
| `tools/player_tools.py` 里的 `find_players` | `destiny_mcp/tools/` | 带着一条修正：聚合入口改成"模糊搜人默认不读别人档案"之后，它必须显式传 `enrich=True`（否则裸抛 `KeyError`）——复活它时别把这一行改回去 |

## 为什么剥离

1. 它们的既定口径就是"默认屏蔽、不进主路径文档、不保证契约、**不单独修 bug**"
   （见 `docs/COMPATIBILITY.md`）。这个口径下的代码必然腐烂：2026-09-20 复核时，
   `analyze_weapon` 还在读 P4/P6 之前的 `result["perk_pool"]`（一直裸抛 `KeyError`），
   `get_historical_stats` 还在读 0.2.0 之前的 `{pve, pvp}` 手写八键（把有数据说成"未找到统计数据"）。
2. 逐个核对过：67 个工具里 **62 个在 8 个聚合工具里都有对应**，只有 5 个没有
   （`raw_api_call`、`get_item_definition`、配装导入三件套）。前者与"数据分三档、不裸打 API"
   的设计相冲；配装导入则**已决定不要这个功能**（README 与技能文档里相关的宣传同时删掉）。
3. 保留成本是实打实的：`server.py` 的 profile 机制、两个环境变量、55 处引用
   （测试、语料、CI、安装脚本、技能文档）。

决定与取舍见 ADR（`docs/adr/`）与 `docs/plans/LEGACY_SURFACE_REMOVAL_PLAN.md`。

## 怎么用（和怎么别用）

- **别 import 它们**：文件里的相对 import（`from ..exceptions import …`、`from ._registry import mcp`）
  是按原来的目录写的，直接搬回来才成立；这里的路径不是可运行状态。
- **要看旧行为**：直接读这里的文件。
- **要复活某一个**：从 git 历史取回原位最稳（`git log --diff-filter=D -- destiny_mcp/tools/<名字>.py`），
  或者把它拷回 `destiny_mcp/tools/` 并修相对 import、在 `server.py` 的 `_TOOL_MODULES` 里登记 ——
  但复活意味着重新接受维护责任：参数守卫（`tools/_param_contracts.py`）、响应信封、
  进入语料与测试，一样都不能少。
- **不要**在 `server.py` 里把它们挂回去，也不要给 `legacy/` 加测试或让它进 lint：
  存档的价值是"能查"，不是"还能跑"。
