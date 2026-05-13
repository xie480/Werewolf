"""
Game Engine 异常定义模块。

**Why**: 状态流转校验失败、对局不可运行等场景必须抛出明确的自定义异常，
以便上层调用方（如 API 层、Worker 进程）能够精确捕获并做出响应。
这避免了依赖过于宽泛的 Python 内置异常（如 ``ValueError``），
防止异常处理逻辑误捕获无关错误。
"""

from __future__ import annotations

from typing import Optional

from ai_werewolf_core.schemas.enums import GamePhase, GameStatus
from ai_werewolf_core.schemas.models import AgentAction


class InvalidTransitionError(Exception):
    """
    非法状态流转异常。

    当调用方试图执行一个不在 :attr:`PhaseStateMachine.VALID_TRANSITIONS`
    或 :attr:`LifecycleManager.VALID_STATUS_TRANSITIONS` 中定义的
    状态流转时抛出。

    Attributes:
        current_state: 当前所处的阶段或状态。
        target_state: 试图跳转到的目标阶段或状态。
        message: 人类可读的错误描述。
    """

    def __init__(
        self,
        current_state: Optional[GamePhase | GameStatus],
        target_state: GamePhase | GameStatus,
        message: Optional[str] = None,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        if message is None:
            message = (
                f"非法的状态流转: 无法从 [{current_state}] 跳转到 [{target_state}]，"
                f"该路径不在预定义的合法流转图中。"
            )
        super().__init__(message)


class GameNotRunnableError(Exception):
    """
    游戏不可运行异常。

    当调用方试图在游戏状态不是 :attr:`GameStatus.RUNNING` 时
    推进游戏阶段（调用 :meth:`LifecycleManager.advance_phase`）时抛出。

    Attributes:
        current_status: 当前的对局生命周期状态。
        game_id: 对局 ID。
    """

    def __init__(
        self,
        current_status: GameStatus,
        game_id: str,
        message: Optional[str] = None,
    ) -> None:
        self.current_status = current_status
        self.game_id = game_id
        if message is None:
            message = (
                f"游戏 [{game_id}] 当前状态为 [{current_status}]，"
                f"仅当状态为 [{GameStatus.RUNNING}] 时才能推进阶段。"
            )
        super().__init__(message)


class ActionValidationError(Exception):
    """行动校验失败异常。

    当 Agent 提交的动作未通过 Role System 或 Manager 的合法性校验时抛出。
    调用方（如 API 层）应捕获此异常并向 Agent 返回明确的拒绝原因。

    **Why (统一异常类)**: 将行动校验失败统一为一个异常类型，
    避免 VoteManager、SpecialActionResolver、ActionResolver 各自定义
    不同异常导致上层需要处理多种异常类型。

    Attributes:
        action: 被拒绝的原始动作。
        reason: 拒绝原因描述。
    """

    def __init__(self, action: AgentAction, reason: str) -> None:
        self.action = action
        self.reason = reason
        action_type_str = getattr(action.action_type, 'value', action.action_type)
        super().__init__(
            f"行动校验失败 [actor={action.actor_id}, "
            f"action={action_type_str}]: {reason}"
        )


class ResolverError(Exception):
    """结算器内部异常。

    当结算过程中出现不可恢复的逻辑错误（如尝试结算未开始的对局、
    重复结算同一夜晚等）时抛出。

    Attributes:
        game_id: 对局 ID。
        message: 错误描述。
    """

    def __init__(self, game_id: str, message: str) -> None:
        self.game_id = game_id
        self.message = message
        super().__init__(f"结算器错误 [game={game_id}]: {message}")
