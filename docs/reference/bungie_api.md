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


## 同名多版本武器：定义级解析必然有歧义（2026-09-26 实测，2026-09-28 修）

真机「千码凝视」`item_hash` 有**两个**，特性池毫无交集 —— 这不是个例，复刻/重发武器都会这样：

| `item_hash` | `traitIds` 里的版本 | 特性栏池 |
| --- | --- | --- |
| `4164201232` | `releases.v540.season` | 热力四射 / 精准连击 / 稳若磐石 / 光速拔枪 / 心无旁骛 / 永动不歇 |
| `1648948519`（**账号里 4 把副本就是这个**） | `releases.v970.core` | 孤狼 / 集体爆破 / 失调协议 / 光能之触 / 维度偏移 / 斩首武器 |

- **枪管/弹匣两栏吻合不代表选对了**（两版都 22 / 14 项，名字也重合），**特性栏才是判据**：两版 0 交集。
- 所以"`item_hash` 不相等"**不是**错配的判据（对照组「岁时之巅」解析 hash `2965080304`、账号副本 `3293207827`，hash 不同但池完全吻合）。
  正确判据是：**账号副本组件 310 的可切换项是否全部落在该定义的池内**。
- `plugCategoryHash` / Manifest 定义槽位：金装/武器的那两个"定义里是占位"的槽是 `plugSources: 1`（只从实例来），
  定义里给的 `singleInitialItemHash` 是占位（例：职业金装 = `183430252` / `183430246`），**池子只能从账号读**。
- 查 Manifest 定义时 `item_hash` **要试有符号写法**：`4164201232` 直接按无符号查不到，转成 `-130765065` 才有（老坑，见上文）。


## 十六、突袭/地牢报表：PGCR 的「全程」判据与副本级计数器（2026-09-30 实测）

做 raid.report 那种表之前先看这一节。计划与取舍见 `docs/plans/RAID_REPORT_PLAN.md`。

### 16.1 PGCR 顶层有「这一把是不是从头开的」——**但它在历史上坏过三段**

`GetPostGameCarnageReport` 的响应顶层（`Response`）字段里，与"全程"直接相关的是两个：

| 字段 | 实测行为 |
| --- | --- |
| `activityWasStartedFromBeginning` | 现代（见下方时间线）可用；同一次采样 18 场里 15 真 3 假 |
| `startingPhaseIndex` | 2022 年之后的场次**恒为 0**，不是判据；但**Beyond Light 之前它是有效的**（见时间线） |
| `activityDifficultyTier` | **不是难度**：18 场里 17 场是 `-1`。难度只能靠活动定义的 name/hash 分（见 16.4） |

