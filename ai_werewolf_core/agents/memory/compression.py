import json
from typing import List
from ai_werewolf_core.schemas.models import PublicEventLog, AdapterRequest, CompressionResponse
from ai_werewolf_core.agents.model.registry import ModelRegistry
from ai_werewolf_core.agents.adapter.factory import AdapterFactory
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.schemas.enums import GamePhase

logger = get_logger(__name__)

class MemoryCompressionService:
    """记忆压缩服务"""
    
    @classmethod
    async def compress(cls, events: List[PublicEventLog], game_id: str, model_id: str = "default-openai") -> str:
        """
        压缩事件列表并存储到 Redis
        """
        if not events:
            return ""
            
        # 1. 组装 full_prompt
        events_text = "\n".join([e.description for e in events])
        full_prompt = f"""请作为狼人杀高级复盘专家，对以下历史公共事件进行高度浓缩的逻辑摘要。
要求：
1. 保留关键帧（如死亡播报、投票结果、阶段切换）。
2. 对玩家发言进行逻辑提炼（如“1号跳预言家查杀2号”，“3号跟票”），丢弃冗余的语气词。
3. 按照时间线顺序输出。
4. 必须返回 JSON 格式，包含 `summary` 字段。

历史事件：
{events_text}
"""
        
        # 2. 调用适配器
        try:
            adapter = AdapterFactory.get_adapter(model_id)
            request = AdapterRequest(
                model_id=model_id,
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
                summary = response.parsed_data.summary
            else:
                logger.warning("compression_llm_failed", error=response.error_message)
                summary = events_text # 回退为简单拼接
                
        except Exception as e:
            logger.error("compression_exception", error=str(e), exc_info=True)
            summary = events_text # 回退为简单拼接
            
        # 3. 存入 Redis
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_memory_summary(game_id)
            # 设置 7 天过期
            await redis.setex(key, 7 * 24 * 3600, summary)
        except Exception as e:
            logger.error("save_compressed_summary_failed", error=str(e), exc_info=True)
            
        return summary
