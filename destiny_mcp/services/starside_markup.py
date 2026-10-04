"""Starside 站点标记的解析器：把 `{perk|槽化枪管}` 这类记号翻成我们自己的说法。

站点文本里混着 41 种 token（真机统计），它们带语义 —— 元素、敌人档位、PvP 专用数值、强化后的
增减、作者自己的"不确定"标记等等。**不许原样回显给玩家**（那是把站点的内部标记当答案），也不许
"看不懂就删"（那是静默丢信息）。规则：

- 认识的 token：按右边的表翻成我们的词（元素走 `vocabulary.py`）；
- `{num|…}`：解包成数字文本（站点会写成 `{num|13514\\\\11384}` 多值、`{num|7087\\\\(级联点)}` 带标签，
  还有 `∞` —— 真机语料挖出来的）；
- `{unsure|…}`：**保留**"作者标注不确定"（3,805 处，抹平就是把不确定说成确定）；
- `{pvp|…}`：标成 PvP 专用（与 PvE 数值**不许混**）；
- 不认识的 token：**原样保留 + 记进 `unknown_tokens()`**，由调用方决定怎么提示。

`\\\\` 是站点的"多选分隔符"（1,826 条物品文本用到），统一翻成 `／`。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from . import starside_icons

#: token → 我们的说法（`None` 表示"直接取花括号里的正文"）。
#: 这份表是**单一出处**：守门测试拿它跟已入库数据里出现过的 token 对账，出现新 token 就红。
TOKENS: dict[str, str | None] = {
    # 引用类
    "perk": None, "art-perk": None, "exotic": None, "spirit": None, "src": None,
    # 元素与状态
    "el-void": None, "el-arc": None, "el-solar": None, "el-stasis": None,
    "el-strand": None, "el-kinetic": None, "el-prismatic": None,
    "deb-void": None, "deb-arc": None, "deb-solar": None, "deb-stasis": None,
    "deb-strand": None, "buff": None, "debuff": None,
    # 敌人与血量档位
    "enemy": None, "bar-red": None, "bar-orange": None, "bar-yellow": None, "health": None,
    # 资源与机制
    "orb": None, "pickup": None, "armor-charge": None, "ammo-heavy": None, "ammo-special": None,
    "cost": None, "cd": None, "slot": None, "stack": None, "note": None, "num": None, "na": None,
    # 需要改写的
    "pvp": "（PvP）", "enh": "强化：", "unsure": "（作者标注：不确定）",
}

TOKEN_PATTERN = re.compile(r"\{([a-zA-Z0-9#_-]+)\|([^{}]*)\}")
_MULTI = "\\\\"  # 站点用两个反斜杠分隔多选

#: 站点文本里的图片写法（`![](icons/xxx.webp)`）。**只认空 alt** —— 站点就是这么写的；
#: 带 alt 的图片不是它的格式，别顺手改（改错了会把正文里的普通 markdown 一起吃掉）。
_ICON_MARKUP = re.compile(r"!\[\]\(([^)]*)\)")


def unknown_tokens(text: str) -> tuple[str, ...]:
    """文本里出现的、`TOKENS` 表里没有的 token（守门与留痕用）。"""
    return tuple(sorted({m.group(1) for m in TOKEN_PATTERN.finditer(text or "") if m.group(1) not in TOKENS}))


def number(value: object) -> tuple[str, bool]:
    """站点的数值写法 → `(文本, 能不能当数值看)`。

    真机语料挖出来的三种写法：`{num|2576}`、`{num|13514\\11384}`（多值，取第一个）、
    `{num|7087\\(级联点)}`（带标签），还有 `∞`（作者就这么写）。**单一出处**：
    语料脚本与响应成形都走这里，别各自再写一份解包。
    """
    text = TOKEN_PATTERN.sub(lambda m: m.group(2) if m.group(1) == "num" else m.group(0), str(value or "")).strip()
    text = text.split(_MULTI)[0].strip()
    text = re.sub(r"（[^）]*）\s*$", "", text).strip()
    if text in ("∞", "-∞"):
        return text, True
    try:
        float(text.replace(",", ""))
    except ValueError:
        return text, False
    return text, True


#: 站点有些引用带武器/变体后缀（`{perk|Suros 协同（铭纹-41）}`）——查名时先去掉再试一次
_VARIANT_SUFFIX = re.compile(r"（[^）]*）\s*$")


def strip_variant_suffix(name: str) -> str:
    """去掉站点引用里的「（武器名/变体）」后缀（单一出处，别在脚本里再抄一份正则）。"""
    return _VARIANT_SUFFIX.sub("", str(name or "")).strip()


def perk_names(text: object) -> tuple[str, ...]:
    """文本里 `{perk|…}` 引用的原始名字（成形层拿它记"我们库里查不到的名字"）。"""
    return tuple(m.group(2).strip() for m in TOKEN_PATTERN.finditer(str(text or "")) if m.group(1) == "perk")


def _clean(body: str) -> str:
    """把站点自己的图标写法补成**归档里的相对路径**（`assets/<主题>/icons/<名>.webp`）。

    以前这里是把 `![](icons/xxx.webp)` **整段抹掉**，注释写的是"那份资源没随归档给我们" ——
    **那句话现在不成立**：图标就在归档里（3767 个，`missing on disk: 0`），抹掉等于把
    "这里本来有张图"这条信息丢掉。改成保留，但**只给相对路径**：
    `https://starside.work/...` 是第三方站点的热链（归档 `redistribution_license:
    not_established`），写进响应就是替别人做分发 —— 理由与判据在 `starside_icons`。

    非 `icons/` 的相对地址（真出现外链之类）仍旧抹掉：那类地址要么是别人的数据、
    要么在归档里没有对应文件，留着只会给前端一个打不开的 src。
    """
    return _ICON_MARKUP.sub(
        lambda m: f"![]({starside_icons.archive_relative_path(m.group(1))})"
        if m.group(1).startswith("icons/")
        else "",
        body,
    ).strip()


def render(text: object, *, names: Callable[[str], str | None] | None = None) -> str:
    """把一段站点文本翻成可读中文；多选分隔成 `／`。

    `names`：`{perk|X}` 这类引用的取名函数（通常是 Manifest 查名）。查不到就**原样保留**——
    库里没有的名字（真机语料实测占 2.4%）不许猜、也不许丢。
    """

    def replace(match: re.Match[str]) -> str:
        token, body = match.group(1), _clean(match.group(2))
        if token not in TOKENS:
            return match.group(0)  # 不认识的：原样留着，调用方用 unknown_tokens() 提示
        if token == "num":
            return body.split(_MULTI)[0].strip() or body
        if token == "na":
            return "—"
        prefix = TOKENS[token]
        resolved = names(body) if (token == "perk" and names) else None
        value = resolved or body
        if resolved and resolved != body:
            value = f"{resolved}"
        return f"{prefix}{value}" if prefix else value

    rendered = TOKEN_PATTERN.sub(replace, str(text or ""))
    return rendered.replace(_MULTI, "／").strip()


def render_all(values: dict[str, object], *, names: Callable[[str], str | None] | None = None) -> dict[str, str]:
    """把一组字段一起翻（空值不进结果）。"""
    out: dict[str, str] = {}
    for key, value in (values or {}).items():
        if value in (None, "", []):
            continue
        text = render(value, names=names)
        if text:
            out[key] = text
    return out


def alternatives(text: object) -> tuple[str, ...]:
    """`{perk|A}\\\\{perk|B}` 这种多选拆成多项（保留原始 token，调用方自己 render）。"""
    parts = [p.strip() for p in str(text or "").split(_MULTI)]
    return tuple(p for p in parts if p)
