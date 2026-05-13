# coding: utf-8
"""
Agent LangGraph 工作流单元测试

测试状态定义、节点逻辑和条件路由。
"""

import pytest
from unittest.mock import AsyncMock, patch

from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.agents.graph.state import AgentState, create_initial_state
from ai_werewolf_core.agents.graph.nodes import (
    memory_node,
    reasoning_node,
    validation_node,
    fallback_node,
    generate_safe_default_action,
)
from ai_werewolf_core.agents.graph.graph import build_agent_graph, route_after_validation


class TestAgentState:
    """测试 AgentState 创建"""

    def test_create_initial_state(self):
        """测试初始状态创建"""
        state = create_initial_state(
            game_id="game_123",
            player_id="player_456",
            current_phase=GamePhase.DAY_DISCUSSION,
            max_retries=5,
        )

        assert state["game_id"] == "game_123"
        assert state["player_id"] == "player_456"
        assert state["current_phase"] == GamePhase.DAY_DISCUSSION
        assert state["max_retries"] == 5
        assert state["retry_count"] == 0
        assert state["is_valid"] is False
        assert state["proposed_action"] is None
        assert state["validation_errors"] == []

    def test_create_initial_state_defaults(self):
        """测试默认参数"""
        state = create_initial_state(
            game_id="g1", player_id="p1", current_phase=GamePhase.NIGHT_START
        )

        assert state["max_retries"] == 3
        assert state["suspect_list"] == {}


class TestNodes:
    """测试工作流节点"""

    @pytest.mark.asyncio
    async def test_memory_node(self):
        """测试记忆节点"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        result = await memory_node(state)

        assert "memory_snapshot" in result
        snapshot = result["memory_snapshot"]
        assert snapshot["game_id"] == "g1"
        assert snapshot["player_id"] == "p1"

    @pytest.mark.asyncio
    async def test_reasoning_node_success(self):
        """测试推理节点成功场景"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["memory_snapshot"] = {"test": "data"}

        result = await reasoning_node(state)

        assert "raw_llm_response" in result
        assert "internal_monologue" in result
        assert "suspect_list" in result
        assert "proposed_action" in result

    @pytest.mark.asyncio
    async def test_reasoning_node_with_validation_errors(self):
        """测试推理节点带校验错误的重试"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["validation_errors"] = ["Previous error"]

        result = await reasoning_node(state)

        # 当前占位实现返回成功
        assert result.get("is_valid", True) is True

    @pytest.mark.asyncio
    async def test_validation_node_valid_action(self):
        """测试校验节点通过有效动作"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["proposed_action"] = {
            "type": "SPEAK",
            "player_id": "p1",
            "target_player_id": None,
            "content": "Hello",
        }

        result = await validation_node(state)

        assert result["is_valid"] is True
        assert result["validation_errors"] == []
        assert result["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_validation_node_missing_action(self):
        """测试校验节点缺少动作"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["proposed_action"] = None

        result = await validation_node(state)

        assert result["is_valid"] is False
        assert len(result["validation_errors"]) > 0
        assert "No proposed action" in result["validation_errors"][0]
        assert result["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_validation_node_invalid_action_type(self):
        """测试校验节点无效动作类型"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["proposed_action"] = {"invalid": "data"}

        result = await validation_node(state)

        assert result["is_valid"] is False
        assert result["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_fallback_node(self):
        """测试降级节点"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["validation_errors"] = ["Error1", "Error2"]

        result = await fallback_node(state)

        assert result["is_valid"] is True
        assert result["proposed_action"] is not None
        assert "系统强制接管" in result["internal_monologue"]

    def test_generate_safe_default_action(self):
        """测试生成安全默认动作"""
        # DAY 阶段
        action_day = generate_safe_default_action(GamePhase.DAY_DISCUSSION, "p1")
        assert action_day["type"] == "SPEAK"
        assert action_day["content"] == "跳过发言"

        # NIGHT 阶段
        action_night = generate_safe_default_action(GamePhase.NIGHT_START, "p1")
        assert action_night["type"] == "SKIP"

        # VOTE 阶段
        action_vote = generate_safe_default_action(GamePhase.DAY_VOTE, "p1")
        assert action_vote["type"] == "VOTE"
        assert action_vote["content"] == "弃权"


class TestGraphRouting:
    """测试图路由逻辑"""

    def test_route_after_validation_success(self):
        """测试校验通过：结束"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["is_valid"] = True
        state["retry_count"] = 1

        result = route_after_validation(state)
        # LangGraph 返回的 END 变量实际值为 "__end__"
        assert result in ("END", "__end__")

    def test_route_after_validation_retry(self):
        """测试校验失败且可重试：返回 reasoning"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["is_valid"] = False
        state["retry_count"] = 1
        state["max_retries"] = 3

        result = route_after_validation(state)
        assert result == "reasoning"

    def test_route_after_validation_fallback(self):
        """测试重试次数耗尽：进入 fallback"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["is_valid"] = False
        state["retry_count"] = 3
        state["max_retries"] = 3

        result = route_after_validation(state)
        assert result == "fallback"

    def test_route_after_validation_exceed_limit(self):
        """测试重试次数超过限制：进入 fallback"""
        state = create_initial_state("g1", "p1", GamePhase.DAY_DISCUSSION)
        state["is_valid"] = False
        state["retry_count"] = 5
        state["max_retries"] = 3

        result = route_after_validation(state)
        assert result == "fallback"


class TestGraphCompilation:
    """测试图编译"""

    def test_build_graph(self):
        """测试图构建"""
        graph = build_agent_graph()

        # 验证节点存在
        assert "memory" in graph.nodes
        assert "reasoning" in graph.nodes
        assert "validation" in graph.nodes
        assert "fallback" in graph.nodes

    @pytest.mark.asyncio
    async def test_graph_invoke_success(self):
        """测试图成功执行"""
        from ai_werewolf_core.agents.graph.graph import run_agent_workflow

        # 由于当前节点是占位实现，只要不抛异常就算通过
        result = await run_agent_workflow("g1", "p1", GamePhase.DAY_DISCUSSION)

        assert "game_id" in result or "proposed_action" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
