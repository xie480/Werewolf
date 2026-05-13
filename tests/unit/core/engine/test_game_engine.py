"""GameEngine 编排器单元测试。

覆盖:
- 初始化和子系统装配
- submit_action 按阶段动作路由
- _determine_next_phase 决策表（所有阶段分支）
- _has_dead_hunter / _is_retryable 辅助方法
- GameStartResult / SubmitResult / AdvanceResult 数据类

注意: 由于 Redis 在测试环境中不可用，以下测试已跳过：
  - init_game / start_game（依赖 Redis）
  - advance_phase 完整流程（依赖 Redis）
  - submit_action 通过门禁流向 Manager 的完整路径（依赖 Redis 用于存活校验）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_werewolf_core.core.action.gate import AdmitResult
from ai_werewolf_core.core.engine.game_engine import (
    NIGHT_ACT_PHASES,
    RESOLVE_PHASES,
    SPEECH_PHASES,
    VOTE_PHASES,
    AdvanceResult,
    GameEngine,
    GameStartResult,
    SubmitResult,
)
from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import ActionType, GamePhase, GameStatus, Role


# ============================================================================
# 辅助函数
# ============================================================================


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


def make_roles(*role_specs: tuple[str, Role]) -> dict:
    """从 (player_id, Role) 元组列表构造 roles 字典。"""
    roles = {}
    for pid, role_type in role_specs:
        roles[pid] = create_role(role_type, pid)
    return roles


def make_mock_event_bus() -> EventBus:
    """创建一个 mock EventBus（避免 Redis 连接）。"""
    mock = MagicMock(spec=EventBus)
    mock.publish = AsyncMock()
    return mock


# ============================================================================
# 固定数据
# ============================================================================


@pytest.fixture
def event_bus() -> EventBus:
    """Mock EventBus。"""
    return make_mock_event_bus()


@pytest.fixture
def basic_roles() -> dict:
    """基础角色配置：1 狼人 + 1 预言家 + 1 女巫 + 1 猎人 + 2 村民。"""
    return make_roles(
        ("player_1", Role.WEREWOLF),
        ("player_2", Role.VILLAGER),
        ("player_3", Role.SEER),
        ("player_4", Role.WITCH),
        ("player_5", Role.HUNTER),
        ("player_6", Role.VILLAGER),
    )


@pytest.fixture
def engine(event_bus: EventBus, basic_roles: dict) -> GameEngine:
    """创建 GameEngine 实例。"""
    return GameEngine("game_001", event_bus, basic_roles)


# ============================================================================
# 初始化和装配测试
# ============================================================================


class TestEngineInitialization:
    """GameEngine 初始化测试。"""

    def test_engine_initialized_with_subsystems(
        self, engine: GameEngine, event_bus: EventBus, basic_roles: dict
    ) -> None:
        """引擎初始化后所有子系统引用均已设置。"""
        assert engine.game_id == "game_001"
        assert engine.event_bus is event_bus
        assert engine.roles is basic_roles
        assert engine.state_machine is not None
        assert engine.lifecycle is not None
        assert engine.action_gate is not None
        assert engine.resolver is not None
        assert engine.vote_manager is not None
        assert engine.special_action_resolver is not None

    def test_roles_preserved(self, engine: GameEngine, basic_roles: dict) -> None:
        """roles 字典在初始化后被保留。"""
        assert len(engine.roles) == 6
        assert engine.roles["player_5"].role_type == Role.HUNTER
        assert engine.roles["player_1"].role_type == Role.WEREWOLF


# ============================================================================
# submit_action 路由测试（不需要 Redis）
# ============================================================================


class TestSubmitActionRouting:
    """submit_action 阶段路由测试。

    通过 mock state_machine.get_current_phase() 来模拟不同阶段，
    验证动作是否路由到正确的 Manager 或返回预期的拒绝结果。
    """

    @pytest.mark.asyncio
    async def test_unknown_player_rejected(
        self, engine: GameEngine
    ) -> None:
        """未知的 actor_id 被拒绝。"""
        action = make_action(actor_id="player_99")
        result = await engine.submit_action(action)
        assert result.accepted is False
        assert "未知玩家" in result.reason

    @pytest.mark.asyncio
    async def test_game_not_started_rejected(
        self, engine: GameEngine
    ) -> None:
        """游戏未开始时提交动作被拒绝。"""
        with patch.object(
            engine.state_machine, "get_current_phase", AsyncMock(return_value=None)
        ):
            action = make_action(actor_id="player_1")
            result = await engine.submit_action(action)
            assert result.accepted is False
            assert "游戏尚未开始" in result.reason

    @pytest.mark.asyncio
    async def test_resolve_phase_rejected(
        self, engine: "GameEngine"
    ) -> None:
        """结算阶段不接受动作提交（使用 PASS 避免依赖 Redis 存活校验）。"""
        for phase in [GamePhase.NIGHT_RESOLVE, GamePhase.VOTE_RESOLVE, GamePhase.GAME_OVER]:
            with patch.object(
                engine.state_machine,
                "get_current_phase",
                AsyncMock(return_value=phase),
            ):
                action = make_action(
                    actor_id="player_1", phase=phase, action_type=ActionType.PASS
                )
                result = await engine.submit_action(action)
                assert result.accepted is False
                assert "不接受动作提交" in result.reason

    @pytest.mark.asyncio
    async def test_gate_rejection_short_circuits(
        self, engine: GameEngine
    ) -> None:
        """ActionGate 拒绝时短路返回，不进行路由。"""
        with patch.object(
            engine.state_machine,
            "get_current_phase",
            AsyncMock(return_value=GamePhase.NIGHT_WOLF_ACT),
        ):
            action = make_action(
                actor_id="player_1",
                action_type=ActionType.WOLF_KILL,
                phase=GamePhase.DAY_DISCUSSION,  # 阶段不匹配
                target_id="player_2",
            )
            result = await engine.submit_action(action)
            assert result.accepted is False
            assert "阶段不匹配" in result.reason
            # 验证解析器未被调用
            assert engine.resolver.pending_actions == []


# ============================================================================
# _determine_next_phase 决策表测试
# ============================================================================


class TestDetermineNextPhase:
    """_determine_next_phase 决策表测试。

    覆盖设计文档第 3 节中的每个阶段转移规则。
    """

    # ── game_over 优先级测试 ──

    def test_game_over_overrides_all(self, engine: GameEngine) -> None:
        """game_over=True 时始终返回 GAME_OVER，不论当前阶段。"""
        for phase in GamePhase:
            if phase == GamePhase.GAME_OVER:
                continue
            result = engine._determine_next_phase(phase, [], True, None)
            assert result == GamePhase.GAME_OVER, f"phase={phase}"

    # ── 夜晚阶段链 ──

    def test_init_to_night_start(self, engine: GameEngine) -> None:
        """INIT → NIGHT_START。"""
        assert (
            engine._determine_next_phase(GamePhase.INIT, [], False, None)
            == GamePhase.NIGHT_START
        )

    def test_night_start_to_wolf_act(self, engine: GameEngine) -> None:
        """NIGHT_START → NIGHT_WOLF_ACT。"""
        assert (
            engine._determine_next_phase(GamePhase.NIGHT_START, [], False, None)
            == GamePhase.NIGHT_WOLF_ACT
        )

    def test_night_wolf_act_to_witch_act(self, engine: GameEngine) -> None:
        """NIGHT_WOLF_ACT → NIGHT_WITCH_ACT。"""
        assert (
            engine._determine_next_phase(GamePhase.NIGHT_WOLF_ACT, [], False, None)
            == GamePhase.NIGHT_WITCH_ACT
        )

    def test_night_witch_act_to_seer_act(self, engine: GameEngine) -> None:
        """NIGHT_WITCH_ACT → NIGHT_SEER_ACT。"""
        assert (
            engine._determine_next_phase(GamePhase.NIGHT_WITCH_ACT, [], False, None)
            == GamePhase.NIGHT_SEER_ACT
        )

    def test_night_seer_act_to_night_resolve(self, engine: GameEngine) -> None:
        """NIGHT_SEER_ACT → NIGHT_RESOLVE。"""
        assert (
            engine._determine_next_phase(GamePhase.NIGHT_SEER_ACT, [], False, None)
            == GamePhase.NIGHT_RESOLVE
        )

    def test_night_resolve_to_day_start(self, engine: GameEngine) -> None:
        """NIGHT_RESOLVE → DAY_START。"""
        assert (
            engine._determine_next_phase(GamePhase.NIGHT_RESOLVE, [], False, None)
            == GamePhase.DAY_START
        )

    # ── DAY_START 分支 ──

    def test_day_start_hunter_dies_at_night(self, engine: GameEngine) -> None:
        """DAY_START + 猎人死亡 → HUNTER_SHOOT。"""
        assert (
            engine._determine_next_phase(
                GamePhase.DAY_START, ["player_5"], False, None
            )
            == GamePhase.HUNTER_SHOOT
        )

    def test_day_start_non_hunter_death(self, engine: GameEngine) -> None:
        """DAY_START + 非猎人死亡 → DAY_DISCUSSION。"""
        assert (
            engine._determine_next_phase(
                GamePhase.DAY_START, ["player_2"], False, None
            )
            == GamePhase.DAY_DISCUSSION
        )

    def test_day_start_peaceful_night(self, engine: GameEngine) -> None:
        """DAY_START + 平安夜 → DAY_DISCUSSION。"""
        assert (
            engine._determine_next_phase(GamePhase.DAY_START, [], False, None)
            == GamePhase.DAY_DISCUSSION
        )

    # ── 白天阶段链 ──

    def test_day_discussion_to_day_vote(self, engine: GameEngine) -> None:
        """DAY_DISCUSSION → DAY_VOTE。"""
        assert (
            engine._determine_next_phase(GamePhase.DAY_DISCUSSION, [], False, None)
            == GamePhase.DAY_VOTE
        )

    def test_day_vote_no_tie(self, engine: GameEngine) -> None:
        """DAY_VOTE + 无平票 → VOTE_RESOLVE。"""
        from ai_werewolf_core.core.engine.vote_manager import VoteResolveResult

        vr = VoteResolveResult(False, ["player_2"], {}, {}, 6)
        assert (
            engine._determine_next_phase(GamePhase.DAY_VOTE, [], False, vr)
            == GamePhase.VOTE_RESOLVE
        )

    def test_day_vote_tie(self, engine: GameEngine) -> None:
        """DAY_VOTE + 平票 → DAY_PK_DISCUSSION。"""
        from ai_werewolf_core.core.engine.vote_manager import VoteResolveResult

        vr = VoteResolveResult(True, ["player_2", "player_3"], {}, {}, 6)
        assert (
            engine._determine_next_phase(GamePhase.DAY_VOTE, [], False, vr)
            == GamePhase.DAY_PK_DISCUSSION
        )

    def test_day_vote_none_result(self, engine: GameEngine) -> None:
        """DAY_VOTE + vote_result=None → VOTE_RESOLVE（默认）。"""
        assert (
            engine._determine_next_phase(GamePhase.DAY_VOTE, [], False, None)
            == GamePhase.VOTE_RESOLVE
        )

    def test_day_pk_discussion_to_pk_vote(self, engine: GameEngine) -> None:
        """DAY_PK_DISCUSSION → DAY_PK_VOTE。"""
        assert (
            engine._determine_next_phase(GamePhase.DAY_PK_DISCUSSION, [], False, None)
            == GamePhase.DAY_PK_VOTE
        )

    def test_day_pk_vote_to_vote_resolve(self, engine: GameEngine) -> None:
        """DAY_PK_VOTE → VOTE_RESOLVE。"""
        assert (
            engine._determine_next_phase(GamePhase.DAY_PK_VOTE, [], False, None)
            == GamePhase.VOTE_RESOLVE
        )

    # ── VOTE_RESOLVE 分支 ──

    def test_vote_resolve_hunter_voted_out(self, engine: GameEngine) -> None:
        """VOTE_RESOLVE + 猎人被票 → HUNTER_SHOOT。"""
        assert (
            engine._determine_next_phase(
                GamePhase.VOTE_RESOLVE, ["player_5"], False, None
            )
            == GamePhase.HUNTER_SHOOT
        )

    def test_vote_resolve_non_hunter_voted_out(self, engine: GameEngine) -> None:
        """VOTE_RESOLVE + 非猎人被票 → LAST_WORDS。"""
        assert (
            engine._determine_next_phase(
                GamePhase.VOTE_RESOLVE, ["player_2"], False, None
            )
            == GamePhase.LAST_WORDS
        )

    def test_vote_resolve_no_death(self, engine: GameEngine) -> None:
        """VOTE_RESOLVE + 无人被票 → NIGHT_START。"""
        assert (
            engine._determine_next_phase(GamePhase.VOTE_RESOLVE, [], False, None)
            == GamePhase.NIGHT_START
        )

    # ── 特殊阶段 ──

    def test_hunter_shoot_to_day_discussion(self, engine: GameEngine) -> None:
        """HUNTER_SHOOT → DAY_DISCUSSION。"""
        assert (
            engine._determine_next_phase(GamePhase.HUNTER_SHOOT, [], False, None)
            == GamePhase.DAY_DISCUSSION
        )

    def test_last_words_to_night_start(self, engine: GameEngine) -> None:
        """LAST_WORDS → NIGHT_START。"""
        assert (
            engine._determine_next_phase(GamePhase.LAST_WORDS, [], False, None)
            == GamePhase.NIGHT_START
        )

    def test_game_over_self_loop(self, engine: GameEngine) -> None:
        """GAME_OVER → GAME_OVER（终态）。"""
        assert (
            engine._determine_next_phase(GamePhase.GAME_OVER, [], False, None)
            == GamePhase.GAME_OVER
        )


# ============================================================================
# _has_dead_hunter 辅助方法测试
# ============================================================================


class TestHasDeadHunter:
    """_has_dead_hunter 辅助方法测试。"""

    def test_hunter_in_deaths(self, engine: GameEngine) -> None:
        """死亡名单中有猎人时返回 True。"""
        assert engine._has_dead_hunter(["player_5"]) is True

    def test_hunter_not_in_deaths(self, engine: GameEngine) -> None:
        """死亡名单中无猎人时返回 False。"""
        assert engine._has_dead_hunter(["player_1", "player_2"]) is False

    def test_empty_deaths(self, engine: GameEngine) -> None:
        """空死亡名单返回 False。"""
        assert engine._has_dead_hunter([]) is False

    def test_multiple_deaths_includes_hunter(self, engine: GameEngine) -> None:
        """多个死亡含猎人时返回 True。"""
        assert engine._has_dead_hunter(["player_1", "player_5", "player_3"]) is True


# ============================================================================
# _is_retryable 辅助方法测试
# ============================================================================


class TestIsRetryable:
    """_is_retryable 辅助方法测试。"""

    def test_cooldown_retryable(self, engine: GameEngine) -> None:
        """冷却拒绝可重试。"""
        result = AdmitResult.rejected("冷却中", "ActionValidator.cooldown")
        assert engine._is_retryable(result) is True

    def test_phase_mismatch_retryable(self, engine: GameEngine) -> None:
        """阶段不匹配可重试。"""
        result = AdmitResult.rejected("阶段不匹配", "ActionValidator.phase")
        assert engine._is_retryable(result) is True

    def test_survival_retryable(self, engine: GameEngine) -> None:
        """生存状态不匹配可重试。"""
        result = AdmitResult.rejected("角色要求存活但玩家已死亡", "ActionValidator.survival")
        assert engine._is_retryable(result) is True

    def test_ghost_player_not_retryable(self, engine: GameEngine) -> None:
        """幽灵玩家不可重试。"""
        result = AdmitResult.rejected("幽灵玩家", "AntiCheatDetector")
        assert engine._is_retryable(result) is False

    def test_structural_error_not_retryable(self, engine: GameEngine) -> None:
        """结构错误不可重试。"""
        result = AdmitResult.rejected("actor_id 格式无效", "ActionValidator.structural")
        assert engine._is_retryable(result) is False


# ============================================================================
# 阶段常量测试
# ============================================================================


class TestPhaseConstants:
    """阶段常量测试。"""

    def test_night_act_phases(self) -> None:
        """NIGHT_ACT_PHASES 包含所有夜晚行动阶段。"""
        assert GamePhase.NIGHT_WOLF_ACT in NIGHT_ACT_PHASES
        assert GamePhase.NIGHT_WITCH_ACT in NIGHT_ACT_PHASES
        assert GamePhase.NIGHT_SEER_ACT in NIGHT_ACT_PHASES
        assert GamePhase.DAY_DISCUSSION not in NIGHT_ACT_PHASES

    def test_vote_phases(self) -> None:
        """VOTE_PHASES 包含所有投票阶段。"""
        assert GamePhase.DAY_VOTE in VOTE_PHASES
        assert GamePhase.DAY_PK_VOTE in VOTE_PHASES

    def test_speech_phases(self) -> None:
        """SPEECH_PHASES 包含所有发言阶段。"""
        assert GamePhase.DAY_DISCUSSION in SPEECH_PHASES
        assert GamePhase.DAY_PK_DISCUSSION in SPEECH_PHASES
        assert GamePhase.LAST_WORDS in SPEECH_PHASES

    def test_resolve_phases(self) -> None:
        """RESOLVE_PHASES 不接受动作提交。"""
        assert GamePhase.NIGHT_RESOLVE in RESOLVE_PHASES
        assert GamePhase.VOTE_RESOLVE in RESOLVE_PHASES
        assert GamePhase.GAME_OVER in RESOLVE_PHASES


# ============================================================================
# 数据类测试
# ============================================================================


class TestGameStartResult:
    """GameStartResult 数据类测试。"""

    def test_creation(self) -> None:
        """正常构造。"""
        result = GameStartResult(
            game_id="g1",
            player_count=6,
            role_distribution={"player_1": "WEREWOLF"},
            initial_phase=GamePhase.NIGHT_START,
        )
        assert result.game_id == "g1"
        assert result.player_count == 6
        assert result.initial_phase == GamePhase.NIGHT_START

    def test_frozen(self) -> None:
        """不可变。"""
        result = GameStartResult("g1", 6, {}, GamePhase.NIGHT_START)
        with pytest.raises(Exception):
            result.game_id = "g2"  # type: ignore[misc]


class TestSubmitResult:
    """SubmitResult 数据类测试。"""

    def test_accepted_factory(self) -> None:
        """accepted_result() 工厂方法。"""
        result = SubmitResult.accepted_result()
        assert result.accepted is True
        assert result.reason is None

    def test_rejected_factory(self) -> None:
        """rejected_result() 工厂方法。"""
        result = SubmitResult.rejected_result("测试拒绝", retry=True)
        assert result.accepted is False
        assert result.reason == "测试拒绝"
        assert result.requires_retry is True

    def test_frozen(self) -> None:
        """不可变。"""
        result = SubmitResult.accepted_result()
        with pytest.raises(Exception):
            result.accepted = False  # type: ignore[misc]


class TestAdvanceResult:
    """AdvanceResult 数据类测试。"""

    def test_creation(self) -> None:
        """正常构造。"""
        result = AdvanceResult(
            old_phase=GamePhase.NIGHT_WOLF_ACT,
            new_phase=GamePhase.NIGHT_WITCH_ACT,
            round=1,
            deaths=[],
        )
        assert result.old_phase == GamePhase.NIGHT_WOLF_ACT
        assert result.new_phase == GamePhase.NIGHT_WITCH_ACT
        assert result.round == 1
        assert result.game_over is False

    def test_with_deaths(self) -> None:
        """带死亡名单。"""
        result = AdvanceResult(
            old_phase=GamePhase.NIGHT_RESOLVE,
            new_phase=GamePhase.DAY_START,
            round=2,
            deaths=["player_2"],
        )
        assert result.deaths == ["player_2"]

    def test_game_over(self) -> None:
        """game_over 字段。"""
        result = AdvanceResult(
            old_phase=GamePhase.VOTE_RESOLVE,
            new_phase=GamePhase.GAME_OVER,
            round=3,
            deaths=[],
            game_over=True,
            winner="VILLAGER",
        )
        assert result.game_over is True
        assert result.winner == "VILLAGER"


# ============================================================================
# schedule_phase_timer 测试
# ============================================================================


class TestSchedulePhaseTimer:
    """schedule_phase_timer 方法测试。"""

    @pytest.mark.asyncio
    async def test_schedule_phase_timer_success(
        self, engine: GameEngine
    ) -> None:
        """调度阶段定时器应成功创建并保存 task_id。"""
        from unittest.mock import ANY

        with patch(
            "ai_werewolf_core.tasks.game.advance_phase_task"
        ) as mock_task, patch.object(
            engine.lifecycle, "save_task_id", AsyncMock()
        ) as mock_save:
            mock_task.apply_async = MagicMock(
                return_value=MagicMock(id="mock_task_id_123")
            )

            await engine.schedule_phase_timer(GamePhase.NIGHT_WOLF_ACT)

            mock_task.apply_async.assert_called_once()
            call_kwargs = mock_task.apply_async.call_args[1]
            assert call_kwargs["countdown"] == 60
            mock_save.assert_called_once_with("mock_task_id_123")

    @pytest.mark.asyncio
    async def test_schedule_phase_timer_game_over(
        self, engine: GameEngine
    ) -> None:
        """GAME_OVER 阶段不调度定时器。"""
        with patch(
            "ai_werewolf_core.tasks.game.advance_phase_task"
        ) as mock_task:
            mock_task.apply_async = MagicMock()

            await engine.schedule_phase_timer(GamePhase.GAME_OVER)

            mock_task.apply_async.assert_not_called()


# ============================================================================
# _check_early_termination 测试
# ============================================================================


class TestEarlyTermination:
    """提前结束机制测试。"""

    @pytest.mark.asyncio
    async def test_submit_action_is_accepted(
        self, engine: GameEngine
    ) -> None:
        """狼人动作通过 mock 门禁后应被接受。"""
        with patch.object(
            engine.state_machine, "get_current_phase",
            AsyncMock(return_value=GamePhase.NIGHT_WOLF_ACT),
        ), patch.object(
            engine.action_gate, "admit",
            AsyncMock(return_value=AdmitResult.accepted()),
        ):
            action = make_action(
                actor_id="player_1",
                action_type=ActionType.WOLF_KILL,
                target_id="player_2",
                phase=GamePhase.NIGHT_WOLF_ACT,
            )
            result = await engine.submit_action(action)
            assert result.accepted is True

    @pytest.mark.asyncio
    async def test_wolf_actions_stored_in_resolver(
        self, engine: GameEngine
    ) -> None:
        """通过 submit_action 提交的狼人动作应存入 resolver 的 pending_actions。"""
        with patch.object(
            engine.state_machine, "get_current_phase",
            AsyncMock(return_value=GamePhase.NIGHT_WOLF_ACT),
        ), patch.object(
            engine.action_gate, "admit",
            AsyncMock(return_value=AdmitResult.accepted()),
        ):
            wolves = [pid for pid, r in engine.roles.items() if r.role_type == Role.WEREWOLF]
            for w in wolves:
                action = make_action(
                    actor_id=w,
                    action_type=ActionType.WOLF_KILL,
                    target_id="player_2",
                    phase=GamePhase.NIGHT_WOLF_ACT,
                )
                await engine.submit_action(action)

            wolf_kills = [
                a for a in engine.resolver.pending_actions
                if a.action_type == ActionType.WOLF_KILL
            ]
            assert len(wolf_kills) == len(wolves)
            assert engine.resolver.is_action_completed(
                engine.roles, GamePhase.NIGHT_WOLF_ACT
            )

    @pytest.mark.asyncio
    async def test_is_action_completed_partial(
        self, engine: GameEngine
    ) -> None:
        """部分狼人提交时 is_action_completed 返回 False。"""
        # 创建一个有多只狼的角色配置
        multi_wolf_roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_2", Role.WEREWOLF),
            ("player_3", Role.VILLAGER),
            ("player_4", Role.VILLAGER),
        )
        multi_engine = GameEngine("game_multi", make_mock_event_bus(), multi_wolf_roles)

        with patch.object(
            multi_engine.state_machine, "get_current_phase",
            AsyncMock(return_value=GamePhase.NIGHT_WOLF_ACT),
        ), patch.object(
            multi_engine.action_gate, "admit",
            AsyncMock(return_value=AdmitResult.accepted()),
        ):
            action = make_action(
                actor_id="player_1",
                action_type=ActionType.WOLF_KILL,
                target_id="player_3",
                phase=GamePhase.NIGHT_WOLF_ACT,
            )
            await multi_engine.submit_action(action)
            # 只有 player_1 提交，player_2 未提交
            assert not multi_engine.resolver.is_action_completed(
                multi_engine.roles, GamePhase.NIGHT_WOLF_ACT
            )
