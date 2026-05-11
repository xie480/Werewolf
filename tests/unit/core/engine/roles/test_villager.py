"""VillagerRole 单元测试。

覆盖:
- 村民无专属动作
- 村民可通过通用动作发言和投票
- 死亡村民无任何动作权限
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


@pytest.fixture
def villager():
    """创建一个存活村民。"""
    return create_role(Role.VILLAGER, "player_1")


class TestVillagerRoleActions:
    """村民专属动作校验。"""

    def test_villager_has_no_special_actions(self, villager) -> None:
        """村民 can_act 始终返回 False（无专属动作）。"""
        assert villager.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WOLF_KILL) is False
        assert villager.can_act(GamePhase.NIGHT_SEER_ACT, ActionType.SEER_CHECK) is False
        assert villager.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is False
        assert villager.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is False
        assert villager.can_act(GamePhase.HUNTER_SHOOT, ActionType.WOLF_KILL) is False

    def test_villager_can_speak_day(self, villager) -> None:
        """村民在白天讨论阶段可以发言。"""
        assert villager.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_villager_can_vote_day(self, villager) -> None:
        """村民在白天投票阶段可以投票。"""
        assert villager.validate_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_villager_cannot_act_at_night(self, villager) -> None:
        """村民在夜间不能执行技能动作。"""
        assert villager.validate_action(GamePhase.NIGHT_WOLF_ACT, ActionType.WOLF_KILL) is False

    def test_dead_villager_cannot_speak(self, villager) -> None:
        """死亡村民不能发言。"""
        villager.die()
        assert villager.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is False


class TestVillagerRoleAttributes:
    """村民属性测试。"""

    def test_role_type(self, villager) -> None:
        assert villager.role_type == Role.VILLAGER

    def test_faction(self, villager) -> None:
        assert villager.faction.value == "VILLAGER"

    def test_player_id(self, villager) -> None:
        assert villager.player_id == "player_1"

    def test_is_alive_default(self, villager) -> None:
        assert villager.is_alive is True
