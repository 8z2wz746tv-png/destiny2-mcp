"""Manifest 名称查表：伤害 / 弹药 / 破盾 / 属性的中文名只在这里定义一份。

这些名字以前散在几张硬编码表里，代价是：
- 漏了「突袭」这个伤害类型；
- 7 号伤害类型写成「棱镜」（Manifest 里是「缚丝」）；
- 属性名在 `weapon_detail_service` / `manifest_query_service` / `weapon_roll_filter_service`
  各写一套，加一个属性要改三处。

现在统一查 `Destiny*Definition`，带进程内缓存；查不到就把 hash 原样返回并让调用方决定，
**绝不抛异常**（fail-soft：新版本加了新伤害类型，也只是名字暂时显示成 hash）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .manifest import ManifestManager

# 每分钟发射数（RPM）：定义里的投资值与显示值可能不同，见 weapon_profile.stat_value
RPM_STAT_HASH = 4284893193

# 弹药类型是 Bungie 的枚举，没有对应 Definition —— 这是唯一一份表。
# 用「白弹/绿弹/威能」而不是官方文案的「主要/特殊/威能弹药」：本地刷取清单就叫
# 「刷取清单-白弹紫枪 / 绿弹紫枪 / 威能紫枪」，回答时要能跟清单名对上。
AMMO_TYPE_NAMES: dict[int, str] = {1: "白弹", 2: "绿弹", 3: "威能"}

_DAMAGE_SUFFIX = "伤害"


def _unsigned(value: int) -> int:
    return value + 4294967296 if value < 0 else value


def _iter_table(manifest: Any, table: str) -> list[dict]:
    """遍历一张表；manifest 实现（或测试替身）没有这个能力时安静地返回空。

    名称表是**装饰性**数据：拿不到就回退 hash/空串，绝不能让一次查询失败。
    """
    iterator = getattr(manifest, "iter_definitions", None)
    if not callable(iterator):
        return []
    try:
        return list(iterator(table))
    except Exception:  # noqa: BLE001 - 见上：名称拿不到不影响主流程
        return []


class ManifestNames:
    """按需建表、建完缓存；每个 Manifest 实例一份。"""

    def __init__(self, manifest: "ManifestManager") -> None:
        self._manifest = manifest
        self._stat_names: dict[int, str] = {}
        self._damage_by_hash: dict[int, str] = {}
        self._damage_by_enum: dict[int, str] = {}
        self._breaker_by_enum: dict[int, str] = {}
        self._breaker_by_hash: dict[int, str] = {}

    # ── 属性 ──────────────────────────────────────────────────────────────
    def stat(self, stat_hash: int) -> str:
        """属性中文名；Manifest 里没有名字时返回空串（调用方应跳过该属性）。"""
        if stat_hash in self._stat_names:
            return self._stat_names[stat_hash]
        getter = getattr(self._manifest, "get_definition", None)
        definition = getter("DestinyStatDefinition", stat_hash) if callable(getter) else None
        name = ""
        if isinstance(definition, dict):
            name = str((definition.get("displayProperties") or {}).get("name") or "")
        self._stat_names[stat_hash] = name
        return name

    # ── 伤害类型 ──────────────────────────────────────────────────────────
    def _load_damage_types(self) -> None:
        if self._damage_by_hash or self._damage_by_enum:
            return
        for definition in _iter_table(self._manifest, "DestinyDamageTypeDefinition"):
            name = str((definition.get("displayProperties") or {}).get("name") or "")
            name = name.removesuffix(_DAMAGE_SUFFIX)  # 「电弧伤害」→「电弧」，与游戏内一致
            if not name:
                continue
            definition_hash = definition.get("hash")
            if isinstance(definition_hash, int):
                self._damage_by_hash[definition_hash] = name
                self._damage_by_hash[_unsigned(definition_hash)] = name
            enum_value = definition.get("enumValue")
            if isinstance(enum_value, int):
                self._damage_by_enum[enum_value] = name

    def damage_type(self, value: int | None) -> str:
        """接受伤害类型的 hash 或枚举值（`defaultDamageTypeHash` / `defaultDamageType`）。"""
        if not isinstance(value, int) or value <= 0:
            return ""
        self._load_damage_types()
        if value in self._damage_by_hash:
            return self._damage_by_hash[value]
        return self._damage_by_enum.get(value, "")

    # ── 破盾类型（勇士）────────────────────────────────────────────────────
    def _load_breaker_types(self) -> None:
        if self._breaker_by_enum or self._breaker_by_hash:
            return
        for definition in _iter_table(self._manifest, "DestinyBreakerTypeDefinition"):
            name = str((definition.get("displayProperties") or {}).get("name") or "")
            if not name:
                continue
            definition_hash = definition.get("hash")
            if isinstance(definition_hash, int):
                self._breaker_by_hash[definition_hash] = name
                self._breaker_by_hash[_unsigned(definition_hash)] = name
            enum_value = definition.get("enumValue")
            if isinstance(enum_value, int):
                self._breaker_by_enum[enum_value] = name

    def breaker_type(self, value: int | None) -> str:
        """破盾类型名（干扰/眩晕/贯穿护盾）；无破盾能力时返回空串。"""
        if not isinstance(value, int) or value <= 0:
            return ""
        self._load_breaker_types()
        if value in self._breaker_by_hash:
            return self._breaker_by_hash[value]
        return self._breaker_by_enum.get(value, "")

    # ── 弹药 ──────────────────────────────────────────────────────────────
    @staticmethod
    def ammo_type(ammo_enum: int | None) -> str:
        if not isinstance(ammo_enum, int):
            return ""
        return AMMO_TYPE_NAMES.get(ammo_enum, "")


def names_for(manifest: Any) -> ManifestNames:
    """取（或建）某个 Manifest 对应的名称表。"""
    cached = getattr(manifest, "_weapon_names_cache", None)
    if cached is None:
        cached = ManifestNames(manifest)
        try:
            manifest._weapon_names_cache = cached
        except AttributeError:  # pragma: no cover - 只读实现
            pass
    return cached
