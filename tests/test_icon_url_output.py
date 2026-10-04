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
3. **活动身份行另算一套**（`test_activity_rows_carry_icon_url`）：带 `activity_name`/`activity`
   的行（活动、副本、PvP 场次）也必须带 `icon_url`。**为什么单列**：那类图标不是物品图，
   走的是另一条道（`pgcrImage`），用物品道的判据扫不到、也守不住 —— 2026-10-04 的缺口
   就是"物品全对、活动全裂"（见 `docs/plans/ICON_URL_PLAN.md` §八）。
4. **活动道只能有一个出处**（`test_activity_images_come_from_the_activity_lane`）：
   全仓只有 `manifest_lookup` 读 `pgcrImage`。

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

from destiny_mcp.manifest_lookup import DefinitionLookupMixin
from destiny_mcp.utils.icons import (
    BUNGIE_ORIGIN,
    is_bungie_activity_image,
    is_bungie_icon,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "destiny_mcp"

#: 唯一允许构造图标 URL 的模块（`BUNGIE_ORIGIN` / `icon_url()` 的出处）
ICON_SOURCE = "destiny_mcp/utils/icons.py"

#: 名字键：一行"这是什么"靠它
_NAME_KEYS = {"name", "plug_name", "item_name", "set_name", "perk_name"}
#: 身份 hash 键：一行"是哪一件"靠它。刻意**不含**裸 `hash`/`record_hash`/`tier_hash`
#: —— 那些在套装层级、记录、档位表里到处都是，收进来会把台账冲成噪声。
_HASH_KEYS = {"item_hash", "plug_hash", "itemHash", "plugItemHash", "set_hash"}
#: 活动/副本身份行的键（与物品那套**分开**：它们的图标走另一条道，见模块开头第 3 条）
_ACTIVITY_NAME_KEYS = {"activity_name", "activity"}
_ICON_KEYS = {"icon_url", "iconUrl"}

# ── 豁免台账 ──────────────────────────────────────────────────────────────
# 键 = `相对路径::函数名`（键清单常量用 `相对路径::常量名`）。每条都要写**为什么**：
# 理由必须能被核对 —— 写清"是什么东西、凭什么不给图、代价是多少"，不许写"忘了/以后再说"。
#
# ⚠️ **两类"不给图"不属于本台账**（它们形状上就扫不到，理由在这里写死）：
#
# ① **定义级 perk 池**（`weapon_profile` 的 socket options `scope="definition"`）：
#    带上 `icon_url`+`description` = **+17 KB/把（图标 13.2 + 描述 4.0，P6 实测）**
#    （`docs/plans/WEAPON_FORMAT_PLAN.md:360`）。它是"**可能滚到什么**"、不是"你有这件"；
#    要图/要效果走实例级 options 与 `perk_description`。正面钉在
#    `tests/test_perk_icon_output.py::test_perk_pool_includes_weapon_and_perk_icons_with_description_fallback`。
# ② **`duplicates` 的 perk 行**（`inventory_analysis_service._perk_row` → `{name, slot}`）：
#    真机基线每实例 1.81 KB，其中 **`perks` 1.64 KB = 91%**，而 `limit=5` 整包有 **20 KB 闸**
#    （`docs/adr/021-default-exit-gives-rows-and-references.md:15`、
#    `docs/plans/RESPONSE_PROJECTION_PLAN.md:12`）—— 补图就把闸顶破。
#    它压成 `{name, slot}` 是**取舍过的**（同一份 docstring 里写着为什么不能只留 id/光等）。
#    正面钉在 `test_duplicates_perk_rows_stay_lean_by_volume`。
#
# 台账里剩下的每一条都是"这一行**不是出口**（中间产物/回滚快照/求解器内部模型）"，
# 或者"它本来就不是物品"（套装行、收藏品）。
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
    "destiny_mcp/services/weapon_local_data.py::_popularity_summary": "定义级 perk 行不带图标（与同 intent 的选取率服务统一，体积口径）",
    "destiny_mcp/services/weapon_popularity_service.py::get_weapon_popularity": "同上：`popular_combinations[].perks[]` 与 `perk_columns` 是同一批定义级 perk 行",
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


#: 活动道的字段名（`DestinyActivityDefinition.pgcrImage`）。全仓只有一处能读它。
ACTIVITY_IMAGE_FIELD = "pgcrImage"
#: 活动道的**唯一出处**
ACTIVITY_LANE = "destiny_mcp/manifest_lookup.py"


def _code_mentions(path: Path, field: str) -> bool:
    """文件里有没有把 `field` 当**字段名**用（字符串字面量），**不算文档字符串**。

    为什么要排掉 docstring：字段名写进注释/docstring 是"说明"，写进代码才是"读它"。
    这一条是给 `test_activity_images_come_from_the_activity_lane` 用的 —— 它要抓的是
    "第二个地方自己读了活动图字段"，不是"第二份文档提到了它"。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return any(
        isinstance(node, ast.Constant)
        and node.value == field
        and id(node) not in docstrings
        for node in ast.walk(tree)
    )


def test_activity_images_come_from_the_activity_lane() -> None:
    """活动/副本的图只能从活动道取：全仓只有 `manifest_lookup` 读 `pgcrImage`。

    为什么值得一条守卫：把活动 hash 塞进物品道（`get_item_info(activity_hash)`）**不会报错**，
    只会一路返回空串 —— 症状是"物品图全对、活动全裂"，而裂的那半边没有任何报错可查
    （`docs/plans/ICON_URL_PLAN.md` §八）。所以这里钉住"活动图字段只有一个读者"。
    """
    holders = [
        rel for rel, path in _modules() if _code_mentions(path, ACTIVITY_IMAGE_FIELD)
    ]
    assert holders == [ACTIVITY_LANE], (
        f"`{ACTIVITY_IMAGE_FIELD}` 只许在 {ACTIVITY_LANE} 里读（活动道的唯一出处）：{holders}"
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


# ── ②b 活动/副本身份行（另一条道，另一套键）───────────────────────────────


def _activity_rows(tree: ast.AST) -> list[tuple[str, int, set[str]]]:
    """带活动名字键的行：字典字面量、`Model(activity_name=…)` 构造调用。

    判据刻意**宽**：只要有 `activity_name`/`activity` 就算活动身份行，不要求它同时带 hash ——
    `history` 的行以前就只有名字没有 hash，正是这样漏掉的（补 hash 是这一步的副产品）。
    """
    owner = _functions(tree)
    rows: list[tuple[str, int, set[str]]] = []
    for node in ast.walk(tree):
        keys: set[str] | None = None
        if isinstance(node, ast.Dict):
            keys = _dict_keys(node)
        elif isinstance(node, ast.Call):
            keys = {kw.arg for kw in node.keywords if kw.arg}
        if keys and (keys & _ACTIVITY_NAME_KEYS):
            rows.append((owner.get(id(node), "?"), node.lineno, keys))
    return rows


def _scan_activity_rows() -> list[str]:
    """缺 `icon_url` 的活动身份行（没有台账：活动行一律要有图，不存在"该不给"的）。"""
    missing: list[str] = []
    for rel, path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, lineno, keys in _activity_rows(tree):
            if keys & _ICON_KEYS:
                continue
            missing.append(f"{rel}:{lineno} {kind} {sorted(keys & _ACTIVITY_NAME_KEYS)}")
    return missing


def test_activity_rows_carry_icon_url() -> None:
    """活动/副本身份行必须带 `icon_url`（`docs/plans/ICON_URL_PLAN.md` §八 的缺口）。

    覆盖的出口：`history` / `pgcr` / `aggregate`（`activity_service`）、`raid_report`
    （`raid_report_service` 的行）、`rotations` 的两个活动行（`rotation_service`）、
    `pvp_weapons`（`pvp_match_tally.activity_rows`）。**没有豁免台账**：
    每一行都是一个真实的副本/活动，给不出图就是漏了。
    """
    missing = _scan_activity_rows()
    assert not missing, (
        "这些活动身份行没有 `icon_url`；走活动道补上"
        "（`manifest.get_icon_url(activity_hash=…)`，别再拼 URL、也别用物品道）：\n  "
        + "\n  ".join(missing)
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


# ── ④ 出口函数内部的两条道（行为面）───────────────────────────────────────


class _LaneProbe(DefinitionLookupMixin):
    """只实现两条道各自要用的查表：**走错道就一定露馅**（另一张表根本没实现）。"""

    def __init__(self, activity: dict | None, item: dict | None) -> None:
        self.activity = activity
        self.item = item
        self.tables: list[str] = []

    def get_definition(self, table: str, hash_id: int) -> dict | None:
        assert table == "DestinyActivityDefinition", f"活动 hash 走进了 {table}（不是活动道）"
        self.tables.append(table)
        return self.activity

    def get_item_info(self, item_hash: int) -> dict | None:
        self.tables.append("item")
        return self.item


def test_the_two_lanes_are_dispatched_by_definition_table() -> None:
    """`get_icon_url` 内部两条道：`item_hash=` 查物品表、`activity_hash=` 查活动表。

    这是"共用一个出口函数、内部两条道"的行为面证据 —— 静态守门只能证明"没人绕过出口"，
    证明不了"出口自己分得对"。三者一起才是完整的：

    1. `test_no_second_icon_url_constructor`：没人自己拼 URL；
    2. `test_activity_images_come_from_the_activity_lane`：活动字段只有一个读者；
    3. 本条：出口按**表**分道（活动 hash 去物品表 → `_LaneProbe` 直接断言失败）。
    """
    probe = _LaneProbe(
        activity={"pgcrImage": "/img/destiny_content/pgcr/raid_kings_fall.jpg",
                  "displayProperties": {"icon": "/common/destiny2_content/icons/raid.png"}},
        item={"icon": f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/weapon.jpg"},
    )

    # 活动道：优先 `pgcrImage`（副本卡片要的是那张横幅，不是通用图标）
    icon = probe.get_icon_url(activity_hash=123)
    assert icon == f"{BUNGIE_ORIGIN}/img/destiny_content/pgcr/raid_kings_fall.jpg"
    assert is_bungie_activity_image(icon), "活动图的形状不对（前缀/源站）"
    assert probe.tables == ["DestinyActivityDefinition"]

    # 物品道：查物品表，**一次活动表都不查**
    probe.tables.clear()
    assert probe.get_icon_url(item_hash=456) == f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/weapon.jpg"
    assert probe.tables == ["item"]

    # 占位横幅不算图：往下退到 displayProperties.icon（PvP 活动全是占位，见 manifest_lookup）
    placeholder = _LaneProbe(
        activity={"pgcrImage": "/img/theme/destiny/bgs/pgcrs/placeholder.jpg",
                  "displayProperties": {"icon": "/common/destiny2_content/icons/crucible.png"}},
        item=None,
    )
    assert placeholder.get_icon_url(activity_hash=1).endswith("/common/destiny2_content/icons/crucible.png")

    # 查不到定义 / 两张图都是哨兵 → 空串（缺值不编地址）
    assert _LaneProbe(activity=None, item=None).get_icon_url(activity_hash=1) == ""
    sentinels = _LaneProbe(
        activity={"pgcrImage": "/img/theme/destiny/bgs/pgcrs/placeholder.jpg",
                  "displayProperties": {"icon": "/img/misc/missing_icon_d2.png"}},
        item=None,
    )
    assert sentinels.get_icon_url(activity_hash=1) == ""
    assert _LaneProbe(activity=None, item=None).get_icon_url() == ""


# ── ⑤ "同一 intent 两条路一致"（体积口径，不是忘了）────────────────────────


def test_popularity_paths_agree_on_perk_rows() -> None:
    """同一个 `popularity` intent 的两条路，perk 行必须是**同一套键**。

    两条路：`weapon_popularity_service._enrich_entry`（第三方选取率快照，`data.popularity`）
    与 `weapon_local_data._popularity_summary`（本地归档摘要，挂在 `weapon.sources.popularity`）。
    2026-10-04 之前一条带 `icon_url`、一条不带 —— 同一句话问两次拿到两种行形状，
    这是**真不一致**（不是有意为之）。口径统一成"定义级 perk 行不带图"，
    这条守门就是钉住"下次别再漂"。
    """
    from destiny_mcp.services import weapon_local_data
    from destiny_mcp.services.weapon_popularity_service import WeaponPopularityService

    warnings: list[str] = []
    enriched = WeaponPopularityService._enrich_entry(
        "狂暴",
        [{"itemHash": 300, "name": "狂暴",
          "icon": f"{BUNGIE_ORIGIN}/common/destiny2_content/icons/perk.png"}],
        warnings,
        selection_rate=12.5,
    )
    summary = weapon_local_data._popularity_summary(
        {
            "perk_columns": [
                {"label": "特性 1",
                 "items": [{"name": "狂暴", "plug_hash": 300, "selection_rate": 12.5}]}
            ],
            "popular_combinations": [{"perks": [{"name": "狂暴", "plug_hash": 300}],
                                      "selection_rate": 30.0}],
            "source": {"kind": "fixture"},
        },
        {300: {"name": "狂暴"}},
    )
    row = summary["columns"][0]["items"][0]

    assert set(row) == set(enriched) == {"name", "plug_hash", "selection_rate"}, (
        "同一个 popularity intent 的两条路 perk 行形状不一致："
        f"本地摘要={sorted(row)}、选取率快照={sorted(enriched)}"
    )
    assert not warnings, f"唯一命中不该有 warning（别为了少个字段顺手加话术）：{warnings}"


def test_duplicates_perk_rows_stay_lean_by_volume() -> None:
    """`duplicates` 的 perk 行保持 `{name, slot}`：**体积口径，不是忘了**。

    真机基线每实例 1.81 KB，其中 `perks` **1.64 KB（91%）**；`limit=5` 的整包有 20 KB 闸
    （`docs/adr/021-default-exit-gives-rows-and-references.md:15`、
    `docs/plans/RESPONSE_PROJECTION_PLAN.md:12`）。补 `icon_url` 就是把闸顶破，
    所以这里正面钉住"它就是两个键" —— 要加图先回答体积账。
    组级 `icon_url` **有**（每组一个 URL，5 组约 0.5 KB），不受这条约束。
    """
    from destiny_mcp.services import inventory_analysis_service as analysis

    row = analysis._perk_row({"name": "狂暴", "plug_category": "v400.plugs.weapons.rarity.tier1",
                              "plug_hash": 300, "icon_url": f"{BUNGIE_ORIGIN}/x.png"})
    assert set(row) == {"name", "slot"}, f"duplicates 的 perk 行变胖了：{sorted(row)}"

    groups = [{"item_hash": 1, "name": "测试武器", "icon_url": "/common/destiny2_content/icons/w.png",
               "instances": [{"instance_id": "9", "perks": [
                   {"name": "狂暴", "plug_category": "v400.plugs.weapons.rarity.tier1"}]}]}]
    group = analysis.duplicate_rows(groups)[0]
    assert set(group["instances"][0]["perks"][0]) == {"name", "slot"}
    assert group["icon_url"].startswith(BUNGIE_ORIGIN), "组级图标必须在（每组一个 URL，不占体积）"
