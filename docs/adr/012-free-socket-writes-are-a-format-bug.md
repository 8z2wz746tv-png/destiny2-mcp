# ADR-012: 免费插槽写入是线上格式写错了，不是"应用没权限"——推翻 ADR-002

- Status: accepted
- Date: 2026-09-21
- Decision By: maintainer
- Scope: `destiny_mcp/bungie_client.py`（`insert_socket_plug_free` / `insert_socket_plug`）、
  `services/armor_mod_service.py`、`services/loadout_mod_sockets.py`、`inventory_assistant(intent="equip_mod")`、
  `subclass_assistant(intent="modify")`

## Context

**What changed**：ADR-002 断言"护甲模组只能游戏内装：个人应用拿不到 `AdvancedWriteActions`，工具不许
为它回滚"。2026-09-21 用户要求复核"DIM 能做的我们为什么不能"，结论是**那条断言错了**，真因是两个
线上格式错误：

1. **字段名**：Bungie 的两个插槽接口**字段名不同** —— 免费 `DestinyInsertPlugsFreeActionRequest.itemId`、
   付费 `DestinyInsertPlugsActionRequest.itemInstanceId`（Bungie OpenAPI 生成的类型可逐字对上）。
   aiobungie 的 `insert_socket_plug_free` 把**付费**那套字段名抄了过来，于是免费请求里 `itemId`
   缺失、上游取不到物品，固定回 1663 `DestinyItemActionForbidden` +
   `message_data: {'request.itemInstanceId': "The item being socketed doesn't have sockets."}`
   —— 那句"物品没有插槽"其实是**字段没绑上**，与权限、角色状态都无关。
2. **有符号 hash**：护甲模组方案里的 plug hash 是**有符号**的（实测 `武器模组` = `-111671246`），
   直接发负数上游回 `InvalidPostBody`「JSON Serialization Error」。子职业的 plug hash 天生为正，
   所以这一条只在护甲模组上暴露。

官方文档原文（`InsertSocketPlugFree`）：**"This does not require 'Advanced Write Action' authorization
and is available to 3rd-party apps… You must have a valid Destiny Account, and the character must either
be in a social space, in orbit, or offline."** Required Scope(s): `MoveEquipDestinyItems`。
即：免费接口**从来不需要** `AdvancedWriteActions`。

**为什么错误结论能活这么久**：免费那次失败（字段名）之后，`loadout_mod_sockets._insert_armor_mod`
判定"这是非免费 plug"→ 退回**付费**接口 → 付费接口确实缺 `AdvancedWriteActions` → **403**。
摆在台面上的是 403，于是 403 被当成根因，写进了 ADR-002、注释、话术与文档。真因在更早一步，
而它从来没有被读过——因为那句 `message_data` 被当成了"上游的模糊抱怨"。

途中被证伪的假设（记下来免得再走一遍）：**角色不在轨道**。已实测：角色确认在轨道
（`currentActivityHash=82913930`）时仍然失败，所以状态不是根因。

## Decision

1. **免费插槽写入自己拼 body**（`static_request`），字段名按官方 schema：`itemId` / `characterId` /
   `membershipType` + `plug{socketIndex, socketArrayType, plugItemHash}`；**不再调用 aiobungie 的
   `insert_socket_plug_free`**（它的字段名是错的）。
2. **plug hash 一律 `to_unsigned()`**（`utils.hash_utils` 就是为 API 调用准备的），两个接口都转。
3. **`socketArrayType` 只有 `Default=0` / `Intrinsic=1`** 两个取值（官方枚举）；此前注释里的
   "1 = reusable"是错的。
4. **免费接口覆盖的东西就按能写来做**：护甲模组、子职业插槽（超能/手雷/近战/职业技能/星相/碎片）、
   Perk、着色器、装饰。不许再宣称"只能在游戏内"。
5. **"角色必须在社交区 / 轨道 / 离线"是真实的上游前提**：这条不是我们编的，失败时要如实转述，
   但**不许**把它和"权限不足"混为一谈。
6. **非免费 plug（付费接口）仍然做不到**：它要 AWA 的三段流程（`AwaInitializeRequest` →
   用户在游戏内/网站上批准 → `AwaGetActionToken`）拿 `actionToken`——**我们没有实现**。
   这一类（调谐、强化类）如实说"需要游戏内改"，但理由要写成"我们没实现 AWA 流程"，
   不能写成"应用没有权限"。

被否掉的选项：

- **继续认定"个人应用没权限"**：与官方文档直接冲突（免费接口明说对第三方开放），而且真机已反例；
- **只改注释不改代码**：字段名不改，功能就是坏的——这次是真能写通；
- **顺手把付费接口也接上 AWA 三段流程**：那是另一个功能（要用户交互），本次不做，但要把
  "没实现"这件事说清楚，而不是伪装成"上游不允许"。

## Consequences

- **ADR-002 被本条件推翻**：其 Status 改为 `superseded by ADR-012`，文件保留（按编号规矩不删）。
  其中"调谐只能游戏内改"的**理由**也换了：不是"接口不给第三方"，而是"付费接口要 AWA，我们没实现"。
  （2026-09-22 再修正：这条理由也不对 —— 免费接口**能**换调谐，判据是"那颗在不在**这件护甲**
  允许的清单里"（组件 310），不在里面的回 1675。见 ADR-014 与 `scripts/verify_tuning_write.py`。）
- 护甲模组与子职业插槽现在**真的能通过 API 写**，真机已验证：5 颗属性模组全部写入成功，
  装完六维与求解器预测**零差值**（武器 107 / 生命 7 / 职业 71 / 手雷 120 / 近战 71 / 超能 110，逐项吻合）。
- 一份"只能在游戏内"的清单跟着改了：`_mod_write_needs_in_game` 的判定已被
  `ModSocketMixin.mod_write_blocker` 取代（三种原因分开，见 ADR-013）、`armor_mod_service` 的
  403 话术、`build_results` / `_build_flow` 的措辞、README 的写入权限段已经改完；
  `TESTING_CORPUS.md` 的对应条目随 ADR-013 一起更新，`小黑盒_功能总览.md` **还没扫**。
- 守门：`tests/test_bungie_client_actions.py` 两条新测试钉住"免费接口用 `itemId`"与
  "plug hash 转无符号"（已注入违规确认会红）。
- 改这条决定要同时改：`destiny_mcp/bungie_client.py`、`services/armor_mod_service.py`、
  `services/loadout_mod_sockets.py`、`services/loadout_equipment_service.py`、`docs/adr/README.md`、
  `docs/adr/002-armor-mods-require-in-game.md`（改 Status）、`README.md`、`CHANGELOG.md`。
