"""狼人角色实现。

**Why**: 狼人是游戏的核心反派角色，拥有夜间刀人技能。
其 :meth:`can_act` 仅允许在 ``NIGHT_ACTION`` 阶段执行 ``WOLF_KILL`` 动作，
且必须处于存活状态。
"""

from __future__ import annotations

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase, Role


class WerewolfRole(BaseRole):
    """狼人 — 拥有夜间刀人技能，属于狼人阵营。

    狼人在夜间可以与其他狼队友协商选择一名玩家进行杀害。
    狼人阵营的胜利条件是存活狼人数量大于等于存活好人数量。

    **校验逻辑**:
    1. 必须存活（死亡狼人不能刀人）。
    2. 必须在 ``NIGHT_ACTION`` 阶段。
    3. 动作类型必须是 ``WOLF_KILL``。

    Attributes:
        role_type: 固定为 ``Role.WEREWOLF``。
        faction: 固定为 ``Faction.WEREWOLF``（狼人阵营）。
    """

    role_type: Role = Role.WEREWOLF
    faction: Faction = Faction.WEREWOLF

    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """校验狼人专属动作 —— 夜间刀人。

        **Why (存活校验放在此处而非基类)**: 基类的通用动作校验已检查
        存活状态，但角色专属动作的校验独立于通用动作，因此此处也需要
        显式检查 ``is_alive``，防止调用方跳过通用校验直接调用此方法。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果狼人在夜间且存活且动作为 WOLF_KILL，否则 ``False``。
        """
        if not self.is_alive:
            return False
        if phase == GamePhase.NIGHT_ACTION and action_type == ActionType.WOLF_KILL:
            return True
        return False
