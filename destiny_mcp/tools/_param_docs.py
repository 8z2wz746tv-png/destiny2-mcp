"""参数的 schema 说明：写一次，所有工具共用。

模型只看得见 schema。这里给**八个 assistant 的每一个参数**写说明，包括四个工具的
`intent`（以前只有 weapon/build 有，同一件事两种待遇）。曾经这些说明是缺的，
模型只能靠参数名猜：把 item_instance_id 传给 intent="get"、把物品名传给
intent="summary" —— 这两种错都在真实会话里出现过。

schema 说明和 `_param_contracts.py` 的拦截是两层，分工不同：

- 这里是**建议**：跟着工具一起进上下文，模型不用额外读文档就能看到；
- 那里是**保证**：真传错了会被拒绝。

说明只写「这个参数是什么、常见配对是哪几个」，不重复列出全部认领者 ——
那张名单会随意图增减而腐烂，而拦截错误里给的名单永远是最新的。

用法：`assistants.py` 用 `from . import _param_docs as fields`，参数写成
`limit: fields.Limit = 10`。模块限定导入是为了让新增别名不再让 import 块长胖。
说明必须挂在**每个使用点**上：`tests/test_ignored_parameters.py` 会检查
「每个参数都有说明」，漏一个就红。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ._requests import (
    InventoryIntent,
    PlayerIntent,
    SubclassIntent,
    WorldIntent,
)

PlayerName = Annotated[
    str | None,
    Field(description=(
        '玩家 BungieName（形如 名字#1234）。留空则用 .env 里配置的默认玩家，'
        "或当前 OAuth 登录的玩家。"
    )),
]

Character = Annotated[
    str,
    Field(description=(
        "角色：hunter/warlock/titan，或猎人/术士/泰坦。"
        "只在该 intent 需要指定角色时才有意义（如 vendor、collectible_node、equip）。"
    )),
]

CharacterOptional = Annotated[
    str | None,
    Field(description=(
        "角色：hunter/warlock/titan，或猎人/术士/泰坦。"
        "只在该 intent 需要指定角色时才有意义（如 vendor、collectible_node、equip）。"
    )),
]

Location = Annotated[
    str,
    Field(description=(
        "位置过滤：vault=仓库，character=角色身上，all=全部；留空由服务默认。"
        "只被 summary、get、search、type 读。"
    )),
]

ItemName = Annotated[
    str,
    Field(description=(
        '物品名称（中英文）。按名字找东西配 intent="search"；查重复武器配 duplicates；'
        '移动物品配 move；查收藏品状态配 world_assistant(intent="collectible_item")。'
    )),
]

ItemInstanceId = Annotated[
    str,
    Field(description=(
        "物品副本的唯一 instance ID（一物一码，不是物品 hash）。"
        '读某个副本当前 Perk 用 weapon_assistant(intent="compare")；'
        '移动/装备/锁定/取回用 inventory_assistant 的 move/equip/lock/pull_postmaster。'
        'intent="get" 不读它。'
    )),
]

ItemInstanceIds = Annotated[
    list[str] | None,
    Field(description=(
        '多个副本 instance ID，只被批量装备 intent="equip_many" 使用；'
        "单个副本用 item_instance_id。"
    )),
]

ItemType = Annotated[
    str,
    Field(description=(
        "物品类型（中文分类，如 手炮、火箭筒、头盔）。被 summary/get/type 读；"
        "按名字查要用 item_name，不是这个。"
    )),
]

TypeName = Annotated[
    str,
    Field(description=(
        '更细的类型名，只被 intent="type" 和 intent="duplicates" 读；'
        'intent="get" 用的是 item_type。'
    )),
]

WeaponName = Annotated[
    str,
    Field(description=(
        "武器名称（中英文）。"
        '除 intent="type"（它按 weapon_type 查）和 intent="perk_description"'
        "（它查 perk_name）外，武器意图都读它。"
    )),
]

NamePrefix = Annotated[
    str,
    Field(description=(
        '名字片段，模糊搜玩家用（intent="find"）。精确查人要用完整 BungieName 配 intent="search"。'
    )),
]

LoadoutId = Annotated[
    str,
    Field(description=(
        '已存配装的 ID。只有 intent="delete" 和 intent="equip_loadout" 读它；'
        "list/get 会返回全部配装、不接受 ID，要哪一套请从结果里挑。"
    )),
]

SlotNumber = Annotated[
    int,
    Field(ge=1, le=20, description=(
        "Bungie 官方配装槽位号（1–20）。只有快照/改标识/清空三个写入 intent 读它；"
        "list/get 返回全部槽位，不接受槽位号。"
    )),
]

ActivityId = Annotated[
    str,
    Field(description=(
        '活动实例 ID。只有 intent="pgcr" 读它（看单场结算）；'
        '看最近几场用 intent="history"。'
    )),
]

GroupId = Annotated[
    str,
    Field(description=(
        '公会（clan）ID。只有 intent="clan_leaderboards" 读它，且必填；'
        '个人排行榜用 intent="leaderboards"，不需要 group_id。'
    )),
]

ArtifactModHash = Annotated[
    int,
    Field(description=(
        '神器模组的 hash，必须为正数。查模组详情用 intent="artifact_mod"，'
        '装备模组用 intent="equip_artifact_mod"；没有按名字查模组的入口。'
    )),
]

CanonicalBuild = Annotated[
    dict | None,
    Field(description=(
        '服务端签发的完整配装候选（一次绑定、五个护甲部位齐全）。'
        '只能在用户确认后原样回传给 intent="equip_build"；'
        "禁止自己拼 hash、用 score，或把社区模板/build_template 当成它。"
    )),
]


# ── 四个工具的 intent 说明：每个工具的意图清单写一次 ────────────────────────
# 意图是路由的核心，weapon/build 早就有说明，其它四个不能没有。

PlayerIntentField = Annotated[
    PlayerIntent,
    Field(description=(
        "玩家查询意图。profile=我的角色列表与档案；"
        "search=按完整 BungieName 精确搜人，拿 membership_id；"
        "find=名字记不全时按片段模糊搜候选。"
    )),
]

InventoryIntentField = Annotated[
    InventoryIntent,
    Field(description=(
        "背包查询意图。summary=数量概况；get=列出清单；search=按物品名找（item_name）；"
        "type=按类型列（type_name）；duplicates=按精确 item_hash 分组找重复武器。"
        "移动/装备/锁定/取回等写入用 move/transfer/equip/equip_many/pull_postmaster/lock/track_quest。"
    )),
]

SubclassIntentField = Annotated[
    SubclassIntent,
    Field(description=(
        "子职业意图。get=当前配置；options=某元素的某类可选项（需要 element + component）；"
        "fragments=碎片列表（需要 element）；fragment_details=单个碎片效果；"
        "artifact=赛季神器；artifact_mod=神器模组详情（需要 artifact_mod_hash）；"
        "modify/equip_artifact_mod=写入。"
    )),
]

WorldIntentField = Annotated[
    WorldIntent,
    Field(description=(
        "世界/周常意图。weekly=本周概要；weekly_full=完整周常；vendor=商人当前货架；"
        "search_collectible_nodes=按关键词搜收藏品节点；collectible_node=某节点解锁状态（需要 collectible_node_hash）；"
        "collectible_item=某件物品的收藏状态；community=本地社区资料（唯一能跨分类搜的入口）。"
    )),
]

# ── 其余共享参数 ────────────────────────────────────────────────────────────

Limit = Annotated[
    int,
    Field(description=(
        "最多返回多少条。只限制返回条数，不限制扫描范围，被截断时响应里会带 truncated/总数。"
        "intent=\"vendor\" 时：不点名商人 = 最多列几个商人，点名 = 每个商人最多几件商品。"
        "不读它的 intent 传了会被拒绝。"
    )),
]

Offset = Annotated[
    int,
    Field(ge=0, description="翻页偏移，配合 next_offset 继续读；只有支持翻页的 intent 会读它。"),
]

Query = Annotated[
    str,
    Field(description="关键词。搜社区资料、搜收藏品节点、搜官方配装标识时用它，具体搜哪里取决于 intent。"),
]

Confirmed = Annotated[
    bool,
    Field(description=(
        "写入确认。会改账号状态的 intent 必须由用户明确同意后传 true，"
        "否则返回 confirmation_required 且不调用服务层。不要替用户推断同意。"
    )),
]

KnowledgeId = Annotated[
    str,
    Field(description="本地社区资料的条目 ID（上一次 community 搜索返回的）。给了它就读详情，不再按关键词搜。"),
]

CommunitySection = Annotated[
    Literal["text", "tables", "links"],
    Field(description="社区资料要读哪一部分：正文 text、表格 tables、外链 links。"),
]

Scenario = Annotated[
    str,
    Field(description="社区配装的场景筛选（取值由本地资料决定）。只在 build_assistant(intent=\"community\") 上生效。"),
]

Category = Annotated[
    str,
    Field(description="社区配装的分类筛选（取值由本地资料决定）。只在 build_assistant(intent=\"community\") 上生效。"),
]

IncludeInventory = Annotated[
    bool,
    Field(description=(
        "要不要读账号库存。weapon 的 filter_rolls/analyze 用它在全量定义与账号扫描之间切换；"
        "build 的 community 用它决定要不要把社区模板与你的库存比对。"
    )),
]

ArmorSlot = Annotated[
    str,
    Field(description="护甲部位过滤：helmet/gauntlets/chest/legs/class_item（也认头盔/手套/胸甲/腿甲/职业物品）。"),
]

Rarity = Annotated[
    str,
    Field(description="稀有度过滤（如 传说/异域、legendary/exotic）。只有列出清单的 intent 读它。"),
]

ToCharacter = Annotated[
    str,
    Field(description="目标角色（hunter/warlock/titan 或猎人/术士/泰坦）。intent=\"transfer\" 用它。"),
]

FromCharacter = Annotated[
    str,
    Field(description="当前所在角色；留空表示不限制来源。move/transfer 读它。"),
]

Destination = Annotated[
    str,
    Field(description='移动目标：vault/仓库，或角色名。intent="move" 必填。'),
]

Equip = Annotated[
    bool,
    Field(description='移动的同时装上（intent="move" + equip=true）。目标为仓库时不能 equip。'),
]

Locked = Annotated[
    bool,
    Field(description='锁或解锁：true=锁定，false=解锁。只有 intent="lock" 读它。'),
]

Tracked = Annotated[
    bool,
    Field(description='追踪或取消：true=追踪，false=取消。只有 intent="track_quest" 读它。'),
]

RequiredPerks = Annotated[
    list[str] | str | None,
    Field(description="必须全部命中的 Perk（同栏任一即可满足其中一项）。只有 catalog 与 filter_rolls 支持。"),
]

AnyPerks = Annotated[
    list[str] | str | None,
    Field(description="命中任意一个即可的 Perk。只有 catalog 与 filter_rolls 支持。"),
]

ExcludedPerks = Annotated[
    list[str] | str | None,
    Field(description="命中就排除的 Perk。只有 catalog 与 filter_rolls 支持。"),
]

SetBonusName = Annotated[
    str | None,
    Field(description='套装名。intent="set_bonus" 查它的 2/4 件效果；求解类 intent 把它当硬约束。'),
]

SetBonusCount = Annotated[
    int | None,
    Field(ge=2, le=4, description="套装件数约束（2 或 4）。只在求解类 intent 上生效。"),
]

Name = Annotated[
    str,
    Field(description='要保存的配装名字。只有 intent="save" 读它。'),
]

Notes = Annotated[
    str,
    Field(description='配装备注，跟配装一起存下来。只有 intent="save" 读它。'),
]

NameHash = Annotated[
    int | None,
    Field(description="官方配装槽的名称标识 hash。先用 intent=\"search_identifiers\" 查，再回传。"),
]

IconHash = Annotated[
    int | None,
    Field(description="官方配装槽的图标标识 hash。先用 intent=\"search_identifiers\" 查，再回传。"),
]

ColorHash = Annotated[
    int | None,
    Field(description="官方配装槽的颜色标识 hash。先用 intent=\"search_identifiers\" 查，再回传。"),
]

Kind = Annotated[
    str,
    Field(description='搜哪一类官方配装标识：all/name/icon/color（也认 全部/名称/图标/颜色）。只有 intent="search_identifiers" 读它。'),
]

Element = Annotated[
    str,
    Field(description=(
        "元素：void/solar/arc/stasis/strand/prism（也认 虚空/烈日/电弧/冰影/编织/棱镜）。"
        'options 与 fragments 必须给，否则返回 subclass_error。'
    )),
]

Component = Annotated[
    str,
    Field(description=(
        "要列哪一类：super/melee/grenade/aspect/movement（也认 超能/近战/手雷/星象/移动）。"
        '只有 intent="options" 读它，且必填。'
    )),
]

FragmentName = Annotated[
    str,
    Field(description='碎片名（中英文，模糊匹配）。intent="fragment_details" 查它的效果。'),
]

ArtifactName = Annotated[
    str,
    Field(description='赛季神器名。留空则列全部神器。只有 intent="artifact" 读它。'),
]

Changes = Annotated[
    dict[str, str] | None,
    Field(description=(
        "要改的组件 → 目标名称，例如 {\"super\": \"金色枪\"}。"
        "键可用 super/melee/grenade/class_ability/movement/aspect/fragment，值中英文皆可。"
        '只有 intent="modify" 读它，且必填。'
    )),
]

Mode = Annotated[
    str | None,
    Field(description="活动模式过滤（如 raid/dungeon/allpvp）。history、排行榜和社区资料读它。"),
]

StatId = Annotated[
    str | None,
    Field(description='榜单指标，例如 "activitiesCleared"。只有排行榜类 intent 读它。'),
]

MaxTop = Annotated[
    int,
    Field(description="榜单取前多少名。只有排行榜类 intent 读它。"),
]

Count = Annotated[
    int,
    Field(description="要多少条记录。history、武器历史、聚合统计和社区资料读它；生涯统计不分条数。"),
]

VendorName = Annotated[
    str,
    Field(description=(
        '商人名、名字片段、别名或 hash（如 班西-44 / 萨瓦拉 / 672118013）。'
        '留空 = 先列出有哪些商人（菜单，不含商品）；写一个名字 = 直接看那个商人的货架与分类；'
        '名字匹配到多个时会返回候选列表而不是猜。intent="vendor" 用它。'
    )),
]

CollectibleNodeHash = Annotated[
    int,
    Field(description=(
        "展示节点（文件夹）的 hash，必须先经 intent=\"search_collectible_nodes\" 拿到。"
        "它不是 collectible_item 返回的 collectible_hash —— 后者是单个收藏品的号，"
        '拿它来查节点会被拒绝。'
    )),
]

IncludeInvisible = Annotated[
    bool,
    Field(description="是否把隐藏节点也算进来。只有 intent=\"collectible_node\" 读它。"),
]

PriorityStat = Annotated[
    str | None,
    Field(description=(
        'intent="armor_mods" 时是模组属性筛选词：weapons/health/class_stat/grenade/super_stat/melee'
        "（也认 武器/生命/职业/手雷/超能/近战）；求解类 intent 里是排序用的优先级属性名。"
    )),
]
