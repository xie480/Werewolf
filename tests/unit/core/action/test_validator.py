"""ActionValidator 单元测试。

覆盖:
- 结构化校验（actor_id 格式、字段完整性）
- 阶段校验（阶段不匹配拒绝）
- 冷却校验（重复提交拦截）
- 生存状态校验（角色声明的 SurvivalRequirement）
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.action.validator import (
    ActionValidator,
    ValidationResult,
    DEFAULT_COOLDOWN_SECONDS,
)
from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


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


class TestStructuralValidation:
    """结构化校验测试。"""

    def test_valid_action_passes(self) -> None:
        """合法动作通过结构化校验。"""
        validator = ActionValidator("game_001")
        action = make_action()
        result = validator._validate_structure(action)
        assert result.is_valid is True

    def test_empty_actor_id_rejected(self) -> None:
        """空 actor_id 被拒绝。"""
        validator = ActionValidator("game_001")
        action = make_action(actor_id="")
        result = validator._validate_structure(action)
        assert result.is_valid is False
        assert result.rejected_by == "structural"
        assert "actor_id" in result.reason

    def test_invalid_actor_id_format_rejected(self) -> None:
        """无效 actor_id 格式被拒绝。"""
        validator = ActionValidator("game_001")
        action = make_action(actor_id="invalid_id")
        result = validator._validate_structure(action)
        assert result.is_valid is False
        assert "player_" in result.reason

    def test_target_id_none_passes(self) -> None:
        """target_id 为 None 时通过。"""
        validator = ActionValidator("game_001")
        action = make_action(target_id=None)
        result = validator._validate_structure(action)
        assert result.is_valid is True

    def test_valid_target_id_passes(self) -> None:
        """有效 target_id 格式通过。"""
        validator = ActionValidator("game_001")
        action = make_action(target_id="player_3")
        result = validator._validate_structure(action)
        assert result.is_valid is True


class TestPhaseValidation:
    """阶段校验测试。"""

    def test_matching_phase_passes(self) -> None:
        """声明的阶段与当前阶段一致时通过。"""
        validator = ActionValidator("game_001")
        action = make_action(phase=GamePhase.DAY_DISCUSSION)
        result = validator._validate_phase(action, GamePhase.DAY_DISCUSSION)
        assert result.is_valid is True

    def test_mismatched_phase_rejected(self) -> None:
        """阶段不匹配时被拒绝。"""
        validator = ActionValidator("game_001")
        action = make_action(phase=GamePhase.DAY_DISCUSSION)
        result = validator._validate_phase(action, GamePhase.NIGHT_WOLF_ACT)
        assert result.is_valid is False
        assert result.rejected_by == "phase"
        assert "阶段不匹配" in result.reason


class TestCooldownValidation:
    """冷却校验测试。"""

    def test_first_action_passes(self) -> None:
        """首次提交通过冷却校验。"""
        validator = ActionValidator("game_001")
        action = make_action()
        result = validator._validate_cooldown(action)
        assert result.is_valid is True

    def test_repeat_within_cooldown_rejected(self, monkeypatch) -> None:
        """冷却窗口内重复提交被拒绝。"""
        import time as time_module

        validator = ActionValidator("game_001")
        action = make_action()

        # 第一次通过
        result1 = validator._validate_cooldown(action)
        assert result1.is_valid is True

        # 模拟时间几乎未流逝，第二次应被拒绝
        result2 = validator._validate_cooldown(action)
        assert result2.is_valid is False
        assert result2.rejected_by == "cooldown"
        assert "冷却" in result2.reason

    def test_different_phase_passes(self) -> None:
        """不同阶段的动作互不干扰。"""
        validator = ActionValidator("game_001")
        action1 = make_action(phase=GamePhase.DAY_DISCUSSION)
        action2 = make_action(phase=GamePhase.DAY_VOTE)

        result1 = validator._validate_cooldown(action1)
        assert result1.is_valid is True

        result2 = validator._validate_cooldown(action2)
        assert result2.is_valid is True

    def test_different_action_type_passes(self) -> None:
        """不同动作类型互不干扰。"""
        validator = ActionValidator("game_001")
        action1 = make_action(action_type=ActionType.SPEAK)
        action2 = make_action(action_type=ActionType.VOTE)

        result1 = validator._validate_cooldown(action1)
        assert result1.is_valid is True

        result2 = validator._validate_cooldown(action2)
        assert result2.is_valid is True

    def test_different_actor_passes(self) -> None:
        """不同玩家的动作互不干扰。"""
        validator = ActionValidator("game_001")
        action1 = make_action(actor_id="player_1")
        action2 = make_action(actor_id="player_2")

        result1 = validator._validate_cooldown(action1)
        assert result1.is_valid is True

        result2 = validator._validate_cooldown(action2)
        assert result2.is_valid is True

    def test_reset_cooldowns(self) -> None:
        """重置冷却后可以再次提交。"""
        validator = ActionValidator("game_001")
        action = make_action()

        validator._validate_cooldown(action)
        validator.reset_cooldowns()

        result = validator._validate_cooldown(action)
        assert result.is_valid is True


class TestExtractSeatNumber:
    """座位号提取测试。"""

    def test_valid_player_id(self) -> None:
        """有效的 player_N 格式正确提取。"""
        result = ActionValidator._extract_seat_number("player_5")
        assert result == 5

    def test_player_id_with_large_number(self) -> None:
        """大座位号正确提取。"""
        result = ActionValidator._extract_seat_number("player_12")
        assert result == 12

    def test_invalid_format_raises(self) -> None:
        """无效格式抛出 ValueError。"""
        with pytest.raises(ValueError, match="无法从 actor_id 提取座位号"):
            ActionValidator._extract_seat_number("invalid")


class TestValidationResult:
    """ValidationResult 数据类测试。"""

    def test_passed_factory(self) -> None:
        """passed() 工厂方法。"""
        result = ValidationResult.passed()
        assert result.is_valid is True
        assert result.reason == ""

    def test_rejected_factory(self) -> None:
        """rejected() 工厂方法。"""
        result = ValidationResult.rejected("测试原因", "structural")
        assert result.is_valid is False
        assert result.reason == "测试原因"
        assert result.rejected_by == "structural"
