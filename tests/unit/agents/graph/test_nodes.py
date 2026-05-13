import pytest
from unittest.mock import patch, MagicMock
from ai_werewolf_core.schemas.enums import GamePhase, ActionType
from ai_werewolf_core.agents.graph.state import create_initial_state
from ai_werewolf_core.agents.graph.nodes import (
    memory_node,
    reasoning_node,
    validation_node,
    fallback_node,
    generate_safe_default_action,
)
from ai_werewolf_core.core.action.validator import ValidationResult

@pytest.mark.asyncio
async def test_memory_node():
    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    result = await memory_node(state)
    
    assert "memory_snapshot" in result
    snapshot = result["memory_snapshot"]
    assert snapshot["game_id"] == "game_1"
    assert snapshot["player_id"] == "player_1"
    assert snapshot["phase"] == GamePhase.DAY_DISCUSSION

@pytest.mark.asyncio
async def test_reasoning_node():
    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    result = await reasoning_node(state)
    
    assert result["is_valid"] is True
    assert "proposed_action" in result
    action = result["proposed_action"]
    assert action["action_type"] == ActionType.PASS.value
    assert action["actor_id"] == "player_1"
    assert action["phase"] == GamePhase.DAY_DISCUSSION.value
    assert action["round"] == 1

@pytest.mark.asyncio
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
async def test_validation_node_success(mock_validate_basic):
    mock_validate_basic.return_value = ValidationResult.passed()
    
    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    state["proposed_action"] = {
        "action_type": ActionType.SPEAK.value,
        "actor_id": "player_1",
        "target_id": None,
        "phase": GamePhase.DAY_DISCUSSION.value,
        "round": 1,
        "reason": "test",
        "confidence": 1.0,
    }
    
    result = await validation_node(state)
    
    assert result["is_valid"] is True
    assert len(result["validation_errors"]) == 0
    assert result["retry_count"] == 0

@pytest.mark.asyncio
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
async def test_validation_node_business_failure(mock_validate_basic):
    mock_validate_basic.return_value = ValidationResult.rejected("Target dead", "business")
    
    state = create_initial_state("game_1", "player_1", GamePhase.NIGHT_WOLF_ACT, 1)
    state["proposed_action"] = {
        "action_type": ActionType.WOLF_KILL.value,
        "actor_id": "player_1",
        "target_id": "player_2",
        "phase": GamePhase.NIGHT_WOLF_ACT.value,
        "round": 1,
        "reason": "test",
        "confidence": 1.0,
    }
    
    result = await validation_node(state)
    
    assert result["is_valid"] is False
    assert len(result["validation_errors"]) == 1
    assert "Target dead" in result["validation_errors"][0]
    assert result["retry_count"] == 1

@pytest.mark.asyncio
async def test_validation_node_schema_failure():
    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    state["proposed_action"] = {
        "action_type": "INVALID_TYPE",
        "actor_id": "player_1",
    }
    
    result = await validation_node(state)
    
    assert result["is_valid"] is False
    assert len(result["validation_errors"]) > 0
    assert "Schema validation error" in result["validation_errors"][0]
    assert result["retry_count"] == 1

@pytest.mark.asyncio
async def test_fallback_node():
    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    state["validation_errors"] = ["error1", "error2"]
    
    result = await fallback_node(state)
    
    assert result["is_valid"] is True
    assert "proposed_action" in result
    action = result["proposed_action"]
    assert action["action_type"] == ActionType.SPEAK.value
    assert action["actor_id"] == "player_1"
    assert action["phase"] == GamePhase.DAY_DISCUSSION.value
    assert action["round"] == 1
    assert len(result["validation_errors"]) == 0

def test_generate_safe_default_action():
    # Test speech phase
    action = generate_safe_default_action(GamePhase.DAY_DISCUSSION, 1, "player_1")
    assert action["action_type"] == ActionType.SPEAK.value
    
    # Test vote phase
    action = generate_safe_default_action(GamePhase.DAY_VOTE, 1, "player_1")
    assert action["action_type"] == ActionType.VOTE.value
    
    # Test night phase
    action = generate_safe_default_action(GamePhase.NIGHT_WOLF_ACT, 1, "player_1")
    assert action["action_type"] == ActionType.PASS.value
