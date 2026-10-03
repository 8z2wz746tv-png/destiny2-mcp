"""模组写入的两趟：**先守门（换装前）、再规划（换装后）** —— 守门这一半在这里。

为什么要单独一个模块：`loadout_mod_sockets` 贴着体量上限，而这两趟的语义本来就不一样 ——
守门是"注定失败就别写"，规划是"照着现场排写入"。混在一起时最容易发生的事就是有人顺手
让**写入那一趟**复用守门那份快照（换装**之前**的现场），于是按过期的插槽号去写新现场。

以 mixin 挂在 `LoadoutEquipmentService` 上（`self` 上就有 `insertable_plugs` /
`_prepare_mod_operations`），调用点不用改。
"""

from __future__ import annotations

from ..exceptions import TransferError
from ..models import Loadout


class ModPreflightMixin:
    """"这批模组写得成吗"的只读预检。"""

    # ── 模组写入的两趟：先守门（换装前）、再规划（换装后） ──────────────

    def _mod_write_snapshot(
        self, profile: dict, char_id: str
    ) -> tuple[dict[str, list[dict]], dict, dict[int, set[int]]]:
        """一份 profile → 写入要用的三样：插槽缓存、实例（能量）、这一位能插的 plug。

        合成一处是因为守门与规划各要一份，而**口径必须一模一样**：各写一遍就会出现
        "预检放行的件、规划时按另一套判据被拦"，那种不一致最难查。
        """
        item_components = profile.get("itemComponents", {})
        sockets_cache = {
            instance_id: payload.get("sockets", [])
            for instance_id, payload in (
                item_components.get("sockets", {}).get("data", {})
            ).items()
        }
        # "这一位角色实际能插哪些 plug"（组件 207，随 305 一起回来）。不给就等于不判断 ——
        # 上游没给这份数据时，"装不了"与"没查到"必须分开（本项目的老毛病）。
        return (
            sockets_cache,
            item_components.get("instances", {}).get("data", {}),
            self.insertable_plugs(profile, char_id),
        )

    async def _mod_preflight(
        self,
        loadout: Loadout,
        membership_id: str,
        membership_type: int,
        profile: dict,
        char_id: str,
    ) -> tuple[bool, str]:
        """把"这批模组写得成吗"在**任何写入之前**判掉：`(能不能继续, 原因)`。

        为什么必须前置（真机 2026-10-03）：预检原本排在搬运+批量装备**之后**，于是预检
        注定失败的批次已经把装备换好了，只能整条回滚 —— 实测一次白烧 3.5 分钟。
        预检要的数据在同一次 profile 里就有，换装前拿得到，所以前置不会变成假阴性 ——
        也正因如此它**直接用这一趟的快照**（组件集够：`EQUIP_LOADOUT ⊇ INVENTORY_SOCKETS`，
        多一个 100，同一趟相隔几秒），而不是为每件护甲各抓一份全量档案（真机 5 件 6.73 秒）。

        只有"确定失败"才算失败：`TransferError`（读不到插槽 / 找不到唯一兼容槽 / 算不出
        能量）。**"这一位装不上某颗"不算** —— 那是 `blocked` 操作，装备照换、如实报
        （ADR-013）；拿它当预检失败会把本来能成的批次拦下来。

        这里的结果**有意丢弃**（换装会改账号，写入必须按新现场重新规划，见 Step 2）：只当守门。
        """
        sockets_cache, instances_data, insertable = self._mod_write_snapshot(profile, char_id)
        try:
            for lo_item in loadout.items:
                if not (
                    lo_item.mods or lo_item.mod_sockets or lo_item.functional_mod_groups
                ) or not lo_item.item_instance_id:
                    continue
                await self._prepare_mod_operations(
                    lo_item,
                    membership_id,
                    membership_type,
                    # 手里这份快照就是现场（理由见 `_mod_preflight`）：传 `{}` 会让每件护甲
                    # 各抓一份全量档案（3.3 MB/件，最坏吃满 8 次重试窗口）—— 真机 6.73 秒的来源。
                    sockets_cache,
                    instances_data,
                    insertable,
                )
        except TransferError as exc:
            return False, str(exc)
        return True, ""
