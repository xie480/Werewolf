"""
Game Engine 核心模块 —— 状态机、生命周期管理、行动结算与胜负判定。

本模块是狼人杀 Game Engine 的核心骨架，包含:
- :class:`PhaseStateMachine`: 硬编码的游戏阶段状态机，管理对局内阶段流转。
- :class:`LifecycleManager`: 全局生命周期管理器，协调对局从创建到结束的全流程。
- :class:`ActionResolver`: 夜晚行动解析与结算器，负责夜晚动作的暂存、校验与统一结算。
- :class:`VoteManager`: 白天投票管理器，负责选票收集、校验、计票与即时放逐结算。
- :class:`VoteResolveResult`: 投票结算结果数据类，包含平票状态和计票明细。
- :class:`SpecialActionResolver`: 白天特殊行动结算器，负责猎人开枪等即时技能处理。
- :class:`SpecialActionResult`: 特殊行动结算结果数据类。
- :class:`WinEvaluator`: 胜负判定器，基于存活玩家阵营分布判定屠边/屠城条件。
- :class:`NightResolveResult`: 夜晚结算结果数据类。
- :class:`WinEvaluationResult`: 胜负判定结果数据类。
- :class:`ActionValidationError`: 行动校验失败异常。
- :class:`ResolverError`: 结算器内部异常。
- :class:`InvalidTransitionError`: 非法状态流转异常。
- :class:`GameNotRunnableError`: 游戏不可运行异常。
"""

from .exceptions import (
    ActionValidationError,
    GameNotRunnableError,
    InvalidTransitionError,
    ResolverError,
)
from .evaluator import WinEvaluator, WinEvaluationResult
from .lifecycle import LifecycleManager
from .resolver import ActionResolver, NightResolveResult
from .special_action_resolver import SpecialActionResolver, SpecialActionResult
from .state_machine import PhaseStateMachine
from .vote_manager import VoteManager, VoteResolveResult

__all__ = [
    "PhaseStateMachine",
    "LifecycleManager",
    "ActionResolver",
    "VoteManager",
    "VoteResolveResult",
    "SpecialActionResolver",
    "SpecialActionResult",
    "WinEvaluator",
    "NightResolveResult",
    "WinEvaluationResult",
    "ActionValidationError",
    "ResolverError",
    "InvalidTransitionError",
    "GameNotRunnableError",
]
