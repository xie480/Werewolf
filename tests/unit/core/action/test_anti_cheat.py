"""AntiCheatDetector 单元测试。

覆盖:
- 幽灵玩家检测（actor_id 不在 roles 中）
- 跨阵营越权检测（非狼人提交 WOLF_KILL，非夜间阶段提交 WOLF_KILL）
- 重放攻击检测（相同动作重复提交）
- 超频提交检测（超过阈值触发限速）
- 限速状态管理
- 惩罚应用
- 违规记录
"""

from __future__ import annotations

import time

import pytest

from ai_werewolf_core.core.action.anti_cheat import (
    MAX_ACTIONS_PER_PHASE,
    AntiCheatDetector,
    InspectionResult,
    PenaltyType,
    ViolationRecord,
)
from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, Faction, GamePhase, Role


def make_action(
    actor_id: str = "player_1",
    action_type: ActionType = ActionType.SPEAK,
    target_id: str | None = None,
    phase: GamePhase = GamePhase.DAY_DISCUSSION,
    round_num: int = 1,
) -> "AgentAction":
    """快速构造测试用 AgentAction。"""
    from ai_werewolf_core.schemas.models import AgentAction

    return AgentAction(
        action_type=action_type,
        actor_id=actor_id,
        target_id=target_id,
        phase=phase,
        round=round_num,
        reason="测试动作",
    )


def make_roles(*player_ids: str) -> dict:
    """快速构造测试用 roles 字典（全部为村民）。"""
    roles = {}
    for pid in player_ids:
        role = create_role(Role.VILLAGER, pid)
        roles[pid] = role
    return roles


@pytest.fixture
def detector() -> AntiCheatDetector:
    """创建防作弊检测器实例。"""
    return AntiCheatDetector("game_001")


@pytest.fixture
def roles() -> dict:
    """创建基础 roles 字典（含 player_1 到 player_5）。"""
    return make_roles("player_1", "player_2", "player_3", "player_4", "player_5")


# ============================================================================
# 幽灵玩家检测测试
# ============================================================================


class TestGhostPlayerDetection:
    """幽灵玩家检测测试。"""

    def test_existing_player_passes(self, detector: AntiCheatDetector, roles: dict) -> None:
        """存在于 roles 中的 player 通过检测。"""
        action = make_action(actor_id="player_1")
        result = detector._check_ghost_player(action, roles)
        assert result.is_clean is True

    def test_non_existent_player_detected(self, detector: AntiCheatDetector, roles: dict) -> None:
        """不存在于 roles 中的 player 被检测为幽灵玩家。"""
        action = make_action(actor_id="player_99")
        result = detector._check_ghost_player(action, roles)
        assert result.is_clean is False
        assert "幽灵玩家" in result.violation_type
        assert result.penalty == PenaltyType.MARK_SUSPICIOUS


# ============================================================================
# 跨阵营越权检测测试
# ============================================================================


class TestFactionViolation:
    """跨阵营越权检测测试。"""

    def test_non_wolf_kill_passes(
        self, detector: AntiCheatDetector, roles: dict
    ) -> None:
        """非 WOLF_KILL 动作直接通过。"""
        action = make_action(action_type=ActionType.SPEAK)
        result = detector._check_faction_violation(action, roles)
        assert result.is_clean is True

    def test_werewolf_kill_at_night_passes(self, detector: AntiCheatDetector) -> None:
        """狼人在夜间提交 WOLF_KILL 通过。"""
        roles = {
            "player_1": create_role(Role.WEREWOLF, "player_1"),
            "player_2": create_role(Role.VILLAGER, "player_2"),
        }
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.WOLF_KILL,
            phase=GamePhase.NIGHT_WOLF_ACT,
            target_id="player_2",
        )
        result = detector._check_faction_violation(action, roles)
        assert result.is_clean is True

    def test_non_werewolf_kill_rejected(self, detector: AntiCheatDetector) -> None:
        """非狼人阵营提交 WOLF_KILL 被拒绝。"""
        roles = {
            "player_1": create_role(Role.VILLAGER, "player_1"),
        }
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.WOLF_KILL,
            phase=GamePhase.NIGHT_WOLF_ACT,
        )
        result = detector._check_faction_violation(action, roles)
        assert result.is_clean is False
        assert "非狼人" in result.violation_type
        assert result.penalty == PenaltyType.MARK_SUSPICIOUS

    def test_werewolf_kill_at_day_rejected(self, detector: AntiCheatDetector) -> None:
        """狼人在非夜间阶段提交 WOLF_KILL 被拒绝。"""
        roles = {
            "player_1": create_role(Role.WEREWOLF, "player_1"),
        }
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.WOLF_KILL,
            phase=GamePhase.DAY_DISCUSSION,
        )
        result = detector._check_faction_violation(action, roles)
        assert result.is_clean is False
        assert "非夜间阶段" in result.violation_type
        assert result.penalty == PenaltyType.FORCE_PASS


