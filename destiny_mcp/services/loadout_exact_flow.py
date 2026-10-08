"""装备一套配装的**完整一趟**：抓恢复点 → 应用 → 判要不要回滚。

**两条入口共用这一趟**（`equip_loadout` 与 `equip_build`）。以前 `equip_loadout` 直接调
`_equip_local_unlocked` 就完事、**没有任何恢复点** —— 同一条 `_equip_local_unlocked` 失败时，
`equip_build` 会把账号恢复回执行前，而 `equip_loadout` 留下"装备换了、模组只写了一半"的混合
状态，且没有任何东西能把它还原。同一个动作、两种后果，只因为走了不同的入口。

搬出 `loadout_equipment_service.py` 的原因和这一族其它 mixin 一样（那边贴着体量上限），
但这里还有一条更实的理由：**"要不要回滚"是一个判断，不是一个步骤**。它以前散在
`_equip_local_unlocked` 的返回分支与 `equip_with_recovery` 的外层之间，于是出现了真机 2026-10-03
那两类事故 —— 模组被挡住就提前 return（子职业整段没跑）、外侧拿自己那次读的结论覆盖内侧
（整条配装白回滚）。现在它只有一处：`_apply_exact_with_recovery`。

**外层不再自己回读**（2026-10-03 真机 audit `/123435` 的账）：那次 `equip_build` 224.2 秒里
147.8 秒花在两轮"回读没对上"上 —— 内侧 Step 4 按 `write_readback` 重试满一轮（8 次读，
每次 3 次网络往返：`search_player` + profile[200] + profile[INVENTORY_SOCKETS]）之后，外层
**又开了第二个窗口**。两轮读的是同一份账号状态、用的是同一个判据，结论不可能变；而那一轮的
"对不上"本身还是判据没归一造成的误报（见 `loadout_matches`）。所以核对（连同它的话术）只有
一处：`_equip_local_unlocked` 的 Step 4 调 `loadout_verify.readback_verdict`；这里只读结论、
判要不要回滚。

以 mixin 挂在 `LoadoutEquipmentService` 上（`self` 上就有恢复快照与 `_equip_local_unlocked`），
所以调用点不用改。
"""

from __future__ import annotations

import asyncio

import aiobungie
import anyio

from ..exceptions import (
    DestinyMCPError,
    ItemNotFoundError,
    TransferError,
    describe_exception,
)
from ..logging_config import get_logger
from ..models import Loadout, LoadoutOperationResult, MoveItemStep

logger = get_logger(__name__)

_CANCEL_ROLLBACK_TIMEOUT_SECONDS = 60


