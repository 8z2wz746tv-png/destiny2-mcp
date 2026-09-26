"""中文词表的唯一出处：六维、职业、位置、元素、旧属性名。

以前同一份映射散在 9 个文件里，而且**值已经漂了**——Manifest 官方中文是「生命值」，
`_armor_branches` / `_armor_ladder` / `build_tools` 三处写成了「生命」，
`armor_mod_service` 的旧名映射还把玩家说的「韧性模组」换成「生命模组」，
而游戏里那个模组叫「生命值模组」——真机实测直接报「没找到护甲模组 '韧性模组'」。

权威来源是 Manifest（`DestinyStatDefinition` / `DestinyClassDefinition` 的官方中文），
`tests/test_vocabulary.py` 把实测到的官方名钉住，并禁止再抄一份。

两个概念别混：

- **展示名**（`*_LABELS_ZH`）：我们输出给用户看的规范名，只能有一个（生命值）。
- **输入别名**（`*_ALIASES`）：用户可能怎么打字（生命值/生命/韧性……），可以有多个，
  但必须都指向同一个规范键。
"""

from __future__ import annotations

import unicodedata

# ── 六维（Armor 3.0 的叫法；官方中文来自 DestinyStatDefinition）────────────

STAT_KEYS: tuple[str, ...] = (
    "weapons",
    "health",
    "class_stat",
    "grenade",
    "melee",
    "super_stat",
)

STAT_LABELS_ZH: dict[str, str] = {
    "weapons": "武器",
    "health": "生命值",
    "class_stat": "职业",
    "grenade": "手雷",
    "melee": "近战",
    "super_stat": "超能",
}

# 旧六维名 → Armor 3.0 的规范中文名（很多玩家还在用旧叫法）。
# 值必须能在游戏里对上模组名：`韧性` 曾经被换成「生命」，而模组叫「生命值模组」，
# 于是"韧性模组"这条路直接查不到东西（真机复现过）。
LEGACY_STAT_ALIASES: dict[str, str] = {
    "机动": "武器",
    "韧性": "生命值",
    "恢复": "职业",
    "力量": "近战",
    "纪律": "手雷",
    "智慧": "超能",
}

# 输入别名 → 六维键（键名、官方中文、官方英文、旧中文名都认）
STAT_ALIASES: dict[str, str] = {
    **{key: key for key in STAT_KEYS},
    **{label: key for key, label in STAT_LABELS_ZH.items()},
    "生命": "health",
    "力量": "melee",
    "strength": "melee",
    "discipline": "grenade",
    "intellect": "super_stat",
    "super": "super_stat",
    "class": "class_stat",
}

# ── 职业 ────────────────────────────────────────────────────────────────

CLASS_KEYS: tuple[str, ...] = ("titan", "hunter", "warlock")

CLASS_LABELS_ZH: dict[str, str] = {
    "titan": "泰坦",
    "hunter": "猎人",
    "warlock": "术士",
}

# 组件里的 classType（0/1/2）→ 键
CLASS_TYPE_KEYS: dict[int, str] = {0: "titan", 1: "hunter", 2: "warlock"}

CLASS_ALIASES: dict[str, str] = {
    **{key: key for key in CLASS_KEYS},
    **{label: key for key, label in CLASS_LABELS_ZH.items()},
}


def class_key(value: str | int | None) -> str:
    """职业（`hunter`/`猎人`/`1`/`Hunter`）→ 统一键；认不出来给空串（不编）。"""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return CLASS_TYPE_KEYS.get(value, "")
    if not isinstance(value, str):
        return ""
    return CLASS_ALIASES.get(value.strip().casefold(), "")


# ── 位置（仓库 / 角色）─────────────────────────────────────────────────────

LOCATION_ALIASES: dict[str, str] = {
    "": "",
    "all": "",
    "全部": "",
    "vault": "vault",
    "仓库": "vault",
    **CLASS_ALIASES,
}

LOCATION_LABELS_ZH: dict[str, str] = {
    "vault": "仓库",
    "postmaster": "邮政官",
    **CLASS_LABELS_ZH,
}

# ── 物品大类 ─────────────────────────────────────────────────────────────

ITEM_TYPE_ALIASES: dict[str, str] = {
    "": "",
    "all": "",
    "全部": "",
    "weapon": "weapon",
    "weapons": "weapon",
    "武器": "weapon",
    "armor": "armor",
    "armors": "armor",
    "护甲": "armor",
}

