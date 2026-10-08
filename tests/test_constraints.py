"""`build/constraints.parse` 的报错话术守门。

（2026-10-06 建：真机那次"套装名对不上"只回一句"请使用正式名称"，用户只能自己猜。）
"""

from __future__ import annotations

import pytest

def test_an_unknown_set_name_error_tells_you_what_to_try() -> None:
    """套装名对不上时，报错要给候选 / 点破"这可能是副本名"。

    2026-10-06 真机：求解只回一句「无法确认指定套装加成“玻璃拱顶”。请先查询可用套装并使用正式名称。」
    —— 用户只能自己猜。而那次他写的是**副本名**（套装名是「埃希恩记忆」），两串字符零重叠，
    模糊匹配也找不到，所以两种情况都要说清。
    """
    from destiny_mcp.build.constraints import parse
    from destiny_mcp.build.models import BuildRequest
    from destiny_mcp.exceptions import BuildValidationError

    class _Manifest:
        def search(self, *args, **kwargs):
            return []

        def search_set_bonus(self, name: str):
            return None

        def get_all_set_bonuses(self) -> dict:
            return {
                1: {"set_name": "埃希恩记忆", "perks": []},
                2: {"set_name": "移民号陨落", "perks": []},
            }

    request = BuildRequest(
        character_class="hunter", set_bonus_name="国王的陨落", set_bonus_count=4,
    )

    with pytest.raises(BuildValidationError) as excinfo:
        parse(request, _Manifest())

    message = str(excinfo.value)
    assert "无法确认指定套装加成“国王的陨落”" in message
    assert "移民号陨落" in message, f"要点名候选（字符有重叠时），实际：{message}"

    # 零重叠的那种（副本名）要另说清 —— 不能再让调用方去猜
    request = BuildRequest(
        character_class="hunter", set_bonus_name="玻璃拱顶", set_bonus_count=4,
    )
    with pytest.raises(BuildValidationError) as excinfo:
        parse(request, _Manifest())
    assert "活动/副本名" in str(excinfo.value)
