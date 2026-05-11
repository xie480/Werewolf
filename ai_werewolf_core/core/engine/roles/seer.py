"""预言家角色实现。

**Why**: 预言家是好人阵营的核心信息角色，拥有夜间验人技能。
其 :meth:`can_act` 仅允许在 ``NIGHT_SEER_ACT`` 阶段执行 ``SEER_CHECK`` 动作，
且必须处于存活状态。每夜仅能查验一次的限制由引擎层的 PhaseMachine 控制，
不在角色层追踪，保持单一职责原则。
"""

from __future__ import annotations

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase, Role


class SeerRole(BaseRole):
    """预言家 — 拥有夜间验人技能，属于好人阵营。

    预言家每晚可以查验一名玩家的阵营身份（好人或狼人）。
    查验结果只有预言家本人知道（通过 ``PRIVATE`` 可见性事件推送）。

    **Why (角色层不追踪验人次数)**: 验人次数的限制是游戏流程层面的规则，
    应由 PhaseMachine 在每轮 ``NIGHT_SEER_ACT`` 阶段控制"已行动"标记，
    而非角色状态。这保持了单一职责原则，避免角色层与流程层耦合。

    Attributes:
        role_type: 固定为 ``Role.SEER``。
        faction: 固定为 ``Faction.VILLAGER``（好人阵营）。
    """

    role_type: Role = Role.SEER
    faction: Faction = Faction.VILLAGER

    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """校验预言家专属动作 —— 夜间验人。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果预言家在夜间且存活且动作为 SEER_CHECK，否则 ``False``。
        """
        if not self.is_alive:
            return False
        if phase == GamePhase.NIGHT_SEER_ACT and action_type == ActionType.SEER_CHECK:
            return True
        return False
