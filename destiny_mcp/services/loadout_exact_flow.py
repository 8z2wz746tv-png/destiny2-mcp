"""装备一套配装的**完整一趟**：抓恢复点 → 应用 → 回读核对 → 判要不要回滚。

**两条入口共用这一趟**（`equip_loadout` 与 `equip_build`）。以前 `equip_loadout` 直接调
`_equip_local_unlocked` 就完事、**没有任何恢复点** —— 同一条 `_equip_local_unlocked` 失败时，
`equip_build` 会把账号恢复回执行前，而 `equip_loadout` 留下"装备换了、模组只写了一半"的混合
状态，且没有任何东西能把它还原。同一个动作、两种后果，只因为走了不同的入口。

搬出 `loadout_equipment_service.py` 的原因和这一族其它 mixin 一样（那边贴着体量上限），
但这里还有一条更实的理由：**"要不要回滚"是一个判断，不是一个步骤**。它以前散在
`_equip_local_unlocked` 的返回分支与 `equip_with_recovery` 的外层之间，于是出现了真机 2026-10-03
那两类事故 —— 模组被挡住就提前 return（子职业整段没跑）、外侧只读一次就把"没确认"判成失败
（整条配装白回滚）。现在它只有一处：`_apply_exact_with_recovery`。

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
from . import loadout_verify, write_readback
from .write_readback import read_until

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
        verified = False
        if applied.success:
            # 有模组被上游挡住时，"对不上"的原因就是它 —— 别让调用方以为整个装备都没生效。
            blocked = [step for step in applied.steps if step.action == "mod_blocked"]
            verification_detail = (
                f"有 {len(blocked)} 颗模组没装上（见 mod_blocked 步骤），其余已按确认内容写入。"
                if blocked else "执行结果与确认的配装不一致。"
            )
            if applied.verified:
                # 内层 Step 4 已经核对过的那一趟**不再读第二遍**：它是同一份判据、同一个窗口，
                # 重读只会让这条路多等一个同步窗口（实测最长 10.5 秒 × 2）。
                verified = True
            else:
                try:
                    # **走 `write_readback`，与内层 Step 4 同一个窗口**。真机 2026-10-03 第 3 轮：
                    # 内层重试满约 10.5 秒、按口径报"别当成没装上"，而这里**只读一次**就判失败，
                    # 于是同步窗口一超过 10.5 秒，同一份状态被读成两种结论、整条配装白白回滚。
                    # 一处口径两处用，读法也必须一样。
                    verified = await read_until(
                        lambda: loadout_verify.verify_loadout(
                            self, player_name, loadout
                        ),
                        bool,
                    )
                    if not verified:
                        window = int(write_readback.ATTEMPTS * write_readback.DELAY_SECONDS)
                        verification_detail = (
                            f"写入步骤都成功了，但回读重试 {window} 秒后仍对不上（可能是同步窗口）"
                            "——过十几秒再看一次，别当成没装上。"
                        )
                except (DestinyMCPError, aiobungie.HTTPError, OSError) as exc:
                    logger.error("Exact loadout verification failed: %s", exc)
                    verification_detail = str(exc)
            applied.steps.append(MoveItemStep(
                action="verify",
                detail=(
                    "已验证装备实例、模组和子职业配置。"
                    if verified
                    else verification_detail
                ),
                success=verified,
            ))
            applied.verified = verified
            if verified:
                return applied
            # 没确认时 message 也要跟着改口径：调用方读的常常就是这一句，而内层原来那句
            # （"写入都成功了…"）在"装备确实还在"的意义上是对的，但绝不能说成"失败"。
            applied.message = (
                f"配装 '{loadout.name}' 的写入都成功了，但回读没确认：{verification_detail}"
            )

        # 三个不许回滚的情形，理由不同但都写在这：
        # - **模组预检失败**：那一步在**任何写入之前**，账号一个字节都没动
        #   （见 `loadout_mod_preflight`），回滚没有对象 —— 真机上再跑一趟反而是几分钟白等，
        #   而且会把"预检失败、这次没动账号"这句更准的话换成含糊的"已恢复执行前状态"；
        # - **模组被上游拒绝写入**：equipment 是好的，为一颗插件把装备换回去更糟（ADR-013）；
        # - **没确认**：写入阶段一路成功、只是回读还没同步。这时回滚等于把**已经正确的账号**
        #   改回旧状态 —— 回滚本身是对的，错的是拿"没确认"当回滚条件（口径见 `write_readback`
        #   的"没确认 ≠ 没换成"）。真机 2026-10-03 第 3 轮就是被这条判错的。
        #
        # 回滚只留给**写入阶段本身失败/结果未知**的那条路（`applied.success is False`
        # 且已经动过账号）。
        if applied.success or any(
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
