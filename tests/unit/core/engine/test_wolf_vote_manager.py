"""WolfVoteManager 单元测试。

覆盖:
- 初始化与 Redis Key 构建
- submit_vote 校验逻辑（动作类型、存活状态、阵营身份、目标有效性）
- is_vote_complete 投票完整性检测
- resolve_vote 结算逻辑（唯一最高票、平票、全弃权）
- 审计时间戳记录
- 多狼人并行投票场景

注意: 涉及 Lua 脚本的集成测试需要 Redis 运行环境。
本文件中的单元测试通过 mock 避免 Redis 依赖。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_werewolf_core.core.engine.roles import create_role
from ai_werewolf_core.core.engine.wolf_vote_manager import (
    WolfVoteManager,
    WolfVoteResolveResult,
)
from ai_werewolf_core.core.event.bus import EventBus
from ai_werewolf_core.schemas.enums import (
    ActionType,
    Faction,
    GamePhase,
    Role,
)
from ai_werewolf_core.schemas.models import AgentAction


# ============================================================================
# 辅助函数
# ============================================================================


def make_action(
    actor_id: str = "player_1",
    action_type: ActionType = ActionType.WOLF_KILL,
    target_id: str | None = "player_7",
    phase: GamePhase = GamePhase.NIGHT_WOLF_ACT,
    round_num: int = 1,
) -> AgentAction:
    """快速构造测试用 AgentAction。

    注意: AgentAction.target_id 校验要求以 'player_' 开头。
    """
    return AgentAction(
        action_type=action_type,
        actor_id=actor_id,
        target_id=target_id,
        phase=phase,
        round=round_num,
        reason="狼人投票测试",
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

# 标准 12 人局：3 狼人 + 1 预言家 + 1 女巫 + 1 猎人 + 6 村民
WEREWOLF_IDS = ["player_1", "player_2", "player_3"]

STANDARD_ROLES = make_roles(
    ("player_1", Role.WEREWOLF),
    ("player_2", Role.WEREWOLF),
    ("player_3", Role.WEREWOLF),
    ("player_4", Role.SEER),
    ("player_5", Role.WITCH),
    ("player_6", Role.HUNTER),
    ("player_7", Role.VILLAGER),
    ("player_8", Role.VILLAGER),
    ("player_9", Role.VILLAGER),
    ("player_10", Role.VILLAGER),
    ("player_11", Role.VILLAGER),
    ("player_12", Role.VILLAGER),
)


@pytest.fixture
def event_bus() -> EventBus:
    """Mock EventBus。"""
    return make_mock_event_bus()


@pytest.fixture
def manager(event_bus: EventBus) -> WolfVoteManager:
    """创建 WolfVoteManager 实例。"""
    mgr = WolfVoteManager("game_001", event_bus)
    mgr._current_round = 1
    return mgr


# ============================================================================
# 测试类
# ============================================================================


class TestWolfVoteManagerInit:
    """初始化测试。"""

    def test_init(self, manager: WolfVoteManager) -> None:
        """初始化后所有属性正确设置。"""
        assert manager.game_id == "game_001"
        assert manager.event_bus is not None
        assert manager._current_round == 1

    def test_vote_key_format(self, manager: WolfVoteManager) -> None:
        """_vote_key 返回正确的 Redis Key 格式。"""
        key = manager._vote_key()
        assert key == "werewolf:wolf_vote:game_001:1"


class TestSubmitVoteValidation:
    """submit_vote 校验逻辑测试（不依赖 Redis）。"""

    @pytest.mark.asyncio
    async def test_wrong_action_type_rejected(self, manager: WolfVoteManager) -> None:
        """非 WOLF_KILL 动作类型被拒绝。"""
        action = make_action(action_type=ActionType.VOTE, actor_id="player_1")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, STANDARD_ROLES, GamePhase.NIGHT_WOLF_ACT)
        assert "非狼人投票动作" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unknown_voter_rejected(self, manager: WolfVoteManager) -> None:
        """不存在的投票人被拒绝。"""
        action = make_action(actor_id="player_99")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, STANDARD_ROLES, GamePhase.NIGHT_WOLF_ACT)
        assert "不存在于当前对局中" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_dead_wolf_rejected(self, manager: WolfVoteManager) -> None:
        """死亡的狼人不能投票。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_2", Role.WEREWOLF),
            ("player_7", Role.VILLAGER),
        )
        # 狼人死亡
        roles["player_1"].die()

        action = make_action(actor_id="player_1")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, roles, GamePhase.NIGHT_WOLF_ACT)
        assert "已死亡" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_wolf_rejected(self, manager: WolfVoteManager) -> None:
        """非狼人阵营的玩家不能参与狼人投票。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_7", Role.VILLAGER),  # 村民，非狼人
        )
        action = make_action(actor_id="player_7")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, roles, GamePhase.NIGHT_WOLF_ACT)
        assert "不是狼人阵营" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_self_target_rejected(self, manager: WolfVoteManager) -> None:
        """狼人不能刀自己。"""
        action = make_action(actor_id="player_1", target_id="player_1")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, STANDARD_ROLES, GamePhase.NIGHT_WOLF_ACT)
        assert "不能刀自己" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_nonexistent_target_rejected(self, manager: WolfVoteManager) -> None:
        """不存在的刀人目标被拒绝。"""
        action = make_action(actor_id="player_1", target_id="player_99")
        from ai_werewolf_core.core.engine.exceptions import ActionValidationError

        with pytest.raises(ActionValidationError) as exc_info:
            await manager.submit_vote(action, STANDARD_ROLES, GamePhase.NIGHT_WOLF_ACT)
        assert "不存在于当前对局中" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_close_vote_round_rejected(self, manager: WolfVoteManager) -> None:
        """投票回合关闭后拒绝新投票（模拟 Lua 返回 CLOSED）。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        action = make_action(actor_id="player_1", target_id="player_7")

        with patch.object(
            LuaScriptManager, "evalsha", AsyncMock(return_value=["CLOSED", "player_1", ""])
        ):
            from ai_werewolf_core.core.engine.exceptions import ActionValidationError

            with pytest.raises(ActionValidationError) as exc_info:
                await manager.submit_vote(action, STANDARD_ROLES, GamePhase.NIGHT_WOLF_ACT)
            assert "已关闭" in str(exc_info.value)


