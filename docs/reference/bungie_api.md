# Bungie API 实测事实清单

**口径声明（先读这一段）**：本文档记的是**我们自己的实测事实** —— 我们依赖了什么、在哪一步踩过什么坑、
之后代码里怎么做的。Bungie 官方文档（<https://bungie-net.github.io/>，scope 表、端点、AWA、组件枚举都在那一页）
是**定义来源**：字段叫什么、端点怎么拼，以官方为准。

**冲突时以本文档的「实测」为准**（这是项目规矩），并做两件事：① 在对应条目上标 `⚠️ 与官方文档冲突`；
② 去更新那一条 —— 实测变了就改实测，别让文档里躺着一句过期结论。要重下官方文档深查用
`python scripts/fetch_bungie_api_docs.py`（拉到 `~/.destiny_mcp/reference/`，不进 git；`--check` 看版本变没变）。

每条格式：事实 / 出处 / 实测 / 结论。**没实测过的条目不许写成实测**。

---

## 大师加成是"档位"，且只落在非词条三项上（2026-09-22 实测）

- 护甲的大师插槽（`v460.plugs.armor.masterworks`）那颗「升级护甲」在 Manifest 里声明的是
  **"六维各 +N"**，N 就是档位（实测见过 1/3/4/5 档；只带能量标记的那种是 **0 档**）：
  `1283668724` → `{武器3 生命3 职业3 手雷3 超能3 近战3}`、`1283668722` → 各 5、
  `3266153492` → 只有 `16120457: 11`（能量标记，没有属性）。
- 但**真正生效的是"非词条那三项"各 +N**（词条 = 那三个 30/25/20 的属性）：
  降临回音臂铠 `6917530197749539530` 是 3 档，词条 武器20/超能30/近战25，
  304 就是 `武器20 生命3 职业3 手雷3 超能30 近战25`。
- 所以"满大师 = +5×3"这句话只在 5 档成立；`is_masterworked` 必须看**插件声明的档位**，
  不是 T 级（T5 也可能是 0 档）。

## 每件护甲允许装哪些调谐：只在组件 310（2026-09-22 实测）

- Manifest 的调谐 plug set（`1155052024`）是**全局 32 颗**，三件同名「光芒领主手套」指向的就是
  同一个集合、内容一模一样 —— **照它规划会给出装不上的调谐**。
- 真正逐件的是 **310 `ItemReusablePlugs`**：`itemComponents.reusablePlugs.data[实例].plugs["槽号"]`
  给出这一槽能插的插件。调谐槽那儿是 **6 颗**（5 颗"某一个属性 +5"的定向调谐 + 平衡调整），
  而这"某一个属性"**逐件不同**（实测：两件同名手套分别是 职业 / 近战；护腿 职业；面具 超能；
  至高碎片 武器）。**金装（星火协议）是 31 颗 = 任意属性**。
- 角色的可插入清单（`profilePlugSets`）里，调谐集合只有 **1 颗"空调整模组插槽"**，
  `characterPlugSets` 里没有调谐集合 —— 那两份**不能**用来判"这件能装什么调谐"。
  **也别拿它们判"这一位能不能插"**（2026-09-22 追加实测）：调谐槽里**正装着的那颗都不在**那份
  清单里，而一颗被判"不可插入"的调谐写入上游**照样接受**（真机：至高碎片 `6917530188462629544`
  槽 11，`+武器 / -超能` = 305/plug set 判 false → `ErrorCode=1`，回读六维 超能 25→20、近战 0→5、
  再换回也成功）。**调谐写入的判据只有组件 310 一条**；`unlock_state` 对调谐一律报 `null`，
  免得那个 false 被读成"写不了"（见 ADR-014 修订、`scripts/verify_tuning_write.py`）。
- 代价：请求 310 会让护甲快照响应从 **4.11 MB 涨到 8.90 MB**（术士全账号护甲），
  所以只有"要规划调谐"的那条路带它（`profile_components.BUILD_ARMOR`）。
- **310 给的是无符号 hash，`manifest.search` 给的是有符号** —— 同一个插件两种写法
  （真机：`+超能 / -生命值` = 310 的 `4026414261` / 搜索的 `-268553035`）。比较必须过
  `to_unsigned`（`build.models.tuning_is_allowed` 是这条的唯一出处）；裸 `in` 会把清单里
  明明有的调谐判成"装不到这件上"（真机踩过：光芒领主面具 `6917530188460608169`，2026-09-22）。
- 组件 304 读回来的**插槽当前值**同样是这份口径：`from.hash` 可能是无符号、而 `to.hash`
  （来自搜索）是有符号 —— 两者是同一个 hash 空间的两种写法，不是两颗粒子。

## 护甲六维不夹 0：组件 304 会报负数（2026-09-22 实测）

一件护甲装着「+职业 / -生命值」，而它的生命**基础值是 0** 时，组件 304 给的是
**生命 −5**（真机：光芒领主手套 `6917530188462631525`；护甲面 = 武器25 生命−5 职业25 手雷0 超能0 近战30，
职业那 10 点是模组）。也就是说：

- **API 不夹 0** —— 谁按"每个属性最低 0"去算，反推基础值时就会凭空长出 5 点；
- 我们的求解器口径因此定为"**等于游戏报的数**"（不夹），见
  `docs/plans/SOLVER_OPTIMALITY_PLAN.md` 的四·十三；
- 待确认（要游戏里看一眼）：游戏**界面**是否按件夹 0 显示、人物总属性是否受这 −5 影响。

## 一、OAuth scope

