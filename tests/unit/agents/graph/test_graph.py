import pytest
from unittest.mock import patch, MagicMock, AsyncMock
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
@patch("ai_werewolf_core.agents.adapter.factory.AdapterFactory")
@patch("ai_werewolf_core.agents.memory.public.PublicMemoryManager")
@patch("ai_werewolf_core.agents.memory.private.PrivateMemoryManager")
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
@patch("ai_werewolf_core.core.engine.player_manager.PlayerStatusManager")
async def test_run_agent_workflow_success(mock_player_mgr_class, mock_validate_basic, mock_private_mgr_class, mock_public_mgr_class, mock_adapter_factory):
    mock_player_mgr = mock_player_mgr_class.return_value
    mock_player_mgr.get_player_info = AsyncMock(return_value={"model_id": "test_model"})

    mock_validate_basic.return_value = ValidationResult.passed()
    
    mock_public_mgr = mock_public_mgr_class.return_value
    mock_public_mgr.fetch_round_memories = AsyncMock(return_value=[])
    
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
    mock_private_mgr.save_reasoning = AsyncMock()
    mock_private_mgr.save_suspect_list = AsyncMock()

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
@patch("ai_werewolf_core.agents.adapter.factory.AdapterFactory")
@patch("ai_werewolf_core.agents.memory.public.PublicMemoryManager")
@patch("ai_werewolf_core.agents.memory.private.PrivateMemoryManager")
@patch("ai_werewolf_core.core.action.validator.ActionValidator.validate_basic")
@patch("ai_werewolf_core.core.engine.player_manager.PlayerStatusManager")
async def test_run_agent_workflow_fallback(mock_player_mgr_class, mock_validate_basic, mock_private_mgr_class, mock_public_mgr_class, mock_adapter_factory):
    mock_player_mgr = mock_player_mgr_class.return_value
    mock_player_mgr.get_player_info = AsyncMock(return_value={"model_id": "test_model"})

    # Always fail validation to trigger fallback
    mock_validate_basic.return_value = ValidationResult.rejected("Always fail", "business")
    
    mock_public_mgr = mock_public_mgr_class.return_value
    mock_public_mgr.fetch_round_memories = AsyncMock(return_value=[])
    
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
    mock_private_mgr.save_reasoning = AsyncMock()
    mock_private_mgr.save_suspect_list = AsyncMock()

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
