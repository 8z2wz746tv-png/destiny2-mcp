"""`scripts/benchmark_equip_chain.py` 的**离线冒烟**：能 import、参数面能解析、
**不带 `--write` 时一个写接口都不调**。

为什么只有这一条（而不是一堆单测）：这是真机 benchmark，验收标准是"跑一次看表"，
单测堆多了只会挡住改表。但"零写入"这条**只能**靠离线跑一遍来证明 ——
它是账号安全的底线，靠人眼读代码以前已经错过一次（`equip_build` 的 `confirmed=True`
藏在分支深处）。

跑法：真跑一遍脚本，但把四个工具入口换成 `--offline` 替身（`OfflineStub`），
**零网络**；真跑的那一半（`--write` + 替身）留给人工验收。
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_equip_chain.py"


def _load_module():
    """按路径加载脚本，并**注册进 `sys.modules`**：脚本里有 dataclass，而 dataclass 的
    字符串注解要能反查模块（不注册会 `AttributeError: 'NoneType' object has no attribute
    '__dict__'`，看着像脚本坏了，其实是加载方式的问题）。"""
    name = "benchmark_equip_chain"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_script_parses_and_imports() -> None:
    """先保证它**能被 import**：今天那批重构（`_verify_loadout` 搬成模块函数）就是
    让这个脚本连 import 都过不去，而它不在 pytest 收集范围里，坏了没人知道。"""
    module = _load_module()
    assert callable(module.main)
    ast.parse(SCRIPT.read_text(encoding="utf-8"))


def test_parser_exposes_the_documented_parameters() -> None:
    module = _load_module()
    args = module.build_parser().parse_args([])
    # 角色/金装/六维/碎片/功能模组都从命令行来，且**默认值是一套能跑的示例**。
    assert args.character == "warlock"
    assert args.exotic_name == "黎明副歌"
    assert args.weapons_target == 130
    assert args.class_target == 70  # `--class-stat-target` 的 dest 必须落在 class_target
    assert args.health_target is None  # 没给默认值 = 不设该目标
    assert args.write is False
    targets = module.targets_from_args(args)
    assert targets["character"] == "warlock"
    assert targets["fragment_names"]  # 默认示例那 5 片
    assert targets["functional_mods"]
    assert all(":" in entry for entry in targets["functional_mods"])


def test_overrides_reach_the_solver_arguments() -> None:
    module = _load_module()
    args = module.build_parser().parse_args(
        [
            "--character", "hunter",
            "--exotic-name", "快速装弹松身裤",
            "--weapons-target", "150",
            "--health-target", "0",  # 0 = 不设该目标（项目统一约定）
            "--fragment", "保护琢面",
            "--functional-mod", "helmet:谐振虹吸",
        ]
    )
    targets = module.targets_from_args(args)
    assert targets["character"] == "hunter"
    assert targets["exotic_name"] == "快速装弹松身裤"
    assert targets["weapons_target"] == 150
    assert "health_target" not in targets  # 0 等于没指定，别把 0 当硬约束发出去
    assert targets["fragment_names"] == ["保护琢面"]
    assert targets["functional_mods"] == ["helmet:谐振虹吸"]


def test_functional_mod_without_slot_is_refused() -> None:
    """认不出部位的模组**不许猜** —— 猜错就是把一颗模组装到错的部位上。"""
    module = _load_module()
    with pytest.raises(SystemExit):
        module.parse_functional_mods(["谐振虹吸"])


def test_is_write_call_spares_the_read_only_preview() -> None:
    """`equip_build` 的 `confirmed=False` 是**只读预览**，不能和真写一起拦。"""
    module = _load_module()
    assert module._is_write_call("build_assistant", "equip_build", False) is False
    assert module._is_write_call("build_assistant", "equip_build", True) is True
    assert module._is_write_call("loadout_assistant", "save", False) is True
    assert module._is_write_call("inventory_assistant", "move", False) is True
    assert module._is_write_call("build_assistant", "find", False) is False


def _run_offline(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    # `PYTHONDONTWRITEBYTECODE=1`：`__pycache__` 的失效判据是 (mtime, 大小)，
    # 等长替换会被判"没过期"（AGENTS.md 那条），真机脚本的冒烟不值得冒这个险。
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--offline", *extra],
        capture_output=True, text=True, cwd=tmp_path, timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_read_only_run_calls_no_write_interface(tmp_path: Path) -> None:
    """不带 `--write`：只跑 find + 确认回显，写接口一个都不许碰。"""
    out = tmp_path / "segments.json"
    proc = _run_offline(tmp_path, "--out", str(out))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "零写入" in proc.stdout
    assert "账号一个字节都没动" in proc.stdout
    assert "ReadOnlyViolation" not in proc.stdout
    assert out.exists()
    # 五张表的分节标题都在（表空是正常的：离线替身不打网络，段自然为空）
    for title in ("【表 1】", "【表 2】", "【表 3】", "【表 4】", "【表 5】"):
        assert title in proc.stdout


def test_the_guard_actually_bites() -> None:
    """注入一次违规，确认"零写入"这条守门真会红 —— 不注入就不知道它咬不咬人。"""
    module = _load_module()

    class Holder:
        async def build_assistant(self, **kwargs: object) -> dict:
            return {"ok": True}

    tool = Holder()
    module._install_write_guard(tool)
    # 合法的那条（只读预览）必须还过得去，否则守住的是"确认回显"这条正常路径。
    assert asyncio.run(tool.build_assistant(intent="equip_build", confirmed=False)) == {"ok": True}
    with pytest.raises(module.ReadOnlyViolation):
        asyncio.run(tool.build_assistant(intent="equip_build", confirmed=True))


# ── 写段（下面的三条守的都是 2026-10-04 真机踩出来的那一类） ───────────────────


_TOOL_ENTRIES = {"build_assistant", "loadout_assistant", "inventory_assistant", "subclass_assistant"}
#: 脚本自己那三个"要往下传 ctx"的辅助函数 —— 它们**没有**默认值，漏传就是 TypeError。
_CTX_HELPERS = {"save_snapshot", "restore_snapshot", "socket_diff_rows"}


def test_every_tool_call_in_the_script_passes_ctx() -> None:
    """脚本里**每一次**工具调用、以及三个辅助函数的**每一次调用**，都必须带 `ctx`。

    真机 2026-10-04 崩的就是这条：`find`/`equip_build` 带了 `ctx=ctx`，写段那三处
    （存快照 / 还原 / 逐槽 diff）没带，活体工具第一行 `get_ctx(ctx)` 就
    `'NoneType' object has no attribute 'request_context'`。**崩的偏偏是安全网**，
    而它藏在 `whether --write` 分支深处，只读路径永远跑不到。

    两头都要守：工具调用漏传（辅助函数内部），和辅助函数调用漏传（`run()` 里那三行）。
    注入验证时确认过——只守工具调用那一头，`run()` 漏传是抓不到的。

    结构检查而不是行为检查：这三处分别在不同的分支/`finally` 里，靠离线替身只能覆盖
    其中一部分（而且替身自己也要收严，见 `OfflineStub`）；漏一处就够了。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target = ""
        if isinstance(func, ast.Attribute) and func.attr in _TOOL_ENTRIES:
            target = ast.unparse(func)
        elif isinstance(func, ast.Name) and func.id in _CTX_HELPERS:
            target = func.id
        if not target:
            continue
        if not any(keyword.arg == "ctx" for keyword in node.keywords):
            offenders.append(f"    第 {node.lineno} 行：{target}(…)")
    assert not offenders, (
        "写段有调用漏传 ctx（活体工具第一行就崩）：\n" + "\n".join(offenders)
    )