### scope 表：三个名字的官方原文一句话
- 事实：官方 scope 表里与我们相关的三条，原文分别是
  `MoveEquipDestinyItems` —— "Move or equip Destiny items"；
  `ReadDestinyInventoryAndVault` —— "Read Destiny 1 Inventory and Vault contents. For Destiny 2,
  this scope is needed to read anything regarded as private. This is the only scope a Destiny 2 app
  needs for read operations against Destiny 2 data such as inventory, vault, currency, vendors,
  milestones, progression, etc."；
  `AdvancedWriteActions` —— "Can perform actions that will result in a prompt to the user via the Destiny app."
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 把令牌解出来看，`scope` 字段**没有**任何 scope（授权 URL 一个 `scope` 都没带）；
  写入因此被上游拒成 403 `AccessNotPermittedByApplicationScope`。
- **授权 URL 上不许带 `scope`**（2026-09-23 真机反转）：带上就 100% 登录失败，Bungie 回
  `invalid_scope` + `Scope is always configured value. Do not specify scope parameter.`。
  令牌里的 scope **由 Developer Portal 的应用配置决定**（`https://www.bungie.net/en/Application` 勾选），
  旧结论"取决于授权 URL 上显式申请了什么"（09-16 场景是"带了但应用没批"）已作废。
  代码见 `destiny_mcp/oauth_setup.py`：`_auth_url` 不再输出 `scope`，也不再有"退回不带 scope"的降级；
  回调页面会先透出 Bungie 的 `error`/`error_description`，不再把它吞成 `invalid_state`。
- 实测：2026-09-16 真机 —— 0.4.6 刚在授权 URL 上加了 `scope=AdvancedWriteActions`，**连登录都做不成**
  （授权页回 `invalid_scope`），因为该应用没被授予。0.4.7 改成：先按"带 scope"试一次，撞到
  `invalid_scope` 就打印说明并自动退回"不带 scope"再登一次。
- 结论：`destiny_mcp/oauth_setup.py` 的 `_WANTED_SCOPES` 只声明 **`AdvancedWriteActions`**，且它只是
  "想要"不是"必须有" —— 降级后功能上只少了「带消耗/不可逆的插槽写入」（付费 `InsertSocketPlug`、
  神器重置）。`MoveEquipDestinyItems` / `ReadDestinyInventoryAndVault` **我们从来没申请过**，
  而读与移动/装备一直是通的；别照文档表"补全"一堆 scope，那会重新引入登录失败。

---

## 二、AdvancedWriteActions（AWA）与"没有它时写入怎么失败"

### AWA 三段端点
- 事实：`POST /Destiny2/Awa/Initialize/` → 上游返回 `correlationId`；`POST
  /Destiny2/Awa/AwaProvideAuthorizationResult/` 把用户在 Destiny app 里的授权结果回填；`GET
  /Destiny2/Awa/GetActionToken/{correlationId}/` 取那一次动作的 token。它对应官方 scope 描述里的
  "a prompt to the user via the Destiny app"。
- 出处：<https://bungie-net.github.io/> → `Destiny2` 端点清单（文档版本 2.21.8，2026-09-17 查阅）
- 实测：**本项目没有实现 AWA 流程**（仓库里只有 scope 名，没有任何 `Awa/` 调用）。我们的写入要么走
  免费插槽接口、要么要求应用具备 `AdvancedWriteActions` 直接拿令牌，不需要用户弹窗。
- 结论：要接"需要用户在 Destiny app 里点确认"的动作时才去实现这三段；在那之前不要照抄流程，
  把 `AdvancedWriteActions` 当成"令牌里有没有"来用就够了。

### 没有 scope / 不允许的动作，上游怎么回（三次实测对照）
- 事实：权限不足回 **403** `AccessNotPermittedByApplicationScope`（消息里点名 RequiredScope）；
  策略不允许的动作回 **ErrorCode 1663** `DestinyItemActionForbidden` "This action can only be done in-game."
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测（2026-09-16 真机，`error.code` 与上游原文一起带出来之后才看清真因）：
  ① 装属性模组走 `InsertSocketPlugFree` → **403** `Access not permitted by application scope`；
  ② 为腾能量卸掉一颗模组 → **500** `This action can only be done in-game.`；
  ③ 换调谐类 plug 走 free 接口 → **1663** `DestinyItemActionForbidden` + `This action can only be done in-game.`
- 结论：**403 与 1663 是两件事，处置不同**（见下一条）；两条都必须在话术里带上游原文，
  不能统一写成"写入失败"。旧版本把这两句丢掉，只写"模组 X → '铁能面罩'"，看上去像我们的插槽查找又错了，
  为此白查了好几轮。

### 403 不重试、1663 才换接口
- 事实：403 是应用级权限问题，重试同一个接口永远不会成功；1663 是"这个 plug 不免费/不可逆"，
  换付费接口才可能成。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— free 接口对"非免费可逆"的 plug 回 1663；0.4.5 之前盲目按
  "能量消耗 > 0"选付费接口，真机上装护甲模组一直 403（而护甲模组本来就该走 free）。
- 结论：`services/loadout_mod_sockets.py` 现在**先走 free**，只有拿到 1663
  （`in-game` / `DestinyItemActionForbidden`）才**退回**付费接口；403 直接如实报权限问题、不重试。
  守门测试钉住这三条分支（`tests/test_equip_mod.py`）。

---

## 三、`InsertSocketPlug` vs `InsertSocketPlugFree`

### free 指"没有材料消耗"，不是"不花能量"
- 事实：官方对 `InsertSocketPlugFree` 的说明是**没有材料消耗**，并明确覆盖
  "Perks, **Armor Mods**, Shaders, Ornaments"；`InsertSocketPlug` 是带消耗的那条。
- 出处：<https://bungie-net.github.io/> → `/Destiny2/Actions/Items/InsertSocketPlugFree/`
  与 `/Destiny2/Actions/Items/InsertSocketPlug/`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 我们原先按"能量消耗 > 0"判断该走付费接口，于是护甲模组全走错；
  用户指出"DIM 能操作模组"后复查官方原文，确认护甲模组属于 free 覆盖范围（它消耗的是能量、不是材料）。
