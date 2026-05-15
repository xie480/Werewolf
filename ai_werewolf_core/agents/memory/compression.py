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
            
        # 0. 检查是否已压缩（防止并发重复压缩），使用 HSETNX 占位符保证单次执行
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_memory_summary(game_id)
            # 先尝试写入 PENDING 占位符，只有第一个写入成功的才执行 LLM 压缩
            is_first = await redis.hsetnx(key, str(round_num), "PENDING")
            if not is_first:
                logger.info(f"Round {round_num} already compressed or compressing, skipping LLM call.")
                import asyncio
                for _ in range(15):
                    raw_data = await redis.hget(key, str(round_num))
                    if raw_data and raw_data != b"PENDING" and raw_data != "PENDING":
                        data = json.loads(raw_data)
                        return CompressionResponse(**data)
                    await asyncio.sleep(1)
                # 超时返回占位信息，不阻塞
                return CompressionResponse(speech_summary="正在压缩中", key_facts="")
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

    @classmethod
    async def compress_reasoning(cls, reasoning: List[str], game_id: str, agent_id: str, round_num: int) -> str:
        """
        压缩单轮推理记录并存储到 Redis Hash
        
        :param reasoning: 推理记录列表，包含多条推理文本
        :param game_id: 游戏会话ID，用于标识当前游戏
        :param agent_id: 智能体ID，用于标识执行推理的智能体
        :param round_num: 当前回合数，作为压缩结果的键值
        :return: 压缩后的推理文本字符串
        """
        # 如果没有推理记录则直接返回空字符串
        if not reasoning:
            return ""
            
        # 检查Redis中是否已存在该回合的压缩结果，避免重复调用LLM
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_reasoning(game_id, agent_id)
            # 使用 HSETNX 写入 PENDING 占位符，只有第一个写入成功的才执行 LLM 压缩
            is_first = await redis.hsetnx(key, str(round_num), "PENDING")
            if not is_first:
                logger.info(f"Reasoning for round {round_num} already compressed or compressing, skipping LLM call.")
                import asyncio
                for _ in range(15):
                    raw_data = await redis.hget(key, str(round_num))
                    if raw_data and raw_data != b"PENDING" and raw_data != "PENDING":
                        return raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data
                    await asyncio.sleep(1)
                # 超时返回占位信息，不阻塞
                return "正在压缩中..."
        except Exception as e:
            logger.warning("check_compressed_reasoning_failed", error=str(e))

        # 将推理列表合并为单个文本字符串
        reasoning_text = "\n".join(reasoning)
        from ai_werewolf_core.agents.prompts.builder import PromptBuilder
        builder = PromptBuilder()
        template = builder.env.get_template("compress_reasoning.j2")
        full_prompt = template.render(round_num=round_num, reasoning_text=reasoning_text)
        
        # 使用适配器模式调用压缩模型进行推理压缩
        try:
            adapter = AdapterFactory.get_adapter("compression_model")
            from pydantic import BaseModel
            class StringResponse(BaseModel):
                content: str
                
            request = AdapterRequest(
                model_id="compression_model",
                agent_id="system_compressor",
                game_id=game_id,
                phase=GamePhase.INIT,
                full_prompt=full_prompt,
                temperature=0.3,
                max_tokens=512,
                response_model=StringResponse
            )
            
            response = await adapter.agenerate(request)
            
            # 处理压缩模型响应
            if response.is_success:
                result = response.raw_content
            else:
                logger.warning("compress_reasoning_llm_failed", error=response.error_message)
                result = reasoning_text # 回退到原始推理文本
                
        except Exception as e:
            logger.error("compress_reasoning_exception", error=str(e), exc_info=True)
            result = reasoning_text # 回退到原始推理文本
            
        # 将压缩结果保存到Redis中，并设置过期时间（7天）
        try:
            redis = await RedisClientManager.get_client()
            key = RedisKeys.compressed_reasoning(game_id, agent_id)
            await redis.hset(key, str(round_num), result)
            await redis.expire(key, 7 * 24 * 3600)
        except Exception as e:
            logger.error("save_compressed_reasoning_failed", error=str(e), exc_info=True)
            
        return result

    @classmethod
    async def merge_global_summary(cls, current_summary: str, new_info: str, game_id: str, agent_id: str, round_num: int) -> str:
        """
        将新一轮的信息融入全局摘要中
        
        :param current_summary: 当前的全局摘要字符串
        :param new_info: 需要合并到全局摘要的新信息
        :param game_id: 游戏ID，用于标识当前游戏会话
        :param agent_id: 智能体ID，用于标识当前智能体
        :param round_num: 当前轮次编号
        :return: 合并后的全局摘要字符串
        """
        redis = await RedisClientManager.get_client()
        lock_key = f"werewolf:lock:summary:{game_id}:{agent_id}"
        summary_key = RedisKeys.global_summary(game_id, agent_id)
        
        import asyncio
        # 获取分布式锁，防止并发更新导致丢失
        for _ in range(30):
            acquired = await redis.set(lock_key, "1", nx=True, ex=30)
            if acquired:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("merge_global_summary_lock_timeout", game_id=game_id, agent_id=agent_id)
            return current_summary + "\n" + new_info
            
        try:
            # 重新获取最新的 current_summary，避免使用传入的旧数据
            latest_summary = await redis.get(summary_key)
            if latest_summary:
                current_summary = latest_summary.decode('utf-8') if isinstance(latest_summary, bytes) else latest_summary

            # 使用模板构建器加载merge_summary.j2模板，并用当前摘要、新信息和轮次号渲染
            from ai_werewolf_core.agents.prompts.builder import PromptBuilder
            builder = PromptBuilder()
            template = builder.env.get_template("merge_summary.j2")
            full_prompt = template.render(current_summary=current_summary, new_info=new_info, round_num=round_num)
            
            try:
                # 获取适配器实例并定义响应模型
                adapter = AdapterFactory.get_adapter("compression_model")
                from pydantic import BaseModel
                class StringResponse(BaseModel):
                    content: str
                    
                # 创建请求对象，指定压缩模型参数
                request = AdapterRequest(
                    model_id="compression_model",
                    agent_id="system_compressor",
                    game_id=game_id,
                    phase=GamePhase.INIT,
                    full_prompt=full_prompt,
                    temperature=0.3,
                    max_tokens=1024,
                    response_model=StringResponse
                )
                
                # 异步调用适配器生成合并后的内容
                response = await adapter.agenerate(request)
                
                if response.is_success:
                    result = response.raw_content
                else:
                    logger.warning("merge_global_summary_llm_failed", error=response.error_message)
                    result = current_summary + "\n" + new_info # 回退机制：如果LLM调用失败，则简单拼接新信息
                    
            except Exception as e:
                logger.error("merge_global_summary_exception", error=str(e), exc_info=True)
                result = current_summary + "\n" + new_info # 回退机制：异常情况下同样简单拼接
                
            # 尝试将合并后的结果保存到Redis缓存中，设置过期时间为7天
            try:
                await redis.set(summary_key, result, ex=7 * 24 * 3600)
            except Exception as e:
                logger.error("save_global_summary_failed", error=str(e), exc_info=True)
                
            return result
        finally:
            await redis.delete(lock_key)

    @classmethod
    async def extreme_compress_summary(cls, current_summary: str, game_id: str, agent_id: str) -> str:
        """
        极限压缩全局摘要
        使用大语言模型对当前摘要进行极限压缩，并将结果存储到Redis中
        
        :param current_summary: 当前需要被压缩的摘要文本
        :param game_id: 游戏会话ID
        :param agent_id: 智能体ID
        :return: 压缩后的摘要文本
        """
        from ai_werewolf_core.agents.prompts.builder import PromptBuilder
        builder = PromptBuilder()
        template = builder.env.get_template("extreme_compress.j2")
        # 使用模板渲染压缩提示词
        full_prompt = template.render(current_summary=current_summary)
        
        try:
            # 获取适配器并构建请求以调用大语言模型进行摘要压缩
            adapter = AdapterFactory.get_adapter("compression_model")
            from pydantic import BaseModel
            class StringResponse(BaseModel):
                content: str
                
            request = AdapterRequest(
                model_id="compression_model",
                agent_id="system_compressor",
                game_id=game_id,
                phase=GamePhase.INIT,
                full_prompt=full_prompt,
                temperature=0.3,
                max_tokens=512,
                response_model=StringResponse
            )
            
            response = await adapter.agenerate(request)
            
            if response.is_success:
                result = response.raw_content
            else:
                logger.warning("extreme_compress_summary_llm_failed", error=response.error_message)
                result = current_summary[:500] # 回退机制：截取前500字符
                
        except Exception as e:
            logger.error("extreme_compress_summary_exception", error=str(e), exc_info=True)
            result = current_summary[:500] # 异常时回退机制：截取前500字符
            
        try:
            # 将压缩结果保存到Redis缓存中，设置过期时间为7天
            redis = await RedisClientManager.get_client()
            key = RedisKeys.global_summary(game_id, agent_id)
            await redis.set(key, result, ex=7 * 24 * 3600)
        except Exception as e:
            logger.error("save_extreme_compressed_summary_failed", error=str(e), exc_info=True)
            
        return result
