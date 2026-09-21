"""Plug 定义查询：护甲模组与子职业两条链共用。

只依赖 ManifestManager，不认识装备流程，放在两者下面，避免两条链互相引用。
"""

from __future__ import annotations

from ..utils.hash_utils import to_unsigned


class PlugLookupMixin:
    """按 plug hash 读类别、读插入条件；子类通过 self._manifest 取定义。"""

    def _plug_category_hash(self, plug_hash: int) -> int:
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            definition = {}
        if not (definition.get("plug") or {}):
            summary = self._manifest.get_item_info(plug_hash)
            definition = summary if isinstance(summary, dict) else {}
        category = (definition.get("plug") or {}).get("plugCategoryHash", 0)
        return category if isinstance(category, int) else 0

    # ── 插入条件与"这一位能不能插" ────────────────────────────────────

    def plug_insertion_conditions(self, plug_hash: int) -> list[str]:
        """这颗 plug 的插入条件（Manifest 官方中文）。

        条件不满足时上游回 1676 `DestinyFailedPlugInsertionRules`，而错误体里**不带**
        是哪一条没过（`message_data` 是空的）。`plug.insertionRules[].failureMessage`
        是唯一拿得到的中文说法 —— 实测被 1676 拒掉的那几颗，条件里都有
        「必须在赛季神器中选择」。

        给的是**全部**条件，不是"没过的那条"：哪条没过我们没测过，不能替上游下结论。
        """
        definition = self._manifest.get_item_definition(plug_hash)
        if not isinstance(definition, dict):
            return []
        rules = ((definition.get("plug") or {}).get("insertionRules")) or []
        return [
            message
            for message in (
                (rule or {}).get("failureMessage") for rule in rules
                if isinstance(rule, dict)
            )
            if message
        ]

    @staticmethod
    def insertable_plugs(profile: dict, character_id: str) -> dict[int, set[int]]:
        """profile → `{plugSetHash: 这一位角色能插入的 plugHash}`。

        上游把"实际能插的 plug"放在 `characterPlugSets`（组件 207，**随 305 一起回来**，
        不用单独请求）；profile 级的 `profilePlugSets` 是更小的一份，两个都并 ——
        真机实测头盔 plug set 有 61 颗，这一位只能插 21 颗，差的就是没解锁的。

        为什么按**角色**读：解锁跟着角色走（赛季神器在角色身上），profile 级那份代替不了。

        为什么是"缺键 = 不出现"而不是给空集合：上游没给这个 plug set 时，调用方必须退回
        "不判断"；把"没数据"读成"不允许"会把能装的模组判成装不了（本项目的老毛病）。

        **集合里的 hash 一律转无符号**：上游 profile 给的 plugHash 是无符号的，而
        `manifest.search` 给的是**有符号**的（实测 `复原` = -207911122 vs 4087056174）。
        在这里统一口径，调用方就不必各自记得转换（第一版没转，凡是负数 hash 的模组全被判成
        "没解锁" —— 真机上 `复原`、`特殊终结技` 这些**正装着的**模组都中招）。
        """
        pools: dict[int, set[int]] = {}
        for key, scope in (("profilePlugSets", None), ("characterPlugSets", character_id)):
            data = (profile.get(key) or {}).get("data") or {}
            if scope is not None:
                data = data.get(scope) or {}
            for plug_set_hash, items in (data.get("plugs") or {}).items():
                pools.setdefault(int(plug_set_hash), set()).update(
                    to_unsigned(int((row or {}).get("plugItemHash", 0)))
                    for row in (items or [])
                )
        return pools

    @staticmethod
    def socket_plug_sets(item_definition: dict | None, socket_index: int) -> list[int]:
        """`socketEntries[i]` 声明的 plug set（reusable / randomized），没有给空表。"""
        entries = ((item_definition or {}).get("sockets") or {}).get("socketEntries") or []
        if socket_index < 0 or socket_index >= len(entries):
            return []
        return [
            int(plug_set_hash)
            for plug_set_hash in (
                entries[socket_index].get("reusablePlugSetHash", 0),
                entries[socket_index].get("randomizedPlugSetHash", 0),
            )
            if plug_set_hash
        ]

    def plug_is_insertable(
        self,
        pools: dict[int, set[int]],
        item_definition: dict | None,
        socket_index: int,
        plug_hash: int,
        current_plug_hash: int = 0,
    ) -> bool | None:
        """这一位现在能不能往这个槽插这颗 —— `None` = 上游没给这个槽的 plug set。

        **已经装在这个槽里的那颗永远算能插**：上游那份清单会漏（实测调谐槽里正装着的
        那颗就不在清单里），而"重新插回原值"是游戏里一定允许的动作。

        两边都比**无符号**：调用方给的 `plug_hash` 多来自 `manifest.search`（有符号），
        `current_plug_hash` 来自 profile（无符号），直接比永远不相等。
        `pools` 的键值由 `insertable_plugs` 统一成无符号，这里**不再重复转换一遍**
        （一个事实只写一次；手工拼 pool 的调用方要自己保证是无符号的）。
        """
        if plug_hash and to_unsigned(plug_hash) == to_unsigned(current_plug_hash or 0):
            return True
        known = False
        target = to_unsigned(plug_hash)
        for plug_set_hash in self.socket_plug_sets(item_definition, socket_index):
            pool = pools.get(plug_set_hash)
            if pool is None:
                continue
            known = True
            if target in pool:
                return True
        return False if known else None