- 结论：**接口语义 ≠ 权限**。护甲模组走 free；要不要 scope 是另一条线（付费接口才要
  `AdvancedWriteActions`）。别再拿"消耗大不大"去决定用哪个接口。

### 插槽写入的第三种结局：1676 `DestinyFailedPlugInsertionRules`（"条件没满足"）
- 事实：除了 403（没 scope）与 1663（`in-game` 含糊话术），还有 **1676**
  `DestinyFailedPlugInsertionRules` —— "The request to apply a change to an item failed.
  The requirements have not been met."，`message_data` 是**空的**，不说是哪条没过。
- 出处：<https://bungie-net.github.io/> → `Destiny2/Actions/Items/InsertSocketPlugFree/`
  与 `Exceptions` 的 `PlatformErrorCodes`（文档版本 2.21.8，2026-09-21 查阅）
- 实测：2026-09-21 真机 —— 给护甲换部位模组时，四颗被回 1676；它们在 Manifest 的
  `plug.insertionRules[].failureMessage` 里都写着「**必须在赛季神器中选择**」。
  同批的另一颗（职业物品的终结技模组）条件里没有这条，**写通了**。
- 结论：1676 = **这颗模组这一位还没解锁**，游戏里同样装不上；不许报成"请去游戏里手动装"、
  也不许当成重试能成的错误。能拿到的中文说法只有 `plug.insertionRules[].failureMessage`
  （`services/loadout_plug_lookup.py: plug_insertion_conditions`）。这条由
  `tests/test_armor_mod_unlock.py` 的 1676 两条钉住。

### 能不能插，看组件 207 `characterPlugSets`（随 305 一起回来）
- 事实：`DestinyProfilePlugSetsComponent` / `DestinyCharacterPlugSetsComponent` 给出
  **每一位玩家/角色实际能插入**的 plug 清单（`plugItems[].canInsert` / `enabled`），
  比 Manifest 的 `reusablePlugItems` 小得多。
- 出处：<https://bungie-net.github.io/> → `DestinyComponentType`（106 `ProfilePlugSets`、
  207 `CharacterPlugSets`）与 `DestinyPlugSet`（文档版本 2.21.8，2026-09-21 查阅）
- 实测：2026-09-21 真机 —— 请求 305 时响应里**已经带着** `profilePlugSets` 与
  `characterPlugSets`（不用单独请求 106/207；实测专门请求 `components=106,207` 反而什么都不回）。
  头盔 plug set：Manifest 61 颗、这一位只能插 21 颗；手臂 25/56；一般模组 9/24。
  四颗被 1676 拒掉的全不在这份清单里，写通的全在。
- 结论：`insertable_plugs(profile, character_id)` 是"这一位能插哪些"的唯一读法，
  profile 级与角色级**都要并**；**上游没给这个 plug set 时返回缺键**（`None` = 不判断），
  绝不能把"没数据"读成"不允许"。调谐槽里正装着的那颗不在清单里 —— 所以"已装在槽里的那颗"
  单独放行（见 ADR-013）。

### 1679 `DestinySocketAlreadyHasPlug`：原样重插的回执
- 事实：往某个槽插入它**已经有**的 plug，上游回 1679 `DestinySocketAlreadyHasPlug`
  （"Refresh the item and try again."），不是 1663。
- 实测：2026-09-21 真机 —— 用免费接口把两颗调谐插件原样重插，都是 1679。
- 处置：`ArmorModService.apply` 把 1679 当**无操作成功**（`already_installed=true`，摘要写
  「已经装着它，这次没有改动」）—— 用户要的状态已经成立，报失败会诱使重试。
- 结论：这条说明免费接口**能寻址调谐槽**（旧说法"免费接口对调谐回 1663、所以第三方写不进去"
  是那个字段名 bug 的残留，见 ADR-012）。这只证明"寻址得到"，**不证明"换一颗能成"** ——
  真要放开调谐写入得另做一次可逆的真机验证。

### 两个端点官方都标 Preview
- 事实：官方端点清单里 `InsertSocketPlug` 与 `InsertSocketPlugFree` 都挂着
  `Preview - Not Ready for Release` 标记。
- 出处：<https://bungie-net.github.io/> → `Destiny2` 端点清单（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 免费插入子职业碎片/星象、以及神器模组都实际生效（回读 305 确认过）。
- 结论：Preview 只说明**契约可能变**，不代表不能用；我们可以用，但别把它当稳定契约宣传，
  上游一改就以实测更新本文档。

---

## 四、Profile 组件：只请求 305 时上游不返回插槽

### 要插槽就必须带"清单类"组件
- 事实：`GetProfile` 按 `components` 取数；只给插槽组件时，上游不会附带物品清单，插槽自然无处可挂。
- 出处：<https://bungie-net.github.io/> → `DestinyComponentType` 枚举与 `GetProfile` 端点
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— `get_profile(..., [305])` 返回 **0 件**带插槽；
  同一次会话带上清单类组件（102/200/201/205/300）返回 **1627 件**。模组插槽读取当时正是只写了 `[305]`，
  于是拿到一串空数据，接着每件护甲都报"找不到模组 X 的唯一兼容插槽"，查了一整晚。
- 结论：插槽读取统一走 `destiny_mcp/services/profile_components.py` 的 **`INVENTORY_SOCKETS`**
  （`[102, 200, 201, 205, 300, 305]`），不在服务里写裸组件号。两条守门测试钉住：
  `tests/test_profile_components.py::test_socket_reads_always_carry_an_inventory_component`
  （要 305 就必须带 102/200/201/205）与 `…::test_no_service_writes_a_raw_component_list_any_more`
  （服务里禁止裸组件号字面量）。

### 缓存里的"空列表"不等于"这件没有插槽"
- 事实：profile 是异步快照，物品刚被搬动时这次响应里可能还没有它的插槽。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 插槽缓存只判"键在不在"，装备刚被搬过来时键在、值是空列表，
  于是每个槽都被跳过（看上去像"这件护甲没有模组槽"）。
