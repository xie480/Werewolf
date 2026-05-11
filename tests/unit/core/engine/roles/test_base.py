"""BaseRole 通用动作校验与生命周期管理测试。

覆盖:
- 通用动作校验（发言、投票、空过）
- 死亡状态限制
- 统一校验入口 validate_action
- 复活逻辑
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.engine.roles.base import BaseRole
from ai_werewolf_core.core.engine.roles.villager import VillagerRole
from ai_werewolf_core.schemas.enums import ActionType, GamePhase


@pytest.fixture
def villager() -> VillagerRole:
    """创建一个存活村民用于测试基类通用行为。"""
    return VillagerRole(player_id="player_1")


# ------------------------------------------------------------------
# 存活状态下通用动作
# ------------------------------------------------------------------


class TestCommonActionsWhenAlive:
    """存活玩家通用动作校验。"""

    def test_speak_during_discussion(self, villager: BaseRole) -> None:
        """存活玩家在 DAY_DISCUSSION 阶段可以发言。"""
        assert villager.can_perform_common_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_speak_during_pk_discussion(self, villager: BaseRole) -> None:
        """存活玩家在 DAY_PK_DISCUSSION 阶段可以发言。"""
        assert villager.can_perform_common_action(GamePhase.DAY_PK_DISCUSSION, ActionType.SPEAK) is True

    def test_speak_during_last_words(self, villager: BaseRole) -> None:
        """存活玩家在 LAST_WORDS 阶段可以发言。"""
        assert villager.can_perform_common_action(GamePhase.LAST_WORDS, ActionType.SPEAK) is True

    def test_speak_during_night_is_denied(self, villager: BaseRole) -> None:
        """存活玩家在夜间不能发言。"""
        assert villager.can_perform_common_action(GamePhase.NIGHT_ACTION, ActionType.SPEAK) is False

    def test_vote_during_day_vote(self, villager: BaseRole) -> None:
        """存活玩家在 DAY_VOTE 阶段可以投票。"""
        assert villager.can_perform_common_action(GamePhase.DAY_VOTE, ActionType.VOTE) is True

    def test_vote_during_pk_vote(self, villager: BaseRole) -> None:
        """存活玩家在 DAY_PK_VOTE 阶段可以投票。"""
        assert villager.can_perform_common_action(GamePhase.DAY_PK_VOTE, ActionType.VOTE) is True

    def test_vote_during_discussion_is_denied(self, villager: BaseRole) -> None:
        """存活玩家在讨论阶段不能投票。"""
        assert villager.can_perform_common_action(GamePhase.DAY_DISCUSSION, ActionType.VOTE) is False

    def test_pass_in_any_phase(self, villager: BaseRole) -> None:
        """存活玩家在所有阶段都可以 PASS。"""
        phases = [GamePhase.NIGHT_ACTION, GamePhase.DAY_DISCUSSION, GamePhase.DAY_VOTE, GamePhase.INIT]
        for phase in phases:
            assert villager.can_perform_common_action(phase, ActionType.PASS) is True


# ------------------------------------------------------------------
# 死亡状态下通用动作
# ------------------------------------------------------------------


class TestCommonActionsWhenDead:
    """死亡玩家通用动作校验。"""

    def test_dead_cannot_speak(self, villager: BaseRole) -> None:
        """死亡玩家不能发言。"""
        villager.die()
        assert villager.can_perform_common_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is False

    def test_dead_cannot_vote(self, villager: BaseRole) -> None:
        """死亡玩家不能投票。"""
        villager.die()
        assert villager.can_perform_common_action(GamePhase.DAY_VOTE, ActionType.VOTE) is False

    def test_dead_cannot_pass(self, villager: BaseRole) -> None:
        """死亡玩家不能 PASS（死亡后不能执行任何动作）。"""
        villager.die()
        assert villager.can_perform_common_action(GamePhase.NIGHT_ACTION, ActionType.PASS) is False


# ------------------------------------------------------------------
# validate_action 统一入口
# ------------------------------------------------------------------


class TestValidateAction:
    """validate_action 统一入口校验。"""

    def test_validate_common_action(self, villager: BaseRole) -> None:
        """validate_action 能正确识别通用动作。"""
        assert villager.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True

    def test_validate_role_specific_action(self, villager: BaseRole) -> None:
        """validate_action 对村民尝试技能动作返回 False。"""
        assert villager.validate_action(GamePhase.NIGHT_ACTION, ActionType.WOLF_KILL) is False

    def test_validate_dead_player(self, villager: BaseRole) -> None:
        """validate_action 对死亡玩家返回 False。"""
        villager.die()
        assert villager.validate_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is False


# ------------------------------------------------------------------
# 生命周期管理
# ------------------------------------------------------------------


class TestLifecycle:
    """角色生命周期管理测试。"""

    def test_initial_state_alive(self, villager: BaseRole) -> None:
        """新创建的角色默认为存活状态。"""
        assert villager.is_alive is True

    def test_die_sets_alive_false(self, villager: BaseRole) -> None:
        """die() 将角色标记为死亡。"""
        villager.die()
        assert villager.is_alive is False

    def test_revive_restores_alive(self, villager: BaseRole) -> None:
        """revive() 将角色复活。"""
        villager.die()
        villager.revive()
        assert villager.is_alive is True

    def test_revive_restores_actions(self, villager: BaseRole) -> None:
        """复活后通用动作恢复正常。"""
        villager.die()
        villager.revive()
        assert villager.can_perform_common_action(GamePhase.DAY_DISCUSSION, ActionType.SPEAK) is True


# ------------------------------------------------------------------
# 抽象方法
# ------------------------------------------------------------------


def test_base_role_is_abstract() -> None:
    """BaseRole 不能直接实例化。"""
    with pytest.raises(TypeError):
        BaseRole(player_id="player_1")  # type: ignore[abstract]


def test_concrete_role_can_be_instantiated() -> None:
    """具体子类可以正常实例化。"""
    role = VillagerRole(player_id="player_1")
    assert role.player_id == "player_1"
    assert role.role_type.value == "VILLAGER"
