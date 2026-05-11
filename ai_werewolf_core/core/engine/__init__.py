"""
Game Engine 核心模块 —— 状态机与生命周期管理。

本模块是狼人杀 Game Engine 的核心骨架，包含:
- :class:`PhaseStateMachine`: 硬编码的游戏阶段状态机，管理对局内阶段流转。
- :class:`LifecycleManager`: 全局生命周期管理器，协调对局从创建到结束的全流程。
- :class:`InvalidTransitionError`: 非法状态流转异常。
- :class:`GameNotRunnableError`: 游戏不可运行异常。
"""

from .exceptions import GameNotRunnableError, InvalidTransitionError
from .lifecycle import LifecycleManager
from .state_machine import PhaseStateMachine

__all__ = [
    "PhaseStateMachine",
    "LifecycleManager",
    "InvalidTransitionError",
    "GameNotRunnableError",
]
