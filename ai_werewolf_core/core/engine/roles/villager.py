"""村民角色实现。

**Why**: 无特殊能力的村民是游戏的基础角色，其 :meth:`can_act` 仅需返回 ``False``
（无专属动作），所有合法动作由 :meth:`BaseRole.can_perform_common_action` 处理。
这是最简单的角色实现，用于验证角色系统的基础架构。
"""

from __future__ import annotations

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase, Role


class VillagerRole(BaseRole):
    """村民 — 无夜间技能，仅拥有白天发言和投票的通用权利。

    村民是狼人杀中最基础的角色，属于好人阵营。
    村民的唯一目标是通过白天的讨论和投票找出并放逐狼人。

    Attributes:
        role_type: 固定为 ``Role.VILLAGER``。
        faction: 固定为 ``Faction.VILLAGER``（好人阵营）。
    """

    role_type: Role = Role.VILLAGER
    faction: Faction = Faction.VILLAGER

    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """村民无专属动作，始终返回 ``False``。

        **Why**: 村民的所有合法行为（发言、投票、空过）均已由基类的
        :meth:`BaseRole.can_perform_common_action` 覆盖，此处无需额外校验。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            始终返回 ``False``。
        """
        return False
