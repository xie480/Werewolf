import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel, Field

from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.schemas.models import AdapterRequest, AdapterResponse
from ai_werewolf_core.agents.adapter.openai_adapter import OpenAIAdapter

# 测试用的 Pydantic 模型
class DummyResponseModel(BaseModel):
    action: str = Field(..., description="要执行的动作")
    target: str = Field(..., description="动作的目标")

@pytest.fixture
def adapter_config():
    return {
        "api_key": "test_key",
        "base_url": "https://api.test.com/v1",
        "model_name": "test-model",
        "timeout": 1.0
    }

@pytest.fixture
def adapter_request():
    return AdapterRequest(
        model_id="default-openai",
        agent_id="agent_1",
        game_id="game_1",
        phase=GamePhase.DAY_DISCUSSION,
        full_prompt="You are a villager. What do you do?",
        response_model=DummyResponseModel
    )

@pytest.mark.asyncio
async def test_openai_adapter_success(adapter_config, adapter_request):
    adapter = OpenAIAdapter(adapter_config)
    
    # 模拟 AsyncOpenAI 客户端
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({"action": "vote", "target": "player_2"})
    mock_response.usage.model_dump.return_value = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    mock_client.chat.completions.create.return_value = mock_response
    
    adapter.client = mock_client
    
    response = await adapter.agenerate(adapter_request)
    
    assert response.is_success is True
    assert response.parsed_data is not None
    assert response.parsed_data.action == "vote"
    assert response.parsed_data.target == "player_2"
    assert response.retry_count == 0
    assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    
    # 验证客户端被正确调用
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert len(call_kwargs["messages"]) == 1
    assert call_kwargs["messages"][0]["content"] == "You are a villager. What do you do?"

@pytest.mark.asyncio
async def test_openai_adapter_json_decode_error_retry_success(adapter_config, adapter_request):
    adapter = OpenAIAdapter(adapter_config)
    
    mock_client = AsyncMock()
    
    # 第一次调用返回无效的 JSON，第二次调用返回有效的 JSON
    mock_response_invalid = MagicMock()
    mock_response_invalid.choices = [MagicMock()]
    mock_response_invalid.choices[0].message.content = "This is not JSON"
    mock_response_invalid.usage = None
    
    mock_response_valid = MagicMock()
    mock_response_valid.choices = [MagicMock()]
    mock_response_valid.choices[0].message.content = json.dumps({"action": "pass", "target": "none"})
    mock_response_valid.usage.model_dump.return_value = {"total_tokens": 20}
    
    mock_client.chat.completions.create.side_effect = [mock_response_invalid, mock_response_valid]
    
    adapter.client = mock_client
    
    # 模拟 asyncio.sleep 以避免在测试期间等待
    with patch("asyncio.sleep", new_callable=AsyncMock):
        response = await adapter.agenerate(adapter_request)
    
    assert response.is_success is True
    assert response.parsed_data.action == "pass"
    assert response.retry_count == 1
    assert mock_client.chat.completions.create.call_count == 2
    
    # 验证第二次调用包含了错误反馈
    second_call_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
    messages = second_call_kwargs["messages"]
    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "This is not JSON"
    assert messages[2]["role"] == "user"
    assert "解析失败" in messages[2]["content"]

@pytest.mark.asyncio
async def test_openai_adapter_validation_error_max_retries(adapter_config, adapter_request):
    adapter = OpenAIAdapter(adapter_config)
    
    mock_client = AsyncMock()
    
    # 总是返回有效的 JSON，但缺少必需的字段（验证错误）
    mock_response_invalid = MagicMock()
    mock_response_invalid.choices = [MagicMock()]
    mock_response_invalid.choices[0].message.content = json.dumps({"wrong_field": "value"})
    mock_response_invalid.usage = None
    
    mock_client.chat.completions.create.return_value = mock_response_invalid
    
    adapter.client = mock_client
    
    with patch("asyncio.sleep", new_callable=AsyncMock):
        response = await adapter.agenerate(adapter_request, max_retries=2)
    
    assert response.is_success is False
    assert response.parsed_data is None
    assert "解析失败" in response.error_message
    assert response.retry_count == 1
    assert mock_client.chat.completions.create.call_count == 2

@pytest.mark.asyncio
async def test_openai_adapter_timeout(adapter_config, adapter_request):
    adapter = OpenAIAdapter(adapter_config)
    
    mock_client = AsyncMock()
    
    # 模拟超时
    mock_client.chat.completions.create.side_effect = asyncio.TimeoutError("Timeout")
    adapter.client = mock_client
    
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(asyncio.TimeoutError):
            await adapter.agenerate(adapter_request, max_retries=1)

@pytest.mark.asyncio
async def test_openai_adapter_api_error(adapter_config, adapter_request):
    adapter = OpenAIAdapter(adapter_config)
    
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = Exception("API Error")
    
    adapter.client = mock_client
    
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(Exception, match="API Error"):
            await adapter.agenerate(adapter_request, max_retries=2)
    
    assert mock_client.chat.completions.create.call_count == 2
