"""生成物新鲜度：`data/weapon_enhanced_pairs.json` 必须与当前 Manifest 对得上。

Manifest 更新后如果忘了重新生成，强化标记会**悄悄错**。这个测试直接拿当前 Manifest
重跑一遍生成逻辑，与落盘文件逐条比对 —— 比"看指纹"更硬：连生成逻辑改了也能发现。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from destiny_mcp import manifest_fingerprint
from destiny_mcp.manifest import ManifestManager
from destiny_mcp.services import weapon_profile as wp

ROOT = Path(__file__).parents[1]
MANIFEST_DIR = ROOT / "manifest"
MANIFEST_ZH = MANIFEST_DIR / "destiny_manifest_zh.sqlite3"
ASSET = ROOT / "data" / "weapon_enhanced_pairs.json"
requires_manifest = pytest.mark.skipif(
    not MANIFEST_ZH.is_file(), reason="需要本机 Manifest"
)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "_generate_weapon_metadata", ROOT / "scripts" / "generate_weapon_metadata.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def asset() -> dict:
    assert ASSET.is_file(), "缺少生成物 data/weapon_enhanced_pairs.json（跑生成脚本）"
    return json.loads(ASSET.read_text(encoding="utf-8"))


def test_asset_shape_and_metadata(asset: dict) -> None:
    assert asset["schema_version"] == 1
    assert asset["pair_count"] == len(asset["pairs"])
    assert asset["perk_count"] == len({entry["name"] for entry in asset["pairs"].values()})
    assert asset["pair_count"] > 150, "配对数量太少，规则可能坏了"
    assert asset["skipped"], "跳过的组也要记下来，方便解释为什么某些 perk 没配对"


def test_asset_entries_are_self_consistent(asset: dict) -> None:
    for base, entry in asset["pairs"].items():
        assert base.isdigit()
        assert str(entry["enhanced"]).isdigit()
        assert entry["base_description"] != entry["enhanced_description"], (
            f"{entry['name']} 的基础与强化描述相同，不该配对"
        )


def test_asset_records_a_manifest_fingerprint(asset: dict) -> None:
    assert asset["manifest_fingerprint"], "生成物没记 Manifest 指纹，无法判断过期"
    ok, message = manifest_fingerprint.verify(MANIFEST_DIR, asset["manifest_fingerprint"])
    assert ok, message


@requires_manifest
def test_asset_matches_a_fresh_regeneration(asset: dict) -> None:
    """重新跑一遍生成逻辑，必须得到同一份配对（连生成规则改了也能发现）。"""
    manifest = ManifestManager()
    manifest._load_from_file()
    pairs, skipped = _generator().build_pairs(manifest)

    assert pairs == asset["pairs"], "生成物与当前 Manifest 不一致，请重新跑生成脚本"
    assert skipped == asset["skipped"]


@requires_manifest
def test_loader_reads_the_asset() -> None:
    pairs = wp.load_enhanced_pairs()

    assert pairs.size > 150
    sample = json.loads(ASSET.read_text(encoding="utf-8"))["pairs"]
    base = int(next(iter(sample)))
    assert pairs.enhanced_of(base) == int(sample[str(base)]["enhanced"])
    assert pairs.is_enhanced(int(sample[str(base)]["enhanced"])) is True
    assert pairs.base_of(int(sample[str(base)]["enhanced"])) == base
    # 不存在的 hash 一律 0，不抛异常
    assert pairs.enhanced_of(1) == 0
    assert pairs.is_enhanced(1) is False
