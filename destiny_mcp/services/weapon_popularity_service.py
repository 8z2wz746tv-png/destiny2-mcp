"""Versioned weapon perk popularity snapshots enriched by the Manifest."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ..exceptions import WeaponPopularityDataError
from ..manifest import ManifestManager
from . import weapon_payload, weapon_profile
from ..utils.hash_utils import to_unsigned

_RESOURCE_PACKAGE = "destiny_mcp"
_RESOURCE_PATH = "data/weapon_popularity.json"
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Rate = Annotated[float, Field(ge=0, le=100)]
_MISSING = object()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _RateEntry(_StrictModel):
    name: _Name
    selection_rate: _Rate | None


class _Combination(_StrictModel):
    perks: list[_Name] = Field(min_length=1)
    selection_rate: _Rate


class _PerkColumn(_StrictModel):
    slot_name: _Name
    label: _Name
    items: list[_RateEntry]


class _Intrinsic(_StrictModel):
    name: _Name
    description: str = ""


class _ManifestVariant(_StrictModel):
    is_holofoil: bool


class _WeaponSnapshot(_StrictModel):
    name: _Name
    weapon_type: _Name
    version_label: _Name
    status: str = ""
    manifest_variant: _ManifestVariant
    release_label: str = ""
    episode: str = ""
    intrinsic: _Intrinsic
    stats: dict[str, int]


class _Source(_StrictModel):
    kind: _Name
    label: _Name
    captured_at: str
    snapshot_sha256: str
    note: str = ""


class _Snapshot(_StrictModel):
    weapon: _WeaponSnapshot
    source: _Source
    popular_combinations: list[_Combination]
    perk_columns: list[_PerkColumn]
    masterworks: list[_RateEntry]
    mods: list[_RateEntry]


class _PopularityData(_StrictModel):
    schema_version: Literal[1]
    snapshots: list[_Snapshot]


class WeaponPopularityMergeResult(TypedDict):
    document: dict[str, object]
    added: list[str]
    replaced: list[str]
    total: int


def merge_weapon_popularity_documents(
    current_path: Path,
    incoming_path: Path,
    *,
    replace: bool = False,
) -> WeaponPopularityMergeResult:
    """Validate and merge snapshot documents without writing to disk."""
    current = (
        WeaponPopularityService._load_data(current_path)
        if current_path.exists()
        else _PopularityData(schema_version=1, snapshots=[])
    )
    incoming = WeaponPopularityService._load_data(incoming_path)

    merged = list(current.snapshots)
    positions: dict[str, int] = {}
    for index, snapshot in enumerate(merged):
        key = snapshot.weapon.name.casefold()
        if key in positions:
            raise WeaponPopularityDataError(
                f"当前数据存在重复武器：{snapshot.weapon.name!r}。"
            )
        positions[key] = index

    added: list[str] = []
    replaced: list[str] = []
    incoming_names: set[str] = set()
    for snapshot in incoming.snapshots:
        key = snapshot.weapon.name.casefold()
        if key in incoming_names:
            raise WeaponPopularityDataError(
                f"导入文档存在重复武器：{snapshot.weapon.name!r}。"
            )
        incoming_names.add(key)
        if key in positions:
            if not replace:
                raise WeaponPopularityDataError(
                    f"「{snapshot.weapon.name}」已存在；如需替换请显式使用 --replace。"
                )
            merged[positions[key]] = snapshot
            replaced.append(snapshot.weapon.name)
        else:
            positions[key] = len(merged)
            merged.append(snapshot)
            added.append(snapshot.weapon.name)

    document = _PopularityData(
        schema_version=1,
        snapshots=merged,
    ).model_dump(mode="json")
    return {
        "document": document,
        "added": added,
        "replaced": replaced,
        "total": len(merged),
    }


class WeaponPopularityService:
    """Read immutable popularity snapshots and add Manifest identities."""

    def __init__(
        self,
        manifest: ManifestManager,
        data_path: Path | None = None,
    ) -> None:
        self._manifest = manifest
        self._data = self._load_data(data_path)
        self._snapshots: dict[str, _Snapshot] = {}
        for snapshot in self._data.snapshots:
            key = snapshot.weapon.name.casefold()
            if key in self._snapshots:
                raise WeaponPopularityDataError(
                    f"武器快照名称重复：{snapshot.weapon.name!r}。"
                )
            self._snapshots[key] = snapshot

    def get_weapon_popularity(self, weapon_name: str) -> dict | None:
        """Return a recorded snapshot with hashes and icons from Manifest."""
        snapshot = self._find_snapshot(weapon_name)
        if snapshot is None:
            return None

        weapon_hit, weapon_definition = self._resolve_weapon(snapshot.weapon)
        socket_items = self._socket_items(weapon_definition)
        warnings: list[str] = []

        intrinsic = self._enrich_entry(
            snapshot.weapon.intrinsic.name,
            socket_items,
            warnings,
        )
        intrinsic["description"] = snapshot.weapon.intrinsic.description

        perk_columns = []
        enriched_by_name: dict[str, dict[str, object]] = {}
        for column in snapshot.perk_columns:
            items = []
            for entry in column.items:
                enriched = self._enrich_entry(
                    entry.name,
                    socket_items,
                    warnings,
                    selection_rate=entry.selection_rate,
                )
                items.append(enriched)
                enriched_by_name.setdefault(entry.name.casefold(), enriched)
            perk_columns.append(
                {
                    "slot_name": column.slot_name,
                    "label": column.label,
                    "items": items,
                }
            )

        combinations = []
        for combination in snapshot.popular_combinations:
            perks = []
            for name in combination.perks:
                combo_entry = enriched_by_name.get(name.casefold())
                if combo_entry is None:
                    combo_entry = self._enrich_entry(name, socket_items, warnings)
                perks.append(
                    {
                        "name": combo_entry["name"],
                        "plug_hash": combo_entry["plug_hash"],
                        "icon_url": combo_entry["icon_url"],
                    }
                )
            combinations.append(
                {
                    "perks": perks,
                    "selection_rate": combination.selection_rate,
                }
            )

        masterworks = [
            self._enrich_entry(
                entry.name,
                socket_items,
                warnings,
                selection_rate=entry.selection_rate,
                required_prefix="大师杰作：",
            )
            for entry in snapshot.masterworks
        ]
        mods = [
            self._enrich_entry(
                entry.name,
                socket_items,
                warnings,
                selection_rate=entry.selection_rate,
            )
            for entry in snapshot.mods
        ]

        return {
            "schema_version": self._data.schema_version,
            "weapon": self._weapon_identity(snapshot.weapon, weapon_hit),
            "intrinsic": intrinsic,
            "stats": dict(snapshot.weapon.stats),
            "source": snapshot.source.model_dump(),
            "popular_combinations": combinations,
            "perk_columns": perk_columns,
            "masterworks": masterworks,
            "mods": mods,
            "warnings": warnings,
        }

    @staticmethod
    def _load_data(data_path: Path | None) -> _PopularityData:
        try:
            if data_path is not None:
                raw = data_path.read_text(encoding="utf-8")
                source = str(data_path)
            else:
                resource = files(_RESOURCE_PACKAGE).joinpath(_RESOURCE_PATH)
                raw = resource.read_text(encoding="utf-8")
                source = f"{_RESOURCE_PACKAGE}/{_RESOURCE_PATH}"
        except OSError as exc:
            raise WeaponPopularityDataError(f"无法读取武器选取率数据：{exc}") from exc

        try:
            return _PopularityData.model_validate_json(raw, strict=True)
        except ValidationError as exc:
            raise WeaponPopularityDataError(
                f"武器选取率数据无效 {source}：{exc}"
            ) from exc

    def _find_snapshot(self, weapon_name: str) -> _Snapshot | None:
        query = weapon_name.strip().casefold()
        if not query:
            return None
        exact = self._snapshots.get(query)
        if exact is not None:
            return exact
        partial = [
            snapshot for name, snapshot in self._snapshots.items() if query in name
        ]
        return partial[0] if len(partial) == 1 else None

    def _resolve_weapon(self, recorded: _WeaponSnapshot) -> tuple[dict, dict]:
        matches: list[tuple[dict, dict]] = []
        for hit in self._manifest.search(recorded.name, limit=50):
            if (
                hit.get("itemType") != 3
                or str(hit.get("name") or "").casefold() != recorded.name.casefold()
            ):
                continue
            definition = self._manifest.get_item_definition(int(hit["itemHash"]))
            if (
                isinstance(definition, dict)
                and definition.get("isHolofoil")
                is recorded.manifest_variant.is_holofoil
            ):
                matches.append((hit, definition))

        if len(matches) != 1:
            raise WeaponPopularityDataError(
                f"「{recorded.name}」的 Manifest 版本判别应唯一命中，"
                f"实际命中 {len(matches)} 个。"
            )
        return matches[0]

    def _weapon_identity(self, recorded: _WeaponSnapshot, hit: dict) -> dict:
        """身份块走统一工厂（稀有度/框架/射速这些以前在选取率里是缺的）。"""
        definition = None
        getter = getattr(self._manifest, "get_item_definition", None)
        if callable(getter):
            definition = getter(int(hit["itemHash"]))
        block = weapon_payload.lean_identity(
            self._manifest,
            definition,
            roll_kind=weapon_profile.roll_kind(definition),
            fallback_name=str(hit.get("name") or recorded.name),
        )
        # item_hash 以命中记录为准（定义里缺 hash 时不能变成 0），沿用快照的无符号约定
        block["item_hash"] = to_unsigned(int(hit["itemHash"]))
        block.update({
            "version_label": recorded.version_label,
            "status": recorded.status,
            "release_label": recorded.release_label,
            "episode": recorded.episode,
        })
        if not block.get("weapon_type"):
            block["weapon_type"] = recorded.weapon_type
        return block

    def _socket_items(self, definition: dict) -> list[dict]:
        resolved: dict[int, dict] = {}
        entries = definition.get("sockets", {}).get("socketEntries", [])
        if not isinstance(entries, list):
            return []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hashes: list[int] = []
            initial_hash = entry.get("singleInitialItemHash")
            if isinstance(initial_hash, int) and initial_hash:
                hashes.append(initial_hash)
            reusable_items = entry.get("reusablePlugItems", [])
            if isinstance(reusable_items, list):
                hashes.extend(
                    plug["plugItemHash"]
                    for plug in reusable_items
                    if isinstance(plug, dict)
                    and isinstance(plug.get("plugItemHash"), int)
                )
            plug_set_hash = entry.get("randomizedPlugSetHash") or entry.get(
                "reusablePlugSetHash"
            )
            if isinstance(plug_set_hash, int) and plug_set_hash:
                hashes.extend(
                    plug["plugItemHash"]
                    for plug in self._manifest.get_plug_set_plugs(plug_set_hash) or []
                    if isinstance(plug, dict)
                    and isinstance(plug.get("plugItemHash"), int)
                )
            for plug_hash in hashes:
                info = self._manifest.get_item_info(plug_hash)
                if isinstance(info, dict):
                    resolved[to_unsigned(plug_hash)] = info
        return list(resolved.values())

    @staticmethod
    def _enrich_entry(
        name: str,
        socket_items: list[dict],
        warnings: list[str],
        *,
        selection_rate: float | None | object = _MISSING,
        required_prefix: str = "",
    ) -> dict[str, object]:
        matches = []
        for item in socket_items:
            manifest_name = str(item.get("name") or "")
            comparable_name = manifest_name
            if required_prefix:
                if not manifest_name.startswith(required_prefix):
                    continue
                comparable_name = manifest_name[len(required_prefix) :]
            if comparable_name.casefold() == name.casefold():
                matches.append(item)

        if not name.startswith("强化"):
            normal = [
                item
                for item in matches
                if not str(item.get("itemTypeNameDisplay") or "").startswith("强化")
            ]
            if normal:
                matches = normal

        unique = {
            to_unsigned(int(item["itemHash"])): item
            for item in matches
            if isinstance(item.get("itemHash"), int)
        }
        result: dict[str, object] = {"name": name}
        if selection_rate is not _MISSING:
            result["selection_rate"] = selection_rate

        if len(unique) == 1:
            plug_hash, item = next(iter(unique.items()))
            result["plug_hash"] = plug_hash
            result["icon_url"] = str(item.get("icon") or "")
            return result

        result["plug_hash"] = None
        icons = {
            str(item.get("icon") or "") for item in unique.values() if item.get("icon")
        }
        result["icon_url"] = next(iter(icons)) if len(icons) == 1 else ""
        if unique:
            warnings.append(
                f"「{name}」在该武器 socket 中对应 {len(unique)} 个 Manifest Hash，"
                "已保留图标但未猜测 Hash。"
            )
        else:
            warnings.append(f"「{name}」未能在该武器的 Manifest socket 中解析。")
        return result
