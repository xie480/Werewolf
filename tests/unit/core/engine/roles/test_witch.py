"""WitchRole 单元测试。

覆盖:
- 存活女巫在夜间可以使用解药和毒药
- 死亡女巫不能使用药品
- 解药/毒药使用后不可重复使用
- 女巫在白天不能使用药品
- use_antidote/use_poison 重复消费抛 ValueError
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, Role


@pytest.fixture
def witch():
    """创建一个存活女巫（解药和毒药均未使用）。"""
    return create_role(Role.WITCH, "player_1")


class TestWitchRoleActions:
    """女巫专属动作校验。"""

    def test_witch_can_save_at_night(self, witch) -> None:
        """女巫在夜间可以使用解药。"""
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is True

    def test_witch_can_poison_at_night(self, witch) -> None:
        """女巫在夜间可以使用毒药。"""
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is True

    def test_witch_cannot_act_during_day(self, witch) -> None:
        """女巫在白天不能使用任何药品。"""
        assert witch.can_act(GamePhase.DAY_DISCUSSION, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.DAY_VOTE, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.DAY_DISCUSSION, ActionType.WITCH_POISON) is False
        assert witch.can_act(GamePhase.DAY_VOTE, ActionType.WITCH_POISON) is False

    def test_witch_cannot_act_in_non_action_night_phases(self, witch) -> None:
        """女巫仅在 NIGHT_WITCH_ACT 阶段可用药。"""
        assert witch.can_act(GamePhase.NIGHT_START, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.NIGHT_SEER_ACT, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.NIGHT_RESOLVE, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.NIGHT_START, ActionType.WITCH_POISON) is False
        assert witch.can_act(GamePhase.NIGHT_WOLF_ACT, ActionType.WITCH_POISON) is False
        assert witch.can_act(GamePhase.NIGHT_SEER_ACT, ActionType.WITCH_POISON) is False
        assert witch.can_act(GamePhase.NIGHT_RESOLVE, ActionType.WITCH_POISON) is False

    def test_witch_cannot_use_other_skills(self, witch) -> None:
        """女巫不能执行其他角色的专属动作。"""
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WOLF_KILL) is False
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.SEER_CHECK) is False


class TestWitchItemConsumption:
    """女巫物品消费测试。"""

    def test_use_antidote_consumes_it(self, witch) -> None:
        """使用解药后 has_antidote 变为 False。"""
        witch.use_antidote()
        assert witch.has_antidote is False

    def test_use_poison_consumes_it(self, witch) -> None:
        """使用毒药后 has_poison 变为 False。"""
        witch.use_poison()
        assert witch.has_poison is False

    def test_cannot_use_antidote_twice(self, witch) -> None:
        """解药使用后不能再次使用。"""
        witch.use_antidote()
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is False

    def test_cannot_use_poison_twice(self, witch) -> None:
        """毒药使用后不能再次使用。"""
        witch.use_poison()
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is False

    def test_use_antidote_raises_when_already_used(self, witch) -> None:
        """重复使用解药抛出 ValueError。"""
        witch.use_antidote()
        with pytest.raises(ValueError, match="解药已使用"):
            witch.use_antidote()

    def test_use_poison_raises_when_already_used(self, witch) -> None:
        """重复使用毒药抛出 ValueError。"""
        witch.use_poison()
        with pytest.raises(ValueError, match="毒药已使用"):
            witch.use_poison()

    def test_using_antidote_does_not_affect_poison(self, witch) -> None:
        """使用解药不影响毒药的可用性。"""
        witch.use_antidote()
        assert witch.has_poison is True
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is True

    def test_using_poison_does_not_affect_antidote(self, witch) -> None:
        """使用毒药不影响解药的可用性。"""
        witch.use_poison()
        assert witch.has_antidote is True
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is True


class TestWitchDeath:
    """女巫死亡状态测试。"""

    def test_dead_witch_cannot_save(self, witch) -> None:
        """死亡女巫不能使用解药。"""
        witch.die()
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is False

    def test_dead_witch_cannot_poison(self, witch) -> None:
        """死亡女巫不能使用毒药。"""
        witch.die()
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is False

    def test_revived_witch_can_use_remaining_items(self, witch) -> None:
        """复活后女巫仍可使用未消费的药品。"""
        witch.use_antidote()  # 仅使用解药
        witch.die()
        witch.revive()
        # 解药已用，毒药仍在
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_SAVE) is False
        assert witch.can_act(GamePhase.NIGHT_WITCH_ACT, ActionType.WITCH_POISON) is True


class TestWitchCommonActions:
    """女巫通用动作测试。"""

    def test_witch_can_speak_day(self, witch) -> None:
        """女巫在白天可以发言。"""
        assert witch.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_witch_can_vote_day(self, witch) -> None:
        """女巫在白天可以投票。"""
        assert witch.validate_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_witch_cannot_wolf_kill(self, witch) -> None:
        """女巫不能执行狼人刀人动作。"""
        assert witch.validate_action(GamePhase.NIGHT_WITCH_ACT, ActionType.WOLF_KILL) is False


class TestWitchRoleAttributes:
    """女巫属性测试。"""

    def test_role_type(self, witch) -> None:
        assert witch.role_type == Role.WITCH

    def test_faction(self, witch) -> None:
        """女巫属于好人阵营。"""
        assert witch.faction.value == "VILLAGER"

    def test_initial_items(self, witch) -> None:
        """初始状态解药和毒药均可用。"""
        assert witch.has_antidote is True
        assert witch.has_poison is True
