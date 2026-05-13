import pytest
from unittest.mock import patch, MagicMock
from ai_werewolf_core.schemas.enums import GamePhase, ActionType
from ai_werewolf_core.agents.graph.state import create_initial_state
from ai_werewolf_core.agents.graph.graph import (
    build_agent_graph,
    route_after_validation,
    run_agent_workflow,
    NODE_REASONING,
    NODE_FALLBACK
)
from ai_werewolf_core.core.action.validator import ValidationResult

def test_route_after_validation():
    # Test valid case
    state = {"is_valid": True, "retry_count": 0, "max_retries": 3}
    assert route_after_validation(state) == "__end__"
    
    # Test retry case
    state = {"is_valid": False, "retry_count": 1, "max_retries": 3}
    assert route_after_validation(state) == NODE_REASONING
    
    # Test fallback case
    state = {"is_valid": False, "retry_count": 3, "max_retries": 3}
    assert route_after_validation(state) == NODE_FALLBACK

@pytest.mark.asyncio
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
async def test_run_agent_workflow_success(mock_validate_basic):
    mock_validate_basic.return_value = ValidationResult.passed()
    
    result = await run_agent_workflow(
        game_id="game_1",
        player_id="player_1",
        current_phase=GamePhase.DAY_DISCUSSION,
        current_round=1,
        max_retries=3
    )
    
    assert result["is_valid"] is True
    assert result["retry_count"] == 0
    assert "proposed_action" in result
    assert result["proposed_action"]["action_type"] == ActionType.PASS.value

@pytest.mark.asyncio
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
async def test_run_agent_workflow_fallback(mock_validate_basic):
    # Always fail validation to trigger fallback
    mock_validate_basic.return_value = ValidationResult.rejected("Always fail", "business")
    
    result = await run_agent_workflow(
        game_id="game_1",
        player_id="player_1",
        current_phase=GamePhase.DAY_DISCUSSION,
        current_round=1,
        max_retries=2
    )
    
    # The final state should be valid because fallback node generates a valid default action
    assert result["is_valid"] is True
    assert result["retry_count"] == 2
    assert "proposed_action" in result
    assert result["proposed_action"]["action_type"] == ActionType.SPEAK.value
    assert result["proposed_action"]["reason"] == "系统强制接管：重试次数耗尽，执行默认动作。"
