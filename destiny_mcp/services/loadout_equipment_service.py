"""装备编排的执行段：搬 → 批量装备 → 模组 → 子职业 → 回读核对，按这个次序跑。
各步的判断（怎么挑槽、怎么腾能量、装不上怎么说）都在 `loadout_*.py` 的 mixin 里；
这里只负责**次序**与把结论写进回执 —— 加代码前先问它该不该落在这个文件。
回读核对那一步（Step 4）只做两次调用：判据与话术在 `loadout_verify`（窗口在
`write_readback`），这里不自己措辞、也不自己重读。
"""

from __future__ import annotations

import aiobungie

from ..bungie_client import BungieClient
from ..exceptions import (
    ItemNotFoundError,
    TransferError,
)
from . import profile_components
from ..manifest import ManifestManager
from ..models import (
    Loadout,
    LoadoutOperationResult,
    MoveItemStep,
)
from ..player_resolver import PlayerResolver
from ..services.transfer_service import TransferService
from .account_action_lock import account_action_lock
from .loadout_blocked_mods import BlockedMods
from .loadout_exact_flow import ExactFlowMixin
from .loadout_armor_state import restore_after_equip
from .loadout_functional_mods import FunctionalModMixin
from .loadout_mod_sockets import ModSocketMixin, plug_already_installed
from .loadout_transfer_step import TransferStepMixin
from .loadout_recovery import RecoveryStateMixin
from .loadout_subclass_sockets import SubclassSocketMixin
from . import loadout_verify