def test_save_snapshot_reads_loadout_id_from_the_real_receipt_path() -> None:
    """`loadout_id` 在 `data.result.loadout_id`；读成 `data.loadout_id` 会静默拿到空串。

    后果不是"少打印一行"：脚本随即判"没拿到还原点"而**拒绝写入** —— 拒写是对的，
    但理由是假的（快照明明存成了）。真机 2026-10-04 就这么白跑一趟 + 多留一套孤儿快照。
    """
    module = _load_module()

    class Tools:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        async def loadout_assistant(self, *, ctx: object, **kwargs: object) -> dict:
            self.kwargs = kwargs
            # 形状照真机回执：`loadout_id` 在 `result` 里。
            return {"ok": True, "data": {"result": {"success": True, "loadout_id": "abc-123"}}}

    tools = Tools()
    snapshot = asyncio.run(
        module.save_snapshot(tools, ctx=object(), player="p", character="warlock")
    )
    assert snapshot["loadout_id"] == "abc-123"
    assert snapshot["ok"] is True
    assert tools.kwargs["intent"] == "save"
    assert tools.kwargs["confirmed"] is True


def test_socket_diff_judges_only_the_sockets_the_snapshot_records() -> None:
    """逐槽 diff 只比**快照记过的槽**，不许把着色器/大师杰作/皮肤报成差异。

    `read_armor_mod_sockets` 收的是"可写模组槽"（通用/部位/调谐），`intent="mods"` 读回
    却把着色器/大师杰作/原型/词条/皮肤一起列出来 —— 按并集比就是**每次都报一遍假红**。
    真机 2026-10-04：五件全 ✗，而账号逐件等于快照。假红比不报更糟：它会教人忽略这份核对。
    """
    module = _load_module()

    def _tools(mods: list[dict]) -> object:
        class Tools:
            async def inventory_assistant(self, *, ctx: object, **kwargs: object) -> dict:
                return {
                    "ok": True,
                    "data": {
                        "equipped_armor": {
                            "characters": [
                                {"character": "warlock", "items": [
                                    {"slot_key": "helmet", "name": "光芒领主面具",
                                     "item_instance_id": "42", "mods": mods},
                                ]},
                            ]
                        }
                    },
                }

        return Tools()

    # 快照记 0 / 11；现场多出 4（着色器）、5（大师杰作）、6（原型）—— 那些**不判**。
    live = [
        {"index": 0, "plug_hash": 111, "name": "职业模组"},
        {"index": 4, "plug_hash": 999, "name": "超黑"},
        {"index": 5, "plug_hash": 998, "name": "升级护甲"},
        {"index": 6, "plug_hash": 997, "name": "掷雷手"},
        {"index": 11, "plug_hash": 222, "name": "平衡调整"},
    ]
    record = module._SlotSnapshot(
        item_instance_id="42", item_hash=1, mod_sockets={0: 111, 11: 222}, slot="helmet"
    )
    rows, gaps = asyncio.run(
        module.socket_diff_rows(
            _tools(live), ctx=object(), player="p", character="warlock", before=[record]
        )
    )
    assert gaps == [], f"快照不记的槽被当成差异了：{gaps}"
    assert any("✓" in row for row in rows)
    # 但"没判什么"要如实说，不能假装全判过了。
    assert any("不判" in row for row in rows), rows

    # 真差异必须照样抓住：快照记过的槽 0 与现场对不上。
    wrong = [dict(entry, plug_hash=12345) if entry["index"] == 0 else entry for entry in live]
    _, gaps2 = asyncio.run(
        module.socket_diff_rows(
            _tools(wrong), ctx=object(), player="p", character="warlock", before=[record]
        )
    )
    assert gaps2 and "槽0" in gaps2[0], gaps2


