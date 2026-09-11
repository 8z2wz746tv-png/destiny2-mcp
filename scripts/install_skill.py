#!/usr/bin/env python3
"""把 `skills/destiny2-mcp` 送到各个智能体宿主手里 —— 不针对某一个平台。

三层落点，对应宿主能力的三种情况：

1. **有 skills 目录的宿主**（Codex `~/.codex/skills/`、Claude Code `~/.claude/skills/`…）：
   整份镜像过去，宿主自己按需加载。
2. **只读全局指令文件的宿主**：`--pointer` 往它的全局指令文件里写一段带标记的指针，
   告诉它「用这个 MCP 之前先读哪份文档」。指针块是幂等的，重复跑不会堆积。
3. **什么都不支持的宿主**：MCP 握手里的 `instructions` 带有线上文档地址，
   能联网就能自己读；README 里也有一段可以直接粘给 Agent 的话。

仓库始终是唯一源头。这个脚本只做两件事：复制目录、写入带标记的指针块；
不改任何宿主配置，也不碰宿主自己管理的目录（例如 Cursor 的 skills-cursor）。

    .venv/bin/python scripts/install_skill.py --list
    .venv/bin/python scripts/install_skill.py --dry-run
    .venv/bin/python scripts/install_skill.py                      # 装到探测到的宿主
    .venv/bin/python scripts/install_skill.py --target ~/.claude/skills
    .venv/bin/python scripts/install_skill.py --pointer            # 写指针到探测到的宿主
    .venv/bin/python scripts/install_skill.py --pointer ~/AGENTS.md
"""

from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "skills" / "destiny2-mcp"
SKILL_NAME = "destiny2-mcp"

# 只有这些进安装包：给 Agent 看的文档 + 宿主识别用的元数据。
INCLUDE = ("SKILL.md", "references", "agents")

# 线上兜底地址。仓库是公开的，所以「读不到本地文件」的宿主可以直接抓这一份。
FALLBACK_REPO = "https://github.com/8z2wz746tv-png/destiny2-mcp"
GUIDE_PATH = f"skills/{SKILL_NAME}/references/routing.md"

BEGIN = "<!-- destiny2-mcp:begin -->"
END = "<!-- destiny2-mcp:end -->"


@dataclass(frozen=True)
class Host:
    """一个宿主的落点。加宿主就是加一行，不需要改逻辑。"""

    name: str
    root: Path
    skills: str | None = None        # 相对 root 的 skills 目录；None = 该宿主没有或不归我们管
    instructions: str | None = None  # 相对 root 的全局指令文件
    note: str = ""


def _hosts() -> tuple[Host, ...]:
    """已知宿主的落点。加宿主就是加一行 —— 这是能力差异，不是平台适配。

    只写「有公开约定、不会踩到宿主自己管理的目录」的那些。其它宿主用
    `--target` / `--pointer <file>` 显式指定，或者走 MCP instructions 里的线上地址。
    """
    home = Path.home()
    return (
        Host("codex", home / ".codex", skills="skills", instructions="AGENTS.md"),
        Host("claude", home / ".claude", skills="skills", instructions="CLAUDE.md"),
        Host(
            "cursor",
            home / ".cursor",
            note="skills 目录由 Cursor 自己管理（skills-cursor），只支持 --target / --pointer",
        ),
    )


def _repo_url() -> str:
    """从 git remote 推断线上地址，拿不到就用兜底常量。"""
    try:
        remote = subprocess.run(
            ["git", "-C", str(SOURCE), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 环境相关
        remote = ""
    match = re.match(r"(?:git@|https://)([^/:]+)[/:](.+?)(?:\.git)?$", remote)
    if not match:
        return FALLBACK_REPO
    return f"https://{match.group(1)}/{match.group(2)}"


def pointer_block(installed: Path | None = None, repo_url: str | None = None) -> str:
    """给全局指令文件用的指针块：只说到哪读，不重复文档内容。"""
    repo = repo_url or _repo_url()
    sources = []
    if installed is not None:
        sources.append(f"- 本地：`{installed / 'SKILL.md'}`，细节在 `{installed / 'references' / 'routing.md'}`")
    sources.append(f"- 线上：{repo}/blob/main/{GUIDE_PATH}")
    return "\n".join(
        [
            BEGIN,
            "## Destiny 2 MCP（本地服务器）",
            "",
            "任务涉及命运 2 的账号、背包、武器与 Perk、配装、配装槽、子职业、战绩、周常或商人时，"
            "先读路由文档再调工具：",
            *sources,
            "",
            "三条不能破的线：",
            "",
            "1. 账号数据（我有什么）、Manifest（游戏里有什么）、社区资料（别人怎么说）互不替代；",
            "2. 参数传给了当前 `intent` 不读的字段会返回 `ignored_parameter` —— 按提示换 intent，不要重试同样的调用；",
            "3. 任何写入（移动、装备、保存、修改）都要用户明确确认后才能执行。",
            END,
        ]
    )


def merge_pointer(text: str, block: str) -> str:
    """把指针块并进指令文件：已有标记就替换，没有就追加，其余内容原样保留。"""
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _old, tail = rest.split(END, 1)
        return f"{head}{block}{tail}"
    if not text.strip():
        return f"{block}\n"
    return f"{text.rstrip()}\n\n{block}\n"


def _relative_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    }


def _wanted_files() -> dict[Path, Path]:
    wanted: dict[Path, Path] = {}
    for name in INCLUDE:
        origin = SOURCE / name
        if origin.is_file():
            wanted[Path(name)] = origin
        elif origin.is_dir():
            for path in origin.rglob("*"):
                if path.is_file() and not any(part.startswith(".") for part in path.parts):
                    wanted[path.relative_to(SOURCE)] = path
    return wanted


def sync(source: Path, target: Path, *, dry_run: bool) -> tuple[list[Path], list[Path]]:
    """把源目录镜像到 target：多出来的文件删掉，相同的跳过。返回 (写入, 删除)。"""
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"源头缺少 SKILL.md：{source}")

    wanted = _wanted_files()
    existing = _relative_files(target) if target.is_dir() else set()
    written: list[Path] = []

    for relative, origin in sorted(wanted.items()):
        destination = target / relative
        if destination.is_file() and filecmp.cmp(origin, destination, shallow=False):
            continue
        written.append(relative)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)

    removed = sorted(existing - set(wanted))
    for relative in removed:
        if not dry_run:
            (target / relative).unlink()

    return written, removed


