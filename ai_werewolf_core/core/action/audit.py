"""审计日志记录器 —— 记录所有拒绝事件，供评测和排障。

**Why**: structlog 是通用日志框架，其结构化输出面向人类运维。
审计日志则需要面向评测系统 (Evaluation System) 和自动告警——
需要明确的 schema、可查询的存储、以及可量化的聚合指标。

本模块提供:
- :class:`RejectedAction`: 不可变值对象，记录单次拒绝事件的全部上下文。
- :class:`AuditLogger`: 记录器，追踪所有通过/拒绝事件，支持按 actor_id 聚合统计。
- :class:`AuditSnapshot`: 审计快照，可发布到 EventBus 或存入复盘数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ai_werewolf_core.schemas.enums import Role
from ai_werewolf_core.schemas.models import AgentAction


@dataclass(frozen=True)
class RejectedAction:
    """被拒绝的动作记录 —— 不可变值对象。

    **Why frozen**: 审计记录一旦写入就不应修改，确保评测数据的完整性。
    使用 ``dataclass(frozen=True)`` 保证不可变性。

    Attributes:
        action: 原始 AgentAction。
        reason: 拒绝原因描述（人类可读）。
        rejector: 拒绝来源（"ActionValidator" / "AntiCheatDetector" / "RoleSystem"）。
        timestamp: 拒绝发生时间（UTC）。
        actor_role: 行动者的角色类型（可选，用于按角色聚合统计）。
    """

    action: AgentAction
    reason: str
    rejector: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor_role: Optional[Role] = None


@dataclass
class AuditSnapshot:
    """审计快照 —— 某一时刻的审计统计摘要。

    可发布到 EventBus 或存入复盘数据，供评测系统消费。

    Attributes:
        game_id: 对局 ID。
        total_accepted: 累计通过次数。
        total_rejected: 累计拒绝次数。
        rejected_actions: 被拒绝的动作列表。
        rejection_by_actor: 按 actor_id 聚合的拒绝次数。
        rejection_by_rejector: 按拒绝来源聚合的拒绝次数。
    """

    game_id: str
    total_accepted: int = 0
    total_rejected: int = 0
    rejected_actions: list[RejectedAction] = field(default_factory=list)
    rejection_by_actor: dict[str, int] = field(default_factory=dict)
    rejection_by_rejector: dict[str, int] = field(default_factory=dict)


class AuditLogger:
    """审计日志记录器 —— 记录所有拒绝事件，供评测和排障。

    **Why**: 与 structlog 分离，提供面向评测系统的结构化审计数据。
    支持按 actor_id 聚合拒绝统计，用于衡量 Agent 行为质量。

    使用方式::

        audit = AuditLogger("game_001")
        audit.log_acceptance("player_1")
        audit.log_rejection(RejectedAction(...))
        stats = audit.get_rejection_stats()
        snapshot = audit.snapshot()

    Attributes:
        game_id: 绑定的对局 ID。
        rejected: 被拒绝的动作列表（按时间顺序）。
        accepted_count: actor_id → 通过次数。
    """

    def __init__(self, game_id: str) -> None:
        """初始化审计日志记录器。

        Args:
            game_id: 对局唯一标识。
        """
        self.game_id: str = game_id
        self.rejected: list[RejectedAction] = []
        self.accepted_count: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 记录方法
    # ------------------------------------------------------------------

    def log_rejection(self, rejected: RejectedAction) -> None:
        """记录一次拒绝事件。

        Args:
            rejected: 被拒绝的动作记录。
        """
        self.rejected.append(rejected)

    def log_acceptance(self, actor_id: str) -> None:
        """记录一次通过事件。

        Args:
            actor_id: 行动者 ID。
        """
        self.accepted_count[actor_id] = self.accepted_count.get(actor_id, 0) + 1

    # ------------------------------------------------------------------
    # 统计查询
    # ------------------------------------------------------------------

    def get_rejection_stats(self) -> dict[str, int]:
        """按 actor_id 聚合的拒绝次数统计。

        **Why**: 评测系统需要按玩家维度量化 Agent 的行为质量。
        拒绝次数高的 Agent 可能存在提示词设计问题或 LLM 幻觉。

        Returns:
            ``{actor_id: 拒绝次数}`` 的字典。
        """
        stats: dict[str, int] = {}
        for entry in self.rejected:
            actor_id = entry.action.actor_id
            stats[actor_id] = stats.get(actor_id, 0) + 1
        return stats

    def get_rejection_by_rejector(self) -> dict[str, int]:
        """按拒绝来源聚合的拒绝次数统计。

        Returns:
            ``{rejector: 拒绝次数}`` 的字典。
        """
        stats: dict[str, int] = {}
        for entry in self.rejected:
            stats[entry.rejector] = stats.get(entry.rejector, 0) + 1
        return stats

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def snapshot(self) -> AuditSnapshot:
        """生成当前审计快照。

        **Why**: 在阶段结算或对局结束时生成快照，
        可通过 EventBus 发布或存入复盘数据供后续分析。

        Returns:
            AuditSnapshot 实例。
        """
        return AuditSnapshot(
            game_id=self.game_id,
            total_accepted=sum(self.accepted_count.values()),
            total_rejected=len(self.rejected),
            rejected_actions=list(self.rejected),
            rejection_by_actor=self.get_rejection_stats(),
            rejection_by_rejector=self.get_rejection_by_rejector(),
        )

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """重置所有审计数据（仅用于测试）。

        **Why**: 测试环境中需要在每个测试用例前后清理状态，
        确保测试隔离性。生产代码不应调用此方法。
        """
        self.rejected.clear()
        self.accepted_count.clear()