# ============================================================================
# 重放攻击检测测试
# ============================================================================


class TestReplayAttackDetection:
    """重放攻击检测测试。"""

    def test_first_action_passes(self, detector: AntiCheatDetector) -> None:
        """首次提交通过重放检测。"""
        action = make_action()
        result = detector._check_replay_attack(action)
        assert result.is_clean is True

    def test_repeat_action_detected(self, detector: AntiCheatDetector) -> None:
        """重复提交相同动作被检测为重放攻击。"""
        action = make_action()

        # 第一次通过
        result1 = detector._check_replay_attack(action)
        assert result1.is_clean is True

        # 第二次被检测
        result2 = detector._check_replay_attack(action)
        assert result2.is_clean is False
        assert "重放" in result2.violation_type
        assert result2.penalty == PenaltyType.FORCE_PASS

    def test_different_action_passes(self, detector: AntiCheatDetector) -> None:
        """不同动作不被视为重放。"""
        action1 = make_action(action_type=ActionType.SPEAK)
        action2 = make_action(action_type=ActionType.VOTE)

        result1 = detector._check_replay_attack(action1)
        assert result1.is_clean is True

        result2 = detector._check_replay_attack(action2)
        assert result2.is_clean is True

    def test_different_round_passes(self, detector: AntiCheatDetector) -> None:
        """不同轮次的相同动作不被视为重放。"""
        action1 = make_action(round_num=1)
        action2 = make_action(round_num=2)

        result1 = detector._check_replay_attack(action1)
        assert result1.is_clean is True

        result2 = detector._check_replay_attack(action2)
        assert result2.is_clean is True


# ============================================================================
# 超频提交检测测试
# ============================================================================


class TestRateLimitDetection:
    """超频提交检测测试。"""

    def test_normal_count_passes(self, detector: AntiCheatDetector) -> None:
        """未超过阈值时通过。"""
        action = make_action()
        for _ in range(MAX_ACTIONS_PER_PHASE):
            result = detector._check_rate_limit(action)
            assert result.is_clean is True

    def test_exceed_threshold_detected(self, detector: AntiCheatDetector) -> None:
        """超过阈值后检测到超频。"""
        action = make_action()
        # 提交 MAX_ACTIONS_PER_PHASE 次（正常）
        for _ in range(MAX_ACTIONS_PER_PHASE):
            result = detector._check_rate_limit(action)
            assert result.is_clean is True

        # 第 MAX_ACTIONS_PER_PHASE + 1 次应触发超频
        result = detector._check_rate_limit(action)
        assert result.is_clean is False
        assert "超频" in result.violation_type
        assert "超过阈值" in result.violation_type
        assert result.penalty == PenaltyType.RATE_LIMIT

    def test_rate_limited_player_rejected(self, detector: AntiCheatDetector) -> None:
        """已被限速的玩家提交动作被拒绝。"""
        action = make_action()
        # 触发限速
        for _ in range(MAX_ACTIONS_PER_PHASE + 1):
            detector._check_rate_limit(action)

        # 限速后的提交应该被拒绝
        result = detector._check_rate_limit(action)
        assert result.is_clean is False
        assert "已被限速" in result.violation_type

    def test_reset_phase_counters(self, detector: AntiCheatDetector) -> None:
        """阶段计数器重置后可以正常提交。"""
        action = make_action()
        for _ in range(MAX_ACTIONS_PER_PHASE):
            detector._check_rate_limit(action)

        detector.reset_phase_counters()
        result = detector._check_rate_limit(action)
        assert result.is_clean is True


# ============================================================================
# 限速状态管理测试
# ============================================================================