def _report(action: str, written: list[Path], removed: list[Path], dry_run: bool) -> None:
    verb = "将要" if dry_run else "已"
    print(f"{verb}{action}")
    print(f"  写入/更新 {len(written)} 个文件" + (f"：{', '.join(map(str, written))}" if written else ""))
    print(f"  删除 {len(removed)} 个多余文件" + (f"：{', '.join(map(str, removed))}" if removed else ""))
    if not written and not removed:
        print("  已经是最新的。")


def install_skills(targets: list[Path], *, dry_run: bool) -> None:
    for target in targets:
        written, removed = sync(SOURCE, target, dry_run=dry_run)
        _report(f"安装 {SKILL_NAME} -> {target}", written, removed, dry_run)


def _installed_for(pointer: Path, installed: list[Path]) -> Path | None:
    """这个指针文件所在的宿主装了哪一份，就指哪一份；否则指第一份。"""
    for candidate in installed:
        if candidate.parents and pointer.parent == candidate.parents[1]:
            return candidate
    return installed[0] if installed else None


def write_pointers(files: list[Path], installed: list[Path], *, dry_run: bool) -> None:
    for path in files:
        block = pointer_block(_installed_for(path, installed))
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        merged = merge_pointer(text, block)
        if merged == text:
            print(f"已是最新指针：{path}")
            continue
        print(f"{'将要' if dry_run else '已'}写入指针：{path}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(merged, encoding="utf-8")


def _detected_skills_targets() -> list[Path]:
    targets = []
    for host in _hosts():
        if host.skills and host.root.is_dir():
            targets.append(host.root / host.skills / SKILL_NAME)
    return targets


def _detected_pointer_files() -> list[Path]:
    return [
        host.root / host.instructions
        for host in _hosts()
        if host.instructions and host.root.is_dir()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="把 destiny2-mcp 文档送到各个智能体宿主")
    parser.add_argument("--target", type=Path, action="append", default=[], help="显式指定 skills 目录（可重复）")
    parser.add_argument(
        "--pointer",
        type=Path,
        nargs="*",
        default=None,
        help="写指针块到全局指令文件；不带路径时用探测到的宿主文件",
    )
    parser.add_argument("--list", action="store_true", help="只列出探测结果")
    parser.add_argument("--dry-run", action="store_true", help="只报告将要做什么")
    args = parser.parse_args()

    if args.list:
        print("探测到的宿主：")
        for host in _hosts():
            exists = "存在" if host.root.is_dir() else "不存在"
            skills = host.root / host.skills / SKILL_NAME if host.skills else None
            target = f"skills -> {skills}" if skills else "skills -> （不适用）"
            pointer = host.root / host.instructions if host.instructions else "（不适用）"
            print(f"  {host.name:8s} {host.root} [{exists}]  {target}；指针 -> {pointer}")
            if host.note:
                print(f"           {host.note}")
        print()
        print("其它宿主：既不放在上面的目录、也没有全局指令文件时，走两条通用路径 ——")
        print(f"  1. MCP 握手的 instructions 里有线上地址：{_repo_url()}/blob/main/{GUIDE_PATH}")
        print("  2. 用 --pointer <文件> 把指针块写进该宿主读的规则文件（例如 ~/AGENTS.md）")
        return 0

    explicit_targets = [path.expanduser() for path in args.target]
    skills_targets = explicit_targets or _detected_skills_targets()
    if skills_targets:
        install_skills(skills_targets, dry_run=args.dry_run)
    elif args.pointer is None:
        print("没有探测到支持 skills 目录的宿主；用 --target 指定，或加 --pointer 写指针。")

    if args.pointer is not None:
        pointer_files = [path.expanduser() for path in args.pointer] or _detected_pointer_files()
        write_pointers(pointer_files, explicit_targets or _detected_skills_targets(), dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
