"""防止上帝类重新长回来。

这些模块已经是各自领域里最大的一个，继续往里加就是原来那种堆积。
这里的数字是「当前长度」，即上限：拆分后要跟着调低，确需放宽时改这里的数字，
改动本身会在 diff 里显式暴露出来，不会被顺手带过。

判断标准不是行数本身，而是新增代码该不该落在这个文件里。行数只是让这件事
变成一次有意识的决定。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]

CEILINGS = {
    "destiny_mcp/manifest.py":      816,
    "destiny_mcp/tools/assistants.py": 1388,
    "destiny_mcp/build/farm_target.py": 1296,
    "destiny_mcp/bungie_client.py": 1219,
    "destiny_mcp/services/build_service.py": 1114,
    "destiny_mcp/services/loadout_equipment_service.py": 794,
    "destiny_mcp/services/starside_service.py": 769,
}


def test_god_modules_do_not_grow() -> None:
    oversized = {}
    for relative, ceiling in CEILINGS.items():
        path = ROOT / relative
        assert path.is_file(), f"{relative} 不存在了；清单需要同步更新"
        size = len(path.read_text(encoding="utf-8").splitlines())
        if size > ceiling:
            oversized[relative] = f"{size} > {ceiling}"

    assert not oversized, (
        "这些模块超过了上限，先把新代码放到别处或拆分已有代码；"
        f"确有必要再显式提高上限：{oversized}"
    )


def test_ceiling_list_stays_meaningful() -> None:
    """上限不能留得离实际太远，否则这道闸就失效了。"""
    slack = {
        relative: ceiling - len((ROOT / relative).read_text(encoding="utf-8").splitlines())
        for relative, ceiling in CEILINGS.items()
    }

    assert all(value >= 0 for value in slack.values())
    assert all(value <= 40 for value in slack.values()), slack
