"""凡输出武器/装备（护甲）/perk 身份的出口，必须带 `icon_url`：跨切面静态守门。

**为什么有这个文件（这笔账要记清楚，不然下次还会有人问"为什么不能自己拼一个 URL"）：**

图标 URL 以前在 12 处各拼一遍（`f"https://www.bungie.net{icon}"`、`_cdn_url()`、
`_absolute_icon_url()`、`BUNGIE_BASE_URL + icon`…），同一个 `icon_url` 字段因此有四种边界行为；
而**出口之间对不齐**这件事不会报错，只会让前端"有的卡片有图、有的只能放色块"——
用户 2026-10-04 拿实拍截图报的正是这个（`inventory_assistant(intent="mods")` 的五件护甲
只有 slot/name/instance_id/hash/is_exotic/power/energy/mods；锻造图样、`find`/`recommend`
的候选行、账号配装的 `build_template` 同样没有图）。

口径照老 web（`~/项目/Destiny_MCP/webui/api/destiny_icons.py::normalize_destiny_icon_url`）：
键名一律 `icon_url`（**perk 与物品同名**，不另造 `perk_icon`/`image`/`icon`），
值一律是绝对的 Bungie CDN 地址。这里守两件事：

1. **单一出处**（`test_no_second_icon_url_constructor`）：除了
   `destiny_mcp/utils/icons.py`，任何模块都不许把 Bungie 源站字面量与别的表达式拼起来
   当图标值用。判据：找 `JoinedStr`（f-string）与字符串 `+` 拼接里含 `bungie.net`
   字面量的节点，再看它绑给了谁 —— 绑定名/字典键/关键字实参里带 `icon` 的判红。
   不看在哪儿出现、只看"结果是不是当图标在用"，所以 `bungie_client` 的
   `f"https://www.bungie.net{world_url}"`（Manifest 资源地址，不是图标）不在此列。
2. **身份行必须带 `icon_url`**（`test_identity_rows_carry_icon_url`）：字典字面量或
   "键清单常量"同时带**名字键**与**身份 hash 键**时，必须也有 `icon_url`，
   除非登记在下面的 `_EXEMPT` 台账里（每条写明理由）。

**台账是冻结的复核清单**，不是"让自己变绿的开关"：新增的漏网出口直接判红；
台账里过期（代码已经补上图标或删掉）的条目也判红，逼着条目跟着代码走。
按 `tests/test_hash_domains.py` 的老规矩：确实存在、只是这一轮不修的，理由里写清是什么、
留多少体积，而不是含糊过去。

**已知漏检边界（别把它当成"图标全查过了"）：**

- 只认**字面量**形状的行：经过 `{key: item.get(key) for key in KEYS}` 投影出来的行，
  只有 `KEYS` 常量本身在扫描范围内（`_ROW_ITEM_FIELDS` 就是这么被抓到的）；
- 名字/hash 键表是**封闭词表**（见 `_NAME_KEYS` / `_HASH_KEYS`），换个键名就漏；
- 只扫 `destiny_mcp/**`：`legacy/`（历史存档，不进包）与 `tests/` 不在范围内；
- 不检查运行期拼装（例如把行交给另一个函数再补键）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from destiny_mcp.utils.icons import BUNGIE_ORIGIN, is_bungie_icon

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "destiny_mcp"

#: 唯一允许构造图标 URL 的模块（`BUNGIE_ORIGIN` / `icon_url()` 的出处）
ICON_SOURCE = "destiny_mcp/utils/icons.py"

#: 名字键：一行"这是什么"靠它
_NAME_KEYS = {"name", "plug_name", "item_name", "set_name", "perk_name"}
#: 身份 hash 键：一行"是哪一件"靠它。刻意**不含**裸 `hash`/`record_hash`/`tier_hash`
#: —— 那些在套装层级、记录、档位表里到处都是，收进来会把台账冲成噪声。
_HASH_KEYS = {"item_hash", "plug_hash", "itemHash", "plugItemHash", "set_hash"}
_ICON_KEYS = {"icon_url", "iconUrl"}

# ── 豁免台账 ──────────────────────────────────────────────────────────────
# 键 = `相对路径::函数名`（键清单常量用 `相对路径::常量名`）。每条都要写**为什么**。
_EXEMPT: dict[str, str] = {
    # ── ① 定义级 perk 池：体积口径（见上面 ⚠️ ①，+17 KB/把）──────────────────
    # `scope="definition"` 的 perk 池默认不带 description/icon_url —— 池子回答"能滚到什么"，
    # 要看效果走 `perk_description`、要图走实例级 options。
    # 见 `services/weapon_payload.socket_list` 与 `tests/test_weapon_key*` 的
    # `DEFINITION_ONLY_ABSENT`；要翻这条得先回答 17 KB/把 的账（用户 2026-10-04 拍板：不带）。
    "destiny_mcp/services/weapon_profile.py::_plug_option": "定义级池子不带图标（+17 KB/把 的体积口径，P6 实测）",
    "destiny_mcp/services/weapon_profile.py::intrinsic_plug": "固有特性走 socket 行的同一套投影",
    "destiny_mcp/services/weapon_profile.py::with_equipped": "`equipped` 只报「装着哪个」，图在 options 里",
    "destiny_mcp/services/weapon_payload.py::_normalize_equipped": "同上：每槽一个 name/hash 对，图在 options 里",
    # 选取率快照（第三方统计）里的 perk 行：快照本身只有 name/selection_rate，图标是**定义级**的。
    # 同一个 `popularity` intent 的另一条路（`weapon_popularity_service._enrich_entry`）本来
    # **带**图标 —— 2026-10-04 已按同一条口径**统一成不带**（那条路不再有 icon_url），
    # 两条路的一致性由 `test_popularity_paths_agree_on_perk_rows` 钉住。
    "destiny_mcp/services/weapon_local_data.py::_popularity_summary": "第三方统计快照没有图标字段（要补得再查一次 Manifest，见报告的口径不一致项）",
    # ── ② 原始 Manifest 行 / 内部暂存：不是出口 ────────────────────────────
    "destiny_mcp/manifest_search.py::_build_name_index": "索引条目用原始键 `icon`（已是绝对地址），出口才改名 icon_url",
    "destiny_mcp/manifest_armor.py::_lookup_set_bonus": "套装（set_hash/set_name）不是物品，没有自己的图标",
    "destiny_mcp/services/set_bonus_service.py::lookup_armor_set": "套装行同上；`armor_pieces[]` 已带图标",
    "destiny_mcp/services/set_bonus_service.py::list_all_set_bonuses": "套装行同上（列表里没有具体某一件）",
    "destiny_mcp/services/starside_matching.py::_set_resolution": "社区模板匹配的**中间产物**，不进响应",
    "destiny_mcp/services/starside_matching.py::_exact_definitions": "社区模板匹配的**中间产物**，不进响应",
    "destiny_mcp/services/starside_matching.py::validate_build": "社区模板校验结果（不可信参考），不是账号物品",
    "destiny_mcp/services/weapon_detail_service.py::add_weapon": "收集阶段的暂存行，出口走 weapon_payload.list_row（有图）",
    "destiny_mcp/services/manifest_query_service.py::_find_catalysts": "催化剂候选暂存，出口 `catalyst` 行已带图标",
    "destiny_mcp/services/manifest_query_service.py::_extract_catalyst_from_sockets": "同上（另一条取候选的路）",
    "destiny_mcp/services/pattern_service.py::_catalog": "图样目录的中间行，出口 `_row` 已带 icon_url（这里存 icon_path）",
    "destiny_mcp/services/armor_mod_service.py::plan": "模组计划回执的用户输入回显，不是物品身份行",
    "destiny_mcp/services/loadout_recovery.py::_capture_recovery_state()→LoadoutItem": "回滚用的现场快照，不进响应（恢复时按实例 ID 逐件还原）",
    "destiny_mcp/services/vendor_service.py::_compact_response()→PerkInfo": "商人**菜单**的 perk 摘要：这里刻意只留名字 + [PvE]/[PvP] 标记（不是真 perk 身份，plug_hash 都被置 0）",
    "destiny_mcp/services/weapon_profile.py::socket_options()→_plug_option": "定义级池子不带图标（P6 体积口径，与 `_plug_option` 同一条）",
    # ── ④ 求解器内部模型：不是出口（`build_projection` 就是把这些投影掉的）──
    # `TuningChoice`/`PieceTuning` 是护甲求解器内部的行；默认出口走候选行投影，
    # 要执行走 `execution_id` → `canonical_build`。它们不该各自长成一个图标字段。
    "destiny_mcp/build/tuning.py::_catalog()→TuningChoice": "求解器内部模型（`build_projection` 会投影掉）",
    "destiny_mcp/build/tuning.py::piece_tuning()→PieceTuning": "求解器内部模型（同上）",
    # ── ③ 收藏品：不在"武器/装备/perk"三类的口径里 ─────────────────────────
    "destiny_mcp/services/collection_service.py::get_collectible_item_status": "收藏品（徽章/机灵外壳等）不属于武器/护甲/perk",
    "destiny_mcp/services/collection_service.py::get_collectible_node_status": "同上（节点视图）",
}


def _modules() -> list[tuple[str, Path]]:
    return [
        (f"destiny_mcp/{path.relative_to(SOURCE_ROOT).as_posix()}", path)
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _functions(tree: ast.AST) -> dict[int, str]:
    """节点 id → 最近的函数名（没有函数包裹就给 `?`）。"""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner[id(child)] = node.name
    return owner


def _dict_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


# ── ① 单一出处 ────────────────────────────────────────────────────────────


def _concats_bungie_origin(tree: ast.AST) -> list[ast.AST]:
    """所有"把 Bungie 源站字面量与非字面量拼起来"的节点。"""
    hits: list[ast.AST] = []
    for node in ast.walk(tree):
        pieces: list[ast.AST] = []
        if isinstance(node, ast.JoinedStr):
            pieces = list(node.values)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            pieces = [node.left, node.right]
        else:
            continue
        has_literal = any(
            isinstance(p, ast.Constant)
            and isinstance(p.value, str)
            and "bungie.net" in p.value
            for p in pieces
        )
        has_dynamic = any(not isinstance(p, ast.Constant) for p in pieces)
        if has_literal and has_dynamic:
            hits.append(node)
    return hits


def _bound_to_icon(module_tree: ast.AST, node: ast.AST) -> bool:
    """这个拼接表达式是不是"当图标在用"（赋给 icon* 名字 / 当 icon* 键或关键字实参）。"""
    for parent in ast.walk(module_tree):
        for field, value in ast.iter_fields(parent):
            children = value if isinstance(value, list) else [value]
            if node not in children:
                continue
            if isinstance(parent, ast.Dict):
                for key, item in zip(parent.keys, parent.values):
                    if item is node and isinstance(key, ast.Constant) and "icon" in str(key.value).lower():
                        return True
            if isinstance(parent, ast.keyword) and "icon" in (parent.arg or "").lower():
                return True
            if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if any("icon" in name.lower() for name in names):
                    return True
            # 走到语句这一层就够了：再往上只会看到 If/FunctionDef
            del field
    return False


def test_no_second_icon_url_constructor() -> None:
    """图标 URL 只能由 `utils/icons.py` 构造（键名与归一都只有一份）。"""
    offenders: list[str] = []
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _concats_bungie_origin(tree):
            if _bound_to_icon(tree, node):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "这些地方自己拼了 Bungie 图标 URL；改用 destiny_mcp/utils/icons.py 的 "
        f"`icon_url()`（唯一构造点）：{offenders}"
    )


def test_the_icon_source_is_the_only_place_with_the_origin_literal() -> None:
    """源站字面量本身也只有一份：`manifest_data` 只能再导出，不许再抄一遍。"""
    literal = f'"{BUNGIE_ORIGIN}"'
    holders = [
        rel
        for rel, path in _modules()
        if literal in path.read_text(encoding="utf-8")
    ]
    assert holders == [ICON_SOURCE], (
        f"`{BUNGIE_ORIGIN}` 的字面量只许出现在 {ICON_SOURCE}（manifest_data 走再导出）：{holders}"
    )


# ── ② 身份行必须带 icon_url ───────────────────────────────────────────────


def _keyword_identity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """`Model(item_hash=…, name=…)` 这种**构造调用**：关键字实参里同时有名字键与身份 hash 键。

    为什么单列一类：`LoadoutItem` / `PlugOption` / `VendorCost` 这些行不是字典字面量拼的，
    只扫 `ast.Dict` 会整片漏掉（2026-10-04 注入验证时发现的：`build_results` 里的
    `LoadoutItem(icon_url=armor.icon_url)` 拿掉后守门没红）。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        if not ((kwargs & _NAME_KEYS) and (kwargs & _HASH_KEYS)):
            continue
        callee = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else "?"
        )
        rows.append((f"{owner.get(id(node), '?')}()→{callee}", node.lineno, kwargs))
    return rows


