"""ActionGate 单元测试。

覆盖:
- admit 基本流程（通过全部校验链）
- Validator 拒绝时的短路行为
- Detector 拒绝时的短路行为
- 审计日志记录
- 统计查询
- on_phase_change 阶段切换
"""

from __future__ import annotations

import pytest

from ai_werewolf_core.core.action.gate import ActionGate, AdmitResult
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


def make_roles(*player_ids: str) -> dict:
    """快速构造测试用 roles 字典（全部为村民）。"""
    roles = {}
    for pid in player_ids:
        role = create_role(Role.VILLAGER, pid)
        roles[pid] = role
    return roles


@pytest.fixture
def gate() -> ActionGate:
    """创建 ActionGate 实例。"""
    return ActionGate("game_001")


@pytest.fixture
def roles() -> dict:
    """创建基础 roles 字典。"""
    return make_roles("player_1", "player_2", "player_3")


@pytest.fixture
def role() -> "BaseRole":
    """创建 player_1 的村民角色。"""
    return create_role(Role.VILLAGER, "player_1")


# ============================================================================
# 准入流程测试（结构/阶段/冷却 无需 Redis 的部分）
# ============================================================================


class TestAdmitBasic:
    """基本准入流程测试。"""

    @pytest.mark.asyncio
    async def test_clean_action_admitted(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """完全合法的动作通过准入（使用 PASS 避免依赖 Redis 存活校验）。"""
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.PASS,
            phase=GamePhase.DAY_DISCUSSION,
        )
        result = await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert result.admitted is True

    @pytest.mark.asyncio
    async def test_phase_mismatch_rejected(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """阶段不匹配被 Gate 拒绝。"""
        action = make_action(
            actor_id="player_1",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        result = await gate.admit(action, role, roles, GamePhase.NIGHT_WOLF_ACT)
        assert result.admitted is False
        assert "阶段不匹配" in result.reason
        assert "ActionValidator" in result.rejected_by

    @pytest.mark.asyncio
    async def test_invalid_actor_id_rejected(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """无效 actor_id 被 Gate 拒绝。"""
        action = make_action(
            actor_id="invalid",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        result = await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert result.admitted is False
        assert "player_" in result.reason
        assert result.rejected_by.startswith("ActionValidator")

    @pytest.mark.asyncio
    async def test_ghost_player_rejected(
        self, gate: ActionGate, roles: dict
    ) -> None:
        """幽灵玩家被 Gate 拒绝（Detector 层）。"""
        ghost_role = create_role(Role.VILLAGER, "player_99")
        action = make_action(
            actor_id="player_99",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        result = await gate.admit(
            action, ghost_role, roles, GamePhase.DAY_DISCUSSION
        )
        assert result.admitted is False
        assert "幽灵玩家" in result.reason
        assert result.rejected_by == "AntiCheatDetector"


# ============================================================================
# 审计日志测试
# ============================================================================


class TestGateAudit:
    """Gate 审计日志测试。"""

    @pytest.mark.asyncio
    async def test_accepted_action_logged(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """通过的动作记录在 audit 中。"""
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.PASS,
            phase=GamePhase.DAY_DISCUSSION,
        )
        await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert gate.audit.accepted_count.get("player_1", 0) > 0

    @pytest.mark.asyncio
    async def test_rejected_action_logged_in_audit(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """拒绝的动作记录在 audit.rejected 中。"""
        action = make_action(
            actor_id="invalid_id",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert len(gate.audit.rejected) > 0
        assert gate.audit.rejected[0].rejector == "ActionValidator"

    @pytest.mark.asyncio
    async def test_get_rejection_stats(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """get_rejection_stats 返回按 actor_id 聚合的统计。"""
        # 触发一次拒绝（阶段不匹配不需要 Redis）
        action = make_action(
            actor_id="player_1",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        await gate.admit(action, role, roles, GamePhase.NIGHT_WOLF_ACT)
        stats = gate.get_rejection_stats()
        assert "player_1" in stats

    def test_snapshot(self, gate: ActionGate) -> None:
        """snapshot 返回 AuditSnapshot。"""
        snapshot = gate.snapshot()
        assert snapshot.game_id == "game_001"


# ============================================================================
# 阶段管理测试
# ============================================================================


class TestPhaseManagement:
    """阶段管理测试。"""

    def test_on_phase_change_resets_state(self, gate: ActionGate) -> None:
        """阶段切换重置计数器。"""
        # 模拟一些状态
        gate.anti_cheat._action_counts["player_1"] = 5
        gate.on_phase_change()
        # 计数器应被清空
        assert gate.anti_cheat._action_counts == {}

    def test_reset(self, gate: ActionGate) -> None:
        """reset 清空所有状态。"""
        gate.audit.log_acceptance("player_1")
        gate.anti_cheat._action_counts["player_1"] = 5
        gate.reset()
        assert gate.audit.accepted_count == {}
        assert gate.anti_cheat._action_counts == {}


# ============================================================================
# AdmitResult 数据类测试
# ============================================================================


class TestAdmitResult:
    """AdmitResult 数据类测试。"""

    def test_accepted_factory(self) -> None:
        """accepted() 工厂方法。"""
        result = AdmitResult.accepted()
        assert result.admitted is True
        assert result.reason == ""

    def test_rejected_factory(self) -> None:
        """rejected() 工厂方法。"""
        result = AdmitResult.rejected("测试拒绝", "ActionValidator")
        assert result.admitted is False
        assert result.reason == "测试拒绝"
        assert result.rejected_by == "ActionValidator"


# ============================================================================
# 冷却校验集成测试
# ============================================================================


class TestGateCooldown:
    """Gate 冷却校验集成测试。"""

    @pytest.mark.asyncio
    async def test_repeat_within_cooldown_rejected(
        self, gate: ActionGate, role, roles: dict
    ) -> None:
        """冷却窗口内重复提交相同动作被拒绝（使用 PASS 避免 Redis 依赖）。"""
        action = make_action(
            actor_id="player_1",
            action_type=ActionType.PASS,
            phase=GamePhase.DAY_DISCUSSION,
        )
        # 第一次通过
        result1 = await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert result1.admitted is True

        # 第二次（立即）应被冷却拦截
        result2 = await gate.admit(action, role, roles, GamePhase.DAY_DISCUSSION)
        assert result2.admitted is False
        assert "冷却" in result2.reason
        assert "cooldown" in result2.rejected_by

    @pytest.mark.asyncio
    async def test_different_actors_no_cooldown_conflict(
        self, gate: ActionGate, roles: dict
    ) -> None:
        """不同玩家的动作互不影响（使用 PASS 避免 Redis 依赖）。"""
        action1 = make_action(
            actor_id="player_1",
            action_type=ActionType.PASS,
            phase=GamePhase.DAY_DISCUSSION,
        )
        action2 = make_action(
            actor_id="player_2",
            action_type=ActionType.PASS,
            phase=GamePhase.DAY_DISCUSSION,
        )
        role1 = create_role(Role.VILLAGER, "player_1")
        role2 = create_role(Role.VILLAGER, "player_2")

        await gate.admit(action1, role1, roles, GamePhase.DAY_DISCUSSION)
        result = await gate.admit(action2, role2, roles, GamePhase.DAY_DISCUSSION)
        assert result.admitted is True

    @pytest.mark.asyncio
    async def test_get_violations(
        self, gate: ActionGate, roles: dict
    ) -> None:
        """get_violations 返回防作弊违规记录（使用 PASS 避免 Redis 依赖）。"""
        ghost_role = create_role(Role.VILLAGER, "player_99")
        action = make_action(
            actor_id="player_99",
            phase=GamePhase.DAY_DISCUSSION,
            action_type=ActionType.PASS,
        )
        await gate.admit(action, ghost_role, roles, GamePhase.DAY_DISCUSSION)
        violations = gate.get_violations()
        assert len(violations) == 1