def test_offline_write_run_wires_ctx_through_the_whole_write_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """离线跑通**整条写段的接线**：存快照 → 写入 → 自动还原 → 逐槽 diff。

    `OfflineStub` 的四个入口都要求 `ctx`（照活体签名收严），所以任何一处漏传在离线就是
    `TypeError` —— 不必再拿账号去换这条证据。`read_armor_state` 换成假读数：它走
    `owner._resolver`（真网络），而这条测试要一直保持零网络。
    """
    module = _load_module()

    async def fake_read_armor_state(owner: object, player: str, character: str, char_id: str,
                                    *, equipped_only: bool) -> dict:
        return {"char_id": "", "pieces": {}}

    monkeypatch.setattr(module, "read_armor_state", fake_read_armor_state)

    seen: list[object] = []

    class RecordingStub(module.OfflineStub):
        def __init__(self) -> None:
            super().__init__()
            seen.append(self)

    monkeypatch.setattr(module, "OfflineStub", RecordingStub)

    args = module.build_parser().parse_args(
        ["--offline", "--write", "--out", str(tmp_path / "segments.json")]
    )
    assert asyncio.run(module.run(args)) == 0
    out = capsys.readouterr().out

    stub = seen[0]
    loadout_intents = [c.get("intent") for c in stub.calls if c["tool"] == "loadout_assistant"]
    assert loadout_intents == ["save", "equip_loadout"], loadout_intents
    # `id=` 有值 = `loadout_id` 是从 `data.result` 读到的（旧路径会印成空串并拒写）。
    assert "id=offline-snapshot" in out, out[-2000:]
    assert "真写：" in out
    assert "还原 ok=True" in out
    assert "逐槽 diff" in out
    # 为腾格手工搬走的件不在对照范围内，这件事必须说出来（盲区）。
    assert "盲区" in out
    assert "ReadOnlyViolation" not in out
    assert (tmp_path / "segments.json").exists()


