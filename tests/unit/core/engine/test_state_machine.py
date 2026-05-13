"""
PhaseStateMachine 单元测试。

覆盖:
- 初始状态验证
- 合法阶段迁移（正常流程）
- 轮次递增逻辑
- 非法迁移异常抛出
- 事件发布验证
- 边界情况（None -> INIT, GAME_OVER -> None/NIGHT_START）
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_werewolf_core.core.engine.exceptions import InvalidTransitionError
from ai_werewolf_core.core.engine.state_machine import PhaseStateMachine
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import EventType, GamePhase


@pytest.fixture
def event_bus():
    """Provide an isolated EventBus instance, cleaned up after each test."""
    bus = EventBus()
    # HACK: EventBus.__init__ registers _persist_to_db via subscribe_all().
    # Replacing the attribute does NOT affect the already-registered handler.
    # Python creates a fresh bound-method object on every attribute access,
    # so identity checks (is / is not) cannot match the registered handler.
    # Filter by function name instead, then replace with a no-op AsyncMock.
    bus._global_subscribers = [
        h for h in bus._global_subscribers
        if not (hasattr(h, '__name__') and h.__name__ == '_persist_to_db')
    ]
    bus._persist_to_db = AsyncMock()
    yield bus
    bus.clear()


import uuid

@pytest.fixture
def state_machine(event_bus):
    """提供 PhaseStateMachine 实例。"""
    return PhaseStateMachine(game_id=f"test_game_{uuid.uuid4().hex}", event_bus=event_bus)


class TestInitialState:
    """初始状态相关测试。"""

    @pytest.mark.asyncio
    async def test_initial_phase_is_none(self, state_machine):
        """新创建的 PhaseStateMachine 当前阶段应为 None。"""
        assert await state_machine.get_current_phase() is None

    @pytest.mark.asyncio
    async def test_initial_round_is_zero(self, state_machine):
        """新创建的 PhaseStateMachine 轮次应为 0。"""
        assert await state_machine.get_round() == 0

    def test_game_id_is_set(self, state_machine):
        """game_id 应正确保存。"""
        assert state_machine.game_id.startswith("test_game_")

    def test_event_bus_is_set(self, state_machine, event_bus):
        """event_bus 应正确保存。"""
        assert state_machine.event_bus is event_bus


class TestValidTransitions:
    """合法阶段迁移测试。"""

    @pytest.mark.asyncio
    async def test_none_to_init(self, state_machine):
        """从 None 迁移到 INIT 是合法的。"""
        await state_machine.transition_to(GamePhase.INIT)
        assert await state_machine.get_current_phase() == GamePhase.INIT
        # 此时轮次不变（NIGHT_START 才递增）
        assert await state_machine.get_round() == 0

    @pytest.mark.asyncio
    async def test_full_night_cycle(self, state_machine):
        """
        完整的夜晚流程: INIT -> NIGHT_START -> NIGHT_WOLF_ACT -> NIGHT_WITCH_ACT -> NIGHT_SEER_ACT -> NIGHT_RESOLVE -> DAY_START。

        **Why**: 验证夜晚阶段从开始到结算的完整链路，确保所有迁移均合法。
        """
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)

        assert await state_machine.get_current_phase() == GamePhase.DAY_START

    @pytest.mark.asyncio
    async def test_full_day_cycle(self, state_machine):
        """完整的白天流程: DAY_START -> DAY_DISCUSSION -> DAY_VOTE -> VOTE_RESOLVE -> GAME_OVER。"""
        # 先进入 DAY_START
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.GAME_OVER)

        assert await state_machine.get_current_phase() == GamePhase.GAME_OVER

    @pytest.mark.asyncio
    async def test_vote_pk_branch(self, state_machine):
        """投票平票 PK 分支: DAY_VOTE -> DAY_PK_DISCUSSION -> DAY_PK_VOTE -> VOTE_RESOLVE -> GAME_OVER。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.DAY_PK_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_PK_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.GAME_OVER)

        assert await state_machine.get_current_phase() == GamePhase.GAME_OVER

    @pytest.mark.asyncio
    async def test_hunter_shoot_branch(self, state_machine):
        """猎人开枪分支: VOTE_RESOLVE -> HUNTER_SHOOT -> GAME_OVER。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.HUNTER_SHOOT)
        await state_machine.transition_to(GamePhase.GAME_OVER)

        assert await state_machine.get_current_phase() == GamePhase.GAME_OVER

    @pytest.mark.asyncio
    async def test_last_words_from_day_vote(self, state_machine):
        """遗言分支 (从 VOTE_RESOLVE 进入): DAY_VOTE -> VOTE_RESOLVE -> LAST_WORDS -> NIGHT_START。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.LAST_WORDS)
        await state_machine.transition_to(GamePhase.NIGHT_START)

        assert await state_machine.get_current_phase() == GamePhase.NIGHT_START

    @pytest.mark.asyncio
    async def test_game_over_to_night_starts_new_round(self, state_machine):
        """
        GAME_OVER -> INIT -> NIGHT_START 应该递增轮次 (新的一轮)。

        **Why**: 这是多轮游戏的循环机制，确保每轮的天黑阶段正确标记轮次递增。
        """
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        assert await state_machine.get_round() == 1

        # 快速推进到 GAME_OVER
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.GAME_OVER)

        # 新一轮
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        assert await state_machine.get_round() == 2

    @pytest.mark.asyncio
    async def test_game_over_to_none_ends_game(self, state_machine):
        """GAME_OVER -> None 表示对局彻底结束 (无后续阶段)。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.GAME_OVER)
        # None 在 VALID_TRANSITIONS 的 values 中表示终端
        assert None in PhaseStateMachine.VALID_TRANSITIONS[GamePhase.GAME_OVER]

    @pytest.mark.asyncio
    async def test_round_only_increments_on_night_start(self, state_machine):
        """
        只有进入 NIGHT_START 时轮次才递增，其他阶段迁移不改变轮次。

        **Why**: 轮次以"天黑-天亮"循环为单位，内部子阶段不改变轮次。
        """
        await state_machine.transition_to(GamePhase.INIT)
        assert await state_machine.get_round() == 0

        await state_machine.transition_to(GamePhase.NIGHT_START)
        assert await state_machine.get_round() == 1

        # 后续子阶段不应递增轮次
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.DAY_START)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        assert await state_machine.get_round() == 1

        await state_machine.transition_to(GamePhase.DAY_VOTE)
        assert await state_machine.get_round() == 1


class TestInvalidTransitions:
    """非法阶段迁移测试。"""

    @pytest.mark.asyncio
    async def test_init_to_day_discussion_raises(self, state_machine):
        """从 INIT 直接跳到 DAY_DISCUSSION 应抛出 InvalidTransitionError。"""
        with pytest.raises(InvalidTransitionError) as exc_info:
            await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        assert exc_info.value.current_state is None
        assert exc_info.value.target_state == GamePhase.DAY_DISCUSSION

    @pytest.mark.asyncio
    async def test_night_wolf_act_to_day_discussion_raises(self, state_machine):
        """从 NIGHT_WOLF_ACT 跳过后续阶段直接到 DAY_DISCUSSION 应抛出异常。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)

        with pytest.raises(InvalidTransitionError):
            await state_machine.transition_to(GamePhase.DAY_DISCUSSION)

    @pytest.mark.asyncio
    async def test_day_discussion_to_night_wolf_act_raises(self, state_machine):
        """从 DAY_DISCUSSION 逆序跳回 NIGHT_WOLF_ACT 应抛出异常。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)

        with pytest.raises(InvalidTransitionError):
            await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)

    @pytest.mark.asyncio
    async def test_game_over_to_day_discussion_raises(self, state_machine):
        """从 GAME_OVER 跳回 DAY_DISCUSSION 应抛出异常。"""
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        await state_machine.transition_to(GamePhase.NIGHT_WOLF_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_WITCH_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_SEER_ACT)
        await state_machine.transition_to(GamePhase.NIGHT_RESOLVE)
        await state_machine.transition_to(GamePhase.DAY_START)
        await state_machine.transition_to(GamePhase.DAY_DISCUSSION)
        await state_machine.transition_to(GamePhase.DAY_VOTE)
        await state_machine.transition_to(GamePhase.VOTE_RESOLVE)
        await state_machine.transition_to(GamePhase.GAME_OVER)

        with pytest.raises(InvalidTransitionError):
            await state_machine.transition_to(GamePhase.DAY_DISCUSSION)

    @pytest.mark.asyncio
    async def test_illegal_transition_preserves_state(self, state_machine):
        """
        非法迁移不应修改当前状态 (原子性保证)。

        **Why**: 状态机必须保证操作原子性 —— 校验失败时状态保持
        不变，避免残留半更新状态。
        """
        await state_machine.transition_to(GamePhase.INIT)
        await state_machine.transition_to(GamePhase.NIGHT_START)
        phase_before = await state_machine.get_current_phase()

        try:
            await state_machine.transition_to(GamePhase.DAY_VOTE)
        except InvalidTransitionError:
            pass

        assert await state_machine.get_current_phase() == phase_before


class TestEventPublishing:
    """事件发布相关测试。"""

    @pytest.mark.asyncio
    async def test_transition_publishes_event(self, event_bus):
        """阶段迁移应发布 PHASE_TRANSITION_EVENT 事件。"""
        import uuid
        game_id = f"test_pub_{uuid.uuid4().hex}"
        sm = PhaseStateMachine(game_id=game_id, event_bus=event_bus)

        # 使用 mock 捕获 publish 调用
        event_bus.publish = AsyncMock(wraps=event_bus.publish)

        await sm.transition_to(GamePhase.INIT)

        event_bus.publish.assert_called()
        call_args = event_bus.publish.call_args[0]
        event = call_args[0]
        assert event.event_type == EventType.PHASE_TRANSITION_EVENT
        assert event.game_id == game_id
        assert event.visibility == "PUBLIC"
        assert event.payload["old_phase"] is None
        assert event.payload["new_phase"] == GamePhase.INIT.value

    @pytest.mark.asyncio
    async def test_transition_event_contains_context(self, event_bus):
        """发布的事件 payload 应包含调用方传入的 context 数据。"""
        import uuid
        game_id = f"test_ctx_{uuid.uuid4().hex}"
        sm = PhaseStateMachine(game_id=game_id, event_bus=event_bus)

        captured_events = []
        event_bus.publish = AsyncMock(
            side_effect=lambda e: captured_events.append(e)
        )

        await sm.transition_to(
            GamePhase.INIT,
            context={"initiated_by": "admin", "players": 8},
        )

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.payload["initiated_by"] == "admin"
        assert event.payload["players"] == 8

    @pytest.mark.asyncio
    async def test_transition_event_contains_round(self, event_bus):
        """事件 payload 应包含当前轮次信息。"""
        import uuid
        game_id = f"test_rnd_{uuid.uuid4().hex}"
        sm = PhaseStateMachine(game_id=game_id, event_bus=event_bus)

        captured_events = []
        event_bus.publish = AsyncMock(
            side_effect=lambda e: captured_events.append(e)
        )

        await sm.transition_to(GamePhase.INIT)
        await sm.transition_to(GamePhase.NIGHT_START)

        # NIGHT_START 事件应包含 round=1
        night_event = captured_events[-1]
        assert night_event.payload["round"] == 1


class TestValidTransitionsMapping:
    """VALID_TRANSITIONS 映射表本身的正确性验证。"""

    def test_none_key_has_only_init(self):
        """键 None 的唯一合法目标应是 GamePhase.INIT。"""
        valid = PhaseStateMachine.VALID_TRANSITIONS[None]
        assert valid == [GamePhase.INIT]

    def test_all_enum_values_have_transitions(self):
        """
        所有 GamePhase 枚举值 (除 GAME_OVER 外) 都应有对应的非空迁移列表。

        **Why**: 确保每个阶段都能继续推进，不存在死锁阶段。
        """
        for phase in GamePhase:
            if phase in PhaseStateMachine.VALID_TRANSITIONS:
                targets = PhaseStateMachine.VALID_TRANSITIONS[phase]
                assert len(targets) > 0, (
                    f"GamePhase.{phase.name} 的合法迁移目标为空，"
                    f"可能导致对局卡死。"
                )

    def test_finished_status_has_no_transitions(self):
        """终结态 (None 作为 value) 仅出现在 GAME_OVER 的合法目标中。"""
        for phase, targets in PhaseStateMachine.VALID_TRANSITIONS.items():
            if phase is None:
                continue
            if None in targets:
                assert phase == GamePhase.GAME_OVER, (
                    f"仅 GAME_OVER 允许迁移到终端 None，"
                    f"但 {phase} 也包含了 None。"
                )