class ExactFlowMixin:
    """精确配装的执行、核对与回滚闸门。"""

    async def equip_with_recovery(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """装备一套配装：拿锁 → 抓恢复点 → 应用/核对/必要时回滚。

        `equip_build`（确认过的精确配装）与 `equip_loadout`（已存配装）**共用这一条**：
        两条入口对"失败之后账号该是什么样"的答案必须一样。
        """
        async with self._equip_lock:
            return await self._equip_with_recovery_unlocked(player_name, loadout)

    async def _equip_with_recovery_unlocked(
        self,
        player_name: str,
        loadout: Loadout,
    ) -> LoadoutOperationResult:
        """`equip_with_recovery` 的锁内部分（取消恢复必须留在持锁的那个 task 里）。"""
        try:
            recovery = await self._capture_recovery_state(player_name, loadout)
        except (ItemNotFoundError, TransferError) as exc:
            return LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=f"配装预检失败：{exc}",
                steps=[MoveItemStep(action="preflight", detail=str(exc), success=False)],
            )

        try:
            return await self._apply_exact_with_recovery(player_name, loadout, recovery)
        except asyncio.CancelledError:
            # Stay in the lock-owning task: rollback re-enters account-write methods.
            rollback_ok = False
            steps: list[MoveItemStep] = []
            with anyio.move_on_after(_CANCEL_ROLLBACK_TIMEOUT_SECONDS, shield=True):
                try:
                    rollback_ok = await self._restore_exact_state(
                        player_name, loadout, recovery, steps
                    )
                except Exception:
                    logger.exception("Cancelled exact loadout rollback failed")
            if rollback_ok:
                logger.info("Cancelled exact loadout restored the previous equipment state")
            else:
                logger.error("Cancelled exact loadout recovery incomplete; check character equipment")
            raise

    async def _apply_exact_with_recovery(
        self, player_name: str, loadout: Loadout, recovery: dict
    ) -> LoadoutOperationResult:
        try:
            applied = await self._equip_local_unlocked(player_name, loadout)
        except (DestinyMCPError, aiobungie.HTTPError, OSError) as exc:
            # `describe_exception`：`TimeoutError()` 的 str 是空的，直接写会留下"失败但没有原因"（真机 2026-09-23 撞过）。
            logger.exception("Exact loadout application failed: %s", describe_exception(exc))
            reason = describe_exception(exc)
            applied = LoadoutOperationResult(
                success=False,
                loadout_name=loadout.name,
                message=reason,
                steps=[MoveItemStep(action="apply", detail=reason, success=False)],
            )
        if applied.success:
            if applied.verified is None:
                # 只有"绕开 Step 4 的调用方"会走到这里（生产路径上 success=True 必带核对结论）。
                # 结论是"**没核对**"，不是"没装上"：写入阶段一路成功，缺的是证据。
                # 这里**不自己开窗口** —— 窗口只有 `write_readback` 一处、判据只有 `loadout_matches`
                # 一处、话术只有 `loadout_verify.readback_verdict` 一处；真机 2026-10-03 audit
                # 123435 就是外层自己又读了一整轮（约 78 秒），读的还是同一份状态。
                applied.steps.append(MoveItemStep(
                    action="verify",
                    detail="这次没有回读核对（执行层没给核对结论），账号状态未确认。",
                    success=False,
                ))
                applied.message = (
                    f"配装 '{loadout.name}' 的写入都成功了，但这次没有回读核对"
                    "——账号上是不是这套没有证据（别当成没装上）。"
                )
            # 有结论时（True/False）**原样透传**：核对那一趟、它写进 steps 的那一行、以及
            # 它那句话术都归 Step 4（`loadout_equipment_service` 调 `readback_verdict`），
            # 这里只回答"要不要回滚" —— 两处各措一次辞，同一份状态就会被说成两种结论
            # （真机 2026-10-03 第 3 轮："没确认"被外层改写成"对不上"）。
            return applied

        # 走到这里 = 写入阶段失败了（`success=True` 的那条上面已经 return）。两种不许回滚，
        # 理由不同但都写在这：
        # - **模组预检失败**：那一步在**任何写入之前**，账号一个字节都没动
        #   （见 `loadout_mod_preflight`），回滚没有对象 —— 真机上再跑一趟反而是几分钟白等，
        #   而且会把"预检失败、这次没动账号"这句更准的话换成含糊的"已恢复执行前状态"；
        # - **模组被上游拒绝写入**：equipment 是好的，为一颗插件把装备换回去更糟（ADR-013）。（2026-10-06 试过更细的判据"失败步全是 mod_* 才跳过"，撞掉两条 ADR-013 用例 → 撤回。）
        #
        # 第三种"写入全成功、只是回读没确认"已经不在这条路上（`verified is False` 也在上面
        # return 了）：那时回滚等于把**已经正确的账号**改回旧状态，错的是拿"没确认"当回滚
        # 条件（口径见 `write_readback` 的"没确认 ≠ 没换成"）。
        #
        # 回滚只留给**写入阶段本身失败/结果未知**且已经动过账号的那条路。
        if any(
            st.action in {"mod_blocked", "mod_preflight"} for st in applied.steps
        ):
            return applied

        rollback_steps: list[MoveItemStep] = []
        try:
            rollback_ok = await self._restore_exact_state(
                player_name,
                loadout,
                recovery,
                rollback_steps,
            )
        except (DestinyMCPError, aiobungie.HTTPError, OSError, TypeError) as exc:
            logger.exception("Exact loadout rollback failed: %s", exc)
            rollback_steps.append(MoveItemStep(
                action="rollback_error", detail=str(exc), success=False
            ))
            rollback_ok = False
        return LoadoutOperationResult(
            success=False,
            loadout_name=loadout.name,
            message=(
                f"配装 '{loadout.name}' 执行失败，已恢复执行前状态。"
                if rollback_ok
                else f"配装 '{loadout.name}' 执行失败，且自动恢复不完整；请检查角色装备。"
            ),
            steps=[*applied.steps, *rollback_steps],
        )
