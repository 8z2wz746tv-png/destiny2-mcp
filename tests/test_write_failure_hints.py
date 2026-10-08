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
    from destiny_mcp.tools._write_failure_hints import _WRITE_FAILURE_HINTS, write_failure_hints

    assert _WRITE_FAILURE_HINTS, "这张表不该是空的"
    for entry in _WRITE_FAILURE_HINTS:
        assert len(entry) == 2, f"这条不是 (关键词, 话术)：{entry!r}"
        keywords, hint = entry
        assert keywords and all(isinstance(k, str) and k for k in keywords), entry
        assert isinstance(hint, str) and hint.strip(), entry
    assert write_failure_hints({}) == []
    assert write_failure_hints({"message": "DestinyNoRoomInDestination"})
