"""AuditLogger 单元测试。

覆盖:
- RejectedAction 不可变性
- log_rejection / log_acceptance 基本功能
- get_rejection_stats 聚合统计
- get_rejection_by_rejector 按来源聚合
- snapshot 快照生成
- reset 重置功能
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.action.audit import (
    AuditLogger,
    AuditSnapshot,
    RejectedAction,
)
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role
from ai_werewolf_core.schemas.models import AgentAction


def make_action(
    actor_id: str = "player_1",
    action_type: ActionType = ActionType.SPEAK,
    target_id: str | None = None,
    phase: GamePhase = GamePhase.DAY_DISCUSSION,
    round_num: int = 1,
) -> AgentAction:
    """快速构造测试用 AgentAction。"""
    return AgentAction(
        action_type=action_type,
        actor_id=actor_id,
        target_id=target_id,
        phase=phase,
        round=round_num,
        reason="测试动作",
    )


class TestRejectedAction:
    """RejectedAction 值对象测试。"""

    def test_frozen_dataclass(self) -> None:
        """RejectedAction 是不可变值对象。"""
        action = make_action()
        record = RejectedAction(
            action=action,
            reason="测试拒绝",
            rejector="ActionValidator",
            actor_role=Role.VILLAGER,
        )
        with pytest.raises(Exception):
            record.reason = "修改失败"  # type: ignore[misc]

    def test_default_timestamp(self) -> None:
        """默认时间戳为 UTC 当前时间。"""
        action = make_action()
        record = RejectedAction(
            action=action,
            reason="测试",
            rejector="ActionValidator",
        )
        assert record.timestamp is not None

    def test_actor_role_optional(self) -> None:
        """actor_role 可以为 None。"""
        action = make_action()
        record = RejectedAction(
            action=action,
            reason="测试",
            rejector="AntiCheatDetector",
        )
        assert record.actor_role is None


class TestAuditLoggerBasic:
    """AuditLogger 基础功能测试。"""

    def test_init(self) -> None:
        """初始化 AuditLogger。"""
        audit = AuditLogger("game_001")
        assert audit.game_id == "game_001"
        assert len(audit.rejected) == 0
        assert len(audit.accepted_count) == 0

    def test_log_acceptance(self) -> None:
        """记录通过事件。"""
        audit = AuditLogger("game_001")
        audit.log_acceptance("player_1")
        audit.log_acceptance("player_1")
        audit.log_acceptance("player_2")
        assert audit.accepted_count["player_1"] == 2
        assert audit.accepted_count["player_2"] == 1

    def test_log_rejection(self) -> None:
        """记录拒绝事件。"""
        audit = AuditLogger("game_001")
        action = make_action()
        audit.log_rejection(
            RejectedAction(action=action, reason="测试", rejector="ActionValidator")
        )
        assert len(audit.rejected) == 1
        assert audit.rejected[0].reason == "测试"


class TestAuditLoggerStats:
    """AuditLogger 统计功能测试。"""

    def test_get_rejection_stats_empty(self) -> None:
        """空记录时返回空字典。"""
        audit = AuditLogger("game_001")
        stats = audit.get_rejection_stats()
        assert stats == {}

    def test_get_rejection_stats(self) -> None:
        """按 actor_id 聚合拒绝次数。"""
        audit = AuditLogger("game_001")
        for i in range(3):
            audit.log_rejection(
                RejectedAction(
                    action=make_action(actor_id="player_1"),
                    reason=f"拒绝{i}",
                    rejector="ActionValidator",
                )
            )
        for i in range(2):
            audit.log_rejection(
                RejectedAction(
                    action=make_action(actor_id="player_2"),
                    reason=f"拒绝{i}",
                    rejector="AntiCheatDetector",
                )
            )
        stats = audit.get_rejection_stats()
        assert stats["player_1"] == 3
        assert stats["player_2"] == 2

    def test_get_rejection_by_rejector(self) -> None:
        """按 rejector 聚合拒绝次数。"""
        audit = AuditLogger("game_001")
        audit.log_rejection(
            RejectedAction(
                action=make_action(),
                reason="测试1",
                rejector="ActionValidator",
            )
        )
        audit.log_rejection(
            RejectedAction(
                action=make_action(),
                reason="测试2",
                rejector="ActionValidator",
            )
        )
        audit.log_rejection(
            RejectedAction(
                action=make_action(),
                reason="测试3",
                rejector="AntiCheatDetector",
            )
        )
        stats = audit.get_rejection_by_rejector()
        assert stats["ActionValidator"] == 2
        assert stats["AntiCheatDetector"] == 1


class TestAuditSnapshot:
    """AuditSnapshot 快照测试。"""

    def test_snapshot_empty(self) -> None:
        """空记录快照。"""
        audit = AuditLogger("game_001")
        snapshot = audit.snapshot()
        assert isinstance(snapshot, AuditSnapshot)
        assert snapshot.game_id == "game_001"
        assert snapshot.total_accepted == 0
        assert snapshot.total_rejected == 0

    def test_snapshot_with_data(self) -> None:
        """带数据的快照。"""
        audit = AuditLogger("game_001")
        audit.log_acceptance("player_1")
        audit.log_acceptance("player_1")
        audit.log_acceptance("player_2")
        audit.log_rejection(
            RejectedAction(
                action=make_action(actor_id="player_1"),
                reason="拒绝",
                rejector="ActionValidator",
            )
        )
        snapshot = audit.snapshot()
        assert snapshot.total_accepted == 3
        assert snapshot.total_rejected == 1
        assert snapshot.rejection_by_actor["player_1"] == 1
        assert snapshot.rejection_by_rejector["ActionValidator"] == 1
        assert len(snapshot.rejected_actions) == 1


class TestAuditLoggerReset:
    """AuditLogger 重置测试。"""

    def test_reset(self) -> None:
        """重置清空所有数据。"""
        audit = AuditLogger("game_001")
        audit.log_acceptance("player_1")
        audit.log_rejection(
            RejectedAction(
                action=make_action(),
                reason="测试",
                rejector="ActionValidator",
            )
        )
        audit.reset()
        assert len(audit.rejected) == 0
        assert len(audit.accepted_count) == 0