- 结论：**空列表要触发重读**，不能当"这件是空的"用（没查到 ≠ 没有）。

---

## 五、写入后的同步窗口（3～10 秒）

### 写完立刻回读会读到旧值
- 事实：写入接口返回成功 ≠ profile 立刻可见。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— `EquipItem` 回 `ErrorCode=1`，**立刻**回读仍是旧子职业，约 3 秒后再读才是新的；
  同一晚两次写入的窗口不一样：一次 3 秒可见、一次 5 秒还没现身。
- 结论：统一走 `destiny_mcp/services/write_readback.py` 的 `read_until`（`ATTEMPTS=8` × `DELAY_SECONDS=1.5`，
  约 10.5 秒），用**最后一次**读到的值判断。同一坑还有两处：读实例插槽、`equip_items` 的
  "物品必须在目标角色背包里"预检 —— 刚搬完立刻批量装备必失败（报"请先 move_item"）。

### 超窗只能说"没确认"
- 事实：重试到次数用尽还没看到新值，与"写入失败"是两件事。
- 出处：<https://bungie-net.github.io/>（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 回读窗口"几秒到十几秒不固定"，超过 10.5 秒仍读不到的情况出现过。
- 结论：超窗报 **`unverified`** + `unverified_reason`，**不许**说"没换成"（上游已回成功），
  也**不许**当成功；"写完立刻读一次就下结论"会把成功的写入报成假失败。

---

## 六、`isEquipped` 只在组件 300

### `characterEquipment` 的条目没有这个字段
- 事实：`isEquipped` 在 `itemInstances`（组件 300）的 `instances.data[实例]` 上；
  `characterEquipment`（205）/`characterInventories`（201）的条目里没有它。
- 出处：<https://bungie-net.github.io/> → `DestinyItemComponent` 与 `DestinyItemInstanceComponent`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 直喂真账号数据时，205/201 条目缺 `isEquipped`；直接按桶判断"谁装备着"
  会得出对所有装备都为真的结论（那只是"它在装备桶里"）。
- 结论：谁是装备着的，只看组件 300 填出来的 `is_equipped`；`characterEquipment` 里的条目
  **必须先按桶过滤**再用。"装备位 + 背包"合并的语义写死在 `EquipPlanRequest` 的 docstring 里
  （见 `docs/plans/EQUIP_FLOW_PLAN.md` 第六节）。

---

## 七、赛季神器：三个 hash 家族

### 玩家实例 / 赛季定义 / 目录定义不是一回事
- 事实：神器同时存在三类标识 —— ①**玩家实例**（profile 条目的 `itemInstanceId` + 那件东西的
  `itemHash`）；②**赛季定义族**（`DestinyArtifactDefinition` 的那一行，报的是本赛季神器）；
  ③ 神器模组的目录条目（`DestinyInventoryItemDefinition`，候选池从这里查）。
- 出处：<https://bungie-net.github.io/> → `DestinyArtifactDefinition`、`DestinyInventoryItemDefinition`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 目录里的「当前神器」（`DestinyArtifactDefinition` 全表只有 1 行，报 s27 好奇之器）
  与角色身上那件**可以完全不同**（同一时刻三角色分别装着 s26/s21/s25）；而且**同名不同 hash**
  （好奇之器：目录 `-1600062152`、玩家实例 `23349941`）。hash 还有 signed/unsigned 两种写法，
  查定义前要 `to_signed()`。
- 结论：**"我现在用哪个神器、能不能换"只读账号实例**，目录只配用来查模组池；
  槽位从"这件神器的定义"算（不写死槽数/槽号，不同赛季不同）。

### 神器不可转移
- 事实：神器与子职业物品同类，`transferStatus` 表明不能搬。
- 出处：<https://bungie-net.github.io/> → `DestinyItemComponent.transferStatus`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 背包里 `transferStatus=2`、装备位 `=3`；仓库与邮政长里神器 **0 件**。
- 结论：能换的只有**同一角色背包/装备位**里那几件；别规划"从仓库搬神器"，也别报"仓库里有一件更好的"。

---

## 八、子职业元素在 `plugCategoryIdentifier` 的第二段

### 第二段是元素，第三段才是槽类型
- 事实：子职业 plug 的 `plugCategoryIdentifier` 形如 `warlock.solar.supers` / `shared.void.grenades` /
  `warlock.arc.aspects` —— 第一段是职业（或 `shared`），**第二段是元素**，第三段是槽类型。
- 出处：<https://bungie-net.github.io/> → `DestinyPlugItemDefinition.plugCategoryIdentifier`
  （文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-17 查本地 Manifest（38894 条物品定义）：第二段取值是
  `arc/solar/void/stasis/strand/prism`（另有 `shared` 前缀族）；第三段取值是
  `supers/melee/grenades/class_abilities/movement/aspects/fragments`，**没有** `movement_abilities`
  这种写法（另有 `totems/trinkets/transcendence/prism_grenade` 等少量族）。
- 结论：`subclass_service._CATEGORY_PATTERN` 的捕获组只包了**第三段**（槽类型：`supers`/`melee`/
  `grenades`/`movement`/…），拿它当元素用必然错位；断元素只能用 `_ELEMENT_PATTERN`（捕获第二段）。
  另一条实采结论（`_element_of` 的 docstring）：元素**不写在子职业物品的定义里**
  （18 件子职业物品的 `defaultDamageType` 全是 0、没有 element 字段、
  `socketEntries[].plugCategoryIdentifier` 也是空的），只能从**已装 plug 的类别串**读 ——
  "按定义猜元素"的写法一律不成立。

---

## 九、上游铁律清单（只编排，不绕过）

