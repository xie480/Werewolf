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

    async def agenerate(self, request: AdapterRequest) -> AdapterResponse:
        """直接转发完整 Prompt 并返回结构化响应"""
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.config.get("model_name", "gpt-4-turbo"),
                    messages=[{"role": "user", "content": request.full_prompt}],
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    response_format={"type": "json_object"}
                ),
                timeout=self.config.get("timeout", 15.0)
            )
            
            raw_text = response.choices[0].message.content
            
            try:
                # 解析 JSON
                parsed_dict = json.loads(raw_text)
                # 校验
                validated_data = request.response_model(**parsed_dict)
                success = True
                err_msg = None
            except (JSONDecodeError, ValidationError) as e:
                validated_data = None
                success = False
                err_msg = str(e)
                
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
                is_success=success,
                error_message=err_msg,
                retry_count=0,
                usage=usage_dict
            )
            
        except Exception as e:
            logger.error(
                "llm_api_error",
                game_id=request.game_id,
                agent_id=request.agent_id,
                error=str(e)
            )
            raise e
                
    async def close(self):
        await self.client.close()