class LoadoutEquipmentService(
    ExactFlowMixin, RecoveryStateMixin, ModSocketMixin, FunctionalModMixin,
    SubclassSocketMixin, TransferStepMixin,
):
    """Apply loadout equipment: transfer, equip, mods, subclass config."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver
        self._transfer = TransferService(bungie, manifest, resolver)
        self._equip_lock = account_action_lock(bungie)

    # ── Reading helpers (used by both save and equip) ─────────────────

    async def equip_local(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """装备一套**已存配装**（`equip_loadout`）：与 `equip_build` 同一条恢复路径。

        以前这里直接调 `_equip_local_unlocked`：步骤之间失败时留下"装备换了、模组只写了一半"
        的混合状态，而没有任何恢复点把它还原 —— 同一个动作，`equip_build` 会恢复、
        `equip_loadout` 不会，只因为入口不同（真机 2026-10-03 复测点名了这条差异）。
        """
        result = await self.equip_with_recovery(player_name, loadout)
        # 快照里那份"账号护甲现场"（当时没穿着、被上一次操作改过的件）在这里补还原：
        # 挂最外层是为了上面哪条分支返回都要跑（口径与代价见 loadout_armor_state）
        return await restore_after_equip(self, player_name, loadout, result)

    async def _equip_local_unlocked(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """Equip a local loadout by transferring, then batch-equipping items.

        Also applies saved mod configuration and subclass configuration.
        """
        steps: list[MoveItemStep] = []
        all_ok = True

        p = await self._resolver.resolve_player(player_name)
        mid, mtype = p["membership_id"], p["membership_type"]

        # Find character ID
        profile = await self._resolver.get_profile(mid, mtype, profile_components.EQUIP_LOADOUT)
        chars_data = profile.get("characters", {}).get("data", {})
        char_id = None
        for cid, cinfo in chars_data.items():
            class_type = cinfo.get("classType", -1)
            class_name = {0: "titan", 1: "hunter", 2: "warlock"}.get(class_type, "")
            if class_name == loadout.character:
                char_id = cid
                break

        if not char_id:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"找不到角色 '{loadout.character}'。",
            )

        # Step 0: 模组预检 —— **必须在任何写入之前**。真机 2026-10-03：预检（插槽读不到 /
        # 找不到唯一兼容槽 / 能量腾不出来）排在搬运+装备**之后**，于是注定失败的那一批已经把
        # 装备换好了，只能整条回滚，实测一次白烧 3.5 分钟。预检要的数据（插槽、能量、
        # 可插入清单）在同一次 profile 里就有，换装前拿得到，所以前置不产生假阴性。
        # 守门跑在**副本**上：规划会往 `LoadoutItem.mod_sockets` 记下"挑中的槽位"（回读核对要它），
        # 而这一趟的结果有意丢弃 —— 真机 2026-10-03 那 15 对「同一颗模组既写成功、又被报
        # `插槽 -1` 装不上」就是它留下的槽位被写入那一趟读成了"调用方指定"。
        preflight_ok, preflight_error = await self._mod_preflight(
            loadout.model_copy(deep=True), mid, mtype, profile, char_id
        )
        if not preflight_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=(
                    f"配装 '{loadout.name}' 模组预检失败（{preflight_error}）—— "
                    "这次**没有搬运、没有换装、没有写模组**，账号一个字节都没动。"
                ),
                steps=[MoveItemStep(
                    action="mod_preflight", detail=preflight_error, success=False,
                )],
            )

        # Step 1: 搬运（**顺序**、不是并发；先都搬过来再一起装 —— 原因见 loadout_transfer_step）。
        transfer_steps, transferred_ids, transfers_ok = await self.transfer_loadout_items(
            player_name, loadout
        )
        steps.extend(transfer_steps)
        if not transfers_ok:
            all_ok = False

        if loadout.items and len(transferred_ids) == len(loadout.items):
            try:
                equip_result = await self._transfer.equip_items(
                    player_name, transferred_ids, loadout.character
                )
                equipped = bool(equip_result.get("success"))
                # 失败要说清上游为什么（以前只写"批量装备 N 件物品"，真机排查时只能靠猜）。
                steps.append(MoveItemStep(
                    action="equip_many",
                    detail=(
                        f"批量装备 {len(transferred_ids)} 件物品"
                        if equipped
                        else f"批量装备失败：{equip_result.get('message') or '上游没给原因'}"
                    ),
                    success=equipped,
                ))
                all_ok = all_ok and equipped
            except (ItemNotFoundError, TransferError) as exc:
                steps.append(MoveItemStep(
                    action="error",
                    detail=f"批量装备失败: {exc}",
                    success=False,
                ))
                all_ok = False
        elif loadout.items:
            steps.append(MoveItemStep(
                action="equip_many",
                detail="存在转移失败，未执行批量装备。",
                success=False,
            ))
            all_ok = False

        if not all_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装 '{loadout.name}' 装备阶段失败，未继续修改模组或子职业。",
                steps=steps,
            )

        # Step 2: 按**换装之后**的现场重读一次再规划写入。
        #
        # 不复用 Step 0 那份 profile：搬运与批量装备刚改过账号，那份快照已经过期 ——
        # 拿它规划写入会照旧快照的插槽号/能量去写新现场（Step 0 只当"守门"，不当"抄近路"）。
        # 预检已经在这一趟里判过一次，所以这里再失败只会是刚换上去的件还没同步到；
        # `_prepare_mod_operations` → `_read_sockets` 自带同步窗口重试（见 loadout_mod_sockets）。
        profile = await self._resolver.get_profile(
            mid, mtype, profile_components.EQUIP_LOADOUT
        )
        sockets_cache, instances_data, insertable = self._mod_write_snapshot(profile, char_id)
        mod_operations: dict[str, list[tuple[str, int, int]]] = {}
        for lo_item in loadout.items:
            has_work = lo_item.mods or lo_item.mod_sockets or lo_item.functional_mod_groups
            if not has_work or not lo_item.item_instance_id:
                continue
            try:
                mod_operations[lo_item.item_instance_id] = (
                    await self._prepare_mod_operations(
                        lo_item,
                        mid,
                        mtype,
                        sockets_cache,
                        instances_data,
                        insertable,
                    )
                )
            except TransferError as exc:
                steps.append(MoveItemStep(
                    action="mod_preflight",
                    detail=str(exc),
                    success=False,
                ))
                all_ok = False

        if not all_ok:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装 '{loadout.name}' 模组预检失败，未修改模组或子职业。",
                steps=steps,
            )

        blocked = BlockedMods()
        for lo_item in loadout.items:
            for operation, mod_hash, socket_idx, reason in mod_operations.get(
                lo_item.item_instance_id, []
            ):
                if operation == "keep":
                    steps.append(self.keep_mod_step(lo_item, mod_hash, socket_idx))
                    continue
                if operation in {"blocked", "skipped"}:
                    # 装不上的那一颗：不写、也不回退整条配装，如实报给玩家（ADR-013）。
                    # 两类都交给 `blocked` 记账 —— 哪一类算"回读永远等不到"由它一处判。
                    steps.append(self.blocked_mod_step(lo_item, mod_hash, socket_idx, reason))
                    blocked.add(operation, lo_item.name, mod_hash, reason)
                    continue
                try:
                    mod_result = await self._insert_armor_mod(
                        lo_item.item_instance_id,
                        mod_hash,
                        socket_idx,
                        char_id,
                        mtype,
                    )
                    upstream = str(mod_result.get("Message") or "").strip()
                    # 1679 = 这个槽已经装着它了：状态已成立，不算失败（当成失败会让整条配装回退
                    # 3.5 分钟，真机实测）。判据与 equip_mod 那条路共用一份。
                    already = plug_already_installed(mod_result)
                    ok = mod_result.get("ErrorCode", 0) == 1 or already
                    # 回执写**模组名字**：steps 是调用方唯一的写后证据，写 hash 会逼它再逐件查护甲。
                    mod_label = self.mod_label(mod_hash)
                    if already:
                        detail = (
                            f"'{lo_item.name}' 插槽 {socket_idx} 已经空着"
                            if operation == "clear"
                            else f"'{lo_item.name}' 插槽 {socket_idx} 已经装着 '{mod_label}'，未改动"
                        )
                    elif operation == "clear":
                        # **点名被换掉的是哪一颗**：只写"为 X 腾出能量"的话，调用方看不出
                        # 这一格原来装的是什么 —— 而"我原来那颗去哪了"正是玩家第一个要问的。
                        replaced = sockets_cache.get(lo_item.item_instance_id, [])
                        replaced_label = (
                            self.mod_label(replaced[socket_idx].get("plugHash", 0))
                            if socket_idx < len(replaced) else ""
                        )
                        detail = (
                            f"为 '{mod_label}' 腾出能量：'{lo_item.name}' 插槽 {socket_idx}"
                            + (f"（换下 '{replaced_label}'）" if replaced_label else "")
                        )
                    else:
                        detail = f"模组 '{mod_label}' → '{lo_item.name}'"
                    # 失败要带上游原文（以前只写"为…腾出能量"，真因看不到）——
                    # **清能量那一支同样要带**：它失败时调用方往往只看到这一行。
                    if not ok and not already:
                        detail += f" 失败：{upstream or '上游没给原因'}"
                    steps.append(MoveItemStep(
                        action="mod_clear" if operation == "clear" else "mod",
                        detail=detail,
                        success=ok,
                    ))
                    if not ok:
                        blocker = self.mod_write_blocker(mod_result, mod_hash, profile)
                        if blocker:
                            # 上游明确拒绝的：记下原因，继续走完剩下的模组，最后如实汇报。
                            blocked.add(operation, lo_item.name, mod_hash, blocker)
                            continue
                        all_ok = False
                        break
                except (aiobungie.HTTPError, TransferError) as e:
                    steps.append(MoveItemStep(
                        action="error",
                        detail=f"模组 {mod_hash} 应用失败: {e}",
                        success=False,
                    ))
                    all_ok = False
                    break

        # Step 3: 子职业 —— **无条件跑**，不再让模组阶段的结论把它吞掉。
        #
        # 真机 2026-10-03 第 1 轮：`equip_build` 里 1 颗模组被上游挡住（1676），旧代码在这个
        # 分支提前 return，Step 3（子职业/碎片）整段没跑，而回执只说"装备已经换上" ——
        # **少做了一步却不说**。碎片是这套配装的独立一半，模组写不动不影响它，
        # 所以这里不设门；跑没跑、成没成，一律写进 steps 与 message。
        subclass_ok: bool | None = None
        subclass_why = ""
        if loadout.subclass:
            subclass_ok, subclass_why = await self._apply_subclass_config(
                player_name, loadout, char_id, mtype, steps
            )
            if not subclass_ok:
                all_ok = False

        # "子职业那一步做没做"的一句话 —— 只在"装备换上了、但模组有被挡住"的回执里用得上：
        # 那一刻调用方最容易被"装备已经换上"骗过去，如果这半句不写，少做的一步就没人提。
        if subclass_ok is None:
            subclass_receipt = "没做（这套配装没存子职业配置）"
        elif subclass_ok:
            subclass_receipt = "做完了"
        else:
            # 带上游原文（与 `mod` 那条路同一口径）：只写"做了但没成（见 steps）"时，
            # 调用方还得自己去翻一遍步骤才知道为什么 —— 真机 2026-10-03 第 3 轮就是这么丢的。
            subclass_receipt = f"做了但没成（{subclass_why}）"

        # Step 4: 回读核对 —— **这一趟是全流程唯一的核对**（没核对过就不能说"已装备"：真机
        # 2026-09-24 这条路直接以"已装备"收尾，回执里没有一步证明装备真在身上）。
        # 判据（`loadout_matches`）、窗口（`write_readback`）、话术（`readback_verdict`）、
        # "写不成的模组算不算"（`loadout_blocked_mods`）各只有一处；`equip_with_recovery` 的
        # 外层直接读这里的 `verified`，**不再自己开第二个窗口**
        # —— 真机 2026-10-03 audit 123435：内侧这一轮 8 次读没对上之后，外层又读了一整轮，
        # 两次读的是同一份状态、同一个函数，结论不可能变，那 147.8 秒全是白等。
        # 核对不上只报"没确认"，**不改写入结论**。
        detail, verified = await loadout_verify.readback_verdict(
            self, player_name, loadout, blocked=blocked
        )
        steps.append(MoveItemStep(action="verify", detail=detail, success=verified))

        # 模组被上游拒绝写入时 equipment 是好的，但**子职业那一半的状态要说清**：
        # 回执是调用方唯一的证据，少做一步就必须点名（不然它只会读到"装备已经换上"）。
        # 预检判死的那几颗**不并进这条**：它们各自已经有一条 `mod_blocked` 步（循环里写的），
        # 再聚合一次就是同一颗报两遍。
        if blocked.has_upstream:
            steps.append(MoveItemStep(
                action="mod_blocked", detail=blocked.upstream_detail(), success=False,
            ))

        if not all_ok:
            # 失败也要说清**走到了哪一步**：以前这句是"未继续修改子职业"，而子职业其实跑了
            # （真机 2026-10-03 的毛病就是"少做了一步却不说"，反过来"做了却说没做"同样是假话）。
            message = (
                f"配装 '{loadout.name}' 未完全生效：子职业那一步{subclass_receipt}，"
                "模组阶段有没写成的（原因见 steps）。"
            )
        elif blocked.has_any:
            message = f"配装 '{loadout.name}' 的装备已经换上；子职业那一步{subclass_receipt}。"
        elif verified:
            message = f"配装 '{loadout.name}' 已装备，回读核对通过。"
        else:
            message = f"配装 '{loadout.name}' 的写入都成功了，但回读没确认：{detail}"
        if blocked.has_any:
            message += blocked.tail()
        return LoadoutOperationResult(
            success=all_ok and not blocked.has_upstream,
            loadout_name=loadout.name,
            message=message,
            steps=steps,
            verified=verified,
        )