### 四条硬规则
- 事实：上游有四条**我们绕不过**的规则：
  ① `DestinyItemUniqueEquipRestricted`（`UniqueEquipRestricted`）—— 同一个角色全身只能一件异域护甲；
  ② `DestinyCannotPerformActionOnEquippedItem` —— 正装备着的物品不能被移动（要换下来先）；
  ③ `DestinyNoRoomInDestination` —— 目标位置空间不足（含背包满）；
  ④ `EquipItem` 只接受**在该角色身上**的实例（仓库/别的角色上的会报 "not found in the character's inventory"）。
- 出处：<https://bungie-net.github.io/> → 上述异常/错误码与 `EquipItem` 端点说明（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— 这条链子当时花了 **10 轮**才走通；0.4.4 把批量装备失败的上游原文带出来之后，
  才看清真正原因是"物品还没在目标角色背包里"（同步窗口），不是插槽匹配。
- 结论：我们的角色是**编排**：预检（`services/equip_planner.py` 四类）→ 计划（先顶下、再装目标）→
  `confirmed=true` 才写 → 回读核对；失败照实转述上游原文并给下一步（`tools/_responses.py` 的
  `_WRITE_FAILURE_HINTS`）。**不许循环重试、不许回滚成"假装没发生"、不许把上游策略限制包装成用户账号问题**；
  回滚只回**真正变过**的部位，失败照实报 `rolled_back: false`。

### 背包容量口径：正装备那件也算占用
- 事实：护甲桶容量取 Manifest 的 `DestinyInventoryBucketDefinition.itemCount`，且要 `to_signed()` 之后查
  `id` 才命中（uint32 直查时头盔/臂铠两个桶查不到）。
- 出处：<https://bungie-net.github.io/> → `DestinyInventoryBucketDefinition.itemCount`（文档版本 2.21.8，2026-09-17 查阅）
- 实测：2026-09-16 真机 —— Helmet/Gauntlets/Chest/Legs/Class 各 **10**、Artifacts **7**；
  真机上 chest/gauntlets/helmet 当时都是 10/10，旧口径（不含正装备那件）会假报"还能放一件"。
- 结论：`used` **含正装备那件**（与 DIM 一致）—— 两个方向风险不对称：多算顶多让用户白清一格，
  少算会去撞 `NoRoomInDestination`。

---

## 十、与本文档有关的代码位置

| 事实 | 唯一出处 |
| --- | --- |
| 组件号集合 | `destiny_mcp/services/profile_components.py` |
| 锻造图样进度只在组件 900 | `destiny_mcp/services/pattern_service.py`（见本文第十四节、ADR-009） |
| 游戏内生涯计数器（组件 1100）与统计接口的分工 | `destiny_mcp/services/activity_counters_service.py`（见本文第十一节） |
| 三档口径与合并语义（sum/max/min/derived/none） | `destiny_mcp/activity_stats.py`（「三档并存」段） |
| 统计接口两条路（按角色 / 账号级）与 `modes`/`periodType` | `destiny_mcp/bungie_stats.py`（见本文第十二节） |
| 生涯口径的真机验收 | `scripts/verify_career_stats.py`（8 条断言）、`scripts/verify_career_counters.py` |
| 写入回读重试 | `destiny_mcp/services/write_readback.py` |
| 模组/插槽写入与 free→付费回退 | `destiny_mcp/services/loadout_mod_sockets.py` |
| 装备编排与预检 | `destiny_mcp/services/equip_planner.py`、`services/transfer_service.py` |
| 失败话术与上游原文 | `destiny_mcp/tools/_responses.py`（`_WRITE_FAILURE_HINTS`） |
| OAuth scope | `destiny_mcp/oauth_setup.py`（`_WANTED_SCOPES`） |
| 神器三个 hash 家族 | `destiny_mcp/services/artifact_service.py`、`manifest_artifacts.py` |
| 子职业元素第二段 | `destiny_mcp/services/subclass_service.py`（`_ELEMENT_PATTERN`） |

## 十一、Metrics（组件 1100）vs Stats：三个生涯数字，各有各的出处

**口径声明**：这一节里的数字全部来自本机真账号实测（2026-09-17 采集、2026-09-18 复核，只读，
未做任何写入）。同一个"熔炉生涯击败"有三个数：

| 来源 | 数字 | 范围 |
| --- | --- | --- |
| `profile.metrics`（组件 1100，`811894228`） | **124,495** | 游戏内那个计数器：自 S1 起累计、含已删角色 |
| `GetHistoricalStatsForAccount` 的 `mergedAllCharacters` | **78,864** | 上游还列举得出的角色（现存 50,622 + 已删 28,242） |
| `GetHistoricalStats`（按角色） | 17,703 / 22,279 / 8,864 | 单个角色自己的数 |

这不是谁算错了，是三个口径。**谁问哪一路就给哪一路，并且把出处一起给出去**；三个数不许相加、
也不许互相"纠正"。口径决定见 ADR-005，验收脚本 `scripts/verify_career_stats.py`。

### 组件 1100 的响应形状

- **事实**：形状是 `Response.metrics.data.metrics = {metricHash: {invisible,
  objectiveProgress: {objectiveHash, progress, completionValue, complete, visible}}}` ——
  **名字和描述不在里面**，只有 hash 和数字。
- **出处**：<https://bungie-net.github.io/> → `DestinyComponentType.Metrics`（文档 2.21.8，
  2026-09-17 查阅）；计数器定义在 Manifest 表 `DestinyMetricDefinition` 的
  `displayProperties.name/description`。
- **实测**：本账号该组件共 **402 条**计数器（原始响应约 66 KB）。其中
  `811894228` = `Opponents Defeated`，描述原文 *"The total number of opponents defeated in
  Crucible matches. Tracks from Season 1 onward."*，`progress = 124495` ——
  **与游戏内、第三方机器人显示的数字一字不差**；数值最大的一条是 PvE 累计 `79,712,994`。
