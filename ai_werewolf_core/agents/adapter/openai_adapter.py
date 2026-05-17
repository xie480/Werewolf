import asyncio
import json
from json.decoder import JSONDecodeError
from typing import Any, Dict

from openai import AsyncOpenAI
from pydantic import ValidationError
import structlog

from .base import BaseModelAdapter
from ai_werewolf_core.schemas.models import AdapterRequest, AdapterResponse

logger = structlog.get_logger(__name__)

class OpenAIAdapter(BaseModelAdapter):
    def _initialize_client(self) -> AsyncOpenAI:
        """初始化 OpenAI 客户端"""
        return AsyncOpenAI(
            api_key=self.config.get("api_key"),
            base_url=self.config.get("base_url")
        )

    async def agenerate(self, request: AdapterRequest, max_retries: int = 3) -> AdapterResponse:
        """直接转发完整 Prompt 并返回结构化响应，包含重试机制"""
        messages = [{"role": "user", "content": request.full_prompt}]
        
        # [DIAGNOSIS LOG] 验证 messages 结构和内容
        has_json = "json" in messages[0]["content"].lower()
        logger.info(
            "llm_api_request_diagnosis",
            has_system_msg=any(m["role"] == "system" for m in messages),
            user_msg_contains_json=has_json
        )
        
        if not has_json:
            logger.warning(
                "missing_json_in_prompt",
                game_id=request.game_id,
                agent_id=request.agent_id,
                prompt_preview=messages[0]["content"][:500] + "..." if len(messages[0]["content"]) > 500 else messages[0]["content"]
            )
            
        for attempt in range(max_retries):
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.config.get("model_name", "gpt-4-turbo"),
                        messages=messages,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens
                    ),
                    timeout=self.config.get("timeout", 60.0)
                )
                
                raw_text = response.choices[0].message.content
                
                try:
                    # 解析 JSON
                    parsed_dict = json.loads(raw_text)
                    # 校验
                    validated_data = request.response_model(**parsed_dict)
                    
                    usage_dict = {}
                    if response.usage:
                        usage_dict = response.usage.model_dump()

                    logger.info(
                        "llm_generate_success",
                        game_id=request.game_id,
                        agent_id=request.agent_id,
                        phase=request.phase.value,
                        model=self.config.get("model_name"),
                        usage=usage_dict
                    )

                    return AdapterResponse(
                        raw_content=raw_text,
                        parsed_data=validated_data,
                        is_success=True,
                        error_message=None,
                        retry_count=attempt,
                        usage=usage_dict
                    )
                except (JSONDecodeError, ValidationError) as e:
                    logger.warning(
                        "llm_output_parse_failed",
                        game_id=request.game_id,
                        agent_id=request.agent_id,
                        attempt=attempt,
                        error=str(e),
                        raw_text=raw_text
                    )
                    
                    if attempt == max_retries - 1:
                        return AdapterResponse(
                            raw_content=raw_text,
                            parsed_data=None,
                            is_success=False,
                            error_message=f"解析失败: {str(e)}",
                            retry_count=attempt,
                            usage={}
                        )
                        
                    from ai_werewolf_core.agents.prompts.builder import PromptBuilder
                    builder = PromptBuilder()
                    template = builder.env.get_template("retry.j2")
                    retry_prompt = template.render(error=str(e))
                    messages.append({"role": "assistant", "content": raw_text})
                    messages.append({"role": "user", "content": retry_prompt})
                    
                    await asyncio.sleep(2 ** attempt)
                    
            except asyncio.TimeoutError as e:
                logger.error(
                    "llm_api_timeout",
                    game_id=request.game_id,
                    agent_id=request.agent_id,
                    attempt=attempt
                )
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(
                    "llm_api_error",
                    game_id=request.game_id,
                    agent_id=request.agent_id,
                    error=str(e),
                    attempt=attempt
                )
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(2 ** attempt)
                
    async def close(self):
        await self.client.close()