class TestIsVoteComplete:
    """is_vote_complete 检测逻辑测试。"""

    @pytest.mark.asyncio
    async def test_no_alive_wolves_returns_false(
        self, manager: WolfVoteManager
    ) -> None:
        """没有存活狼人时返回 False。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_7", Role.VILLAGER),
        )
        roles["player_1"].die()  # 唯一的狼人死亡

        with patch.object(manager, "_redis_hgetall", AsyncMock(return_value={})):
            result = await manager.is_vote_complete(roles)
            assert result is False

    @pytest.mark.asyncio
    async def test_all_wolves_voted_returns_true(
        self, manager: WolfVoteManager
    ) -> None:
        """所有存活狼人已投票返回 True。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_2", Role.WEREWOLF),
            ("player_3", Role.WEREWOLF),
            ("player_7", Role.VILLAGER),
        )

        # 模拟 3 只狼人都已投票
        with patch.object(
            manager,
            "_redis_hgetall",
            AsyncMock(
                return_value={
                    "player_1": "player_7",
                    "player_2": "player_7",
                    "player_3": "player_8",
                    "meta:status": "OPEN",
                }
            ),
        ):
            result = await manager.is_vote_complete(roles)
            assert result is True

    @pytest.mark.asyncio
    async def test_partial_votes_returns_false(
        self, manager: WolfVoteManager
    ) -> None:
        """部分狼人已投票返回 False。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_2", Role.WEREWOLF),
            ("player_3", Role.WEREWOLF),
            ("player_7", Role.VILLAGER),
        )

        # 只有 2 只狼人投票
        with patch.object(
            manager,
            "_redis_hgetall",
            AsyncMock(
                return_value={
                    "player_1": "player_7",
                    "player_2": "player_8",
                    "meta:status": "OPEN",
                }
            ),
        ):
            result = await manager.is_vote_complete(roles)
            assert result is False

    @pytest.mark.asyncio
    async def test_dead_wolf_not_counted(self, manager: WolfVoteManager) -> None:
        """死亡的狼人不计入投票要求。"""
        roles = make_roles(
            ("player_1", Role.WEREWOLF),
            ("player_2", Role.WEREWOLF),  # 死亡，不要求投票
            ("player_7", Role.VILLAGER),
        )
        roles["player_2"].die()

        # 只有 player_1 投票
        with patch.object(
            manager,
            "_redis_hgetall",
            AsyncMock(
                return_value={
                    "player_1": "player_7",
                    "meta:status": "OPEN",
                }
            ),
        ):
            result = await manager.is_vote_complete(roles)
            assert result is True


class TestResolveVoteLogic:
    """resolve_vote 结算逻辑测试（mock Lua 脚本）。"""

    @pytest.mark.asyncio
    async def test_unique_target_wins(
        self, manager: WolfVoteManager, event_bus: EventBus
    ) -> None:
        """唯一最高票玩家成为刀人目标。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        # player_1 和 player_2 投 player_7，player_3 投 player_8
        mock_vote_details = {
            "player_1": "player_7",
            "player_2": "player_7",
            "player_3": "player_8",
        }

        with patch.object(
            LuaScriptManager,
            "evalsha",
            AsyncMock(
                return_value=[
                    "OK",
                    '{"player_7": 2, "player_8": 1}',
                    '{"player_1": "player_7", "player_2": "player_7", "player_3": "player_8"}',
                ]
            ),
        ):
            with patch.object(manager, "_redis_hset_meta", AsyncMock()):
                with patch.object(manager, "_execute_wolf_kill", AsyncMock()) as mock_exec:
                    result = await manager.resolve_vote(STANDARD_ROLES, 1)

        assert result.is_tie is False
        assert result.wolf_target == "player_7"
        assert result.total_voters == 3
        assert result.vote_count["player_7"] == 2
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_tie_no_target(self, manager: WolfVoteManager) -> None:
        """平票时无人被刀。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        # 三只狼人分别投三个不同目标，每人一票
        mock_vote_details = {
            "player_1": "player_7",
            "player_2": "player_8",
            "player_3": "player_9",
        }

        with patch.object(
            LuaScriptManager,
            "evalsha",
            AsyncMock(
                return_value=[
                    "OK",
                    '{"player_7": 1, "player_8": 1, "player_9": 1}',
                    '{"player_1": "player_7", "player_2": "player_8", "player_3": "player_9"}',
                ]
            ),
        ):
            with patch.object(manager, "_redis_hset_meta", AsyncMock()):
                result = await manager.resolve_vote(STANDARD_ROLES, 1)

        assert result.is_tie is True
        assert result.wolf_target is None

    @pytest.mark.asyncio
    async def test_all_abstain_no_target(
        self, manager: WolfVoteManager
    ) -> None:
        """全部弃权时无人被刀。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        mock_vote_details = {
            "player_1": "",
            "player_2": "",
            "player_3": "",
        }

        with patch.object(
            LuaScriptManager,
            "evalsha",
            AsyncMock(
                return_value=[
                    "OK",
                    '{}',
                    '{"player_1": "", "player_2": "", "player_3": ""}',
                ]
            ),
        ):
            with patch.object(manager, "_redis_hset_meta", AsyncMock()):
                result = await manager.resolve_vote(STANDARD_ROLES, 1)

        assert result.is_tie is False
        assert result.wolf_target is None
        assert result.total_voters == 3

    @pytest.mark.asyncio
    async def test_one_wolf_abstains(
        self, manager: WolfVoteManager
    ) -> None:
        """部分狼人弃权时，其他狼人的投票仍然有效。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        mock_vote_details = {
            "player_1": "player_7",
            "player_2": "",
            "player_3": "player_7",
        }

        with patch.object(
            LuaScriptManager,
            "evalsha",
            AsyncMock(
                return_value=[
                    "OK",
                    '{"player_7": 2}',
                    '{"player_1": "player_7", "player_2": "", "player_3": "player_7"}',
                ]
            ),
        ):
            with patch.object(manager, "_redis_hset_meta", AsyncMock()):
                with patch.object(manager, "_execute_wolf_kill", AsyncMock()) as mock_exec:
                    result = await manager.resolve_vote(STANDARD_ROLES, 1)

        assert result.is_tie is False
        assert result.wolf_target == "player_7"
        assert result.total_voters == 3
        mock_exec.assert_called_once_with(
            "player_7", STANDARD_ROLES, {"player_7": 2}, 1
        )

    @pytest.mark.asyncio
    async def test_already_settled_returns_previous_result(
        self, manager: WolfVoteManager
    ) -> None:
        """Lua 返回 ALREADY_SETTLED 时从 Redis 拉取已有数据。"""
        from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

        with patch.object(
            LuaScriptManager,
            "evalsha",
            AsyncMock(return_value=["ALREADY_SETTLED", None, None]),
        ):
            with patch.object(
                manager,
                "_extract_votes_from_redis",
                AsyncMock(
                    return_value={
                        "player_1": "player_7",
                        "player_2": "player_7",
                        "player_3": "player_8",
                    }
                ),
            ):
                result = await manager.resolve_vote(STANDARD_ROLES, 1)

        assert result.wolf_target == "player_7"
        assert result.is_tie is False


class TestAuditTimestamps:
    """审计时间戳测试。"""

    @pytest.mark.asyncio
    async def test_audit_timestamps_structure(
        self, manager: WolfVoteManager
    ) -> None:
        """审计时间戳返回正确结构。"""
        with patch.object(
            manager,
            "_redis_hgetall",
            AsyncMock(
                return_value={
                    "audit:phase_entered_at": "2026-05-17T14:00:00+08:00",
                    "audit:wolf_dispatched_at": "2026-05-17T14:00:01+08:00",
                    "meta:opened_at": "2026-05-17T14:00:00+08:00",
                    "meta:vote_start_at": "2026-05-17T14:00:02+08:00",
                    "meta:vote_end_at": "2026-05-17T14:00:20+08:00",
                    "meta:settled_at": "2026-05-17T14:00:21+08:00",
                }
            ),
        ):
            audit = await manager.get_audit_timestamps()

        assert "audit:phase_entered_at" in audit
        assert "audit:wolf_dispatched_at" in audit
        assert "meta:opened_at" in audit
        assert "meta:vote_start_at" in audit
        assert "meta:vote_end_at" in audit
        assert "meta:settled_at" in audit
        assert audit["meta:opened_at"] == "2026-05-17T14:00:00+08:00"
        assert audit["meta:vote_start_at"] == "2026-05-17T14:00:02+08:00"
        assert audit["meta:settled_at"] == "2026-05-17T14:00:21+08:00"
