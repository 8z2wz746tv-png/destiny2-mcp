#!/usr/bin/env python3
"""把 `skills/destiny2-mcp` 装到宿主的 skills 目录里。

为什么需要这一步：MCP 工具的 schema 会跟着工具一起进上下文，但仓库里的这份
Markdown 不会 —— Agent 用 MCP 的时候工作目录通常在别处，它不会去翻这个仓库。
所以「写完文档」和「Agent 真的看得到」之间差一次安装。

仓库始终是唯一源头，这里只做镜像复制（多余文件会被删掉），不改动宿主的
任何配置。默认目标是 Codex 的 `~/.codex/skills/`；其它宿主用 `--target`
指到它自己的 skills 目录。

    .venv/bin/python scripts/install_skill.py --dry-run
    .venv/bin/python scripts/install_skill.py
    .venv/bin/python scripts/install_skill.py --target ~/.claude/skills
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "skills" / "destiny2-mcp"
DEFAULT_TARGET = Path.home() / ".codex" / "skills"
SKILL_NAME = "destiny2-mcp"

# 只有这些进安装包：给 Agent 看的文档 + 宿主识别用的元数据。
# scripts/ 之类的东西留在仓库里，不往宿主目录塞。
INCLUDE = ("SKILL.md", "references", "agents")


def _relative_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    }


def sync(source: Path, target: Path, *, dry_run: bool) -> int:
    if not (source / "SKILL.md").is_file():
        print(f"✗ 源头缺少 SKILL.md：{source}")
        return 1

    wanted: dict[Path, Path] = {}
    for name in INCLUDE:
        origin = source / name
        if origin.is_file():
            wanted[Path(name)] = origin
        elif origin.is_dir():
            for path in origin.rglob("*"):
                if path.is_file() and not any(part.startswith(".") for part in path.parts):
                    wanted[path.relative_to(source)] = path

    existing = _relative_files(target) if target.is_dir() else set()
    copied, removed = [], sorted(existing - set(wanted))

    for relative, origin in sorted(wanted.items()):
        destination = target / relative
        if destination.is_file() and filecmp.cmp(origin, destination, shallow=False):
            continue
        copied.append(relative)
        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)

    for relative in removed:
        if not dry_run:
            (target / relative).unlink()

    verb = "将要" if dry_run else "已"
    print(f"{verb}安装 {SKILL_NAME} -> {target}")
    print(f"  更新 {len(copied)} 个文件" + (f"：{', '.join(map(str, copied))}" if copied else ""))
    print(f"  删除 {len(removed)} 个多余文件" + (f"：{', '.join(map(str, removed))}" if removed else ""))
    if not copied and not removed:
        print("  已经是最新的。")
    if dry_run:
        print("  （--dry-run：没有写入任何文件）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 destiny2-mcp skill 安装到宿主目录")
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"宿主的 skills 目录（默认 {DEFAULT_TARGET}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只报告将要做什么")
    args = parser.parse_args()

    return sync(SOURCE, args.target.expanduser() / SKILL_NAME, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
