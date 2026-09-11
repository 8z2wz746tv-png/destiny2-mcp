"""高危参数的 schema 说明：写一次，所有工具共用。

八个 assistant 的签名里，最容易被传错的那几个参数此前**一个字都没有**：
`item_name`、`item_instance_id`、`item_type`、`type_name`、`location`、`character`。
模型只能靠参数名猜，于是把 item_instance_id 传给 intent="get"、
把物品名传给 intent="summary" —— 这两种错都在真实会话里出现过。

schema 说明和 `_param_contracts.py` 的拦截是两层，分工不同：

- 这里是**建议**：跟着工具一起进上下文，模型不用额外读文档就能看到；
- 那里是**保证**：真传错了会被拒绝。

说明只写「这个参数是什么、常见配对是哪几个」，不重复列出全部认领者 ——
那张名单会随意图增减而腐烂，而拦截错误里给的名单永远是最新的。
参数说明必须挂在**每个使用点**上（tests/test_ignored_parameters.py 会检查），
否则新加的同名参数又会变成没有说明的状态。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

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
        '装备模组用 intent="equip_artifact_mod"；artifact_mod_name 没有任何 intent 读。'
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
