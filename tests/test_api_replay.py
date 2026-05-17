import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

from ai_werewolf_core.main import app
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, Visibility, Role
from ai_werewolf_core.utils.time_utils import now_tz

client = TestClient(app)

@pytest.fixture
def mock_player_manager():
    with patch("ai_werewolf_core.api.routes.replay.PlayerStatusManager") as mock:
        instance = mock.return_value
        
        # Mock players
        p1 = MagicMock()
        p1.player_id = "player_1"
        p1.seat_number = 1
        p1.role = Role.WEREWOLF
        
        p2 = MagicMock()
        p2.player_id = "player_2"
        p2.seat_number = 2
        p2.role = Role.SEER
        
        instance.get_all_players = AsyncMock(return_value=[p1, p2])
        yield instance

@pytest.fixture
def mock_event_bus():
    with patch("ai_werewolf_core.api.routes.replay.event_bus") as mock:
        # Mock events
        events = [
            Event(
                event_id="e1",
                game_id="test_game",
                seq_num=1,
                event_type=EventType.PHASE_TRANSITION_EVENT,
                visibility=Visibility.PUBLIC,
                target_agents=[],
                timestamp=now_tz(),
                payload={"round": 1, "new_phase": "NIGHT_START"}
            ),
            Event(
                event_id="e2",
                game_id="test_game",
                seq_num=2,
                event_type=EventType.SPEECH_EVENT,
                visibility=Visibility.FACTION,
                target_agents=["player_1"],
                timestamp=now_tz(),
                payload={"actor_id": "player_1", "content": "I am wolf", "inner_thought": "Kill player 2"}
            ),
            Event(
                event_id="e3",
                game_id="test_game",
                seq_num=3,
                event_type=EventType.PHASE_TRANSITION_EVENT,
                visibility=Visibility.PUBLIC,
                target_agents=[],
                timestamp=now_tz(),
                payload={"round": 1, "new_phase": "DAY_START"}
            ),
            Event(
                event_id="e4",
                game_id="test_game",
                seq_num=4,
                event_type=EventType.SPEECH_EVENT,
                visibility=Visibility.PUBLIC,
                target_agents=[],
                timestamp=now_tz(),
                payload={"actor_id": "player_2", "content": "I am seer", "inner_thought": "I checked player 1"}
            )
        ]
        
        mock.get_events = AsyncMock(return_value=events)
        yield mock

def test_get_replay_god_mode(mock_player_manager, mock_event_bus):
    response = client.get("/api/games/test_game/replay?perspective=GOD")
    assert response.status_code == 200
    data = response.json()
    
    assert data["game_id"] == "test_game"
    assert data["perspective"] == "GOD"
    
    # Check initial state
    players = data["initial_state"]["players"]
    assert len(players) == 2
    assert players[0]["role"] == "WEREWOLF"
    assert players[1]["role"] == "SEER"
    
    # Check timeline chunking
    timeline = data["timeline"]
    assert len(timeline) == 1
    assert timeline[0]["day_num"] == 1
    
    phases = timeline[0]["phases"]
    assert len(phases) == 2
    assert phases[0]["phase_name"] == "NIGHT_START"
    assert len(phases[0]["events"]) == 2
    
    assert phases[1]["phase_name"] == "DAY_START"
    assert len(phases[1]["events"]) == 2
    
    # Check inner_thought is preserved in GOD mode
    night_events = phases[0]["events"]
    assert "inner_thought" in night_events[1]["payload"]
    
    day_events = phases[1]["events"]
    assert "inner_thought" in day_events[1]["payload"]

def test_get_replay_pov_mode(mock_player_manager, mock_event_bus):
    response = client.get("/api/games/test_game/replay?perspective=POV&agent_id=player_1")
    assert response.status_code == 200
    data = response.json()
    
    assert data["perspective"] == "POV"
    assert data["agent_id"] == "player_1"
    
    # Check initial state masking
    players = data["initial_state"]["players"]
    assert players[0]["agent_id"] == "player_1"
    assert players[0]["role"] == "WEREWOLF"
    
    assert players[1]["agent_id"] == "player_2"
    assert players[1]["role"] == "UNKNOWN"  # Masked
    
    # Check inner_thought filtering
    timeline = data["timeline"]
    phases = timeline[0]["phases"]
    
    # player_1's own inner_thought should be preserved
    night_events = phases[0]["events"]
    assert "inner_thought" in night_events[1]["payload"]
    
    # player_2's inner_thought should be filtered out for player_1
    day_events = phases[1]["events"]
    assert "inner_thought" not in day_events[1]["payload"]

def test_get_replay_invalid_params():
    # Missing agent_id for POV
    response = client.get("/api/games/test_game/replay?perspective=POV")
    assert response.status_code == 400
    
    # Invalid perspective
    response = client.get("/api/games/test_game/replay?perspective=INVALID")
    assert response.status_code == 400
