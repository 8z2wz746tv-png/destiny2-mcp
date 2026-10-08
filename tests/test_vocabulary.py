"""词表的唯一出处：`destiny_mcp/vocabulary.py`。

以前同一份映射散在 9 个文件里，值还漂了：官方中文是「生命值」，三处写成「生命」，
旧名映射把「韧性模组」换成不存在的「生命模组」（真机报「没找到护甲模组」）。
这个测试守四件事：

1. **规范名与 Manifest 官方中文一致**（值在下面钉住，改要一起改）；
2. **别名自洽**：中文展示名必须能反查回同一个键；旧名必须指向**存在**的规范名
   （`韧性`→`生命值` 这条就是回归点）；元素别名必须落在已知元素里；
3. **不许再抄一份**：扫源码找同形状的字典字面量；
4. `class_key()` 的输入容错（键/中文/数字/大小写/垃圾输入）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from destiny_mcp import vocabulary as v

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "destiny_mcp"

# Manifest 官方中文（实测 DestinyStatDefinition / DestinyClassDefinition）。
# 改这些值等于改对外文案，必须同时改测试与 CHANGELOG。
OFFICIAL_STAT_LABELS = {
    "weapons": "武器",
    "health": "生命值",
    "class_stat": "职业",
    "grenade": "手雷",
    "melee": "近战",
    "super_stat": "超能",
}
OFFICIAL_CLASS_LABELS = {"titan": "泰坦", "hunter": "猎人", "warlock": "术士"}

# 允许保留的表（按**变量名**放行，不是整个文件）：语义不同，逐条写理由
_ALLOWED_TABLE_NAMES = {
    # 值是一句句"去哪刷"的建议文案，键恰好是六维而已，不是展示名
    "_STAT_ADVICE",
}


def _dicts_with(path: Path, predicate) -> list[tuple[int, str]]:
    """返回 (行号, 变量名) —— 白名单按变量名放行，避免整个文件被免检。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        name = next(
            (t.id for t in targets if isinstance(t, ast.Name)),
            "",
        )
        # 键要**字符串和数字都收**：只收字符串时，`{6: "异域", 5: "传说"}` 这种数字键的拷贝
        # 永远扫不到 —— 稀有度那份拷贝就是这么活下来的（2026-10-06 才发现）。
        keys = [
            k.value
            for k in value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, (str, int))
        ]
        values = [
            val.value
            for val in value.values
            if isinstance(val, ast.Constant) and isinstance(val.value, str)
        ]
        if keys and values and predicate(set(keys), values):
            hits.append((node.lineno, name))
    return hits


def _has_chinese(values: list[str]) -> bool:
    return any(any("\u4e00" <= ch <= "\u9fff" for ch in value) for value in values)


def test_labels_match_the_manifest_official_names() -> None:
    assert v.STAT_LABELS_ZH == OFFICIAL_STAT_LABELS
    assert v.CLASS_LABELS_ZH == OFFICIAL_CLASS_LABELS


def test_stat_aliases_round_trip_and_legacy_names_point_at_real_labels() -> None:
    for key, label in v.STAT_LABELS_ZH.items():
        assert v.STAT_ALIASES[label] == key, f"官方名 {label} 没反查回 {key}"
        assert v.STAT_ALIASES[key] == key
    # 旧名 → 规范名：值必须是**存在**的展示名（韧性→生命 那次就是坏在这里）
    for legacy, current in v.LEGACY_STAT_ALIASES.items():
        assert current in v.STAT_LABELS_ZH.values(), (
            f"旧名 {legacy} 指向了不存在的规范名 {current!r}；"
            f"现有规范名：{sorted(v.STAT_LABELS_ZH.values())}"
        )
    assert v.LEGACY_STAT_ALIASES["韧性"] == "生命值"