# ── 元素 ────────────────────────────────────────────────────────────────

ELEMENT_LABELS_ZH: dict[str, str] = {
    "void": "虚空",
    "solar": "烈日",
    "arc": "电弧",
    "stasis": "冰影",
    "strand": "缚丝",
    "prism": "棱镜",
}

# 中文以**游戏客户端**的叫法为准：strand=缚丝（「编织」是早期写法，保留兼容）。
# 权威来源是 `manifest_names` 的伤害类型名称表（那边也是缚丝），这里只是输入别名。
#
# 后面三个（火/电/冰）是玩家口语的**元素词根**：中文玩家把元素和职业拼着说——"火术/电猎/冰泰坦"。
# 只收词根，职业尾缀交给 `subclass_element_key` 去拆：6 个元素 × 3 个职业写成 18 条表既长又漏
# （"火术士"这种写法就会漏掉）。
ELEMENT_ALIASES: dict[str, str] = {
    "void": "void",
    "虚空": "void",
    "solar": "solar",
    "烈日": "solar",
    "火": "solar",
    "arc": "arc",
    "电弧": "arc",
    "电": "arc",
    "stasis": "stasis",
    "冰影": "stasis",
    "冰": "stasis",
    "strand": "strand",
    "缚丝": "strand",
    "编织": "strand",
    "prism": "prism",
    "prismatic": "prism",
    "棱镜": "prism",
}

# 子职业叫法里的**职业尾缀**：中文玩家按职业把元素念成"火术/电猎/冰泰坦"。
# 它只用来校验职业（目标角色是猎人、却说了"火术"→ 报错，不硬切），不参与元素解析，
# 所以和 ELEMENT_ALIASES 是两张表、不是一张。
SUBCLASS_CLASS_SUFFIXES: dict[str, str] = {
    "术士": "warlock",
    "术": "warlock",
    "猎人": "hunter",
    "猎": "hunter",
    "泰坦": "titan",
    "泰": "titan",
}


def subclass_element_key(value: str | None) -> tuple[str, str]:
    """把「换子职业」的输入拆成 `(元素规范键, 输入里声明的职业)`。

    只做两件事：归一化（NFKC 半角化、去空格、小写）后查元素表；查不到再拆一次职业尾缀。
    拆不出来给 `("", "")`——由调用方决定怎么报错，这里不猜、不模糊匹配、不查拼音。

    注意**官方子职业名不在这张表里**（破晓/枪手/炎阳……）：名字的权威来源是 Manifest，
    由服务去该角色的子职业物品里按 `displayProperties.name` 精确匹配。
    """
    if not isinstance(value, str):
        return "", ""
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    if not text:
        return "", ""
    key = ELEMENT_ALIASES.get(text, "")
    if key:
        return key, ""
    for suffix, klass in SUBCLASS_CLASS_SUFFIXES.items():
        if len(text) > len(suffix) and text.endswith(suffix):
            key = ELEMENT_ALIASES.get(text[: -len(suffix)], "")
            return (key, klass) if key else ("", "")
    return "", ""

# ── 稀有度（`tierType`）──────────────────────────────────────────────────
# 输入别名 → Manifest 的 `inventory.tierType`。以前只有 `inventory_service` 私藏一份，
# 于是"按稀有度筛库存"认中文、"按稀有度筛模式"没得用；现在两边共用这一张表。
# 玩家把异域叫「金枪/金装」、把传说叫「紫枪/紫装」，一并收进来。
RARITY_ALIASES: dict[str, int] = {
    "exotic": 6, "异域": 6, "金枪": 6, "金装": 6,
    "legendary": 5, "传说": 5, "紫枪": 5, "紫装": 5,
    "rare": 4, "稀有": 4,
}
RARITY_LABELS_ZH: dict[int, str] = {6: "异域", 5: "传说", 4: "稀有"}


def rarity_key(value: str | None) -> int | None:
    """稀有度输入 → `tierType`；认不出来给 None（由调用方决定怎么报错，不猜）。"""
    if not isinstance(value, str):
        return None
    key = unicodedata.normalize("NFKC", value).strip().casefold()
    if key in ("", "all", "全部"):
        return None
    return RARITY_ALIASES.get(key)