def test_manual_restore_hint_survives_a_raising_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """还原**自己抛异常**时，照样要打印"手动还原用哪套快照 id"。

    守的是真机 2026-10-04 那条最危险的路径：写入已经落地，挂在 `finally` 里的自动还原
    自己抛了（当时是漏传 `ctx` 的 `AttributeError`），而那句提示写在 `if not restore["ok"]`
    里面 —— 异常路径走不到，于是脚本带 traceback 退出，**"账号被改过、还原没做成、
    该怎么办"一个字都没留下**。提示必须由快照 id 存不存在来判，不能由"回执说 ok"来判。

    复现方式：把 `restore_snapshot` 换成会抛的替身（离线、零网络），跑的就是 `run()` 本身。
    """
    module = _load_module()

    async def fake_read_armor_state(owner: object, player: str, character: str, char_id: str,
                                    *, equipped_only: bool) -> dict:
        return {"char_id": "", "pieces": {}}

    async def boom(*args: object, **kwargs: object) -> dict:
        raise AttributeError("'NoneType' object has no attribute 'request_context'")

    monkeypatch.setattr(module, "read_armor_state", fake_read_armor_state)
    monkeypatch.setattr(module, "restore_snapshot", boom)

    out_path = tmp_path / "segments.json"
    args = module.build_parser().parse_args(
        ["--offline", "--write", "--out", str(out_path)]
    )
    # 还原自己抛不再等于整条链崩：`run()` 走完并如实报出来（退出码 0，故障落在报告里）。
    assert asyncio.run(module.run(args)) == 0
    out = capsys.readouterr().out

    assert "手动还原" in out, out[-2000:]
    assert "AttributeError" in out, out[-2000:]
    # 提示里必须**带 id**：没有 id 的那句提示等于没提示（人找不到那套快照）。
    assert 'loadout_id="offline-snapshot"' in out, out[-2000:]
    assert "bench-equip-snapshot" in out, out[-2000:]
    # 写入已经落地 → 那几样"账号现在什么样"的证据一样都不能少。
    assert "真写：" in out
    assert "还原 ok=False" in out
    assert "逐槽 diff" in out
    assert out_path.exists()