- **结论**：问"游戏里显示的那个数"必须读组件 1100；组件号进
  `services/profile_components.py` 的 `METRICS`，读实现在
  `services/activity_counters_service.py`（`activity_assistant(intent="counters")`）。
  查定义要 `to_signed()` 回退（与 `get_bucket_definition` 同套路：uint32 hash 直查 `id` 可能不中）。

### 统计接口的三块：`mergedAllCharacters` 已含已删角色

- **事实**：账号级 `GetHistoricalStatsForAccount` 返回三块 —— `mergedAllCharacters`
  （`results.<group>.allTime` 与 `merged`）、`mergedDeletedCharacters`、`characters[]`
  （每条带 `deleted` 标志与自己的 `results`）。
- **实测**（2026-09-18 逐条核对）：本账号 8 条角色（现存 3 / 已删 5），
  `allPvP.allTime.opponentsDefeated` 逐角色为 22,279 / 19,479 / 8,864（现存）
  与 14,457 / 5,004 / 105 / 8,225 / 451（已删）：
  - **8 条之和 = 78,864 = `mergedAllCharacters`**；
  - 已删 5 条之和 = **28,242 = `mergedDeletedCharacters`**（是明细，不是加数）；
  - 现存 3 条之和 = **50,622**（上游不直接给，要自己合）。
- **结论**：`account_total = 78,864`，**不是** `mergedAllCharacters + mergedDeletedCharacters`
  （那是 107,106，把已删角色算了两遍）。三档的名字与来源随 payload 给出去
  （`activity_stats.TIER_LABELS_ZH`），谁也别再自己加。

### 合并语义逐项声明（跨角色怎么合）

- **事实**：上游合并视图对每一类统计的合并方式不同。实测核对（现存/已删两块与账号级的关系）：
  - **可加**（kills/deaths/opponentsDefeated/activitiesEntered/score/weaponKills…）：账号级 = 现存 + 已删；
  - **取最大**（`longestKillSpree`/`bestSingleGameKills`/`mostPrecisionKills`/`longestSingleLife`/
    `highestLightLevel`/`longestKillDistance`…）：账号级 = 8 条里的最大值（21 / 80 / 25 / 395 / 1450 / 106）；
  - **取最小**（`fastestCompletionMs`）：账号级 38,300 = min（max 是 720,200）；
  - **比值**（`killsDeathsRatio`/`efficiency`/`winLossRatio`/`killsDeathsAssists`/`averageScorePerKill`）：
    账号级 = 按分量重算 —— 实测 1.3372127723067742 == 62,734/46,914；1.6810333802276507 ==
    (62,734+16,130)/46,914；0.9868823786620026 == 2,257/(4,544−2,257)；1.5091230762672123 ==
    (62,734+16,130/2)/46,914；1.9001817196416617 == 119,206/62,734；
  - **比不出**（`averageLifespan`/`averageKillDistance`/`averageScorePerLife`/`combatRating`/
    `weaponBestType`）：没有可用的合并语义 → 现存那一档给 `null`（`aggregate="none"`）。
- **陷阱**：`killsDeathsAssists` **不是**"击杀+助攻"的合计（那个和是 78,864，一眼就会误读），
  它是 KDA 指数；`remainingTimeAfterQuitSeconds` 名字像"最短"、实测是**可加**的（5,372,422 =
  814,505 + 已删），一开始按名字猜成取最小被真机断言当场抓出来。
- **结论**：合并语义写进 `activity_stats.aggregate_kind()`（单一出处），且由
  `scripts/verify_career_stats.py` 拿真机数据逐项复核（②③两条断言）。


### 读取不稳定：会出现整块 metrics 缺失

- **事实**：**同一个 URL 连续请求**，会返回**整块 `metrics` 缺失**（0 条）的响应，
  重试后恢复 402 条。
- **实测**：2026-09-17 真机连续请求复现多次；缺失是**整个 `data.metrics` 为空**，
  不是个别条目丢字段。
- **结论**：读 1100 **必须重试**（判据 = 拿到非空 metrics，用
  `services/write_readback.read_until`）；重试后仍为空时只能如实报"不可用"，
  **绝不能把空当 0** —— "空"和"这个账号一条计数都没有"是两件事。
  这是本项目「缺值给 None，不编 0」在组件读取上的具体落点。

### 与统计接口（`GetHistoricalStats`）的关系

- **事实**：统计接口的生涯数字**分三档**，且**不含**上面那个计数器口径。
- **实测**：账号级 `mergedAllCharacters.results.allPvP.allTime.opponentsDefeated = 78,864`
  （= 现存 3 角色 `50,622` + 已删 5 角色 `28,242`）；`mergedDeletedCharacters = 28,242`；
  单角色最高那是第一个角色（`…5779`）的 `17,703 kills / 12,496 deaths`。
- **结论**：**计数器从 S1 起累计，含统计接口已不再列举的旧角色**，所以它比统计接口大
  （124,495 − 78,864 = **45,631**，这个差不是我们能拆出来的部分）。
  两个数都给、各自带 `source`（`profile.metrics` vs `GetHistoricalStatsForAccount`），
  不要互相覆盖，也不要用其中一个去"纠正"另一个。

---

## 十二、统计接口的 `modes` 与 `periodType`（2026-09-18 实测）

**口径声明**：这一节全部是本机真账号上的真机调用（只读），不是照官方文档抄的取值表。

### `periodType` 没有 Season，且只有四个取值

- **事实**：`DestinyStatsPeriodType` 实测只有 `None=0` / `Daily=1` / `AllTime=2` / `Activity=3`。
- **实测**：按角色端点带 `periodType=3` **直接 500**（`InternalServerError`）；
  `periodType=2` 与**不传**的响应都只有 `allTime` 一个块。
- **结论**：**统计接口回答不了"本赛季"** —— 赛季数字只能由游戏内计数器回答
  （`activity_assistant(intent="counters", period="season")`）。
  `stats(period="season")` 如实报 `unavailable`：不去试会 500 的 `periodType=3`，
  也不退化成生涯（拿生涯冒充赛季比如实说"取不到"更糟）。

