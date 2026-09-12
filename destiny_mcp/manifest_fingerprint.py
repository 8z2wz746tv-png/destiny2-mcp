"""Manifest 指纹：给"生成物是否跟得上 Manifest"一个便宜的判定。

背景：强化 perk 配对表这类东西是从 Manifest 生成落盘的。Manifest 更新后如果不重新生成，
数据会**悄悄过期** —— 而 Manifest 里没有版本号（83 张表全是 `Destiny*`，没有 metadata 表），
所以按 hash 单查、按行数猜都不可靠。

做法：
- 指纹记录每个 sqlite 文件的 size + mtime（便宜，用来判断"文件有没有变"）；
- 另外算一次 sha256（贵，685 MB 要几秒），只在**写指纹**时算，之后靠 size+mtime 复用；
- 生成物记录它当时依赖的指纹；启动时比对，不一致就告警"重新跑生成脚本"，**不阻塞**启动。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FINGERPRINT_NAME = "fingerprint.json"


def _sha256(path: Path, *, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _file_entry(path: Path, *, with_hash: bool, previous: dict | None) -> dict:
    stat = path.stat()
    entry = {"size": stat.st_size, "mtime": round(stat.st_mtime, 3)}
    reusable = (
        previous
        and previous.get("size") == entry["size"]
        and previous.get("mtime") == entry["mtime"]
        and previous.get("sha256")
    )
    if reusable:
        entry["sha256"] = previous["sha256"]
    elif with_hash:
        entry["sha256"] = _sha256(path)
    return entry


def manifest_files(manifest_dir: Path) -> list[Path]:
    return sorted(path for path in manifest_dir.glob("*.sqlite3") if path.is_file())


def compute(manifest_dir: Path, *, previous: dict | None = None) -> dict:
    """算指纹：hash 能复用就复用（靠 size+mtime），复用了就标 `hashed=false`。"""
    files: dict[str, dict] = {}
    reused_any = False
    for path in manifest_files(manifest_dir):
        before = (previous or {}).get("files", {}).get(path.name)
        entry = _file_entry(path, with_hash=True, previous=before)
        reused_any = reused_any or bool(before and entry.get("sha256") == before.get("sha256"))
        files[path.name] = entry
    return {
        "schema_version": 1,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reused_hashes": reused_any,
        "files": files,
    }


def read(manifest_dir: Path) -> dict | None:
    path = manifest_dir / FINGERPRINT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write(manifest_dir: Path) -> dict:
    """写指纹（能复用旧 hash 就不重算）。"""
    current = compute(manifest_dir, previous=read(manifest_dir))
    (manifest_dir / FINGERPRINT_NAME).write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return current


def verify(manifest_dir: Path, expected: dict | None) -> tuple[bool, str]:
    """生成物记的指纹与当前 Manifest 是否一致。

    返回 (是否一致, 说明)。生成物还没登记指纹时返回 (True, "未登记")：那是"还没生成过"，
    不是"过期"，不该刷告警。
    """
    if not expected:
        return True, "生成物未登记指纹"
    recorded = (expected.get("files") or {})
    if not recorded:
        return True, "生成物未登记指纹"
    problems: list[str] = []
    for path in manifest_files(manifest_dir):
        want = recorded.get(path.name)
        if not want:
            problems.append(f"{path.name} 是新增的")
            continue
        stat = path.stat()
        if stat.st_size != want.get("size"):
            problems.append(f"{path.name} 大小变了（{want.get('size')} → {stat.st_size}）")
    if problems:
        return False, "Manifest 已更新：" + "；".join(problems) + "。请重新跑生成脚本。"
    return True, "指纹一致"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="写入/校验 Manifest 指纹")
    parser.add_argument("--manifest-dir", type=Path, default=Path("manifest"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if args.verify_only:
        ok, message = verify(args.manifest_dir, read(args.manifest_dir))
        print(message)
        return 0 if ok else 1
    fingerprint = write(args.manifest_dir)
    total = sum(entry["size"] for entry in fingerprint["files"].values())
    print(f"已写入 {args.manifest_dir / FINGERPRINT_NAME}：{len(fingerprint['files'])} 个文件，{total / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
