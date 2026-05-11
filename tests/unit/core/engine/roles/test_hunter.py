"""HunterRole 单元测试。

覆盖:
- 猎人在 HUNTER_SHOOT 阶段可以开枪
- 猎人在非 HUNTER_SHOOT 阶段不能开枪
- 猎人复用 WOLF_KILL 动作类型
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


@pytest.fixture
def hunter():
    """创建一个存活的猎人。"""
    return create_role(Role.HUNTER, "player_1")


class TestHunterRoleActions:
    """猎人专属动作校验。"""

    def test_hunter_can_shoot(self, hunter) -> None:
        """猎人在 HUNTER_SHOOT 阶段可以开枪（复用 WOLF_KILL）。"""
        assert hunter.can_act(GamePhase.HUNTER_SHOOT, ActionType.WOLF_KILL) is True

    def test_hunter_cannot_shoot_in_night(self, hunter) -> None:
        """猎人在夜间不能开枪。"""
        assert hunter.can_act(GamePhase.NIGHT_ACTION, ActionType.WOLF_KILL) is False

    def test_hunter_cannot_shoot_in_day_discussion(self, hunter) -> None:
        """猎人在白天讨论阶段不能开枪。"""
        assert hunter.can_act(GamePhase.DAY_DISCUSSION, ActionType.WOLF_KILL) is False

    def test_hunter_cannot_shoot_in_day_vote(self, hunter) -> None:
        """猎人在白天投票阶段不能开枪。"""
        assert hunter.can_act(GamePhase.DAY_VOTE, ActionType.WOLF_KILL) is False

    def test_hunter_cannot_use_other_action_types(self, hunter) -> None:
        """猎人在 HUNTER_SHOOT 阶段仅能执行 WOLF_KILL。"""
        assert hunter.can_act(GamePhase.HUNTER_SHOOT, ActionType.SEER_CHECK) is False
        assert hunter.can_act(GamePhase.HUNTER_SHOOT, ActionType.WITCH_SAVE) is False


class TestHunterCommonActions:
    """猎人通用动作测试。"""

    def test_hunter_can_speak_day(self, hunter) -> None:
        """猎人在白天可以发言。"""
        assert hunter.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_hunter_can_vote_day(self, hunter) -> None:
        """猎人在白天可以投票。"""
        assert hunter.validate_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_hunter_validate_action_shoot(self, hunter) -> None:
        """猎人的 validate_action 在 HUNTER_SHOOT 阶段返回 True。"""
        assert hunter.validate_action(GamePhase.HUNTER_SHOOT, ActionType.WOLF_KILL) is True


class TestHunterRoleAttributes:
    """猎人属性测试。"""

    def test_role_type(self, hunter) -> None:
        assert hunter.role_type == Role.HUNTER

    def test_faction(self, hunter) -> None:
        """猎人属于好人阵营。"""
        assert hunter.faction.value == "VILLAGER"

    def test_is_alive_default(self, hunter) -> None:
        assert hunter.is_alive is True
