import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from ai_werewolf_core.schemas.models import PublicEventLog, AdapterResponse, CompressionResponse
from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.agents.memory.compression import MemoryCompressionService
from ai_werewolf_core.api.routes.memory_compression import CompressRequest

@pytest.fixture
def mock_events():
    return [
        PublicEventLog(seq_num=1, phase=GamePhase.DAY_DISCUSSION, description="玩家1发言：我是预言家"),
        PublicEventLog(seq_num=2, phase=GamePhase.DAY_DISCUSSION, description="玩家2发言：我是平民"),
    ]

@pytest.mark.asyncio
async def test_compress_empty_events():
    result = await MemoryCompressionService.compress([], "game_123", round_num=1)
    assert result.speech_summary == ""
    assert result.key_facts == ""

@pytest.mark.asyncio
@patch("ai_werewolf_core.agents.memory.compression.AdapterFactory")
@patch("ai_werewolf_core.agents.memory.compression.RedisClientManager")
async def test_compress_success(mock_redis_mgr, mock_factory, mock_events):
    # Mock Adapter
    mock_adapter = AsyncMock()
    mock_response = AdapterResponse(
        raw_content='{"speech_summary": "1号跳预言家，2号平民", "key_facts": "无"}',
        parsed_data=CompressionResponse(speech_summary="1号跳预言家，2号平民", key_facts="无"),
        is_success=True
    )
    mock_adapter.agenerate.return_value = mock_response
    mock_factory.get_adapter.return_value = mock_adapter
    
    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis_mgr.get_client = AsyncMock(return_value=mock_redis)
    
    result = await MemoryCompressionService.compress(mock_events, "game_123", round_num=1)
    
    assert result.speech_summary == "1号跳预言家，2号平民"
    assert result.key_facts == "无"
    mock_adapter.agenerate.assert_called_once()
    mock_redis.hset.assert_called_once()
    mock_redis.expire.assert_called_once()

@pytest.mark.asyncio
@patch("ai_werewolf_core.agents.memory.compression.AdapterFactory")
@patch("ai_werewolf_core.agents.memory.compression.RedisClientManager")
async def test_compress_fallback(mock_redis_mgr, mock_factory, mock_events):
    # Mock Adapter failure
    mock_adapter = AsyncMock()
    mock_response = AdapterResponse(
        raw_content='',
        is_success=False,
        error_message="API Error"
    )
    mock_adapter.agenerate.return_value = mock_response
    mock_factory.get_adapter.return_value = mock_adapter
    
    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis_mgr.get_client = AsyncMock(return_value=mock_redis)
    
    result = await MemoryCompressionService.compress(mock_events, "game_123", round_num=1)
    
    # Should fallback to simple join
    assert "玩家1发言：我是预言家" in result.key_facts
    assert "玩家2发言：我是平民" in result.key_facts
    mock_redis.hset.assert_called_once()
    mock_redis.expire.assert_called_once()
