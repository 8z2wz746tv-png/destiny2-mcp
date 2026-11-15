"""安装脚本与宿主无关：三种落点都要能用，而且不能只认某一个平台。

这里不测「装到哪台机器上」（那是环境），测三件会腐烂的事：

1. 指针块幂等 —— 重复写入不会堆积，也不会吃掉文件里原有的内容；
2. 镜像同步正确 —— 更新、删除多余文件、`--dry-run` 不落盘；
3. 脚本和服务器说的是同一个线上地址（否则「不装也能用」的那条路会指到 404）。
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from destiny_mcp import config

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "install_skill.py"
SKILL_ROOT = ROOT / "skills" / "destiny2-mcp"


def _installer():
    spec = importlib.util.spec_from_file_location("install_skill", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 要靠 cls.__module__ 找回模块
    spec.loader.exec_module(module)
    return module


installer = _installer()


def test_installer_and_server_advertise_the_same_guide() -> None:
    """MCP instructions 里的地址和安装脚本写的地址必须一致。

    这两处分别面向「能联网的客户端」和「只有指令文件的宿主」，指错任何一个都会
    变成一条死路，而且没人会立刻发现。
    """
    assert installer._repo_url() == config.REPO_URL
    assert f"{config.REPO_URL}/blob/main/{installer.GUIDE_PATH}" == config.ROUTING_GUIDE_URL


def test_guide_url_points_at_a_file_that_exists_here() -> None:
    """线上地址对应的文件必须真的在仓库里，否则线上那份是 404。"""
    relative = installer.GUIDE_PATH.split(f"skills/{installer.SKILL_NAME}/", 1)[1]
    assert (SKILL_ROOT / relative).is_file()


def test_pointer_block_is_idempotent() -> None:
    block = installer.pointer_block(Path("/tmp/skills/destiny2-mcp"))
    once = installer.merge_pointer("", block)
    twice = installer.merge_pointer(once, block)

    assert once == twice
    assert once.count(installer.BEGIN) == 1


def test_pointer_block_replaces_an_old_version_and_keeps_the_rest() -> None:
    """别人文件里的内容不能被吃掉；旧版本的指针块要被换掉而不是追加。"""
    old = f"{installer.BEGIN}\n旧内容\n{installer.END}"
    text = f"# 我的规则\n\n{old}\n\n## 其它无关内容\n"
    merged = installer.merge_pointer(text, installer.pointer_block())

    assert merged.startswith("# 我的规则")
    assert "其它无关内容" in merged
    assert "旧内容" not in merged
    assert merged.count(installer.BEGIN) == 1


def test_pointer_block_names_both_ways_to_read_the_guide() -> None:
    """指针要同时给本地副本和线上地址：宿主可能拿不到其中任何一个。"""
    block = installer.pointer_block(Path("/tmp/skills/destiny2-mcp"))

    assert "/tmp/skills/destiny2-mcp/SKILL.md" in block
    assert config.ROUTING_GUIDE_URL in block
    assert "ignored_parameter" in block  # 最容易踩的一条行为约定


def test_sync_mirrors_updates_and_removals(tmp_path: Path) -> None:
    target = tmp_path / "skills" / installer.SKILL_NAME

    written, removed = installer.sync(SKILL_ROOT, target, dry_run=False)
    assert {path.name for path in written} >= {"SKILL.md"}
    assert removed == []
    assert (target / "SKILL.md").is_file()
    assert (target / "references" / "routing.md").is_file()

    # 再跑一次：没有变化
    written, removed = installer.sync(SKILL_ROOT, target, dry_run=False)
    assert (written, removed) == ([], [])

    # 源头没有的文件要被清掉
    stale = target / "references" / "已经删掉的文档.md"
    stale.write_text("旧内容", encoding="utf-8")
    written, removed = installer.sync(SKILL_ROOT, target, dry_run=False)
    assert removed == [Path("references/已经删掉的文档.md")]
    assert not stale.exists()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "skills" / installer.SKILL_NAME

    written, _removed = installer.sync(SKILL_ROOT, target, dry_run=True)

    assert written
    assert not target.exists()


def test_pointer_points_at_the_hosts_own_copy() -> None:
    """每个宿主的指针要指它自己那份副本，不能一律指到第一个装过的宿主。"""
    codex = Path("/home/u/.codex/skills/destiny2-mcp")
    claude = Path("/home/u/.claude/skills/destiny2-mcp")
    installed = [codex, claude]

    assert installer._installed_for(Path("/home/u/.claude/CLAUDE.md"), installed) == claude
    assert installer._installed_for(Path("/home/u/.codex/AGENTS.md"), installed) == codex
    # 不在任何已知宿主下的规则文件（--pointer ~/AGENTS.md）：用第一份
    assert installer._installed_for(Path("/home/u/AGENTS.md"), installed) == codex
    # 一份都没装：只给线上地址
    assert installer._installed_for(Path("/home/u/AGENTS.md"), []) is None


def test_known_hosts_never_target_vendor_managed_directories() -> None:
    """宿主自己管理的目录不能写：Cursor 的 skills 由 Cursor 自己维护。"""
    for host in installer._hosts():
        if host.skills is None:
            continue
        assert "skills-cursor" not in str(host.root / host.skills)
        assert (host.root / host.skills).parent == host.root


def test_skill_folder_is_host_neutral() -> None:
    """SKILL.md 是跨宿主的那一份：frontmatter 只有通用字段，不假设某个平台。"""
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    header = text.split("---", 2)[1]

    assert "name: destiny2-mcp" in header
    assert "description:" in header
    for platform_specific in ("allowed-tools", "openai", "codex"):
        assert platform_specific not in header.lower()


@pytest.mark.parametrize("name", ["SKILL.md", "references/routing.md"])
def test_installed_payload_has_no_absolute_paths(name: str) -> None:
    """文档里不能出现本机绝对路径：它要能装到任何机器、也能直接在线上读。"""
    text = (SKILL_ROOT / name).read_text(encoding="utf-8")

    assert str(Path.home()) not in text
    assert "/Users/" not in text


def test_dsh_is_a_known_host_with_the_documented_roots() -> None:
    """DeepSeek Harness 的技能根是 `~/.dsh/skills`（DSH 文档里的 user-dsh 行）。"""
    dsh = [host for host in installer._hosts() if host.name == "dsh"]
    assert dsh, "install_skill 不认识 dsh 宿主"
    host = dsh[0]
    assert host.skills == "skills"
    assert host.root.name == ".dsh"
    assert host.mcp == "dsh-patch"


def test_running_host_is_detected_from_the_environment(monkeypatch) -> None:
    """默认装到"正在说话的那个宿主"，靠环境变量认出来。"""
    for marker in ("DSH_HOME", "DSH_SHELL", "DSH_SESSION_ID",
                   "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                   "CODEX_HOME", "CODEX_SANDBOX"):
        monkeypatch.delenv(marker, raising=False)

    assert installer._running_host() is None

    monkeypatch.setenv("DSH_HOME", "/tmp/dsh-home")
    assert installer._running_host().name == "dsh"

    monkeypatch.delenv("DSH_HOME")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert installer._running_host().name == "claude"


def test_select_hosts_prefers_the_running_host(monkeypatch, tmp_path) -> None:
    """没有 --all / --host 时不能把 Codex、Claude 都铺一遍。"""
    monkeypatch.setenv("DSH_HOME", "/tmp/dsh-home")
    selected = installer._select_hosts(explicit=None, install_all=False)
    assert [host.name for host in selected] == ["dsh"]

    monkeypatch.setenv("CLAUDECODE", "1")
    # DSH 会话里会同时设好几个标记，只删 DSH_HOME 不够
    for marker in ("DSH_HOME", "DSH_SHELL", "DSH_SESSION_ID"):
        monkeypatch.delenv(marker, raising=False)
    assert [host.name for host in installer._select_hosts(explicit=None, install_all=False)] == ["claude"]

    # 显式指定优先于"当前宿主"
    assert [host.name for host in installer._select_hosts(explicit="codex", install_all=False)] == ["codex"]

    # --all 是"存在的都装"：宿主落点取 `Path.home()/.dsh` 这些目录，**目录不存在就不装**
    # （不替用户凭空创建 Codex/Claude 目录）。所以这里把 HOME 指到临时目录自己造一个，
    # 否则这条测试就变成"只有装过 DSH 的机器才能过"——CI 上就是这么红的。
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows 上 Path.home() 看这个
    assert installer._select_hosts(explicit=None, install_all=True) == []

    (tmp_path / ".dsh").mkdir()
    every = [host.name for host in installer._select_hosts(explicit=None, install_all=True)]
    assert every == ["dsh"]


def test_unknown_host_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("DSH_HOME", "/tmp/dsh-home")
    with pytest.raises(SystemExit, match="未知宿主"):
        installer._select_hosts(explicit="not-a-host", install_all=False)


def test_dsh_patch_block_is_marked_and_complete(tmp_path: Path) -> None:
    block = installer.dsh_patch_block(tmp_path)

    assert installer.DSH_PATCH_BEGIN in block and installer.DSH_PATCH_END in block
    assert "serverName: destiny" in block
    assert f"command: {tmp_path.resolve() / '.venv' / 'bin' / 'destiny-mcp'}" in block
    assert f"DESTINY_MCP_ROOT: {tmp_path.resolve()}" in block
    assert "toolCallTimeoutMs: 300000" in block

    import yaml

    parsed = yaml.safe_load(block.split("\n", 3)[3] if False else "\n".join(
        line for line in block.splitlines()
        if not line.startswith("# destiny2-mcp:mcp-begin") and not line.startswith("# destiny2-mcp:mcp-end")
    ))
    assert parsed[0]["insert"][0]["id"] == "mcp-destiny"


def test_dsh_patch_block_carries_a_reconnect_mark(tmp_path: Path) -> None:
    """重连标记必须在块里：它是「服务器进程被换掉后让 DSH 重连」的唯一触发点。

    踩过的坑：安装器**整块重写**这一段，却不写这一行 —— 本机 profile 里那行是手写的，
    于是照文档跑一次 `--mcp` 就会把它删掉，重连机制永久失效，而且没人会发现。
    """
    import yaml

    block = installer.dsh_patch_block(tmp_path)
    assert "DESTINY_MCP_RECONNECT_MARK" in block

    parsed = yaml.safe_load("\n".join(
        line for line in block.splitlines()
        if not line.startswith("# destiny2-mcp:mcp")
    ))
    env = parsed[0]["insert"][0]["config"]["env"]
    assert env["DESTINY_MCP_RECONNECT_MARK"] == "unknown", "非 git 目录给固定值，幂等才成立"


def test_reconnect_mark_follows_the_code_revision(tmp_path: Path) -> None:
    """标记跟着代码版本走：同一状态稳定（幂等），新提交或工作区变脏就变（于是自动重连）。"""
    if shutil.which("git") is None:
        pytest.skip("这台机器没有 git")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=repo, check=True,
    )

    clean = installer._reconnect_mark(repo)
    assert clean != "unknown"
    assert installer._reconnect_mark(repo) == clean, "同一状态必须稳定"

    (repo / "f.txt").write_text("x", encoding="utf-8")
    assert installer._reconnect_mark(repo) == f"{clean}-dirty"


def test_dsh_mcp_registration_is_idempotent(tmp_path: Path, monkeypatch, capsys) -> None:
    """重复运行只能有一条 mcp-destiny；两条会让后一条加载失败。"""
    profile = tmp_path / "profiles" / "web"
    profile.mkdir(parents=True)
    patch = profile / "cordis.patch.yml"
    patch.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(installer, "_dsh_profile_dir", lambda: profile)

    assert installer.install_dsh_mcp(tmp_path / "repo", dry_run=False) is True
    first = patch.read_text(encoding="utf-8")
    assert first.count("mcp-destiny") == 1

    installer.install_dsh_mcp(tmp_path / "repo", dry_run=False)
    second = patch.read_text(encoding="utf-8")
    assert second == first
    assert second.count("mcp-destiny") == 1
    assert "已是最新" in capsys.readouterr().out

    # dry-run 一个字节都不写
    installer.install_dsh_mcp(tmp_path / "repo", dry_run=True)
    assert patch.read_text(encoding="utf-8") == first


def test_other_hosts_get_a_paste_ready_command(tmp_path: Path) -> None:
    """不方便替用户改配置的宿主：给一条能直接粘的命令，而不是静默写它的配置。"""
    hosts = {host.name: host for host in installer._hosts()}
    claude = installer.mcp_registration_hint(hosts["claude"], tmp_path)
    codex = installer.mcp_registration_hint(hosts["codex"], tmp_path)

    assert claude.startswith("claude mcp add destiny")
    assert codex.startswith("codex mcp add destiny")
    for hint in (claude, codex):
        assert str(tmp_path.resolve()) in hint
        assert "destiny-mcp" in hint
