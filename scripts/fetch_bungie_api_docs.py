#!/usr/bin/env python3
"""把 Bungie 官方 API 文档抓到**仓库外**，供随时深查（scope 表、端点、AWA、组件号都在那一页）。

为什么不进 git：

1. 官方文档是**别人生成的整页快照**（几 MB 的 HTML），入库等于把上游产物抄一遍，
   上游一改我们就得跟着提交一次，diff 里全是噪声；
2. 文档**不是我们的口径**——我们依赖的是自己真机上量到的事实（`docs/reference/bungie_api.md`）。
   把快照放仓库里，容易让人误以为"文档里写的就等于我们验证过的"；
3. 它只读、可随时重下，缓存到 `~/.destiny_mcp/reference/` 就够了，
   而且那个目录**在仓库之外**，`git status` 永远不会看到它（仓库里那条 `.destiny_mcp/`
   规则只管仓库内的同名目录，另有 `scripts/` 里的自检说明）。

网络失败**如实报错**：打印状态码/异常并以非 0 退出，绝不写半截文件、绝不伪造内容。
任何写入都发生在 `--out` 目录里，脚本不会往仓库里写任何东西。

用法：
    python scripts/fetch_bungie_api_docs.py            # 抓一份带日期的快照 + 更新 manifest.json
    python scripts/fetch_bungie_api_docs.py --check    # 只比版本号，不下载
    python scripts/fetch_bungie_api_docs.py --check --exit-code   # 版本变了就退出 1（给 CI/定时任务用）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# 官方文档首页就是全部：scope 表、每个端点、AWA 三段、DestinyComponentType 枚举都在这一页。
SOURCE_URL = "https://bungie-net.github.io/"
# 页面里那一行长这样：<div><strong>Version:</strong> 2.21.8</div>
VERSION_PATTERN = re.compile(r"Version:\s*</strong>\s*([0-9][0-9A-Za-z.\-]*)", re.IGNORECASE)
# `--check` 只想拿页面开头那段，不必把几 MB 下完：版本号在文档最前面（实测前 20 KB 之内）。
CHECK_PROBE_BYTES = 256 * 1024
DEFAULT_OUT = Path.home() / ".destiny_mcp" / "reference"
MANIFEST_NAME = "manifest.json"
USER_AGENT = "destiny2-mcp-bungie-api-reference/1.0"
DEFAULT_TIMEOUT = 180.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_manifest(out_dir: Path) -> dict:
    path = out_dir / MANIFEST_NAME
    if not path.is_file():
        return {"records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 坏掉的记录不当成"没有记录"：如实说，别把"读不出来"说成"第一次抓"。
        raise SystemExit(f"✗ {path} 读不出来：{exc}（修好或删掉它再跑）") from exc
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise SystemExit(f"✗ {path} 形状不对：缺 records 列表")
    return data


def _extract_version(html: str) -> str | None:
    match = VERSION_PATTERN.search(html)
    if match:
        return match.group(1)
    # 兜底：有的版本段落可能被换行/标签拆开，退化成"找第一处 Version: 后面那串数字"。
    loose = re.search(r"Version:\s*(?:<[^>]+>\s*)*([0-9]+\.[0-9]+(?:\.[0-9]+)*)", html)
    return loose.group(1) if loose else None


def _fetch(client: httpx.Client, *, limit_bytes: int | None) -> tuple[bytes, str]:
    """GET 文档；limit_bytes 给定时读够就停（用于 --check 的轻量探测）。"""
    chunks: list[bytes] = []
    got = 0
    with client.stream("GET", SOURCE_URL) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            got += len(chunk)
            if limit_bytes is not None and got >= limit_bytes:
                break
    return b"".join(chunks), str(response.url)


def _report_failure(exc: Exception) -> int:
    if isinstance(exc, httpx.HTTPStatusError):
        print(f"✗ 上游返回 HTTP {exc.response.status_code}：{SOURCE_URL}", file=sys.stderr)
    elif isinstance(exc, httpx.TimeoutException):
        print(f"✗ 抓取超时（{exc!r}）：网络慢或上游没响应，稍后重试即可。", file=sys.stderr)
    elif isinstance(exc, httpx.HTTPError):
        print(f"✗ 抓取失败：{exc!r}", file=sys.stderr)
    else:
        print(f"✗ 抓取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    print("  没有写任何文件，也没有伪造内容；这份失败就是这次的结果。", file=sys.stderr)
    return 1


def run_check(client: httpx.Client, out_dir: Path, exit_code: bool) -> int:
    """只比"当前在线版本号"与上次记录是否一致。"""
    manifest = _load_manifest(out_dir)
    recorded = None
    if manifest["records"]:
        recorded = manifest["records"][-1].get("version") or None
    try:
        html, _ = _fetch(client, limit_bytes=CHECK_PROBE_BYTES)
    except Exception as exc:  # noqa: BLE001 —— 抓取失败的种类由 _report_failure 分流
        return _report_failure(exc)
    online = _extract_version(html.decode("utf-8", errors="replace"))
    if online is None:
        print("✗ 抓到了页面，但解析不出 `Version:` —— 上游改了页面结构，"
              "要么更新本脚本的正则，要么人工去页面确认。", file=sys.stderr)
        return 1

    print(f"来源：{SOURCE_URL}")
    print(f"在线版本：{online}")
    if recorded is None:
        print("上次记录：无（还没抓过）→ 跑一次不带 --check 的抓取把版本记下来。")
        return 1 if exit_code else 0
    if recorded == online:
        print(f"上次记录：{recorded}（一致，文档没换版本）")
        return 0
    print(f"上次记录：{recorded}（不一致，文档换版本了）")
    print("提示：版本变了就去读新文档，再核对 docs/reference/bungie_api.md 里那些带「实测」的条目 ——"
          "实测与文档冲突时以实测为准，并把那一条更新掉。")
    return 1 if exit_code else 0


def run_fetch(client: httpx.Client, out_dir: Path) -> int:
    started = _now()
    try:
        body, final_url = _fetch(client, limit_bytes=None)
    except Exception as exc:  # noqa: BLE001
        return _report_failure(exc)

    text = body.decode("utf-8", errors="replace")
    version = _extract_version(text)
    if version is None:
        print("✗ 抓到了页面，但解析不出 `Version:` —— 不写文件（宁可没有，也不写一份不知道版本的快照）。",
              file=sys.stderr)
        return 1

    host = urlsplit(final_url).hostname or "bungie-net.github.io"
    day = datetime.now().strftime("%Y-%m-%d")
    filename = f"bungie_api_{day}.html"
    if urlsplit(final_url).hostname != urlsplit(SOURCE_URL).hostname:
        filename = f"bungie_api_{host.replace('.', '_')}_{day}.html"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    if target.exists() and target.read_bytes() != body:
        # 同一天抓两次且内容不同：留两份，别互相覆盖（版本号可能没变但页面改了）。
        stamp = datetime.now().strftime("%H%M%S")
        target = out_dir / f"{target.stem}_{stamp}.html"
    target.write_bytes(body)

    digest = hashlib.sha256(body).hexdigest()
    manifest = _load_manifest(out_dir)
    manifest.update({
        "source_url": SOURCE_URL,
        "filename": target.name,
        "version": version,
        "fetched_at": started,
        "bytes": len(body),
        "sha256": digest,
        "out_dir": str(out_dir),
    })
    # 保留历史：重新抓一次不该抹掉"上个月是什么版本"。
    manifest["records"] = [
        record for record in manifest["records"]
        if not (record.get("version") == version and record.get("filename") == target.name)
    ] + [{
        "version": version,
        "fetched_at": started,
        "source_url": SOURCE_URL,
        "filename": target.name,
        "bytes": len(body),
        "sha256": digest,
    }]
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"✓ 文档版本 {version}")
    print(f"✓ 已写入 {target}（{len(body)} 字节，sha256 {digest[:16]}…）")
    print(f"✓ 记录更新 {out_dir / MANIFEST_NAME}（共 {len(manifest['records'])} 条历史）")
    print("提醒：这份快照不是我们的口径；我们依赖的实测事实在 docs/reference/bungie_api.md。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 Bungie 官方 API 文档抓到仓库外（默认 ~/.destiny_mcp/reference/），并记录版本与校验值。",
        epilog="网络失败会如实报错并非 0 退出；脚本不会往仓库里写任何东西。",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="输出目录（默认 ~/.destiny_mcp/reference/，在仓库之外；不存在会创建）")
    parser.add_argument("--check", action="store_true",
                        help="只比对在线文档版本号与上次记录，不下载整页")
    parser.add_argument("--exit-code", action="store_true",
                        help="版本不一致（或没记录/抓取失败）时退出码 1，供 CI 或定时任务使用")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"单次请求超时秒数（默认 {DEFAULT_TIMEOUT:g}）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir: Path = args.out.expanduser().resolve()
    try:
        with httpx.Client(timeout=args.timeout, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            if args.check:
                return run_check(client, out_dir, args.exit_code)
            return run_fetch(client, out_dir)
    except KeyboardInterrupt:
        print("\n已中断：没有写完整文件。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
