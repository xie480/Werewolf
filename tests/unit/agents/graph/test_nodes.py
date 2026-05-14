import pytest
from unittest.mock import patch, MagicMock, AsyncMock
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
@patch("ai_werewolf_core.agents.memory.public.PublicMemoryManager")
@patch("ai_werewolf_core.agents.memory.private.PrivateMemoryManager")
async def test_memory_node(mock_private_mgr_class, mock_public_mgr_class):
    mock_public_mgr = mock_public_mgr_class.return_value
    mock_public_mgr.get_memory_context = AsyncMock(return_value={
        "compressed_memories": {},
        "recent_memories": []
    })
    
    mock_private_mgr = mock_private_mgr_class.return_value
    from ai_werewolf_core.schemas.models import PrivateState
    from ai_werewolf_core.schemas.enums import Role, Faction
    mock_private_mgr.get_private_state = AsyncMock(return_value=PrivateState(
        role=Role.VILLAGER,
        faction=Faction.VILLAGER,
        teammates=[],
        skill_status={}
    ))
    mock_private_mgr.get_private_round_data = AsyncMock(return_value={})
    mock_private_mgr.get_last_suspect_list = AsyncMock(return_value={})

    state = create_initial_state("game_1", "player_1", GamePhase.DAY_DISCUSSION, 1)
    result = await memory_node(state)
    
    assert "memory_snapshot" in result
    snapshot = result["memory_snapshot"]
    assert snapshot["game_id"] == "game_1"
    assert snapshot["agent_id"] == "player_1"

@pytest.mark.asyncio
@patch("ai_werewolf_core.agents.adapter.factory.AdapterFactory")
@patch("ai_werewolf_core.agents.memory.private.PrivateMemoryManager")
@patch("ai_werewolf_core.core.engine.player_manager.PlayerStatusManager")
async def test_reasoning_node(mock_player_mgr_class, mock_private_mgr_class, mock_adapter_factory):
    mock_player_mgr = mock_player_mgr_class.return_value
    mock_player_mgr.get_player_info = AsyncMock(return_value={"model_id": "test_model"})

    mock_adapter = MagicMock()
    mock_adapter_factory.get_adapter.return_value = mock_adapter
    
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.raw_content = "test"
    
    mock_parsed_data = MagicMock()
    mock_parsed_data.action_type = ActionType.PASS.value
    mock_parsed_data.action_target = None
    mock_parsed_data.internal_monologue = "test reasoning"
    mock_parsed_data.confidence = 1.0
    mock_parsed_data.speech_content = None
    mock_parsed_data.suspect_list = {}
    
    mock_response.parsed_data = mock_parsed_data
    mock_adapter.agenerate = AsyncMock(return_value=mock_response)
    
    mock_private_mgr = mock_private_mgr_class.return_value
    mock_private_mgr.save_reasoning = AsyncMock()
    mock_private_mgr.save_suspect_list = AsyncMock()

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
