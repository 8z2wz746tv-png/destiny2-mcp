"""写入失败的下一步话术：形状守门。

为什么单独一条：这张表是 `(关键词元组, 话术)` 的元组列表，合错行时**全量测试可能全绿**，
只有真机调用才会炸（2026-10-06 现场）。
"""

from __future__ import annotations
def test_the_failure_hint_table_keeps_its_shape() -> None:
    """每条必须是 `(关键词元组, 话术)` 两元组 —— 少了这层形状检查，合错行的表要到运行时才炸。

    2026-10-06 现场：一次编辑把两条合成了一条四元组，**全量测试全绿**，只有真机调用到
    `write_failure_hints` 才会 `ValueError: too many values to unpack`。
    """
    from destiny_mcp.tools._failure_hints import _WRITE_FAILURE_HINTS, write_failure_hints

    assert _WRITE_FAILURE_HINTS, "这张表不该是空的"
    for entry in _WRITE_FAILURE_HINTS:
        assert len(entry) == 2, f"这条不是 (关键词, 话术)：{entry!r}"
        keywords, hint = entry
        assert keywords and all(isinstance(k, str) and k for k in keywords), entry
        assert isinstance(hint, str) and hint.strip(), entry
    assert write_failure_hints({}) == []
    assert write_failure_hints({"message": "DestinyNoRoomInDestination"})


def test_a_location_error_is_translated_even_when_it_only_lives_in_steps() -> None:
    """D2 回归（2026-10-18 真机）：`equip_build` 的 1671 只出现在 `steps[].detail` 里。

    顶层 message 是笼统一句 `配装…执行失败`，`equipStatus=1671` 藏在 `equip_many.detail`。
    只看 code+message 时，按上游原文关键词写的话术**全部等于死代码** —— 调用方只能自己
    去外部源捞枚举（报告里那段 404/WebSearch 的成本就是这么来的）。
    """
    from destiny_mcp.tools._failure_hints import write_failure_hints

    payload = {
        "code": "build_equip_failed",
        "message": "配装 '已确认的精确配装' 执行失败，且自动恢复不完整；请检查角色装备。",
        "steps": [
            {"action": "transfer", "detail": "转移 '至高狂徒面具' → hunter", "success": True},
            {"action": "equip_many",
             "detail": "批量装备失败：…至高狂徒面具 (equipStatus=1671)；…请刷新装备确认状态。",
             "success": False},
        ],
    }
    hints = write_failure_hints(payload)
    assert hints, "1671 必须能被翻译成一句人话"
    assert any("回轨道" in h for h in hints), hints


def test_platform_codes_keep_their_distinct_meanings() -> None:
    """1670/1671/1672/1673 语义不同，不许合并成一句「稍后重试」。"""
    from destiny_mcp.tools._failure_hints import write_failure_hints

    def hint_for(code: str) -> str:
        return " ".join(write_failure_hints({"message": code}))

    assert "回轨道" in hint_for("1671")
    assert "限流" in hint_for("1672")
    assert "旧平台" in hint_for("1670")
    assert "副作用" in hint_for("1673")
