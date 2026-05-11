"""女巫角色实现。

**Why**: 女巫是好人阵营最强的辅助角色，拥有解药和毒药各一瓶。
其 :meth:`can_act` 需同时校验存活状态、游戏阶段、动作类型以及
物品（解药/毒药）的可用性。物品状态由本模块自行管理，
消费动作由 Action Resolver 在结算成功后调用。
"""

from __future__ import annotations

import structlog

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase, Role


class WitchRole(BaseRole):
    """女巫 — 拥有解药和毒药各一瓶，属于好人阵营。

    女巫在夜间可以分别使用解药（救活被狼人杀害的玩家）和毒药（毒杀一名玩家）。
    两瓶药各有且仅有一瓶，使用后即消耗，整局游戏不可补充。

    **物品管理（关键边界条件）**:
    - ``has_antidote: bool = True`` → 使用后由引擎调用 :meth:`use_antidote` 置为 ``False``。
    - ``has_poison: bool = True`` → 使用后由引擎调用 :meth:`use_poison` 置为 ``False``。
    - 两瓶药不能在同一夜使用 —— 本模块不检查此规则，
      由引擎层的 Action Resolver 在每夜结算时控制。

    **校验逻辑（can_act）**:
    1. 必须存活。
    2. 必须在 ``NIGHT_ACTION`` 阶段。
    3. ``WITCH_SAVE`` → ``has_antidote`` 必须为 ``True``。
    4. ``WITCH_POISON`` → ``has_poison`` 必须为 ``True``。

    Attributes:
        role_type: 固定为 ``Role.WITCH``。
        faction: 固定为 ``Faction.VILLAGER``（好人阵营）。
        has_antidote: 解药是否可用。
        has_poison: 毒药是否可用。
    """

    role_type: Role = Role.WITCH
    faction: Faction = Faction.VILLAGER

    def __init__(self, player_id: str) -> None:
        """初始化女巫角色。

        Args:
            player_id: 绑定的玩家 ID，格式 ``player_{序号}``。
        """
        super().__init__(player_id)
        self.has_antidote: bool = True
        """解药是否尚可使用。"""
        self.has_poison: bool = True
        """毒药是否尚可使用。"""
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger().bind(
            role_type=self.role_type.value,
            player_id=self.player_id,
        )

    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """校验女巫专属动作 —— 夜间用药。

        **Why**: 女巫的可用动作不仅取决于阶段和存活状态，还取决于
        对应药品是否尚未使用。因此校验逻辑比狼人和预言家更复杂。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果女巫在夜间且存活且对应药品可用，否则 ``False``。
        """
        if not self.is_alive:
            return False
        if phase != GamePhase.NIGHT_ACTION:
            return False
        if action_type == ActionType.WITCH_SAVE and self.has_antidote:
            return True
        if action_type == ActionType.WITCH_POISON and self.has_poison:
            return True
        return False

    # ------------------------------------------------------------------
    # 物品消费接口
    # ------------------------------------------------------------------

    def use_antidote(self) -> None:
        """消费解药。

        **Why**: 将物品消费独立于 :meth:`can_act`，确保校验与副作用分离。
        Action Resolver 在动作结算成功后调用此方法，若解药已使用则抛出
        ``ValueError`` 防止重复消费。

        Raises:
            ValueError: 如果解药已经被使用。
        """
        if not self.has_antidote:
            raise ValueError(
                f"解药已使用，玩家 [{self.player_id}] 无法再次使用解药。"
            )
        self.has_antidote = False
        self._logger.info("antidote_used")

    def use_poison(self) -> None:
        """消费毒药。

        **Why**: 同 :meth:`use_antidote`，将副作用与校验分离。

        Raises:
            ValueError: 如果毒药已经被使用。
        """
        if not self.has_poison:
            raise ValueError(
                f"毒药已使用，玩家 [{self.player_id}] 无法再次使用毒药。"
            )
        self.has_poison = False
        self._logger.info("poison_used")
