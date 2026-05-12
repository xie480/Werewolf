"""动作门控 —— 动作校验与防作弊系统的统一入口 (Facade)。

**Why (Facade 模式)**: GameEngine 不需要知道内部有多少个校验组件，
只需要一个 Admit/Reject 的二值结果。Gate 封装了所有校验和审计逻辑，单点调用即可。

编排完整的校验管道：
1. ActionValidator.validate()     —— 基本合法性校验（含角色声明的生存状态校验）
2. AntiCheatDetector.inspect()    —— 防作弊检测
3. AuditLogger.log_*()            —— 审计记录

返回最终的二值结果：Admit 或 Reject（含原因）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import structlog

from ai_werewolf_core.core.action.anti_cheat import AntiCheatDetector, InspectionResult, PenaltyType
from ai_werewolf_core.core.action.audit import AuditLogger, AuditSnapshot, RejectedAction
from ai_werewolf_core.core.action.validator import (
    ActionValidator,
    ValidationResult,
    _safe_enum_value,
)
from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.schemas.enums import GamePhase, Role
from ai_werewolf_core.schemas.models import AgentAction

logger = structlog.get_logger(__name__)


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class AdmitResult:
    """动作准入结果 —— Gate 的最终输出。

    Attributes:
        admitted: 是否准予通过（True = 放行，False = 拒绝）。
        reason: 拒绝原因（仅在 admitted=False 时有意义）。
        rejected_by: 拒绝来源（"ActionValidator" / "AntiCheatDetector" / "RoleSystem"）。
    """

    admitted: bool
    reason: str = ""
    rejected_by: str = ""

    @classmethod
    def accepted(cls) -> "AdmitResult":
        """快速构造通过结果。"""
        return cls(admitted=True)

    @classmethod
    def rejected(cls, reason: str, rejected_by: str) -> "AdmitResult":
        """快速构造拒绝结果。

        Args:
            reason: 人类可读的拒绝原因。
            rejected_by: 拒绝来源标识。
        """
        return cls(admitted=False, reason=reason, rejected_by=rejected_by)


# ============================================================================
# ActionGate
# ============================================================================

class ActionGate:
    """动作门控 —— 动作校验与防作弊系统的统一入口 (Facade)。

    编排完整的校验管道：
    1. ActionValidator.validate()     —— 基本合法性校验
    2. AntiCheatDetector.inspect()    —— 防作弊检测
    3. AuditLogger.log_*()            —— 审计记录

    使用方式::

        gate = ActionGate("game_001")
        result = await gate.admit(action, role, roles, current_phase)
        if not result.admitted:
            # 处理拒绝

    Attributes:
        game_id: 绑定的对局 ID。
        validator: ActionValidator 实例。
        anti_cheat: AntiCheatDetector 实例。
        audit: AuditLogger 实例。
    """

    def __init__(self, game_id: str) -> None:
        """初始化动作门控。

        Args:
            game_id: 对局唯一标识。
        """
        self.game_id: str = game_id
        self.validator: ActionValidator = ActionValidator(game_id)
        self.anti_cheat: AntiCheatDetector = AntiCheatDetector(game_id)
        self.audit: AuditLogger = AuditLogger(game_id)

    # ------------------------------------------------------------------
    # 准入判断
    # ------------------------------------------------------------------

    async def admit(
        self,
        action: AgentAction,
        role: BaseRole,
        roles: dict[str, BaseRole],
        current_phase: GamePhase,
    ) -> AdmitResult:
        """动作准入判断 —— 通过全部校验链方可放行。

        校验链（短路求值，任一步失败即停止）:
        1. ActionValidator.validate()  → 失败则记录拒绝并返回 Rejected
        2. AntiCheatDetector.inspect() → 失败则记录违规并返回 Rejected
        3. AuditLogger.log_acceptance() → 通过

        Args:
            action: 待校验的 AgentAction。
            role: 行动者的角色对象，用于获取生存状态声明。
            roles: 当前对局的所有角色映射（供 Detector 使用）。
            current_phase: PhaseStateMachine 的当前阶段。

        Returns:
            AdmitResult 实例。
        """
        # Step 1: ActionValidator 基本合法性校验
        validation_result = await self.validator.validate(action, role, current_phase)
        if not validation_result.is_valid:
            self.audit.log_rejection(
                RejectedAction(
                    action=action,
                    reason=validation_result.reason,
                    rejector="ActionValidator",
                    actor_role=role.role_type if hasattr(role, 'role_type') else None,
                )
            )
            logger.info(
                "动作被 ActionValidator 拒绝",
                game_id=self.game_id,
                actor_id=action.actor_id,
                action_type=_safe_enum_value(action.action_type),
                reason=validation_result.reason,
                rejected_by=validation_result.rejected_by,
            )
            return AdmitResult.rejected(
                reason=validation_result.reason,
                rejected_by=f"ActionValidator.{validation_result.rejected_by}",
            )

        # Step 2: AntiCheatDetector 防作弊检测
        inspection_result = self.anti_cheat.inspect(action, roles)
        if not inspection_result.is_clean:
            self.audit.log_rejection(
                RejectedAction(
                    action=action,
                    reason=inspection_result.violation_type,
                    rejector="AntiCheatDetector",
                    actor_role=role.role_type if hasattr(role, 'role_type') else None,
                )
            )
            logger.info(
                "动作被 AntiCheatDetector 拒绝",
                game_id=self.game_id,
                actor_id=action.actor_id,
                action_type=_safe_enum_value(action.action_type),
                violation_type=inspection_result.violation_type,
                penalty=inspection_result.penalty.value if inspection_result.penalty else "none",
            )
            return AdmitResult.rejected(
                reason=inspection_result.violation_type,
                rejected_by="AntiCheatDetector",
            )

        # Step 3: 通过全部校验，记录通过
        self.audit.log_acceptance(action.actor_id)
        logger.debug(
            "动作通过全部校验",
            game_id=self.game_id,
            actor_id=action.actor_id,
            action_type=_safe_enum_value(action.action_type),
            phase=_safe_enum_value(current_phase),
        )
        return AdmitResult.accepted()

    # ------------------------------------------------------------------
    # 统计查询
    # ------------------------------------------------------------------

    def get_rejection_stats(self) -> dict[str, int]:
        """按 actor_id 聚合的拒绝统计。

        Returns:
            ``{actor_id: 拒绝次数}`` 的字典。
        """
        return self.audit.get_rejection_stats()

    def get_violations(self) -> list:
        """获取所有防作弊违规记录。

        Returns:
            违规记录列表。
        """
        return self.anti_cheat.get_violations()

    def snapshot(self) -> AuditSnapshot:
        """生成当前审计快照。

        Returns:
            AuditSnapshot 实例。
        """
        return self.audit.snapshot()

    # ------------------------------------------------------------------
    # 阶段管理
    # ------------------------------------------------------------------

    def on_phase_change(self) -> None:
        """阶段切换时调用 —— 重置所有阶段级别的计数器。

        **Why**: 冷却追踪、提交计数等应在阶段切换后清零，
        因为不同阶段的动作互不干扰。
        """
        self.validator.reset_cooldowns()
        self.anti_cheat.reset_phase_counters()

    # ------------------------------------------------------------------
    # 重置（测试用）
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置所有状态（仅用于测试）。

        **Why**: 测试环境中需要在每个测试用例前后清理状态，
        确保测试隔离性。生产代码不应调用此方法。
        """
        self.validator.reset_cooldowns()
        self.anti_cheat.reset()
        self.audit.reset()
