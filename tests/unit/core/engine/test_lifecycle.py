"""
LifecycleManager 单元测试。

覆盖:
- 初始状态验证
- 合法生命周期流转 (INIT → START → RUNNING → SETTLING → FINISHED)
- 非法状态迁移异常抛出
- advance_phase 委托与 GameNotRunnableError
- abort_game 从各种状态中止
- 事件发布验证
- 与 PhaseStateMachine 的联动
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_werewolf_core.core.engine.exceptions import (
    GameNotRunnableError,
    InvalidTransitionError,
)
from ai_werewolf_core.core.engine.lifecycle import LifecycleManager
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import EventType, GamePhase, GameStatus


@pytest.fixture
def event_bus():
    """提供独立的 EventBus 实例，屏蔽数据库持久化。"""
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


@pytest.fixture
def lifecycle(event_bus):
    """提供 LifecycleManager 实例。"""
    return LifecycleManager(game_id="test_lifecycle_1", event_bus=event_bus)


class TestInitialState:
    """初始状态验证。"""

    def test_initial_status_is_init(self, lifecycle):
        """新创建的 LifecycleManager 状态应为 INIT。"""
        assert lifecycle.status == GameStatus.INIT

    def test_game_id_is_set(self, lifecycle):
        """game_id 应正确保存。"""
        assert lifecycle.game_id == "test_lifecycle_1"

    def test_state_machine_is_created(self, lifecycle):
        """内部的 PhaseStateMachine 应在构造函数中创建。"""
        assert lifecycle.state_machine is not None
        assert lifecycle.state_machine.current_phase is None
        assert lifecycle.state_machine.round == 0

    def test_event_bus_is_set(self, lifecycle, event_bus):
        """event_bus 应正确保存。"""
        assert lifecycle.event_bus is event_bus


class TestInitGame:
    """init_game 方法测试。"""

    @pytest.mark.asyncio
    async def test_init_game_from_init(self, lifecycle):
        """从 INIT 调用 init_game 应迁移到 START。"""
        await lifecycle.init_game()
        assert lifecycle.status == GameStatus.START

    @pytest.mark.asyncio
    async def test_init_game_publishes_event(self, lifecycle, event_bus):
        """init_game 应通过 EventBus 发布状态变更事件。"""
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        await lifecycle.init_game()

        assert len(captured) >= 1
        # init_game 现在先发布 PHASE_TRANSITION_EVENT (INIT) 再发布 SYSTEM_ANNOUNCEMENT
        # 遍历所有事件查找状态变更事件
        status_events = [
            e for e in captured
            if e.event_type == EventType.SYSTEM_ANNOUNCEMENT
            and "old_status" in e.payload
        ]
        assert len(status_events) >= 1
        status_event = status_events[0]
        assert status_event.payload["old_status"] == GameStatus.INIT.value
        assert status_event.payload["new_status"] == GameStatus.START.value

    @pytest.mark.asyncio
    async def test_init_game_twice_raises(self, lifecycle):
        """重复调用 init_game 应抛出 InvalidTransitionError (START 不在 INIT 的后继中)。"""
        await lifecycle.init_game()
        # 此时状态为 START，再次调用 init_game 应该失败
        # 因为 _set_status 会校验 START -> START 不在 VALID_STATUS_TRANSITIONS 中
        # 但 init_game 调用的是 _set_status(GameStatus.START)，不是从当前状态校验
        # 实际上 init_game 不校验当前状态是否为 INIT 就直接调 _set_status(START)
        # 当状态已经是 START 时，_validate_status_transition(START) 检查的是
        # START -> START，在 VALID_STATUS_TRANSITIONS[START] = [RUNNING, ABORTED] 中确实没有
        with pytest.raises(InvalidTransitionError):
            await lifecycle.init_game()


class TestStartGame:
    """start_game 方法测试。"""

    @pytest.mark.asyncio
    async def test_start_game_from_start(self, lifecycle):
        """正常流程: init_game 后 start_game，状态应为 RUNNING。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        assert lifecycle.status == GameStatus.RUNNING

    @pytest.mark.asyncio
    async def test_start_game_activates_state_machine(self, lifecycle):
        """
        start_game 应激活内部的 PhaseStateMachine 并进入 NIGHT_START 阶段。

        **Why**: start_game 不仅是状态变更，还标志着局内阶段流转的开始。
        """
        await lifecycle.init_game()
        await lifecycle.start_game()

        assert lifecycle.state_machine.current_phase == GamePhase.NIGHT_START
        assert lifecycle.state_machine.round == 1

    @pytest.mark.asyncio
    async def test_start_game_without_init_raises(self, lifecycle):
        """跳过 init_game 直接调用 start_game 应抛出异常。"""
        # 当前状态为 INIT，而 INIT -> RUNNING 不在合法迁移中；
        # start_game 会调用 _set_status(RUNNING)
        with pytest.raises(InvalidTransitionError):
            await lifecycle.start_game()

    @pytest.mark.asyncio
    async def test_start_game_publishes_events(self, lifecycle, event_bus):
        """start_game 应发布状态变更事件和阶段变更事件。"""
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        await lifecycle.init_game()
        await lifecycle.start_game()

        # 至少应有: STATUS 变更 (START->RUNNING) + PHASE 变更 (NIGHT_START)
        event_types = [e.event_type for e in captured]
        assert EventType.SYSTEM_ANNOUNCEMENT in event_types
        assert EventType.PHASE_TRANSITION_EVENT in event_types


