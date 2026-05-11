"""
Game Engine 生命周期管理器 (Lifecycle Manager) 模块。

**Why**: 本模块统一协调狼人杀对局的完整生命周期 —— 从创建房间、初始化身份、
启动对局、推进阶段、到结算结束或异常中止。它确保所有操作均符合全局状态机
（:attr:`LifecycleManager.VALID_STATUS_TRANSITIONS`）的制约，并通过内部的
:class:`PhaseStateMachine` 管理对局内阶段流转。

参考: :doc:`docs/plan/状态机与生命周期设计`
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from ai_werewolf_core.core.engine.exceptions import (
    GameNotRunnableError,
    InvalidTransitionError,
)
from ai_werewolf_core.core.engine.state_machine import PhaseStateMachine
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import (
    EventType,
    GamePhase,
    GameStatus,
    Visibility,
)
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.utils.logger import (
    bind_game_context,
    clear_all_context,
    get_logger,
)

logger = get_logger(__name__)


class LifecycleManager:
    """
    对局生命周期管理器。

    封装了从房间创建到对局结束的全流程控制。内部持有:
    - ``status``: 全局游戏状态（:class:`GameStatus`）
    - ``state_machine``: 阶段状态机（:class:`PhaseStateMachine`），负责管理
      ``RUNNING`` 状态下的具体阶段流转。

    **Why**: 将生命周期和阶段流转拆分为两个层级的控制，使得全局状态
    （INIT/START/RUNNING/SETTLING/FINISHED/ABORTED）与局内阶段（NIGHT/DAY/DISCUSSION...）
    可以独立校验和演进，避免状态管理逻辑耦合在单一类中。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 事件总线实例。
        state_machine: 内部阶段状态机，仅当 ``status == RUNNING`` 时处于活跃状态。
    """

    VALID_STATUS_TRANSITIONS: dict[GameStatus, list[GameStatus]] = {
        GameStatus.INIT: [GameStatus.START],
        GameStatus.START: [GameStatus.RUNNING, GameStatus.ABORTED],
        GameStatus.RUNNING: [GameStatus.SETTLING, GameStatus.ABORTED],
        GameStatus.SETTLING: [GameStatus.FINISHED, GameStatus.ABORTED],
        GameStatus.FINISHED: [],
        GameStatus.ABORTED: [],
    }
    """
    合法全局状态迁移映射表。

    **Why**: 全局生命周期状态必须严格按序流转，绝不允许从 FINISHED 回退到
    RUNNING 等非法操作。终结态（FINISHED、ABORTED）无后继状态。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """
        初始化生命周期管理器。

        Args:
            game_id: 对局唯一标识。
            event_bus: 事件总线实例，用于发布全局状态变更事件。

        :ivar status: 当前全局游戏状态，初始为 :attr:`GameStatus.INIT`。
        :ivar state_machine: 内部阶段状态机实例，随 ``start_game`` 激活。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self._status: GameStatus = GameStatus.INIT
        self.state_machine: PhaseStateMachine = PhaseStateMachine(game_id, event_bus)

    @property
    def status(self) -> GameStatus:
        """当前全局游戏生命周期状态 (:class:`GameStatus`)。"""
        return self._status

    # ------------------------------------------------------------------
    # 内部校验与事件发布
    # ------------------------------------------------------------------

    def _validate_status_transition(self, new_status: GameStatus) -> None:
        """
        校验全局状态迁移是否合法。

        **Why**: 所有状态变更操作（init_game、start_game、end_game、abort_game）
        必须通过此方法校验，防止跳过步骤或逆序操作。

        Args:
            new_status: 目标全局状态。

        Raises:
            InvalidTransitionError: 迁移路径不在 :attr:`VALID_STATUS_TRANSITIONS` 中。
        """
        allowed = self.VALID_STATUS_TRANSITIONS.get(self._status, [])
        if new_status not in allowed:
            raise InvalidTransitionError(
                current_state=self._status,
                target_state=new_status,
            )

    async def _publish_status_change(
        self,
        old_status: GameStatus,
        new_status: GameStatus,
        extra: Optional[dict] = None,
    ) -> None:
        """
        发布全局状态变更事件。

        以 :attr:`EventType.SYSTEM_ANNOUNCEMENT` 类型、:attr:`Visibility.PUBLIC`
        可见性发布，payload 中包含 ``old_status``、``new_status`` 及可选的
        额外上下文（如中止原因、胜利阵营）。

        Args:
            old_status: 迁移前的全局状态。
            new_status: 迁移后的全局状态。
            extra: 可选的额外 payload 字段。
        """
        payload: dict = {
            "old_status": old_status.value,
            "new_status": new_status.value,
        }
        if extra:
            payload.update(extra)

        event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.SYSTEM_ANNOUNCEMENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=datetime.now(timezone.utc),
            payload=payload,
        )

        await self.event_bus.publish(event)

    async def _set_status(self, new_status: GameStatus) -> None:
        """
        内部原子操作：校验 -> 更新状态 -> 日志 -> 发布事件。

        Args:
            new_status: 目标全局状态。

        Raises:
            InvalidTransitionError: 校验失败。
        """
        self._validate_status_transition(new_status)
        old_status = self._status
        self._status = new_status

        logger.info(
            "lifecycle_status_changed",
            game_id=self.game_id,
            old_status=old_status.value,
            new_status=new_status.value,
        )

        await self._publish_status_change(old_status, new_status)

    # ------------------------------------------------------------------
    # 公开接口: 生命周期操作
    # ------------------------------------------------------------------

    async def init_game(self) -> None:
        """
        初始化对局: 将状态从 :attr:`GameStatus.INIT` 迁移到 :attr:`GameStatus.START`。

        此阶段通常对应创建房间、玩家加入、身份分配等准备工作。
        成功后将状态变更为 ``START`` 并广播事件。

        Raises:
            InvalidTransitionError: 当前状态不是 ``INIT``。
        """
        logger.info("game_init", game_id=self.game_id)
        bind_game_context(self.game_id, GamePhase.INIT.value)
        # 初始化阶段状态机: 进入 INIT 阶段 (对局准备)
        await self.state_machine.transition_to(GamePhase.INIT)
        await self._set_status(GameStatus.START)

    async def start_game(self) -> None:
        """
        启动对局: 将状态从 :attr:`GameStatus.START` 迁移到 :attr:`GameStatus.RUNNING`，
        同时激活内部的 :class:`PhaseStateMachine`，进入首轮天黑阶段。

        此方法会:
        1. 校验当前状态为 ``START``。
        2. 将全局状态变更为 ``RUNNING``。
        3. 初始化阶段状态机进入 :attr:`GamePhase.NIGHT_START`，轮次设为 1。

        **Why**: 此处分两步发布事件（先 STATUS 变更，再 PHASE 变更），
        确保下游模块（如 WebSocket 网关）先收到"对局开始"的通知，
        再收到"进入 NIGHT"的通知，保证前端状态同步顺序。

        Raises:
            InvalidTransitionError: 当前状态不是 ``START``。
        """
        logger.info("game_start", game_id=self.game_id)
        await self._set_status(GameStatus.RUNNING)

        # 激活阶段状态机: 进入首轮 NIGHT_START
        await self.state_machine.transition_to(
            GamePhase.NIGHT_START,
            context={"reason": "game_start"},
        )

    async def advance_phase(
        self, next_phase: GamePhase, context: Optional[dict] = None
    ) -> None:
        """
        推进游戏阶段：委托给内部 :class:`PhaseStateMachine` 执行阶段迁移。

        仅当全局状态为 :attr:`GameStatus.RUNNING` 时允许推进阶段，
        否则抛出 :class:`GameNotRunnableError`。

        **Why**: 此方法是 Game Engine 推进对局的核心入口。Engine 根据
        游戏规则决定下一个阶段后，调用此方法完成实际的状态迁移和事件广播。

        Args:
            next_phase: 目标游戏阶段。
            context: 可选的上下文数据（如 ``{"pk_triggered": True}``）。

        Raises:
            GameNotRunnableError: 全局状态不是 ``RUNNING``。
            InvalidTransitionError: 阶段迁移路径非法（由 PhaseStateMachine 抛出）。
        """
        if self._status != GameStatus.RUNNING:
            raise GameNotRunnableError(
                current_status=self._status,
                game_id=self.game_id,
            )

        await self.state_machine.transition_to(next_phase, context)

    async def end_game(self, winner_faction: str) -> None:
        """
        正常结束对局: RUNNING → SETTLING → FINISHED。

        此方法:
        1. 将状态从 ``RUNNING`` 迁移到 ``SETTLING``（结算阶段）。
        2. 将 :class:`PhaseStateMachine` 的阶段设置为 :attr:`GamePhase.GAME_OVER`。
        3. 发布 :attr:`EventType.GAME_OVER_EVENT` 事件，payload 包含 ``winner_faction``。
        4. 将状态从 ``SETTLING`` 迁移到 ``FINISHED``。
        5. 调用 ``clear_all_context()`` 清除日志上下文。

        **Why**: 分两步（SETTLING → FINISHED）的原因是为结算逻辑（如积分计算、
        成就判定）预留执行窗口，确保结算事件先于终结事件发布。

        Args:
            winner_faction: 胜利阵营标识，如 ``"VILLAGER"`` 或 ``"WEREWOLF"``。

        Raises:
            InvalidTransitionError: 当前状态不是 ``RUNNING`` 或 ``SETTLING``。
        """
        logger.info("game_end", game_id=self.game_id, winner_faction=winner_faction)

        # Step 1: RUNNING -> SETTLING
        await self._set_status(GameStatus.SETTLING)

        # Step 2: 阶段进入 GAME_OVER
        # 注意: GAME_OVER 向 None 的迁移由 PhaseStateMachine 的 VALID_TRANSITIONS 允许，
        # 但实际"游戏彻底结束"由 FINISHED 状态表达；这里仅标记局内阶段的终结。
        #
        # HACK: 直接设置内部状态以绕过阶段合法性校验。
        # end_game 是强制结束操作，当前阶段可能是任意值（如 NIGHT_START），
        # 而 GAME_OVER 并非所有阶段的合法后继。因此直接操作 _current_phase。
        self.state_machine._current_phase = GamePhase.GAME_OVER

        # Step 3: 发布 GAME_OVER 事件
        game_over_event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,
            event_type=EventType.GAME_OVER_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=datetime.now(timezone.utc),
            payload={
                "winner_faction": winner_faction,
                "total_rounds": self.state_machine.round,
            },
        )
        await self.event_bus.publish(game_over_event)

        # Step 4: SETTLING -> FINISHED
        await self._set_status(GameStatus.FINISHED)

        # Step 5: 清理日志上下文
        clear_all_context()
        logger.info("game_finished", game_id=self.game_id)

    async def abort_game(self, reason: str) -> None:
        """
        异常中止对局: 从任意可中止的状态迁移到 :attr:`GameStatus.ABORTED`。

        根据 :attr:`VALID_STATUS_TRANSITIONS`，可从 START、RUNNING、SETTLING
        三种状态中止。INIT 状态不可中止（尚无对局实体），FINISHED 和 ABORTED
        状态已是终结态。

        **Why**: 此方法不校验当前状态是否"允许中止"，而是直接跳转到 ABORTED。
        这是因为中止是紧急操作，只要不是 INIT/FINISHED/ABORTED 都应被允许。
        然而，为了保持架构一致性，我们仍然以 ``_set_status`` 为基础进行校验；
        如果当前状态不允许迁移到 ABORTED，将抛出异常。

        Args:
            reason: 中止原因（如 ``"player_disconnected"``）。

        Raises:
            InvalidTransitionError: 当前状态不在允许中止的状态集合中。
        """
        logger.warning("game_abort", game_id=self.game_id, reason=reason)

        await self._set_status(GameStatus.ABORTED)

        clear_all_context()
        logger.info("game_aborted", game_id=self.game_id)