class TestRateLimitState:
    """限速状态管理测试。"""

    def test_not_rate_limited_initially(self, detector: AntiCheatDetector) -> None:
        """初始状态未被限速。"""
        assert detector.is_rate_limited("player_1") is False

    def test_apply_rate_limit(self, detector: AntiCheatDetector) -> None:
        """应用限速后 is_rate_limited 返回 True。"""
        detector.apply_penalty("player_1", PenaltyType.RATE_LIMIT)
        assert detector.is_rate_limited("player_1") is True

    def test_different_players_independent(self, detector: AntiCheatDetector) -> None:
        """不同玩家的限速状态互不影响。"""
        detector.apply_penalty("player_1", PenaltyType.RATE_LIMIT)
        assert detector.is_rate_limited("player_1") is True
        assert detector.is_rate_limited("player_2") is False

    def test_force_pass_penalty(self, detector: AntiCheatDetector) -> None:
        """FORCE_PASS 惩罚不触发限速。"""
        detector.apply_penalty("player_1", PenaltyType.FORCE_PASS)
        assert detector.is_rate_limited("player_1") is False

    def test_mark_suspicious_penalty(self, detector: AntiCheatDetector) -> None:
        """MARK_SUSPICIOUS 惩罚不触发限速。"""
        detector.apply_penalty("player_1", PenaltyType.MARK_SUSPICIOUS)
        assert detector.is_rate_limited("player_1") is False


# ============================================================================
# 违规记录测试
# ============================================================================


class TestViolationRecords:
    """违规记录测试。"""

    def test_violations_initially_empty(self, detector: AntiCheatDetector) -> None:
        """初始违规记录为空。"""
        assert detector.get_violations() == []

    def test_inspect_records_violation(
        self, detector: AntiCheatDetector, roles: dict
    ) -> None:
        """inspect 检测到违规后自动记录。"""
        action = make_action(actor_id="player_99")  # 幽灵玩家
        detector.inspect(action, roles)
        violations = detector.get_violations()
        assert len(violations) == 1
        assert violations[0].actor_id == "player_99"
        assert violations[0].penalty == PenaltyType.MARK_SUSPICIOUS

    def test_multiple_violations(
        self, detector: AntiCheatDetector, roles: dict
    ) -> None:
        """多次违规全部记录。"""
        for i in range(3):
            action = make_action(actor_id=f"player_{90 + i}")
            detector.inspect(action, roles)
        assert len(detector.get_violations()) == 3

    def test_reset_clears_violations(self, detector: AntiCheatDetector, roles: dict) -> None:
        """reset 清空违规记录。"""
        action = make_action(actor_id="player_99")
        detector.inspect(action, roles)
        detector.reset()
        assert detector.get_violations() == []


# ============================================================================
# 集成检测测试
# ============================================================================


class TestInspectPipeline:
    """inspect 完整管道测试。"""

    def test_clean_action_passes_all(
        self, detector: AntiCheatDetector, roles: dict
    ) -> None:
        """正常的动作通过所有检测。"""
        action = make_action(actor_id="player_1")
        result = detector.inspect(action, roles)
        assert result.is_clean is True

    def test_ghost_player_caught_first(
        self, detector: AntiCheatDetector, roles: dict
    ) -> None:
        """幽灵玩家在第一步就被拦截。"""
        action = make_action(actor_id="player_99")
        result = detector.inspect(action, roles)
        assert result.is_clean is False
        assert "幽灵玩家" in result.violation_type


# ============================================================================
# InspectionResult / ViolationRecord 数据类测试
# ============================================================================


class TestInspectionResult:
    """InspectionResult 数据类测试。"""

    def test_passed_factory(self) -> None:
        """passed() 工厂方法。"""
        result = InspectionResult.passed()
        assert result.is_clean is True
        assert result.violation_type == ""

    def test_failed_factory(self) -> None:
        """failed() 工厂方法。"""
        result = InspectionResult.failed("测试违规", PenaltyType.FORCE_PASS)
        assert result.is_clean is False
        assert result.violation_type == "测试违规"
        assert result.penalty == PenaltyType.FORCE_PASS


class TestViolationRecord:
    """ViolationRecord 数据类测试。"""

    def test_create_record(self) -> None:
        """创建违规记录。"""
        action = make_action()
        record = ViolationRecord(
            actor_id="player_1",
            violation_type="测试",
            penalty=PenaltyType.MARK_SUSPICIOUS,
            action=action,
        )
        assert record.actor_id == "player_1"
        assert record.penalty == PenaltyType.MARK_SUSPICIOUS
        assert record.action is action
        assert record.timestamp is not None

    def test_record_without_action(self) -> None:
        """action 可选。"""
        record = ViolationRecord(
            actor_id="player_1",
            violation_type="测试",
            penalty=PenaltyType.FORCE_PASS,
        )
        assert record.action is None
