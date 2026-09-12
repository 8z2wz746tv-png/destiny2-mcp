"""Manifest 指纹：让"生成物过期"变成可检测的事，而不是某天突然发现数据全错。

Manifest 里没有版本号（83 张表全是 `Destiny*`），所以只能自己钉：
- 指纹记 size + mtime + sha256；重复计算时靠 size+mtime 复用 hash（685 MB 不必每次重算）；
- 校验只比 size：Manifest 更新几乎必然改变 size，而 mtime 会因为拷贝/rsync 变化，
  拿 mtime 当判据会误报。
"""

from __future__ import annotations

from pathlib import Path

from destiny_mcp import manifest_fingerprint as fp


def _manifest_dir(tmp_path: Path) -> Path:
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    (manifest_dir / "destiny_manifest.sqlite3").write_bytes(b"a" * 1024)
    (manifest_dir / "destiny_manifest_zh.sqlite3").write_bytes(b"b" * 2048)
    (manifest_dir / "not-a-manifest.txt").write_text("忽略我", encoding="utf-8")
    return manifest_dir


def test_compute_only_covers_sqlite_files(tmp_path: Path) -> None:
    fingerprint = fp.compute(_manifest_dir(tmp_path))

    assert set(fingerprint["files"]) == {
        "destiny_manifest.sqlite3",
        "destiny_manifest_zh.sqlite3",
    }
    entry = fingerprint["files"]["destiny_manifest.sqlite3"]
    assert entry["size"] == 1024
    assert len(entry["sha256"]) == 64


def test_write_then_verify_round_trip(tmp_path: Path) -> None:
    manifest_dir = _manifest_dir(tmp_path)

    fp.write(manifest_dir)
    ok, message = fp.verify(manifest_dir, fp.read(manifest_dir))

    assert ok, message
    assert message == "指纹一致"


def test_verify_detects_an_updated_manifest(tmp_path: Path) -> None:
    manifest_dir = _manifest_dir(tmp_path)
    fp.write(manifest_dir)

    (manifest_dir / "destiny_manifest_zh.sqlite3").write_bytes(b"c" * 4096)
    ok, message = fp.verify(manifest_dir, fp.read(manifest_dir))

    assert not ok
    assert "大小变了" in message
    assert "重新跑生成脚本" in message


def test_verify_is_quiet_when_the_asset_has_no_fingerprint(tmp_path: Path) -> None:
    ok, message = fp.verify(_manifest_dir(tmp_path), None)

    assert ok
    assert "未登记" in message


def test_repeated_compute_reuses_the_hash(tmp_path: Path) -> None:
    manifest_dir = _manifest_dir(tmp_path)

    first = fp.compute(manifest_dir)
    second = fp.compute(manifest_dir, previous=first)

    assert second["reused_hashes"] is True
    assert (
        second["files"]["destiny_manifest.sqlite3"]["sha256"]
        == first["files"]["destiny_manifest.sqlite3"]["sha256"]
    )


def test_new_file_in_the_directory_invalidates_the_fingerprint(tmp_path: Path) -> None:
    manifest_dir = _manifest_dir(tmp_path)
    fp.write(manifest_dir)

    (manifest_dir / "destiny_manifest_fr.sqlite3").write_bytes(b"d" * 512)
    ok, message = fp.verify(manifest_dir, fp.read(manifest_dir))

    assert not ok
    assert "新增的" in message
