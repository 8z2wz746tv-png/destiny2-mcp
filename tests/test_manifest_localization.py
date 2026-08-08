from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

os.environ.setdefault("BUNGIE_API_KEY", "dummy")
os.environ.setdefault("BUNGIE_CLIENT_ID", "1")
os.environ.setdefault("BUNGIE_CLIENT_SECRET", "dummy")

from destiny_mcp.manifest import ManifestManager


class TempManifestManager(ManifestManager):
    def __init__(self, manifest_dir: Path) -> None:
        super().__init__()
        self._manifest_dir = manifest_dir

    @property
    def manifest_path(self) -> Path:
        return self._manifest_dir / "destiny_manifest.sqlite3"

    @property
    def manifest_path_zh(self) -> Path:
        return self._manifest_dir / "destiny_manifest_zh.sqlite3"


def _write_manifest(
    path: Path,
    *,
    name: str,
    type_name: str,
    damage_type: int,
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE DestinyInventoryItemDefinition (id INTEGER PRIMARY KEY, json TEXT NOT NULL)"
        )
        definition = {
            "hash": 1234,
            "displayProperties": {
                "name": name,
                "icon": "/common/destiny2_content/icons/test.png",
            },
            "itemType": 3,
            "itemTypeDisplayName": type_name,
            "inventory": {"tierType": 5, "bucketTypeHash": 1498876634},
            "classType": -1,
            "defaultDamageType": damage_type,
            "equippingBlock": {"ammoType": 1},
        }
        conn.execute(
            "INSERT INTO DestinyInventoryItemDefinition (id, json) VALUES (?, ?)",
            (1234, json.dumps(definition)),
        )
        conn.commit()
    finally:
        conn.close()


def test_manifest_uses_chinese_names_with_english_search_alias(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path / "destiny_manifest.sqlite3",
        name="Fatebringer",
        type_name="Hand Cannon",
        damage_type=3,
    )
    _write_manifest(
        tmp_path / "destiny_manifest_zh.sqlite3",
        name="命运悲剧",
        type_name="手炮",
        damage_type=3,
    )

    manifest = TempManifestManager(tmp_path)
    manifest._load_from_file()

    assert manifest.get_item_name(1234) == "命运悲剧"
    assert manifest.get_english_name(1234) == "Fatebringer"

    english_hit = manifest.search("Fatebringer", limit=1)[0]
    assert english_hit["name"] == "命运悲剧"
    assert english_hit["nameEn"] == "Fatebringer"
    assert english_hit["itemTypeNameDisplayEn"] == "Hand Cannon"
    assert english_hit["damageType"] == 3
    assert english_hit["bucketTypeHash"] == 1498876634

    chinese_hit = manifest.search("命运", limit=1)[0]
    assert chinese_hit["name"] == "命运悲剧"
    assert chinese_hit["nameEn"] == "Fatebringer"

    assert manifest.search_by_type_name("Hand Cannon", limit=1)[0]["name"] == "命运悲剧"
    assert manifest.search_by_type_name("手炮", limit=1)[0]["name"] == "命运悲剧"
    assert manifest.list_weapon_catalog("Hand Cannon")[0]["name"] == "命运悲剧"

    manifest.close()
