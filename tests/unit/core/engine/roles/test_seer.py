"""SeerRole 单元测试。

覆盖:
- 存活预言家在夜间可以验人
- 死亡预言家不能验人
- 预言家在白天不能验人
- 预言家不能执行其他角色的专属动作
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


@pytest.fixture
def seer():
    """创建一个存活预言家。"""
    return create_role(Role.SEER, "player_1")


class TestSeerRoleActions:
    """预言家专属动作校验。"""

    def test_seer_can_check_at_night(self, seer) -> None:
        """存活预言家在 NIGHT_ACTION 阶段可以验人。"""
        assert seer.can_act(GamePhase.NIGHT_ACTION, ActionType.SEER_CHECK) is True

    def test_seer_cannot_check_during_day(self, seer) -> None:
        """预言家在白天不能验人。"""
        assert seer.can_act(GamePhase.DAY_DISCUSSION, ActionType.SEER_CHECK) is False
        assert seer.can_act(GamePhase.DAY_VOTE, ActionType.SEER_CHECK) is False

    def test_seer_cannot_check_in_other_night_phases(self, seer) -> None:
        """预言家仅在 NIGHT_ACTION 阶段可以验人。"""
        assert seer.can_act(GamePhase.NIGHT_START, ActionType.SEER_CHECK) is False
        assert seer.can_act(GamePhase.NIGHT_RESOLVE, ActionType.SEER_CHECK) is False

    def test_seer_cannot_use_other_skills(self, seer) -> None:
        """预言家不能执行其他角色的专属动作。"""
        assert seer.can_act(GamePhase.NIGHT_ACTION, ActionType.WOLF_KILL) is False
        assert seer.can_act(GamePhase.NIGHT_ACTION, ActionType.WITCH_SAVE) is False
        assert seer.can_act(GamePhase.NIGHT_ACTION, ActionType.WITCH_POISON) is False

    def test_dead_seer_cannot_check(self, seer) -> None:
        """死亡预言家不能验人。"""
        seer.die()
        assert seer.can_act(GamePhase.NIGHT_ACTION, ActionType.SEER_CHECK) is False

    def test_seer_validate_action_includes_common(self, seer) -> None:
        """预言家的 validate_action 也支持通用动作。"""
        assert seer.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True
        assert seer.validate_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_seer_validate_at_night(self, seer) -> None:
        """预言家 validate_action 在夜间返回 True。"""
        assert seer.validate_action(GamePhase.NIGHT_ACTION, ActionType.SEER_CHECK) is True


class TestSeerRoleAttributes:
    """预言家属性测试。"""

    def test_role_type(self, seer) -> None:
        assert seer.role_type == Role.SEER

    def test_faction(self, seer) -> None:
        """预言家属于好人阵营。"""
        assert seer.faction.value == "VILLAGER"
