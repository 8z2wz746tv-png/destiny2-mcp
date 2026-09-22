"""护甲词条重建的真机金标准：**大师加成是"档位"，不是"有/无"**。

这条是 2026-09-22 口径审计挖出来的（`scripts/audit_armor_model.py` 跑全账号 468 件）：

- 大师插槽那颗「升级护甲」在 Manifest 里**声明"六维各 +N"**，N = 档位 1–5；
- 真机规则是：**只给"非词条那三项"各 +N**（和 `平衡调整` 一样，声明值 ≠ 生效范围）；
- 我们以前的模型写死 `gear_tier`（=5）当加成，于是"只升到 1/3/4 档"的件永远反推不出来
  （真机上一大批件就是这样，`roll_parse_error` 一直挂着）。

下面三件是真机上查过的证人；每条的期望值都能在账号里逐项核对。
端到端那条（468 件全部对得上）由审计脚本负责，这里只钉住**规则本身**。
"""

from __future__ import annotations

from destiny_mcp.build.armor_rules import BALANCED_TUNING_HASH
from destiny_mcp.build.models import _matching_armor3_roll

#: 真机的三个词条原型 hash（`armor_rules` 里都有）。
SKIRMISHER = 1687144140   # 突击手：近战30 / 武器25 / 职业20
GRENADIER = 2937665788    # 掷雷手：手雷30 / 超能25 / 职业20
PARAGON = 4227065942      # 楷模典范：超能30 / 近战25 / 武器20
BALANCED = BALANCED_TUNING_HASH  # 平衡调整（`armor_rules` 里是有符号形式，比较要按无符号）
CLASS_UP_HEALTH_DOWN = 4030660414  # +职业 / -生命值


def test_tier_three_piece_needs_a_plus_three_bonus() -> None:
    """降临回音臂铠 `6917530197749539530`（楷模典范，**3 档**）。

    词条槽 = 超能30 / 近战25 / 武器20；304 = 武器20 生命3 职业3 手雷3 超能30 近战25。
    "非词条那三项"（武器/生命/职业 之外的 生命/职业/手雷）各是 **3** —— 插件声明的就是 3。
    """
    actual_304_minus_mods = (20, 3, 3, 3, 25, 30)  # STAT_NAMES 顺序：武器 生命 职业 手雷 近战 超能

    matched = _matching_armor3_roll(
        dict(zip(("weapons", "health", "class_stat", "grenade", "melee", "super_stat"),
                 actual_304_minus_mods)),
        gear_tier=5, archetype_hash=PARAGON, tuning_hash=None, masterwork_bonus=3,
    )
    assert matched is not None, "3 档就该按 +3 反推"

    wrong = _matching_armor3_roll(
        dict(zip(("weapons", "health", "class_stat", "grenade", "melee", "super_stat"),
                 actual_304_minus_mods)),
        gear_tier=5, archetype_hash=PARAGON, tuning_hash=None, masterwork_bonus=5,
    )
    assert wrong is None, "拿满大师的 +5 去套 3 档的件，必须对不上（这就是修掉的那个 bug）"


def test_tier_five_piece_matches_with_the_full_bonus() -> None:
    """至高碎片 `6917530188462629544`（掷雷手，**5 档**，装着平衡调整）。

    词条槽 = 手雷30 / 超能25 / 职业20；304 − 模组 = 武器6 生命6 职业20 手雷30 近战6 超能25
    （武器/生命/近战 = 0 + 5 大师 + 1 平衡调整）。
    """
    actual = (6, 6, 20, 30, 6, 25)
    matched = _matching_armor3_roll(
        dict(zip(("weapons", "health", "class_stat", "grenade", "melee", "super_stat"), actual)),
        gear_tier=5, archetype_hash=GRENADIER, tuning_hash=BALANCED, masterwork_bonus=5,
    )
    assert matched is not None
    template, option = matched
    assert option is not None
    assert (option.plug_hash & 0xFFFFFFFF) == (BALANCED & 0xFFFFFFFF)


def test_tier_zero_piece_has_no_masterwork_bonus() -> None:
    """光芒领主手套 `6917530188462631525`（突击手，大师插槽只有能量标记 = **0 档**）。

    词条槽 = 近战30 / 武器25 / 职业20；装着「+职业 / -生命值」；
    304 − 模组 = 武器25 生命−5 职业25 手雷0 超能0 近战30 —— 非词条三项是 **0**（不是 5）。
    """
    actual = (25, -5, 25, 0, 30, 0)
    fields = ("weapons", "health", "class_stat", "grenade", "melee", "super_stat")
    matched = _matching_armor3_roll(
        dict(zip(fields, actual)),
        gear_tier=5, archetype_hash=SKIRMISHER,
        tuning_hash=CLASS_UP_HEALTH_DOWN, masterwork_bonus=0,
    )
    assert matched is not None, "0 档就按 0 反推"

    inflated = _matching_armor3_roll(
        dict(zip(fields, actual)),
        gear_tier=5, archetype_hash=SKIRMISHER,
        tuning_hash=CLASS_UP_HEALTH_DOWN, masterwork_bonus=5,
    )
    assert inflated is None, "0 档的件按 +5 去套必须对不上"
