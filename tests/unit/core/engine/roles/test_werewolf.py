"""WerewolfRole 单元测试。

覆盖:
- 存活狼人在夜间可以刀人
- 死亡狼人不能刀人
- 狼人在白天不能刀人
- 狼人不能执行其他角色的专属动作
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


@pytest.fixture
def werewolf():
    """创建一个存活狼人。"""
    return create_role(Role.WEREWOLF, "player_1")


class TestWerewolfRoleActions:
    """狼人专属动作校验。"""

    def test_werewolf_can_kill_at_night(self, werewolf) -> None:
        """存活狼人在 NIGHT_WOLF_ACT 阶段可以刀人。"""
        assert werewolf.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WOLF_KILL) is True

    def test_werewolf_cannot_kill_during_day(self, werewolf) -> None:
        """狼人在白天不能刀人。"""
        assert werewolf.can_act(GamePhase.DAY_DISCUSSION, ActionType.WOLF_KILL) is False
        assert werewolf.can_act(GamePhase.DAY_VOTE, ActionType.WOLF_KILL) is False
        assert werewolf.can_act(GamePhase.DAY_START, ActionType.WOLF_KILL) is False

    def test_werewolf_cannot_kill_in_other_night_phases(self, werewolf) -> None:
        """狼人仅在 NIGHT_WOLF_ACT 阶段可以刀人，其他夜间子阶段不行。"""
        assert werewolf.can_act(GamePhase.NIGHT_START, ActionType.WOLF_KILL) is False
        assert werewolf.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WOLF_KILL) is False
        assert werewolf.can_act(GamePhase.NIGHT_SEER_ACT, ActionType.WOLF_KILL) is False
        assert werewolf.can_act(GamePhase.NIGHT_RESOLVE, ActionType.WOLF_KILL) is False

    def test_werewolf_cannot_use_other_skills(self, werewolf) -> None:
        """狼人不能执行其他角色的专属动作。"""
        assert werewolf.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.SEER_CHECK) is False
        assert werewolf.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WITCH_SAVE) is False
        assert werewolf.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WITCH_POISON) is False

    def test_dead_werewolf_cannot_kill(self, werewolf) -> None:
        """死亡狼人不能刀人。"""
        werewolf.die()
        assert werewolf.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WOLF_KILL) is False

    def test_werewolf_validate_action_includes_common(self, werewolf) -> None:
        """狼人的 validate_action 也支持通用动作。"""
        assert werewolf.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True
        assert werewolf.validate_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_werewolf_validate_at_night(self, werewolf) -> None:
        """狼人 validate_action 在夜间返回 True。"""
        assert werewolf.validate_action(GamePhase.NIGHT_WOLF_ACT, ActionType.WOLF_KILL) is True


class TestWerewolfRoleAttributes:
    """狼人属性测试。"""

    def test_role_type(self, werewolf) -> None:
        assert werewolf.role_type == Role.WEREWOLF

    def test_faction(self, werewolf) -> None:
        assert werewolf.faction.value == "WEREWOLF"

    def test_can_speak_during_day(self, werewolf) -> None:
        """狼人在白天可以发言（伪装成村民）。"""
        assert werewolf.can_perform_common_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_can_vote_during_day(self, werewolf) -> None:
        """狼人在白天可以投票。"""
        assert werewolf.can_perform_common_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True