class TestAdvancePhase:
    """advance_phase 方法测试。"""

    @pytest.mark.asyncio
    async def test_advance_phase_when_running(self, lifecycle):
        """RUNNING 状态下推进阶段应正常执行。"""
        await lifecycle.init_game()
        await lifecycle.start_game()

        # 从 NIGHT_START 推进到 NIGHT_WOLF_ACT
        await lifecycle.advance_phase(GamePhase.NIGHT_WOLF_ACT)
        assert lifecycle.state_machine.current_phase == GamePhase.NIGHT_WOLF_ACT

    @pytest.mark.asyncio
    async def test_advance_phase_when_init_raises(self, lifecycle):
        """INIT 状态下推进阶段应抛出 GameNotRunnableError。"""
        with pytest.raises(GameNotRunnableError) as exc_info:
            await lifecycle.advance_phase(GamePhase.NIGHT_START)
        assert exc_info.value.current_status == GameStatus.INIT
        assert exc_info.value.game_id == "test_lifecycle_1"

    @pytest.mark.asyncio
    async def test_advance_phase_when_finished_raises(self, lifecycle):
        """FINISHED 状态下推进阶段应抛出 GameNotRunnableError。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("VILLAGER")

        with pytest.raises(GameNotRunnableError):
            await lifecycle.advance_phase(GamePhase.NIGHT_START)

    @pytest.mark.asyncio
    async def test_advance_phase_invalid_transition_raises(self, lifecycle):
        """RUNNING 状态下非法阶段跳转应抛出 InvalidTransitionError。"""
        await lifecycle.init_game()
        await lifecycle.start_game()

        # NIGHT_START -> DAY_DISCUSSION 是非法的
        with pytest.raises(InvalidTransitionError):
            await lifecycle.advance_phase(GamePhase.DAY_DISCUSSION)

    @pytest.mark.asyncio
    async def test_advance_phase_with_context(self, lifecycle, event_bus):
        """advance_phase 传入的 context 应合并到事件 payload 中。"""
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        await lifecycle.init_game()
        await lifecycle.start_game()

        # 清空之前的事件
        captured.clear()

        await lifecycle.advance_phase(
            GamePhase.NIGHT_WOLF_ACT,
            context={"wolf_target": "player_3"},
        )

        phase_events = [
            e for e in captured
            if e.event_type == EventType.PHASE_TRANSITION_EVENT
        ]
        assert len(phase_events) == 1
        assert phase_events[0].payload["wolf_target"] == "player_3"


class TestEndGame:
    """end_game 方法测试。"""

    @pytest.mark.asyncio
    async def test_end_game_normal_flow(self, lifecycle):
        """正常结束流程: RUNNING → SETTLING → FINISHED。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("VILLAGER")

        assert lifecycle.status == GameStatus.FINISHED

    @pytest.mark.asyncio
    async def test_end_game_sets_phase_to_game_over(self, lifecycle):
        """end_game 应将阶段状态机设为 GAME_OVER。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("WEREWOLF")

        assert lifecycle.state_machine.current_phase == GamePhase.GAME_OVER

    @pytest.mark.asyncio
    async def test_end_game_publishes_game_over_event(self, lifecycle, event_bus):
        """end_game 应发布 GAME_OVER_EVENT 事件。"""
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("VILLAGER")

        game_over_events = [
            e for e in captured
            if e.event_type == EventType.GAME_OVER_EVENT
        ]
        assert len(game_over_events) == 1
        assert game_over_events[0].payload["winner_faction"] == "VILLAGER"

    @pytest.mark.asyncio
    async def test_end_game_without_start_raises(self, lifecycle):
        """未 start_game 直接调用 end_game 应抛出异常。"""
        # INIT -> SETTLING 不在合法路径中
        with pytest.raises(InvalidTransitionError):
            await lifecycle.end_game("VILLAGER")

    @pytest.mark.asyncio
    async def test_end_game_twice_raises(self, lifecycle):
        """重复调用 end_game 应抛出异常 (FINISHED 无后继状态)。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("VILLAGER")

        with pytest.raises(InvalidTransitionError):
            await lifecycle.end_game("VILLAGER")