### `modes` 只在按角色的端点上生效

- **事实**：`modes=` 传的是 `DestinyActivityModeType` 数值；**账号级端点会静默忽略它**。
- **实测**：账号级 `.../Account/{id}/Stats/` 传 `modes=84`、`periodType=2`、`groups=1`
  与**什么都不传**的响应一字不差（都是 `mergedAllCharacters` 的 allPvE/allPvP 合并视图）；
  按角色端点传 `modes=84` 才真的按模式返回，响应**只有**那个模式的组。
- **按角色端点返回的组名**（实测，一次一个模式）：熔炉 `5` → `allPvP`、铁旗 `19` →
  `ironBanner`、竞技 `69` → `pvpCompetitive`、智谋 `63` → `pvecomp_gambit`、
  试炼 `84` → `trials_of_osiris`、突袭 `4` → `raid`。多模式（`modes=19,84`）会返回多个组。
- **`modes=9` 会 500**：`services/activity_service.ACTIVITY_MODES` 里那个 `allpvp=9`
  是从旧的按场次过滤沿用下来的，`GetHistoricalStats` 不接受；模式数值只从
  `data/pvp_counters.MODE_ACTIVITY_TYPES`（本地 Manifest 的
  `DestinyActivityModeDefinition.modeType`）取。
- **已删角色照样能按角色取**：`characters[].deleted=true` 的 ID 拿去请求同样返回数据 ——
  所以"按模式的账号级合计"**能**把已删角色算进去（与三档口径一致）。
- **结论**：账号级 + 按模式 = **逐角色取 + 自己合**（可加相加 / 最多取最大 / 比值按公式重算，
  见第十一节），payload 标 `aggregation="computed"`。真机交叉验证：
  `mode="crucible"`（`modes=5` → 上游就是 `allPvP`）自行合并出来的 60 项，
  与账号级 `mergedAllCharacters.allPvP` 逐项一致（1e-3 内）。

---

## 玩家名：游戏内 ID 与平台名是两个字段（2026-09-17 实采）

- **事实**：玩家结构里 `bungieGlobalDisplayName` + `bungieGlobalDisplayNameCode` 是**游戏内 ID**
  （`名字#1234`），同一账号在**所有平台完全一致**；`displayName` 是**平台 persona**
  （Steam / Xbox / PSN / Epic 各自的昵称），**每个平台都不一样**。
- **出处**：<https://bungie-net.github.io/> 的 `DestinyProfileUserInfoCard` /
  `UserInfoCard`（2026-09-17 查阅，文档 2.21.8）；`User/GetMembershipsById`。
- **实测**：本账号游戏内 ID = `OneTop丶Husky#6641`；四平台 `displayName` 分别是
  `OneTop丶Husky`（Steam）/ `SecHusky`（Xbox）/ `early_moccasin0`（PSN）/
  `此人以嫖到广东`（Epic）。社区反馈的"显示成 Steam 名而不是游戏内 ID"由此而来。
- **结论**：展示玩家名一律走 `destiny_mcp/utils/player_names.bungie_display_name()`
  （游戏内 ID 优先，平台名只作兜底），`tests/test_player_display_name.py` 会扫**裸用
  `displayName` 拼名字**的代码并判红。

## 十三、玩家名 / 武器名 / 计数器查询的实测坑（2026-09-18）

| 现象 | 实测 | 结论 |
| --- | --- | --- |
| 搜索结果里名字是 `名字#`（尾随空 `#`） | `SearchDestinyPlayerByBungieName` 对部分账号把 `bungieGlobalDisplayNameCode` 返回成**空字符串**（不是缺字段） | 拼名字必须走 `utils/player_names`（它把 `None/""/0` 都当"没有编码"）；自己拼 `f"{name}#{code}"` 会漏 |
| 按名找武器报"找不到武器" | `搜索"玉兔"` 命中 **54 条**：53 条 `itemType=20`（`itemTypeDisplayName` 仍是"斥候步枪·异域"）+ 1 条 `itemType=3` 真武器，真武器在**第 7 位** | 同名条目会占满搜索窗口：按类型过滤要在**切片之前**做（`search(item_type=3)`），不能"扫前 N 条再挑" |
| 同一条武器的 hash 两种写法 | 武器榜（PGCR `referenceId`）给无符号 `3844694310`；Manifest 名字索引里是**有符号** `-450272986`（`to_signed` 的结果） | 比较 hash 前先归一（`to_signed`/`to_unsigned`） |
| `counters(query="crucible")` 返回 0 条 | 同一账号 `query="熔炉"` → 13 条；`query="已击败对手"` → 6 条 | `query` 是**名称/描述子串**匹配，而计数器名称来自中文 Manifest；英文模式词匹配不到（按模式用 `mode=`） |
| 统计接口按模式的数字远小于游戏内计数器 | `stats(mode="trials")`：击败 1,474 / 胜场 105；计数器：**10,696 / 826** | 按模式的生涯数字同样要并列计数器（ADR-005）；统计接口没有"这个模式的合并视图" |
| 收藏品节点 `counts` 全 0，但搜索说这个节点有 50 件 | 那 50 条是 `children.records`（条目），组件 800 里**没有**它们的收藏状态（`children.collectibles=0`） | `records` 与 `collectibles` 是两种数据：节点详情必须说明"0 只是这个口径下没有可查的收藏品" |

## 十四、锻造图样（武器模式）：进度只在组件 900（2026-09-20 实测）

