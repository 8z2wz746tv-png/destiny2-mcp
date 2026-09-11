"""安装脚本与宿主无关：三种落点都要能用，而且不能只认某一个平台。

这里不测「装到哪台机器上」（那是环境），测三件会腐烂的事：

1. 指针块幂等 —— 重复写入不会堆积，也不会吃掉文件里原有的内容；
2. 镜像同步正确 —— 更新、删除多余文件、`--dry-run` 不落盘；
3. 脚本和服务器说的是同一个线上地址（否则「不装也能用」的那条路会指到 404）。
"""

from __future__ import annotations

import importlib.util
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
