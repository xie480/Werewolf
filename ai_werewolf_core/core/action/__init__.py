"""动作校验与防作弊系统模块。

本模块作为游戏入口守卫，在执行任何 Agent 动作之前构建一道
独立于业务逻辑的纯规则防火墙。

核心组件:
- ActionGate: 统一入口 (Facade)，编排完整的校验管道
- ActionValidator: 基本合法性校验（结构/阶段/生存状态/冷却）
- AntiCheatDetector: 防作弊检测（幽灵玩家/越权/重放/超频）
- AuditLogger: 审计日志（拒绝事件记录与聚合统计）

数据类:
- ValidationResult: 校验结果
- InspectionResult: 检测结果
- AdmitResult: 准入结果
- RejectedAction: 拒绝事件记录
- ViolationRecord: 违规记录
- AuditSnapshot: 审计快照

使用方式::

    from ai_werewolf_core.core.action import ActionGate

    gate = ActionGate("game_001")
    result = await gate.admit(action, role, roles, current_phase)
"""

from ai_werewolf_core.core.action.anti_cheat import (
    AntiCheatDetector,
    InspectionResult,
    PenaltyType,
    ViolationRecord,
)
from ai_werewolf_core.core.action.audit import (
    AuditLogger,
    AuditSnapshot,
    RejectedAction,
)
from ai_werewolf_core.core.action.gate import (
    ActionGate,
    AdmitResult,
)
from ai_werewolf_core.core.action.validator import (
    ActionValidator,
    ValidationResult,
)

__all__ = [
    # 核心组件
    "ActionGate",
    "ActionValidator",
    "AntiCheatDetector",
    "AuditLogger",
    # 结果数据类
    "AdmitResult",
    "ValidationResult",
    "InspectionResult",
    # 审计数据类
    "RejectedAction",
    "ViolationRecord",
    "AuditSnapshot",
    # 枚举
    "PenaltyType",
]
