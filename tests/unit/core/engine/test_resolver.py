"""
ActionResolver 单元测试 —— 动作提交、结算与完成度检查。

覆盖:
- submit_action 校验与暂存
- is_action_completed 各阶段完成判断
- 夜晚结算逻辑
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_werewolf_core.core.engine.exceptions import ActionValidationError
from ai_werewolf_core.core.engine.resolver import ActionResolver
from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, GameStatus, Role
from ai_werewolf_core.schemas.models import AgentAction


def make_action(
    actor_id: str = "player_1",
    action_type: ActionType = ActionType.WOLF_KILL,
    target_id: str | None = None,
    phase: GamePhase = GamePhase.NIGHT_WOLF_ACT,
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


def make_roles(*role_specs: tuple[str, Role]) -> dict:
    """从 (player_id, Role) 元组列表构造 roles 字典。"""
    roles = {}
    for pid, role_type in role_specs:
        roles[pid] = create_role(role_type, pid)
    return roles


def make_mock_event_bus():
    """创建一个 mock EventBus。"""
    return MagicMock()


# ============================================================================
# 动作完成度检查测试
# ============================================================================


class TestIsActionCompleted:
    """is_action_completed 方法测试。

    注意: NIGHT_WOLF_ACT 阶段的完成度检测已迁移到 WolfVoteManager，
    ActionResolver 不再处理此阶段。相关测试已移除。
    参考: plans/狼人并行投票与原子结算设计.md
    """

    def _make_resolver(self) -> ActionResolver:
        return ActionResolver("game_test", make_mock_event_bus())

    def test_witch_phase_save_submitted(self):
        """女巫提交 WITCH_SAVE 后完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.WITCH),
            ("player_2", Role.VILLAGER),
            ("player_3", Role.WEREWOLF),
        )

        # 模拟狼人刀人目标已设置（WolfVoteManager 结算后同步到 Resolver）
        resolver._current_night_wolf_target = "player_2"
        resolver.pending_deaths["player_2"] = ActionType.WOLF_KILL

        resolver.submit_action(
            make_action("player_1", ActionType.WITCH_SAVE, "player_2",
                       phase=GamePhase.NIGHT_WITCH_ACT),
            roles, GamePhase.NIGHT_WITCH_ACT,
        )
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_WITCH_ACT)

    def test_witch_phase_poison_submitted(self):
        """女巫提交 WITCH_POISON 后完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.WITCH),
            ("player_2", Role.VILLAGER),
        )

        resolver.submit_action(
            make_action("player_1", ActionType.WITCH_POISON, "player_2",
                       phase=GamePhase.NIGHT_WITCH_ACT),
            roles, GamePhase.NIGHT_WITCH_ACT,
        )
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_WITCH_ACT)

    def test_witch_phase_pass_submitted(self):
        """女巫提交 PASS 后完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.WITCH),
            ("player_2", Role.VILLAGER),
        )

        resolver.submit_action(
            make_action("player_1", ActionType.PASS,
                       phase=GamePhase.NIGHT_WITCH_ACT),
            roles, GamePhase.NIGHT_WITCH_ACT,
        )
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_WITCH_ACT)

    def test_witch_not_acted(self):
        """女巫未行动时未完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.WITCH),
            ("player_2", Role.VILLAGER),
        )
        assert not resolver.is_action_completed(roles, GamePhase.NIGHT_WITCH_ACT)

    def test_witch_dead_auto_complete(self):
        """女巫已死亡时自动完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.WITCH),
            ("player_2", Role.VILLAGER),
        )
        roles["player_1"].die()
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_WITCH_ACT)

    def test_seer_phase_check_submitted(self):
        """预言家提交 SEER_CHECK 后完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.SEER),
            ("player_2", Role.VILLAGER),
        )

        resolver.submit_action(
            make_action("player_1", ActionType.SEER_CHECK, "player_2",
                       phase=GamePhase.NIGHT_SEER_ACT),
            roles, GamePhase.NIGHT_SEER_ACT,
        )
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_SEER_ACT)

    def test_seer_not_acted(self):
        """预言家未行动时未完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.SEER),
            ("player_2", Role.VILLAGER),
        )
        assert not resolver.is_action_completed(roles, GamePhase.NIGHT_SEER_ACT)

    def test_seer_dead_auto_complete(self):
        """预言家已死亡时自动完成。"""
        resolver = self._make_resolver()
        roles = make_roles(
            ("player_1", Role.SEER),
            ("player_2", Role.VILLAGER),
        )
        roles["player_1"].die()
        assert resolver.is_action_completed(roles, GamePhase.NIGHT_SEER_ACT)

    def test_unknown_phase_not_completed(self):
        """未知阶段返回 False。"""
        resolver = self._make_resolver()
        roles = make_roles(("player_1", Role.VILLAGER))
        assert not resolver.is_action_completed(roles, GamePhase.DAY_START)
