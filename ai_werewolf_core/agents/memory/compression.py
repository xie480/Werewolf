import json
from typing import List
from ai_werewolf_core.schemas.models import PublicEventLog, AdapterRequest, CompressionResponse
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.schemas.enums import GamePhase
from ai_werewolf_core.agents.adapter.factory import AdapterFactory

logger = get_logger(__name__)

class MemoryCompressionService:
    """记忆压缩服务"""
    
    @classmethod
    async def compress(cls, events: List[PublicEventLog], game_id: str, round_num: int) -> CompressionResponse:
        """
        压缩单轮事件列表并存储到 Redis Hash
        """
        if not events:
            return CompressionResponse(speech_summary="", key_facts="")
            
        # 0. 检查是否已压缩（防止并发重复压缩）
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_memory_summary(game_id)
            exists = await redis.hexists(key, str(round_num))
            if exists:
                logger.info(f"Round {round_num} already compressed, skipping LLM call.")
                raw_data = await redis.hget(key, str(round_num))
                data = json.loads(raw_data)
                return CompressionResponse(**data)
        except Exception as e:
            logger.warning("check_compressed_summary_failed", error=str(e))

        # 1. 组装 full_prompt
        events_text = "\n".join([e.description for e in events])
        from ai_werewolf_core.agents.prompts.builder import PromptBuilder
        builder = PromptBuilder()
        template = builder.env.get_template("compression.j2")
        full_prompt = template.render(round_num=round_num, events_text=events_text)
        
        # 2. 调用适配器
        from ai_werewolf_core.config import settings
        
        try:
            # 使用 AdapterFactory 获取已注册的压缩模型适配器
            adapter = AdapterFactory.get_adapter("compression_model")
            request = AdapterRequest(
                model_id="compression_model",
                agent_id="system_compressor",
                game_id=game_id,
                phase=GamePhase.INIT, # 仅作为占位
                full_prompt=full_prompt,
                temperature=0.3,
                max_tokens=1024,
                response_model=CompressionResponse
            )
            
            response = await adapter.agenerate(request)
            
            if response.is_success and response.parsed_data:
                result = response.parsed_data
            else:
                logger.warning("compression_llm_failed", error=response.error_message)
                result = CompressionResponse(speech_summary="压缩失败", key_facts=events_text) # 回退
                
        except Exception as e:
            logger.error("compression_exception", error=str(e), exc_info=True)
            result = CompressionResponse(speech_summary="压缩异常", key_facts=events_text) # 回退
            
        # 3. 存入 Redis Hash
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_memory_summary(game_id)
            # 存入 Hash，Field 为 round_num
            await redis.hset(key, str(round_num), result.model_dump_json())
            # 设置 7 天过期
            await redis.expire(key, 7 * 24 * 3600)
        except Exception as e:
            logger.error("save_compressed_summary_failed", error=str(e), exc_info=True)
            
        return result