def test_class_and_location_and_element_aliases_are_consistent() -> None:
    for key, label in v.CLASS_LABELS_ZH.items():
        assert v.CLASS_ALIASES[label] == key
        assert v.LOCATION_ALIASES[label] == key
        assert v.LOCATION_LABELS_ZH[key] == label
    assert v.LOCATION_ALIASES["仓库"] == "vault" and v.LOCATION_ALIASES["vault"] == "vault"
    assert v.LOCATION_ALIASES[""] == "" and v.LOCATION_ALIASES["全部"] == ""
    for alias, canonical in v.ELEMENT_ALIASES.items():
        assert canonical in v.ELEMENT_LABELS_ZH, f"元素别名 {alias} 指向未知元素 {canonical}"
    assert v.ELEMENT_ALIASES["编织"] == "strand", "「编织」是 strand 的早期写法，保留兼容"


def test_class_key_accepts_the_usual_spellings() -> None:
    assert v.class_key("hunter") == "hunter"
    assert v.class_key("HUNTER") == "hunter"
    assert v.class_key(" 猎人 ") == "hunter"
    assert v.class_key(2) == "warlock"
    assert v.class_key(0) == "titan"
    assert v.class_key(None) == ""
    assert v.class_key("乱写") == ""
    assert v.class_key(True) == "", "布尔是 int 的子类，不能当成 classType 0"


def test_vocabulary_tables_are_not_copied_again() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "vocabulary.py":
            continue
        stat_hits = _dicts_with(
            path,
            lambda keys, values: len(keys & set(v.STAT_KEYS)) >= 4 and _has_chinese(values),
        )
        class_hits = _dicts_with(
            path,
            lambda keys, values: len(keys & set(v.CLASS_KEYS)) >= 2 and _has_chinese(values),
        )
        reverse_hits = _dicts_with(
            path,
            lambda keys, values: len(keys & set(v.CLASS_LABELS_ZH.values())) >= 2
            and set(values) <= set(v.CLASS_KEYS),
        )
        element_hits = _dicts_with(
            path,
            lambda keys, values: len(keys & set(v.ELEMENT_ALIASES)) >= 6
            and set(values) <= set(v.ELEMENT_LABELS_ZH),
        )
        # 稀有度：2026-10-06 才发现 `services/weapon_profile.py` 自己抄了一份
        # （多 3/2 两档）—— 这份扫描以前只覆盖六维/职业/元素，所以它活了下来。
        rarity_hits = _dicts_with(
            path,
            lambda keys, values: len(keys & set(v.RARITY_LABELS_ZH)) >= 2
            and set(values) <= set(v.RARITY_LABELS_ZH.values()),
        )
        for kind, hits in (
            ("六维中文", stat_hits),
            ("职业中文", class_hits),
            ("中文职业→键", reverse_hits),
            ("元素别名", element_hits),
            ("稀有度中文", rarity_hits),
        ):
            offenders.extend(
                f"{path.relative_to(SOURCE_ROOT.parent)}:{line} {name}（{kind}）"
                for line, name in hits
                if name not in _ALLOWED_TABLE_NAMES
            )

    assert not offenders, (
        "这些地方又抄了一份词表，请改成从 destiny_mcp/vocabulary.py 导入"
        "（确实语义不同就加进 _ALLOWED_TABLE_NAMES 并写明理由）：\n  " + "\n  ".join(offenders)
    )


def test_param_descriptions_only_promise_values_the_code_accepts() -> None:
    """**参数说明里列举的取值，代码必须都认**（这条是被真 bug 换来的）。

    历史事故（`CHANGELOG`）：`rarity` 的参数说明写着"传说/异域"可用，而代码只映射英文，
    中文取不到就被**静默跳过过滤** —— 用户以为筛过了，其实拿到的是全量。
    两个方向都钉：说明里提到的值要能解析；代码收的别名也要出现在说明里（否则模型不会用）。
    """
    from destiny_mcp.tools import _param_docs as fields

    description = fields.Rarity.__metadata__[0].description
    promised = ("传说", "异域", "稀有", "legendary", "exotic", "rare", "金枪", "金装", "紫枪", "紫装")
    for value in promised:
        assert value in description, f"说明里没提 {value!r}，模型就不会用它"
        assert v.rarity_key(value) is not None, (
            f"说明承诺了 {value!r}，而 rarity_key 解析不出来 —— 这就会变成静默跳过过滤"
        )
    for alias in v.RARITY_ALIASES:
        assert alias in description, f"代码收 {alias!r}，说明里却没写 —— 模型不会用它"
