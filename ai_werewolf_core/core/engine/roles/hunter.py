"""猎人角色实现。

**Why**: 猎人是好人阵营的强神角色，死亡时可开枪带走一名玩家。
其 :meth:`can_act` 仅在 ``HUNTER_SHOOT`` 阶段生效，。被毒杀时不能开枪的限制由
Action Resolver 根据死亡原因判定，不在角色层处理。
"""

from __future__ import annotations

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import (
    ActionType,
    Faction,
    GamePhase,
    Role,
    SurvivalRequirement,
)


class HunterRole(BaseRole):
    """猎人 — 死亡时可开枪带走一名玩家，属于好人阵营。

    猎人在被狼人杀害或被投票放逐时可以开枪击杀任意一名玩家。
    **被女巫毒杀的猎人不能开枪** — 此条件不在角色层校验，
    由 Action Resolver 根据死亡原因（``cause_of_death``）判定。

    Attributes:
        role_type: 固定为 ``Role.HUNTER``。
        faction: 固定为 ``Faction.VILLAGER``（好人阵营）。
    """

    role_type: Role = Role.HUNTER
    faction: Faction = Faction.VILLAGER

    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """校验猎人专属动作 —— 死亡时开枪。

        **边界条件**:
        - 猎人被女巫毒杀时不能开枪 —— 此规则不由本方法校验，
          由 Action Resolver 层根据死亡来源判定是否进入 ``HUNTER_SHOOT`` 阶段。
          如果在 HUNTER_SHOOT 阶段被调用，说明开枪资格已被 Action Resolver 确认。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果角色死亡，当前为 HUNTER_SHOOT 阶段且动作为 HUNTER_SHOOT
            否则 ``False``。
        """
        if self.is_alive:
            return False
        if phase == GamePhase.HUNTER_SHOOT and action_type == ActionType.HUNTER_SHOOT:
            return True
        return False

    def get_survival_requirement(
        self, action_type: ActionType | str
    ) -> SurvivalRequirement:
        """返回猎人对指定动作类型的生存状态要求。

        覆盖基类默认实现：HUNTER_SHOOT 要求角色必须死亡，
        其余动作沿用基类的默认行为（MUST_BE_ALIVE，PASS 为 ANY）。

        兼容字符串和枚举类型（AgentAction 的 use_enum_values = True
        会将 action_type 序列化为字符串）。

        Args:
            action_type: 待校验的动作类型（ActionType 枚举或字符串值）。

        Returns:
            SurvivalRequirement 枚举值。
        """
        # 兼容字符串输入
        if isinstance(action_type, str):
            action_type = ActionType(action_type)
        if action_type == ActionType.HUNTER_SHOOT:
            return SurvivalRequirement.MUST_BE_DEAD
        return super().get_survival_requirement(action_type)
