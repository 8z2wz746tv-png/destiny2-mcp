"""图标 URL 的**唯一构造点**。

为什么要有这个模块（这笔账要记清楚，不然下次还会有人问"为什么不能直接拼字符串"）：

图标 URL 以前在 12 处各拼一遍（`f"https://www.bungie.net{icon}"`、`_cdn_url()`、
`_absolute_icon_url()`、`BUNGIE_BASE_URL + icon`……），于是同一个字段在四条路上有四种边界行为：
一份把非 Bungie 的绝对 URL 原样放过、一份把它拼成 `https://www.bungie.nethttps://…`、
一份返回空串、还有一份连 `None` 都会拼成 `"https://www.bungie.netNone"`。
**表现不是报错，是"有的出口有图、有的出口没图"** —— 前端只能拿色块兜底。

口径照老 web（`~/项目/Destiny_MCP/webui/api/destiny_icons.py::normalize_destiny_icon_url`）：
键名一律 `icon_url`（perk 与物品**同名**，不另造 `perk_icon`/`image`/`icon`），
值一律是**绝对的 Bungie CDN 地址**，路径前缀 `/common/destiny2_content/icons/`。
差别只有一处：老 web 是**消费侧**的安全过滤器（渲染前把来路不明的 URL 拦掉），
这里是**生产侧**的归一器，所以对"已经绝对的 URL"原样放过而不是判空 ——
本仓既有键的取值不许因为这次收敛而变化。

Manifest 的形状：`displayProperties.icon` 是**路径**（`/common/...icons/x.jpg`），
而 `manifest.get_item_info(hash)["icon"]` 已经是**绝对地址**（在 `manifest_search` 加载时拼好）。
本函数两种都吃，所以调用方不必先分清自己手里是哪一种。

**两条道（2026-10-04 补）**：图标 URL 只有一个**出口函数**
（`manifest_lookup.get_icon_url()`），但它内部必须分两条道查定义 ——

| 道 | 查什么 | 图在哪 |
| --- | --- | --- |
| 物品道 | `DestinyInventoryItemDefinition` | `displayProperties.icon` |
| 活动道 | `DestinyActivityDefinition` | `pgcrImage`（回退 `displayProperties.icon`） |

把活动 hash 硬塞进物品道的结果是"**物品图都对、活动全裂**"——而且裂得很难查
（hash 查不到定义时物品道给空串，看起来就像"这个活动本来就没图"）。这里只做**归一**
（相对路径 → 绝对地址），不含查表；查表与分道在 `manifest_lookup.get_icon_url()`。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

#: Bungie 资源基址。`manifest_data.BUNGIE_BASE_URL` 从这里再导出，全仓只有这一份字面量。
BUNGIE_ORIGIN = "https://www.bungie.net"

#: 物品/Perk 图标在 CDN 上的路径前缀（老 web 的判据，也是"这个 URL 能不能当图标用"的形状）。
ICON_PATH_PREFIX = "/common/destiny2_content/icons/"

#: 活动/副本图的路径前缀（PGCR 横幅）。**这不是同一个形状**：物品图在
#: `/common/destiny2_content/icons/` 下，活动图在 `/img/destiny_content/pgcr/`（全屏横幅）
#: 或 `/img/theme/destiny/bgs/`（PvP 活动的通用横幅）下 —— 拿物品的前缀去判活动图必判假。
#: 所以白名单是**两条**，不是一条放宽的前缀。
ACTIVITY_IMAGE_PREFIXES = ("/img/destiny_content/pgcr/", "/img/theme/destiny/bgs/")

_ABSOLUTE_SCHEMES = ("http://", "https://")


def icon_url(value: Any) -> str:
    """把 Manifest 的图标字段归一成绝对 URL；给不出就给空串（**不猜、不编**）。

    - 空 / `None` / 非字符串 → `""`（"没有图"与"没读到"在 JSON 里都是空串，
      这是老 web 与本仓既有的取值约定，别改成 `null`）；
    - 已经是 `http(s)://…` → 原样返回（本仓既有出口就是这么做的，见模块开头）；
    - 以 `/` 开头 → 补 `BUNGIE_ORIGIN`；
    - 其它（相对路径、`data:`、坏值）→ `""` —— 宁可没图，也不给前端一个打不开的地址。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(_ABSOLUTE_SCHEMES):
        return raw
    if raw.startswith("/"):
        return f"{BUNGIE_ORIGIN}{raw}"
    return ""


def _is_bungie_path(value: Any, prefixes: tuple[str, ...]) -> bool:
    """值的**形状**判据：源站对、无 userinfo、端口默认、路径落在给定的某条前缀下。"""
    try:
        parsed = urlsplit(icon_url(value))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.bungie.net"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and parsed.path.startswith(prefixes)
    )


def is_bungie_icon(value: Any) -> bool:
    """这个值是不是一个"前端真的打得开"的**物品/Perk 图标**地址（供守门测试用，不参与响应构造）。

    判据与老 web 的消费侧过滤器一致：必须是 Bungie 源站、无 userinfo、端口是默认的、
    路径落在图标前缀下。生产侧不拿它过滤（那会改既有取值）—— 它只回答
    "新加的那些 url 形状对不对"，别拿它当出口的开关。
    """
    return _is_bungie_path(value, (ICON_PATH_PREFIX,))


def is_bungie_activity_image(value: Any) -> bool:
    """这个值是不是一个"前端真的打得开"的**活动/副本图**（供守门测试用）。

    与 `is_bungie_icon` 的差别只有路径前缀：活动图不在物品图标目录下。分开两条判据
    而不是合成一条宽前缀，是因为合成之后"物品图对了、活动图全裂"这类错会互相掩盖 ——
    两种图本来就来自 Manifest 的两张表（`DestinyInventoryItemDefinition` /
    `DestinyActivityDefinition`），判据也该是两条。
    """
    return _is_bungie_path(value, ACTIVITY_IMAGE_PREFIXES)


def is_bungie_image(value: Any) -> bool:
    """物品图标或活动图 —— 两者之一即可（汇总判据，别拿它替代上面两条具体的）。"""
    return is_bungie_icon(value) or is_bungie_activity_image(value)
