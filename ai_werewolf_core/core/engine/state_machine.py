"""
Game Engine 阶段状态机 (Phase State Machine) 模块。

**Why**: 本模块是游戏引擎的核心骨架，负责硬编码管理狼人杀对局内各阶段的
严格流转。所有状态迁移路径必须在 :attr:`PhaseStateMachine.VALID_TRANSITIONS`
中预先定义为有向图，杜绝 LLM 或任何外部输入决定状态流转。每次迁移都
经过合法性校验、结构化日志记录并通过 EventBus 广播事件。

参考: :doc:`docs/plan/状态机与生命周期设计`
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from ai_werewolf_core.core.engine.exceptions import InvalidTransitionError
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import EventType, GamePhase, Visibility
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.utils.logger import bind_game_context, get_logger

logger = get_logger(__name__)


class PhaseStateMachine:
    """
    游戏对局阶段状态机。

    持有当前阶段与回合数，强制校验每一次阶段迁移的合法性，
    在迁移成功时通过 EventBus 广播 ``PHASE_TRANSITION_EVENT`` 事件。

    **Why**: 狼人杀对局有严格的时序逻辑（天黑→夜间行动→结算→天亮→
    讨论→投票→遗言→检查胜负），任何跳步或逆序都会导致游戏逻辑崩溃。
    因此所有合法迁移路径必须硬编码在校验字典中。

    Attributes:
        game_id: 对局唯一标识。
        event_bus: 用于发布阶段变更事件的 EventBus 实例。
    """

    VALID_TRANSITIONS: dict[Optional[GamePhase], list[GamePhase | None]] = {
        None: [GamePhase.INIT],
        GamePhase.INIT: [GamePhase.NIGHT_START],
        
        # 夜晚阶段
        GamePhase.NIGHT_START: [GamePhase.NIGHT_WOLF_ACT],
        GamePhase.NIGHT_WOLF_ACT: [GamePhase.NIGHT_WITCH_ACT],
        GamePhase.NIGHT_WITCH_ACT: [GamePhase.NIGHT_SEER_ACT],
        GamePhase.NIGHT_SEER_ACT: [GamePhase.NIGHT_RESOLVE],
        GamePhase.NIGHT_RESOLVE: [
            GamePhase.DAY_START
        ],
        
        # 白天阶段
        GamePhase.DAY_START: [
            GamePhase.DAY_DISCUSSION,# 正常进入讨论
            GamePhase.HUNTER_SHOOT,  # 夜晚猎人死亡，天亮开枪
            GamePhase.LAST_WORDS,    # 首夜死亡遗言
            GamePhase.GAME_OVER      # 天亮播报后游戏结束
        ],
        GamePhase.DAY_DISCUSSION: [GamePhase.DAY_VOTE],
        GamePhase.DAY_VOTE: [
            GamePhase.VOTE_RESOLVE,
            GamePhase.DAY_PK_DISCUSSION
        ],
        
        # 投票结算
        GamePhase.VOTE_RESOLVE: [
            GamePhase.HUNTER_SHOOT,  # 猎人被票出局
            GamePhase.LAST_WORDS,    # 被票出局者遗言
            GamePhase.NIGHT_START,   # 平安日，无人出局，直接天黑
            GamePhase.GAME_OVER,     # 投票后游戏结束
        ],
        
        # PK 阶段
        GamePhase.DAY_PK_DISCUSSION: [GamePhase.DAY_PK_VOTE],
        GamePhase.DAY_PK_VOTE: [
            GamePhase.VOTE_RESOLVE
        ],
        
        # 特殊结算阶段
        GamePhase.HUNTER_SHOOT: [
            GamePhase.LAST_WORDS,    # 开枪后发表遗言
            GamePhase.DAY_DISCUSSION,# 夜晚死亡开枪后，进入白天讨论
            GamePhase.GAME_OVER      # 开枪后游戏结束
        ],
        GamePhase.LAST_WORDS: [
            GamePhase.DAY_DISCUSSION,# 首夜死亡遗言后，进入白天讨论
            GamePhase.NIGHT_START,   # 白天被票遗言后，进入夜晚
        ],
        
        # 游戏结束
        GamePhase.GAME_OVER: [
            GamePhase.INIT,          # 再来一局
            None                     # 彻底结束
        ],
    }

    """
    合法阶段迁移映射表（有向图）。

    **Why**: 这是整个游戏时序逻辑的唯一权威数据源。键为当前阶段，
    值为允许跳转到的目标阶段列表。``None`` 作为键表示初始状态
    （游戏尚未开始），``None`` 作为值表示游戏彻底结束。

    注意: 状态机只负责校验迁移路径的合法性，不负责选择分支路径。
    调用方（Game Engine）根据游戏规则（如是否有 PK、是否猎人死亡等）
    从合法目标中选择具体的下一个阶段并通过 ``transition_to`` 传入。
    """

    def __init__(self, game_id: str, event_bus: EventBus) -> None:
        """
        初始化阶段状态机。

        Args:
            game_id: 对局唯一标识，用于日志追踪和事件路由。
            event_bus: 事件总线实例。阶段变更时将发布事件以驱动
                Agent Runtime、前端推送等下游模块。

        :ivar current_phase: 当前所处游戏阶段，初始为 ``None``。
        :ivar round: 当前轮次，初始为 ``0``。进入 NIGHT_START 时递增。
        """
        self.game_id: str = game_id
        self.event_bus: EventBus = event_bus
        self._current_phase: Optional[GamePhase] = None
        self._round: int = 0

    @property
    def current_phase(self) -> Optional[GamePhase]:
        """当前所处的游戏阶段 (:class:`GamePhase`)，可能为 ``None`` 表示尚未开始。"""
        return self._current_phase

    @property
    def round(self) -> int:
        """当前轮次，从 0 开始。进入 :attr:`GamePhase.NIGHT_START` 时自增。"""
        return self._round

    async def transition_to(
        self, next_phase: GamePhase, context: Optional[dict] = None
    ) -> None:
        """
        执行阶段迁移：校验 -> 更新状态 -> 记录日志 -> 发布事件。

        **合法性校验**:
        根据 :attr:`VALID_TRANSITIONS` 判断 ``next_phase`` 是否是当前阶段的
        合法后继。若校验失败则抛出 :class:`InvalidTransitionError`。

        **轮次递增**:
        当目标阶段为 :attr:`GamePhase.NIGHT_START` 时，轮次自增 1。
        这标记着新的"天黑-天亮"循环开始。

        **事件发布**:
        以 :attr:`EventType.PHASE_TRANSITION_EVENT` 类型、:attr:`Visibility.PUBLIC`
        可见性发布事件，payload 中包含 ``old_phase``、``new_phase``、``round``
        以及调用方传入的 ``context``。

        Args:
            next_phase: 目标游戏阶段。
            context: 可选上下文数据（如 ``{"pk_triggered": True}``），
                会合并到发布事件的 payload 中。

        Raises:
            InvalidTransitionError: 当前阶段到 ``next_phase`` 的迁移路径
                不在 :attr:`VALID_TRANSITIONS` 定义中。
        """
        # 获取当前阶段允许切换的下一个阶段
        allowed = self.VALID_TRANSITIONS.get(self._current_phase, [])
        # 校验目标阶段
        if next_phase not in allowed:
            raise InvalidTransitionError(
                current_state=self._current_phase,
                target_state=next_phase,
            )

        # 当前阶段成为前置阶段
        old_phase = self._current_phase
        # 更新当前阶段
        self._current_phase = next_phase

        # 新的一轮开始：进入 NIGHT_START 时递增轮次
        if next_phase == GamePhase.NIGHT_START:
            self._round += 1

        logger.info(
            "phase_transition",
            game_id=self.game_id,
            old_phase=old_phase.value if old_phase else None,
            new_phase=next_phase.value,
            round=self._round,
        )

        # 绑定当前阶段
        bind_game_context(self.game_id, next_phase.value)

        # 发布阶段变更事件
        await self._publish_phase_change(old_phase, next_phase, context or {})

    async def _publish_phase_change(
        self,
        old_phase: Optional[GamePhase],
        new_phase: GamePhase,
        context: dict,
    ) -> None:
        """
        创建并发布阶段变更事件。

        **Why**: 事件发布逻辑从 ``transition_to`` 中提取为独立方法，
        便于子类覆盖或测试 mock。事件 ID 使用 UUID4 保证全局唯一。

        Args:
            old_phase: 迁移前的阶段，可能为 ``None``。
            new_phase: 迁移后的阶段。
            context: 合并到事件 payload 的额外上下文。
        """
        # 创建事件内容
        payload: dict = {
            "old_phase": old_phase.value if old_phase else None,
            "new_phase": new_phase.value,
            "round": self._round,
            **context,
        }

        # 创建事件实体
        event = Event(
            event_id=str(uuid.uuid4()),
            game_id=self.game_id,
            seq_num=0,  # EventBus 自动分配
            event_type=EventType.PHASE_TRANSITION_EVENT,
            visibility=Visibility.PUBLIC,
            target_agents=[],
            timestamp=datetime.now(timezone.utc),
            payload=payload,
        )

        # 发布事件
        await self.event_bus.publish(event)