class TestAbortGame:
    """abort_game 方法测试。"""

    @pytest.mark.asyncio
    async def test_abort_from_start(self, lifecycle):
        """从 START 状态中止。"""
        await lifecycle.init_game()
        await lifecycle.abort_game("admin_cancel")
        assert lifecycle.status == GameStatus.ABORTED

    @pytest.mark.asyncio
    async def test_abort_from_running(self, lifecycle):
        """从 RUNNING 状态中止。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.abort_game("server_shutdown")
        assert lifecycle.status == GameStatus.ABORTED

    @pytest.mark.asyncio
    async def test_abort_from_settling(self, lifecycle):
        """
        从 SETTLING 状态中止。

        提示: 由于 end_game 内部自动完成 SETTLING->FINISHED，
        我们通过直接操作内部状态来模拟 SETTLING 状态。
        """
        await lifecycle.init_game()
        await lifecycle.start_game()
        # 手动推进到 SETTLING
        await lifecycle._set_status(GameStatus.SETTLING)
        await lifecycle.abort_game("settling_timeout")
        assert lifecycle.status == GameStatus.ABORTED

    @pytest.mark.asyncio
    async def test_abort_from_init_raises(self, lifecycle):
        """从 INIT 状态中止应抛出异常 (INIT 不可中止)。"""
        with pytest.raises(InvalidTransitionError):
            await lifecycle.abort_game("premature")

    @pytest.mark.asyncio
    async def test_abort_from_finished_raises(self, lifecycle):
        """从 FINISHED 状态中止应抛出异常 (已是终结态)。"""
        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.end_game("VILLAGER")

        with pytest.raises(InvalidTransitionError):
            await lifecycle.abort_game("late_abort")

    @pytest.mark.asyncio
    async def test_abort_publishes_event_with_reason(self, lifecycle, event_bus):
        """abort_game 应发布状态变更事件，并正确完成中止流程。"""
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        await lifecycle.init_game()
        await lifecycle.start_game()
        await lifecycle.abort_game("player_disconnected")

        # 验证状态已变为 ABORTED
        assert lifecycle.status == GameStatus.ABORTED

        # 查找 ABORTED 相关的 SYSTEM_ANNOUNCEMENT 事件
        abort_events = [
            e for e in captured
            if e.event_type == EventType.SYSTEM_ANNOUNCEMENT
            and e.payload.get("new_status") == GameStatus.ABORTED.value
        ]
        assert len(abort_events) >= 1


class TestLifecycleFullFlow:
    """全流程集成测试。"""

    @pytest.mark.asyncio
    async def test_full_game_flow(self, lifecycle, event_bus):
        """
        完整的游戏流程: init -> start -> advance phases -> end。

        **Why**: 验证 LifecycleManager 在典型对局场景下的行为完整性。
        """
        captured = []
        event_bus.publish = AsyncMock(side_effect=lambda e: captured.append(e))

        # 1. 初始化
        await lifecycle.init_game()
        assert lifecycle.status == GameStatus.START

        # 2. 开局
        await lifecycle.start_game()
        assert lifecycle.status == GameStatus.RUNNING
        assert lifecycle.state_machine.round == 1

        # 3. 夜晚流程
        await lifecycle.advance_phase(GamePhase.NIGHT_WOLF_ACT)
        await lifecycle.advance_phase(GamePhase.NIGHT_WITCH_ACT)
        await lifecycle.advance_phase(GamePhase.NIGHT_SEER_ACT)
        await lifecycle.advance_phase(GamePhase.NIGHT_RESOLVE)
        await lifecycle.advance_phase(GamePhase.DAY_START)
        assert lifecycle.state_machine.current_phase == GamePhase.DAY_START

        # 4. 白天流程
        await lifecycle.advance_phase(GamePhase.DAY_DISCUSSION)
        await lifecycle.advance_phase(GamePhase.DAY_VOTE)
        await lifecycle.advance_phase(GamePhase.VOTE_RESOLVE)
        await lifecycle.advance_phase(GamePhase.GAME_OVER)

        # 5. 结束
        await lifecycle.end_game("VILLAGER")
        assert lifecycle.status == GameStatus.FINISHED

        # 6. 验证事件已发布
        assert len(captured) > 0, "应该有事件被发布"
        event_types = {e.event_type for e in captured}
        assert EventType.SYSTEM_ANNOUNCEMENT in event_types
        assert EventType.PHASE_TRANSITION_EVENT in event_types
        assert EventType.GAME_OVER_EVENT in event_types

    @pytest.mark.asyncio
    async def test_game_state_machine_consistency(self, lifecycle):
        """
        验证 LifecycleManager 的状态与 PhaseStateMachine 的一致性。

        **Why**: 全局状态与局内阶段是两层控制，但在关键节点必须保持一致。
        如 start_game 后两者都应该是活跃状态。
        """
        await lifecycle.init_game()
        await lifecycle.start_game()

        assert lifecycle.status == GameStatus.RUNNING
        assert lifecycle.state_machine.current_phase == GamePhase.NIGHT_START
        assert lifecycle.state_machine.round >= 1

        await lifecycle.end_game("WEREWOLF")

        assert lifecycle.status == GameStatus.FINISHED
        assert lifecycle.state_machine.current_phase == GamePhase.GAME_OVER