def _identity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """(kind, lineno, keys)：三种身份行 —— 字典字面量、键清单常量、构造调用。"""
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = list(_keyword_identity_rows(tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = _dict_keys(node)
            if (keys & _NAME_KEYS) and (keys & _HASH_KEYS):
                rows.append((owner.get(id(node), "?"), node.lineno, keys))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                keys = {
                    e.value
                    for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
                if (keys & _NAME_KEYS) and (keys & _HASH_KEYS):
                    rows.append((target.id, node.lineno, keys))
    return rows


def _scan_identity_rows() -> tuple[list[str], list[str]]:
    """返回 (缺失清单, 过期台账条目)。"""
    missing: list[str] = []
    seen: set[str] = set()
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, lineno, keys in _identity_rows(tree):
            if keys & _ICON_KEYS:
                continue
            key = f"{rel}::{kind}"
            seen.add(key)
            if key in _EXEMPT:
                continue
            missing.append(f"{rel}:{lineno} {kind} {sorted(keys & (_NAME_KEYS | _HASH_KEYS))}")
    stale = [key for key in _EXEMPT if key not in seen]
    return missing, stale


def test_identity_rows_carry_icon_url() -> None:
    """武器/装备/perk 的身份行必须带 `icon_url`，否则卡片只能放色块。"""
    missing, _ = _scan_identity_rows()
    assert not missing, (
        "这些身份行没有 `icon_url`；补上（值走 utils/icons.py 的 icon_url()），"
        "确实不该给的登记进本文件的 _EXEMPT 并写明理由：\n  " + "\n  ".join(missing)
    )

def test_icon_exemption_ledger_is_not_stale() -> None:
    """台账里过期（代码已经补上图标或删掉）的条目要跟着删，否则它会掩盖新问题。"""
    _, stale = _scan_identity_rows()
    assert not stale, (
        "这些豁免条目在代码里已经不存在或已经带上 icon_url 了，从 _EXEMPT 里删掉："
        f"{stale}"
    )


# ── ③ 真实基线里的图标形状（数据面，不是代码面）────────────────────────────


def _baseline_files() -> list[Path]:
    root = Path(__file__).resolve().parent / "baselines"
    return sorted((root / "weapon_responses").glob("*.json")) + sorted(
        (root / "armor_responses").glob("*.json")
    )


def test_recorded_baselines_use_openable_bungie_icon_urls() -> None:
    """真机录下来的基线里，每个非空 `icon_url` 都要是**打得开**的 Bungie 图标地址。

    这是"形状对不对"的第二条证据：代码面（前面两条）证明每个出口都在构造图标，
    数据面证明构造出来的确实是 `https://www.bungie.net/common/destiny2_content/icons/…`。
    """
    import json

    files = _baseline_files()
    assert files, "基线夹具不见了；这张网就白织了"

    bad: list[str] = []
    checked = 0

    def walk(node: object, where: str) -> None:
        nonlocal checked
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "icon_url":
                    checked += 1
                    if value and not is_bungie_icon(value):
                        bad.append(f"{where}: {value!r}")
                    continue
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")

    for path in files:
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)

    assert checked > 0, "基线里一个 icon_url 都没有？夹具或扫描坏了"
    assert not bad, f"这些 icon_url 打不开（形状/前缀不对）：{bad[:10]}（共 {len(bad)}）"