**事实**：游戏里「收藏品 → 模式和催化」那一页的武器图样是**记录**结构（不是收藏品）：
根展示节点 `2642502414` → 分组容器 `3442838224` → 主武器模式 `127506319` /
特殊武器模式 `3289524180` / 重武器模式 `1464475380`（+ 异域催化 `2744330515`）→ 武器类型节点 →
183 条 `DestinyRecordDefinition`。每条记录的名字与武器同名，`objectives[0]` 的
`progressDescription` =「模式进度」、`completionValue` = 需要萃取几次
（实测：5 次 148 把 / 3 次 7 把 / 2 次 4 把 / 1 次 24 把，其中金枪 16 把）。

| 组件 | 里面有图样进度吗 | 实测 |
| --- | --- | --- |
| 900 `profileRecords` | **有**：`records[记录hash].objectives[0].progress / completionValue` 就是游戏里那条「图样进度 4/5」 | 1.44 MB / 约 2.5 s；本账号 151 条图样记录返回（149 完成、2 进行中） |
| 800 `profileCollectibles` | 没有 | 拿 `2642502414`/`3442838224` 查 `collectible_node` 回 `total=0`（"条目的解锁状态不在这个组件里"） |
| 1300 `craftables` | 没有 | `characterCraftables.<角色>.craftables` 219 条、`visible` **全为 true**、2.93 MB / 0.90 s；它回答"能塑形哪些 perk"（`sockets[].plugs[].failedRequirementIndexes`） |

**结论**：图样进度只走 900；不进组件 800/1300。口径与取舍见 ADR-009，
实测过程与成本见 `docs/plans/PATTERN_QUERY_PLAN.md`，代码在
`destiny_mcp/services/pattern_service.py`（目录 + 进度）与
`destiny_mcp/services/starside_crafting_sources.py`（社区来源）。

**另一个坑**：`is_craftable`（`inventory.recipeItemHash` 非空）是 **219 件**，比图鉴多 36 件
`（专家）/（失时）/（痛苦）`变体，它们不单列图样（图样记录挂在基础版上）。
"图样数"只能说 183，别拿 219 顶替。

**模式记录分两个作用域（2026-09-20 实测，被用户拿游戏截图抓出来的事故）**：

| 作用域 | 条数 | 数据在哪 |
| --- | --- | --- |
| 档案级（`scope=0`） | 151 | `Response.profileRecords.data.records` |
| 角色级（`scope=1`） | **32** | `Response.characterRecords.<角色>.data.records`（同一 URL、同一个组件 900） |

只读 `profileRecords` 会把那 32 条全判成「未开始」（实测：本账号 181/183 被报成 149/183）。
**同一个组件 900 就同时返回两份**，不需要多请求一次；角色级记录取"进度最靠前的角色"
（模式解锁是账号级的）。判别脚本可以是"已锻造副本反证"：账号里带 Crafted 标记（组件 300 的
item 条目 `state & 8`）的武器，其模式必然已解锁 —— 实测这一步当场指出 33 件冲突。

**变体（专家/失时/痛苦）的塑形配置不同**（2026-09-20 实测，36 件无一例外）：

| 项 | 基础版 | 变体 |
| --- | --- | --- |
| 图样条目 `crafting.requiredSocketTypeHashes` | 5 个（3868679925 框架 / 3694362576 枪管 / 2316004942 弹夹 / 3036227398 特征1 / 3036227399 特征2） | **3 个**（只有框架/枪管/弹夹）→ 三四号特性固定 |
| 组件 1300 里每条插槽的可选项数 | 11 / 19 / 15 / 19 / 19 | 11 / 19 / 15 |
| 插槽里的「空深视插槽」（socketType 1085237186） | 有 | **没有**（换成强化插槽 4251072212）→ 红框只掉基础版 |

组件 1300（`characterCraftables`）的键是**图样条目 hash**（0.9 MB 那个 219 条），不是武器 hash；
要按武器找就用 `inventory.recipeItemHash` 换算。

## 十五、周常轮换：官方只给两处（2026-09-21 实测）

| 数据 | 在哪 | 实测 |
| --- | --- | --- |
| 本周特色突袭/地牢 | `/Destiny2/Milestones/` | 本周 12 条（带 `startDate`/`endDate`/`order`/`activities[].activityHash`）；**`challenges` 全空、`phaseHash` 全 null** —— 所以"本周突袭挑战"做不了 |
| 本周夜幕/宗师（打击 + 词缀 + 掉落） | profile **组件 204** `characterActivities.availableActivities[]` | 294 条可用活动里日落/宗师 4 条：`切除: 宗师` 带 10 条 `modifierHashes` 与 `visibleRewards`（故我在 / 故我在催化 / 上维碎片）；**三个角色完全一致**；上游只给难度时（名字就是 `日落: 大师`）拿不到打击名 |
| 遗失区域（专家） | 常驻列表，**不在 API**（靠 Manifest） | 有「专家」变体的地点 **27 个**：游戏内「World Lost Sector」页按目的地列出（用户截图核对）；`空坦克` 只有传说/大师、`消息，第一/二/三部分` 一条难度变体都没有 → 排除 |
| 遗失区域（传说/大师） | **哪都没有** | 里程碑没有；组件 204 的 294 条里一条都没有；Manifest 的「遗失区域」清单（`3142056444`，42 条，**角色级组件 202**）记的是"打过哪些"（本账号 42/42），不是"今天轮到哪个" |
| 上维挑战 / 异域任务 / 泉源 | 只有候选 | Manifest 里活动与名字齐，但没有任何接口给"这周/今天是哪个"；社区工具都自己排表（Braytech 的 `rotationLostSectors` 甚至是用户可填参数） |
| 顺带：清单（checklists） | 组件 **104**（档案级 19 条）+ **202**（角色级 3 条） | 地区宝箱 / 猫雕像 / 腐化的卵 / 阿罕卡拉遗骨 / 遗失区域 42 / 玉兔 2/9 / 永恒远古头骨 0/7 —— 零新增端点的收集品进度源 |

口径与取舍见 ADR-010，实现见 `destiny_mcp/data/rotations.py` + `services/rotation_service.py`。