**官方时间线**（来源：Bungie 官方仓库 issue [#1601](https://github.com/Bungie-net/api/issues/1601)，
Bungie 员工 `Achronos-BNG` / `jshaffstall-bng` 在评论里确认；工单号 TFS 1070650）：

| 时期 | 字段状态 | 我们该怎么办 |
| --- | --- | --- |
| **Beyond Light 之前** | `startingPhaseIndex` 有效（1…n） | 用 `startingPhaseIndex` 判 |
| **Beyond Light → 巫后**（2020-11 → 2022-02） | `startingPhaseIndex` **彻底坏了**，Bungie 原话「**will not be able to ever be fixed**」 | **判不出来**，只能按口径约定 |
| **2022-02-21 起** | 新增 `activityWasStartedFromBeginning`；但 2022-03-10 发现：**团灭过就会永久变成 False** | 仍不可靠 |
| **2022-05-24 修复** | 团灭后该布尔值保持不变；**修复不追溯**（"The fix won't be retroactive"） | 这一天之后才可信 |
| **最后一愿（`referenceId=2122313384`）** | **游戏本身的 bug，至今没修**：人口数据里只有 **5%** 的最后一愿 PGCR 报 fresh，其他突袭约 **90%** | 这一场的全程数**单独标不可靠** |

**这条时间线直接解释了我们自己那份对不上的账**：全量扫描深岩墓室算出「全程 38」、截图「110」——
因为深岩墓室的场次大半落在"判不出来"和"团灭即 False"的两段里，被我们全部判成了非全程。

**两家大站对此的处理相反**（[RaidHub FAQ](https://raidhub.io/faq) 原文）：
RaidHub 在坏窗口里**默认全算存档点**（fresh=false），raid.report **默认全算全程**（fresh=true）。
我们的截图来自 raid.report，所以它的数偏高。**这不是谁算错了，是口径选择不同 —— 我们必须写明自己按哪套。**

**「全程」的最终判据**：

```
全程 = 本人 completed == 1
       且 period >= 2022-05-24  ? activityWasStartedFromBeginning        # 可信段
       且 period <  2020-11      ? startingPhaseIndex <= 1               # BL 之前
       且 中间那段              ? 按口径约定（我们选：与 raid.report 一致 = 算全程）
```
末一行是**一致性问题，不是技术问题**——选哪套都要在响应里标明。

**两个坑（都真机验证过）**：

1. **`activityDurationSeconds` 是实例时长，不等于"我打了多久"。** 本人时长是 `timePlayedSeconds`，
   本人进本时间偏移是 `startSeconds`：实测一场救赎花园实例 6161s，本人 `startSeconds=5562` →
   本人只打了 599s（历史接口里那条正是 `9m 59s`）。
   **不要用 `startSeconds <= 0` 之类的玩家级条件去过滤"全程"** —— 实测那样会把合格场次砍掉，
   最小值反而变大（晚星之主：实例口径 61m36s，加玩家级过滤后变成 89m44s，与截图不符）。
2. **时长字段用 `activityDurationSeconds`**（不是本人时长）：三次独立验证的"全程最短用时"都逐秒吻合
   （深岩墓室 2192s / 救赎花园 1843s / 破碎王座 885s = 截图 36m32s / 30m43s / 14m45s）。

### 16.2 副本级计数器（组件 1100）：`完成数` / `无瑕` / `单人无瑕` / `导师`

`DestinyMetricDefinition` 里每个副本一整套，实测抓出 **171 条**（含 `本周` / `本赛季` 变体），涉及 55 个活动名。

**判据（重要）**：同一个副本的**多个计数器名字完全相同**，只能靠 `displayProperties.description` 区分：

- 总人数/全时段：描述里**不含**「本周 / 本赛季 / 本篇章 / 本次发布」；
- 「本周带领完成首次…」「本赛季带领完成首次…」是另外的 hash，抓错就拿错数。

**单位是「人数」不是「次数」**：导师计数器原文是「带领完成首次"深岩墓室"突袭的守护者**总人数**」——
一次带 3 个新人记 +3。这解释了一个看起来矛盾的数：某副本「全程 0 / 完成 6 / 导师 11」。

**真机对照**（该账号 2026-09-30，与同一张截图）：

- `完成数`：深岩墓室 229、救赎花园 140、异端深渊 117、预言 91、国王的陨落 103、梦魇根源 96、
  战争领主的废墟 39、守望者尖塔 36、晚星之主 28、忧愁王冠 26、星之塔 6、最后一愿 81、永恒沙漠 2
  —— **15 个副本与截图逐字相同**；
- `导师`：深岩墓室 56、玻璃拱顶 53、星之塔 11、克洛塔的末日 21、梦魇根源 33、忧愁王冠 11、
  往日之苦 22、永恒沙漠 2、分离教义 0 —— 9 个逐字吻合（另有 7 个差 1~11，是截图与当前读数的时差）。

**覆盖不全，不许填 0**：一批地牢（战争领主的废墟、深渊机灵、守望者尖塔、二象性、贪婪之握、
预言、异端深渊、破碎王座）**没有** `导师`。

**计数器命名不统一，别用正则抓**（2026-09-30 实测，抓错过两次）：

- 就叫 `完成数` 的：深岩墓室、救赎花园、国王的陨落……
- 叫 `完成次数` 的：**玻璃拱顶 `2506886274`**、**门徒誓约 `3585185883`**
  —— 按 `完成数$` 匹配会把这两个**漏掉**（我第一版清单就漏了，还在文档里写成了"没有"）；
- 反过来，按后缀匹配会抓进一堆**噪音**：`悬赏完成数`（3264536674）、`遗失区域完成数`（740213466）、
  `日落挑战完成数`、`英雄公共事件完成数`、`无瑕完成数`（349 次那个是"无瑕"这个活动名 + 完成数）……
- 所以：**必须人工核对成一张表**，外加守门测试（hash 存在、名字与副本对得上、类型正确）。

**基础计数器与「巅峰」计数器是两个独立的 hash**（实测）：`世界吞噬者 2659534585` 与
`巅峰世界吞噬者 3284024615`、`利维坦 2486745106` 与 `巅峰利维坦 1130423918`、
`星之塔 700051716` 与 `巅峰星之塔 3070318724`。

- 世界吞噬者：基础 **3** + 巅峰 **2**，与截图的「普通 3 / 巅峰 2」**逐行吻合** → 基础**不含**巅峰；
- 利维坦：基础 **45**，而截图是「普通 34 + 巅峰 11 = 45」→ 又像是**含**。
- **两者关系未定标**，P1 必须拿截图逐行对齐才能定；在那之前不要相加、也不要当成互斥。
- 另外 `门徒誓约` 有两个描述不同的 all-time 导师 hash（`2629533159` 带"自第16赛季开始计算"、
  `3632833403` 不带）—— 同名重复的情况真实存在，消歧只能靠 description。

### 16.3 `时间试炼` 计数器不能用

`X 时间试炼` 的描述是「完成"克洛塔的末日"突袭的最快时间」，看着正对「全程最短用时」，但**实测不是**：
与截图逐项对比，比值在**不同副本之间从 40 到 979**（救赎花园 856100 vs 真实 1843s = 464.6 倍；
救赎的边缘 188000 vs 4680s = 40 倍；永恒沙漠 2811000 vs 2871s = 979 倍），
**不存在任何一致的单位换算**。要么它的口径与"全程"无关，要么同名不同义。

**结论：最短用时走 PGCR（16.1 的判据），不用这个计数器。**

### 16.4 副本归组：靠活动定义，不靠名字前缀

- `DestinyActivityDefinition.activityTypeHash`：**`2043403989` = 突袭**，**`608898761` = 地牢**；
- 活动名带难度后缀：`: 普通` `: 标准` `: 大师` `: 传说` `: 巅峰` `: 竞赛` `: 专家` `: 最后通牒` `: 探索者` `（史诗）` `: 等级58`；
- **不能只用名字归组**：`利维坦` / `利维坦，星之塔` / `世界吞噬者，利维坦` 三条都含"利维坦"，
  但报表里是**三行**。归组要用人工核对过的映射表 + 守门（hash 存在、类型正确、名字匹配）。

### 16.5 首日（Day One）

组件 900 里**有**官方的首日/竞赛记录（实测搜到 8 条：顶尖速度 / 弑君者 / 初出墓室 /
首位王冠持有者 / 金字塔之日 / 分而未离 / 轨道大赛 / 开荒先锋），描述形如
「在"深岩墓室"突袭发布后 24 小时内将其完成」。

但：① 不是每个副本都有（按副本名 + 竞赛/24 小时搜，只搜到 4 条命中：玻璃拱顶 / 国王的陨落 / 深岩墓室 / 晚星之主；
**克洛塔的末日、梦魇根源根本没有对应记录**，可截图里它们带着 `DayOne` 徽章）；
② 该账号只持有 2 条（深岩墓室 `state=28`、忧愁王冠 `state=6`，且**两条都没完成**——state 里带 `ObjectiveNotCompleted` 位）。

**编号为什么没有：把记录拆到底看过**。「初出墓室」（`2699580344`）的全部字段里没有任何计数/名次类字段
（`recordValueStyle=0`、`completionInfo.ScoreValue=0`、`intervalInfo` 空、`titleInfo.hasTitle=false`），
它的 4 个 objective 就是**四个遭遇战**：

| objectiveHash | progressDescription |
| --- | --- |
| 4132628245 | 已规避墓室安保 |
| 4132628244 | 已击败阿特拉克斯-1 |
| 4132628247 | 已在下落中存活 |
| 4132628246 | 已击败坦尼克斯，厌恶者 |

在 `DestinyObjectiveDefinition` / `DestinyRecordDefinition` / `DestinyMetricDefinition` 三张表里
按「名次 / 排名 / 第 N 名 / 全球第」全库搜，**只有 7 条命中，全部无关**
（赛雀联赛的「奖台名次」、智谋排名、以及 2024 守护者游戏的「你的得分名列前 X%」）。

**结论：「是否首日完成」可以做；`DayOne #编号` 官方没有，吹了。**
编号只能自己建人群库（见 16.8）。

**布尔值本身也要有兜底**：官方记录只覆盖一部分副本，所以"是否首日"还得有一条
「PGCR `period` 落在发布后 24 小时内」的路 —— 那需要一张**自维护的发布时间表**
（照 `data/rotations.py` 那套：表 + 锚点 + 更新时间，见 ADR-010）。

**建议的替代品**（比全球名次对玩家更有用，而且完全可算）：
「你在这把里是**开服后 6 小时 12 分**完成的」——用 PGCR `period` 减发布时间，一天内精确到分钟。
玩家记得住的是"我们开服那天打过了"，记不住也不关心自己是不是全球第 15358 个。

**实测：站点的「全程」数字复刻不出来（2026-09-30，深岩墓室 336 场全量扫描）**

| 口径 | 深岩墓室 | 说明 |
| --- | --- | --- |
| 只认 `activityWasStartedFromBeginning == true` 且我自己完成 | **38** | 严格口径 |
| 再加上"坏窗口那段"（字段明确但不可信）算全程 | **79** | 与 raid.report 处理坏窗口的方式一致 |
| 不计全程、只算我完成的场次 | 179 | |
| **截图（raid.report）** | **110** | 落在 79 与 179 之间 —— **对不上任何可复现口径** |

三条支撑实测：

- **`startingPhaseIndex` 完全不携带信息**：336 场里**每一场都是 0**（坏窗口内、坏窗口外都是），
  所以那句"pre-Beyond Light 用 startingPhaseIndex 判"在我们的数据上无从下手；
- **字段从来不是缺失的**：2020~2021 的场次也给 `False`（Bungie 回填的），
  所以"缺失当全程"这条兜底一次都不会触发 —— 坏窗口只能靠 **日期** 认（修复日 `2022-05-24`）；
- 深岩墓室按年：2020 完成 6 / fresh 0、2021 完成 23 / fresh 0、2022 完成 76 / fresh 9、
  2023 完成 56 / fresh 15、2024 完成 10 / fresh 10 —— 修复日前的 fresh 恒为 0，一眼可见是坏数据。

**因此实现给两个数**（`full_clears` 默认宽松、`full_clears_strict` 严格），
在响应里说明差异；而**「最短用时」只用严格口径** —— 检查点开局的场次时长短（跳过了前面的关卡），
混进去会得到一个"最快的全程"其实是半程的数字。


**发布时间这张表从哪来（2026-09-30 查证，三条路都试过）：**

| 来源 | 结论 |
| --- | --- |
| **Manifest** | `DestinyActivityDefinition.releaseTime` 字段**每条都有，但 4069 条全是 `0`** —— 拿不到 |
| **赛季 `startDate` 当锚点** | **不行**：突袭都在赛季开始后 3~11 天才开（狂猎 2020-11-10 → 深岩墓室 2020-11-21；永夜 2021-05-11 → 玻璃拱顶 2021-05-22；苏生 2022-02-22 → 门徒誓约 2022-03-05；奇巫 2023-08-22 → 克洛塔 2023-09-01；回响 2024-06-04 → 救赎的边缘 2024-06-07）。拿它当窗口会把整个赛季算成首日 |
| **接别人的表** | RaidHub 的 `Web-App` / `API` 仓库**没有声明许可证**（GitHub `license: null` = 默认保留所有权利），`Services` 是 "Other/NOASSERTION"；raid.report 闭源；DIM 只有**赛季**级 `releaseDate`（`d2-season-info.ts`），没有副本级 |

**所以只能自己维护，但成本很低**：约 25 行（每个突袭/地牢一行：发布时刻 + 来源），
与 `data/rotations.py` 同一模式（ADR-010：官方只给一半，其余自维护 + 锚点 + 注明）。

**而且我们有一个别人写不出的守门测试**：账号历史里**必然没有早于发布时刻的场次**。
把该副本所有 PGCR 的 `period` 取最小，断言它 **≥ 表里的发布时刻** ——
表填错（早于真实发布）测试就红。这是拿**我们自己的数据**去校验那张表，不需要信任何人。

要用别人的表就得先拿许可 —— 项目有先例：Starside 的资料是**经作者许可**随附的
（`docs/community/COMMUNITY_DATA_NOTICE.md`），不是直接抄的。

### 16.6 致命前提：`GetActivityHistory` 不全（2026-09-30 全量实测）

把三个角色的历史**翻到底**（`page` 递增直到返回空）拿到 **17,155 场**（去重后，跨 2020-08 → 2026-09，
不是时间截断）。然后按副本分组、逐场取 PGCR，与同一张 raid.report 截图对账：

| 副本 | 历史里的场次 | 我们算的「全程」 | 截图「全程」 | 截图「完成」 | 官方计数器「完成数」 |
| --- | --- | --- | --- | --- | --- |
| 深岩墓室 | 336 | **38** | **110** | 229 | **229** ✅ |
| 救赎花园 | **133** | **50** | **120** | **140** | **140** ✅ |
| 破碎王座 | 223 | 16 | 25 | 59 | 6（老内容，计数器本身也不可信） |

**救赎花园那一格是铁证**：历史里只有 **133 场**，而游戏自己的计数器与截图都写着 **140 次完成**。
**场次数少于完成数 → 历史接口必然漏场次，不是我们翻页没翻完**（翻到返回空为止；名字解析失败的只有 12/17155）。

由此三条口径**定死**：

1. **`完成数` / `导师` / `无瑕` / `单人无瑕` → 必须用官方计数器（组件 1100）**。
   它是游戏自己的账，与截图 **15/15 逐字吻合**；用 PGCR 自己数永远是错的
   （深岩墓室：我们数出 179「我完成」/ 211「任一人完成」，都低于 229）。
2. **`全程数` → 复刻不了**。三个副本我们算出来都只有截图的三分之一到一半（38 vs 110、50 vs 120）。
   要么不做这一列，要么标 `lower_bound: true` + 覆盖率，**不许当成真值展示**。
3. **`全程最短用时` → 可复刻，且已三次逐秒吻合**：
   深岩墓室 **2192s = 36m32s**、救赎花园 **1843s = 30m43s**、破碎王座 **885s = 14m45s**，
   与截图完全一致（判据见 16.1）。它同样是下界，但最快那场通常落在拿得到的场次里。

**徽章仍然复刻不了**（见 16.7）：`Solo` 我们算 3、截图 `Solo x4`（差 1，符合"漏场次"）；
而 `Flawless xN` 方向相反 —— 我们按"本人 0 死"算 26、按"全队 0 死"算 19，**截图一个都没有**。
**同一个账号、同一批数据，两种合理定义都比截图多** → 定义不同，不是数据问题。

**所以这张表的最终口径**：三列可复刻（完成数/导师/最短用时，其中前两个来自官方计数器），
一列**按口径**可复刻（全程数——见 16.1 的时间线，选 raid.report 那套还是 RaidHub 那套要先声明），
徽章不可复刻（改用官方计数器并换我们自己的名字）。

### 16.7 徽章（Flawless / Solo / Duo / Trio）：官方计数器**不能**冒充，我们也复刻不出来

社区是怎么做的：[Bungie 官方仓库 issue #951](https://github.com/Bungie-net/api/issues/951)（2019 提、
2024 关成 "distant future wishlist"）里，有人请求「批量 PGCR，或者把人数 / `startingPhaseIndex` /
全队死亡数直接塞进 `getActivityHistory`」，原话是
「**i'm trying to get players badges like how Raid.report does it but having a hard time to process all
those PGCRs as it takes way too long and i have to throttle the requests too … 那能省掉租一个数据库**」。
**Bungie 没加。** 所以这条路只有一种走法：**自己逐场抓 PGCR 并落库**（RaidHub 的 FAQ 也写着
"when your profile is next crawled by RaidHub's backend" —— 他们是持续爬的）。
另外 issue 里还记着：`startingPhaseIndex` 在 Beyond Light 到巫后之间彻底坏了（见 16.1）。

截图那种 `Flawless x11` / `Flawless Solo` / `Solo x4` 的徽章，**不是**官方计数器的值。四个反例（同一天实测）：

| 副本 | 官方 `无瑕完成数` | 官方 `单人无瑕完成数` | 截图徽章 |
| --- | --- | --- | --- |
| 贪婪之握 | **1** | 1 | `Flawless Solo` `Solo` **`Flawless x11`** |
| 异端深渊 | **29** | **0** | `Flawless Solo`（有！）`Solo` `Flawless x6` |
| 破碎王座 | **27** | 无此计数器 | `Flawless Solo`（有！）`Solo x4`（**没有** `Flawless xN`） |
| 二象性 / 守望者尖塔 | 1 / 1 | 1 / 1 | `Flawless Solo` `Solo`（这两行与官方**对得上**） |

试过的两种候选定义**都对不上**（2026-09-30 逐场 PGCR 统计）：

| 副本 | 样本完成数 | 「我本人 0 死」 | 「全队 0 死」 | 截图的 `Flawless xN` |
| --- | --- | --- | --- | --- |
| 贪婪之握 | 11 | 10 | 8 | 11 |
| 破碎王座 | 22 | 17 | 17 | **没有这个徽章** |

破碎王座是判决性的：两种定义都给 17/22，而站点一个 `Flawless` 徽章都没给。
**所以第三方那套徽章的定义我们还没还原出来，也不该照截图反推。**

**决定（写进计划）**：徽章这一块要么用**官方计数器**并换成我们自己的名字与口径
（`flawless` = 官方无瑕完成数、`solo_flawless` = 官方单人无瑕完成数），
要么自己按 PGCR 定义并在响应里写清定义；**不许把官方计数器贴上第三方的徽章名**。
`Duo` / `Trio` 官方完全没有，只能从 PGCR 的 `playerCount` 来 —— 而它**不等于队伍人数**
（实测 6 人突袭里出现 `playerCount = 7 / 8`，说明中途换人算进去了），
所以 `Solo`（=1）、`Duo`（=2）、`Trio`（=3）成立，但"6 人满编"要另找判据。

### 16.8 排名与 DayOne 编号：**本质是人群数据，单账号复刻不了**（2026-09-30 查证）

截图页头那两行（`Full Clears Rank: Diamond IV 439` / `Speed Rank: Platinum I 6h32m26s`）
和徽章里的 `DayOne #15358`，都**不是这个账号的属性**，而是"这个账号在某个库里排第几"。

[RaidHub FAQ](https://raidhub.io/faq) 把口径写明了：

- **等级 = 百分位**：`Top 500` / `Grandmaster`（前 0.05%）/ `Master`（0.05–0.20%）/
  `Diamond`（0.20–0.50%）/ `Platinum`（0.5–1.5%）/ `Gold` / `Silver` / `Bronze` / `Iron`，
  每一档再切成 `I–V` 五段。**分母是「RaidHub 数据库里的所有玩家」。**
- **WFR（World First Rating）**：`Σ 1.25^(raid-1) / √placement(raid)` ——
  需要知道你在**每一次世界首杀赛里的名次**。
- 他们还有「每天 10:00 GMT+0 刷新一次个人榜单」这种运维节奏 —— 也是人群库才需要的东西。

**结论**：

| 项 | 能不能做 | 原因 |
| --- | --- | --- |
| `DayOne` **是否**首日完成 | ✅ 能 | 官方记录（组件 900，见 16.5）或"PGCR 时间落在自维护的发布窗口内" |
| `DayOne #编号` | ❌ 不能 | 是全球完成顺序，需要人群级库 |
| `Full Clears Rank` / `Speed Rank` | ❌ 不能 | 是百分位排名，分母是别人的全量玩家库 |

要做这三样只能自己**持续爬 PGCR 建库**（见 16.7 里 issue #951 的来龙去脉），
那是另一个产品形态，不是这个本地工具该干的事 —— 第一版一律 `unavailable`，
并把"这是人群数据、我们没有"写在 reason 里，**不用近似值顶替**。

### 16.9 但社区有现成的：RaidHub 的 semi-public API（2026-09-30 查证）

**16.8 的结论要修正一句**：不是"拿不到"，而是"**Bungie 官方不给，社区服务给**"。
[RaidHub](https://raidhub.io) 把它的 API 规格公开在仓库里
（`Raid-Hub/API` 的 `open-api/openapi.json`），自述为 **"The Semi-public API for RaidHub"**，
`servers: https://api.raidhub.io`，安全方案是 **`API Key`**（未带 key 直接回 401 `ApiKeyError`）。

**正好覆盖我们缺的那几样**：

| 我们要的 | 他们的端点 / 字段 |
| --- | --- |
| **DayOne 排名（`#15358`）** | `GET /player/{membershipId}/profile` → `worldFirstEntries[].rank` |
| DayOne 布尔 | 同上的 `isDayOne`；还有 `isContest` / `isWeekOne` / `isChallengeMode` |
| **距发布多久**（我提的替代品） | 同上的 `timeAfterLaunch`（整数） |
| **发布时间表**（16.5 里说要自己维护的那张） | `GET /manifest` → `activityDefinitions[].releaseDate` / `contestEnd` |
| **副本归组映射**（P2 那张表） | `GET /manifest` → `hashes`：Bungie hash → `activityId` / `versionId` |
| 全程 / 无瑕 / 人数 | `GET /player/{membershipId}/instances` → `fresh` / `flawless` / `playerCount`（**他们已经算好了**） |
| 竞赛完整排名 | `GET /leaderboard/team/contest/{raid}`；前 1000 名：`GET /leaderboard/team/first/{activity}/{version}` |

`WorldFirstEntry` 的完整定义（照抄自他们的规格）：

```json
{"activityId":0,"instanceId":"0","timeAfterLaunch":0,"rank":0,
 "isDayOne":true,"isContest":true,"isWeekOne":true,"isChallengeMode":true}
```

**接它要守三条**（这不是技术问题，是纪律问题）：

1. **要先拿许可**。规格里没写申请方式（"semi-public"），得去他们的 Discord 问。
   **不能绕过 key 去爬网页** —— 那既不体面也不稳。项目有正确先例：Starside 的资料是经作者许可随附的。
2. **隐私**：调用要把你的 Bungie membershipId 发给第三方。所以必须**默认关闭、用户显式开启**，
   和账号写入要 `confirmed=true` 一个道理。
3. **标来源、能降级**：它是**第三方参考**（`source: "raidhub"` + `fetched_at`），不是官方事实；
   拿不到就退回"官方计数器 + PGCR 自算"那套，**不许用缓存或近似值顶替**。

**因此数据分档变成四层**，RaidHub 属于第三层（社区参考）里最实时的那个：
Bungie 官方 / 你的账号 / 社区服务（RaidHub、Starside）/ 我们自己算的。

#### 16.9.1 怎么拿 key（2026-09-30 查证）

**没有自助申请入口**。key 是**运营方手工签发的服务端条目**：他们仓库里的
`api-keys.example.json` 长这样 —— 复制成 `api-keys.json`，每条 `{description, origin, key}`，
`PROD=true` 时请求必须带 **`X-API-KEY`** 头匹配其中一条。
规格里的安全方案名就叫 `API Key`（`type: apiKey`, `name: X-API-KEY`, `in: header`），
`info.contact` 是 **`admin@raidhub.io`**；文档站（`api-docs.raidhub.io`）只是同一份规格的渲染，
**没有任何"如何申请"的说明**。

三个可用的渠道（2026-09-30 逐个核实过）：

| 渠道 | 说明 |
| --- | --- |
| **`admin@raidhub.io`** | **他们唯一的公开邮箱**：`raidhub.io` 的首页 / privacy / terms / faq 页脚都是它，GitHub org 资料里填的也是它，API 规格 `info.contact` 还是它（三处一致）。要凭据走邮件最合适 |
| GitHub Issue | **开在 `Raid-Hub/API/issues`**（API 仓库，当时只剩 4 个未关 issue，dev 更常看），不要开在 `Raid-Hub/Web-App`（那是网站，35 个未关） |
| Discord `discord.raidhub.io` | 他们 FAQ 的主渠道 |

README 里有一句对我们特别关键：**「use `origin` `"*"` for local tools such as raidhub-discord」**
—— **他们自己的 Discord 机器人就是一个本地工具**。所以"本地单机工具"不是例外，是他们本来就支持的形态。

**只需要 API Key 就够，不必去要 `clientSecret`**：

- `GET /manifest`（发布时间 + hash→activityId 映射）—— 全局 API Key 即可；
- `GET /leaderboard/team/contest/{raid}` 与 `/leaderboard/team/first/{activity}/{version}`
  —— 两者都只要 API Key，而且 **`search` 参数的类型是 `^\d+n?$`（成员 ID）**，
  404 里还专门有 `PlayerNotOnLeaderboard`：**`?search=<membershipId>` 就能查出这个人的竞赛名次**；
- 只有 `/player/{membershipId}/profile`（带 `worldFirstEntries`）、`/history`、`/teammates`
  要 **Bearer Token**（走 `/authorize/user`，body 里要 `clientSecret`）—— **这条路我们可以不走**。

**一个安全坑（写进实现纪律）**：它的错误响应**会把请求里的 key 原样回显**：

```json
{"code":"ApiKeyError","error":{"message":"Invalid API Key","apiKey":"00000000-0000-4000-8000-000000000000"}}
```

所以**这个响应体绝不能落进日志或审计**（项目规矩：密钥永不进日志）—— 出错只记 `code` 与 `message`，
`apiKey` 字段一律丢弃。另外没带 key 是 `Missing API Key`、带错 key 是 `Invalid API Key`，
两者要分开报（"没配 key"和"key 不对"是两件事，下一步动作不同）。


## 十七、游戏内配装槽（官方配装）：只有四个动作，三个标识必须都给（2026-10-06 实测）

要做"把配装存进游戏内配装槽、以后游戏内一键换"之前先看这一节。
代码在 `destiny_mcp/bungie_loadouts.py`（端点族）与 `destiny_mcp/services/loadout_official_identifiers.py`（标识补齐）。
当天全部实测都在真实账号上做（守护者等级 11，三个角色）。

### 17.1 官方只给了四个动作 —— **没有"把任意配装数据写进槽位"的接口**

`Destiny2/Actions/Loadouts/` 下只有这四条，别去找第五条：

| 动作 | 干什么 | 位置要求 |
| --- | --- | --- |
| `SnapshotLoadout` | 把角色**当前装备**存进槽位 | 无（猎人在**活动中**也成功） |
| `UpdateLoadoutIdentifiers` | 改名称/图标/颜色 | 无 |
| `ClearLoadout` | 清空槽位 | 无 |
| `EquipLoadout` | 应用某个槽位 | **必须不在活动里** |

怎么排除掉"写任意数据"这条路的（三条独立证据，不是查不到就算了）：

- 官方帮助页 `HelpDetail/POST?uri=Actions/Loadouts/SetLoadout/` → **HTTP 404**；
  同一次同一种写法的 `...EquipLoadout/` → **HTTP 200**（对照组，证明 404 不是路径拼错）；
- 文档全量（版本 2.21.8，`--check` 与线上一致）：`Destiny2` 一共 43 个端点，
  `Actions/Loadouts/` 下**只有上表四个**；
- DIM 源码（本地克隆）`src/app/bungie-api/destiny2-api.ts` 用的**就是这四个**；
  它那个「保存为游戏内配装」按钮的弹窗标题是 `InGameLoadout.CreateTitle`
  = **「从当前装备创建游戏内配装」**，动作名 `snapshotInGameLoadout`。

**结论**：要让配装进游戏槽，只有"**先把配装穿到身上，再快照**"这一条路。

### 17.2 三个标识**必须都给** —— 少一个就是 HTTP 500

`SnapshotLoadout` 与 `UpdateLoadoutIdentifiers` 都吃 `nameHash` / `iconHash` / `colorHash`：

| 请求 | 结果 |
| --- | --- |
| 三个都给真值（`characterId` 数字或字符串都行） | ✅ 成功（响应 `0`） |
| 少给一个 | ❌ HTTP 500 `DestinyInvalidRequest` |
| 给 `null` | ❌ 同上 |
| **干脆省略** | ❌ 同上 |
| 三个都给空哨兵 `2166136261` | ❌ 同上（**创建不出"没名字"的槽**） |

上游原文（省略三个标识时）：

    http_status: 500, message: Your request was invalid.,
    error_status: DestinyInvalidRequest,
    url: https://www.bungie.net/Platform/Destiny2/Actions/Loadouts/SnapshotLoadout/

两个直接后果：

1. **`update_official_identifiers`「只改名字」在 API 上不存在** —— 必须把另外两个旧值带上；
2. 往**空槽**里存时，名称/图标/颜色必须由调用方选（`loadout_assistant(intent="search_identifiers")`
   给候选）；这跟游戏内那个弹窗强制你选名字/图标/颜色是同一件事。

**两个被证伪的想当然**（都做过对照实验，别再照着改）：

- `characterId` 发**数字或字符串都能成** —— "int64 被 JS 精度截断"不成立
  （`2305843009679355779` 确实 > 2^53，但两种写法都回了成功）；
- HTTP 500 与"槽位没解锁"无关：同样的请求在**三种**槽位（已用/空/最大索引 19）上
  失败与成功的分界**只跟三个标识齐不齐有关**。

### 17.3 写入有同步窗口：改标识**立刻**读还是旧值

`UpdateLoadoutIdentifiers` 回 `ErrorCode=1` 之后：**0 秒**回读三个标识**全是旧值**，
**30 秒**回读才是新值（中间没再测更细的边界）。与本文档第五节（3～10 秒）同一族现象，
但**别把"写完立刻读一次"当判据**——那会把成功的写入报成假失败。

### 17.4 `EquipLoadout` 的 URL **不带 `{membershipType}` 路径段**（曾经一直是 404）

同一个请求体，只改路径（2026-10-06 真机，猎人 9 号槽 = 身上这套，等价 no-op）：

| 路径 | 结果 |
| --- | --- |
| `Destiny2/Actions/Loadouts/EquipLoadout/3/` | ❌ **HTTP 404**（`Expected JSON response, Got text/html`） |
| `Destiny2/Actions/Loadouts/EquipLoadout/` | ✅ **成功**（响应 `0`） |

`membershipType` 在**请求体**里，和另外三个动作一样；官方帮助页给的 URL 也没有路径参数。
**这意味着 `loadout_assistant(intent="equip_loadout")` 在 2026-10-06 之前一次都没成功过** ——
404 被包成"装备失败"，看着像上游故障。守门逐字钉住 URL：
`tests/test_bungie_client_actions.py::test_equip_loadout_path_has_no_membership_type_segment`。

### 17.5 `EquipLoadout` 成功时返回的是**裸 int**，不是信封对象

`static_request` 对这条动作**已经剥掉信封**：成功时返回的是 `Response`（这里是 `0`），
不是 `{"ErrorCode": 1, ...}`。所以"自己 `static_request` + `result.get("ErrorCode")`"会炸
`'int' object has no attribute 'get'`（2026-10-06 真机：URL 修好后立刻暴露）。
归一只有一处：`BungieClient._post_action`（它把裸值包成 `{"ErrorCode": 1, "Response": ...}`），
四个动作**都**走它。守门：
`tests/test_bungie_client_actions.py::test_equip_loadout_tolerates_a_bare_int_response`。

### 17.6 `EquipLoadout` 不能在活动里；另三个没这个限制

同一天同一账号上：

- 猎人正在活动里（组件 204 `currentActivityHash=82913930`，12 分钟前开始）→
  `EquipLoadout` 回 **`DestinyCannotPerformActionAtThisLocation`**，
  而**同一次会话里** `SnapshotLoadout` 与 `ClearLoadout` 都成功；
- 术士/泰坦在轨道（`currentActivityHash=0`）→ `SnapshotLoadout` 同样因缺标识而失败，
  补全标识后成功。

DIM 专门捕获这个码并在界面上提示"回轨道再试"——我们的话术照它。

### 17.7 槽位数组**永远 20 条**，数组长度不代表解锁了几个

组件 206 `characterLoadouts.data.<charId>.loadouts` 是**定长 20 的数组**（索引 0–19，与
`loadoutIndex` 字段无关——上游根本不回这个字段，位置即索引）。实测该账号：

| 角色 | 有内容的槽 | 空槽 |
| --- | --- | --- |
| 猎人 | 8（本次测试后为 9） | 12 |
| 术士 | 14 | 6 |
| 泰坦 | 18 | 2 |

空槽的判据（两个都要）：三个标识都是 `2166136261` **且** 十件 `itemInstanceId` 全是 `"0"`。

**别拿 Manifest 的 `DestinyLoadoutConstantsDefinition.loadoutCountPerCharacter` 当上限**：
它写的是 **10**，而这个账号泰坦已经用了 **18** 个 —— 那个字段是过期的。
DIM 的做法是直接把"数组长度"当已解锁数（`availableLoadoutSlotsSelector`）。

### 17.8 槽里存的是什么：10 件 + 一个插槽一个 plug

每个槽固定 **10 件**（实测逐件查过桶）：

| # | 桶 | 例 |
| --- | --- | --- |
| 1–3 | 动能 / 能量 / 威能武器 | 命运终结者、隐秘追猎、光芒复仇 |
| 4–8 | 头 / 臂 / 胸 / 腿 / 职业护甲 | 星界夜鹰…光泽披风 |
| 9 | **分支职业** | 棱镜猎人（14 个非空插槽，含全部碎片） |
| 10 | **赛季神器** | 女王兰香炉（7 个非空插槽 = 神器天赋选择） |

每件的原始字段**只有两个**：

```json
{"itemInstanceId": "6917530193641893728",
 "plugItemHashes": [2166136261, 3250572790, "...一个插槽一个 hash，共 16 位..."]}
```

护甲上读回来能看到 `enhancements.v2_*` 模组、`core.gear_systems.armor_tiering.plugs.tuning.mods`
调谐模组、`shader` 着色器、`armor_skins_*` 皮肤 —— 所以**快照会把皮肤和全部模组一起存进去**
（用户看到的 DIM 行为就是这个）。但要说清：**快照是"照抄当时身上穿的"**——
皮肤跟着装备走，神器天赋是"当时选的那套"，不会自动变成配装模板要的那套。

**回读核对的一条纪律**（照抄 DIM 的注释，它踩过）：
*"In game loadouts map any socket that has only a single option to UNSET_PLUG_HASH instead of
the real plug hash"* —— 所以**单选项插槽不能要求逐位相等**，否则回读永远假红。

### 17.9 代码位置

| 判据/实现 | 在哪 |
| --- | --- |
| 端点族 + 上面这些结论 | `destiny_mcp/bungie_loadouts.py` |
| 三个标识怎么补齐（继承现值 / 空槽必须给全） | `destiny_mcp/services/loadout_official_identifiers.py` |
| 槽位与服务方法 | `destiny_mcp/services/loadout_service.py` |
| 守门 | `tests/test_loadout_official_identifiers.py`、`tests/test_bungie_client_actions.py` |
