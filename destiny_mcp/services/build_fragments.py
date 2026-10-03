"""碎片配置替换：把点名的碎片落到当前子职业的插槽上（写入前的兼容性预检）。

从 `build_service` 抽出来（那边贴着 `tests/test_module_size_ratchet` 的上限）：
这一块读 Manifest 插槽定义、逐槽判"这颗能不能插进来"、并保留原有插槽下标，
与求解/排名/候选签发无关 —— 拆开也正好让 hash 归一的修复有个不挤的地方。

**槽号与"哪个槽能写"都来自现场**：能排的碎片槽由 `services/fragment_sockets` 按账号
现报的 `isEnabled` 算出来（判据链见 `loadout_plug_lookup.socket_is_assignable`）。
棱镜术士 6 个碎片槽里 socket 14 是禁用的，第 6 颗排进去上游回
500 `DestinySocketActionNotAllowed`。
"""

from __future__ import annotations

from ..exceptions import BuildValidationError
from ..manifest import ManifestManager
from ..models import LoadoutSubclassConfig
from ..utils.hash_utils import hash_variants, to_unsigned
from .fragment_sockets import fragment_slot_indices, fragment_slot_report


def replace_fragment_config(
    manifest: ManifestManager,
    current: LoadoutSubclassConfig | None,
    fragment_hashes: list[int],
) -> LoadoutSubclassConfig:
    """Replace a complete fragment set while preserving exact socket indices."""
    if current is None or not current.subclass_item_hash:
        raise BuildValidationError("无法读取当前子职业，不能安全应用碎片。")

    # 请求里的碎片 hash 来自 `manifest.search()`（库里的 `id` 列是**有符号**，「保护琢面」
    # = -1668045176），插槽的 `singleInitialItemHash` / `reusablePlugItems` 在物品 JSON 体内
    # （**无符号**，同一个碎片 = 2626922120）：不归一就只有 hash < 2^31 的碎片碰巧能过 ——
    # 真机 2026-10-03 的后果是社区配装 5 颗里 4 颗（保护/黎明/勇气/希望）被判"与插槽不兼容"，
    # 能过的恰好都是使命(124726498)/毁灭(124726499) 这类小 hash。两套值域这个病本月第三次
    # 咬人（前两次：配装规模闸门的指定金装、更早的 DIM 愿望单查表），说明"比较前先归一"
    # 不能靠人记 —— 集合式比较走 `hash_variants`，写回去的 hash 统一归到**无符号**：
    # 账号侧那份就是无符号（`read_subclass_config` 直读 profile），
    # 而写完的回读核对（`_verify_loadout`）是拿插槽裸值逐位比的。
    requested = [to_unsigned(int(h)) for h in fragment_hashes]
    # 能排的槽只有现场报"开着"的那些碎片槽（判据在 `fragment_sockets`）。
    # 禁用槽根本不在 `current.plug_sockets` 里：读取时就按 `isEnabled` 滤掉了，所以
    # "第 N 颗进第 N 个碎片槽"这个位置假设在这里已经不成立 —— 真机棱镜术士的 6 个碎片槽里
    # socket 14 是禁用的，旧写法把第 6 颗排进 14，上游回 500 `DestinySocketActionNotAllowed`。
    fragment_indices = fragment_slot_indices(current)
    if len(requested) != len(fragment_indices):
        # 报错必须**可操作**：说清能写几颗、收到几颗、差几颗，以及这些槽分别是哪些、
        # 哪个空着、哪个被禁用 —— 否则调用方只能自己再读一次账号去猜。
        # 缺的那颗**不替玩家补**：补哪一颗是构筑选择，不在这里决定。
        raise BuildValidationError(
            "碎片配置对不上："
            f"当前子职业能写 {len(fragment_indices)} 颗，收到 {len(requested)} 颗"
            f"（{'多' if len(requested) > len(fragment_indices) else '少'}"
            f" {abs(len(requested) - len(fragment_indices))} 颗）。"
            f"{fragment_slot_report(manifest, current)}。"
            "要装就得给**正好这么多颗**碎片（按顺序填进上面这些槽）；"
            "少的那几颗补哪一颗由你定，这里不替你选。"
        )

    subclass_definition = manifest.get_item_definition(current.subclass_item_hash) or {}
    socket_entries = (subclass_definition.get("sockets") or {}).get("socketEntries", [])
    updated_sockets = dict(current.plug_sockets)
    for socket_index, plug_hash in zip(fragment_indices, requested):
        if socket_index >= len(socket_entries):
            raise BuildValidationError("子职业碎片插槽定义已变化，请刷新 Manifest。")
        entry = socket_entries[socket_index]
        # 两边都可能拿另一套写法：`hash_variants` 把这一颗的两种写法都算上，
        # 于是"谁给的有符号、谁给的无符号"都不影响判定。
        variants = hash_variants(plug_hash)
        accepted = entry.get("singleInitialItemHash", 0) in variants
        for plug_set_hash in {
            entry.get("reusablePlugSetHash", 0),
            entry.get("randomizedPlugSetHash", 0),
        }:
            if not plug_set_hash:
                continue
            plug_set = manifest.get_definition(
                "DestinyPlugSetDefinition", plug_set_hash
            ) or {}
            if any(
                item.get("plugItemHash", 0) in variants
                for item in plug_set.get("reusablePlugItems", [])
            ):
                accepted = True
                break
        if not accepted:
            raise BuildValidationError(
                f"碎片 {plug_hash} 与当前子职业插槽 {socket_index} 不兼容。"
            )
        updated_sockets[socket_index] = plug_hash

    return current.model_copy(update={
        "fragment_hashes": requested,
        "plug_sockets": updated_sockets,
    })
