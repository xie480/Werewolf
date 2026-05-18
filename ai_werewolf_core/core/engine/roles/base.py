"""角色基类 —— 所有具体角色的抽象父类。

**Why (架构)**: 使用 ABC 强制所有子类实现 :meth:`can_act`，确保 Action Resolver
可以安全地调用统一的校验接口。每个子类必须定义 ``role_type`` 和 ``faction``
类属性，以便工厂函数和日志系统能够在不实例化的情况下获取元信息。

参考: :doc:`docs/plan/角色与能力系统设计`
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import structlog

from ai_werewolf_core.schemas.enums import (
    ActionType,
    Faction,
    GamePhase,
    Role,
    SurvivalRequirement,
)


class BaseRole(ABC):
    """角色基类 —— 所有具体角色的抽象父类。

    **Why**: 狼人杀中每个角色拥有不同的技能和行动窗口。
    通过 ABC 定义统一的校验接口，Game Engine 的 Action Resolver
    可以无需关心具体角色类型，只调用 :meth:`validate_action` 即可完成权限判定。

    Attributes:
        role_type: 角色类型枚举，子类必须覆盖。
        faction: 所属阵营枚举，子类必须覆盖。
        player_id: 绑定的玩家 ID。
        is_alive: 玩家是否存活。
    """

    role_type: Role
    """角色类型 —— 子类必须覆盖此 class attribute。"""

    faction: Faction
    """所属阵营 —— 子类必须覆盖此 class attribute。"""

    def __init__(self, player_id: str) -> None:
        """初始化角色实例。

        Args:
            player_id: 绑定的玩家 ID，格式 ``player_{序号}``，如 ``player_1``。
        """
        self.player_id: str = player_id
        self.is_alive: bool = True
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger().bind(
            role_type=self.role_type.value,
            player_id=self.player_id,
        )

    @property
    def seat_number(self) -> int:
        """获取玩家座位号。
        
        从 player_id (如 'player_1') 中提取数字部分。
        
        Returns:
            座位号整数。
        """
        try:
            return int(self.player_id.split("_")[1])
        except (IndexError, ValueError) as e:
            raise ValueError(f"无法从 player_id 提取座位号: {self.player_id}") from e

    # ------------------------------------------------------------------
    # 通用动作校验
    # ------------------------------------------------------------------

    def can_perform_common_action(
        self, phase: GamePhase, action_type: ActionType
    ) -> bool:
        """校验通用动作（发言、投票、空过）。

        **Why**: 发言和投票是所有存活玩家共有的权利，不需要每个角色
        重复实现相同的校验逻辑。集中在此方法后，子类只需关注专属技能。

        规则：
        - **死亡玩家不能执行任何动作**（包括通用动作）。
        - ``SPEAK`` 仅在 ``DAY_DISCUSSION`` / ``DAY_PK_DISCUSSION`` /
          ``LAST_WORDS`` 阶段合法。
        - ``VOTE`` 仅在 ``DAY_VOTE`` / ``DAY_PK_VOTE`` 阶段合法。
        - ``PASS`` 在所有阶段对所有存活角色合法（表示主动放弃行动）。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果该动作在此阶段合法，否则 ``False``。
        """
        if not self.is_alive:
            return False

        if action_type == ActionType.SPEAK and phase in (
            GamePhase.DAY_DISCUSSION,
            GamePhase.DAY_PK_DISCUSSION,
            GamePhase.LAST_WORDS,
        ):
            return True

        if action_type == ActionType.VOTE and phase in (
            GamePhase.DAY_VOTE,
            GamePhase.DAY_PK_VOTE,
        ):
            return True

        # PASS 在所有阶段合法 —— Why: Agent 可能需要显式声明"不做任何事"
        if action_type == ActionType.PASS:
            return True

        return False

    # ------------------------------------------------------------------
    # 抽象接口 —— 子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    def can_act(self, phase: GamePhase, action_type: ActionType) -> bool:
        """校验角色专属动作 —— 子类必须实现。

        **Why**: 每个角色的技能窗口不同（狼人只能夜间刀人、女巫只能
        夜间用药等），必须由子类各自实现具体校验逻辑。

        Action Resolver 调用链：
        1. 先调用 :meth:`can_perform_common_action` 校验通用动作。
        2. 若通用校验返回 ``False``，再调用 :meth:`can_act` 校验角色专属动作。
        3. 两次校验均返回 ``False`` 时拒绝动作。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果该角色在此阶段允许执行此动作，否则 ``False``。
        """
        ...

    # ------------------------------------------------------------------
    # 统一校验入口
    # ------------------------------------------------------------------

    def validate_action(self, phase: GamePhase, action_type: ActionType) -> bool:
        """统一动作校验入口。

        **Why**: Action Resolver 不需要关心具体是通用动作还是角色专属动作，
        只需调用此方法即可获取最终判定结果。
        内部先尝试通用校验，再尝试角色专属校验。

        Args:
            phase: 当前游戏阶段。
            action_type: 待校验的动作类型。

        Returns:
            ``True`` 如果动作合法（无论属于通用还是角色专属），否则 ``False``。
        """
        if self.can_perform_common_action(phase, action_type):
            return True
        if self.can_act(phase, action_type):
            return True
        return False

    # ------------------------------------------------------------------
    # 生存状态需求声明
    # ------------------------------------------------------------------

    def get_survival_requirement(
        self, action_type: ActionType | str
    ) -> SurvivalRequirement:
        """返回此角色对指定动作类型的生存状态要求。

        **Why**: 集中式 ActionValidator 在生存状态校验环节调用此方法，
        获取角色声明的约束，然后去 Redis BitMap 验证实际状态。
        规则由角色定义（开闭原则），校验由防火墙执行（职责分离）。

        兼容字符串和枚举类型：AgentAction 的 ``use_enum_values = True``
        会将 action_type 序列化为字符串，此处统一转换为枚举进行比较。

        默认行为：PASS 动作不校验生存状态，其余动作要求存活。
        子类覆盖此方法以声明不同的约束（如猎人 HUNTER_SHOOT 要求死亡）。

        Args:
            action_type: 待校验的动作类型（ActionType 枚举或字符串值）。

        Returns:
            SurvivalRequirement 枚举值。
        """
        # 兼容字符串输入（use_enum_values = True 时 action_type 为字符串）
        if isinstance(action_type, str):
            action_type = ActionType(action_type)
        if action_type == ActionType.PASS:
            return SurvivalRequirement.ANY
        return SurvivalRequirement.MUST_BE_ALIVE

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    def die(self) -> None:
        """标记角色死亡。

        **Why**: 集中管理死亡逻辑（如清理状态、触发遗言判断等），
        避免在多处重复设置 ``is_alive = False`` 导致不一致。
        后续可在此方法中扩展遗言触发、猎人开枪等逻辑。
        """
        self.is_alive = False
        self._logger.info("role_died")

    def revive(self) -> None:
        """复活角色。

        **Why**: 女巫解药可以复活被狼人杀害的玩家。提供显式的复活方法
        避免直接操作 ``is_alive`` 导致的状态不一致。
        """
        self.is_alive = True
        self._logger.info("role_revived")
